"""실 모델 검증(지시서 §7, `pytest -m live`). 집에서만, DEEPINFRA_API_KEY가 있을 때만 돈다.

    $env:KB_REQUIRE_LIVE='1'; .venv\\Scripts\\python.exe -m pytest -q -m live tests/test_live.py -s

- 키가 없으면 skip. `KB_REQUIRE_LIVE=1`이면 skip 대신 fail. 둘 다 가져올 때 읽는다
  (conftest의 autouse 픽스처가 KB_*를 지우기 때문. `live` 테스트에는 키를 남겨 둔다).
- 회사 환경(볼트 설정 또는 저장소의 config.local.yaml이 home이 아님)이면 fail: 공개 호스트로 보내지 않는다.
- 허구 자료만 쓴다(홍길동·김철수, 회사 이름 없음). 문구가 아니라 형태·능력만 검사한다.
- 채팅 POST는 세션 전체 20회 상한(실제 전송 기준, 429 재시도 포함). 넘으면 보내기 전에 fail.
- 관찰 결과는 `OBSERVATIONS`에 모아 끝에 출력하고 docs/LIVE.md의 관찰 구역에 쓴다(비밀값 없음).

공급자 주소는 이 파일과 config.local.example.yaml에만 쓴다.
"""
from __future__ import annotations

import copy
import hashlib
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

import pytest
import requests
import yaml

import compile as kb_compile
import ingest_images
from lib import config, core, frontmatter, llm_client, llm_detect, managed, prompts

pytestmark = pytest.mark.live

# 가져올 때 읽는다(테스트가 돌 때는 conftest가 KB_*를 지운 뒤다).
_API_KEY = os.environ.get("DEEPINFRA_API_KEY") or ""
_REQUIRE_LIVE = os.environ.get("KB_REQUIRE_LIVE", "").strip() == "1"

ROOT = Path(__file__).resolve().parents[1]
LIVE_MD = ROOT / "docs" / "LIVE.md"
OBS_START = "<!-- live:observed:start -->"
OBS_END = "<!-- live:observed:end -->"

BASE_URL = "https://api.deepinfra.com/v1/openai"
MAIN_MODEL = "nvidia/NVIDIA-Nemotron-3.5-Super-VL"
FALLBACK_MODEL = "google/gemma-4-26B-A4B-it"
BAD_MODEL = "kb-live-test/no-such-model"
MAX_POSTS = 20

DAY = "2026-11-03"
DAILY = f"""---
date: {DAY}
---
## 오늘
<!-- kb:today:start -->
<!-- kb:today:end -->

## 이월
<!-- kb:carryover:start -->
<!-- kb:carryover:end -->

## 기록
- 09:10 [[KB-12]] 캐시 만료 시각은 서버에서 한 번만 계산하기로 결정했다. @김철수 동의
- 11:30 @김철수 에게 캐시 설계 리뷰를 요청함, 금요일까지
- 15:00 사내 스터디에서 front matter 필드를 최소로 두는 방법을 배움
"""

KB12 = """---
key: KB-12
summary: 캐시 front matter 확정
status: In Progress
status_category: indeterminate
issuetype: Task
project: KB
assignee: 홍길동
due: 2026-11-06
---
# KB-12 캐시 front matter 확정

## 설명
허구의 일감입니다.

## 노트에서 언급
<!-- kb:mentions:start -->
<!-- kb:mentions:end -->
"""

OBSERVATIONS: dict[str, Any] = {}
_STATE: dict[str, Any] = {"stage": "-", "posts": 0, "gets": 0, "wire": [], "outcomes": [], "detected": None}


# ---------------------------------------------------------------- 문 지기


def _gate() -> None:
    if not _API_KEY:
        message = "DEEPINFRA_API_KEY가 없어 실 모델 검증을 건너뜁니다"
        if _REQUIRE_LIVE:
            pytest.fail(message + " (KB_REQUIRE_LIVE=1)")
        pytest.skip(message)
    local = ROOT / "config.local.yaml"
    if local.is_file():
        data = yaml.safe_load(local.read_bytes().decode("utf-8")) or {}
        if str(data.get("environment") or "company") != "home":
            pytest.fail("회사 환경(config.local.yaml)에서는 실 모델 검증을 하지 않습니다")


