"""오늘 daily 준비: 뼈대, `today` 구역(Jira 뷰), `carryover` 구역(개인 할 일 이월).

사용법: python scripts/kb.py today [--date YYYY-MM-DD] [--no-carryover] [--print-path]
        python scripts/kb.py today --check-done [--date YYYY-MM-DD] [--json]

규칙(지시서 §5.1·§6.4, D17·D18·D19·D24·D26·D28):
- 파일이 없으면 notes.daily_skeleton으로 만든다. 있는데 제목이 빠졌으면 기존 글 아래에 덧붙인다.
  제목은 있는데 구역 표시가 없으면 그 제목 바로 아래에 빈 구역을 넣는다. `## 기록` 내용은 건드리지 않는다.
- `today`(merge): `jira/_views/today.md`·`blocked.md`의 일감. 다시 돌려도 키별 체크 상태를 지키고,
  뷰에서 빠진 키는 `[x]`일 때만 남긴다. 뷰 파일이 둘 다 없으면 `- (Jira 동기화 전)` 한 줄.
  도구가 그린 줄(지난번 본문은 `.state/today.json`에 기록)만 다시 그리고, 사람이 쓰거나 고친 줄은
  바이트 그대로 순서대로 남긴다(merge_today_record).
- `carryover`(merge): 이전 daily들의 `## 기록`과 `carryover` 구역에서 Jira 키 없는 `- [ ]` 줄을
  `- [ ] 내용 (← YYYY-MM-DD)`로 옮기고, 원래 줄은 `- [>] 내용 (YYYY-MM-DD로 이월)`로 표시한다.
  `[>]` 줄은 다시 옮기지 않으므로 원장 없이 멱등이다. 이미 구역에 있는 항목(체크 여부 무관)은 더하지 않는다.
- 승격한 할 일은 옮기지 않는다(D53): 원본 줄(이월 사본이면 `(← 날짜)`의 daily에 있는 원래 줄)이 ` [[KEY]]`로
  끝나거나, 그 줄의 해시가 `jira/outbox.jsonl`의 pending·sending·done 승격 기록과 같으면 건너뛴다.
  outbox는 읽기만 한다(없거나 못 읽으면 없는 것으로 본다). 승격이 성공하면 jira_act가
  `link_carried_copies`로 뒤 날짜 carryover 구역에 이미 있는 사본에도 ` [[KEY]]`를 붙인다.
- 쓰기는 잠금 안에서, daily는 expect_sha256로. 사람이 도중에 고친 파일은 건너뛰고 보고한다.

`--check-done [--json]`(지시서 §5.1·§6.4, Phase 3): 그날 daily의 `## 오늘`에서 `- [x] [[KEY]]` 줄마다
jira/KEY.md를 보고, 아직 완료(status_category done)가 아니면 전환 **제안**만 출력한다.
Jira를 부르지 않고 아무 파일도 쓰지 않는다(잠금도 잡지 않는다). 자동 전환은 없다(§10).
전환 이름은 `jira.done_transition_names`(기본 [Done, 완료])의 첫 이름이다. 캐시에 전환 목록이 없으므로
후보 확인 명령(`act transitions KEY`)을 함께 안내한다.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from lib import config, core, frontmatter, managed, notes, outbox
from lib.config import Config

NOTE_NO_SYNC = "- (Jira 동기화 전)"
TODAY_STATE = "today"  # .state/today.json: daily별로 지난번에 그린 today 본문(merge 판정용)
TODAY_STATE_KEEP = 31
_SECTIONS = (("오늘", "today"), ("이월", "carryover"), ("기록", None))
_JIRA_KEY = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")
_TODAY_LINE = re.compile(r"^- \[([ xX])\] \[\[([A-Z][A-Z0-9_]+-\d+)(?:[|#][^\]]*)?\]\]")
_OPEN_TASK = re.compile(r"^- \[ \] (.*)$")
_ANY_TASK = re.compile(r"^- \[[ xX>]\] (.*)$")
_TIME = re.compile(r"^\d{2}:\d{2}(?:\s+|$)")
_TASK_PROBE = b"- [ ]"
_TRAILING_BLANKS = " \t"
_CARRIED_MARK = re.compile(r"^- \[>\] (.*) \(\d{4}-\d{2}-\d{2}로 이월\)$")
_CARRY_COPY = re.compile(r"^(.*) \(← (\d{4}-\d{2}-\d{2})\)$")
_COPY_TASK = re.compile(r"^- \[[ xX]\] (.*)$")
_ENDS_WITH_KEY = re.compile(r"\s\[\[[A-Z][A-Z0-9_]+-\d+\]\]$")
_PROMOTE_LIVE = (outbox.PENDING, outbox.SENDING, outbox.DONE)


@dataclass(frozen=True)
class _Edit:
    """이전 daily의 한 줄을 `[>]` 표시로 바꾸는 편집."""

    path: Path
    offset: int  # 줄 시작 바이트
    old: bytes  # 줄 끝 문자를 뺀 원래 줄
    new: bytes


@dataclass(frozen=True)
class _Candidate:
    text: str  # `내용 (← YYYY-MM-DD)`
    key: str  # 중복 판정용 정규화 텍스트
    edit: _Edit


@dataclass
class Result:
    path: Path
    created: bool = False
    changed: bool = False
    today_items: int = 0
    carried: int = 0  # 이번에 carryover에 새로 더한 항목
    marked: int = 0  # `[>]`로 표시한 원래 줄
    today_skipped: bool = False  # 오늘 파일을 쓰지 못함(사람 편집 등)
    skipped: list[str] = field(default_factory=list)  # "경로: 이유"


# ---------------------------------------------------------------- 도우미


def _read_bytes(path: Path) -> bytes:
    """읽기 지점(테스트가 '도중에 사람이 고침'을 흉내 낼 때 바꿔 끼운다)."""
    return path.read_bytes()


def _rel(cfg: Config, path: Path) -> str:
    try:
        return notes.rel_path(cfg, path)
    except ValueError:
        return str(path)


def _decode(data: bytes, rel: str) -> str:
    try:
        return notes.decode(data)
    except UnicodeDecodeError as exc:
        raise core.KbError(f"{rel}: UTF-8 파일이 아니라 고치지 않았습니다 ({exc.reason})") from exc


def _region_lines(data: bytes, name: str) -> list[str]:
    body = managed.read_region(data, name)
    if not body:
        return []
    return notes.split_lines(body.decode("utf-8"))  #   같은 문자에서 줄을 끊지 않는다


def _line_end(data: bytes, offset: int) -> int:
    nl = data.find(b"\n", offset)
    return len(data) if nl < 0 else nl + 1


def _empty_region(name: str, eol: bytes) -> bytes:
    return f"<!-- kb:{name}:start -->".encode("ascii") + eol + f"<!-- kb:{name}:end -->".encode("ascii") + eol


def _parse_day(text: str) -> dt.date:
    try:
        return dt.date.fromisoformat(text)
    except ValueError as exc:
        raise core.ConfigError(f"--date: YYYY-MM-DD 형식이어야 합니다 (지금 값: {text!r})") from exc


# ---------------------------------------------------------------- 뼈대


def ensure_skeleton(data: bytes, rel: str = "daily/x.md") -> bytes:
    """빠진 제목·구역을 채운다. 기존 바이트는 그대로 두고 삽입·덧붙이기만 한다(D24).

    - 제목이 있고 구역이 없으면: 제목 줄 바로 아래에 빈 구역.
    - 제목도 구역도 없으면: 파일 끝에 `## 제목` + 빈 구역(`## 기록`은 제목만).
    - 구역이 다른 곳에 이미 있으면 그 부분은 있는 것으로 본다.
    """
    regions = {name for name, _start, _end in managed.spans(data)}  # MarkerError는 호출자가 처리
    eol = managed.detect_eol(data)
    headings: dict[str, notes.NoteLine] = {}
    for line in notes.iter_lines(rel, data):
        if line.in_code or line.region is not None:
            continue
        match = notes.HEADING2.match(line.text)
        if match:
            headings.setdefault(match.group(1).strip(), line)

    inserts: list[tuple[int, bytes]] = []
    for heading, region in _SECTIONS:
        if region and heading in headings and region not in regions:
            at = _line_end(data, headings[heading].offset)
            prefix = b"" if data[:at].endswith((b"\n", b"\r")) else eol
            inserts.append((at, prefix + _empty_region(region, eol)))
    for at, chunk in sorted(inserts, reverse=True):
        data = data[:at] + chunk + data[at:]

    blocks: list[bytes] = []
    for heading, region in _SECTIONS:
        if heading in headings or (region and region in regions):
            continue
        block = f"## {heading}".encode("utf-8") + eol
        if region:
            block += _empty_region(region, eol)
        blocks.append(block)
    if not blocks:
        return data
    head = data
    if head and head != notes.BOM and not head.endswith((b"\n", b"\r")):
        head += eol
    content = head[len(notes.BOM):] if head.startswith(notes.BOM) else head
    if content.strip() and not content.endswith(eol + eol):
        head += eol  # 기존 글과 빈 줄로 띄운다
    return head + eol.join(blocks)


# ---------------------------------------------------------------- today 구역


def _due(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    try:
        day = dt.date.fromisoformat(text[:10])
    except ValueError:
        return text
    return f"{day.month:02d}-{day.day:02d}"


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def _render_item(row: dict[str, str], *, blocked: bool) -> str:
    head = f"[[{row['key']}]]"
    title = _clean(row.get("제목", ""))
    parts = [f"{head} {title}" if title else head]
    if blocked:
        parts.append("막힘")
    else:
        status = _clean(row.get("상태", ""))
        if status:
            parts.append(status)
    due = _due(row.get("마감", ""))
    if due:
        parts.append(f"마감 {due}")
    return " · ".join(parts)


def view_items(cfg: Config) -> list[tuple[str, str]] | None:
    """[(키, '[[KEY]] 제목 · 상태 · 마감 MM-DD')]. 뷰 파일이 둘 다 없으면 None."""
    views = cfg.path("jira", "_views")
    if not (views / "today.md").exists() and not (views / "blocked.md").exists():
        return None
    today_rows = [r for r in notes.read_view(cfg, "today") if _JIRA_KEY.match(r.get("key", ""))]
    blocked_rows = [r for r in notes.read_view(cfg, "blocked") if _JIRA_KEY.match(r.get("key", ""))]
    blocked = {r["key"]: r for r in blocked_rows}
    items: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in today_rows + blocked_rows:
        key = row["key"]
        if key in seen:
            continue
        seen.add(key)
        if key in blocked:
            items.append((key, _render_item(blocked[key], blocked=True)))
        else:
            items.append((key, _render_item(row, blocked=False)))
    return items


def merge_today_record(
    old_lines: list[str], items: list[tuple[str, str]] | None, previous: Mapping[str, str] | None = None
) -> tuple[list[str], dict[str, str]]:
    """today 구역 merge. (새 줄 목록, 이번에 그린 본문 기록 {키: '[[KEY]] …'}).

    도구가 그린 줄은 `- [s] 본문`이 지금 뷰의 본문이나 지난번에 그린 본문(previous)과 정확히 같은,
    키별 첫 줄뿐이다. 그 줄만 다시 그리고(체크 상태 유지), 뷰에서 빠졌으면 `[x]`일 때만 남긴다.
    그 밖의 줄(같은 키의 둘째 줄, 사람이 덧붙여 고친 줄, 뷰에 없는 키의 사람 줄, 키 없는 글)은
    바이트 그대로, 원래 순서대로 남긴다. 각 사람 줄은 바로 앞의 도구 줄을 따라다닌다.
    사람이 쓴 줄이 이미 있는 키는 새 줄을 더하지 않는다(사람 줄이 그 키를 대신한다).
    """
    previous = previous or {}
    view = dict(items or [])
    lead: list[str] = []  # 첫 도구 줄 앞의 사람 줄
    ours: dict[str, tuple[str, str, list[str]]] = {}  # 키 → (체크, 원래 줄, 뒤따르는 사람 줄)
    taken: set[str] = set()  # 사람 줄로 이미 있는 키
    followers = lead
    for line in old_lines:
        stripped = line.strip()
        if not stripped or stripped == NOTE_NO_SYNC:
            continue
        match = _TODAY_LINE.match(line)
        if match:
            key, mark = match.group(2), match.group(1)
            body = line[len("- [ ] "):]
            if key not in ours and body in (view.get(key), previous.get(key)):
                ours[key] = (mark, line, [])
                followers = ours[key][2]
                continue
            taken.add(key)
        followers.append(line)
    out = list(lead)
    record: dict[str, str] = {}
    for key, body in items or []:
        if key in ours:
            mark, _line, after = ours[key]
            out.append(f"- [{mark}] {body}")
            out.extend(after)
            record[key] = body
        elif key not in taken:
            out.append(f"- [ ] {body}")
            record[key] = body
    for key, (mark, line, after) in ours.items():
        if key in view:
            continue
        if mark in "xX":
            out.append(line)
            record[key] = line[len("- [ ] "):]
        out.extend(after)
    if items is None:
        out.append(NOTE_NO_SYNC)
    return out, record


def merge_today(
    old_lines: list[str], items: list[tuple[str, str]] | None, *, previous: Mapping[str, str] | None = None
) -> list[str]:
    """체크 상태를 키별로 지키며 합친다. 규칙은 merge_today_record."""
    return merge_today_record(old_lines, items, previous)[0]


def _load_records(cfg: Config) -> dict[str, dict[str, str]]:
    """`.state/today.json`: daily별로 지난번에 그린 today 본문. 손상되었으면 빈 것으로 본다."""
    try:
        data = core.load_state(cfg.root, TODAY_STATE)
    except (core.KbError, OSError):
        return {}
    return {
        rel: {str(k): str(v) for k, v in item.items()}
        for rel, item in data.items()
        if isinstance(rel, str) and isinstance(item, dict)
    }


def _save_record(cfg: Config, records: dict[str, dict[str, str]], rel: str, record: dict[str, str]) -> None:
    if records.get(rel, {}) == record:
        return
    records[rel] = record
    keep = sorted(records)[-TODAY_STATE_KEEP:]
    core.save_state(cfg.root, TODAY_STATE, {name: records[name] for name in keep})


# ---------------------------------------------------------------- carryover


def _carry_key(text: str) -> str:
    match = _ANY_TASK.match(text)
    return core.normalize_line(match.group(1) if match else text)


def _mark(line: notes.NoteLine, path: Path, content: str, day: dt.date) -> _Edit:
    body = content.rstrip(_TRAILING_BLANKS)  # str.rstrip()은 \x0c·  같은 줄 안 문자까지 지운다
    new = f"- [>] {body} ({day.isoformat()}로 이월)"
    return _Edit(path, line.offset, line.text.encode("utf-8"), new.encode("utf-8"))


def _dailies(cfg: Config) -> list[tuple[dt.date, Path]]:
    base = cfg.path("daily")
    if not base.is_dir():
        return []
    found: list[tuple[dt.date, Path]] = []
    for path in base.glob("*.md"):
        match = notes.DAILY_NAME.match(path.name)
        if not match or not core.is_scannable(path) or not path.is_file():
            continue
        try:
            when = dt.date.fromisoformat(match.group(1))
        except ValueError:
            continue
        found.append((when, path))
    return sorted(found)


def _earlier_dailies(cfg: Config, day: dt.date) -> list[tuple[dt.date, Path]]:
    return [(when, path) for when, path in _dailies(cfg) if when < day]


# ---------------------------------------------------------------- 승격한 할 일(D53)


def task_body(text: str) -> str | None:
    """`- [ ] B`·`- [>] B (날짜로 이월)`(끝의 ` [[KEY]]` 무시) → 이월 사본과 비교할 정규화 본문.

    이월 사본은 B의 앞 시각(`HH:MM`)을 떼므로 여기서도 뗀다. 할 일 줄이 아니면 None.
    """
    line = _ENDS_WITH_KEY.sub("", text.rstrip(_TRAILING_BLANKS)).rstrip(_TRAILING_BLANKS)
    match = _CARRIED_MARK.match(line) or _OPEN_TASK.match(line)
    if match is None:
        return None
    body = _TIME.sub("", match.group(1).strip(), count=1).strip()
    return core.normalize_line(body) or None


def source_hashes(text: str) -> set[str]:
    """줄의 해시와, `[>] … (이월)` 꼴이면 원래 `- [ ] …` 꼴의 해시(승격 기록의 line_hash와 맞춰 본다)."""
    line = text.rstrip(_TRAILING_BLANKS)
    out = {core.line_hash(line)}
    carried = _CARRIED_MARK.match(line.strip())
    if carried:
        out.add(core.line_hash(f"- [ ] {carried.group(1)}"))
    return out


def promoted_lines(cfg: Config) -> dict[str, set[str]]:
    """outbox의 pending·sending·done 승격 기록: {daily 상대 경로: 줄 해시들}. 읽기만 하고, 없거나 못 읽으면 {}."""
    try:
        items = outbox.records(cfg.root)
    except (core.KbError, OSError) as exc:
        core.get_logger(cfg.root).warning("이월: outbox를 읽지 못해 승격 기록을 보지 않습니다 (%s)", exc)
        return {}
    out: dict[str, set[str]] = {}
    for record in items:
        if record.get("action") != "promote" or record.get("status") not in _PROMOTE_LIVE:
            continue
        rel = str(record.get("source") or "").rpartition(":")[0]
        want = (record.get("payload") or {}).get("line_hash") if isinstance(record.get("payload"), dict) else None
        if rel and isinstance(want, str) and want:
            out.setdefault(rel, set()).add(want)
    return out


def _is_promoted(text: str, rel: str, promoted: Mapping[str, set[str]]) -> bool:
    return bool(_ENDS_WITH_KEY.search(text.rstrip(_TRAILING_BLANKS))) or bool(promoted.get(rel, set()) & source_hashes(text))


def _source_lines(cfg: Config, rel: str, cache: dict[str, list[notes.NoteLine]]) -> list[notes.NoteLine]:
    """daily의 `## 기록` 줄(관리 구역·코드 밖). 없거나 못 읽으면 []."""
    if rel not in cache:
        lines: list[notes.NoteLine] = []
        path = cfg.path(*rel.split("/"))
        try:
            data = path.read_bytes()
            span = notes.section_span(data, "기록")
            if span is not None:
                lines = [
                    line for line in notes.iter_lines(rel, data)
                    if not line.in_code and line.region is None and span[0] <= line.offset < span[1]
                ]  # fmt: skip
        except (OSError, UnicodeDecodeError):
            lines = []
        cache[rel] = lines
    return cache[rel]


def _copy_promoted(cfg: Config, text: str, promoted: Mapping[str, set[str]], cache: dict[str, list[notes.NoteLine]]) -> bool:
    """carryover 사본 `내용 (← 날짜)`의 원래 줄(그 날짜 daily의 `## 기록`)이 승격됐는가."""
    match = _CARRY_COPY.match(text.rstrip(_TRAILING_BLANKS))
    if match is None:
        return False
    body = core.normalize_line(match.group(1))
    rel = f"daily/{match.group(2)}.md"
    for line in _source_lines(cfg, rel, cache):
        if task_body(line.text) == body and _is_promoted(line.text, rel, promoted):
            return True
    return False


def link_carried_copies(cfg: Config, source_rel: str, source_text: str, key: str) -> tuple[int, list[str]]:
    """승격한 줄(source_rel의 source_text)의 이월 사본이 뒤 날짜 carryover 구역에 있으면 끝에 ` [[KEY]]`를 붙인다.

    `- [ ]`·`- [x]` 사본만 본다(`[>]`는 이미 옮긴 흔적). 잠금 안, expect_sha256로 쓴다.
    (붙인 사본 수, 건너뛴 파일 설명들).
    """
    day = notes.day_of(source_rel)
    body = task_body(source_text)
    if day is None or body is None:
        return 0, []
    link = f"[[{key}]]"
    probe = f"(← {day})".encode("utf-8")
    count = 0
    problems: list[str] = []
    with core.kb_lock(cfg.root, "daily_prepare"):
        for when, path in _dailies(cfg):
            if when.isoformat() <= day:
                continue
            data = path.read_bytes()
            if probe not in data:
                continue
            rel = _rel(cfg, path)
            try:
                lines = list(notes.iter_lines(rel, data))
            except UnicodeDecodeError:
                continue
            edits: list[notes.NoteLine] = []
            for line in lines:
                if line.in_code or line.region != "carryover":
                    continue
                task = _COPY_TASK.match(line.text)
                if task is None:
                    continue
                text = task.group(1).rstrip(_TRAILING_BLANKS)
                copy = _CARRY_COPY.match(text)
                if link in text or _ENDS_WITH_KEY.search(text) or copy is None:
                    continue
                if copy.group(2) == day and core.normalize_line(copy.group(1)) == body:
                    edits.append(line)
            if not edits:
                continue
            new = data
            for line in reversed(edits):
                old = line.text.encode("utf-8")
                replacement = f"{line.text.rstrip(_TRAILING_BLANKS)} {link}".encode("utf-8")
                new = new[: line.offset] + replacement + new[line.offset + len(old):]
            try:
                core.atomic_write(path, new, expect_sha256=core.sha256_hex(data))
            except (core.ConcurrentEdit, core.FileBusy) as exc:
                problems.append(f"{rel}: {exc}")
                continue
            count += len(edits)
    return count, problems


def collect_carryover(cfg: Config, day: dt.date, keys: list[str], skipped: list[str]) -> tuple[list[_Candidate], dict[Path, bytes]]:
    """이전 daily들에서 옮길 항목과 그 파일의 읽은 바이트. 못 읽거나 표시가 깨진 파일은 skipped에 적는다.

    승격한(또는 승격 중인) 할 일과 그 이월 사본은 옮기지 않는다(D53).
    """
    logger = core.get_logger(cfg.root)
    candidates: list[_Candidate] = []
    sources: dict[Path, bytes] = {}
    promoted = promoted_lines(cfg)
    cache: dict[str, list[notes.NoteLine]] = {}
    for when, path in _earlier_dailies(cfg, day):
        data = _read_bytes(path)
        if _TASK_PROBE not in data:
            continue
        rel = _rel(cfg, path)
        try:
            notes.decode(data)
            managed.spans(data)
        except UnicodeDecodeError:
            skipped.append(f"{rel}: UTF-8이 아니라 이월하지 않았습니다")
            logger.warning("이월 건너뜀(UTF-8 아님): %s", rel)
            continue
        except managed.MarkerError as exc:
            skipped.append(f"{rel}: 관리 구역 표시가 잘못되어 이월하지 않았습니다 ({exc})")
            logger.warning("이월 건너뜀(구역 표시 오류): %s: %s", rel, exc)
            continue
        span = notes.section_span(data, "기록")
        found = False
        for line in notes.iter_lines(rel, data):
            if line.in_code:
                continue
            match = _OPEN_TASK.match(line.text)
            if not match or notes.find_keys(line.text, keys):
                continue
            if line.region == "carryover":
                text = match.group(1).strip()
                if _ENDS_WITH_KEY.search(text) or _copy_promoted(cfg, text, promoted, cache):
                    continue
            elif line.region is None and span is not None and span[0] <= line.offset < span[1]:
                if _is_promoted(line.text, rel, promoted):
                    continue
                body = _TIME.sub("", match.group(1).strip(), count=1).strip()
                text = f"{body} (← {when.isoformat()})" if body else ""
            else:
                continue
            if not text:
                continue
            candidates.append(_Candidate(text, core.normalize_line(text), _mark(line, path, match.group(1), day)))
            found = True
        if found:
            sources[path] = data
    return candidates, sources


def merge_carryover(old_lines: list[str], candidates: list[_Candidate]) -> tuple[list[str], int]:
    """이미 있는 항목(체크 상태 무관)은 두고 새 항목만 끝에 더한다."""
    seen = {_carry_key(line) for line in old_lines if line.strip()}
    out = list(old_lines)
    added = 0
    for cand in candidates:
        if cand.key in seen:
            continue
        seen.add(cand.key)
        out.append(f"- [ ] {cand.text}")
        added += 1
    return out, added


def _apply_marks(cfg: Config, edits: list[_Edit], sources: dict[Path, bytes], result: Result) -> None:
    by_path: dict[Path, list[_Edit]] = {}
    for edit in edits:
        by_path.setdefault(edit.path, []).append(edit)
    for path, items in by_path.items():
        original = sources[path]
        data = original
        for edit in sorted({e.offset: e for e in items}.values(), key=lambda e: e.offset, reverse=True):
            end = edit.offset + len(edit.old)
            if data[edit.offset:end] != edit.old:  # 방어: 읽은 바이트와 어긋나면 손대지 않는다
                continue
            data = data[:edit.offset] + edit.new + data[end:]
        if data == original:
            continue
        rel = _rel(cfg, path)
        try:
            core.atomic_write(path, data, expect_sha256=core.sha256_hex(original))
        except core.ConcurrentEdit:
            result.skipped.append(f"{rel}: 읽은 뒤에 바뀌어 [>] 표시를 하지 않았습니다. 다시 실행하면 표시합니다")
            core.get_logger(cfg.root).warning("[>] 표시 건너뜀(사람 편집): %s", rel)
            continue
        result.marked += len(items)


# ---------------------------------------------------------------- 실행


def run(cfg: Config, day: dt.date | str, *, carryover: bool = True) -> Result:
    """오늘 파일을 준비한다. 결과(Result)에 만든·바꾼 것과 건너뛴 파일을 담는다."""
    if isinstance(day, str):
        day = _parse_day(day)
    keys: list[str] = []
    if carryover:
        keys = cfg.project_keys
        if not keys:
            raise core.ConfigError(
                "jira.project_keys가 설정되지 않았습니다. 이월은 Jira 키가 없는 할 일만 옮기므로 키 목록이 필요합니다. "
                "config.local.yaml의 jira.project_keys(예: [KB, OPS])를 채우거나 --no-carryover로 실행하세요"
            )
    path = notes.daily_path(cfg, day)
    result = Result(path=path)
    with core.kb_lock(cfg.root, "daily_prepare"):
        rel = _rel(cfg, path)
        exists = path.exists()
        original = _read_bytes(path) if exists else None
        data = original if original is not None else notes.daily_skeleton(day)
        _decode(data, rel)
        try:
            data = ensure_skeleton(data, rel)
            old_today = _region_lines(data, "today")
            records = _load_records(cfg)
            new_today, record = merge_today_record(old_today, view_items(cfg), records.get(rel))
            result.today_items = len(record)  # 도구가 그린 일감 줄(사람 줄 제외)
            if new_today != old_today:
                data = managed.replace_region(data, "today", "\n".join(new_today))
            candidates: list[_Candidate] = []
            sources: dict[Path, bytes] = {}
            if carryover:
                candidates, sources = collect_carryover(cfg, day, keys, result.skipped)
                old_carry = _region_lines(data, "carryover")
                new_carry, result.carried = merge_carryover(old_carry, candidates)
                if result.carried:
                    data = managed.replace_region(data, "carryover", "\n".join(new_carry))
        except managed.MarkerError as exc:
            raise core.KbError(f"{rel}: 관리 구역 표시가 잘못되어 고치지 않았습니다 ({exc})") from exc

        if data != original:
            try:
                if original is None:  # 읽은 뒤에 누가 만들었으면 ConcurrentEdit(교체 순간까지 확인)
                    core.atomic_write(path, data, expect_sha256=core.MUST_NOT_EXIST)
                else:
                    core.atomic_write(path, data, expect_sha256=core.sha256_hex(original))
            except core.ConcurrentEdit:
                result.today_skipped = True
                result.skipped.append(f"{rel}: 읽은 뒤에 바뀌어 쓰지 않았습니다. 다시 실행하세요")
                core.get_logger(cfg.root).warning("daily 준비 건너뜀(사람 편집): %s", rel)
                return result
            result.created = original is None
            result.changed = True
        _save_record(cfg, records, rel, record)
        if carryover and candidates:
            _apply_marks(cfg, [c.edit for c in candidates], sources, result)
    return result


def prepare(cfg: Config, day: dt.date | str, *, carryover: bool = True) -> Path:
    """계약(부록 A §2): 오늘 daily를 준비하고 경로를 돌려준다."""
    return run(cfg, day, carryover=carryover).path


# ---------------------------------------------------------------- --check-done (읽기 전용)

DONE_TRANSITION_NAMES = ("Done", "완료")
_MOVE_HOPS = 5
KB_CMD = "python scripts/kb.py"


@dataclass(frozen=True)
class DoneItem:
    """`[x]` 일감 하나의 완료 전환 제안(또는 안내).

    kind: proposal(전환 제안), no_cache(캐시 없음·스텁·못 읽음 → 동기화 안내), out_of_scope(범위 밖).
    """

    key: str  # 제안 대상 키(옮겨졌으면 새 키)
    line: int  # daily의 줄 번호(1부터)
    title: str
    status: str
    status_category: str
    kind: str
    transition: str  # 제안 전환 이름(없으면 "")
    command: str  # 실행할 명령(없으면 "")
    hint: str  # 덧붙이는 안내(없으면 "")
    moved_from: str = ""  # 옮겨진 일감이면 daily에 적힌 옛 키


@dataclass
class CheckDoneResult:
    path: Path
    exists: bool
    items: list[DoneItem] = field(default_factory=list)
    done: int = 0  # 이미 완료라 건너뛴 `[x]` 일감


def done_transition_names(cfg: Config) -> list[str]:
    """`jira.done_transition_names`(목록 또는 쉼표 구분 글자). 비었거나 없으면 기본값."""
    raw = cfg.get("jira.done_transition_names")
    if raw is None:
        return list(DONE_TRANSITION_NAMES)
    if isinstance(raw, str):
        items: list[Any] = raw.split(",")
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        raise core.ConfigError("jira.done_transition_names: 전환 이름 목록이어야 합니다. 예: [Done, 완료]")
    names: list[str] = []
    for item in items:
        if isinstance(item, bool) or not isinstance(item, (str, int, float)):
            raise core.ConfigError(
                f"jira.done_transition_names: {item!r}은(는) 전환 이름이 아닙니다. 따옴표로 감싼 글자로 쓰세요"
            )
        name = " ".join(str(item).split())
        if name and name.casefold() not in {n.casefold() for n in names}:
            names.append(name)
    return names or list(DONE_TRANSITION_NAMES)


def _quote(text: str) -> str:
    return '"' + text.replace('"', '\\"') + '"'


def _issue_meta(cfg: Config, key: str) -> dict[str, Any] | None:
    """jira/KEY.md의 front matter. 없거나 못 읽으면 None."""
    path = cfg.path("jira", f"{key}.md")
    if not path.is_file():
        return None
    try:
        meta = frontmatter.parse(path.read_bytes())
    except (OSError, core.KbError):
        return None
    return meta if isinstance(meta, dict) else None


def _resolve_issue(cfg: Config, key: str) -> tuple[str, dict[str, Any] | None]:
    """moved_to를 따라간 (키, front matter). 고리나 너무 긴 사슬이면 마지막으로 읽은 것에서 멈춘다."""
    seen = {key}
    meta = _issue_meta(cfg, key)
    for _ in range(_MOVE_HOPS):
        target = str((meta or {}).get("moved_to") or "").strip().upper()
        if not target or not _JIRA_KEY.match(target) or target in seen:
            break
        target_meta = _issue_meta(cfg, target)
        if target_meta is None:  # 새 키 캐시가 아직 없음 → 새 키로 동기화 안내
            return target, None
        seen.add(target)
        key, meta = target, target_meta
    return key, meta


def _line_title(text: str) -> str:
    """`- [x] [[KEY]] 제목 · 상태 · 마감 …`에서 제목."""
    rest = text.split("]]", 1)[1] if "]]" in text else ""
    return _clean(rest.split(" · ", 1)[0])


def _truthy_false(value: Any) -> bool:
    return value is False or (isinstance(value, str) and value.strip().lower() == "false")


def _transition_choice(meta: Mapping[str, Any], names: list[str]) -> tuple[str, bool]:
    """(전환 이름, 캐시의 전환 목록으로 확인했는가). 캐시에 목록이 있는데 맞는 이름이 없으면 ("", True)."""
    raw = meta.get("transitions")
    available: list[str] = []
    if isinstance(raw, (list, tuple)):
        for item in raw:
            name = item.get("name") if isinstance(item, Mapping) else item
            if isinstance(name, str) and name.strip():
                available.append(" ".join(name.split()))
    if not available:
        return names[0], False
    by_fold = {name.casefold(): name for name in available}
    for wanted in names:
        if wanted.casefold() in by_fold:
            return by_fold[wanted.casefold()], True
    return "", True


def _check_item(cfg: Config, key: str, lineno: int, text: str, names: list[str]) -> DoneItem | None:
    """완료가 아닌 `[x]` 일감의 제안. 이미 완료면 None."""
    target, meta = _resolve_issue(cfg, key)
    moved_from = key if target != key else ""
    title = _line_title(text)
    sync_hint = f"먼저 동기화: {KB_CMD} sync --key {target}"
    if meta is None or str(meta.get("stub", "")).strip().lower() == "true" or not str(meta.get("status_category") or "").strip():
        return DoneItem(target, lineno, title, "", "", "no_cache", "", "", sync_hint, moved_from)
    title = _clean(str(meta.get("summary") or "")) or title
    status = _clean(str(meta.get("status") or ""))
    category = _clean(str(meta.get("status_category") or "")).lower()
    if category == "done":
        return None
    if _truthy_false(meta.get("in_scope")):
        hint = f"Jira에서 찾지 못했거나 범위 밖입니다. 확인: {KB_CMD} sync --key {target}"
        return DoneItem(target, lineno, title, status, category, "out_of_scope", "", "", hint, moved_from)
    name, checked = _transition_choice(meta, names)
    candidates = f"{KB_CMD} act transitions {target}"
    if not name:
        hint = f"설정의 완료 전환({', '.join(names)})이 없습니다. 후보 확인: {candidates}"
        return DoneItem(target, lineno, title, status, category, "proposal", "", "", hint, moved_from)
    command = f"{KB_CMD} act transition {target} {_quote(name)}"
    hint = "" if checked else f"전환 이름이 다르면 후보 확인: {candidates}"
    return DoneItem(target, lineno, title, status, category, "proposal", name, command, hint, moved_from)


def _checked_lines(rel: str, data: bytes) -> list[tuple[str, int, str]]:
    """`## 오늘`(today 구역과 그 제목 아래 사람 줄)의 `- [x] [[KEY]]` 줄: [(키, 줄 번호, 줄)]. 키별 첫 줄만."""
    span = notes.section_span(data, "오늘")
    found: list[tuple[str, int, str]] = []
    seen: set[str] = set()
    for line in notes.iter_lines(rel, data):
        if line.in_code:
            continue
        in_today = line.region == "today" or (
            line.region is None and span is not None and span[0] <= line.offset < span[1]
        )
        if not in_today:
            continue
        match = _TODAY_LINE.match(line.text)
        if not match or match.group(1) not in "xX" or match.group(2) in seen:
            continue
        seen.add(match.group(2))
        found.append((match.group(2), line.lineno, line.text))
    return found


def check_done(cfg: Config, day: dt.date | str) -> CheckDoneResult:
    """완료 전환 제안 목록. Jira를 부르지 않고 아무것도 쓰지 않는다."""
    if isinstance(day, str):
        day = _parse_day(day)
    path = notes.daily_path(cfg, day)
    result = CheckDoneResult(path=path, exists=path.is_file())
    if not result.exists:
        return result
    names = done_transition_names(cfg)
    rel = _rel(cfg, path)
    data = _read_bytes(path)
    _decode(data, rel)
    for key, lineno, text in _checked_lines(rel, data):
        item = _check_item(cfg, key, lineno, text, names)
        if item is None:
            result.done += 1
        else:
            result.items.append(item)
    return result


def format_done_item(item: DoneItem) -> list[str]:
    head = f"{item.key} {item.title}".rstrip()
    if item.moved_from:
        head += f" (← {item.moved_from})"
    if item.kind == "no_cache":
        return [f"{head} · 캐시 없음 → {item.hint}"]
    if item.kind == "out_of_scope":
        return [f"{head} · 현재 {item.status or '?'} → {item.hint}"]
    if not item.command:
        return [f"{head} · 현재 {item.status or '?'} → 완료 전환 제안: {item.hint}"]
    lines = [f"{head} · 현재 {item.status or '?'} → 완료 전환 제안: {item.command}"]
    if item.hint:
        lines.append(f"  ({item.hint})")
    return lines


def _check_done_main(cfg: Config, day: dt.date, as_json: bool) -> int:
    result = check_done(cfg, day)
    rel = _rel(cfg, result.path)
    if as_json:
        if not result.exists:
            print(f"{rel}이(가) 없습니다", file=sys.stderr)
        print(json.dumps([asdict(item) for item in result.items], ensure_ascii=False, indent=1))
        return 0
    if not result.exists:
        print(f"{rel}이(가) 없습니다. 먼저 `{KB_CMD} today`로 오늘 파일을 만드세요.")
        return 0
    for item in result.items:
        for line in format_done_item(item):
            print(line)
    if not result.items:
        print(f"완료 전환 제안 없음 ({rel}, 이미 완료 {result.done}건)")
    else:
        proposals = sum(1 for item in result.items if item.command)
        print(
            f"전환 제안 {proposals}건, 확인 필요 {len(result.items) - proposals}건, 이미 완료 {result.done}건 ({rel}). "
            "자동 전환은 하지 않습니다."
        )
    return 0


def today_main(argv: list[str]) -> int:
    parser = core.make_parser("kb.py today", "오늘 daily를 준비(뼈대·오늘·이월 구역)하고 경로를 출력")
    parser.add_argument("--date", default=None, help="날짜 YYYY-MM-DD (기본: 오늘)")
    parser.add_argument("--no-carryover", action="store_true", help="이월 구역을 건드리지 않음")
    parser.add_argument("--print-path", action="store_true", help="파일 경로만 출력")
    parser.add_argument("--check-done", action="store_true", help="`[x]` 일감의 완료 전환 제안만 출력(읽기 전용, 자동 전환 없음)")
    parser.add_argument("--json", action="store_true", help="--check-done 결과를 JSON 목록으로")
    args = parser.parse_args(argv)
    if args.json and not args.check_done:
        raise core.ConfigError("--json은 --check-done과 함께만 쓸 수 있습니다")
    cfg = config.load(args.root)
    day = _parse_day(args.date) if args.date else core.now(cfg.tz).date()
    if args.check_done:
        return _check_done_main(cfg, day, args.json)
    result = run(cfg, day, carryover=not args.no_carryover)
    for item in result.skipped:
        print(f"건너뜀: {item}", file=sys.stderr)
    if args.print_path:
        print(result.path)
    else:
        rel = _rel(cfg, result.path)
        if result.today_skipped:
            state = "쓰지 못함"
        elif result.created:
            state = "만듦"
        elif result.changed:
            state = "고침"
        else:
            state = "변경 없음"
        print(f"{rel}: {state}")
        print(f"오늘 일감 {result.today_items}건, 새로 이월 {result.carried}건, [>] 표시 {result.marked}건")
        print(result.path)
    return 1 if result.today_skipped else 0


main = today_main

if __name__ == "__main__":
    core.run_main(today_main)
