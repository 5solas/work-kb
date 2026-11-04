"""LLM 서버 능력 자동 탐지(§6.11, D35~D39, D41).

순서(D35): 컨텍스트(GET /models, 호출 수에 넣지 않음) → 기준선 채팅 1회 → 추론 토글 후보 1~4
→ 찾은 '끄기' 상태로 JSON 모드 1회 → 비전 1회. POST는 모두 `Budget`이 세며 10회를 넘지 않는다.
토글 후보에는 8회(기준선 포함)만 쓰고 2회는 JSON·비전 몫으로 남겨 둔다.

판정 규칙:
- 200 응답은 증거가 아니다. 판정은 생각의 흔적(`reasoning`·`reasoning_content` 필드, content 안의
  `</think>`)으로 한다(D36). 흔적이 양쪽 모두 없고 usage가 있을 때만 토큰 수를 보조로 쓴다
  (켜기 ≥ 2×끄기, 차이 ≥ 30). `/think` 접두는 흔적으로만 인정한다(프롬프트 자체가 바뀌므로).
- 각 후보는 기준선과 반대 방향을 먼저 보낸다. 뒤집히면 다른 방향 한 번으로 확인하고 채택한다.
  4xx는 '이 방식이 아님', 200인데 변화 없음은 '증거 없음'으로 보고 다음 후보로 간다.
- '끄기' 쪽 답은 비어 있으면 안 된다(D36).
- 예산이 떨어지면 `method: none` + `budget_exhausted` 근거 + 경고.
- `method: none`이어도 `baseline_reasoning`을 기록해 D41의 출력 하한이 적용되게 한다.

회사 차단은 `LlmClient.__init__`이 하고, `detect()` 입구에서 이 프로필 주소를 한 번 더 검사한다.
LLM HTTP는 모두 `LlmClient`(chat, list_models)를 거친다. 이 모듈은 가져올 때 아무 일도 하지 않는다.
"""
from __future__ import annotations

import base64
import json
import re
import struct
import zlib
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from lib import core
from lib.llm_client import (
    ASSUMED_CONTEXT,
    ChatHttpError,
    ChatResult,
    LlmClient,
    SchemaError,
    assert_host_allowed,
    parse_json_object,
)

MAX_POSTS = 10
RESERVED_POSTS = 2  # JSON·비전 몫
PROBE_MAX_TOKENS = 1024  # D36
TOKEN_RATIO = 2.0  # D36 보조 신호: 켜기 ≥ 2×끄기
TOKEN_GAP = 30  # D36 보조 신호: 차이 ≥ 30 (출처 없는 시작값)
ANSWER_SNIPPET = 80
VISION_SIZE = 64

# 탐지 프롬프트: 허구의 짧은 산수 하나로 고정(§6.11).
SYSTEM_PROMPT = "짧게 답한다."
ARITHMETIC_PROMPT = "상자에 구슬이 17개 있다. 5개를 꺼내고 8개를 넣었다. 지금 구슬은 몇 개인가? 숫자만 답하라."
JSON_SYSTEM_PROMPT = "JSON 객체 하나만 출력한다."
JSON_PROMPT = '상자에 구슬이 17개 있다. 5개를 꺼내고 8개를 넣었다. 지금 구슬은 몇 개인가? {"answer": 정수} 형식의 JSON 객체로만 답하라.'
VISION_PROMPT = "이 그림은 한 가지 색으로 칠해져 있다. 무슨 색인지 한국어 한 단어로 답하라."

_RED = re.compile(r"빨강|빨간|빨갛|\bred\b", re.IGNORECASE)


def _error_text(exc: BaseException) -> str:
    """오류 설명 문자열(net.error_text와 같은 규칙). 이 모듈은 lib.net을 가져오지 않는다 — HTTP는 LlmClient만 쓴다."""
    problem = getattr(exc, "problem", None)
    hint = getattr(exc, "hint", None)
    if problem:
        return f"{problem} → {hint}" if hint else str(problem)
    return core.redact(str(exc)) or type(exc).__name__


class BudgetExhausted(core.KbError):
    """탐지 POST 예산을 다 씀."""