def _secret_free(text: str) -> str:
    if _API_KEY and _API_KEY in text:
        raise AssertionError("관찰 기록에 API 키가 들어갔습니다")
    return text


# ---------------------------------------------------------------- 전송 계수(실제 HTTP)


def _short(text: Any, limit: int = 160) -> str:
    flat = " ".join(str(text or "").split())
    return core.redact(flat[: limit - 1] + "…" if len(flat) > limit else flat)


def _wire_entry(body: Any, resp: requests.Response | None, elapsed: float, error: str | None) -> dict:
    entry: dict[str, Any] = {"stage": _STATE["stage"]}
    if isinstance(body, dict):
        entry["model"] = body.get("model")
        entry["max_tokens"] = body.get("max_tokens")
        extra = {k: v for k, v in body.items() if k not in ("messages", "model", "max_tokens", "temperature")}
        if extra:
            entry["params"] = extra
        messages = body.get("messages") or []
        system = next((m.get("content") for m in messages if isinstance(m, dict) and m.get("role") == "system"), None)
        if isinstance(system, str):
            for word in ("/no_think", "/think"):
                if re.search(rf"(^|\n){re.escape(word)}(\n|$)", system):
                    entry["system_prefix"] = word
                    break
            if llm_client.FORCED_JSON_RULE in system:
                entry["forced_json_prompt"] = True
        entry["images"] = sum(
            1
            for m in messages
            if isinstance(m, dict) and isinstance(m.get("content"), list)
            for part in m["content"]
            if isinstance(part, dict) and part.get("type") == "image_url"
        )
    entry["elapsed_s"] = round(elapsed, 1)
    if error is not None:
        entry["error"] = _short(error)
        return entry
    assert resp is not None
    entry["http"] = resp.status_code
    if not 200 <= resp.status_code < 300:
        entry["body"] = _short(resp.content.decode("utf-8", errors="replace"), 200)
        return entry
    try:
        payload = resp.json()
        choice = payload["choices"][0]
        message = choice.get("message") or {}
    except Exception:  # 형태가 다르면 그대로 남긴다
        entry["body"] = _short(resp.content.decode("utf-8", errors="replace"), 200)
        return entry
    entry["finish_reason"] = choice.get("finish_reason")
    entry["message_fields"] = sorted(k for k, v in message.items() if v not in (None, "", []))
    content = message.get("content")
    if isinstance(content, str):
        entry["content_chars"] = len(content)
        entry["think_tag_in_content"] = "</think>" in content
    usage = payload.get("usage") or {}
    entry["usage"] = {
        k: usage.get(k) for k in ("prompt_tokens", "completion_tokens") if isinstance(usage.get(k), int)
    }
    details = usage.get("completion_tokens_details")
    if isinstance(details, dict) and details.get("reasoning_tokens") is not None:
        entry["usage"]["reasoning_tokens"] = details.get("reasoning_tokens")
    return entry


