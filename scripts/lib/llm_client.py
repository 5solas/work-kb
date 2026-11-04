"""OpenAI 호환 LLM 클라이언트. LLM HTTP의 유일한 통로다(D44).

- 회사 차단(D44): `LlmClient.__init__`이 모든 프로필 주소를 검사해, `environment`가 home이
  아니고 어느 하나라도 공개 호스트면 `PublicHostRefused`(종료 5). `llm.enabled: false`여도
  먼저 검사한다. 요청 직전에도 주소를 다시 검사한다. 리디렉션은 따라가지 않는다(3xx는 오류).
- `chat()`: 프로필의 `detected`에 따라 추론 토글(param / system_prefix / none)과 JSON 모드
  (`response_format` 또는 프롬프트 강제)를 적용한다. 추론 켜짐 + response_format이 400이나
  빈 답이면 프롬프트 강제 JSON으로 한 번 다시 보내고, 이 클라이언트가 사는 동안 그 프로필은
  추론 켜짐 요청에 response_format을 쓰지 않는다(D38).
- `normalize_message()`: `reasoning`/`reasoning_content` 필드와 content 안의 `</think>`
  (여는 태그가 없어도) 앞부분을 생각으로 떼어 낸다(D39).
- `run_task()`: 작업 프로필 → 나머지 `fallback_order` 순서. 연결·시간 초과·HTTP 오류(429 재시도
  소진, 5xx, 401, 403, 404, 400, 413, 3xx 등)는 다음 프로필로, 스키마 실패는 같은 프로필에서
  한 번 더(길이 잘림이면 출력 상한 2배, 최대 16384) 뒤 다음 프로필로. 다 실패하면 `skipped`(D40).
- 요청마다 로그 한 줄(`.state/logs/kb.log`, 비밀값 가림). 본문은 남기지 않는다.

탐지(llm_detect)는 `chat(..., body_extra=...)`로 원하는 파라미터를 직접 보낸다. `json_mode=True`는
탐지 결과(`detected.json_mode`)를 따르므로, 탐지 중 response_format을 시험하려면
`body_extra={"response_format": {"type": "json_object"}}`를 쓴다.
"""
from __future__ import annotations

import copy
import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence, Union

from lib import config, core, net
from lib import secrets as kb_secrets
from lib.prompts import MARKER_PREFIX

ASSUMED_CONTEXT = 262144  # /models에 없으면 쓰는 값(D41, source: assumed)
INPUT_CONTEXT_RATIO = 0.8
OUTPUT_CAP = 16384  # 길이 잘림 재시도 상한(D40). DeepInfra 문서의 출력 상한과도 같다
NONE_REASONING_FLOOR = 2048  # method none + 기준선 추론 켜짐일 때 출력 하한(D41)
DEFAULT_MAX_TOKENS = 1024
DEFAULT_MAX_OUTPUT: dict[str, int] = {
    "tag": 64,
    "decisions": 8192,
    "entity_summary": 2048,
    "topic_proposal": 8192,
    "image_ingest": 2048,
    "contradictions": 8192,
}
MAX_429 = 2  # net.request의 429 재시도 횟수. 그 뒤에도 429면 다음 프로필
MODELS_TIMEOUT = 30.0
BODY_SNIPPET = 500
THINK_WORDS = {True: "/think", False: "/no_think"}
FORCED_JSON_RULE = (
    "출력 형식: JSON 객체 하나만 쓴다. 코드 블록, 설명, 다른 글 없이 `{`로 시작해 `}`로 끝낸다."
)

_FENCED = re.compile(r"^```[A-Za-z0-9_-]*[ \t]*\n(.*?)\n?```\s*$", re.DOTALL)

Messages = list[dict[str, Any]]
MessagesFor = Union[Callable[[int], Messages], Sequence[Mapping[str, Any]]]


# ---------------------------------------------------------------- 결과·오류