class DetectError(core.KbError):
    """기준선 호출이 실패해 아무것도 판정할 수 없음. `probes`에 지금까지의 기록이 있다."""

    exit_code = 3

    def __init__(self, message: str, probes: list[dict] | None = None) -> None:
        super().__init__(message)
        self.probes = list(probes or [])


class Budget:
    """POST 횟수 예산. `parent`가 있으면 부모 예산도 함께 줄고, 둘 중 작은 쪽이 남은 양이다."""

    def __init__(self, limit: int = MAX_POSTS, parent: "Budget | None" = None) -> None:
        self.limit = int(limit)
        self.used = 0
        self.parent = parent

    @property
    def remaining(self) -> int:
        own = self.limit - self.used
        return min(own, self.parent.remaining) if self.parent is not None else own

    def take(self, what: str = "") -> None:
        """POST 한 번을 쓴다. 남은 게 없으면 BudgetExhausted(아무것도 줄지 않음)."""
        if self.remaining <= 0:
            raise BudgetExhausted(f"탐지 호출 예산({self.limit}회)을 다 썼습니다" + (f": {what}" if what else ""))
        self.used += 1
        if self.parent is not None:
            self.parent.take(what)

    def sub(self, limit: int) -> "Budget":
        return Budget(limit, parent=self)


# ---------------------------------------------------------------- 후보


@dataclass(frozen=True)
class Candidate:
    name: str
    method: str  # param | system_prefix
    param_name: str | None
    on_value: Any
    off_value: Any
    tokens_ok: bool = True  # 토큰 수 보조 신호를 쓸 수 있는가

    def value(self, on: bool) -> Any:
        return self.on_value if on else self.off_value

    def describe(self, on: bool) -> str:
        value = self.value(on)
        if self.method == "system_prefix":
            return f"시스템 접두 {value}"
        return f"{self.param_name}={json.dumps(value, ensure_ascii=False)}"


CANDIDATES: tuple[Candidate, ...] = (
    Candidate("reasoning_effort", "param", "reasoning_effort", "medium", "none"),
    Candidate("chat_template_kwargs", "param", "chat_template_kwargs", {"enable_thinking": True}, {"enable_thinking": False}),
    Candidate("system_prefix", "system_prefix", None, "/think", "/no_think", tokens_ok=False),
    Candidate("reasoning", "param", "reasoning", {"enabled": True}, {"enabled": False}),
)


def _toggle_request(toggle: Mapping[str, Any] | None, on: bool) -> tuple[dict, str | None]:
    """(body_extra, 시스템 접두어) — 기록된(또는 후보의) 토글을 한 방향으로 적용하는 방법."""
    if not isinstance(toggle, Mapping):
        return {}, None
    method = toggle.get("method")
    value = toggle.get("on_value" if on else "off_value")
    if method == "param":
        name = toggle.get("param_name")
        if isinstance(name, str) and name and value is not None:
            return {name: value}, None
    elif method == "system_prefix":
        word = value if isinstance(value, str) and value.strip() else ("/think" if on else "/no_think")
        return {}, word.strip()
    return {}, None


def _candidate_toggle(candidate: Candidate) -> dict:
    out: dict[str, Any] = {"method": candidate.method}
    if candidate.param_name:
        out["param_name"] = candidate.param_name
    out["on_value"] = candidate.on_value
    out["off_value"] = candidate.off_value
    return out


# ---------------------------------------------------------------- 메시지


def _messages(user: Any, *, system: str = SYSTEM_PROMPT, prefix: str | None = None) -> list[dict]:
    head = f"{prefix}\n{system}" if prefix else system
    return [{"role": "system", "content": head}, {"role": "user", "content": user}]


def _png_chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


@lru_cache(maxsize=None)
def solid_png(width: int = VISION_SIZE, height: int = VISION_SIZE, rgb: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    """한 색으로 칠한 8비트 RGB PNG(표준 라이브러리만)."""
    row = b"\x00" + bytes(rgb) * width
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(row * height, 9))
        + _png_chunk(b"IEND", b"")
    )