@pytest.fixture(scope="module", autouse=True)
def _wire():
    """requests.Session.request를 감싸 실제 채팅 POST를 센다. 21번째는 보내기 전에 fail."""
    _gate()
    original = requests.Session.request

    def counted(self, method, url, *args, **kwargs):
        is_chat = str(method).upper() == "POST" and str(url).rstrip("/").endswith("/chat/completions")
        if not is_chat:
            _STATE["gets"] += 1
            return original(self, method, url, *args, **kwargs)
        if _STATE["posts"] >= MAX_POSTS:
            pytest.fail(f"채팅 POST 상한 {MAX_POSTS}회를 넘으려 했습니다 (단계 {_STATE['stage']})")
        _STATE["posts"] += 1
        started = time.monotonic()
        try:
            resp = original(self, method, url, *args, **kwargs)
        except Exception as exc:
            _STATE["wire"].append(_wire_entry(kwargs.get("json"), None, time.monotonic() - started, f"{type(exc).__name__}: {exc}"))
            raise
        _STATE["wire"].append(_wire_entry(kwargs.get("json"), resp, time.monotonic() - started, None))
        return resp

    original_run_task = llm_client.LlmClient.run_task

    def spied_run_task(self, task, messages_for, **kwargs):
        outcome = original_run_task(self, task, messages_for, **kwargs)
        _STATE["outcomes"].append(
            {
                "stage": _STATE["stage"],
                "task": task,
                "status": outcome.status,
                "profile": outcome.profile,
                "data": copy.deepcopy(outcome.data),
                "attempts": [
                    {k: (_short(v, 200) if k == "error" and v else v) for k, v in a.items() if v is not None}
                    for a in outcome.attempts
                ],
                "forced_json_profiles": sorted(self._forced_json),  # D38이 발동했는가
            }
        )
        return outcome

    mp = pytest.MonkeyPatch()
    mp.setattr(requests.Session, "request", counted)
    mp.setattr(llm_client.LlmClient, "run_task", spied_run_task)
    try:
        yield
    finally:
        mp.undo()


@pytest.fixture(scope="session", autouse=True)
def _report():
    """끝에 OBSERVATIONS를 출력하고 docs/LIVE.md의 관찰 구역에 쓴다."""
    yield
    if not _STATE["wire"] and not OBSERVATIONS:
        return
    OBSERVATIONS["total"] = {"chat_posts": _STATE["posts"], "other_requests": _STATE["gets"], "cap": MAX_POSTS}
    OBSERVATIONS["wire"] = _STATE["wire"]
    text = _secret_free(yaml.safe_dump(OBSERVATIONS, allow_unicode=True, sort_keys=False, width=120))
    print("\n==== live OBSERVATIONS ====")
    print(text)
    print(f"채팅 POST {_STATE['posts']}회 / 상한 {MAX_POSTS}")
    stamp = time.strftime("%Y-%m-%d %H:%M")
    block = f"{OBS_START}\n마지막 실행: {stamp} (자동 기록, tests/test_live.py)\n\n```yaml\n{text.rstrip()}\n```\n{OBS_END}"
    old = LIVE_MD.read_bytes().decode("utf-8") if LIVE_MD.is_file() else "# 실 모델 검증\n"
    if OBS_START in old and OBS_END in old:
        head, _, rest = old.partition(OBS_START)
        _, _, tail = rest.partition(OBS_END)
        new = head + block + tail
    else:
        new = old.rstrip("\n") + "\n\n## 관찰 (자동 기록)\n\n" + block + "\n"
    LIVE_MD.parent.mkdir(parents=True, exist_ok=True)
    LIVE_MD.write_bytes(new.encode("utf-8"))


# ---------------------------------------------------------------- 볼트


def _profiles(main_model: str = MAIN_MODEL) -> dict:
    ref = "env:DEEPINFRA_API_KEY"
    return {
        "main": {"base_url": BASE_URL, "model": main_model, "api_key_ref": ref},
        "fallback": {"base_url": BASE_URL, "model": FALLBACK_MODEL, "api_key_ref": ref},
    }


def _local(profiles: dict) -> dict:
    return {
        "environment": "home",
        "jira": {"project_keys": ["KB", "OPS"]},
        "llm": {"enabled": True, "profiles": profiles, "fallback_order": ["main", "fallback"]},
    }


