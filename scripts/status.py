"""상태 요약 (`kb.py status`).

한 줄에 한 항목, 한국어. 네트워크·비밀값 저장소는 쓰지 않고 파일만 읽는다. 아무것도 쓰지 않는다.
항목: 환경, config.local.yaml, 오늘 daily, 마지막 동기화(.state/sync.json), outbox 대기·실패,
미처리 제안(wiki/_proposals.md), 미처리 이미지(사이드카 없는 inbox 이미지와 daily에 임베드된
raw/assets 이미지), LLM 프로필과 탐지 결과,
잠금 소유자(.state/kb.lock), KB_AGENT.
`--json`이면 같은 내용을 JSON 객체로 낸다(키는 collect() 참고).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

from lib import config, core, managed, notes
from lib.config import Config

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
NOT_DETECTED = "탐지 안 됨"
_LIST_ITEM = re.compile(r"^(?:[-*+] |\d+[.)] )")


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


# ---------------------------------------------------------------- 항목별 수집


def last_sync(cfg: Config, problems: list[str]) -> str | None:
    """`.state/sync.json`의 `last_sync`. 파일·값이 없으면 None."""
    try:
        state = core.load_state(cfg.root, "sync")
    except (core.KbError, OSError) as exc:
        problems.append(f".state/sync.json을 읽지 못했습니다 ({exc})")
        return None
    value = state.get("last_sync") if isinstance(state, Mapping) else None
    return str(value) if value else None


def outbox_counts(cfg: Config, problems: list[str]) -> dict[str, int]:
    """`jira/outbox.jsonl`의 상태별 개수. 파일이 없으면 0."""
    counts = {"pending": 0, "sending": 0, "failed": 0}
    path = cfg.path("jira", "outbox.jsonl")
    if not path.is_file():
        return counts
    broken = 0
    try:
        raw = path.read_bytes()
    except OSError as exc:
        problems.append(f"jira/outbox.jsonl을 읽지 못했습니다 ({exc})")
        return counts
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError):
            broken += 1
            continue
        status = entry.get("status") if isinstance(entry, Mapping) else None
        if status in counts:
            counts[status] += 1
    if broken:
        problems.append(f"jira/outbox.jsonl에 읽지 못한 줄 {broken}개")
    return counts


def pending_proposals(cfg: Config, problems: list[str]) -> int:
    """`wiki/_proposals.md`의 `proposals` 구역 안 최상위 목록 항목 수. 없으면 0.

    Phase 4가 제안 형식(번호·상태)을 정하면 거기에 맞춰 좁힌다.
    """
    path = cfg.path("wiki", "_proposals.md")
    if not path.is_file():
        return 0
    try:
        body = managed.read_region(path.read_bytes(), "proposals")
        text = notes.decode(body) if body is not None else ""
    except (OSError, UnicodeDecodeError, managed.MarkerError) as exc:
        problems.append(f"wiki/_proposals.md를 읽지 못했습니다 ({exc})")
        return 0
    return sum(1 for line in text.splitlines() if _LIST_ITEM.match(line))


def _embedded_assets(cfg: Config) -> set[str]:
    """daily `## 기록`에 `![[raw/assets/…]]`(또는 이름만 `![[파일.png]]`, D64)로 임베드된 이미지 파일명."""
    names: set[str] = set()
    base = cfg.path("daily")
    if not base.is_dir():
        return names
    for path in sorted(base.glob("*.md")):
        if not core.is_scannable(path) or not path.is_file():
            continue
        try:
            data = path.read_bytes()
            if b"![[" not in data:
                continue
            lines = notes.record_lines(f"daily/{path.name}", data)
        except (OSError, UnicodeDecodeError):
            continue
        for line in lines:
            if line.in_code or line.region is not None or "![[" not in line.text:
                continue
            for target in notes.image_embeds(line.text):
                target = target.replace("\\", "/")
                if target.startswith("raw/assets/"):
                    target = target[len("raw/assets/"):]
                if "/" not in target and Path(target).suffix.lower() in IMAGE_SUFFIXES:
                    names.add(target)
    return names


def unprocessed_images(cfg: Config) -> int:
    """사이드카(`inbox/<이미지 이름>.md`, D21)가 없는 이미지 수.

    - `inbox/*.png|jpg|jpeg|webp`
    - daily `## 기록`에 `![[raw/assets/…]]`로 임베드되었고 `raw/assets`에 있는 이미지(캡처 C·F의 결과, §6.5 T6)
    """
    count = 0
    inbox = cfg.path("inbox")
    if inbox.is_dir():
        for path in inbox.iterdir():
            if path.suffix.lower() in IMAGE_SUFFIXES and path.is_file() and core.is_scannable(path):
                if not path.with_suffix(".md").exists():
                    count += 1
    assets = cfg.path("raw", "assets")
    for name in sorted(_embedded_assets(cfg)):
        image = assets / name
        if image.is_file() and not cfg.path("inbox", f"{Path(name).stem}.md").exists():
            count += 1
    return count


def _yes(value: Any) -> str:
    if value is True:
        return "예"
    if value is False:
        return "아니오"
    return str(value)


def summarize_detected(detected: Any) -> str:
    """`detected` 블록 한 줄 요약. 없으면 '탐지 안 됨'."""
    if not isinstance(detected, Mapping) or not detected:
        return NOT_DETECTED
    parts: list[str] = []
    if "vision" in detected:
        parts.append(f"비전 {_yes(detected['vision'])}")
    if "json_mode" in detected:
        parts.append(f"JSON 모드 {_yes(detected['json_mode'])}")
    toggle = detected.get("reasoning_toggle")
    if isinstance(toggle, Mapping) and toggle.get("method"):
        parts.append(f"추론 토글 {toggle['method']}")
    tokens = detected.get("context_tokens")
    if isinstance(tokens, int) and not isinstance(tokens, bool):
        parts.append(f"컨텍스트 {tokens:,}")
    return ", ".join(parts) or "기록됨"


def llm_info(cfg: Config, problems: list[str]) -> dict[str, Any]:
    order = cfg.get("llm.fallback_order")
    info: dict[str, Any] = {
        "enabled": _truthy(cfg.get("llm.enabled"), False),
        "fallback_order": [str(x) for x in order] if isinstance(order, (list, tuple)) else [],
        "profiles": {},
    }
    try:
        profiles = cfg.profiles()
    except core.KbError as exc:
        problems.append(f"llm.profiles: {exc}")
        return info
    for name, profile in profiles.items():
        url = profile.get("base_url")
        url = url if isinstance(url, str) and url.strip() else None
        detected = profile.get("detected")
        info["profiles"][name] = {
            "model": str(profile["model"]) if profile.get("model") else None,
            "host": config.checked_host(url) if url else None,
            "public_host": bool(url) and config.is_public_host(url, cfg.blocklist),
            "detected": dict(detected) if isinstance(detected, Mapping) and detected else None,
            "detected_summary": summarize_detected(detected),
        }
    return info


def lock_holder(root: Path) -> str | None:
    """`.state/kb.lock`을 잡은 작업. 잡혀 있지 않으면 None. 잠금 파일을 만들거나 쓰지 않는다."""
    lock_path = core.state_dir(root) / "kb.lock"
    key = os.path.normcase(os.path.abspath(lock_path))
    if key in core._HELD:  # 이 프로세스가 잡고 있음(재진입 잠금)
        return core._read_owner(lock_path)
    if not lock_path.is_file():
        return None
    try:
        fd = os.open(lock_path, os.O_RDWR)
    except OSError:
        return None
    try:
        if core._try_lock(fd):
            core._unlock(fd)
            return None
        return core._read_owner(lock_path)
    finally:
        os.close(fd)


# ---------------------------------------------------------------- 모으기·출력


def collect(cfg: Config) -> dict[str, Any]:
    """상태 사전. 키: environment, local_config, vault, agent, today, last_sync, outbox,
    proposals, images, llm, lock, problems."""
    problems: list[str] = []
    day = core.now(cfg.tz).date()
    daily = notes.daily_path(cfg, day)
    return {
        "environment": cfg.environment,
        "local_config": cfg.local_exists,
        "vault": str(cfg.root),
        "agent": os.environ.get("KB_AGENT") == "1",
        "today": {"date": day.isoformat(), "path": f"daily/{daily.name}", "exists": daily.is_file()},
        "last_sync": last_sync(cfg, problems),
        "outbox": outbox_counts(cfg, problems),
        "proposals": pending_proposals(cfg, problems),
        "images": unprocessed_images(cfg),
        "llm": llm_info(cfg, problems),
        "lock": lock_holder(cfg.root),
        "problems": problems,
    }


def render(info: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    env = info["environment"]
    if info["local_config"]:
        lines.append(f"환경: {env}")
        lines.append("config.local.yaml: 있음")
    else:
        lines.append(f"환경: {env} (config.local.yaml이 없어 회사로 봅니다)")
        lines.append("config.local.yaml: 없음 → kb.py init")
    lines.append(f"볼트: {info['vault']}")
    today = info["today"]
    lines.append(f"오늘 daily: {today['path']} " + ("있음" if today["exists"] else "없음 → kb.py today"))
    lines.append(f"마지막 동기화: {info['last_sync'] or '없음'}")
    outbox = info["outbox"]
    lines.append(f"outbox: 대기 {outbox['pending']}개, 전송 중 {outbox.get('sending', 0)}개, 실패 {outbox['failed']}개")
    lines.append(f"미처리 제안: {info['proposals']}개")
    lines.append(f"미처리 이미지: {info['images']}개")
    llm = info["llm"]
    if llm["enabled"]:
        order = " → ".join(llm["fallback_order"]) or "없음"
        lines.append(f"LLM: 켜짐 (순서: {order})")
    else:
        lines.append("LLM: 꺼짐")
    if not llm["profiles"]:
        lines.append("LLM 프로필: 없음")
    for name, prof in llm["profiles"].items():
        where = " @ ".join(x for x in (prof["model"], prof["host"]) if x) or "모델·주소 없음"
        warn = " · 공개 호스트(회사 환경에서는 거부)" if prof["public_host"] and env == "company" else ""
        lines.append(f"LLM {name}: {where} · 탐지: {prof['detected_summary']}{warn}")
    lines.append(f"잠금: {info['lock'] or '없음'}")
    if info["agent"]:
        lines.append("에이전트 모드: KB_AGENT=1 (외부 쓰기·설정 변경 거부)")
    for problem in info["problems"]:
        lines.append(f"주의: {problem}")
    return lines


def main(argv: list[str]) -> int:
    parser = core.make_parser("kb.py status", "상태 요약 (네트워크를 쓰지 않음)")
    parser.add_argument("--json", action="store_true", help="JSON 객체로 출력")
    args = parser.parse_args(argv)
    cfg = config.load(args.root)
    info = collect(cfg)
    if args.json:
        print(core.redact(json.dumps(info, ensure_ascii=False, indent=1, default=str)))
    else:
        for line in render(info):
            print(core.redact(line))
    return 0


if __name__ == "__main__":
    core.run_main(main)