def red_png_data_url() -> str:
    """D38: 64×64 빨강 PNG의 data URL."""
    return "data:image/png;base64," + base64.b64encode(solid_png()).decode("ascii")


# ---------------------------------------------------------------- 결과 읽기


def has_trace(result: ChatResult) -> bool:
    """생각의 흔적이 있는가: reasoning·reasoning_content 필드, content 안의 `</think>`(normalize_message)."""
    return bool(result.reasoning and result.reasoning.strip())


def trace_field(result: ChatResult) -> str | None:
    """흔적이 어디서 왔나: 'reasoning', 'reasoning_content', 'think_tag'(content 안). 없으면 None."""
    if not has_trace(result):
        return None
    raw = result.raw if isinstance(result.raw, Mapping) else {}
    choices = raw.get("choices")
    message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], Mapping) else None
    if isinstance(message, Mapping):
        for key in ("reasoning", "reasoning_content"):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                return key
    return "think_tag"


def _completion_tokens(result: ChatResult) -> int | None:
    usage = result.usage if isinstance(result.usage, Mapping) else None
    value = usage.get("completion_tokens") if usage else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _snippet(text: str | None, limit: int = ANSWER_SNIPPET) -> str:
    flat = " ".join((text or "").split())
    if len(flat) > limit:
        flat = flat[: limit - 1] + "…"
    return core.redact(flat)


def _state(result: ChatResult) -> str:
    return f"생각 있음({trace_field(result)})" if has_trace(result) else "생각 없음"


def _log_warning(client: LlmClient, text: str) -> None:
    try:
        core.get_logger(client.cfg.root).warning(f"LLM 탐지: {text}")
    except Exception:  # 로그 실패가 탐지를 막지 않게
        pass


# ---------------------------------------------------------------- POST 한 번


def _post(
    client: LlmClient,
    profile: str,
    budget: Budget,
    probes: list[dict],
    step: str,
    messages: list[dict],
    extra: Mapping[str, Any] | None,
    request: str,
) -> ChatResult | None:
    """예산을 하나 쓰고 chat을 한 번 보낸다. 실패면 None(기록은 probes에). 회사 차단은 그대로 올린다."""
    budget.take(step)
    record: dict[str, Any] = {"step": step, "request": request}
    probes.append(record)
    try:
        result = client.chat(profile, messages, body_extra=dict(extra) if extra else None, max_tokens=PROBE_MAX_TOKENS)
    except core.PublicHostRefused:
        raise
    except ChatHttpError as exc:
        record.update(status="http", http_status=exc.status, error=_snippet(exc.body or str(exc), 200))
        return None
    except core.KbError as exc:
        record.update(status="error", error=_snippet(_error_text(exc), 200))
        return None
    record.update(
        status="ok",
        trace=has_trace(result),
        trace_field=trace_field(result),
        completion_tokens=_completion_tokens(result),
        finish_reason=result.finish_reason,
        answer=_snippet(result.content),
    )
    return result


def _is_4xx(record: Mapping[str, Any]) -> bool:
    status = record.get("http_status")
    return isinstance(status, int) and 400 <= status < 500


def _failure_text(record: Mapping[str, Any]) -> str:
    if record.get("status") == "http":
        return f"{record.get('http_status')} {record.get('error') or ''}".strip()
    return str(record.get("error") or record.get("status"))


# ---------------------------------------------------------------- 컨텍스트


def _model_of(client: LlmClient, profile: str) -> dict:
    getter = getattr(client, "_profile", None)
    if callable(getter):
        return getter(profile)
    return client.cfg.profile(profile)


def _context_value(entry: Mapping[str, Any]) -> tuple[int, str] | None:
    """D41 순서: metadata.context_length → max_model_len → context_length."""
    metadata = entry.get("metadata")
    places = [
        ("metadata.context_length", metadata.get("context_length") if isinstance(metadata, Mapping) else None),
        ("max_model_len", entry.get("max_model_len")),
        ("context_length", entry.get("context_length")),
    ]
    for where, value in places:
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and value > 0 and float(value).is_integer():
            return int(value), where
        if isinstance(value, str) and value.strip().isdigit() and int(value.strip()) > 0:
            return int(value.strip()), where
    return None


