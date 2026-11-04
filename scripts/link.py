"""A단계 연결 (`kb.py link`). LLM 없이 daily·inbox의 Jira 키와 `@이름`을 엔티티 페이지에 잇는다.

- 원천: `daily/*.md`, `inbox/*.md`(이미지 사이드카 포함). 코드 블록·인라인 코드·URL·front matter와
  관리 구역은 건너뛴다. 단 `extracted` 구역(이미지 추출 텍스트)은 매칭한다(정규화는 하지 않음).
- 대상: `jira/KEY.md`(없으면 `stub: true` 최소 페이지), `wiki/projects/<프로젝트>.md`,
  `wiki/people/<이름>.md`의 `mentions` 구역. `wiki/index.md`의 `index` 구역.
- 각 구역은 모든 원천의 순수 함수다(D20). 원장 `.state/links.json`({원천: {줄 해시: [대상]}})과
  `.state/links_cache.json`(파일 sha256별 추출 결과)은 다시 만들 수 있는 캐시일 뿐이고,
  구역 내용은 캐시 이력에 기대지 않는다. 크기·수정 시각이 캐시와 같은 원천은 열지 않고(웜 실행 예산),
  다르면 읽어서 sha256으로 확인한다. 의심스러우면 `--rebuild`.
- `normalize_keys`면 daily `## 기록`의 맨 글자 키를 `[[KEY-1]]`로 바꾼다. link가 daily를 고치는
  유일한 경우이며 expect_sha256으로 써서 사람이 고친 파일은 건너뛰고 알린다.
"""
from __future__ import annotations

import json
import re
import stat
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from lib import config, core, frontmatter, managed, notes
from lib.config import Config

CACHE_VERSION = 1
LEDGER = "links"  # .state/links.json
CACHE = "links_cache"  # .state/links_cache.json
MAX_TEXT = 120
MANAGED_DIRS = (("jira",), ("wiki", "projects"), ("wiki", "people"))
STUB_KEY = re.compile(r"^([A-Z][A-Z0-9_]+)-(\d+)$")

_WIKILINK_SPAN = re.compile(r"!?\[\[(?:(?!\]\]).)*\]\]")
_BULLET = re.compile(r"^(?:[-*+]|>|#{1,6})\s+")
_CHECKBOX = re.compile(r"^\[[ xX>]\]\s+")
_INBOX_STAMP = re.compile(r"^(\d{4})-?(\d{2})-?(\d{2})(?:-(\d{2})(\d{2}))?")
_ISO_DAY = re.compile(r"^(\d{4}-\d{2}-\d{2})")
# 정규화하지 않는 앞뒤 문자: 경로·확장자·태그·이메일·대괄호 조각
_NORM_BAD_BEFORE = set("[/\\.#@=&?%~$")
_NORM_JOINERS = set("-./\\")


# ---------------------------------------------------------------- 자료형


@dataclass(frozen=True)
class Entry:
    """원천 줄 하나가 한 대상 페이지에 남기는 항목."""

    day: str  # 정렬용 날짜(없으면 "")
    time: str  # 정렬용 시각(없으면 앞 줄의 시각, 그것도 없으면 "")
    rel: str
    lineno: int
    hash: str
    text: str  # 렌더된 한 줄 `- [[YYYY-MM-DD]] HH:MM 원문`
    targets: tuple[str, ...]

    def sort_key(self) -> tuple[str, str, str, int]:
        return (self.day, self.time, self.rel, self.lineno)

    def to_json(self) -> list[Any]:
        return [self.day, self.time, self.lineno, self.hash, self.text, list(self.targets)]

    @classmethod
    def from_json(cls, rel: str, raw: list[Any]) -> "Entry":
        day, time_, lineno, hash_, text, targets = raw
        return cls(str(day), str(time_), rel, int(lineno), str(hash_), str(text), tuple(str(t) for t in targets))


@dataclass
class Source:
    rel: str
    path: Path
    data: bytes | None  # 읽은 바이트(크기·수정 시각이 캐시와 같아 읽지 않았으면 None)
    sha256: str
    stat: list[int]  # [크기, 수정 시각 ns] — 읽기 전에 잰 값
    new_data: bytes | None = None  # 정규화 결과(바뀔 때만)
    normalized: int = 0
    entries: list[Entry] = field(default_factory=list)
    cacheable: bool = True
    from_cache: bool = False