def _write_local(root: Path, data: dict) -> None:
    text = _secret_free(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    (root / "config.local.yaml").write_bytes(text.encode("utf-8"))


def _make_vault(base: Path, local: dict) -> Path:
    root = base / "한글 경로 (실모델)" / "work-kb"
    for sub in ("daily", "inbox", "raw/assets", "jira/_views", "wiki/projects", "wiki/people", "wiki/topics", "_trash", ".state"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "config.yaml", root / "config.yaml")
    _write_local(root, local)
    return root


@pytest.fixture(scope="module")
def live_vault(tmp_path_factory) -> Path:
    root = _make_vault(tmp_path_factory.mktemp("live"), _local(_profiles()))
    (root / "daily" / f"{DAY}.md").write_bytes(DAILY.encode("utf-8"))
    (root / "jira" / "KB-12.md").write_bytes(KB12.encode("utf-8"))
    _STATE["daily_sha"] = _daily_sha(root)
    return root


def _daily_sha(root: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((root / "daily").glob("*.md"))}


def _load(root: Path) -> config.Config:
    cfg = config.load(root, environ={})
    if cfg.environment != "home":
        pytest.fail("볼트 설정이 회사 환경입니다. 공개 호스트로 보내지 않습니다")
    return cfg


def _need_detected() -> dict:
    detected = _STATE.get("detected")
    if not isinstance(detected, dict):
        pytest.skip("main 탐지 결과가 없어 건너뜁니다(test_detect_main 실패)")
    return detected


def _outcomes(stage: str) -> list[dict]:
    return [o for o in _STATE["outcomes"] if o["stage"] == stage]


def _posts(stage: str) -> int:
    return sum(1 for w in _STATE["wire"] if w["stage"] == stage)


def _attempt_view(outcomes: list[dict]) -> list[dict]:
    return [
        {"task": o["task"], "status": o["status"], "profile": o["profile"], "attempts": o["attempts"], "forced_json_profiles": o["forced_json_profiles"]}
        for o in outcomes
    ]


# ---------------------------------------------------------------- 1. 탐지


def test_detect_main(live_vault):
    _STATE["stage"] = "detect"
    cfg = _load(live_vault)
    client = llm_client.LlmClient(cfg)
    started = time.monotonic()
    block = llm_detect.detect(client, "main")
    elapsed = time.monotonic() - started
    posts = _posts("detect")

    toggle = block.get("reasoning_toggle") or {}
    OBSERVATIONS["detect"] = {
        "model": MAIN_MODEL,
        "method": toggle.get("method"),
        "param_name": toggle.get("param_name"),
        "on_value": toggle.get("on_value"),
        "off_value": toggle.get("off_value"),
        "evidence": toggle.get("evidence"),
        "baseline_reasoning": block.get("baseline_reasoning"),
        "json_mode": block.get("json_mode"),
        "vision": block.get("vision"),
        "context_tokens": block.get("context_tokens"),
        "context_source": block.get("context_source"),
        "notes": block.get("notes"),
        "warnings": block.get("warnings"),
        "probes": [
            {k: p.get(k) for k in ("step", "request", "status", "http_status", "trace", "trace_field", "completion_tokens", "finish_reason", "answer", "verdict", "error") if p.get(k) is not None}
            for p in block.get("probes") or []
        ],
        "trace_fields": sorted({p.get("trace_field") for p in block.get("probes") or [] if p.get("trace_field")}),
        "posts": posts,
        "elapsed_s": round(elapsed, 1),
    }

    assert {"json_mode", "vision", "reasoning_toggle"} <= set(block)
    assert toggle.get("method") in ("param", "system_prefix", "none")
    assert isinstance(block.get("baseline_reasoning"), bool)
    assert isinstance(block.get("context_tokens"), int) and block["context_tokens"] > 0
    assert posts <= llm_detect.MAX_POSTS

    # 다음 테스트(T1·T2·T6)가 쓰도록 볼트 설정에 저장(probes는 빼고)
    detected = {k: v for k, v in block.items() if k != "probes"}
    _STATE["detected"] = detected
    profiles = _profiles()
    profiles["main"]["detected"] = detected
    _write_local(live_vault, _local(profiles))


# ---------------------------------------------------------------- 2. T1 tag


def test_t1_tag(live_vault):
    _need_detected()
    _STATE["stage"] = "t1_tag"
    cfg = _load(live_vault)
    lines: list[str] = []
    result = kb_compile.run(cfg, tasks=["tag"], day=DAY, out=lines.append)
    ledger = core.load_state(cfg.root, "compile").get("tag") or {}
    allowed = set(kb_compile.fixed_tags())
    entries = {k.split("#", 1)[0] + "#" + k.split("#", 1)[1][:8]: v for k, v in ledger.items()}
    OBSERVATIONS["t1_tag"] = {
        "status": result["status"],
        "summary": [line for line in lines if line.startswith("compile:")],
        "ledger": {k: {"status": v.get("status"), "tag": v.get("tag"), "profile": v.get("profile")} for k, v in entries.items()},
        "runs": _attempt_view(_outcomes("t1_tag")),
        "posts": _posts("t1_tag"),
    }

    assert result["status"] == "ok"
    ok = [v for v in ledger.values() if v.get("status") == "ok"]
    assert ok, f"태그 원장에 ok가 없습니다: {ledger}"
    assert all(v.get("tag") in allowed for v in ok)


# ---------------------------------------------------------------- 3. T2 decisions


_ENTRY = re.compile(r"^## (\d{4}-\d{2}-\d{2}) (.+)$", re.MULTILINE)
_SOURCE = re.compile(r"^- 출처: \[\[(\d{4}-\d{2}-\d{2})\]\](?: (\d{1,2}:\d{2}))? <!-- src:([0-9a-f]+) -->$", re.MULTILINE)


def test_t2_decisions(live_vault):
    _need_detected()
    _STATE["stage"] = "t2_decisions"
    cfg = _load(live_vault)
    lines: list[str] = []
    result = kb_compile.run(cfg, tasks=["decisions"], day=DAY, out=lines.append)
    data = (cfg.root / "wiki" / "decisions.md").read_bytes() if (cfg.root / "wiki" / "decisions.md").is_file() else b""
    region = (managed.read_region(data, "decisions") or b"").decode("utf-8") if data else ""
    blocks = [b for b in re.split(r"(?m)^(?=## )", region) if b.startswith("## ")]
    daily_rel = f"daily/{DAY}.md"
    daily_bytes = (cfg.root / "daily" / f"{DAY}.md").read_bytes()
    valid_src = {kb_compile._src(item) for item in kb_compile._record_items(daily_rel, DAY, daily_bytes)}
    actions = cfg.root / "wiki" / "actions.md"
    action_region = (managed.read_region(actions.read_bytes(), "actions") or b"").decode("utf-8") if actions.is_file() else ""
    OBSERVATIONS["t2_decisions"] = {
        "status": result["status"],
        "summary": [line for line in lines if line.startswith("compile:")],
        "problems": result["problems"],
        "decision_entries": len(blocks),
        "action_entries": sum(1 for line in action_region.splitlines() if line.startswith("- [ ]")),
        "entries_with_state_proposal": sum(1 for b in blocks if "\n- 상태: 제안" in b),
        "related_lines": [line for b in blocks for line in b.splitlines() if line.startswith("- 관련:")],
        "runs": _attempt_view(_outcomes("t2_decisions")),
        "posts": _posts("t2_decisions"),
    }

    assert result["status"] == "ok"
    assert blocks, f"decisions.md에 결정이 없습니다: {lines}"
    resolved = 0
    for block in blocks:
        assert "\n- 상태: 제안" in block
        source = _SOURCE.search(block)
        assert source, f"출처 줄이 없습니다: {block!r}"
        day, _time, src = source.groups()
        assert (cfg.root / "daily" / f"{day}.md").is_file()
        if src in valid_src:
            resolved += 1
    assert resolved >= 1, "daily 줄로 풀리는 출처가 없습니다"
    assert _daily_sha(cfg.root) == _STATE["daily_sha"], "daily 바이트가 바뀌었습니다"


# ---------------------------------------------------------------- 4. T6 image_ingest


def test_t6_image_ingest(live_vault):
    detected = _need_detected()
    _STATE["stage"] = "t6_image"
    cfg = _load(live_vault)
    shutil.copy(ROOT / "tests" / "fixtures" / "images" / "whiteboard.png", cfg.root / "inbox" / "whiteboard.png")
    lines: list[str] = []
    report = ingest_images.run(cfg, out=lines.append)
    runs = _outcomes("t6_image")
    data = next((o["data"] for o in runs if o["status"] == "ok" and o["data"]), None) or {}
    sidecar_rel = report["processed"][0]["sidecar"] if report["processed"] else None
    sidecar = cfg.root / sidecar_rel if sidecar_rel else None
    fields: dict = {}
    extracted = ""
    if sidecar is not None and sidecar.is_file():
        raw = sidecar.read_bytes()
        fields = frontmatter.parse(raw)
        extracted = (managed.read_region(raw, "extracted") or b"").decode("utf-8")
    OBSERVATIONS["t6_image"] = {
        "status": report["status"],
        "vision_detected": detected.get("vision"),
        "processed": report["processed"],
        "skipped": report["skipped"],
        "failed": report["failed"],
        "model_kind": data.get("kind"),
        "model_jira_keys": data.get("jira_keys"),
        "model_people": data.get("people"),
        "text_lines": len(str(data.get("text") or "").splitlines()),
        "sidecar_kind": fields.get("kind"),
        "sidecar_has": {
            "요약": "\n- 요약: " in "\n" + extracted,
            "키": "\n- 키: " in "\n" + extracted,
            "읽은 글": "### 읽은 글" in extracted,
        },
        "runs": _attempt_view(runs),
        "posts": _posts("t6_image"),
    }

    assert report["status"] == "ok", lines
    assert len(report["processed"]) == 1, lines
    assert sidecar is not None and sidecar.is_file()
    assert fields.get("kind") in ingest_images.KINDS
    assert fields.get("source_image", "").startswith("raw/assets/")
    assert "- 요약: " in extracted and "### 읽은 글" in extracted
    assert {"kind", "text", "summary"} <= set(data)
    assert "KB-12" in [str(k).strip().upper() for k in data.get("jira_keys") or []]
    assert "[[KB-12]]" in extracted
    assert _daily_sha(cfg.root) == _STATE["daily_sha"], "daily 바이트가 바뀌었습니다"


# ---------------------------------------------------------------- 5. 대체 프로필


def test_fallback_profile(tmp_path):
    detected = _need_detected()
    _STATE["stage"] = "fallback"
    profiles = _profiles(main_model=BAD_MODEL)
    profiles["main"]["detected"] = copy.deepcopy(detected)
    root = _make_vault(tmp_path, _local(profiles))
    cfg = _load(root)
    client = llm_client.LlmClient(cfg)
    tags = ", ".join(kb_compile.fixed_tags())
    user = (
        f"고정 태그: {tags}\n\n줄 목록 (줄 id, 날짜, 원문):\n"
        f"[L1] {DAY} 11:30 @김철수 에게 캐시 설계 리뷰를 요청함\n\n"
        '출력: {"tags": {"<줄 id>": "<태그>"}} — 모든 줄 id에 태그 하나씩.'
    )
    outcome = client.run_task("tag", prompts.build_messages("tag", user), validate=kb_compile.validate_tag)
    OBSERVATIONS["fallback"] = {
        "bad_main_model": BAD_MODEL,
        "status": outcome.status,
        "profile": outcome.profile,
        "data": outcome.data,
        "attempts": _attempt_view(_outcomes("fallback"))[0]["attempts"] if _outcomes("fallback") else [],
        "posts": _posts("fallback"),
    }

    assert outcome.status == "ok"
    assert outcome.profile == "fallback"
    first = outcome.attempts[0]
    assert first["profile"] == "main" and first["status"] == "http"
    assert isinstance(first["http_status"], int) and 400 <= first["http_status"] < 500
    assert outcome.attempts[-1]["profile"] == "fallback" and outcome.attempts[-1]["status"] == "ok"
    assert _STATE["posts"] <= MAX_POSTS