def detect_context(client: LlmClient, profile: str) -> dict:
    """GET /models의 메타데이터로 컨텍스트 길이. 없으면 262144를 `assumed`로(호출 수에 넣지 않음)."""
    model = str(_model_of(client, profile).get("model") or "")

    def assumed(why: str) -> dict:
        return {"context_tokens": ASSUMED_CONTEXT, "context_source": "assumed", "evidence": f"{why} → {ASSUMED_CONTEXT} 가정"}

    try:
        models = client.list_models(profile)
    except core.PublicHostRefused:
        raise
    except ChatHttpError as exc:
        return assumed(f"/models 실패({exc.status})")
    except core.KbError as exc:
        return assumed(f"/models 실패({_snippet(_error_text(exc), 120)})")
    if models is None:
        return assumed("/models 없음")
    entry = next((m for m in models if str(m.get("id") or "") == model), None)
    if entry is None and not model and len(models) == 1:
        entry = models[0]
    if entry is None:
        return assumed(f"/models에 모델 '{model}' 없음")
    found = _context_value(entry)
    if found is None:
        return assumed(f"/models의 '{model}'에 컨텍스트 정보 없음")
    value, where = found
    return {"context_tokens": value, "context_source": "models", "evidence": f"/models {where}={value}"}


# ---------------------------------------------------------------- 추론 토글


def _signal(on_result: ChatResult, off_result: ChatResult, candidate: Candidate) -> str | None:
    """켜기·끄기 결과 한 쌍이 구분되는가: 'trace', 'tokens', 또는 None."""
    if not (off_result.content or "").strip():
        return None  # D36: 끄기 쪽 답은 비어 있으면 안 된다
    on_trace, off_trace = has_trace(on_result), has_trace(off_result)
    if on_trace and not off_trace:
        return "trace"
    if on_trace or off_trace or not candidate.tokens_ok:
        return None
    on_tokens, off_tokens = _completion_tokens(on_result), _completion_tokens(off_result)
    if on_tokens is None or off_tokens is None:
        return None
    if on_tokens >= TOKEN_RATIO * off_tokens and on_tokens - off_tokens >= TOKEN_GAP:
        return "tokens"
    return None


def _pair(first: ChatResult, second: ChatResult, second_on: bool) -> tuple[ChatResult, ChatResult]:
    """(켜기 결과, 끄기 결과)."""
    return (second, first) if second_on else (first, second)


def _none_toggle(evidence: str) -> dict:
    return {"method": "none", "on_value": None, "off_value": None, "evidence": evidence, "source": "detected"}


