"""오늘 daily 준비: 뼈대, `today` 구역(Jira 뷰), `carryover` 구역(개인 할 일 이월).

사용법: python scripts/kb.py today [--date YYYY-MM-DD] [--no-carryover] [--print-path]

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
- 쓰기는 잠금 안에서, daily는 expect_sha256로. 사람이 도중에 고친 파일은 건너뛰고 보고한다.
"""
from __future__ import annotations

import datetime as dt
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from lib import config, core, managed, notes
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


def _earlier_dailies(cfg: Config, day: dt.date) -> list[tuple[dt.date, Path]]:
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
        if when < day:
            found.append((when, path))
    return sorted(found)


def collect_carryover(cfg: Config, day: dt.date, keys: list[str], skipped: list[str]) -> tuple[list[_Candidate], dict[Path, bytes]]:
    """이전 daily들에서 옮길 항목과 그 파일의 읽은 바이트. 못 읽거나 표시가 깨진 파일은 skipped에 적는다."""
    logger = core.get_logger(cfg.root)
    candidates: list[_Candidate] = []
    sources: dict[Path, bytes] = {}
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
            elif line.region is None and span is not None and span[0] <= line.offset < span[1]:
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


def today_main(argv: list[str]) -> int:
    parser = core.make_parser("kb.py today", "오늘 daily를 준비(뼈대·오늘·이월 구역)하고 경로를 출력")
    parser.add_argument("--date", default=None, help="날짜 YYYY-MM-DD (기본: 오늘)")
    parser.add_argument("--no-carryover", action="store_true", help="이월 구역을 건드리지 않음")
    parser.add_argument("--print-path", action="store_true", help="파일 경로만 출력")
    parser.add_argument("--check-done", action="store_true", help="완료 전환 제안 (Phase 3)")
    args = parser.parse_args(argv)
    if args.check_done:
        print("완료 전환 제안(--check-done)은 Phase 3에서 추가됩니다.")
        return 4
    cfg = config.load(args.root)
    day = _parse_day(args.date) if args.date else core.now(cfg.tz).date()
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
