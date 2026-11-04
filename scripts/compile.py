"""B단계 편찬 (`kb.py compile`, `kb.py proposals`). LLM으로 태그·결정·할 일·엔티티 요약·주제 제안을 만든다.

작업(§6.5, AGENTS.md ## 작업 스키마)
- T1 tag: `## 기록` 줄마다 고정 태그 하나. 여러 줄을 한 요청에 묶는다(줄 id → 태그). 결과는 원장에만
  남는다(D22: link가 index에 개수를 그린다).
- T2 decisions: 하루치 `## 기록`(+ 그날 이미지 사이드카의 `extracted` 글) + 언급된 Jira 캐시 요약 →
  `wiki/decisions.md`(상태 '제안')와 `wiki/actions.md`에 덧붙인다(append). 항목마다
  `<!-- src:HASH -->`(원천 파일 + 줄 해시)를 남겨 (파일, 줄 해시)로 중복을 막는다(D47). 출처는 daily 줄이면
  `[[날짜]] HH:MM`, 사이드카 줄이면 link와 같은 `[[inbox/<이름>]]`. 이미 반영된 줄은
  입력에 `(이미 반영)`으로 표시한다. 입력에 없는 `source_line`은 버리고, project_keys 밖의 키는 뺀다.
  체크박스 줄(`- [ ]`·`- [x]`)에서 나온 할 일은 버린다(개인 할 일은 daily_prepare 몫). `[>]` 줄은 입력에서 뺀다.
- T3 entity_summary: projects·people·topics 페이지 중 `mentions`에 새 줄이 생긴 것. 현재 요약 + 새 언급
  (+ 프로젝트의 `issues` 표) → `summary` 구역을 통째로 바꾼다. `sources`가 입력에 없으면 스키마 실패.
- T4 topic_proposal: `--task topic_proposal`이거나 마지막 성공 뒤 7일이 지났을 때. 최근 2주 기록 + 기존
  주제 → `wiki/_proposals.md`의 `proposals` 구역. 제안 번호는 원장(`_proposals`)에 고정된다.
  `kb.py proposals accept <n|all>`가 `wiki/topics/<이름>.md`를 만들거나(`merge_with`면 그 주제의
  `aliases`에 이름을 더한다).

원장 `.state/compile.json`
    {"tag": {"daily/2026-11-04.md#<줄해시>": {status, profile, at, line_hashes, tag}},
     "decisions": {"daily/2026-11-04.md": {...}},
     "entity_summary": {"wiki/projects/KB.md": {...}},
     "topic_proposal": {"2026-11-05": {...}},
     "_proposals": {"next_id": 2, "items": {"1": {name, why, sources, merge_with, status, created, ...}}}}
  status: ok | skipped | gave_up. 같은 입력으로 3번 연속 건너뛰면 gave_up이고, 입력이 바뀌거나
  `--force`가 아니면 다시 보내지 않는다. ok인 단위는 새 줄이 생길 때만 다시 보낸다(지운 줄은 무시).
  `_`로 시작하는 키는 작업이 아니다.

지키는 것
- daily·inbox·raw는 읽기만 한다(바이트 불변). 쓰는 곳: wiki/decisions.md, wiki/actions.md,
  wiki/{projects,people,topics}/*.md의 summary 구역, wiki/_proposals.md, .state/compile.json, 로그.
- LLM 호출은 잠금 밖, 쓰기는 `.state/kb.lock` 안에서 파일을 다시 읽고 expect_sha256으로 쓴다(D26).
  관리 구역이 망가졌거나 사람이 동시에 고친 파일은 건너뛰고 알린다(다음 실행에서 다시 시도).
- `llm.enabled: false`이거나 연결되는 프로필이 없으면 한 줄만 남기고 종료 0. 회사 환경 공개 호스트는
  LlmClient가 먼저 거부한다(종료 5). 할 일이 없으면 HTTP 요청을 하나도 하지 않는다.
- `--dry-run`: LLM은 부르지만 아무것도 쓰지 않는다(원장·로그 포함). 바뀔 내용을 unified diff로 출력.

T1 출력 상한: `llm.tasks.tag.max_output_tokens`(기본 64)는 **줄 하나당** 값으로 읽고, 이 모듈이
만드는 클라이언트에는 `줄당 × 묶음 크기(40)`(최대 16384)를 준다. 밖에서 클라이언트를 넘기면 그 상한에
맞춰 묶음 크기를 줄인다(줄당 16토큰 가정).
"""
from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import difflib
import functools
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from lib import config, core, frontmatter, llm_client, managed, notes, prompts
from lib.config import Config
from lib.llm_client import LlmClient, SchemaError, TaskOutcome

TASKS: tuple[str, ...] = ("tag", "decisions", "entity_summary", "topic_proposal")
LEDGER = "compile"  # .state/compile.json
PROPOSALS_KEY = "_proposals"
GIVE_UP_AFTER = 3
DEFAULT_SINCE_DAYS = 7  # --date·--since가 없을 때 T1·T2가 보는 날 수(오늘 포함). 0이면 전부
T4_EVERY_DAYS = 7
T4_WINDOW_DAYS = 14
TAG_BATCH_LINES = 40
TAG_TOKENS_PER_LINE = 16
DEFAULT_TAGS: tuple[str, ...] = ("회의", "결정", "요청", "이슈", "학습", "아이디어", "개인", "참고", "일정", "리뷰")
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})
ENTITY_DIRS: tuple[tuple[str, str], ...] = (("projects", "project"), ("people", "person"), ("topics", "topic"))
ENTITY_LABEL = {"project": "프로젝트", "person": "사람", "topic": "주제"}
PROPOSAL_PENDING = "제안"
PROPOSAL_ACCEPTED = "승인"

MAX_TITLE = 80
MAX_TEXT = 500
MAX_SUMMARY = 1500
MAX_TOPIC_NAME = 40
MAX_ISSUES_CHARS = 6000

_SRC_RE = re.compile(r"<!-- src:([0-9a-f]{8,64}) -->")
_KEY_RE = re.compile(r"^([A-Z][A-Z0-9_]+)-[1-9][0-9]*$")
_PROJECT_RE = re.compile(r"^[A-Z][A-Z0-9_]+$")
_ID_RE = re.compile(r"^\[?\s*([A-Za-z]{1,3})\s*(\d{1,6})\s*\]?$")
_MENTION_SOURCE = re.compile(r"^- (\[\[[^\]]+\]\])(?: (\d{1,2}:\d{2}))?")
_CARRIED = re.compile(r"^[-*+] \[>\] ")
_CHECKBOX = re.compile(r"^[-*+] \[[ xX]\] ")
_BULLET = re.compile(r"^[-*+] ")
_TOPIC_BAD = re.compile(r'[\[\]#^|<>:"/\\?*\x00-\x1f]')
_META_LINE = re.compile(r"^- (출처|갱신): ")

Edit = tuple[Path, "str | None", Callable[[bytes], bytes]]


# ---------------------------------------------------------------- 고정 태그


@functools.lru_cache(maxsize=1)
def fixed_tags() -> tuple[str, ...]:
    """AGENTS.md `## 태그` 절의 쉼표 목록. 읽지 못하면 기본 10개."""
    try:
        text = (prompts.CODE_ROOT / prompts.AGENTS_NAME).read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return DEFAULT_TAGS
    body = prompts.section(text, "태그")
    for line in (body or "").splitlines()[1:]:
        stripped = line.strip()
        if stripped.count(",") >= 2 and not stripped.startswith(("#", "-", "|", ">")):
            tags = tuple(unicodedata.normalize("NFC", t.strip()) for t in stripped.split(",") if t.strip())
            if tags:
                return tags
    return DEFAULT_TAGS