def detect_reasoning(client: LlmClient, profile: str, budget: Budget) -> dict:
    """기준선 1회 → 후보 1~4. 결과: {baseline_reasoning, reasoning_toggle, probes, warnings}.

    기준선이 실패하면 DetectError(판정할 근거가 없다).
    """
    probes: list[dict] = []
    warnings: list[str] = []
    try:
        baseline = _post(client, profile, budget, probes, "baseline", _messages(ARITHMETIC_PROMPT), None, "토글 없음")
    except BudgetExhausted as exc:
        raise DetectError(f"탐지 예산이 없어 기준선을 보내지 못했습니다: {exc}", probes) from exc
    if baseline is None:
        raise DetectError(f"기준선 호출이 실패해 탐지할 수 없습니다: {_failure_text(probes[-1])}", probes)
    base_on = has_trace(baseline)
    base_tokens = _completion_tokens(baseline)
    base_text = f"기준선 {_state(baseline)}" + (f", 출력 {base_tokens}토큰" if base_tokens is not None else ", usage 없음")

    outcomes: list[str] = []

    def finish_none(evidence: str) -> dict:
        return {"baseline_reasoning": base_on, "reasoning_toggle": _none_toggle(evidence), "probes": probes, "warnings": warnings}

    for index, candidate in enumerate(CANDIDATES):
        first_on = not base_on  # 기준선과 반대 방향부터
        try:
            first = _send_candidate(client, profile, budget, probes, candidate, first_on)
        except BudgetExhausted:
            untested = [c.name for c in CANDIDATES[index:]]
            return _exhausted(client, finish_none, warnings, base_text, outcomes, untested)
        if first is None:
            record = probes[-1]
            verdict = "거부" if _is_4xx(record) else "오류"
            outcomes.append(f"{candidate.name}: {verdict}({_failure_text(record)})")
            record["verdict"] = f"{verdict}: 이 방식이 아님" if verdict == "거부" else "오류: 증거 없음"
            continue
        on_r, off_r = _pair(baseline, first, first_on)
        signal = _signal(on_r, off_r, candidate)
        if signal is None:
            outcomes.append(f"{candidate.name}: 200, 변화 없음")
            probes[-1]["verdict"] = "변화 없음(200은 증거 아님)"
            continue
        probes[-1]["verdict"] = f"뒤집힘({signal})"
        try:
            second = _send_candidate(client, profile, budget, probes, candidate, not first_on)
        except BudgetExhausted:
            untested = [f"{candidate.name}(확인 못 함)"] + [c.name for c in CANDIDATES[index + 1 :]]
            return _exhausted(client, finish_none, warnings, base_text, outcomes, untested)
        if second is None:
            outcomes.append(f"{candidate.name}: 뒤집힌 뒤 확인 호출 실패({_failure_text(probes[-1])})")
            probes[-1]["verdict"] = "확인 실패"
            continue
        on_r, off_r = _pair(first, second, not first_on)
        confirm = _signal(on_r, off_r, candidate)
        if confirm is None:
            outcomes.append(f"{candidate.name}: 뒤집혔으나 반대 방향으로 확인되지 않음")
            probes[-1]["verdict"] = "확인 실패"
            continue
        probes[-1]["verdict"] = f"확인({confirm})"
        signals = sorted({signal, confirm})
        toggle = _candidate_toggle(candidate)
        toggle["evidence"] = _adopt_evidence(candidate, baseline, first, second, first_on, signals)
        toggle["source"] = "detected"
        return {"baseline_reasoning": base_on, "reasoning_toggle": toggle, "probes": probes, "warnings": warnings}

    evidence = "none: 켜고 끄기가 구분되는 후보 없음. " + base_text + ". " + "; ".join(outcomes)
    warnings.append(_none_warning(base_on))
    _log_warning(client, warnings[-1])
    return finish_none(evidence)


def _send_candidate(
    client: LlmClient, profile: str, budget: Budget, probes: list[dict], candidate: Candidate, on: bool
) -> ChatResult | None:
    extra, prefix = _toggle_request(_candidate_toggle(candidate), on)
    step = f"{candidate.name}:{'on' if on else 'off'}"
    return _post(client, profile, budget, probes, step, _messages(ARITHMETIC_PROMPT, prefix=prefix), extra, candidate.describe(on))


def _adopt_evidence(
    candidate: Candidate, baseline: ChatResult, first: ChatResult, second: ChatResult, first_on: bool, signals: list[str]
) -> str:
    def part(result: ChatResult, on: bool) -> str:
        tokens = _completion_tokens(result)
        return f"{candidate.describe(on)} {_state(result)}" + (f" {tokens}토큰" if tokens is not None else "")

    base_tokens = _completion_tokens(baseline)
    base = f"기준선 {_state(baseline)}" + (f" {base_tokens}토큰" if base_tokens is not None else "")
    head = "+".join(signals)
    if signals == ["tokens"]:
        head += f"(흔적 없음, 켜기 ≥ {TOKEN_RATIO:g}배·차이 ≥ {TOKEN_GAP})"
    return f"{head}: {base} → {part(first, first_on)} → {part(second, not first_on)}"