@dataclass
class PageWrite:
    rel: str
    path: Path
    new: bytes
    expect: str | None  # None이면 새 파일
    kind: str  # jira·project·person·index
    body_sha: str  # 구역 본문의 해시(페이지 캐시용)


@dataclass
class Plan:
    sources: list[Source]
    pages: list[PageWrite]
    ledger: dict[str, dict[str, list[str]]]
    ledger_changed: bool
    cache: dict[str, Any]
    cache_changed: bool
    mentions: int
    skipped: list[tuple[str, str]]
    entries_by_page: dict[str, list[Entry]]


# ---------------------------------------------------------------- 설정 맥락


@dataclass(frozen=True)
class _Ctx:
    cfg: Config
    project_keys: tuple[str, ...]
    key_re: re.Pattern[str]
    prefix: str
    normalize: bool
    today: str

    @property
    def fingerprint(self) -> str:
        raw = json.dumps([CACHE_VERSION, MAX_TEXT, list(self.project_keys), self.prefix, self.normalize], ensure_ascii=False)
        return core.sha256_hex(raw.encode("utf-8"))[:16]


def _context(cfg: Config) -> _Ctx:
    keys = tuple(cfg.project_keys)
    pattern = notes.key_pattern(keys)
    if pattern is None:
        raise core.ConfigError(
            "jira.project_keys가 비어 있어 연결할 Jira 키가 없습니다. "
            "config.local.yaml에 `jira: {project_keys: [KB, OPS]}`처럼 적거나 `kb.py init`을 실행하세요"
        )
    prefix = str(cfg.get("people_prefix") or "@")
    normalize = bool(cfg.get("normalize_keys", True))
    today = core.now(cfg.tz).date().isoformat()
    return _Ctx(cfg, keys, pattern, prefix, normalize, today)


# ---------------------------------------------------------------- 줄 분석


def _wikilink_spans(text: str) -> list[tuple[int, int]]:
    if "[[" not in text:
        return []
    return [(m.start(), m.end()) for m in _WIKILINK_SPAN.finditer(text)]


