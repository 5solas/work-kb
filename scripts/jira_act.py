"""Jira 쓰기 (`kb.py act <하위 명령>`). Phase 3, 지시서 §6.3, D26·D52·D53·D57.

하위 명령:
  comment KEY --body "…" | --from-line daily/YYYY-MM-DD.md:N   댓글 (줄 지정 시 기본 본문 = 그 줄)
  transition KEY NAME [--yes]        전환 이름(대소문자 무시) → id. 후보가 여럿이거나 없으면 목록, 종료 4
  transitions KEY [--json]           지금 가능한 전환 목록만 본다(아무것도 바꾸지 않음)
  assign KEY USERNAME                담당자 지정(DC 사용자 이름)
  worklog KEY 1h30m [--comment …] [--started ISO]
  create PROJECT --summary … --type … [--description …] [--yes]
  promote --from daily/YYYY-MM-DD.md:N PROJECT [--type Task] [--yes]
                                     그 줄로 일감을 만들고 성공하면 줄 끝에 ` [[새 키]]`를 붙인다(D53)
  flush [--retry-failed [ID …]] [--retry-uncertain [ID …]]
  list [--json] [--all]

흐름(모든 쓰기): 잠금 안에서 기록을 `sending`으로 표시(claim: claimed_at·pid·attempts+1) → 잠금 풀기 →
보내기 → 결과 기록과 표시 지우기(잠금 안, 한 번에) → 성공이면 `jira_sync.sync_key`로 캐시 갱신(잠금 밖,
sync_key가 스스로 잠근다). 네트워크 요청을 하는 동안에는 절대 잠금을 쥐지 않는다(D26).
`submit`은 기록을 처음부터 `sending`으로 넣는다(넣기와 표시가 한 걸음). flush는 잠금 안에서 다시 읽어
아직 보낼 수 있는 기록만 표시하고, 다른 실행이 표시한 기록은 건너뛴다. 그래서 두 flush가 겹치거나
`act comment`와 flush가 겹쳐도 한 번만 보낸다. 보내다 끊기면(Ctrl+C·강제 종료·잠금 시간 초과·FileBusy)
기록은 `sending`으로 남는다. 주인 프로세스가 죽었거나 표시가 `jira.write_timeout_sec`(기본 60)×2초보다
오래되면 불확실로 보고(`list`에 uncertain), `--retry-uncertain`일 때만 다시 보낸다.

결과 규칙(D52):
- 약속한 모양의 2xx → done, done_at.
- 연결 실패·5xx → pending, attempts+1, 종료 3. 5번째 실패면 failed. 전환의 500은 잘못된 id라서 바로 failed.
- 429(재시도 뒤에도) → pending, 시도로 세지 않음, 종료 3.
- 응답 시간 초과·응답 전 끊김·모양이 다른 2xx(HTML 화면, id·key 없음) → pending, last_error "uncertain"
  (보냈을 수 있음). 몇 번째 시도든 failed가 되지 않는다. 그냥 flush와 `--retry-failed`는 건너뛰고
  `--retry-uncertain`일 때만 다시 보낸다.
- 401/403 → `/myself`로 다시 확인: 200이면 권한 없음 → failed(종료 1), 401이면 토큰 문제 → pending, 종료 2.
- 3xx(리다이렉트) → failed, flush 중단(종료 1). 그 밖의 4xx → failed(종료 1).
flush는 pending을 id 순서로 보낸다. project_keys 밖 프로젝트의 기록은 보내지 않는다(종료 4).
`--retry-failed`·`--retry-uncertain`에 기록 id를 주면 그 기록만 다시 보낸다(그 상태가 아니면 종료 4).

본문(댓글·제목·설명 등)에 등록된 비밀값(토큰)이 들어 있으면 outbox에 넣지 않고 거부한다(종료 4).
자동 상태 전환은 없다. `create`·`transition`·`promote`는 `--yes`가 없으면 y/N을 묻는다(EOF는 거절, 종료 1).
`KB_AGENT=1`이면 `list`·`transitions` 말고는 모두 거부(종료 7). kb.py 경로와 별개로 여기서도 확인한다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, TextIO

import daily_prepare
import jira_sync
from lib import config, core, notes, outbox
from lib import jira_client as jc
from lib.config import Config
from lib.jira_client import JiraClient

LOCK_OWNER = outbox.LOCK_OWNER
READ_ONLY = frozenset({"list", "transitions"})
KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]+-[1-9][0-9]*$")
PROJECT_RE = re.compile(r"^[A-Z][A-Z0-9_]+$")
SOURCE_RE = re.compile(r"^(daily/\d{4}-\d{2}-\d{2}\.md):([1-9][0-9]*)$")
DURATION_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?$")
LIST_PREFIX = re.compile(r"^\s*[-*+]\s+(?:\[[ xX>]\]\s+)?(?:\d{1,2}:\d{2}(?:\s+|$))?")
CARRIED_RE = re.compile(r"^- \[>\] (.*) \(\d{4}-\d{2}-\d{2}로 이월\)$")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}(?:\s|$)")
MARKER_RE = re.compile(r"^\s*<!--\s*kb:")
CARRY_SOURCE_RE = re.compile(r"\(← (\d{4}-\d{2}-\d{2})\)\s*$")
EMBED_RE = re.compile(r"!\[\[[^\]]*\]\]")
LINK_RE = re.compile(r"\[\[([^\]\|#]+)(?:#[^\]\|]*)?(?:\|([^\]]*))?\]\]")
SUMMARY_MAX = 255
YES_WORDS = frozenset({"y", "yes", "예", "네", "ㅇ"})
STALE_BASE_SEC = 60.0  # jira.write_timeout_sec 기본값. 보내는 중 표시는 이것의 두 배보다 오래되면 불확실
SECRET_REFUSED = "본문에 토큰이 들어 있어 보내지 않습니다. 토큰을 지운 뒤 다시 하세요"

_input: Callable[[str], str] = input  # 테스트가 바꿔 끼운다(확인 질문)


# ---------------------------------------------------------------- 도움 함수


def _now_iso(cfg: Config) -> str:
    return core.now(cfg.tz).isoformat(timespec="seconds")


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def confirm(question: str) -> bool:
    """y/N 질문. EOF·빈 답·그 밖의 답은 모두 거절."""
    try:
        answer = _input(f"{core.redact(question)} [y/N] ")
    except EOFError:
        print()
        return False
    return (answer or "").strip().lower() in YES_WORDS


def project_of(record: Mapping[str, Any]) -> str:
    key = str(record.get("key") or "")
    if record.get("action") in ("create", "promote"):
        return key
    return key.split("-", 1)[0]


def _check_project(cfg: Config, project: str) -> None:
    if project not in cfg.project_keys:
        allowed = ", ".join(cfg.project_keys) or "(없음)"
        raise core.ConfigError(f"프로젝트 {project}는 project_keys에 없습니다 (허용: {allowed})")


def issue_key(cfg: Config, raw: str) -> str:
    key = (raw or "").strip().upper()
    if key.startswith("[[") and key.endswith("]]"):
        key = key[2:-2].strip()
    if not KEY_RE.match(key):
        raise core.ConfigError(f"이슈 키 형식이 아닙니다: {raw!r}. 예: KB-12")
    _check_project(cfg, key.split("-", 1)[0])
    return key


def project_key(cfg: Config, raw: str) -> str:
    project = (raw or "").strip().upper()
    if not PROJECT_RE.match(project):
        raise core.ConfigError(f"프로젝트 키 형식이 아닙니다: {raw!r}. 예: KB")
    _check_project(cfg, project)
    return project


def parse_duration(text: str) -> int:
    """`1h30m`·`2h`·`45m`·`1h 30m` → 초. 그 밖(일·주 포함)은 ConfigError(인스턴스마다 하루 길이가 다르다)."""
    compact = re.sub(r"\s+", "", (text or "").lower())
    match = DURATION_RE.match(compact)
    if not compact or match is None:
        raise core.ConfigError(f"작업 시간 형식이 아닙니다: {text!r}. 예: 1h30m, 2h, 45m")
    seconds = int(match.group(1) or 0) * 3600 + int(match.group(2) or 0) * 60
    if seconds < 60:
        raise core.ConfigError(f"작업 시간은 1분 이상이어야 합니다: {text!r}")
    return seconds


def parse_started(cfg: Config, text: str | None) -> dt.datetime:
    if not text:
        return core.now(cfg.tz)
    try:
        value = dt.datetime.fromisoformat(text.strip())
    except ValueError:
        raise core.ConfigError(f"--started: ISO 8601 시각이 아닙니다: {text!r}. 예: 2026-11-05T14:00") from None
    return value if value.tzinfo is not None else value.replace(tzinfo=cfg.tz)


def line_content(text: str) -> str:
    """daily 한 줄 → Jira로 보낼 글: 목록 표시·체크박스·시각을 떼고, 위키링크는 글자로, 이미지 임베드는 뺀다."""
    body = LIST_PREFIX.sub("", text, count=1)
    body = EMBED_RE.sub("", body)
    body = LINK_RE.sub(lambda m: (m.group(2) or m.group(1)).strip(), body)
    return re.sub(r"\s+", " ", body).strip()


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def refuse_secret(payload: Mapping[str, Any]) -> None:
    """본문에 등록된 비밀값(JiraClient.from_config가 등록한 PAT 등)이 있으면 ConfigError(종료 4)."""
    text = json.dumps(payload, ensure_ascii=False)
    if core.redact(text) != text or any(core.redact(item) != item for item in _strings(payload)):
        raise core.ConfigError(SECRET_REFUSED)


# ---------------------------------------------------------------- 보내는 중 표시(claim)


def stale_after(cfg: Config) -> float:
    """보내는 중 표시를 불확실로 볼 나이(초): `jira.write_timeout_sec`(기본 60)의 두 배."""
    raw = cfg.get("jira.write_timeout_sec")
    try:
        base = float(raw) if raw not in (None, "") and not isinstance(raw, bool) else STALE_BASE_SEC
    except (TypeError, ValueError):
        base = STALE_BASE_SEC
    return 2 * (base if base > 0 else STALE_BASE_SEC)


def pid_alive(pid: Any) -> bool:
    """그 pid의 프로세스가 살아 있는가. 모르면 살아 있다고 본다(권한 거부 등)."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # ACCESS_DENIED: 있지만 열 수 없음
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _claim_fields() -> dict[str, Any]:
    # 실제 시계(KB_FAKE_NOW와 무관): 표시의 나이를 재는 데 쓴다
    return {"claimed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "pid": os.getpid()}


def claim_is_stale(record: Mapping[str, Any], stale_sec: float, now: dt.datetime | None = None) -> bool:
    """보내는 중 표시의 주인이 죽었거나 표시가 stale_sec보다 오래됐는가."""
    if not pid_alive(record.get("pid")):
        return True
    try:
        claimed = dt.datetime.fromisoformat(str(record.get("claimed_at") or ""))
    except ValueError:
        return True
    if claimed.tzinfo is None:
        claimed = claimed.replace(tzinfo=dt.timezone.utc)
    now = now or dt.datetime.now(dt.timezone.utc)
    return (now - claimed).total_seconds() > stale_sec


def state_of(record: Mapping[str, Any], stale_sec: float) -> str:
    """보이는 상태: pending · sending(다른 실행이 보내는 중) · uncertain · failed · done."""
    status = str(record.get("status") or "")
    if status == outbox.SENDING:
        return outbox.UNCERTAIN if claim_is_stale(record, stale_sec) else outbox.SENDING
    if status == outbox.PENDING and record.get("last_error") == outbox.UNCERTAIN:
        return outbox.UNCERTAIN
    return status


def _claim(cfg: Config, record_id: str, eligible: Callable[[Mapping[str, Any]], bool]) -> dict | None:
    """잠금 안에서 다시 읽어, 아직 보낼 수 있으면 sending으로 표시하고(attempts+1) 그 기록을 돌려준다."""

    def change(record: dict) -> dict | None:
        if not eligible(record):
            return None
        attempts = 0 if record.get("status") == outbox.FAILED else int(record.get("attempts") or 0)
        return {"status": outbox.SENDING, "attempts": attempts + 1, **_claim_fields()}

    return outbox.modify(cfg.root, record_id, change)


def _finish(cfg: Config, record: Mapping[str, Any], changes: Mapping[str, Any]) -> dict:
    """결과를 적고 보내는 중 표시를 지운다(잠금 안, 한 번에)."""
    return outbox.update(cfg.root, record["id"], {**changes, "claimed_at": outbox.REMOVE, "pid": outbox.REMOVE})


# ---------------------------------------------------------------- 원본 줄


@dataclass(frozen=True)
class SourceLine:
    spec: str  # daily/2026-11-05.md:14
    rel: str
    lineno: int
    path: Path
    text: str


def read_source(cfg: Config, spec: str) -> SourceLine:
    """`daily/YYYY-MM-DD.md:N`의 줄(1부터, `\\n` 기준. Neovim 줄 번호와 같다)."""
    normalized = (spec or "").strip().replace("\\", "/")
    match = SOURCE_RE.match(normalized)
    if match is None:
        raise core.ConfigError(f"줄 지정 형식이 아닙니다: {spec!r}. 예: daily/2026-11-05.md:14")
    rel, lineno = match.group(1), int(match.group(2))
    path = cfg.path(*rel.split("/"))
    if not path.is_file():
        raise core.ConfigError(f"{rel} 파일이 없습니다")
    try:
        lines = notes.split_lines(notes.decode(path.read_bytes()))
    except UnicodeDecodeError:
        raise core.ConfigError(f"{rel}: UTF-8이 아니라 읽지 못했습니다") from None
    if lineno > len(lines):
        raise core.ConfigError(f"{rel}에는 {lineno}번째 줄이 없습니다 (전체 {len(lines)}줄)")
    return SourceLine(spec=f"{rel}:{lineno}", rel=rel, lineno=lineno, path=path, text=lines[lineno - 1])


def unpromotable(line: notes.NoteLine) -> str | None:
    """승격(줄 끝에 ` [[KEY]]`를 붙임)할 수 없는 줄이면 그 이유. 된다면 None."""
    if line.in_code:
        if MARKER_RE.match(line.text):
            return "관리 구역 표시 줄입니다"
        return "front matter나 코드 블록 안의 줄입니다"
    if line.region is not None:
        reason = f"관리 구역({line.region}) 안의 줄입니다. 도구가 다시 쓰는 곳이라 키를 붙이지 않습니다"
        carried = CARRY_SOURCE_RE.search(line.text) if line.region == "carryover" else None
        if carried:
            reason += f". 원래 줄(daily/{carried.group(1)}.md의 `## 기록`)을 승격하세요"
        return reason
    if HEADING_RE.match(line.text):
        return "제목 줄입니다"
    if not line.text.strip() or not line_content(line.text):
        return "빈 줄입니다"
    return None


def _note_lines(rel: str, data: bytes) -> list[notes.NoteLine]:
    return list(notes.iter_lines(rel, data))


def _line_hashes(text: str) -> set[str]:
    """줄의 해시와, 이월 표시(`- [>] … (날짜로 이월)`)가 붙었으면 원래 `- [ ] …` 꼴의 해시."""
    out = {core.line_hash(text)}
    carried = CARRIED_RE.match(text.strip())
    if carried:
        out.add(core.line_hash(f"- [ ] {carried.group(1)}"))
    return out


def link_promoted(cfg: Config, record: Mapping[str, Any], new_key: str) -> tuple[bool, str]:
    """승격한 daily 줄 끝에 ` [[NEW-1]]`를 붙인다(잠금 안, expect_sha256). (붙였는가, 설명).

    승격할 수 있는 줄(관리 구역·표시·front matter·코드 블록·제목·빈 줄이 아닌 줄)만 본다.
    파일이 승격할 때와 같으면(payload.source_sha256) 기록한 줄 번호의 줄에 붙인다. 바뀌었으면 기록한
    해시와 같은 줄이 파일에 정확히 하나일 때만 붙인다(위에 줄이 끼어들어 번호만 밀린 경우). 같은 줄이
    여럿이면 어느 것인지 모르므로 붙이지 않는다. 이월 표시(`[>]`)가 붙은 꼴도 같은 줄로 본다.
    붙였으면 뒤 날짜 carryover 구역의 이월 사본에도 붙인다(daily_prepare.link_carried_copies).
    """
    match = SOURCE_RE.match(str(record.get("source") or ""))
    payload = record.get("payload") or {}
    want = str(payload.get("line_hash") or "")
    if match is None or not want:
        return False, "기록에 원본 줄 정보가 없습니다"
    rel, lineno = match.group(1), int(match.group(2))
    path = cfg.path(*rel.split("/"))
    link = f"[[{new_key}]]"
    with core.kb_lock(cfg.root, LOCK_OWNER):
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return False, f"{rel} 파일이 없습니다"
        bom = notes.BOM if data.startswith(notes.BOM) else b""
        try:
            parts = notes.split_lines(notes.decode(data), keepends=True)
            note_lines = _note_lines(rel, data)
        except UnicodeDecodeError:
            return False, f"{rel}: UTF-8이 아닙니다"
        bodies = [part.rstrip("\r\n") for part in parts]
        same = [
            i for i, body in enumerate(bodies)
            if i < len(note_lines) and unpromotable(note_lines[i]) is None and want in _line_hashes(body)
        ]  # fmt: skip
        index: int | None = None
        unchanged = payload.get("source_sha256") == core.sha256_hex(data)
        if unchanged and (lineno - 1) in same:
            index = lineno - 1
        elif len(same) == 1:
            index = same[0]
        if index is None:
            if len(same) > 1:
                return False, f"{rel}:{lineno} 줄과 같은 줄이 {len(same)}개라 어느 줄인지 모릅니다"
            return False, f"{rel}:{lineno} 줄이 승격할 때와 달라졌습니다"
        body = bodies[index]
        if link in body:
            note = f"{rel}:{index + 1}에 이미 {link}가 있습니다"
        else:
            parts[index] = body.rstrip(" \t") + f" {link}" + parts[index][len(body):]
            new = bom + "".join(parts).encode("utf-8")
            try:
                core.atomic_write(path, new, expect_sha256=core.sha256_hex(data))
            except core.ConcurrentEdit:
                return False, f"{rel}을(를) 쓰는 사이에 사람이 고쳤습니다"
            except core.FileBusy as exc:
                return False, str(exc)
            note = f"{rel}:{index + 1}에 {link}를 붙였습니다"
        try:
            copies, problems = daily_prepare.link_carried_copies(cfg, rel, body, new_key)
        except (core.KbError, OSError) as exc:
            copies, problems = 0, [str(exc)]
    if copies:
        note += f" (이월 사본 {copies}곳에도 붙였습니다)"
    for problem in problems:
        _err(f"경고: 이월 사본에 {link}를 붙이지 못했습니다: {problem}")
    return True, note


# ---------------------------------------------------------------- 보내기와 결과


@dataclass
class Outcome:
    code: int
    record: dict
    stop: bool = False  # flush가 여기서 멈춰야 하는가(인증·연결 문제)


def describe(record: Mapping[str, Any]) -> str:
    return core.redact(_describe(record))


def _describe(record: Mapping[str, Any]) -> str:
    action = record.get("action")
    key = record.get("key")
    payload = record.get("payload") or {}
    if action == "comment":
        body = " ".join(str(payload.get("body") or "").split())
        return f"댓글 {key}: {body[:40]}{'…' if len(body) > 40 else ''}"
    if action == "transition":
        return f"전환 {key} → {payload.get('to') or payload.get('name')} (id {payload.get('transition_id')})"
    if action == "assign":
        return f"담당 {key} → {payload.get('username')}"
    if action == "worklog":
        return f"워크로그 {key} {payload.get('time_spent') or str(payload.get('seconds')) + '초'}"
    if action == "create":
        return f"새 일감 {key}: {payload.get('summary')}"
    if action == "promote":
        return f"승격 {key}: {payload.get('summary')} ({record.get('source')})"
    return f"{action} {key}"


def _send(client: JiraClient, record: Mapping[str, Any]) -> dict | None:
    """기록 하나를 Jira로 보낸다. 결과(기록에 남길 작은 dict) 또는 None."""
    action = record.get("action")
    key = str(record.get("key") or "")
    payload = record.get("payload") or {}
    if action == "comment":
        data = client.add_comment(key, str(payload.get("body") or ""))
        return {"comment_id": data["id"]}
    if action == "transition":
        client.do_transition(key, str(payload.get("transition_id") or ""))
        return None
    if action == "assign":
        client.assign(key, str(payload.get("username") or ""))
        return None
    if action == "worklog":
        started = jc.parse_datetime(payload.get("started"))
        if started is None:
            raise core.ConfigError(f"워크로그 시작 시각을 읽지 못했습니다: {payload.get('started')!r}")
        data = client.add_worklog(key, int(payload.get("seconds") or 0), str(payload.get("comment") or ""), started)
        return {"worklog_id": data["id"]}
    if action in ("create", "promote"):
        extra: dict[str, Any] = {}
        if payload.get("description"):
            extra["description"] = str(payload["description"])
        data = client.create_issue(key, str(payload.get("summary") or ""), str(payload.get("issuetype") or ""), **extra)
        return {"key": data["key"]}
    raise core.ConfigError(f"알 수 없는 outbox 동작입니다: {action!r}")


def _log(cfg: Config, record: Mapping[str, Any], note: str) -> None:
    core.get_logger(cfg.root).info(
        "act %s %s %s → %s (시도 %s) %s",
        record.get("id"), record.get("action"), record.get("key"), record.get("status"), record.get("attempts"), note,
    )


def _fail(cfg: Config, record: Mapping[str, Any], attempts: int, message: str, hint: str = "", *, stop: bool = False) -> Outcome:
    rec = _finish(cfg, record, {"status": outbox.FAILED, "attempts": attempts, "last_error": core.redact(message)})
    _err(f"실패: {describe(rec)} — {core.redact(message)}" + (f"\n  {hint}" if hint else ""))
    _log(cfg, rec, message)
    return Outcome(1, rec, stop=stop)


def _keep(cfg: Config, record: Mapping[str, Any], attempts: int, error: str, detail: str | None, *, stop: bool) -> Outcome:
    """pending으로 남긴다. 시도가 MAX_ATTEMPTS에 닿으면 failed. 단 불확실은 몇 번째든 불확실로 남긴다
    (failed가 되면 `--retry-failed`가 이미 반영됐을 수 있는 쓰기를 다시 보낸다)."""
    if attempts >= outbox.MAX_ATTEMPTS and error != outbox.UNCERTAIN:
        message = detail or error
        return _fail(cfg, record, attempts, f"{outbox.MAX_ATTEMPTS}번 시도했지만 보내지 못했습니다: {message}",
                     "Jira 상태를 확인한 뒤 `kb.py act flush --retry-failed`로 다시 보낼 수 있습니다")  # fmt: skip
    changes: dict[str, Any] = {"status": outbox.PENDING, "attempts": attempts, "last_error": core.redact(error)}
    if detail is not None or "error_detail" in record:
        changes["error_detail"] = core.redact(detail) if detail else None
    rec = _finish(cfg, record, changes)
    if error == outbox.UNCERTAIN:
        _err(
            f"불확실: {describe(rec)} — {core.redact(detail or '')}\n"
            "  Jira에 반영됐는지 먼저 확인하세요. 반영되지 않았으면 "
            f"`kb.py act flush --retry-uncertain {rec['id']}`"
        )
    else:
        _err(f"대기: {describe(rec)} — {core.redact(error)}\n  outbox에 남겼습니다. 나중에 `kb.py act flush`로 보냅니다")
    _log(cfg, rec, error)
    return Outcome(3, rec, stop=stop)


def _denied(cfg: Config, client: JiraClient, record: Mapping[str, Any], attempts: int, exc: core.KbError) -> Outcome:
    """401/403: `/myself`로 토큰을 다시 확인해 권한 문제(failed)와 토큰 문제(pending, 종료 2)를 가른다(D52)."""
    try:
        client.myself()
    except core.AuthError as again:
        changes = {"status": outbox.PENDING, "attempts": attempts - 1, "last_error": core.redact(str(again))}
        rec = _finish(cfg, record, changes)  # 토큰 문제는 시도로 세지 않는다
        _err(
            f"인증 실패: {describe(rec)} — {core.redact(str(again))}\n"
            "  outbox에 남겼습니다. 토큰을 고친 뒤(`kb.py init --section jira`) `kb.py act flush`"
        )
        _log(cfg, rec, "auth")
        return Outcome(2, rec, stop=True)
    except core.KbError as again:
        return _keep(cfg, record, attempts, str(again), None, stop=True)
    return _fail(cfg, record, attempts, f"권한이 없습니다: {exc}", "Jira에서 이 일감에 대한 권한을 확인하세요")


def deliver(cfg: Config, client: JiraClient, record: Mapping[str, Any], out: TextIO) -> Outcome:
    """보내는 중으로 표시한(claim) 기록 하나를 보내고 결과를 outbox에 적는다(표시도 지운다).

    잠금은 결과를 적을 때만 잡는다. 결과를 적지 못하면(잠금 시간 초과·FileBusy) 기록은 sending으로
    남아 다음 flush가 다시 보내지 않는다(주인이 끝나면 불확실로 보인다).
    """
    attempts = int(record.get("attempts") or 1)  # claim이 이번 시도를 이미 셌다
    try:
        refuse_secret(record.get("payload") or {})  # 예전에 큐에 들어간 기록도 토큰을 보내지 않는다
        result = _send(client, record)
    except core.AuthError as exc:  # WriteDenied 포함
        return _denied(cfg, client, record, attempts, exc)
    except jc.RateLimited as exc:
        return _keep(cfg, record, attempts - 1, str(exc), None, stop=True)
    except jc.UnexpectedResponse as exc:  # 2xx지만 모양이 다르다: 반영됐는지 모른다
        return _keep(cfg, record, attempts, outbox.UNCERTAIN, str(exc), stop=True)
    except jc.WriteServerError as exc:
        if record.get("action") == "transition" and exc.status == 500:
            return _fail(cfg, record, attempts, str(exc),
                         f"전환 id가 지금 상태에 맞지 않을 수 있습니다. `kb.py act transitions {record.get('key')}`로 확인하세요")  # fmt: skip
        return _keep(cfg, record, attempts, str(exc), None, stop=False)
    except core.NetworkError as exc:
        if exc.after_send:
            return _keep(cfg, record, attempts, outbox.UNCERTAIN, str(exc), stop=True)
        return _keep(cfg, record, attempts, str(exc), None, stop=True)
    except jc.WriteRejected as exc:  # 3xx·4xx: 다시 보내도 같다. 리다이렉트면 나머지도 같으므로 멈춘다
        return _fail(cfg, record, attempts, str(exc), stop=300 <= exc.status < 400)
    except core.KbError as exc:  # 형식 오류 등: 다시 보내도 같다
        return _fail(cfg, record, attempts, str(exc))
    changes: dict[str, Any] = {"status": outbox.DONE, "attempts": attempts, "last_error": None, "done_at": _now_iso(cfg)}
    if "error_detail" in record:
        changes["error_detail"] = None
    if result:
        changes["result"] = result
    try:
        rec = _finish(cfg, record, changes)
    except core.KbError:  # 잠금·파일 문제: Jira에는 이미 반영됐다. 다시 보내면 중복이 된다
        _err(
            f"경고: Jira에는 보냈지만 outbox에 완료를 적지 못했습니다 ({record['id']} {describe(record)}).\n"
            "  기록은 '보내는 중'으로 남아 그냥 flush는 다시 보내지 않습니다(목록에는 uncertain으로 보입니다).\n"
            f"  다시 보내지 마세요. 결과: {json.dumps(result or {}, ensure_ascii=False)}"
        )
        raise
    print(f"보냄: {describe(rec)}", file=out)
    _log(cfg, rec, "")
    _after_done(cfg, client, rec, out)
    return Outcome(0, rec)


def _after_done(cfg: Config, client: JiraClient, record: dict, out: TextIO) -> None:
    """성공 뒤: 승격이면 daily 줄에 키를 붙이고, 캐시(jira/KEY.md)를 갱신한다(잠금 밖에서 부른다)."""
    target = str(record.get("key") or "")
    if record.get("action") in ("create", "promote"):
        target = str((record.get("result") or {}).get("key") or "")
        print(f"새 일감: {target}", file=out)
        if record.get("action") == "promote":
            linked, note = link_promoted(cfg, record, target)
            outbox.update(cfg.root, record["id"], {"result": {**(record.get("result") or {}), "linked": linked}})
            if linked:
                print(note, file=out)
            else:
                _err(f"경고: 일감 {target}은(는) 만들었지만 daily 줄에 [[{target}]]를 붙이지 않았습니다: {note}\n"
                     f"  필요하면 그 줄 끝에 직접 [[{target}]]를 붙이세요")  # fmt: skip
    if not KEY_RE.match(target):
        return
    try:
        path = jira_sync.sync_key(cfg, client, target)
    except core.KbError as exc:
        _err(f"경고: Jira에는 보냈지만 캐시 갱신에 실패했습니다 ({exc}). 나중에 `kb.py sync --key {target}`")
        return
    print(f"jira/{path.name}: 동기화함", file=out)


def submit(
    cfg: Config,
    client: JiraClient,
    out: TextIO,
    *,
    action: str,
    key: str,
    payload: Mapping[str, Any],
    source: str | None = None,
) -> int:
    """outbox에 보내는 중(sending) 상태로 넣고(잠금 안, 넣기와 표시가 한 걸음) 잠금을 푼 뒤 바로 보낸다.

    본문에 비밀값이 있으면 넣지 않는다(종료 4). client를 만든 뒤에 불러야 PAT가 등록돼 있다.
    """
    refuse_secret(payload)
    record = outbox.make_record(action=action, key=key, payload=payload, ts=_now_iso(cfg), source=source)
    record.update({"status": outbox.SENDING, "attempts": 1, **_claim_fields()})
    outbox.append(cfg.root, record)
    print(f"outbox에 넣음: {record['id']} {describe(record)}", file=out)
    return deliver(cfg, client, record, out).code


# ---------------------------------------------------------------- 하위 명령


def cmd_comment(cfg: Config, args: argparse.Namespace, out: TextIO) -> int:
    key = issue_key(cfg, args.key)
    source: str | None = None
    body = args.body
    if args.from_line:
        line = read_source(cfg, args.from_line)
        source = line.spec
        if body is None:
            body = line_content(line.text)
    if body is None:
        raise core.ConfigError("댓글 내용이 필요합니다: --body \"…\" 또는 --from-line daily/YYYY-MM-DD.md:N")
    if not body.strip():
        raise core.ConfigError("댓글 내용이 비어 있습니다")
    with JiraClient.from_config(cfg) as client:
        return submit(cfg, client, out, action="comment", key=key, payload={"body": body}, source=source)


def match_transitions(options: list[dict], wanted: str) -> list[dict]:
    """이름(대소문자 무시) → 없으면 id → 없으면 도착 상태 이름 순으로 찾는다."""
    text = (wanted or "").strip().casefold()
    by_name = [t for t in options if str(t.get("name") or "").strip().casefold() == text]
    if by_name:
        return by_name
    by_id = [t for t in options if str(t.get("id") or "") == wanted.strip()]
    if by_id:
        return by_id
    return [t for t in options if str(((t.get("to") or {}).get("name")) or "").strip().casefold() == text]


def _transition_rows(options: list[dict]) -> list[dict]:
    rows = []
    for item in options:
        to = item.get("to") or {}
        category = (to.get("statusCategory") or {}).get("key") if isinstance(to, Mapping) else None
        rows.append({"id": str(item.get("id") or ""), "name": str(item.get("name") or ""),
                     "to": str(to.get("name") or "") if isinstance(to, Mapping) else "", "to_category": category or ""})  # fmt: skip
    return rows


def _print_candidates(key: str, options: list[dict], out: TextIO) -> None:
    for row in _transition_rows(options):
        print(f"  {row['id']}\t{row['name']} → {row['to']}", file=out)
    print(f"이름이 겹치면 id로 고르세요: kb.py act transition {key} <id>", file=out)


def cmd_transition(cfg: Config, args: argparse.Namespace, out: TextIO) -> int:
    key = issue_key(cfg, args.key)
    with JiraClient.from_config(cfg) as client:
        options = client.transitions(key)
        found = match_transitions(options, args.name)
        if len(found) != 1:
            if found:
                _err(f"'{args.name}'에 맞는 전환이 {len(found)}개입니다 ({key}). 하나를 고르세요")
                _print_candidates(key, found, out)
            else:
                _err(f"'{args.name}'에 맞는 전환이 없습니다 ({key}). 가능한 전환:")
                _print_candidates(key, options, out)
            return 4
        chosen = found[0]
        row = _transition_rows([chosen])[0]
        if not args.yes and not confirm(f"{key}를 '{row['name']}'(→ {row['to']})로 전환할까요?"):
            print("취소했습니다", file=out)
            return 1
        payload = {"transition_id": row["id"], "name": row["name"], "to": row["to"]}
        return submit(cfg, client, out, action="transition", key=key, payload=payload)


def cmd_transitions(cfg: Config, args: argparse.Namespace, out: TextIO) -> int:
    key = issue_key(cfg, args.key)
    with JiraClient.from_config(cfg) as client:
        rows = _transition_rows(client.transitions(key))
    if args.json:
        print(json.dumps(rows, ensure_ascii=False), file=out)
        return 0
    if not rows:
        print(f"{key}: 가능한 전환이 없습니다", file=out)
    for row in rows:
        print(f"{row['id']}\t{row['name']} → {row['to']}", file=out)
    return 0


def cmd_assign(cfg: Config, args: argparse.Namespace, out: TextIO) -> int:
    key = issue_key(cfg, args.key)
    username = (args.username or "").strip()
    if not username:
        raise core.ConfigError("담당자 사용자 이름이 비어 있습니다")
    with JiraClient.from_config(cfg) as client:
        return submit(cfg, client, out, action="assign", key=key, payload={"username": username})


def cmd_worklog(cfg: Config, args: argparse.Namespace, out: TextIO) -> int:
    key = issue_key(cfg, args.key)
    seconds = parse_duration(args.duration)
    started = parse_started(cfg, args.started)
    payload = {
        "seconds": seconds,
        "time_spent": re.sub(r"\s+", "", args.duration.lower()),
        "comment": args.comment or "",
        "started": jc.format_started(started),
    }
    with JiraClient.from_config(cfg) as client:
        return submit(cfg, client, out, action="worklog", key=key, payload=payload)


def _summary(text: str) -> str:
    summary = re.sub(r"\s+", " ", text or "").strip()
    if not summary:
        raise core.ConfigError("일감 제목이 비어 있습니다")
    if len(summary) > SUMMARY_MAX:
        summary = summary[: SUMMARY_MAX - 1] + "…"
    return summary


def cmd_create(cfg: Config, args: argparse.Namespace, out: TextIO) -> int:
    project = project_key(cfg, args.project)
    summary = _summary(args.summary)
    issuetype = (args.type or "").strip()
    if not issuetype:
        raise core.ConfigError("--type: 일감 유형이 비어 있습니다. 예: Task")
    payload: dict[str, Any] = {"summary": summary, "issuetype": issuetype}
    if args.description:
        payload["description"] = args.description
    with JiraClient.from_config(cfg) as client:
        refuse_secret(payload)  # 확인 질문에 본문이 보이기 전에
        if not args.yes and not confirm(f"{project}에 새 일감 '{summary}'({issuetype})을(를) 만들까요?"):
            print("취소했습니다", file=out)
            return 1
        return submit(cfg, client, out, action="create", key=project, payload=payload)


def cmd_promote(cfg: Config, args: argparse.Namespace, out: TextIO) -> int:
    project = project_key(cfg, args.project)
    line = read_source(cfg, args.source)
    data = line.path.read_bytes()
    note_lines = _note_lines(line.rel, data)
    reason = unpromotable(note_lines[line.lineno - 1]) if line.lineno <= len(note_lines) else "줄을 찾지 못했습니다"
    if reason:
        raise core.ConfigError(f"{line.spec}: {reason}. 승격하지 않습니다")
    summary = _summary(line_content(line.text))
    if re.search(rf"\[\[{re.escape(project)}-[1-9][0-9]*\]\]\s*$", line.text):
        raise core.ConfigError(f"{line.spec}: 이미 일감 키가 붙은 줄입니다")
    line_hash = core.line_hash(line.text)
    for record in outbox.records(cfg.root):
        if (
            record.get("action") == "promote"
            and record.get("status") in (outbox.PENDING, outbox.SENDING)
            and record.get("source") == line.spec
            and (record.get("payload") or {}).get("line_hash") == line_hash
        ):
            raise core.ConfigError(f"{line.spec}: 같은 줄의 승격이 이미 대기 중입니다 ({record['id']}). `kb.py act flush`로 보내세요")
    issuetype = (args.type or "").strip() or "Task"
    payload = {"summary": summary, "issuetype": issuetype, "line_hash": line_hash, "source_sha256": core.sha256_hex(data)}
    with JiraClient.from_config(cfg) as client:
        refuse_secret({"summary": summary})  # 확인 질문에 제목이 보이기 전에
        if not args.yes and not confirm(f"{line.spec}을(를) {project} 일감 '{summary}'({issuetype})으로 만들까요?"):
            print("취소했습니다", file=out)
            return 1
        return submit(cfg, client, out, action="promote", key=project, payload=payload, source=line.spec)


def _selection(raw: list[str] | None, items: Mapping[str, dict], want: str, stale: float, flag: str) -> set[str] | None:
    """--retry-failed·--retry-uncertain 값 → None(끔) · 빈 집합(그 상태 전부) · 고른 id들. 틀린 id는 종료 4."""
    if raw is None:
        return None
    chosen: set[str] = set()
    for record_id in raw:
        record = items.get(record_id)
        if record is None:
            raise core.ConfigError(f"{flag}: outbox에 {record_id} 기록이 없습니다. `kb.py act list`로 id를 확인하세요")
        if state_of(record, stale) != want:
            raise core.ConfigError(f"{flag}: {record_id}는 {want} 상태가 아닙니다 (지금: {state_of(record, stale)})")
        chosen.add(record_id)
    return chosen


def _picked(selection: set[str] | None, record_id: str) -> bool:
    return selection is not None and (not selection or record_id in selection)


def cmd_flush(cfg: Config, args: argparse.Namespace, out: TextIO) -> int:
    stale = stale_after(cfg)
    items = sorted(outbox.records(cfg.root), key=lambda r: str(r.get("id")))
    by_id = {str(r.get("id")): r for r in items}
    retry_failed = _selection(args.retry_failed, by_id, outbox.FAILED, stale, "--retry-failed")
    retry_uncertain = _selection(args.retry_uncertain, by_id, outbox.UNCERTAIN, stale, "--retry-uncertain")

    def eligible(record: Mapping[str, Any]) -> bool:
        state = state_of(record, stale)
        if state == outbox.PENDING:
            return True
        if state == outbox.UNCERTAIN:
            return _picked(retry_uncertain, str(record.get("id")))
        if state == outbox.FAILED:
            return _picked(retry_failed, str(record.get("id")))
        return False

    targets: list[dict] = []
    uncertain = 0
    busy = 0
    for record in items:
        state = state_of(record, stale)
        if eligible(record):
            targets.append(record)
        elif state == outbox.UNCERTAIN:
            uncertain += 1
        elif state == outbox.SENDING:
            busy += 1
    note = f" · 불확실 {uncertain}건은 건너뜀(Jira에서 확인 뒤 --retry-uncertain ID)" if uncertain else ""
    note += f" · 다른 실행이 보내는 중 {busy}건은 건너뜀" if busy else ""
    if not targets:
        print(f"flush: 보낼 항목이 없습니다{note}", file=out)
        return 0
    refused = [r for r in targets if project_of(r) not in cfg.project_keys]
    for record in refused:
        _err(f"거부: {describe(record)} — 프로젝트 {project_of(record)}는 project_keys에 없어 보내지 않습니다")
    sendable = [r for r in targets if r not in refused]
    codes: list[int] = []
    sent = 0
    left = 0
    skipped = 0
    if sendable:
        with JiraClient.from_config(cfg) as client:
            for index, record in enumerate(sendable):
                claimed = _claim(cfg, record["id"], eligible)
                if claimed is None:
                    skipped += 1  # 다른 실행이 먼저 표시했거나 처리했다
                    continue
                outcome = deliver(cfg, client, claimed, out)
                codes.append(outcome.code)
                sent += outcome.code == 0
                if outcome.stop:
                    left = len(sendable) - index - 1
                    if left:
                        _err(f"나머지 {left}건은 보내지 않았습니다. 문제를 고친 뒤 다시 flush하세요")
                    break
    taken = f" · 다른 실행이 먼저 맡음 {skipped}" if skipped else ""
    print(f"flush: 보냄 {sent} · 실패·대기 {len(codes) - sent} · 남김 {left} · 거부 {len(refused)}{taken}{note}", file=out)
    for code in (2, 3):
        if code in codes:
            return code
    if refused:
        return 4
    return 1 if any(codes) else 0


def cmd_list(cfg: Config, args: argparse.Namespace, out: TextIO) -> int:
    stale = stale_after(cfg)
    items = outbox.records(cfg.root)
    shown = items if args.all else [r for r in items if r.get("status") != outbox.DONE]
    if args.json:
        print(json.dumps([{**r, "state": state_of(r, stale)} for r in shown], ensure_ascii=False), file=out)
        return 0
    count = {name: 0 for name in (outbox.PENDING, outbox.SENDING, outbox.UNCERTAIN, outbox.FAILED, outbox.DONE)}
    for record in items:
        state = state_of(record, stale)
        if state in count:
            count[state] += 1
    for record in shown:
        state = state_of(record, stale)
        error = record.get("last_error")
        if state == outbox.UNCERTAIN:
            detail = record.get("error_detail") if record.get("status") == outbox.PENDING else None
            error = detail or f"보내다 끊김(pid {record.get('pid')}, {record.get('claimed_at')})"
        elif state == outbox.SENDING:
            error = f"pid {record.get('pid')}이(가) 보내는 중"
        tail = f" · {core.redact(str(error))}" if error else ""
        print(f"{record.get('id')} {state} 시도 {record.get('attempts')} {describe(record)}{tail}", file=out)
    hidden = "" if args.all or not count[outbox.DONE] else " (완료 항목은 --all로 봅니다)"
    extra = "".join(
        f" · {label} {count[name]}" for name, label in ((outbox.UNCERTAIN, "불확실"), (outbox.SENDING, "보내는 중")) if count[name]
    )
    print(f"outbox: 대기 {count[outbox.PENDING]} · 실패 {count[outbox.FAILED]} · 완료 {count[outbox.DONE]}{extra}{hidden}", file=out)
    broken = outbox.broken_lines(cfg.root)
    if broken:
        _err(f"경고: jira/outbox.jsonl에 읽지 못한 줄 {broken}개")
    return 0


HANDLERS: dict[str, Callable[[Config, argparse.Namespace, TextIO], int]] = {
    "comment": cmd_comment,
    "transition": cmd_transition,
    "transitions": cmd_transitions,
    "assign": cmd_assign,
    "worklog": cmd_worklog,
    "create": cmd_create,
    "promote": cmd_promote,
    "flush": cmd_flush,
    "list": cmd_list,
}


# ---------------------------------------------------------------- 진입점


def build_parser() -> argparse.ArgumentParser:
    parser = core.make_parser("kb.py act", "Jira 쓰기(outbox 큐 경유). 자동 상태 전환은 하지 않는다")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=argparse.SUPPRESS, help="볼트 루트")
    subs = parser.add_subparsers(dest="sub", metavar="<하위 명령>")

    def sub(name: str, help_text: str) -> argparse.ArgumentParser:
        return subs.add_parser(name, parents=[common], help=help_text, description=help_text, allow_abbrev=False)

    p = sub("comment", "댓글 달기")
    p.add_argument("key", metavar="KEY")
    p.add_argument("--body", default=None, help="댓글 내용")
    p.add_argument("--from-line", dest="from_line", default=None, metavar="daily/YYYY-MM-DD.md:N", help="이 줄을 기본 내용·출처로")

    p = sub("transition", "상태 전환(이름 → id). --yes가 없으면 확인")
    p.add_argument("key", metavar="KEY")
    p.add_argument("name", metavar="NAME", help="전환 이름(대소문자 무시) 또는 id")
    p.add_argument("--yes", action="store_true", help="확인 없이 보낸다")

    p = sub("transitions", "지금 가능한 전환 목록(아무것도 바꾸지 않음)")
    p.add_argument("key", metavar="KEY")
    p.add_argument("--json", action="store_true", help="JSON 배열 [{id, name, to, to_category}]")

    p = sub("assign", "담당자 지정")
    p.add_argument("key", metavar="KEY")
    p.add_argument("username", metavar="USERNAME", help="Jira 사용자 이름(DC name)")

    p = sub("worklog", "작업 시간 기록")
    p.add_argument("key", metavar="KEY")
    p.add_argument("duration", metavar="DURATION", help="예: 1h30m, 2h, 45m")
    p.add_argument("--comment", default="", help="워크로그 설명")
    p.add_argument("--started", default=None, help="시작 시각 ISO 8601 (기본: 지금)")

    p = sub("create", "새 일감 만들기. --yes가 없으면 확인")
    p.add_argument("project", metavar="PROJECT")
    p.add_argument("--summary", required=True, help="제목")
    p.add_argument("--type", required=True, help="일감 유형 이름. 예: Task")
    p.add_argument("--description", default=None, help="설명")
    p.add_argument("--yes", action="store_true", help="확인 없이 보낸다")

    p = sub("promote", "daily 한 줄을 일감으로 만들고 줄 끝에 [[새 키]]를 붙인다. --yes가 없으면 확인")
    p.add_argument("--from", dest="source", required=True, metavar="daily/YYYY-MM-DD.md:N", help="승격할 줄")
    p.add_argument("project", metavar="PROJECT")
    p.add_argument("--type", default="Task", help="일감 유형 이름 (기본 Task)")
    p.add_argument("--yes", action="store_true", help="확인 없이 보낸다")

    p = sub("flush", "outbox의 대기 항목을 id 순서로 보낸다")
    p.add_argument("--retry-failed", nargs="*", default=None, metavar="ID",
                   help="실패 항목도 다시 보낸다(시도 횟수 초기화). id를 주면 그 기록만")  # fmt: skip
    p.add_argument("--retry-uncertain", nargs="*", default=None, metavar="ID",
                   help="결과를 모르는(uncertain) 항목도 다시 보낸다. id를 주면 그 기록만")  # fmt: skip

    p = sub("list", "outbox 항목 보기")
    p.add_argument("--json", action="store_true", help="JSON 배열(각 항목에 보이는 상태 state를 더함)")
    p.add_argument("--all", action="store_true", help="완료 항목도 보인다")
    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.sub:
        raise core.ConfigError(f"하위 명령이 필요합니다: {', '.join(HANDLERS)}. 예: kb.py act list")
    if args.sub not in READ_ONLY:
        core.refuse_if_agent(f"act {args.sub}")  # kb.py 경로와 별개로 직접 실행도 막는다(D57)
    cfg = config.load(args.root)
    return HANDLERS[args.sub](cfg, args, sys.stdout)


if __name__ == "__main__":
    core.run_main(main)