@dataclass(frozen=True)
class ChatResult:
    content: str  # 생각을 떼어 낸 답(없으면 "")
    reasoning: str | None  # 생각(필드 또는 </think> 앞부분). 없으면 None
    finish_reason: str | None
    usage: dict | None
    profile: str
    model: str
    raw: dict | None = field(default=None, repr=False, compare=False)  # 서버 응답 JSON 전체(탐지 근거용)


@dataclass(frozen=True)
class TaskOutcome:
    status: Literal["ok", "skipped", "disabled"]
    data: dict | None
    profile: str | None
    attempts: list[dict]


class ChatHttpError(core.KbError):
    """LLM 서버가 2xx가 아닌 응답(또는 해석할 수 없는 2xx)을 돌려줌. 리디렉션(3xx) 포함."""

    exit_code = 3

    def __init__(self, status: int, body: str, message: str | None = None) -> None:
        self.status = status
        self.body = body
        super().__init__(message or f"LLM 요청이 실패했습니다 ({status}): {body[:200]}")


class SchemaError(ValueError):
    """답이 JSON 객체가 아니거나 비었거나 잘림. run_task가 스키마 실패로 센다."""


# ---------------------------------------------------------------- 공개 호스트


def is_public_host(url: str, blocklist: Iterable[str]) -> bool:
    """config.is_public_host 그대로(하위 도메인, 끝 점, 대소문자, 애매한 주소는 공개로 봄)."""
    return config.is_public_host(url, blocklist)


def _blocklist(cfg: Any) -> frozenset[str]:
    try:
        extra = frozenset(cfg.blocklist)
    except Exception:  # 목록을 읽지 못해도 코드 목록은 늘 적용한다
        extra = frozenset()
    return extra | config.CODE_BLOCKLIST


def _is_home(cfg: Any) -> bool:
    return getattr(cfg, "environment", "company") == "home"


def _url_refused(cfg: Any, url: Any) -> bool:
    """회사 환경에서 이 주소로 보내면 안 되는가. 빈 주소는 보낼 곳이 없으니 False."""
    if _is_home(cfg) or url is None or (isinstance(url, str) and not url.strip()):
        return False
    if not isinstance(url, str):
        return True
    return is_public_host(url, _blocklist(cfg))


def _refusal_text(url: Any, name: str | None = None) -> str:
    host = config.checked_host(url) if isinstance(url, str) else None
    who = f"LLM 프로필 '{name}'" if name else "LLM 주소"
    if host is None:
        return f"회사 환경: {who}의 주소에서 호스트를 하나로 정할 수 없어 보내지 않습니다 ({url!r})"
    return f"회사 환경: {who}가 공개 LLM 호스트({host})라 보내지 않습니다. 사내 주소로 바꾸세요 (kb.py init --section llm)"


def assert_host_allowed(cfg: Any, url: str) -> None:
    """회사 환경(home이 아닌 모든 값)에서 공개 호스트면 PublicHostRefused. init이 호출 전에 쓴다."""
    if _url_refused(cfg, url):
        raise core.PublicHostRefused(_refusal_text(url))


# ---------------------------------------------------------------- 응답 정리


def _text_of(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, Mapping) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "".join(parts)
    return ""


def normalize_message(message: Mapping) -> tuple[str, str | None]:
    """(답, 생각)으로 나눈다(D39).

    - 생각: `reasoning` 또는 `reasoning_content` 필드, content 안 마지막 `</think>` 앞부분
      (`<think>`가 있든 없든), 닫히지 않은 `<think>…`(잘림).
    - 답: 마지막 `</think>` 뒤의 글. content가 None이면 "".
    """
    if not isinstance(message, Mapping):
        return "", None
    content = _text_of(message.get("content"))
    traces: list[str] = []
    for key in ("reasoning", "reasoning_content"):
        value = message.get(key)
        if isinstance(value, str) and value.strip() and value.strip() not in traces:
            traces.append(value.strip())
    cut = content.rfind("</think>")
    if cut >= 0:
        head = content[:cut].replace("<think>", "").replace("</think>", "").strip()
        content = content[cut + len("</think>"):]
        if head:
            traces.append(head)
    elif content.lstrip().startswith("<think>"):
        rest = content.lstrip()[len("<think>"):].strip()
        if rest:
            traces.append(rest)
        content = ""
    return content.strip(), ("\n\n".join(traces) or None)