def _none_warning(base_on: bool) -> str:
    if base_on:
        return (
            "추론 토글을 찾지 못했습니다(기준선: 생각 켜짐). 모든 작업이 생각하며 답하므로 출력 상한 하한 2048이 "
            "적용됩니다(D41). 서버가 아는 방식이 있으면 detected.reasoning_toggle을 손으로 고치세요"
            "(예: reasoning_budget_tokens, docs/TROUBLESHOOTING.md)"
        )
    return (
        "추론 토글을 찾지 못했습니다(기준선: 생각 꺼짐). reasoning: on 작업도 생각 없이 실행됩니다. "
        "서버가 아는 방식이 있으면 detected.reasoning_toggle을 손으로 고치세요(docs/TROUBLESHOOTING.md)"
    )


def _exhausted(client, finish_none, warnings: list[str], base_text: str, outcomes: list[str], untested: list[str]) -> dict:
    evidence = (
        f"budget_exhausted: 탐지 호출 {MAX_POSTS}회 제한으로 중단. {base_text}. "
        + ("; ".join(outcomes) + ". " if outcomes else "")
        + "시험 못 한 후보: "
        + ", ".join(untested)
    )
    warnings.append(
        f"추론 토글 탐지가 호출 제한({MAX_POSTS}회)에 걸려 method: none으로 기록합니다. "
        "kb.py init --redetect로 다시 시도하거나 detected.reasoning_toggle을 손으로 고치세요"
    )
    _log_warning(client, warnings[-1])
    return finish_none(evidence)


# ---------------------------------------------------------------- JSON·비전


def _off_request(toggle: Mapping[str, Any] | None) -> tuple[dict, str | None, str]:
    extra, prefix = _toggle_request(toggle, False)
    method = toggle.get("method") if isinstance(toggle, Mapping) else None
    if method in ("param", "system_prefix") and (extra or prefix):
        what = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in extra.items()) or f"시스템 접두 {prefix}"
        return extra, prefix, f"끄기 상태({what})"
    return {}, None, "토글 없음"


def detect_json(client: LlmClient, profile: str, budget: Budget, *, toggle: Mapping[str, Any] | None = None) -> dict:
    """response_format json_object로 1회. `json.loads`가 JSON 객체를 읽어야 참(D38).

    content가 비고 reasoning 필드에서만 JSON이 나오면 참 + note `json_in_reasoning`.
    결과: {json_mode, evidence, note?, probes}.
    """
    probes: list[dict] = []
    extra, prefix, state = _off_request(toggle)
    extra = {**extra, "response_format": {"type": "json_object"}}
    try:
        result = _post(
            client, profile, budget, probes, "json_mode", _messages(JSON_PROMPT, system=JSON_SYSTEM_PROMPT, prefix=prefix),
            extra, f"response_format=json_object, {state}",
        )
    except BudgetExhausted:
        return {"json_mode": False, "evidence": "budget_exhausted: 보내지 못함", "probes": probes}
    if result is None:
        record = probes[-1]
        verdict = f"거부({_failure_text(record)})" if _is_4xx(record) else f"오류({_failure_text(record)})"
        record["verdict"] = verdict
        return {"json_mode": False, "evidence": f"response_format {verdict}", "probes": probes}
    out: dict[str, Any] = {"probes": probes}
    try:
        data = json.loads(result.content)
    except ValueError:
        data = None
    if isinstance(data, dict):
        out.update(json_mode=True, evidence="content가 json.loads로 JSON 객체")
    elif not result.content.strip() and result.reasoning:
        try:
            parse_json_object(result.reasoning)
        except SchemaError:
            out.update(json_mode=False, evidence="content 비었고 reasoning에서도 JSON 객체 없음")
        else:
            out.update(json_mode=True, evidence="content 비었고 JSON은 reasoning 필드에만 있음", note="json_in_reasoning")
    else:
        why = "빈 답" if not result.content.strip() else "json.loads 실패" if data is None else "JSON 객체 아님"
        out.update(json_mode=False, evidence=f"200이지만 {why}(finish={result.finish_reason})")
    probes[-1]["verdict"] = out["evidence"]
    return out