def _inside(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(s <= start and end <= e for s, e in spans)


def _line_keys(text: str, ctx: _Ctx) -> list[notes.KeyMatch]:
    """언급으로 칠 키. `[[KEY-1]]`이거나 다른 위키링크 내부가 아닌 맨 글자 키."""
    if not ctx.key_re.search(text):
        return []
    spans = _wikilink_spans(text)
    return [m for m in notes.find_keys(text, ctx.project_keys) if m.bracketed or not _inside(spans, m.start, m.end)]


def _normalizable(text: str, match: notes.KeyMatch, spans: list[tuple[int, int]]) -> bool:
    if match.bracketed or _inside(spans, match.start, match.end):
        return False
    before = text[match.start - 1] if match.start > 0 else ""
    if before in _NORM_BAD_BEFORE:
        return False
    after = text[match.end:match.end + 1]
    if after == "]":
        return False
    if after in _NORM_JOINERS and match.end + 1 < len(text) and (text[match.end + 1].isalnum() or text[match.end + 1] == "_"):
        return False  # KB-12.md, KB-12/설계, KB-12-초안 같은 이름의 일부
    return True


def normalize_text(text: str, project_keys: Iterable[str]) -> tuple[str, int]:
    """한 줄의 맨 글자 키를 `[[KEY]]`로 바꾼다. (새 텍스트, 바꾼 수). 멱등."""
    keys = tuple(project_keys)
    matches = notes.find_keys(text, keys)
    if not matches:
        return text, 0
    spans = _wikilink_spans(text)
    out = text
    count = 0
    for match in reversed(matches):
        if _normalizable(text, match, spans):
            out = out[:match.start] + f"[[{match.key}]]" + out[match.end:]
            count += 1
    return out, count


def _clean_text(text: str, time_: str | None) -> str:
    """항목에 넣을 원문: 목록 기호·체크박스·시각을 떼고, 임베드 `!`를 없애고, 120자로 자른다."""
    body = text.strip()
    for _ in range(3):
        stripped = _BULLET.sub("", body, count=1)
        stripped = _CHECKBOX.sub("", stripped, count=1)
        if stripped == body:
            break
        body = stripped
    if time_ and body.startswith(time_) and (len(body) == len(time_) or body[len(time_)].isspace()):
        body = body[len(time_):].lstrip()
    body = body.replace("![[", "[[").replace("<!--", "&lt;!--")
    if len(body) > MAX_TEXT:
        cut = body[:MAX_TEXT]
        open_ = cut.rfind("[[")
        if open_ >= 0 and cut.rfind("]]") < open_:
            cut = cut[:open_]  # 열린 [[ 앞에서 자른다
        body = cut.rstrip() + "…"
    return body


def _source_link(rel: str, day: str | None) -> str:
    if day:
        return f"[[{day}]]"
    return f"[[{rel[:-3] if rel.endswith('.md') else rel}]]"


def _inbox_day(rel: str, data: bytes) -> tuple[str, str]:
    """inbox 파일의 정렬용 (날짜, 시각): 파일명 `YYYYMMDD-HHMM-` → front matter created/date → ("", "")."""
    name = rel.rsplit("/", 1)[-1]
    match = _INBOX_STAMP.match(name)
    if match:
        day = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
        hhmm = f"{match.group(4)}:{match.group(5)}" if match.group(4) else ""
        return day, hhmm
    try:
        fields = frontmatter.parse(data)
    except core.KbError:
        return "", ""
    for key in ("created", "date"):
        value = fields.get(key)
        if isinstance(value, str):
            iso = _ISO_DAY.match(value.strip())
            if iso:
                return iso.group(1), ""
    return "", ""


def _targets(keys: list[notes.KeyMatch], people: list[str]) -> tuple[str, ...]:
    out: list[str] = []
    for match in keys:
        for target in (f"jira/{match.key}.md", f"wiki/projects/{match.key.rsplit('-', 1)[0]}.md"):
            if target not in out:
                out.append(target)
    for name in people:
        target = f"wiki/people/{core.safe_name(name)}.md"
        if target not in out:
            out.append(target)
    return tuple(out)


def _normalize_daily(rel: str, data: bytes, ctx: _Ctx) -> tuple[bytes, int]:
    """daily `## 기록` 줄의 맨 글자 키를 정규화한 바이트와 바꾼 줄 수."""
    if not ctx.key_re.search(notes.decode(data)):
        return data, 0
    pieces: list[bytes] = []
    pos = 0
    changed = 0
    for line in notes.record_lines(rel, data):
        if line.in_code or line.region is not None:
            continue
        new_text, count = normalize_text(line.text, ctx.project_keys)
        if not count:
            continue
        raw_len = len(line.text.encode("utf-8"))
        pieces.append(data[pos:line.offset])
        pieces.append(new_text.encode("utf-8"))
        pos = line.offset + raw_len
        changed += 1
    if not changed:
        return data, 0
    pieces.append(data[pos:])
    return b"".join(pieces), changed


def _extract(rel: str, data: bytes, ctx: _Ctx) -> list[Entry]:
    day = notes.day_of(rel)
    if day is not None:
        sort_day, file_time = day, ""
    elif rel.startswith("daily/"):
        sort_day, file_time = "", ""
    else:
        sort_day, file_time = _inbox_day(rel, data)
    link = _source_link(rel, day)
    entries: list[Entry] = []
    last_time = file_time
    for line in notes.iter_lines(rel, data):
        if line.time:
            last_time = line.time
        if line.in_code or (line.region is not None and line.region != "extracted"):
            continue
        text = line.text
        keys = _line_keys(text, ctx)
        people = notes.find_people(text, ctx.prefix) if ctx.prefix in text else []
        if not keys and not people:
            continue
        body = _clean_text(text, line.time)
        rendered = f"- {link} {line.time} {body}" if line.time else f"- {link} {body}"
        entries.append(
            Entry(sort_day, line.time or last_time, rel, line.lineno, line.hash, rendered.rstrip(), _targets(keys, people))
        )
    return entries


def _scan(source: Source, ctx: _Ctx) -> None:
    """원천 하나를 분석한다(정규화 → 추출). 관리 구역 표시가 잘못된 파일은 정규화하지 않는다."""
    data = source.data
    assert data is not None
    try:
        managed.spans(data)
    except managed.MarkerError as exc:
        source.cacheable = False
        source.entries = _extract(source.rel, data, ctx)
        raise _Skip(f"관리 구역 표시 오류라 정규화하지 않음: {exc}") from None
    if ctx.normalize and source.rel.startswith("daily/"):
        new, count = _normalize_daily(source.rel, data, ctx)
        if count:
            source.new_data = new
            source.normalized = count
            data = new
    source.entries = _extract(source.rel, data, ctx)


class _Skip(Exception):
    pass


# ---------------------------------------------------------------- 상태 파일


def _load_json_state(root: Path, name: str) -> dict:
    """원장·캐시 읽기. 손상되었으면 빈 것으로 본다(다시 만들 수 있다)."""
    path = core.state_dir(root) / f"{name}.json"
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _usable_cache(cache: dict, ctx: _Ctx, rebuild: bool) -> tuple[dict, dict]:
    """(원천 캐시, 페이지 캐시). 버전·설정 지문이 다르거나 --rebuild면 빈 것."""
    if rebuild or cache.get("version") != CACHE_VERSION or cache.get("fingerprint") != ctx.fingerprint:
        return {}, {}
    files = cache.get("files")
    pages = cache.get("pages")
    return (files if isinstance(files, dict) else {}), (pages if isinstance(pages, dict) else {})


def _cached_entries(item: Any, rel: str) -> list[Entry] | None:
    if not isinstance(item, dict):
        return None
    try:
        return [Entry.from_json(rel, raw) for raw in item.get("entries", [])]
    except (TypeError, ValueError):
        return None


def _stat_key(path: Path) -> list[int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    return [st.st_size, st.st_mtime_ns]


# ---------------------------------------------------------------- 계획


def _list_sources(cfg: Config) -> list[tuple[str, Path]]:
    """notes.iter_sources와 같은 규칙(daily·inbox의 *.md, 임시 파일 제외, 정렬)이되 읽지는 않는다.

    웜 실행에서 크기·수정 시각이 캐시와 같은 파일은 열지 않기 위해서다(성능 예산).
    """
    out: list[tuple[str, Path]] = []
    for folder in ("daily", "inbox"):
        base = cfg.path(folder)
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.md")):
            if core.is_scannable(path):
                out.append((f"{folder}/{path.name}", path))
    return out


def _page_rel(*parts: str) -> str:
    return "/".join(parts)


def _existing_pages(cfg: Config) -> dict[str, Path]:
    pages: dict[str, Path] = {}
    for parts in MANAGED_DIRS:
        base = cfg.path(*parts)
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.md")):
            if core.is_scannable(path):
                pages[_page_rel(*parts, path.name)] = path
    return pages


def _template_for(rel: str) -> tuple[str, str]:
    """대상 rel → (ensure_page 종류, 이름)."""
    parts = rel.split("/")
    stem = parts[-1][:-3]
    if parts[0] == "jira":
        return "jira", stem
    if parts[1] == "projects":
        return "project", stem
    return "person", stem


def _render_region(entries: list[Entry]) -> str:
    seen: set[str] = set()
    lines: list[str] = []
    for entry in sorted(entries, key=Entry.sort_key):
        if entry.hash in seen:
            continue
        seen.add(entry.hash)
        lines.append(entry.text)
    return "\n".join(lines)


def _person_name(stem: str) -> str:
    """사람 페이지 파일명 → 이름. `@` 이름 문자에는 금지 문자가 없어 safe_name은 예약 이름에만 `_`를 붙인다."""
    if stem.startswith("_") and core.safe_name(stem[1:]) == stem:
        return stem[1:]
    return stem


def _index_body(ctx: _Ctx, sources: list[Source], pages: dict[str, list[Entry]], all_pages: set[str]) -> str:
    days = sorted(d for d in (notes.day_of(s.rel) for s in sources) if d)
    n_daily = sum(1 for s in sources if s.rel.startswith("daily/"))
    n_inbox = len(sources) - n_daily
    projects = sorted(r for r in all_pages if r.startswith("wiki/projects/"))
    people = sorted(r for r in all_pages if r.startswith("wiki/people/"))
    topics_dir = ctx.cfg.path("wiki", "topics")
    topics = sorted(p.stem for p in topics_dir.glob("*.md") if p.is_file() and core.is_scannable(p)) if topics_dir.is_dir() else []

    def count(rel: str) -> int:
        return len({e.hash for e in pages.get(rel, [])})

    keys_by_project: dict[str, int] = {}
    for rel in all_pages:
        if rel.startswith("jira/") and pages.get(rel):
            project = rel[len("jira/"):-3].rsplit("-", 1)[0]
            keys_by_project[project] = keys_by_project.get(project, 0) + 1

    span = f"{days[0]} ~ {days[-1]}" if days else "없음"
    lines = [
        f"- 기간: {span} · daily {n_daily}개 · inbox {n_inbox}개",
        f"- 프로젝트 {len(projects)} · 일감 {sum(keys_by_project.values())} · 사람 {len(people)} · 주제 {len(topics)}",
        "",
        "### 프로젝트",
    ]
    for rel in projects:
        stem = rel.rsplit("/", 1)[-1][:-3]
        lines.append(f"- [[projects/{stem}|{stem}]] · 일감 {keys_by_project.get(stem, 0)} · 언급 {count(rel)}")
    if not projects:
        lines.append("- (없음)")
    lines += ["", "### 사람"]
    for rel in people:
        stem = rel.rsplit("/", 1)[-1][:-3]
        lines.append(f"- [[people/{stem}|{stem}]] · 언급 {count(rel)}")
    if not people:
        lines.append("- (없음)")
    lines += ["", "### 주제"]
    lines += [f"- [[topics/{t}|{t}]]" for t in topics] or ["- (없음)"]
    return "\n".join(lines)


def _read_source(rel: str, path: Path, key: list[int], cached_files: dict, ctx: _Ctx, skipped: list[tuple[str, str]]) -> Source | None:
    """원천 하나. 크기·수정 시각이 캐시와 같으면 열지 않고, 다르면 읽어서 sha256으로 다시 확인한다."""
    item = cached_files.get(rel)
    if isinstance(item, dict) and item.get("stat") == key and isinstance(item.get("sha256"), str):
        cached = _cached_entries(item, rel)
        if cached is not None:
            return Source(rel, path, None, item["sha256"], key, entries=cached, from_cache=True)
    try:
        data = path.read_bytes()
        notes.decode(data)
    except OSError as exc:
        skipped.append((rel, f"읽지 못함: {exc}"))
        return None
    except UnicodeDecodeError as exc:
        core.get_logger(ctx.cfg.root).warning("UTF-8이 아니라 건너뜀: %s (%s)", path, exc)
        return None
    source = Source(rel, path, data, core.sha256_hex(data), key)
    if isinstance(item, dict) and item.get("sha256") == source.sha256:
        cached = _cached_entries(item, rel)
        if cached is not None:
            source.entries = cached
            source.from_cache = True
            return source
    try:
        _scan(source, ctx)
    except _Skip as exc:
        skipped.append((rel, str(exc)))
    return source


def _page_unchanged(page_cache: dict, rel: str, key: list[int] | None, body_sha: str) -> bool:
    return key is not None and page_cache.get(rel) == [*key, body_sha]


def _plan(ctx: _Ctx, *, rebuild: bool) -> Plan:
    cfg = ctx.cfg
    cache = _load_json_state(cfg.root, CACHE)
    old_ledger = _load_json_state(cfg.root, LEDGER)
    cached_files, page_cache = _usable_cache(cache, ctx, rebuild)
    new_page_cache: dict[str, list[Any]] = {}
    skipped: list[tuple[str, str]] = []
    sources: list[Source] = []
    for rel, path in _list_sources(cfg):
        key = _stat_key(path)  # 읽기 전에 잰다: 그사이 바뀌면 다음 실행이 다시 읽는다
        if key is None:
            continue
        source = _read_source(rel, path, key, cached_files, ctx, skipped)
        if source is not None:
            sources.append(source)

    # 대상별 항목
    entries_by_page: dict[str, list[Entry]] = {}
    mentions = 0
    for source in sources:
        for entry in source.entries:
            for target in entry.targets:
                entries_by_page.setdefault(target, []).append(entry)
                mentions += 1

    existing = _existing_pages(cfg)
    pages: list[PageWrite] = []
    all_pages = set(existing) | set(entries_by_page)
    for rel in sorted(all_pages):
        body = _render_region(entries_by_page.get(rel, []))
        body_sha = core.sha256_hex(body.encode("utf-8"))[:16]
        path = cfg.root / rel
        if rel in existing:
            key = _stat_key(path)
            if key is None:
                continue
            if _page_unchanged(page_cache, rel, key, body_sha):
                new_page_cache[rel] = [*key, body_sha]
                continue
            try:
                data = path.read_bytes()
                new = managed.replace_region(data, "mentions", body) if (body or managed.read_region(data, "mentions") is not None) else data
            except managed.MarkerError as exc:
                skipped.append((rel, f"관리 구역 표시 오류라 고치지 않음: {exc}"))
                continue
            except OSError as exc:
                skipped.append((rel, f"읽지 못함: {exc}"))
                continue
            if new == data:
                new_page_cache[rel] = [*key, body_sha]
                continue
            if rel.startswith("wiki/"):
                try:
                    if frontmatter.split(new)[0]:
                        new = frontmatter.set_fields(new, {"updated": ctx.today})
                except core.KbError:
                    pass
            pages.append(PageWrite(rel, path, new, core.sha256_hex(data), _template_for(rel)[0], body_sha))
        else:
            kind, name = _template_for(rel)
            if kind == "person":
                name = _person_name(name)
            _rel, template = notes.page_template(kind, name, title=None, today=ctx.today)
            new = managed.replace_region(template.encode("utf-8"), "mentions", body)
            pages.append(PageWrite(rel, path, new, None, kind, body_sha))

    # 색인
    index_rel = "wiki/index.md"
    index_path = cfg.path("wiki", "index.md")
    index_body = _index_body(ctx, sources, entries_by_page, all_pages)
    index_sha = core.sha256_hex(index_body.encode("utf-8"))[:16]
    index_key = _stat_key(index_path)
    if _page_unchanged(page_cache, index_rel, index_key, index_sha):
        new_page_cache[index_rel] = [*index_key, index_sha]
    elif index_key is not None:
        try:
            data = index_path.read_bytes()
            new = managed.replace_region(data, "index", index_body)
            if new != data:
                pages.append(PageWrite(index_rel, index_path, new, core.sha256_hex(data), "index", index_sha))
            else:
                new_page_cache[index_rel] = [*index_key, index_sha]
        except managed.MarkerError as exc:
            skipped.append((index_rel, f"관리 구역 표시 오류라 고치지 않음: {exc}"))
        except OSError as exc:
            skipped.append((index_rel, f"읽지 못함: {exc}"))
    elif not index_path.exists():
        _rel, template = notes.page_template("index", "", title=None, today=ctx.today)
        new = managed.replace_region(template.encode("utf-8"), "index", index_body)
        pages.append(PageWrite(index_rel, index_path, new, None, "index", index_sha))

    # 원장과 캐시(다시 만들 수 있는 것)
    ledger: dict[str, dict[str, list[str]]] = {}
    files: dict[str, Any] = {}
    for source in sources:
        lines: dict[str, list[str]] = {}
        for entry in source.entries:
            bucket = lines.setdefault(entry.hash, [])
            bucket.extend(t for t in entry.targets if t not in bucket)
        if lines:
            ledger[source.rel] = lines
        if source.cacheable:
            sha = core.sha256_hex(source.new_data) if source.new_data is not None else source.sha256
            # 정규화로 바뀔 파일의 stat은 쓴 뒤 _apply가 채운다
            stat_key = None if source.new_data is not None else source.stat
            files[source.rel] = {"sha256": sha, "stat": stat_key, "entries": [e.to_json() for e in source.entries]}
    new_cache = {"version": CACHE_VERSION, "fingerprint": ctx.fingerprint, "files": files, "pages": new_page_cache}
    return Plan(
        sources=sources,
        pages=pages,
        ledger=ledger,
        ledger_changed=ledger != old_ledger,
        cache=new_cache,
        cache_changed=new_cache != cache,
        mentions=mentions,
        skipped=skipped,
        entries_by_page=entries_by_page,
    )


def _has_writes(plan: Plan) -> bool:
    return bool(plan.pages) or plan.ledger_changed or plan.cache_changed or any(s.new_data is not None for s in plan.sources)


def _sources_unchanged(plan: Plan) -> bool:
    for source in plan.sources:
        if _stat_key(source.path) == source.stat:
            continue
        try:
            if core.sha256_hex(source.path.read_bytes()) != source.sha256:
                return False
        except OSError:
            return False
    return True


# ---------------------------------------------------------------- 실행


def _apply(ctx: _Ctx, plan: Plan) -> tuple[int, int, int, list[str]]:
    """잠금 안에서 쓴다. (정규화한 줄 수, 만든 페이지 수, 바꾼 페이지 수, 쓴 파일 목록)."""
    normalized = created = updated = 0
    written: list[str] = []
    cache_dirty = plan.cache_changed
    for source in plan.sources:
        if source.new_data is None:
            continue
        try:
            core.atomic_write(source.path, source.new_data, expect_sha256=source.sha256)
        except (core.ConcurrentEdit, core.FileBusy) as exc:
            plan.skipped.append((source.rel, f"정규화하지 못함: {exc}"))
            plan.cache["files"].pop(source.rel, None)  # 다음 실행에서 다시 읽는다
            cache_dirty = True
            continue
        normalized += source.normalized
        written.append(source.rel)
        item = plan.cache["files"].get(source.rel)
        if item is not None:
            item["stat"] = _stat_key(source.path)
        cache_dirty = True
    for page in plan.pages:
        try:
            if page.expect is None:
                if page.path.exists():
                    plan.skipped.append((page.rel, "그사이 다른 곳에서 만들어져 건너뜀"))
                    continue
                core.atomic_write(page.path, page.new)
                created += 1
            else:
                core.atomic_write(page.path, page.new, expect_sha256=page.expect)
                updated += 1
            written.append(page.rel)
        except (core.ConcurrentEdit, core.FileBusy) as exc:
            plan.skipped.append((page.rel, f"쓰지 못함: {exc}"))
            continue
        key = _stat_key(page.path)
        if key is not None:
            plan.cache["pages"][page.rel] = [*key, page.body_sha]
            cache_dirty = True
    if plan.ledger_changed:
        core.save_state(ctx.cfg.root, LEDGER, plan.ledger)
        written.append(f".state/{LEDGER}.json")
    if cache_dirty:
        payload = json.dumps(plan.cache, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        core.atomic_write(core.state_dir(ctx.cfg.root) / f"{CACHE}.json", payload.encode("utf-8"))
        written.append(f".state/{CACHE}.json")
    return normalized, created, updated, written


def create_stubs(cfg: Config, keys: Iterable[str], *, dry_run: bool = False) -> dict:
    """`--stub KEY`: jira/KEY.md 최소 페이지만 만든다(Emacs `o`). 이미 있으면 그대로 둔다."""
    started = time.perf_counter()
    project_keys = cfg.project_keys
    if not project_keys:
        _context(cfg)  # 같은 한국어 오류(종료 4)
    wanted: list[str] = []
    for raw in keys:
        key = raw.strip().upper()
        match = STUB_KEY.match(key)
        if not match:
            raise core.ConfigError(f"Jira 키 형식이 아닙니다: {raw!r} (예: KB-12)")
        if match.group(1) not in project_keys:
            raise core.ConfigError(f"{key}: 설정된 프로젝트 키({', '.join(project_keys)})가 아닙니다")
        if key not in wanted:
            wanted.append(key)
    today = core.now(cfg.tz).date().isoformat()
    created: list[str] = []
    existing: list[str] = []
    if dry_run:
        for key in wanted:
            (existing if cfg.path("jira", f"{key}.md").exists() else created).append(f"jira/{key}.md")
    else:
        with core.kb_lock(cfg.root, "link"):
            for key in wanted:
                rel, body = notes.page_template("jira", key, title=None, today=today)
                path = cfg.root / rel
                if path.exists():
                    existing.append(rel.as_posix())
                    continue
                core.atomic_write(path, body.encode("utf-8"))
                created.append(rel.as_posix())
    return {
        "files_scanned": 0,
        "mentions": 0,
        "pages_created": len(created),
        "normalized_lines": 0,
        "skipped_files": [],
        "elapsed": round(time.perf_counter() - started, 3),
        "stubs_created": created,
        "stubs_existing": existing,
        "dry_run": dry_run,
    }


def run(cfg: Config, *, dry_run: bool = False, stub_keys: Iterable[str] = (), rebuild: bool = False) -> dict:
    """link 한 번. stub_keys가 있으면 그 키의 jira stub만 만든다(원천을 훑지 않음).

    돌려주는 요약: files_scanned, mentions, pages_created, normalized_lines, skipped_files, elapsed
    (+ files_parsed, pages_updated, written, warnings, dry_run).
    """
    stub_keys = list(stub_keys)
    if stub_keys:
        return create_stubs(cfg, stub_keys, dry_run=dry_run)
    started = time.perf_counter()
    ctx = _context(cfg)
    plan = _plan(ctx, rebuild=rebuild)
    normalized = sum(s.normalized for s in plan.sources)
    created = sum(1 for p in plan.pages if p.expect is None)
    updated = len(plan.pages) - created
    written: list[str] = []
    if dry_run:
        written = [s.rel for s in plan.sources if s.new_data is not None] + [p.rel for p in plan.pages]
    elif _has_writes(plan):
        with core.kb_lock(cfg.root, "link"):
            if not _sources_unchanged(plan):  # 계획 뒤에 원천이 바뀌었으면 잠금 안에서 다시 읽는다(D26)
                plan = _plan(ctx, rebuild=rebuild)
            normalized, created, updated, written = _apply(ctx, plan)
    if plan.skipped and not dry_run:
        logger = core.get_logger(cfg.root)
        for rel, reason in plan.skipped:
            logger.warning("link 건너뜀: %s: %s", rel, reason)
    return {
        "files_scanned": len(plan.sources),
        "mentions": plan.mentions,
        "pages_created": created,
        "normalized_lines": normalized,
        "skipped_files": sorted({rel for rel, _ in plan.skipped}),
        "elapsed": round(time.perf_counter() - started, 3),
        "files_parsed": sum(1 for s in plan.sources if not s.from_cache),
        "pages_updated": updated,
        "written": written,
        "warnings": [f"{rel}: {reason}" for rel, reason in plan.skipped],
        "dry_run": dry_run,
    }


# ---------------------------------------------------------------- CLI


def _print_summary(summary: dict) -> None:
    head = "link(시험 실행, 쓰지 않음)" if summary.get("dry_run") else "link"
    if "stubs_created" in summary:
        for rel in summary["stubs_created"]:
            print(f"{head}: stub {'만들 예정' if summary.get('dry_run') else '만듦'}: {rel}")
        for rel in summary["stubs_existing"]:
            print(f"{head}: 이미 있음: {rel}")
        return
    print(
        f"{head}: 원천 {summary['files_scanned']}개(새로 읽음 {summary['files_parsed']}) · "
        f"언급 {summary['mentions']}건 · 새 페이지 {summary['pages_created']} · "
        f"갱신 {summary['pages_updated']} · 키 정규화 {summary['normalized_lines']}줄 · {summary['elapsed']:.2f}초"
    )
    if summary.get("dry_run") and summary["written"]:
        shown = summary["written"][:30]
        print(f"바뀔 파일 {len(summary['written'])}개:")
        for rel in shown:
            print(f"  {rel}")
        if len(summary["written"]) > len(shown):
            print(f"  … 외 {len(summary['written']) - len(shown)}개")
    for warning in summary["warnings"]:
        print(f"건너뜀: {warning}", file=sys.stderr)


def main(argv: list[str]) -> int:
    parser = core.make_parser("kb.py link", "Jira 키와 @이름을 jira·wiki 페이지의 '언급' 구역에 연결한다 (LLM 없음)")
    parser.add_argument("--dry-run", action="store_true", help="아무것도 쓰지 않고 바뀔 파일만 보여 준다")
    parser.add_argument("--stub", action="append", default=[], metavar="KEY", help="jira/KEY.md 최소 페이지만 만든다(여러 번 가능)")
    parser.add_argument("--rebuild", action="store_true", help="원장·캐시를 무시하고 모든 원천을 다시 읽는다")
    parser.add_argument("--json", action="store_true", help="요약을 JSON으로 출력")
    args = parser.parse_args(argv)
    cfg = config.load(args.root)
    summary = run(cfg, dry_run=args.dry_run, stub_keys=args.stub, rebuild=args.rebuild)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        _print_summary(summary)
    return 0


if __name__ == "__main__":
    core.run_main(main)