def _norm_tag(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return unicodedata.normalize("NFC", value).strip().lstrip("#").strip()


# ---------------------------------------------------------------- 스키마 (형태만, 맥락 없는 검사)


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise SchemaError(message)


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def norm_id(value: Any) -> str | None:
    """`L1`·`[L1]`·`l 1` → `L1`. 형식이 아니면 None."""
    if not isinstance(value, str):
        return None
    match = _ID_RE.match(value.strip())
    if not match:
        return None
    return f"{match.group(1).upper()}{int(match.group(2))}"


def _check_str_list(value: Any, where: str, *, required: bool) -> None:
    if value is None and not required:
        return
    _need(isinstance(value, list), f"{where}: 문자열 목록이어야 합니다")
    _need(all(isinstance(x, str) for x in value), f"{where}: 문자열만 넣습니다")
    if required:
        _need(bool(value), f"{where}: 비어 있으면 안 됩니다")


def validate_tag(data: Mapping) -> None:
    _need(isinstance(data, Mapping), "JSON 객체가 아닙니다")
    tags = data.get("tags")
    _need(isinstance(tags, Mapping) and bool(tags), "tags: 비어 있지 않은 객체여야 합니다")
    allowed = set(fixed_tags())
    for key, value in tags.items():
        _need(_norm_tag(value) in allowed, f"tags.{key}: 고정 태그가 아닙니다 ({value!r})")


def validate_decisions(data: Mapping) -> None:
    _need(isinstance(data, Mapping), "JSON 객체가 아닙니다")
    for name in ("decisions", "actions"):
        _need(isinstance(data.get(name), list), f"{name}: 목록이어야 합니다")
    for i, item in enumerate(data["decisions"]):
        where = f"decisions[{i}]"
        _need(isinstance(item, Mapping), f"{where}: 객체여야 합니다")
        _need(_nonempty(item.get("title")), f"{where}.title: 비어 있습니다")
        _need(isinstance(item.get("content"), str), f"{where}.content: 문자열이어야 합니다")
        _need(item.get("rationale") is None or isinstance(item.get("rationale"), str), f"{where}.rationale: 문자열이어야 합니다")
        _check_str_list(item.get("related"), f"{where}.related", required=False)
        _need(isinstance(item.get("source_line"), str), f"{where}.source_line: 줄 id 문자열이어야 합니다")
    for i, item in enumerate(data["actions"]):
        where = f"actions[{i}]"
        _need(isinstance(item, Mapping), f"{where}: 객체여야 합니다")
        _need(_nonempty(item.get("text")), f"{where}.text: 비어 있습니다")
        _check_str_list(item.get("related"), f"{where}.related", required=False)
        _need(isinstance(item.get("source_line"), str), f"{where}.source_line: 줄 id 문자열이어야 합니다")


def validate_entity_summary(data: Mapping) -> None:
    _need(isinstance(data, Mapping), "JSON 객체가 아닙니다")
    _need(_nonempty(data.get("summary")), "summary: 비어 있습니다")
    _check_str_list(data.get("sources"), "sources", required=True)


def validate_topic_proposal(data: Mapping) -> None:
    _need(isinstance(data, Mapping), "JSON 객체가 아닙니다")
    proposals = data.get("proposals")
    _need(isinstance(proposals, list), "proposals: 목록이어야 합니다")
    for i, item in enumerate(proposals):
        where = f"proposals[{i}]"
        _need(isinstance(item, Mapping), f"{where}: 객체여야 합니다")
        _need(_nonempty(item.get("name")), f"{where}.name: 비어 있습니다")
        _need(item.get("why") is None or isinstance(item.get("why"), str), f"{where}.why: 문자열이어야 합니다")
        _check_str_list(item.get("sample_sources"), f"{where}.sample_sources", required=True)
        _need(item.get("merge_with") is None or isinstance(item.get("merge_with"), str), f"{where}.merge_with: 문자열 또는 null")


SCHEMAS: dict[str, Callable[[Mapping], None]] = {
    "tag": validate_tag,
    "decisions": validate_decisions,
    "entity_summary": validate_entity_summary,
    "topic_proposal": validate_topic_proposal,
}


# ---------------------------------------------------------------- 자료형


@dataclass(frozen=True)
class Item:
    """모델에 보여 주는 입력 줄 하나."""

    rel: str  # 원천 파일(볼트 기준, `/`)
    hash: str  # core.line_hash(원문 줄)
    text: str  # 모델에 보일 글(목록 기호 `- ` 뗌)
    day: str  # YYYY-MM-DD(분할용). 없으면 ""
    source: str  # 렌더된 출처: `[[2026-11-04]] 09:12`, `[[사이드카 이름]]`
    kind: str = "line"  # line | checkbox | extracted | mention
    id: str = ""

    @property
    def key(self) -> str:
        return f"{self.rel}#{self.hash}"


@dataclass
class _Ctx:
    cfg: Config
    client: LlmClient
    today: dt.date
    dry_run: bool
    force: bool
    out: Callable[[str], None]
    ledger: dict
    project_keys: tuple[str, ...]
    people_prefix: str
    overlay: dict[Path, tuple[bytes | None, bytes]] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    stats: dict[str, dict[str, int]] = field(default_factory=dict)
    written: list[str] = field(default_factory=list)
    calls: int = 0

    def now_iso(self) -> str:
        return core.now(self.cfg.tz).isoformat(timespec="seconds")

    def log(self, line: str) -> None:
        if self.dry_run:
            return
        try:
            core.get_logger(self.cfg.root).info(line)
        except Exception:  # 로그 실패가 작업을 막지 않게
            pass

    def problem(self, text: str) -> None:
        self.problems.append(text)
        self.log(f"compile: {text}")

    def count(self, task: str, key: str, n: int = 1) -> None:
        bucket = self.stats.setdefault(task, {})
        bucket[key] = bucket.get(key, 0) + n

    def rel(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.cfg.root.resolve()).as_posix()
        except ValueError:
            return str(path)


# ---------------------------------------------------------------- 원장


def _load_ledger(cfg: Config) -> dict:
    data = core.load_state(cfg.root, LEDGER)
    return data if isinstance(data, dict) else {}


def _units(ledger: dict, task: str) -> dict:
    units = ledger.get(task)
    if not isinstance(units, dict):
        units = ledger[task] = {}
    return units


def _pending(record: Any, hashes: Iterable[str], force: bool) -> bool:
    """이 단위를 (다시) 보내야 하는가. ok는 새 줄이 있을 때만, gave_up은 입력이 바뀌었을 때만."""
    if force or not isinstance(record, Mapping):
        return True
    current = set(hashes)
    status = record.get("status")
    if status == "ok":
        return bool(current - set(record.get("line_hashes") or []))
    if status == "gave_up":
        return sorted(record.get("attempt_hashes") or []) != sorted(current)
    return True


def _ok_record(ctx: _Ctx, profile: str | None, hashes: Iterable[str], **extra: Any) -> dict:
    return {"status": "ok", "profile": profile, "at": ctx.now_iso(), "line_hashes": sorted(set(hashes)), **extra}


def _fail_record(ctx: _Ctx, old: Any, hashes: Iterable[str]) -> dict:
    attempt = sorted(set(hashes))
    old = old if isinstance(old, Mapping) else {}
    same = old.get("status") in ("skipped", "gave_up") and sorted(old.get("attempt_hashes") or []) == attempt
    skips = (int(old.get("skips") or 0) if same else 0) + 1
    return {
        "status": "gave_up" if skips >= GIVE_UP_AFTER else "skipped",
        "profile": None,
        "at": ctx.now_iso(),
        "line_hashes": list(old.get("line_hashes") or []),  # 마지막 성공 때 본 줄(새 줄 판정용)
        "attempt_hashes": attempt,
        "skips": skips,
    }


def _note_failure(ctx: _Ctx, task: str, record: dict, label: str) -> None:
    if record["status"] == "gave_up":
        ctx.count(task, "gave_up")
        ctx.problem(f"{task} {label}: {record['skips']}번 연속 실패해 포기했습니다(입력이 바뀌거나 --force면 다시 시도)")
    else:
        ctx.count(task, "skipped")


# ---------------------------------------------------------------- 파일 읽기·쓰기


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _current(ctx: _Ctx, path: Path) -> bytes | None:
    if ctx.dry_run and path in ctx.overlay:
        return ctx.overlay[path][1]
    return _read(path)


def _template_bytes(ctx: _Ctx, kind: str) -> bytes:
    _rel, body = notes.page_template(kind, "", title=None, today=ctx.today.isoformat())
    return body.encode("utf-8")


class _Missing(core.KbError):
    """고칠 파일이 없음(템플릿도 없음)."""


def _apply_edit(ctx: _Ctx, path: Path, kind: str | None, transform: Callable[[bytes], bytes], current: bytes | None) -> bytes:
    if current is not None:
        base = current
    elif kind:
        base = _template_bytes(ctx, kind)
    else:
        raise _Missing(f"{ctx.rel(path)}: 파일이 없습니다")
    return transform(base)


def _commit(ctx: _Ctx, label: str, fn: Callable[[dict], list[Edit]]) -> bool:
    """원장 갱신 + 파일 편집을 한 번에. 실제 실행은 잠금 안에서 원장과 파일을 다시 읽는다.

    fn(ledger)는 원장을 고치고 편집 목록을 돌려준다. 편집이 하나라도 실패하면 원장은 저장하지 않는다
    (T2 항목은 src 표시로 중복이 막히므로 다음 실행이 안전하게 다시 한다).
    """
    try:
        if ctx.dry_run:
            for path, kind, transform in fn(ctx.ledger):
                current = _current(ctx, path)
                new = _apply_edit(ctx, path, kind, transform, current)
                if new != current:
                    original = ctx.overlay[path][0] if path in ctx.overlay else current
                    ctx.overlay[path] = (original, new)
            return True
        with core.kb_lock(ctx.cfg.root, "compile"):
            ledger = _load_ledger(ctx.cfg)
            planned: list[tuple[Path, bytes, str]] = []
            for path, kind, transform in fn(ledger):
                current = _read(path)
                new = _apply_edit(ctx, path, kind, transform, current)
                if new != current:
                    expect = core.sha256_hex(current) if current is not None else core.MUST_NOT_EXIST
                    planned.append((path, new, expect))
            for path, new, expect in planned:
                core.atomic_write(path, new, expect_sha256=expect)
                rel = ctx.rel(path)
                if rel not in ctx.written:
                    ctx.written.append(rel)
            core.save_state(ctx.cfg.root, LEDGER, ledger)
            ctx.ledger = ledger
        return True
    except (managed.MarkerError, core.ConcurrentEdit, core.FileBusy, UnicodeDecodeError, _Missing) as exc:
        ctx.problem(f"{label}: {exc} → 이번에는 쓰지 않았습니다(다음 실행에서 다시 시도)")
        if not ctx.dry_run:
            ctx.ledger = _load_ledger(ctx.cfg)
        return False


# ---------------------------------------------------------------- 글 다듬기


def _one_line(value: Any, limit: int) -> str:
    """LLM 글을 한 줄로: 주석 표시 제거, 공백 정리, 길이 제한."""
    text = unicodedata.normalize("NFC", str(value or ""))
    text = text.replace("<!--", "").replace("-->", "")
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _src(item: Item) -> str:
    return core.sha256_hex(f"{item.rel}\n{item.hash}".encode("utf-8"))[:16]


def _bare(text: str) -> str:
    """`- 09:12 …` → `09:12 …`(목록 기호만 뗀다)."""
    return text[2:] if _BULLET.match(text) else text


def _fit_newest(items: Sequence[Item], limit: int, render: Callable[[Item], str], reserve: int = 0) -> list[Item]:
    """추정 토큰 limit(글자 수 = limit × 2.5 - reserve) 안에 드는 가장 최근 항목들. 최소 1개."""
    budget = max(limit * 2.5 - reserve, 1)
    chosen: list[Item] = []
    used = 0
    for item in reversed(items):
        size = len(render(item)) + 1
        if chosen and used + size > budget:
            break
        chosen.append(item)
        used += size
    return list(reversed(chosen))


def _chunks(items: Sequence[Item], limit: int, render: Callable[[Item], str]) -> list[list[Item]]:
    """날짜 단위, 넘치면 줄 단위로 나눈다(llm_client.split_by_date와 같은 규칙)."""
    if not items:
        return []
    groups = llm_client.split_by_date([(item.day, render(item)) for item in items], max(int(limit), 1))
    out: list[list[Item]] = []
    pos = 0
    for group in groups:
        out.append(list(items[pos : pos + len(group)]))
        pos += len(group)
    return out


def _numbered(items: Sequence[Item], prefix: str, start: int = 1) -> list[Item]:
    return [dataclasses.replace(item, id=f"{prefix}{n}") for n, item in enumerate(items, start)]


def _attempt_calls(outcome: TaskOutcome) -> int:
    return sum(1 for a in outcome.attempts if a.get("status") not in ("missing", "no_vision"))


def _outcome_note(outcome: TaskOutcome) -> str:
    parts = []
    for attempt in outcome.attempts[-3:]:
        error = attempt.get("error") or ""
        parts.append(f"{attempt.get('profile')}={attempt.get('status')}" + (f"({error[:80]})" if error else ""))
    return ", ".join(parts) or "시도 없음"


# ---------------------------------------------------------------- 원천 읽기


def _daily_files(ctx: _Ctx, day: dt.date | None, since: int | None) -> list[tuple[str, str, bytes]]:
    """(날짜, rel, 바이트). day가 있으면 그날만, 아니면 오늘까지 since일(0이면 전부)."""
    base = ctx.cfg.path("daily")
    if not base.is_dir():
        return []
    span = DEFAULT_SINCE_DAYS if since is None else since
    out: list[tuple[str, str, bytes]] = []
    for path in sorted(base.glob("*.md")):
        if not core.is_scannable(path) or not path.is_file():
            continue
        match = notes.DAILY_NAME.match(path.name)
        if not match:
            continue
        try:
            date = dt.date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if day is not None:
            keep = date == day
        elif span == 0:
            keep = date <= ctx.today
        else:
            keep = ctx.today - dt.timedelta(days=span) < date <= ctx.today
        if not keep:
            continue
        data = path.read_bytes()
        try:
            notes.decode(data)
        except UnicodeDecodeError:
            ctx.problem(f"daily/{path.name}: {notes.NOT_UTF8}")
            continue
        out.append((match.group(1), f"daily/{path.name}", data))
    return out


def _record_items(rel: str, day: str, data: bytes) -> list[Item]:
    """`## 기록`의 줄(코드·관리 구역·제목·빈 줄·`[>]` 이월 줄 제외)."""
    items: list[Item] = []
    for line in notes.record_lines(rel, data):
        if line.in_code or line.region is not None:
            continue
        text = line.text.strip()
        if not text or text.startswith("#") or _CARRIED.match(text):
            continue
        source = f"[[{day}]] {line.time}" if line.time else f"[[{day}]]"
        kind = "checkbox" if _CHECKBOX.match(text) else "line"
        items.append(Item(rel=rel, hash=line.hash, text=_bare(text), day=day, source=source, kind=kind))
    return items


def _sidecar_items(ctx: _Ctx, day: str, records: Sequence[Item]) -> list[Item]:
    """그날 이미지의 사이드카(`inbox/<그림 이름>.md`) `extracted` 구역 줄.

    대상: 그날 `## 기록`에 `![[…]]`로 끼운 그림 + 이름이 `YYYY-MM-DD-`로 시작하는 사이드카(inbox 드롭).
    """
    stems: list[str] = []
    for item in records:
        for target in notes.image_embeds(item.text):
            name = target.replace("\\", "/").rsplit("/", 1)[-1]
            suffix = Path(name).suffix.lower()
            if suffix in IMAGE_SUFFIXES and Path(name).stem not in stems:
                stems.append(Path(name).stem)
    inbox = ctx.cfg.path("inbox")
    paths: list[Path] = [inbox / f"{stem}.md" for stem in stems]
    if inbox.is_dir():
        for path in sorted(inbox.glob(f"{day}-*.md")):
            if path not in paths:
                paths.append(path)
    items: list[Item] = []
    for path in paths:
        if not path.is_file() or not core.is_scannable(path):
            continue
        data = path.read_bytes()
        try:
            notes.decode(data)
            fields = frontmatter.parse(data)
            if path.stem not in stems and not fields.get("source_image"):
                continue  # 사이드카가 아닌 inbox 글
            body = managed.read_region(data, "extracted")
        except (UnicodeDecodeError, managed.MarkerError, core.ConfigError) as exc:
            ctx.problem(f"inbox/{path.name}: 사이드카를 읽지 못했습니다 ({exc})")
            continue
        if not body:
            continue
        rel = f"inbox/{path.name}"
        for raw in notes.split_lines(notes.decode(body)):
            text = raw.strip()
            if text:
                items.append(Item(rel=rel, hash=core.line_hash(raw), text=_bare(text), day=day, source=f"[[inbox/{path.stem}]]", kind="extracted"))
    return items


def _jira_summaries(ctx: _Ctx, texts: Iterable[str]) -> list[str]:
    keys: list[str] = []
    for text in texts:
        for match in notes.find_keys(text, list(ctx.project_keys)):
            if match.key not in keys:
                keys.append(match.key)
    lines: list[str] = []
    for key in keys:
        path = ctx.cfg.path("jira", f"{key}.md")
        data = _read(path)
        if data is None:
            continue
        try:
            fields = frontmatter.parse(data)
        except core.ConfigError:
            continue
        if str(fields.get("stub", "")).lower() == "true" or not fields.get("summary"):
            continue
        extra = " · ".join(str(fields[k]) for k in ("status", "assignee", "due") if fields.get(k))
        lines.append(f"- {key}: {_one_line(fields['summary'], 200)}" + (f" · {extra}" if extra else ""))
    return lines


def _region_text(data: bytes, name: str) -> str | None:
    body = managed.read_region(data, name)
    return None if body is None else notes.decode(body)


# ---------------------------------------------------------------- T1 tag


def _tag_line(item: Item) -> str:
    return f"[{item.id}] {item.day} {item.text}"


def _tag_user(items: Sequence[Item]) -> str:
    tags = ", ".join(fixed_tags())
    lines = [f"고정 태그: {tags}", "", "줄 목록 (줄 id, 날짜, 원문):"]
    lines += [_tag_line(item) for item in items]
    lines += ["", '출력: {"tags": {"<줄 id>": "<태그>"}} — 모든 줄 id에 태그 하나씩.']
    return "\n".join(lines)


def _plan_tag(ctx: _Ctx, files: Sequence[tuple[str, str, bytes]]) -> list[Item]:
    units = ctx.ledger.get("tag") if isinstance(ctx.ledger.get("tag"), dict) else {}
    seen: set[str] = set()
    out: list[Item] = []
    for day, rel, data in files:
        for item in _record_items(rel, day, data):
            if item.key in seen:
                continue
            seen.add(item.key)
            if _pending(units.get(item.key), [item.hash], ctx.force):
                out.append(item)
    return out


def _run_tag(ctx: _Ctx, items: list[Item]) -> None:
    if not items:
        return
    per_batch = max(1, min(TAG_BATCH_LINES, ctx.client.max_output_tokens("tag") // TAG_TOKENS_PER_LINE))
    limit = ctx.client.input_limit("tag")
    batches: list[list[Item]] = []
    for chunk in _chunks(items, limit, lambda it: f"[L00] {it.day} {it.text}"):
        batches += [chunk[i : i + per_batch] for i in range(0, len(chunk), per_batch)]
    for batch in batches:
        numbered = _numbered(batch, "L")
        sent: dict[str, Item] = {}

        def messages_for(limit: int, numbered=numbered, sent=sent) -> list[dict]:
            chosen = _fit_newest(numbered, limit, _tag_line)
            sent.clear()
            sent.update({item.id: item for item in chosen})
            return prompts.build_messages("tag", _tag_user(chosen))

        def validate(data: dict, sent=sent) -> None:
            validate_tag(data)
            _need(any(norm_id(k) in sent for k in data["tags"]), "입력에 있는 줄 id가 하나도 없습니다")

        outcome = ctx.client.run_task("tag", messages_for, validate=validate)
        ctx.calls += _attempt_calls(outcome)
        targets = list(sent.values()) or numbered
        tags: dict[str, str] = {}
        if outcome.status == "ok" and outcome.data:
            for key, value in outcome.data["tags"].items():
                ident = norm_id(key)
                if ident in sent:
                    tags[ident] = _norm_tag(value)

        def fn(ledger: dict, targets=targets, tags=tags, outcome=outcome) -> list[Edit]:
            units = _units(ledger, "tag")
            for item in targets:
                tag = tags.get(item.id)
                if tag:
                    units[item.key] = _ok_record(ctx, outcome.profile, [item.hash], tag=tag)
                else:
                    units[item.key] = _fail_record(ctx, units.get(item.key), [item.hash])
            return []

        if not _commit(ctx, "태그 원장", fn):
            continue
        units = _units(ctx.ledger, "tag")
        for item in targets:
            record = units.get(item.key) or {}
            if record.get("status") == "ok":
                ctx.count("tag", "ok")
                if ctx.dry_run:
                    ctx.out(f"  태그 {item.source} → {record.get('tag')}: {_one_line(item.text, 50)}")
            else:
                _note_failure(ctx, "tag", record, item.source)
        if outcome.status != "ok":
            ctx.log(f"compile tag: 묶음 {len(targets)}줄 건너뜀 ({_outcome_note(outcome)})")


# ---------------------------------------------------------------- T2 decisions


@dataclass
class _DayUnit:
    day: str
    rel: str
    records: list[Item]
    sidecars: list[Item]

    @property
    def items(self) -> list[Item]:
        return self.records + self.sidecars

    @property
    def hashes(self) -> list[str]:
        return [item.key for item in self.items]


def _present_srcs(ctx: _Ctx, rel_parts: tuple[str, ...], region: str) -> set[str]:
    data = _current(ctx, ctx.cfg.path(*rel_parts))
    if data is None:
        return set()
    try:
        text = _region_text(data, region) or ""
    except (managed.MarkerError, UnicodeDecodeError):
        return set()
    return set(_SRC_RE.findall(text))


def _plan_decisions(ctx: _Ctx, files: Sequence[tuple[str, str, bytes]]) -> list[_DayUnit]:
    units = ctx.ledger.get("decisions") if isinstance(ctx.ledger.get("decisions"), dict) else {}
    out: list[_DayUnit] = []
    for day, rel, data in files:
        records = _record_items(rel, day, data)
        sidecars = _sidecar_items(ctx, day, records)
        unit = _DayUnit(day, rel, records, sidecars)
        if unit.items and _pending(units.get(rel), unit.hashes, ctx.force):
            out.append(unit)
    return out


def _decision_line(item: Item, consumed: set[str]) -> str:
    mark = "(이미 반영) " if _src(item) in consumed else ""
    if item.kind == "extracted":
        return f"[{item.id}] {mark}({Path(item.rel).stem}) {item.text}"
    return f"[{item.id}] {mark}{item.text}"


def _decisions_user(unit: _DayUnit, records: Sequence[Item], sidecars: Sequence[Item], jira: Sequence[str], consumed: set[str], keys: Sequence[str]) -> str:
    lines = [f"날짜: {unit.day}", f"프로젝트 키: {', '.join(keys) or '(없음)'}", "", "## 기록 (줄 id)"]
    lines += [_decision_line(item, consumed) for item in records] or ["(없음)"]
    if sidecars:
        lines += ["", "## 이미지 추출문 (줄 id)"]
        lines += [_decision_line(item, consumed) for item in sidecars]
    lines += ["", "## 관련 Jira 일감"]
    lines += list(jira) or ["(없음)"]
    lines += ["", "`(이미 반영)` 줄에서는 결정이나 할 일을 다시 뽑지 않는다."]
    return "\n".join(lines)


def _related(raw: Any, project_keys: Sequence[str], people: set[str], *, with_projects: bool) -> tuple[list[str], int]:
    """related 항목 정리: 설정된 프로젝트의 키 → `[[KEY-1]]`(+ `[[projects/KEY]]`), 입력에 나온 사람 → `@이름`.

    (목록, 버린 키 수). 프로젝트 키 밖의 키는 버리고 센다. 알 수 없는 글은 조용히 버린다.
    """
    keys: list[str] = []
    projects: list[str] = []
    persons: list[str] = []
    rejected = 0
    for value in raw if isinstance(raw, list) else []:
        if not isinstance(value, str):
            continue
        text = unicodedata.normalize("NFC", value).strip()
        if text.startswith("[[") and text.endswith("]]"):
            text = text[2:-2].split("|", 1)[0].split("#", 1)[0].strip()
        for prefix in ("jira/", "projects/", "people/"):
            if text.startswith(prefix):
                text = text[len(prefix):]
        if text.endswith(".md"):
            text = text[:-3]
        match = _KEY_RE.match(text)
        if match:
            if match.group(1) in project_keys:
                if f"[[{text}]]" not in keys:
                    keys.append(f"[[{text}]]")
                if with_projects and f"[[projects/{match.group(1)}]]" not in projects:
                    projects.append(f"[[projects/{match.group(1)}]]")
            else:
                rejected += 1
            continue
        if _PROJECT_RE.match(text):
            if text in project_keys:
                if f"[[projects/{text}]]" not in projects:
                    projects.append(f"[[projects/{text}]]")
            else:
                rejected += 1
            continue
        name = text[1:] if text.startswith("@") else text
        if name in people and f"@{name}" not in persons:
            persons.append(f"@{name}")
    return keys + projects + persons, rejected


def _decision_entry(day: str, item: Item, raw: Mapping, related: list[str]) -> str:
    title = _one_line(raw.get("title"), MAX_TITLE)
    content = _one_line(raw.get("content"), MAX_TEXT) or title
    rationale = _one_line(raw.get("rationale"), MAX_TEXT)
    lines = [f"## {day} {title}", "- 상태: 제안"]
    if related:
        lines.append("- 관련: " + " · ".join(related))
    lines.append(f"- 내용: {content}")
    if rationale:
        lines.append(f"- 근거: {rationale}")
    lines.append(f"- 출처: {item.source} <!-- src:{_src(item)} -->")
    return "\n".join(lines) + "\n\n"


def _action_entry(item: Item, raw: Mapping, related: list[str]) -> str:
    text = _one_line(raw.get("text"), MAX_TEXT)
    parts = [f"- [ ] {text}"]
    if related:
        parts.append("관련 " + " ".join(related))
    parts.append(f"출처 {item.source} <!-- src:{_src(item)} -->")
    return " · ".join(parts) + "\n"


def _append_new(region: str, entries: Sequence[tuple[str, str]]) -> Callable[[bytes], bytes]:
    """src가 구역에 아직 없는 항목만 끝에 붙인다(잠금 안에서 다시 읽은 바이트 기준)."""

    def transform(data: bytes) -> bytes:
        text = _region_text(data, region) or ""
        present = set(_SRC_RE.findall(text))
        new = [entry for src, entry in entries if src not in present]
        return managed.append_to_region(data, region, new) if new else data

    return transform


def _run_decisions(ctx: _Ctx, units: list[_DayUnit]) -> None:
    keys = list(ctx.project_keys)
    for unit in units:
        consumed = _present_srcs(ctx, ("wiki", "decisions.md"), "decisions") | _present_srcs(ctx, ("wiki", "actions.md"), "actions")
        records = _numbered(unit.records, "L")
        sidecars = _numbered(unit.sidecars, "X")
        jira = _jira_summaries(ctx, [item.text for item in unit.items])
        people: set[str] = set()
        for item in unit.items:
            people.update(notes.find_people(item.text, ctx.people_prefix))
        reserve = sum(len(line) + 1 for line in jira) + 200
        render = lambda it: _decision_line(it, consumed)  # noqa: E731
        limit = ctx.client.input_limit("decisions")
        chunks = _chunks(records + sidecars, max(limit - int(reserve / 2.5), 1), render)
        decisions: list[tuple[str, str]] = []
        actions: list[tuple[str, str]] = []
        dropped = rejected = 0
        ok = True
        profile: str | None = None
        for chunk in chunks:
            sent: dict[str, Item] = {}

            def messages_for(limit: int, chunk=chunk, sent=sent) -> list[dict]:
                chosen = _fit_newest(chunk, limit, render, reserve)
                sent.clear()
                sent.update({item.id: item for item in chosen})
                recs = [it for it in chosen if it.kind != "extracted"]
                sides = [it for it in chosen if it.kind == "extracted"]
                return prompts.build_messages("decisions", _decisions_user(unit, recs, sides, jira, consumed, keys))

            outcome = ctx.client.run_task("decisions", messages_for, validate=validate_decisions)
            ctx.calls += _attempt_calls(outcome)
            if outcome.status != "ok" or not outcome.data:
                ok = False
                ctx.log(f"compile decisions {unit.rel}: 건너뜀 ({_outcome_note(outcome)})")
                continue
            profile = outcome.profile
            for raw in outcome.data["decisions"]:
                item = sent.get(norm_id(raw.get("source_line")) or "")
                if item is None:
                    dropped += 1
                    continue
                related, bad = _related(raw.get("related"), keys, people, with_projects=True)
                rejected += bad
                decisions.append((_src(item), _decision_entry(unit.day, item, raw, related)))
            for raw in outcome.data["actions"]:
                item = sent.get(norm_id(raw.get("source_line")) or "")
                if item is None or item.kind == "checkbox":
                    dropped += 1
                    continue
                related, bad = _related(raw.get("related"), keys, people, with_projects=False)
                rejected += bad
                actions.append((_src(item), _action_entry(item, raw, related)))

        def fn(ledger: dict, unit=unit, ok=ok, profile=profile, decisions=decisions, actions=actions) -> list[Edit]:
            units_ = _units(ledger, "decisions")
            if ok:
                units_[unit.rel] = _ok_record(ctx, profile, unit.hashes)
            else:
                units_[unit.rel] = _fail_record(ctx, units_.get(unit.rel), unit.hashes)
            edits: list[Edit] = []
            if decisions:
                edits.append((ctx.cfg.path("wiki", "decisions.md"), "decisions", _append_new("decisions", decisions)))
            if actions:
                edits.append((ctx.cfg.path("wiki", "actions.md"), "actions", _append_new("actions", actions)))
            return edits

        before = (_present_srcs(ctx, ("wiki", "decisions.md"), "decisions"), _present_srcs(ctx, ("wiki", "actions.md"), "actions"))
        if not _commit(ctx, f"결정·할 일 {unit.rel}", fn):
            continue
        ctx.count("decisions", "decisions", sum(1 for src, _ in decisions if src not in before[0]))
        ctx.count("decisions", "actions", sum(1 for src, _ in actions if src not in before[1]))
        if dropped:
            ctx.count("decisions", "dropped", dropped)
        if rejected:
            ctx.count("decisions", "rejected_keys", rejected)
        record = _units(ctx.ledger, "decisions").get(unit.rel) or {}
        if record.get("status") == "ok":
            ctx.count("decisions", "ok")
        else:
            _note_failure(ctx, "decisions", record, unit.rel)


# ---------------------------------------------------------------- T3 entity_summary


@dataclass
class _Entity:
    path: Path
    rel: str
    kind: str
    name: str
    summary: str  # 현재 요약 본문(출처·갱신 줄 뺀 것)
    prev_sources: list[str]
    issues: str | None
    new: list[Item]
    hashes: list[str]


def _entity_name(kind: str, stem: str, fields: Mapping) -> str:
    if kind == "project":
        title = str(fields.get("title") or "").strip()
        return f"{stem} — {title}" if title and title != stem else stem
    if kind == "topic":
        aliases = fields.get("aliases")
        names = [a for a in aliases if isinstance(a, str) and a.strip()] if isinstance(aliases, list) else []
        base = str(fields.get("name") or stem)
        return f"{base} (별칭: {', '.join(names)})" if names else base
    return str(fields.get("name") or stem)


def _split_summary(text: str | None) -> tuple[str, list[str]]:
    """summary 구역 → (요약 본문, 이전 출처 목록)."""
    body: list[str] = []
    sources: list[str] = []
    for line in notes.split_lines(text or ""):
        match = _META_LINE.match(line)
        if match:
            if match.group(1) == "출처":
                sources += [s.strip() for s in line[len("- 출처: "):].split(" · ") if s.strip()]
            continue
        body.append(line)
    return "\n".join(body).strip(), sources


def _plan_entities(ctx: _Ctx) -> list[_Entity]:
    units = ctx.ledger.get("entity_summary") if isinstance(ctx.ledger.get("entity_summary"), dict) else {}
    out: list[_Entity] = []
    for sub, kind in ENTITY_DIRS:
        base = ctx.cfg.path("wiki", sub)
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.md")):
            if not core.is_scannable(path) or not path.is_file():
                continue
            rel = f"wiki/{sub}/{path.name}"
            data = _current(ctx, path) or b""
            try:
                notes.decode(data)
                mentions = _region_text(data, "mentions")
                summary = _region_text(data, "summary")
                issues = _region_text(data, "issues") if kind == "project" else None
            except (UnicodeDecodeError, managed.MarkerError) as exc:
                ctx.problem(f"{rel}: 읽지 못했습니다 ({exc})")
                continue
            lines = [line for line in notes.split_lines(mentions or "") if line.startswith("- ")]
            if not lines:
                continue
            hashes = [core.line_hash(line) for line in lines]
            record = units.get(rel)
            if not _pending(record, hashes, ctx.force):
                continue
            seen = set() if ctx.force or not isinstance(record, Mapping) else set(record.get("line_hashes") or [])
            new: list[Item] = []
            for line, digest in zip(lines, hashes):
                if digest in seen:
                    continue
                match = _MENTION_SOURCE.match(line)
                source = (match.group(1) + (f" {match.group(2)}" if match.group(2) else "")) if match else ""
                day = match.group(1)[2:12] if match and re.match(r"\[\[\d{4}-\d{2}-\d{2}", match.group(1)) else ""
                new.append(Item(rel=rel, hash=digest, text=_bare(line), day=day, source=source, kind="mention"))
            if not new:
                continue
            try:
                fields = frontmatter.parse(data)
            except core.ConfigError:
                fields = {}
            text, prev = _split_summary(summary)
            out.append(_Entity(path, rel, kind, _entity_name(kind, path.stem, fields), text, prev, issues, new, hashes))
    return out


def _entity_user(entity: _Entity, items: Sequence[Item]) -> str:
    lines = [f"대상: {ENTITY_LABEL[entity.kind]} {entity.name}", "", "## 현재 요약"]
    lines.append(f"[S1] {' '.join(entity.summary.split())}" if entity.summary else "(없음)")
    lines += ["", "## 새 언급 (줄 id)"]
    lines += [f"[{item.id}] {item.text}" for item in items]
    if entity.kind == "project":
        lines += ["", "## 미완료 일감 표"]
        table = (entity.issues or "").strip()
        lines.append(table[:MAX_ISSUES_CHARS] if table else "(없음)")
    if entity.kind == "person":
        lines += ["", "사람 페이지다. 업무 맥락만 쓰고 개인 정보는 쓰지 않는다."]
    return "\n".join(lines)


def _summary_body(text: str, sources: list[str], day: str, profile: str | None) -> str:
    lines = [text, ""]
    if sources:
        lines.append("- 출처: " + " · ".join(sources))
    lines.append(f"- 갱신: {day}" + (f" ({profile})" if profile else ""))
    return "\n".join(lines)


def _run_entities(ctx: _Ctx, entities: list[_Entity]) -> None:
    for entity in entities:
        numbered = _numbered(entity.new, "M")
        reserve = len(entity.summary) + (min(len(entity.issues or ""), MAX_ISSUES_CHARS) if entity.kind == "project" else 0) + 200
        render = lambda it: f"[{it.id}] {it.text}"  # noqa: E731
        sent: dict[str, Item] = {}
        table_keys = {m.key for m in notes.find_keys(entity.issues or "", list(ctx.project_keys))}

        def messages_for(limit: int, numbered=numbered, sent=sent, entity=entity, reserve=reserve) -> list[dict]:
            chosen = _fit_newest(numbered, limit, render, reserve)
            sent.clear()
            sent.update({item.id: item for item in chosen})
            return prompts.build_messages("entity_summary", _entity_user(entity, chosen))

        def validate(data: dict, sent=sent, entity=entity, table_keys=table_keys) -> None:
            validate_entity_summary(data)
            keys = set(table_keys)
            for item in sent.values():
                keys.update(m.key for m in notes.find_keys(item.text, list(ctx.project_keys)))
            for source in data["sources"]:
                ident = norm_id(source)
                text = source.strip().strip("[]")
                ok = ident in sent or (ident == "S1" and bool(entity.summary)) or text in keys
                _need(ok, f"sources: 입력에 없는 출처입니다 ({source!r})")

        outcome = ctx.client.run_task("entity_summary", messages_for, validate=validate)
        ctx.calls += _attempt_calls(outcome)
        body: str | None = None
        if outcome.status == "ok" and outcome.data:
            sources: list[str] = []
            for source in outcome.data["sources"]:
                ident = norm_id(source)
                if ident in sent:
                    rendered = [sent[ident].source] if sent[ident].source else []
                elif ident == "S1":
                    rendered = entity.prev_sources
                else:
                    rendered = [f"[[{source.strip().strip('[]')}]]"]
                for value in rendered:
                    if value and value not in sources:
                        sources.append(value)
            body = _summary_body(_one_line(outcome.data["summary"], MAX_SUMMARY), sources, ctx.today.isoformat(), outcome.profile)
        else:
            ctx.log(f"compile entity_summary {entity.rel}: 건너뜀 ({_outcome_note(outcome)})")

        def fn(ledger: dict, entity=entity, body=body, outcome=outcome) -> list[Edit]:
            units = _units(ledger, "entity_summary")
            if body is None:
                units[entity.rel] = _fail_record(ctx, units.get(entity.rel), entity.hashes)
                return []
            units[entity.rel] = _ok_record(ctx, outcome.profile, entity.hashes)
            return [(entity.path, None, lambda data, body=body: managed.replace_region(data, "summary", body))]

        if not _commit(ctx, f"요약 {entity.rel}", fn):
            continue
        record = _units(ctx.ledger, "entity_summary").get(entity.rel) or {}
        if record.get("status") == "ok":
            ctx.count("entity_summary", "ok")
        else:
            _note_failure(ctx, "entity_summary", record, entity.rel)


# ---------------------------------------------------------------- T4 topic_proposal


def topic_name(value: Any) -> str:
    """제안 이름 → 파일·위키링크에 쓸 수 있는 주제 이름(금지 문자 제거, 40자)."""
    text = unicodedata.normalize("NFC", str(value or ""))
    text = _TOPIC_BAD.sub(" ", text)
    text = " ".join(text.split()).strip(" .")
    return text[:MAX_TOPIC_NAME].rstrip(" .")


def _name_key(name: str) -> str:
    return topic_name(name).casefold()


def _topic_index(cfg: Config, ctx: _Ctx | None = None) -> dict[str, tuple[str, Path, list[str]]]:
    """이름·별칭(casefold) → (주제 이름, 경로, 별칭 목록)."""
    out: dict[str, tuple[str, Path, list[str]]] = {}
    base = cfg.path("wiki", "topics")
    if not base.is_dir():
        return out
    for path in sorted(base.glob("*.md")):
        if not core.is_scannable(path) or not path.is_file():
            continue
        data = (_current(ctx, path) if ctx is not None else _read(path)) or b""
        try:
            fields = frontmatter.parse(data)
        except core.ConfigError:
            fields = {}
        name = str(fields.get("name") or path.stem)
        raw = fields.get("aliases")
        aliases = [a for a in raw if isinstance(a, str) and a.strip()] if isinstance(raw, list) else []
        entry = (name, path, aliases)
        for key in (name, path.stem, *aliases):
            out.setdefault(_name_key(key), entry)
    return out


@dataclass
class _ProposalUnit:
    end: str
    items: list[Item]
    hashes: list[str]


def _proposal_state(ledger: dict) -> dict:
    state = ledger.get(PROPOSALS_KEY)
    if not isinstance(state, dict):
        state = ledger[PROPOSALS_KEY] = {}
    if not isinstance(state.get("items"), dict):
        state["items"] = {}
    if not isinstance(state.get("next_id"), int) or state["next_id"] < 1:
        existing = [int(k) for k in state["items"] if str(k).isdigit()]
        state["next_id"] = max(existing, default=0) + 1
    return state


def _last_ok(units: Mapping) -> dt.datetime | None:
    best: dt.datetime | None = None
    for record in units.values():
        if not isinstance(record, Mapping) or record.get("status") != "ok":
            continue
        try:
            when = dt.datetime.fromisoformat(str(record.get("at")))
        except ValueError:
            continue
        if best is None or when > best:
            best = when
    return best


def _plan_proposals(ctx: _Ctx, end: dt.date, explicit: bool) -> _ProposalUnit | None:
    units = ctx.ledger.get("topic_proposal") if isinstance(ctx.ledger.get("topic_proposal"), dict) else {}
    if not explicit and not ctx.force:
        last = _last_ok(units)
        if last is not None:
            now = core.now(ctx.cfg.tz)
            if last.tzinfo is None:
                last = last.replace(tzinfo=now.tzinfo)
            if now - last < dt.timedelta(days=T4_EVERY_DAYS):
                return None
    start = end - dt.timedelta(days=T4_WINDOW_DAYS - 1)
    items: list[Item] = []
    for day, rel, data in _daily_files(ctx, None, 0):
        if start.isoformat() <= day <= end.isoformat():
            items += _record_items(rel, day, data)
    if not items:
        return None
    hashes = [item.key for item in items]
    if not _pending(units.get(end.isoformat()), hashes, ctx.force):
        return None
    return _ProposalUnit(end.isoformat(), _numbered(items, "L"), hashes)


def _proposal_user(unit: _ProposalUnit, items: Sequence[Item], topics: Mapping[str, tuple[str, Path, list[str]]]) -> str:
    start = (dt.date.fromisoformat(unit.end) - dt.timedelta(days=T4_WINDOW_DAYS - 1)).isoformat()
    names: list[str] = []
    for name, _path, aliases in topics.values():
        label = f"{name} (별칭: {', '.join(aliases)})" if aliases else name
        if label not in names:
            names.append(label)
    lines = [f"기간: {start} ~ {unit.end}", "", "## 기존 주제"]
    lines += [f"- {name}" for name in names] or ["(없음)"]
    lines += ["", "## 기록 (줄 id, 날짜, 원문)"]
    lines += [f"[{item.id}] {item.day} {item.text}" for item in items]
    return "\n".join(lines)


def _render_proposals(state: Mapping) -> str:
    items = state.get("items") or {}
    pending = sorted(
        ((k, v) for k, v in items.items() if isinstance(v, Mapping) and v.get("status") == PROPOSAL_PENDING),
        key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0,
    )
    lines: list[str] = []
    for ident, item in pending:
        lines.append(f"- [{ident}] {item.get('name')} · {PROPOSAL_PENDING} · {str(item.get('created') or '')[:10]}")
        if item.get("why"):
            lines.append(f"  - 이유: {item['why']}")
        if item.get("sources"):
            lines.append("  - 예: " + " · ".join(item["sources"]))
        if item.get("merge_with"):
            lines.append(f"  - 합치기: [[topics/{core.safe_name(item['merge_with'])}|{item['merge_with']}]]")
    if not pending:
        lines.append("(대기 중인 제안 없음)")
    done = sum(1 for v in items.values() if isinstance(v, Mapping) and v.get("status") != PROPOSAL_PENDING)
    if done:
        lines += ["", f"처리됨 {done}건"]
    return "\n".join(lines)


def _intake(state: dict, proposals: Sequence[Mapping], topics: Mapping[str, tuple[str, Path, list[str]]], now: str) -> int:
    """새 제안을 상태에 더한다. 같은 이름은 같은 번호(대기 중이면 내용만 갱신). 더한 수."""
    items: dict = state["items"]
    by_name = {_name_key(str(v.get("name"))): k for k, v in items.items() if isinstance(v, Mapping)}
    added = 0
    for proposal in proposals:
        name = proposal["name"]
        key = _name_key(name)
        if not key or key in topics:
            continue
        if key in by_name:
            item = items[by_name[key]]
            if item.get("status") == PROPOSAL_PENDING:
                item.update(why=proposal["why"], sources=proposal["sources"], merge_with=proposal["merge_with"], updated=now)
            continue
        ident = str(state["next_id"])
        state["next_id"] += 1
        items[ident] = {
            "name": name,
            "why": proposal["why"],
            "sources": proposal["sources"],
            "merge_with": proposal["merge_with"],
            "status": PROPOSAL_PENDING,
            "created": now,
            "updated": now,
        }
        by_name[key] = ident
        added += 1
    return added


def _run_proposals(ctx: _Ctx, unit: _ProposalUnit) -> None:
    topics = _topic_index(ctx.cfg, ctx)
    limit = ctx.client.input_limit("topic_proposal")
    render = lambda it: f"[{it.id}] {it.day} {it.text}"  # noqa: E731
    reserve = sum(len(name) + 20 for name, _p, _a in topics.values()) + 200
    merged: dict[str, dict] = {}
    ok = True
    profile: str | None = None
    for chunk in _chunks(unit.items, max(limit - int(reserve / 2.5), 1), render):
        sent: dict[str, Item] = {}

        def messages_for(limit: int, chunk=chunk, sent=sent) -> list[dict]:
            chosen = _fit_newest(chunk, limit, render, reserve)
            sent.clear()
            sent.update({item.id: item for item in chosen})
            return prompts.build_messages("topic_proposal", _proposal_user(unit, chosen, topics))

        def validate(data: dict, sent=sent) -> None:
            validate_topic_proposal(data)
            for i, proposal in enumerate(data["proposals"]):
                _need(bool(topic_name(proposal["name"])), f"proposals[{i}].name: 쓸 수 있는 글자가 없습니다")
                for source in proposal["sample_sources"]:
                    _need(norm_id(source) in sent, f"proposals[{i}].sample_sources: 입력에 없는 줄 id ({source!r})")
                merge = (proposal.get("merge_with") or "").strip()
                _need(not merge or _name_key(merge) in topics, f"proposals[{i}].merge_with: 기존 주제가 아닙니다 ({merge!r})")

        outcome = ctx.client.run_task("topic_proposal", messages_for, validate=validate)
        ctx.calls += _attempt_calls(outcome)
        if outcome.status != "ok" or not outcome.data:
            ok = False
            ctx.log(f"compile topic_proposal {unit.end}: 묶음 건너뜀 ({_outcome_note(outcome)})")
            continue
        profile = outcome.profile
        for proposal in outcome.data["proposals"]:
            name = topic_name(proposal["name"])
            merge = (proposal.get("merge_with") or "").strip()
            sources = [sent[norm_id(s)].source for s in proposal["sample_sources"] if norm_id(s) in sent]
            entry = merged.setdefault(
                _name_key(name),
                {"name": name, "why": _one_line(proposal.get("why"), MAX_TEXT), "sources": [], "merge_with": topics[_name_key(merge)][0] if merge else None},
            )
            for source in sources:
                if source and source not in entry["sources"]:
                    entry["sources"].append(source)

    proposals = list(merged.values())
    added_box: list[int] = [0]

    def fn(ledger: dict) -> list[Edit]:
        units = _units(ledger, "topic_proposal")
        units[unit.end] = _ok_record(ctx, profile, unit.hashes) if ok else _fail_record(ctx, units.get(unit.end), unit.hashes)
        if not proposals:
            return []
        state = _proposal_state(ledger)
        added_box[0] = _intake(state, proposals, topics, ctx.now_iso())
        body = _render_proposals(state)
        return [(ctx.cfg.path("wiki", "_proposals.md"), "proposals", lambda data: managed.replace_region(data, "proposals", body))]

    if not _commit(ctx, "주제 제안", fn):
        return
    ctx.count("topic_proposal", "proposals", added_box[0])
    record = _units(ctx.ledger, "topic_proposal").get(unit.end) or {}
    if record.get("status") == "ok":
        ctx.count("topic_proposal", "ok")
    else:
        _note_failure(ctx, "topic_proposal", record, unit.end)


# ---------------------------------------------------------------- 실행


def _scaled_cfg(cfg: Config) -> Config:
    """T1 출력 상한을 '줄당 × 묶음 크기'로 바꾼 설정(이 모듈이 만드는 클라이언트용)."""
    data = copy.deepcopy(dict(cfg.data))
    llm = data.get("llm")
    tasks = llm.get("tasks") if isinstance(llm, dict) else None
    tag = tasks.get("tag") if isinstance(tasks, dict) else None
    if not isinstance(tag, dict):
        return cfg
    per_line = llm_client._positive_int(tag.get("max_output_tokens")) or llm_client.DEFAULT_MAX_OUTPUT["tag"]
    tag["max_output_tokens"] = min(per_line * TAG_BATCH_LINES, llm_client.OUTPUT_CAP)
    return dataclasses.replace(cfg, data=data)


def _as_date(value: dt.date | str | None) -> dt.date | None:
    if value is None or isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        raise core.ConfigError(f"날짜 형식이 아닙니다: {value!r} (YYYY-MM-DD)") from None


def _diff(ctx: _Ctx) -> str:
    chunks: list[str] = []
    for path in sorted(ctx.overlay, key=lambda p: ctx.rel(p)):
        old, new = ctx.overlay[path]
        rel = ctx.rel(path)
        before = (old or b"").decode("utf-8", errors="replace").splitlines(keepends=True)
        after = new.decode("utf-8", errors="replace").splitlines(keepends=True)
        diff = difflib.unified_diff(before, after, fromfile=f"a/{rel}" if old is not None else "/dev/null", tofile=f"b/{rel}")
        text = "".join(line if line.endswith("\n") else line + "\n" for line in diff)
        if text:
            chunks.append(text)
    return "".join(chunks)


_STAT_LABELS = (
    ("tag", "ok", "태그 {}줄"),
    ("decisions", "decisions", "결정 {}건"),
    ("decisions", "actions", "할 일 {}건"),
    ("entity_summary", "ok", "요약 {}쪽"),
    ("topic_proposal", "proposals", "새 제안 {}건"),
)


def _summary_line(ctx: _Ctx) -> str:
    parts = [label.format(ctx.stats.get(task, {}).get(key, 0)) for task, key, label in _STAT_LABELS]
    skipped = sum(bucket.get("skipped", 0) for bucket in ctx.stats.values())
    gave_up = sum(bucket.get("gave_up", 0) for bucket in ctx.stats.values())
    dropped = ctx.stats.get("decisions", {}).get("dropped", 0)
    rejected = ctx.stats.get("decisions", {}).get("rejected_keys", 0)
    if skipped:
        parts.append(f"건너뜀 {skipped}")
    if gave_up:
        parts.append(f"포기 {gave_up}")
    if dropped:
        parts.append(f"출처 없는 출력 버림 {dropped}")
    if rejected:
        parts.append(f"범위 밖 키 뺌 {rejected}")
    parts.append(f"LLM 호출 {ctx.calls}회")
    return "compile: " + " · ".join(parts)


def run(
    cfg: Config,
    *,
    tasks: Sequence[str] | None = None,
    day: dt.date | str | None = None,
    since: int | None = None,
    dry_run: bool = False,
    force: bool = False,
    client: LlmClient | None = None,
    out: Callable[[str], None] | None = None,
) -> dict:
    """compile 한 번. 반환: {status, stats, written, problems, calls, diff}.

    status: disabled | no_profile | nothing | ok. 회사 환경 공개 호스트면 PublicHostRefused(종료 5).
    tasks가 None이면 T1~T3 + (기한이 된) T4. `topic_proposal`을 직접 넣으면 기한과 상관없이 돈다.
    """
    emit = out or print
    explicit = tasks is not None
    chosen = list(dict.fromkeys(tasks)) if tasks else list(TASKS)
    unknown = [t for t in chosen if t not in TASKS]
    if unknown:
        raise core.ConfigError(f"알 수 없는 작업: {', '.join(unknown)} (가능: {', '.join(TASKS)})")
    if since is not None and (isinstance(since, bool) or not isinstance(since, int) or since < 0):
        raise core.ConfigError(f"--since는 0 이상의 정수여야 합니다: {since!r}")
    the_day = _as_date(day)
    if client is None:
        client = LlmClient(_scaled_cfg(cfg))  # 회사 차단(D44): enabled 검사보다 먼저
    if dry_run:
        client._log = lambda line: None  # type: ignore[method-assign]  # dry-run은 로그도 쓰지 않는다
    today = core.now(cfg.tz).date()
    ctx = _Ctx(
        cfg=cfg,
        client=client,
        today=today,
        dry_run=dry_run,
        force=force,
        out=emit,
        ledger=_load_ledger(cfg),
        project_keys=tuple(cfg.project_keys),
        people_prefix=str(cfg.get("people_prefix") or "@"),
    )
    result: dict[str, Any] = {"status": "ok", "stats": ctx.stats, "written": ctx.written, "problems": ctx.problems, "calls": 0, "diff": ""}

    if not client.enabled:
        line = "compile: LLM이 꺼져 있어 건너뜁니다 (llm.enabled: false)"
        ctx.log(line)
        emit(line)
        result["status"] = "disabled"
        return result

    files = _daily_files(ctx, the_day, since) if {"tag", "decisions"} & set(chosen) else []
    tag_items = _plan_tag(ctx, files) if "tag" in chosen else []
    day_units = _plan_decisions(ctx, files) if "decisions" in chosen else []
    entities = _plan_entities(ctx) if "entity_summary" in chosen else []
    proposal_unit = _plan_proposals(ctx, the_day or today, explicit) if "topic_proposal" in chosen else None
    if not (tag_items or day_units or entities or proposal_unit):
        line = "compile: 새로 처리할 것이 없습니다"
        emit(line)
        result["status"] = "nothing"
        return result

    connected = client.connected_profiles()
    if not connected:
        detail = "; ".join(f"{k}: {v}" for k, v in client.last_probe.items()) or "프로필 없음"
        line = f"compile: 연결되는 LLM 프로필이 없어 건너뜁니다 ({detail})"
        ctx.log(line)
        emit(line)
        result["status"] = "no_profile"
        return result

    if dry_run:
        emit("compile --dry-run: LLM은 부르지만 아무것도 쓰지 않습니다")
    _run_tag(ctx, tag_items)
    _run_decisions(ctx, day_units)
    _run_entities(ctx, entities)
    if proposal_unit is not None:
        _run_proposals(ctx, proposal_unit)

    summary = _summary_line(ctx)
    ctx.log(summary)
    emit(summary)
    for problem in ctx.problems:
        emit(f"  ! {problem}")
    result["calls"] = ctx.calls
    if dry_run:
        diff = _diff(ctx)
        result["diff"] = diff
        emit(diff.rstrip("\n") if diff else "(파일 변경 없음)")
    return result


def main(argv: list[str]) -> int:
    parser = core.make_parser("kb.py compile", "B단계 편찬: 태그·결정·할 일·엔티티 요약·주제 제안 (LLM)")
    parser.add_argument("--date", help="그날 daily만 (YYYY-MM-DD). T4는 그날까지 2주")
    parser.add_argument("--since", type=int, default=None, help=f"오늘까지 N일의 daily (기본 {DEFAULT_SINCE_DAYS}, 0이면 전부)")
    parser.add_argument("--task", choices=TASKS, help="이 작업만 (topic_proposal은 기한과 상관없이 실행)")
    parser.add_argument("--dry-run", action="store_true", help="LLM은 부르되 쓰지 않고 바뀔 내용을 diff로 보여 줌")
    parser.add_argument("--force", action="store_true", help="원장을 무시하고 다시 보냄(결정·할 일은 그래도 중복되지 않음)")
    args = parser.parse_args(argv)
    if args.date and args.since is not None:
        raise core.ConfigError("--date와 --since는 함께 쓸 수 없습니다")
    cfg = config.load(args.root)
    run(
        cfg,
        tasks=[args.task] if args.task else None,
        day=_as_date(args.date) if args.date else None,
        since=args.since,
        dry_run=args.dry_run,
        force=args.force,
    )
    return 0


# ---------------------------------------------------------------- proposals list | accept


def pending_proposals(cfg: Config) -> list[tuple[str, dict]]:
    """대기 중(제안) 항목 (번호, 항목) — 번호 순."""
    state = _proposal_state(_load_ledger(cfg))
    items = [(k, v) for k, v in state["items"].items() if isinstance(v, dict) and v.get("status") == PROPOSAL_PENDING]
    return sorted(items, key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0)


def _add_alias(path: Path, name: str) -> bool:
    data = path.read_bytes()
    fields = frontmatter.parse(data)
    raw = fields.get("aliases")
    aliases = [a for a in raw if isinstance(a, str) and a.strip()] if isinstance(raw, list) else ([raw] if isinstance(raw, str) and raw.strip() else [])
    if any(_name_key(a) == _name_key(name) for a in aliases):
        return False
    new = frontmatter.set_fields(data, {"aliases": [*aliases, name]})
    core.atomic_write(path, new, expect_sha256=core.sha256_hex(data))
    return True


def accept(cfg: Config, which: str, *, out: Callable[[str], None] | None = None) -> list[str]:
    """제안 승인. which: 번호 또는 'all'. 만든·고친 주제 페이지 rel 목록을 돌려준다(D22)."""
    emit = out or print
    core.refuse_if_agent("proposals accept")
    which = str(which).strip().lstrip("#")
    done: list[str] = []
    with core.kb_lock(cfg.root, "proposals"):
        ledger = _load_ledger(cfg)
        state = _proposal_state(ledger)
        items: dict = state["items"]
        pending = [k for k, v in items.items() if isinstance(v, dict) and v.get("status") == PROPOSAL_PENDING]
        if which.lower() == "all":
            targets = sorted(pending, key=lambda k: int(k) if k.isdigit() else 0)
            if not targets:
                emit("승인할 제안이 없습니다")
                return done
        elif which in items:
            if which not in pending:
                raise core.ConfigError(f"제안 [{which}]은(는) 이미 처리되었습니다 ({items[which].get('status')})")
            targets = [which]
        else:
            raise core.ConfigError(f"제안 번호가 없습니다: {which!r} → kb.py proposals list")
        now = core.now(cfg.tz).isoformat(timespec="seconds")
        for ident in targets:
            item = items[ident]
            name = topic_name(item.get("name"))
            merge = item.get("merge_with")
            if merge:
                topics = _topic_index(cfg)
                hit = topics.get(_name_key(merge))
                path = hit[1] if hit else notes.ensure_page(cfg, "topic", topic_name(merge))
                added = _add_alias(path, name)
                emit(f"[{ident}] {name} → 주제 '{merge}'의 별칭" + ("으로 더했습니다" if added else "에 이미 있습니다"))
            else:
                path = notes.ensure_page(cfg, "topic", name)
                emit(f"[{ident}] 주제 페이지: {path.relative_to(cfg.root).as_posix()}")
            rel = path.relative_to(cfg.root).as_posix()
            item.update(status=PROPOSAL_ACCEPTED, accepted_at=now, page=rel)
            done.append(rel)
        core.save_state(cfg.root, LEDGER, ledger)
        page = cfg.path("wiki", "_proposals.md")
        current = _read(page)
        _rel, template = notes.page_template("proposals", "", title=None, today=now[:10])
        base = current if current is not None else template.encode("utf-8")
        try:
            new = managed.replace_region(base, "proposals", _render_proposals(state))
            if new != current:
                core.atomic_write(page, new, expect_sha256=core.sha256_hex(current) if current is not None else core.MUST_NOT_EXIST)
        except (managed.MarkerError, core.ConcurrentEdit, core.FileBusy) as exc:
            emit(f"  ! wiki/_proposals.md를 고치지 못했습니다 ({exc}). 다음 compile이 다시 그립니다")
    return done


def proposals_main(argv: list[str]) -> int:
    parser = core.make_parser("kb.py proposals", "주제 제안: list | accept <번호|all>")
    parser.add_argument("action", choices=("list", "accept"))
    parser.add_argument("which", nargs="?", help="accept할 번호 또는 all")
    args = parser.parse_intermixed_args(argv)
    if args.action == "accept":
        core.refuse_if_agent("proposals accept")
        if not args.which:
            raise core.ConfigError("kb.py proposals accept <번호|all>")
        cfg = config.load(args.root)
        accept(cfg, args.which)
        return 0
    if args.which:
        raise core.ConfigError("kb.py proposals list는 인수를 받지 않습니다")
    cfg = config.load(args.root)
    pending = pending_proposals(cfg)
    if not pending:
        print("대기 중인 주제 제안이 없습니다")
        return 0
    for ident, item in pending:
        extra = f" (합치기: {item['merge_with']})" if item.get("merge_with") else ""
        print(f"[{ident}] {item.get('name')}{extra} — {item.get('why') or ''}".rstrip(" —"))
    print("승인: python scripts/kb.py proposals accept <번호|all>")
    return 0


if __name__ == "__main__":
    core.run_main(main)