def detect_vision(client: LlmClient, profile: str, budget: Budget, *, toggle: Mapping[str, Any] | None = None) -> dict:
    """64×64 빨강 PNG를 넣어 1회. 답에 빨강/빨간/red가 있어야 참(D38). 결과: {vision, evidence, note?, probes}."""
    probes: list[dict] = []
    extra, prefix, state = _off_request(toggle)
    user = [
        {"type": "text", "text": VISION_PROMPT},
        {"type": "image_url", "image_url": {"url": red_png_data_url()}},
    ]
    try:
        result = _post(
            client, profile, budget, probes, "vision", _messages(user, prefix=prefix), extra or None,
            f"{VISION_SIZE}×{VISION_SIZE} 빨강 PNG, {state}",
        )
    except BudgetExhausted:
        return {"vision": False, "evidence": "budget_exhausted: 보내지 못함", "probes": probes}
    if result is None:
        record = probes[-1]
        verdict = f"거부({_failure_text(record)})" if _is_4xx(record) else f"오류({_failure_text(record)})"
        record["verdict"] = verdict
        return {"vision": False, "evidence": f"이미지 요청 {verdict}", "probes": probes}
    out: dict[str, Any] = {"probes": probes}
    if _RED.search(result.content or ""):
        out.update(vision=True, evidence="답에 빨강이 있음")
    elif not (result.content or "").strip() and result.reasoning and _RED.search(result.reasoning):
        out.update(vision=True, evidence="content 비었고 reasoning 필드에 빨강이 있음", note="vision_in_reasoning")
    else:
        out.update(vision=False, evidence=f"200이지만 답에 빨강 없음(이미지를 보지 않음): {_snippet(result.content, 40)!r}")
    probes[-1]["verdict"] = out["evidence"]
    return out


# ---------------------------------------------------------------- 전체


def detect(client: LlmClient, profile: str) -> dict:
    """프로필 하나의 `detected` 블록을 만든다(POST ≤ 10). 저장은 호출자(init·doctor --redetect)가 한다.

    반환: {context_tokens, context_source, json_mode, vision, reasoning_toggle{method, param_name?,
    on_value, off_value, evidence, source}, baseline_reasoning, probes[, notes][, warnings]}.
    기준선 호출이 실패하면 DetectError. 회사 환경의 공개 호스트는 PublicHostRefused.
    """
    base_url = _model_of(client, profile).get("base_url")
    assert_host_allowed(client.cfg, base_url if isinstance(base_url, str) else "")
    budget = Budget(MAX_POSTS)

    context = detect_context(client, profile)
    probes: list[dict] = [{"step": "context", "request": "GET /models", "status": context["context_source"], "verdict": context["evidence"]}]

    try:
        reasoning = detect_reasoning(client, profile, budget.sub(MAX_POSTS - RESERVED_POSTS))
    except DetectError as exc:
        exc.probes = probes + exc.probes
        raise
    probes += reasoning["probes"]
    toggle = reasoning["reasoning_toggle"]

    json_part = detect_json(client, profile, budget, toggle=toggle)
    vision_part = detect_vision(client, profile, budget, toggle=toggle)
    probes += json_part["probes"] + vision_part["probes"]

    notes = [part["note"] for part in (json_part, vision_part) if part.get("note")]
    if toggle.get("evidence", "").startswith("budget_exhausted"):
        notes.append("budget_exhausted")
    detected: dict[str, Any] = {
        "context_tokens": context["context_tokens"],
        "context_source": context["context_source"],
        "json_mode": bool(json_part["json_mode"]),
        "vision": bool(vision_part["vision"]),
        "reasoning_toggle": toggle,
        "baseline_reasoning": bool(reasoning["baseline_reasoning"]),
        "probes": probes,
    }
    if notes:
        detected["notes"] = notes
    if reasoning["warnings"]:
        detected["warnings"] = list(reasoning["warnings"])
    return detected


__all__ = [
    "ARITHMETIC_PROMPT",
    "Budget",
    "BudgetExhausted",
    "CANDIDATES",
    "Candidate",
    "DetectError",
    "MAX_POSTS",
    "PROBE_MAX_TOKENS",
    "detect",
    "detect_context",
    "detect_json",
    "detect_reasoning",
    "detect_vision",
    "has_trace",
    "red_png_data_url",
    "solid_png",
    "trace_field",
]