def parse_json_object(content: str) -> dict:
    """답 글에서 JSON 객체 하나를 읽는다. 코드 펜스와 앞뒤 군말은 견딘다. 실패하면 SchemaError."""
    text = (content or "").strip()
    if not text:
        raise SchemaError("빈 답입니다")
    candidates = [text]
    fenced = _FENCED.match(text)
    if fenced:
        candidates.append(fenced.group(1).strip())
    first, last = text.find("{"), text.rfind("}")
    if 0 <= first < last:
        candidates.append(text[first : last + 1])
    error: Exception | None = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError as exc:
            error = exc
            continue
        if isinstance(data, dict):
            return data
        error = SchemaError(f"JSON 객체가 아닙니다 ({type(data).__name__})")
    raise SchemaError(f"JSON을 읽지 못했습니다: {error}")


# ---------------------------------------------------------------- 분량


def estimate_tokens(text: str) -> int:
    """토큰 수 추정: 글자 수 / 2.5(올림)."""
    if not text:
        return 0
    return math.ceil(len(text) / 2.5)


def split_by_date(items: Sequence[tuple[str, str]], limit: int) -> list[list[tuple[str, str]]]:
    """(날짜, 줄) 목록을 추정 토큰 `limit` 이하 묶음으로 나눈다. 순서는 그대로.

    같은 날짜(연속된 항목)는 되도록 한 묶음에 둔다. 한 날짜가 혼자서도 넘치면 줄 단위로 나눈다.
    줄 하나가 혼자 넘치면 그 줄만으로 한 묶음이 된다(자르지 않는다). 줄 사이 줄바꿈 1자를 센다.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError(f"limit은 양의 정수여야 합니다: {limit!r}")
    budget = limit * 2.5  # 글자 수 기준

    def size(group: Sequence[tuple[str, str]]) -> int:
        return sum(len(text) + 1 for _, text in group)

    groups: list[list[tuple[str, str]]] = []
    for item in items:
        if groups and groups[-1][0][0] == item[0]:
            groups[-1].append(item)
        else:
            groups.append([item])

    chunks: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    used = 0
    for group in groups:
        need = size(group)
        if used + need <= budget:
            current.extend(group)
            used += need
            continue
        if current:
            chunks.append(current)
            current, used = [], 0
        if need <= budget:
            current, used = list(group), need
            continue
        for item in group:  # 하루치가 혼자 넘친다: 줄 단위
            line = len(item[1]) + 1
            if current and used + line > budget:
                chunks.append(current)
                current, used = [], 0
            current.append(item)
            used += line
    if current:
        chunks.append(current)
    return chunks


# ---------------------------------------------------------------- 작은 도우미


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float) and value.is_integer() and value > 0:
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
        return number if number > 0 else None
    return None


def _detected(profile: Mapping[str, Any] | None) -> dict:
    raw = profile.get("detected") if isinstance(profile, Mapping) else None
    return dict(raw) if isinstance(raw, Mapping) else {}


def _merge(base: dict, extra: Mapping[str, Any]) -> dict:
    """한 단계 깊이까지 합친다(둘 다 dict면 키별로 덮어씀)."""
    for key, value in extra.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            base[key] = {**base[key], **copy.deepcopy(dict(value))}
        else:
            base[key] = copy.deepcopy(value)
    return base


def _tokens(usage: Any) -> dict | None:
    if not isinstance(usage, Mapping):
        return None
    out = {k: usage.get(k) for k in ("prompt_tokens", "completion_tokens") if isinstance(usage.get(k), int)}
    return out or None


def _snippet(text: str, limit: int = BODY_SNIPPET) -> str:
    flat = " ".join((text or "").split())
    if len(flat) > limit:
        flat = flat[: limit - 1] + "…"
    return core.redact(flat)


def _names(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [x for x in re.split(r"[\s,]+", raw) if x]
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw if x is not None and str(x)]
    return []


def _system_index(messages: list[dict]) -> int | None:
    for i, message in enumerate(messages):
        if isinstance(message, dict) and message.get("role") == "system":
            return i
    return None


def _prefix_system(messages: list[dict], word: str) -> None:
    """시스템 메시지 맨 앞에 `/think`·`/no_think`를 넣는다. 첫 줄이 `KB-TASK:` 표시면 그 다음 줄에."""
    index = _system_index(messages)
    if index is None:
        messages.insert(0, {"role": "system", "content": word})
        return
    message = dict(messages[index])
    content = message.get("content")
    if isinstance(content, list):
        content = _text_of(content)
    content = content if isinstance(content, str) else ""
    if content.startswith(MARKER_PREFIX):
        first, sep, rest = content.partition("\n")
        message["content"] = f"{first}\n{word}\n{rest}" if sep else f"{first}\n{word}"
    else:
        message["content"] = f"{word}\n{content}" if content else word
    messages[index] = message


def _force_json(messages: list[dict]) -> None:
    """시스템 메시지 끝에 JSON 강제 규칙을 붙인다(없으면 시스템 메시지를 만든다)."""
    index = _system_index(messages)
    if index is None:
        messages.insert(0, {"role": "system", "content": FORCED_JSON_RULE})
        return
    message = dict(messages[index])
    content = message.get("content")
    if isinstance(content, list):
        message["content"] = [*content, {"type": "text", "text": "\n\n" + FORCED_JSON_RULE}]
    else:
        text = content if isinstance(content, str) else ""
        message["content"] = f"{text.rstrip()}\n\n{FORCED_JSON_RULE}" if text.strip() else FORCED_JSON_RULE
    messages[index] = message


# ---------------------------------------------------------------- 클라이언트


class LlmClient:
    """프로필 묶음 하나에 대한 클라이언트. 만들 때 회사 차단을 검사한다."""

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        profiles = cfg.profiles()
        if not _is_home(cfg):
            refused = [
                (name, profile.get("base_url"))
                for name, profile in profiles.items()
                if _url_refused(cfg, profile.get("base_url"))
            ]
            if refused:
                raise core.PublicHostRefused(
                    " / ".join(_refusal_text(url, name) for name, url in refused)
                )
        self._profiles: dict[str, dict] = copy.deepcopy(profiles)
        self._keys: dict[str, str | None] = {}
        self._forced_json: set[str] = set()  # D38: 이 실행 동안 추론 켜짐에 response_format을 쓰지 않을 프로필
        self.last_probe: dict[str, str] = {}  # connected_profiles가 남기는 프로필별 결과 설명

    # ------------------------------------------------------------ 설정

    @property
    def enabled(self) -> bool:
        return _truthy(self.cfg.get("llm.enabled"), False)

    def profile_names(self) -> list[str]:
        return list(self._profiles)

    def _profile(self, name: str) -> dict:
        profile = self._profiles.get(name)
        if profile is None:
            raise core.ConfigError(
                f"LLM 프로필 '{name}'이(가) 설정에 없습니다. `kb.py init --section llm`으로 추가하세요"
            )
        return profile

    def detected(self, name: str) -> dict:
        return _detected(self._profiles.get(name))

    def _fallback_order(self) -> list[str]:
        return _names(self.cfg.get("llm.fallback_order"))

    def _task_cfg(self, task: str) -> dict:
        return self.cfg.llm_task(task)

    def profile_order(self, task: str) -> list[str]:
        """작업 프로필, 그다음 fallback_order의 나머지(중복 제거)."""
        first = str(self._task_cfg(task).get("profile") or "main")
        return list(dict.fromkeys([first, *self._fallback_order()]))

    def context_tokens(self, name: str) -> int:
        """detected.context_tokens, 없으면 262144(assumed)."""
        return _positive_int(self.detected(name).get("context_tokens")) or ASSUMED_CONTEXT

    def input_limit(self, task: str, profile: str | None = None) -> int:
        """min(max_input_tokens, 0.8 × context_tokens). 이미지는 세지 않는다(D41)."""
        task_cfg = self._task_cfg(task)
        name = profile or str(task_cfg.get("profile") or "main")
        context = self.context_tokens(name)
        cap = max(int(context * INPUT_CONTEXT_RATIO), 1)
        wanted = _positive_int(task_cfg.get("max_input_tokens"))
        return min(wanted, cap) if wanted else cap

    def max_output_tokens(self, task: str, profile: str | None = None) -> int:
        """작업 출력 상한. method none + 기준선 추론 켜짐이면 하한 2048(D41). 최대 16384."""
        task_cfg = self._task_cfg(task)
        name = profile or str(task_cfg.get("profile") or "main")
        value = _positive_int(task_cfg.get("max_output_tokens")) or DEFAULT_MAX_OUTPUT.get(task, DEFAULT_MAX_TOKENS)
        detected = self.detected(name)
        toggle = detected.get("reasoning_toggle")
        method = toggle.get("method") if isinstance(toggle, Mapping) else None
        if method == "none" and _truthy(detected.get("baseline_reasoning"), False):
            value = max(value, NONE_REASONING_FLOOR)
        return min(value, OUTPUT_CAP)

    # ------------------------------------------------------------ HTTP

    def _url(self, name: str, path: str) -> str:
        profile = self._profile(name)
        base = profile.get("base_url")
        if _url_refused(self.cfg, base):  # 만든 뒤 프로필이 바뀌었더라도 보내기 전에 다시 막는다
            raise core.PublicHostRefused(_refusal_text(base, name))
        if not isinstance(base, str) or not base.strip():
            raise core.ConfigError(f"llm.profiles.{name}.base_url이 없습니다 → kb.py init --section llm")
        url = base.strip().rstrip("/") + path
        if _url_refused(self.cfg, url):
            raise core.PublicHostRefused(_refusal_text(url, name))
        return url

    def _conn(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        conn = {k: profile[k] for k in ("ca_bundle", "verify", "trust_env", "proxies") if k in profile}
        ca = conn.get("ca_bundle")
        if ca and not Path(str(ca)).expanduser().is_absolute():
            base = getattr(self.cfg, "config_dir", None) or getattr(self.cfg, "root", Path("."))
            conn["ca_bundle"] = str(Path(base) / str(ca))
        return conn

    def _key(self, name: str) -> str | None:
        if name not in self._keys:
            ref = self._profile(name).get("api_key_ref")
            self._keys[name] = kb_secrets.resolve(ref, self.cfg) if ref not in (None, "") else None
        return self._keys[name]

    def _request(
        self,
        name: str,
        method: str,
        path: str,
        *,
        timeout: float,
        json_body: Any = None,
        key_optional: bool = False,
    ) -> tuple[int, str, str | None]:
        url = self._url(name, path)
        try:
            key = self._key(name)
        except core.KbError:
            if not key_optional:
                raise
            key = None
        session = net.session_for(self._conn(self._profile(name)), key)
        kw: dict[str, Any] = {"allow_redirects": False}
        if json_body is not None:
            kw["json"] = json_body
        with session:
            resp = net.request(session, method, url, timeout=timeout, max_429=MAX_429, auth_raises=False, **kw)
            with resp:
                try:
                    text = resp.content.decode("utf-8", errors="replace")
                except Exception:  # 본문을 못 읽어도 상태 코드는 쓸 수 있다
                    text = ""
                return resp.status_code, text, resp.headers.get("Location")

    def _http_error(self, status: int, text: str, location: str | None) -> ChatHttpError:
        if 300 <= status < 400:
            where = core.redact(location or "?")
            return ChatHttpError(
                status,
                f"리디렉션 → {where}",
                f"LLM 서버가 다른 주소로 보내려 합니다 ({status} → {where}). 따라가지 않습니다. base_url을 최종 주소로 바꾸세요",
            )
        return ChatHttpError(status, _snippet(text))

    def list_models(self, profile: str) -> list[dict] | None:
        """GET /models(키가 풀리면 키와 함께). 404·목록 아님은 None, 그 밖의 2xx 아님은 ChatHttpError."""
        status, text, location = self._request(
            profile, "GET", "/models", timeout=min(MODELS_TIMEOUT, self._timeout(None)), key_optional=True
        )
        if status == 404:
            return None
        if not 200 <= status < 300:
            raise self._http_error(status, text, location)
        try:
            data = json.loads(text)
        except ValueError:
            return None
        items = data.get("data") if isinstance(data, Mapping) else data
        if not isinstance(items, list):
            return None
        return [x for x in items if isinstance(x, dict)]

    def _candidate_names(self) -> list[str]:
        names = list(self._fallback_order())
        tasks = self.cfg.get("llm.tasks") or {}
        if isinstance(tasks, Mapping):
            for task in tasks.values():
                if isinstance(task, Mapping):
                    names.append(str(task.get("profile") or "main"))
        return [n for n in dict.fromkeys(names) if n in self._profiles]

    def connected_profiles(self, names: Iterable[str] | None = None) -> list[str]:
        """닿는 프로필(작업 프로필과 fallback_order 중 설정에 있는 것). /models 2xx 또는 404면 연결됨.

        401/403(키 거부)·3xx·5xx·연결 실패는 빠진다. 프로필별 설명은 `last_probe`에 남는다.
        """
        out: list[str] = []
        for name in (list(names) if names is not None else self._candidate_names()):
            if name not in self._profiles:
                self.last_probe[name] = "프로필 없음"
                continue
            try:
                models = self.list_models(name)
            except core.PublicHostRefused:
                raise
            except ChatHttpError as exc:
                self.last_probe[name] = str(exc)
                continue
            except core.KbError as exc:
                self.last_probe[name] = net.error_text(exc)
                continue
            self.last_probe[name] = "연결됨" if models is not None else "연결됨, /models 없음"
            out.append(name)
        return out

    def _timeout(self, timeout: float | None) -> float:
        if timeout is not None:
            return float(timeout)
        value = self.cfg.get("llm.timeout_sec")
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 180.0
        return number if number > 0 else 180.0

    def _log(self, line: str) -> None:
        try:
            core.get_logger(self.cfg.root).info(line)
        except Exception:  # 로그 실패가 작업을 막지 않게
            pass

    # ------------------------------------------------------------ chat

    def _apply_toggle(self, body: dict, profile: Mapping[str, Any], on: bool) -> None:
        toggle = _detected(profile).get("reasoning_toggle")
        if not isinstance(toggle, Mapping):
            return
        method = toggle.get("method")
        value = toggle.get("on_value" if on else "off_value")
        if method == "param":
            param = toggle.get("param_name")
            if not isinstance(param, str) or not param or value is None:
                return
            if isinstance(value, Mapping) and isinstance(body.get(param), dict):
                body[param] = {**body[param], **copy.deepcopy(dict(value))}
            else:
                body[param] = copy.deepcopy(value)
        elif method == "system_prefix":
            word = value if isinstance(value, str) and value.strip() else THINK_WORDS[on]
            _prefix_system(body["messages"], word.strip())

    def chat(
        self,
        profile: str,
        messages: list[dict],
        *,
        reasoning: bool | None = None,
        json_mode: bool = False,
        body_extra: Mapping | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: float | None = None,
    ) -> ChatResult:
        """POST /chat/completions 한 번(D38 재시도 포함 최대 두 번). 2xx가 아니면 ChatHttpError."""
        return self._chat(
            profile, messages, reasoning=reasoning, json_mode=json_mode, body_extra=body_extra,
            max_tokens=max_tokens, timeout=timeout, task=None,
        )

    def _chat(
        self,
        name: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        reasoning: bool | None,
        json_mode: bool,
        body_extra: Mapping | None,
        max_tokens: int,
        timeout: float | None,
        task: str | None,
    ) -> ChatResult:
        profile = self._profile(name)
        detected = _detected(profile)
        body: dict[str, Any] = {}
        if profile.get("model"):
            body["model"] = str(profile["model"])
        body["messages"] = [dict(m) for m in copy.deepcopy(list(messages))]
        body["max_tokens"] = int(max_tokens)
        temperature = self.cfg.get("llm.temperature", 0)
        if isinstance(temperature, (int, float)) and not isinstance(temperature, bool):
            body["temperature"] = temperature
        if reasoning is not None:
            self._apply_toggle(body, profile, bool(reasoning))
        use_format = (
            json_mode
            and detected.get("json_mode") is True
            and not (reasoning is True and name in self._forced_json)
        )
        if use_format:
            body["response_format"] = {"type": "json_object"}
        elif json_mode:
            _force_json(body["messages"])
        if body_extra:
            _merge(body, body_extra)

        d38 = use_format and reasoning is True
        try:
            result = self._post_chat(name, body, self._timeout(timeout), task)
        except ChatHttpError as exc:
            if not (d38 and exc.status == 400):
                raise
            reason = f"400: {exc.body[:120]}"
        else:
            if not (d38 and not result.content and result.finish_reason != "length"):
                return result
            reason = "빈 답"
        # D38: 추론 켜짐 + response_format 실패 → 프롬프트 강제 JSON으로 한 번 더, 이번 실행 동안 유지
        self._forced_json.add(name)
        self._log(f"LLM {task or '-'} {name}: 추론 켜짐 + response_format 실패({reason}) → 프롬프트로 JSON 강제")
        return self._chat(
            name, messages, reasoning=reasoning, json_mode=json_mode, body_extra=body_extra,
            max_tokens=max_tokens, timeout=timeout, task=task,
        )

    def _post_chat(self, name: str, body: dict, timeout: float, task: str | None) -> ChatResult:
        model = str(body.get("model") or "")
        started = time.monotonic()
        head = f"LLM {task or '-'} {name} {model or '-'} max_tokens={body.get('max_tokens')}"
        try:
            status, text, location = self._request(name, "POST", "/chat/completions", json_body=body, timeout=timeout)
        except core.PublicHostRefused:
            raise
        except core.KbError as exc:
            self._log(f"{head} → 실패 {type(exc).__name__}: {net.error_text(exc)} ({time.monotonic() - started:.1f}s)")
            raise
        elapsed = time.monotonic() - started
        if not 200 <= status < 300:
            error = self._http_error(status, text, location)
            self._log(f"{head} → {status} {error.body[:200]} ({elapsed:.1f}s)")
            raise error
        try:
            payload = json.loads(text)
        except ValueError:
            self._log(f"{head} → {status} JSON 아닌 응답 ({elapsed:.1f}s)")
            raise ChatHttpError(status, _snippet(text), f"LLM 응답이 JSON이 아닙니다 ({status}): {_snippet(text, 120)}") from None
        choices = payload.get("choices") if isinstance(payload, Mapping) else None
        first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], Mapping) else None
        if first is None:
            self._log(f"{head} → {status} choices 없음 ({elapsed:.1f}s)")
            raise ChatHttpError(status, _snippet(text), f"LLM 응답에 choices가 없습니다 ({status})")
        content, reasoning = normalize_message(first.get("message") or {})
        finish = first.get("finish_reason")
        usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else None
        result = ChatResult(
            content=content,
            reasoning=reasoning,
            finish_reason=finish if isinstance(finish, str) else None,
            usage=dict(usage) if usage is not None else None,
            profile=name,
            model=str(payload.get("model") or model),
            raw=payload if isinstance(payload, dict) else None,
        )
        tokens = _tokens(usage) or {}
        self._log(
            f"{head} → {status} ({elapsed:.1f}s) in={tokens.get('prompt_tokens', '?')} "
            f"out={tokens.get('completion_tokens', '?')} finish={result.finish_reason} "
            f"생각={'있음' if reasoning else '없음'} 답={len(content)}자"
        )
        return result

    # ------------------------------------------------------------ 작업

    def run_task(
        self,
        task: str,
        messages_for: MessagesFor,
        *,
        validate: Callable[[dict], None],
        needs_vision: bool = False,
    ) -> TaskOutcome:
        """작업 하나를 프로필 순서대로 시도한다. 꺼져 있으면 disabled, 다 실패하면 skipped(종료 0 몫)."""
        if not self.enabled:
            return TaskOutcome("disabled", None, None, [])
        reasoning = self._task_cfg(task).get("reasoning") == "on"
        attempts: list[dict] = []
        for name in self.profile_order(task):
            profile = self._profiles.get(name)
            if profile is None:
                attempts.append(_attempt(name, "missing", "프로필이 설정에 없습니다"))
                continue
            if needs_vision and _detected(profile).get("vision") is not True:
                attempts.append(_attempt(name, "no_vision", "비전 탐지 결과가 없거나 거짓입니다"))
                continue
            limit = self.input_limit(task, name)
            messages = messages_for(limit) if callable(messages_for) else [dict(m) for m in messages_for]
            max_out = self.max_output_tokens(task, name)
            for round_no in (1, 2):
                record = _attempt(name, "", None, max_tokens=max_out)
                attempts.append(record)
                try:
                    result = self._chat(
                        name, messages, reasoning=reasoning, json_mode=True, body_extra=None,
                        max_tokens=max_out, timeout=None, task=task,
                    )
                except core.PublicHostRefused:
                    raise
                except ChatHttpError as exc:
                    record.update(status="http", http_status=exc.status, error=exc.body or str(exc))
                    break
                except core.NetworkError as exc:
                    record.update(status="network", error=net.error_text(exc))
                    break
                except core.KbError as exc:
                    record.update(status="error", error=core.redact(str(exc)))
                    break
                record["tokens"] = _tokens(result.usage)
                record["finish_reason"] = result.finish_reason
                try:
                    if result.finish_reason == "length":
                        raise SchemaError("출력이 max_tokens에서 잘렸습니다 (finish_reason=length)")
                    data = parse_json_object(result.content)
                    validate(data)
                except Exception as exc:  # 검증 함수는 어떤 예외든 낼 수 있다 → 스키마 실패
                    length = result.finish_reason == "length"
                    record.update(status="length" if length else "schema", error=core.redact(f"{type(exc).__name__}: {exc}")[:300])
                    if round_no == 1:
                        if length:
                            max_out = min(max_out * 2, OUTPUT_CAP)
                        continue
                    break
                record["status"] = "ok"
                return TaskOutcome("ok", data, name, attempts)
        self._log(
            f"LLM 작업 {task}: 건너뜀 (시도 {len(attempts)}: "
            + ", ".join(f"{a['profile']}={a['status']}" for a in attempts)
            + ")"
        )
        return TaskOutcome("skipped", None, None, attempts)


def _attempt(profile: str, status: str, error: str | None, *, max_tokens: int | None = None) -> dict:
    return {
        "profile": profile,
        "status": status,
        "error": error,
        "http_status": None,
        "tokens": None,
        "max_tokens": max_tokens,
        "finish_reason": None,
    }


__all__ = [
    "ASSUMED_CONTEXT",
    "ChatHttpError",
    "ChatResult",
    "DEFAULT_MAX_OUTPUT",
    "LlmClient",
    "OUTPUT_CAP",
    "SchemaError",
    "TaskOutcome",
    "assert_host_allowed",
    "estimate_tokens",
    "is_public_host",
    "normalize_message",
    "parse_json_object",
    "split_by_date",
]
