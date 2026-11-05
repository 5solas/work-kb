"""C단계 점검 (`kb.py lint [--llm] [--json]`, 지시서 §6.6).

`wiki/_lint.md`의 `lint` 구역(replace 방식)에 보고를 쓴다. 그 밖의 파일은 쓰지 않는다.
항상 종료 0(보고만). 설정을 못 읽는 것처럼 점검 자체를 할 수 없을 때만 다른 종료 코드다.

절(순서대로):
- 고아 페이지: wiki 페이지(색인·결정·할 일·`_` 파일 제외) 중 다른 파일의 위키링크가 하나도 없고
  `mentions` 구역도 빈 것. `wiki/index.md`의 `index` 구역(link가 모든 페이지를 자동으로 나열)은 세지 않는다.
- 30일 멘션 없는 stub: `jira/*.md` 중 `stub: true`이고 `mentions`의 가장 최근 날짜가 30일보다 오래된 것
  (날짜 있는 언급이 없으면 파일 수정 시각).
- 유사 제목 쌍: topics끼리, people끼리 이름을 NFC·casefold·구분자 제거 뒤 difflib 비율 ≥ 0.85.
- 깨진 위키링크: daily·inbox·wiki·jira의 `[[대상]]`·`![[대상]]` 중 볼트의 어떤 파일로도 풀리지 않는 것.
  Obsidian 규칙: 볼트 기준 절대 경로(`[[jira/KB-12]]`·`[[wiki/projects/KB]]`), 경로 끝부분(`[[projects/KB]]`),
  파일 이름만(`[[KB-12]]`), 상대 경로(`./`·`../`), `.md` 생략, 대소문자 무시. 코드 블록·인라인 코드·front matter와
  `mentions` 구역(daily 원문의 사본)은 보지 않는다.
- 오래된 캐시: 동기화된 `jira/*.md`의 `synced_at`이 7일보다 오래된 것(`in_scope: false`·완료 일감 제외)과
  `.state/sync.json`의 `last_sync`가 `poll_minutes × 4`보다 오래된 경우.
- outbox: 대기(pending)·전송 중(sending)·실패(failed)·불확실(uncertain: `last_error: uncertain` 또는 오래된 sending).
- 14일 넘은 '제안': `wiki/decisions.md`에서 `상태: 제안`이고 제목 날짜가 14일보다 오래된 항목.
- 미처리 제안: `.state/compile.json`의 대기 중 주제 제안(`kb.py proposals accept`).
- 미처리 이미지: status와 같은 규칙(사이드카 없는 inbox 이미지·daily에 임베드된 raw/assets 이미지) + 포기한 이미지.
- 포기한 편찬 단위: `.state/compile.json`의 gave_up(D40).
- 마커 손상 파일: 관리 구역 마커가 깨졌거나(D24) UTF-8이 아닌 파일.
- 모순 후보(`--llm`, T5): 엔티티 요약과 관련 결정을 LLM에 보내 `{"conflicts":[{a,b,why}]}`. 기본은 꺼짐이고
  `--llm` 없이 돌면 이전 `--llm` 결과를 그대로 둔다. LLM이 꺼져 있거나 회사 환경 공개 주소면 안내 한 줄.
  `--llm`이 없으면 LLM 클라이언트를 만들지도 않는다(네트워크 없음).
"""
from __future__ import annotations

import datetime as dt
import difflib
import json
import os
import posixpath
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

import status as kb_status
from lib import config, core, frontmatter, managed, notes, outbox, prompts
from lib.config import Config
from lib.llm_client import LlmClient, SchemaError, estimate_tokens

LINT_REL = "wiki/_lint.md"
REGION = "lint"
TASK = "contradictions"

STUB_DAYS = 30
PROPOSAL_DAYS = 14
CACHE_DAYS = 7
SYNC_FACTOR = 4  # last_sync가 poll_minutes × 4보다 오래되면 보고
DEFAULT_POLL_MINUTES = 15
SIMILARITY = 0.85
MAX_LIST = 50  # 절마다 보고에 적는 최대 항목 수(나머지는 '외 N개')
STALE_SENDING_SEC = 120.0  # jira_act.stale_after 기본값(write_timeout_sec 60 × 2)
MAX_SUMMARY_CHARS = 1500
MAX_DECISION_CHARS = 500
MAX_WHY = 300
PROMPT_RESERVE_TOKENS = 200

SCAN_DIRS = ("daily", "inbox", "wiki", "jira")  # 위키링크·마커를 보는 곳
INDEX_DIRS = ("daily", "inbox", "raw", "wiki", "jira")  # 링크 대상이 될 수 있는 파일(+ 루트의 파일)
SKIP_DIRS = frozenset({"_trash", "__pycache__", "node_modules"})
WIKI_HUBS = frozenset({"wiki/index.md", "wiki/decisions.md", "wiki/actions.md"})
ENTITY_DIRS = (("projects", "project"), ("people", "person"), ("topics", "topic"))
KIND_LABEL = {"project": "프로젝트", "person": "사람", "topic": "주제"}

T5_HEADING = "모순 후보"
T5_RUN_PREFIX = "- 검사: "
T5_CARRIED = "- `--llm` 없이 실행되어 이전 검사 결과를 그대로 둡니다."
T5_NEVER = "- 검사하지 않음 (`kb.py lint --llm`으로 실행)"

_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_INBOX_STAMP = re.compile(r"^(\d{4})-?(\d{2})-?(\d{2})")
_SEPARATORS = re.compile(r"[\s\-_.·]+")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_ENTRY_FIELD = re.compile(r"^- (상태|관련|내용|근거|출처):[ \t]*(.*)$")
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$")
_META_LINE = re.compile(r"^- (출처|갱신): ")
_ID = re.compile(r"^\[?\s*([SDsd])\s*(\d{1,6})\s*\]?$")
_PROJECT_STEM = re.compile(r"^[A-Z][A-Z0-9_]+$")
_LINK_UNSAFE = re.compile(r"[\[\]#^|]")


# ---------------------------------------------------------------- 공통 도우미


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def _one_line(value: Any, limit: int = 200) -> str:
    text = " ".join(str(value or "").split())
    text = text.replace("<!--", "&lt;!--")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _code(text: str) -> str:
    """보고에 그대로 보일 글(깨진 링크 등). 위키링크가 다시 생기지 않게 코드로 감싼다."""
    return "`" + str(text).replace("`", "'").replace("\n", " ") + "`"


def _page_link(rel: str) -> str:
    """볼트 안 파일로 가는 Obsidian 절대 경로 링크. 링크로 쓸 수 없는 이름이면 코드로."""
    stem = rel[:-3] if rel.endswith(".md") else rel
    shown = stem[len("wiki/"):] if stem.startswith("wiki/") else stem
    if _LINK_UNSAFE.search(stem):
        return _code(rel)
    return f"[[{stem}|{shown}]]" if shown != stem else f"[[{stem}]]"


def _parse_time(value: Any, tz: dt.tzinfo) -> dt.datetime | None:
    if value is None:
        return None
    try:
        when = dt.datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    return when.replace(tzinfo=tz) if when.tzinfo is None else when


def _fm(data: bytes) -> dict:
    try:
        fields = frontmatter.parse(data)
    except core.KbError:
        return {}
    return fields if isinstance(fields, dict) else {}


def _true(value: Any) -> bool:
    return str(value if value is not None else "").strip().lower() == "true"


def _region_text(data: bytes, name: str) -> str | None:
    try:
        body = managed.read_region(data, name)
    except managed.MarkerError:
        return None
    if body is None:
        return None
    try:
        return notes.decode(body)
    except UnicodeDecodeError:
        return None


def _list_lines(text: str | None) -> list[str]:
    return [line for line in notes.split_lines(text or "") if line.startswith("- ")]


# ---------------------------------------------------------------- 볼트 읽기


@dataclass
class _Vault:
    cfg: Config
    now: dt.datetime
    files: dict[str, str] = field(default_factory=dict)  # fold(rel) → rel (INDEX_DIRS + 루트 파일)
    by_name: dict[str, list[str]] = field(default_factory=dict)  # fold(파일 이름) → rel 목록
    md: dict[str, bytes] = field(default_factory=dict)  # SCAN_DIRS의 .md (UTF-8인 것만)
    not_utf8: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def today(self) -> dt.date:
        return self.now.date()

    def add_file(self, rel: str) -> None:
        self.files[_fold(rel)] = rel
        self.by_name.setdefault(_fold(rel.rsplit("/", 1)[-1]), []).append(rel)

    def resolve(self, source: str, target: str) -> list[str] | None:
        """위키링크 대상 → 풀린 파일 rel 목록([]면 깨짐). None이면 검사하지 않는 대상(URL 등)."""
        text = target.strip().rstrip("\\").strip().replace("\\", "/")
        if not text or "://" in text or text.lower().startswith("mailto:"):
            return None
        if text.startswith(("./", "../")):
            text = posixpath.normpath(posixpath.join(posixpath.dirname(source), text))
            if text.startswith(".."):
                return []
        text = text.lstrip("/")
        if not text:
            return None
        candidates = [text] if text.lower().endswith(".md") else [text, text + ".md"]
        hits = [self.files[_fold(c)] for c in candidates if _fold(c) in self.files]
        if hits:
            return hits
        for candidate in candidates:
            folded = _fold(candidate)
            name = folded.rsplit("/", 1)[-1]
            hits = [rel for rel in self.by_name.get(name, []) if "/" not in folded or _fold(rel).endswith("/" + folded)]
            if hits:
                return hits
        return []


def _walk(base: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if core.is_scannable(path):
                yield path


def _load_vault(cfg: Config) -> _Vault:
    vault = _Vault(cfg=cfg, now=core.now(cfg.tz))
    root = cfg.root
    if root.is_dir():
        for path in sorted(root.iterdir()):
            if path.is_file() and core.is_scannable(path) and not path.name.startswith("."):
                vault.add_file(path.name)
    for folder in INDEX_DIRS:
        base = cfg.path(folder)
        if not base.is_dir():
            continue
        for path in _walk(base):
            rel = path.relative_to(root).as_posix()
            vault.add_file(rel)
            if folder not in SCAN_DIRS or path.suffix.lower() != ".md" or rel == LINT_REL:
                continue
            try:
                data = path.read_bytes()
            except OSError as exc:
                vault.problems.append(f"{rel}: 읽지 못했습니다 ({exc})")
                continue
            try:
                notes.decode(data)
            except UnicodeDecodeError:
                vault.not_utf8.append(rel)
                continue
            vault.md[rel] = data
    return vault


# ---------------------------------------------------------------- 위키링크


@dataclass(frozen=True)
class _Link:
    source: str
    lineno: int
    target: str
    region: str | None


def _mask_inline_code(text: str) -> str:
    return notes.INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), text)


def _links(rel: str, data: bytes) -> Iterator[_Link]:
    for line in notes.iter_lines(rel, data):
        if line.in_code or "[[" not in line.text:
            continue
        for match in notes.WIKILINK_RE.finditer(_mask_inline_code(line.text)):
            yield _Link(rel, line.lineno, match.group(1), line.region)


# ---------------------------------------------------------------- 결정 로그


@dataclass
class _Decision:
    lineno: int
    day: str | None
    title: str
    status: str
    fields: dict[str, str]

    @property
    def text(self) -> str:
        return " ".join([self.title, *self.fields.values()])

    def label(self) -> str:
        head = f"{self.day} {self.title}" if self.day else self.title
        return f"결정 「{_one_line(head, 80)}」({self.status or '상태 없음'})"


def _decisions(data: bytes | None) -> list[_Decision]:
    """`wiki/decisions.md`의 `## [날짜] 제목` 항목과 그 아래 `- 상태:`·`- 관련:`… 줄."""
    if not data:
        return []
    out: list[_Decision] = []
    current: _Decision | None = None
    for line in notes.iter_lines("wiki/decisions.md", data):
        if line.in_code:
            continue
        heading = _HEADING.match(line.text)
        if heading:
            current = None
            if len(heading.group(1)) == 2:
                title = heading.group(2)
                first = title.split(" ", 1)
                day = first[0] if _DAY.fullmatch(first[0]) else None
                rest = first[1] if day and len(first) > 1 else (title if not day else "")
                current = _Decision(line.lineno, day, rest.strip(), "", {})
                out.append(current)
            continue
        if current is None:
            continue
        match = _ENTRY_FIELD.match(line.text)
        if not match:
            continue
        value = _HTML_COMMENT.sub("", match.group(2)).strip()
        if match.group(1) == "상태":
            current.status = value.split()[0] if value.split() else ""
        else:
            current.fields[match.group(1)] = value
    return [d for d in out if d.status or d.fields]


# ---------------------------------------------------------------- 절


@dataclass
class _Section:
    key: str
    title: str
    items: list[dict] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # 개수에 들지 않는 안내 줄
    counted: bool = True  # 제목에 개수를 붙이는가(이번에 검사하지 않은 절은 붙이지 않는다)

    @property
    def count(self) -> int:
        return len(self.items)

    def render(self) -> list[str]:
        body = self.lines[:MAX_LIST]
        if len(self.lines) > MAX_LIST:
            body.append(f"- … 외 {len(self.lines) - MAX_LIST}개")
        if not body and not self.notes:
            body = ["- 없음"]
        title = f"## {self.title} ({self.count})" if self.counted else f"## {self.title}"
        return [title, *self.notes, *body, ""]


def _orphans(vault: _Vault, links: Sequence[_Link]) -> _Section:
    section = _Section("orphans", "고아 페이지")
    inbound: dict[str, set[str]] = {}
    for link in links:
        if link.source == "wiki/index.md" and link.region == "index":
            continue  # link가 모든 페이지를 자동으로 나열한다
        for rel in vault.resolve(link.source, link.target) or ():
            if rel != link.source:
                inbound.setdefault(rel, set()).add(link.source)
    for rel, data in sorted(vault.md.items()):
        if not rel.startswith("wiki/") or rel in WIKI_HUBS or rel.rsplit("/", 1)[-1].startswith("_"):
            continue
        if inbound.get(rel) or _list_lines(_region_text(data, "mentions")):
            continue
        section.items.append({"page": rel})
        section.lines.append(f"- {_page_link(rel)}")
    if section.items:
        section.notes.append("- 어떤 파일도 링크하지 않고 언급도 없는 페이지입니다. 필요 없으면 `_trash/`로 옮깁니다.")
    return section


def _mention_day(line: str) -> dt.date | None:
    links = notes.wikilinks(line)
    if not links:
        return None
    target = links[0].replace("\\", "/")
    name = target.rsplit("/", 1)[-1]
    if _DAY.fullmatch(name):
        text = name
    else:
        match = _INBOX_STAMP.match(name)
        if not match:
            return None
        text = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def _stale_stubs(vault: _Vault) -> _Section:
    section = _Section("stale_stubs", f"{STUB_DAYS}일 멘션 없는 stub")
    limit = vault.today - dt.timedelta(days=STUB_DAYS)
    for rel, data in sorted(vault.md.items()):
        if not rel.startswith("jira/") or rel.count("/") != 1:
            continue
        if not _true(_fm(data).get("stub")):
            continue
        entries = _list_lines(_region_text(data, "mentions"))
        days = [d for d in (_mention_day(line) for line in entries) if d is not None]
        last = max(days) if days else None
        basis = last
        if basis is None:
            try:
                basis = dt.datetime.fromtimestamp(vault.cfg.root.joinpath(rel).stat().st_mtime, vault.now.tzinfo).date()
            except OSError:
                continue
        if basis >= limit:
            continue
        key = rel[len("jira/"):-3]
        section.items.append({"key": key, "page": rel, "last_mention": last.isoformat() if last else None, "mentions": len(entries)})
        when = f"마지막 언급 {last.isoformat()}" if last else "날짜 있는 언급 없음"
        section.lines.append(f"- {_page_link(rel)} · {when} · `kb.py sync --key {key}`로 받거나 지웁니다(`_trash/`)")
    return section


def _similar_titles(vault: _Vault) -> _Section:
    section = _Section("similar_titles", "유사 제목 쌍")
    for sub, kind in (("topics", "topic"), ("people", "person")):
        names: list[tuple[str, str, str]] = []  # (정규화, 이름, rel)
        prefix = f"wiki/{sub}/"
        for rel, data in sorted(vault.md.items()):
            if not rel.startswith(prefix) or "/" in rel[len(prefix):]:
                continue
            name = str(_fm(data).get("name") or rel[len(prefix):-3]).strip()
            key = _SEPARATORS.sub("", _fold(name))
            if key:
                names.append((key, name, rel))
        for i, (key_a, name_a, rel_a) in enumerate(names):
            for key_b, name_b, rel_b in names[i + 1:]:
                matcher = difflib.SequenceMatcher(None, key_a, key_b)
                if matcher.real_quick_ratio() < SIMILARITY or matcher.quick_ratio() < SIMILARITY:
                    continue
                ratio = matcher.ratio()
                if ratio < SIMILARITY:
                    continue
                section.items.append({"kind": kind, "a": rel_a, "b": rel_b, "ratio": round(ratio, 3)})
                section.lines.append(f"- {KIND_LABEL[kind]}: {_page_link(rel_a)} ↔ {_page_link(rel_b)} · 유사도 {ratio:.2f}")
    if section.items:
        section.notes.append("- 같은 대상이면 한쪽으로 합치고(주제는 `aliases`에 이름 추가) 다른 쪽은 `_trash/`로 옮깁니다.")
    return section


def _broken_links(vault: _Vault, links: Sequence[_Link]) -> _Section:
    section = _Section("broken_links", "깨진 위키링크")
    seen: set[tuple[str, int, str]] = set()
    for link in links:
        if link.region == "mentions":
            continue  # daily 원문의 사본: 원문 쪽에서 보고한다
        resolved = vault.resolve(link.source, link.target)
        if resolved is None or resolved:
            continue
        ident = (link.source, link.lineno, link.target)
        if ident in seen:
            continue
        seen.add(ident)
        section.items.append({"source": link.source, "line": link.lineno, "target": link.target.strip()})
        section.lines.append(f"- {_page_link(link.source)} {link.lineno}번째 줄: {_code('[[' + link.target.strip() + ']]')}")
    return section


def _stale_cache(vault: _Vault) -> _Section:
    section = _Section("stale_cache", "오래된 캐시")
    cfg = vault.cfg
    limit = vault.now - dt.timedelta(days=CACHE_DAYS)
    stale: list[tuple[dt.datetime, str, str]] = []
    for rel, data in vault.md.items():
        if not rel.startswith("jira/") or rel.count("/") != 1:
            continue
        fields = _fm(data)
        if _true(fields.get("stub")) or str(fields.get("in_scope", "")).strip().lower() == "false":
            continue
        if str(fields.get("status_category", "")).strip().lower() == "done":
            continue
        synced = _parse_time(fields.get("synced_at"), cfg.tz)
        if synced is None or synced >= limit:
            continue
        stale.append((synced, rel, str(fields.get("synced_at"))))
    for synced, rel, raw in sorted(stale):
        key = rel[len("jira/"):-3]
        section.items.append({"kind": "issue", "key": key, "page": rel, "synced_at": raw})
        section.lines.append(f"- {_page_link(rel)} · 동기화 {synced.astimezone(cfg.tz).strftime('%Y-%m-%d %H:%M')}")
    if stale:
        section.notes.append(f"- 일감 {len(stale)}개가 {CACHE_DAYS}일 넘게 다시 받지 않았습니다(내용이 같으면 `synced_at`은 바뀌지 않습니다). `kb.py sync --full`")

    try:
        state = core.load_state(cfg.root, "sync")
    except (core.KbError, OSError) as exc:
        vault.problems.append(f".state/sync.json을 읽지 못했습니다 ({exc})")
        state = {}
    raw_last = state.get("last_sync") if isinstance(state, Mapping) else None
    last = _parse_time(raw_last, cfg.tz)
    try:
        poll = float(cfg.get("jira.poll_minutes") or DEFAULT_POLL_MINUTES)
    except (TypeError, ValueError):
        poll = float(DEFAULT_POLL_MINUTES)
    poll = poll if poll > 0 else float(DEFAULT_POLL_MINUTES)
    allowed = dt.timedelta(minutes=poll * SYNC_FACTOR)
    if last is not None:
        age = vault.now - last
        if age > allowed:
            hours = age.total_seconds() / 3600
            section.items.append({"kind": "last_sync", "last_sync": str(raw_last), "age_hours": round(hours, 1)})
            section.lines.insert(0, f"- 마지막 동기화 {last.astimezone(cfg.tz).strftime('%Y-%m-%d %H:%M')} ({hours:.1f}시간 전, 기준 {poll * SYNC_FACTOR:g}분) → `kb.py sync`")
    elif cfg.get("jira.base_url"):
        section.items.append({"kind": "last_sync", "last_sync": None, "age_hours": None})
        section.lines.insert(0, "- 동기화 기록이 없습니다 → `kb.py sync`")
    return section


def _outbox(vault: _Vault) -> _Section:
    section = _Section("outbox", "outbox 대기·실패")
    cfg = vault.cfg
    try:
        records = outbox.records(cfg.root)
        broken = outbox.broken_lines(cfg.root)
    except (core.KbError, OSError) as exc:
        vault.problems.append(f"jira/outbox.jsonl을 읽지 못했습니다 ({exc})")
        return section
    try:
        stale_sec = 2 * float(cfg.get("jira.write_timeout_sec") or STALE_SENDING_SEC / 2)
    except (TypeError, ValueError):
        stale_sec = STALE_SENDING_SEC
    now = dt.datetime.now(dt.timezone.utc)  # claimed_at은 실제 시계(KB_FAKE_NOW와 무관)
    labels = {"pending": "대기", "sending": "전송 중", "failed": "실패", "uncertain": "불확실"}
    hints = {
        "pending": "`kb.py act flush`",
        "failed": "`kb.py act flush --retry-failed`",
        "uncertain": "Jira에서 확인 뒤 `kb.py act flush --retry-uncertain ID`",
        "sending": "다른 실행이 보내는 중",
    }
    for record in records:
        state = str(record.get("status") or "")
        if state == outbox.PENDING and record.get("last_error") == outbox.UNCERTAIN:
            state = "uncertain"
        elif state == outbox.SENDING:
            claimed = _parse_time(record.get("claimed_at"), dt.timezone.utc)
            if claimed is None or (now - claimed).total_seconds() > stale_sec:
                state = "uncertain"
        if state not in labels:
            continue
        ident = str(record.get("id") or "?")
        section.items.append({
            "id": ident, "state": state, "action": record.get("action"), "key": record.get("key"),
            "attempts": record.get("attempts"), "last_error": record.get("last_error"),
        })
        error = record.get("error_detail") or record.get("last_error")
        tail = f" · 오류: {_one_line(error, 120)}" if error and state != "uncertain" else ""
        section.lines.append(
            f"- {labels[state]} · {_one_line(record.get('action'), 20)} {_one_line(record.get('key'), 30)} · `{ident}`"
            f" · 시도 {record.get('attempts') or 0}{tail} → {hints[state]}"
        )
    if broken:
        section.notes.append(f"- jira/outbox.jsonl에 읽지 못한 줄 {broken}개")
    return section


def _old_proposed(vault: _Vault, decisions: Sequence[_Decision]) -> _Section:
    section = _Section("old_proposed", f"{PROPOSAL_DAYS}일 넘은 '제안'")
    limit = vault.today - dt.timedelta(days=PROPOSAL_DAYS)
    for decision in decisions:
        if decision.status != "제안" or not decision.day:
            continue
        try:
            day = dt.date.fromisoformat(decision.day)
        except ValueError:
            continue
        if day >= limit:
            continue
        section.items.append({"day": decision.day, "title": decision.title, "line": decision.lineno})
        section.lines.append(f"- {decision.day} {_one_line(decision.title, 80)} · [[wiki/decisions|decisions]] {decision.lineno}번째 줄")
    if section.items:
        section.notes.append("- `상태: 제안`을 '확정' 또는 '기각'으로 바꿉니다.")
    return section


def _pending_proposals(vault: _Vault) -> _Section:
    section = _Section("pending_proposals", "미처리 제안")
    try:
        import compile as kb_compile  # 무거운 모듈이라 여기서만 가져온다

        pending = kb_compile.pending_proposals(vault.cfg)
    except (core.KbError, OSError) as exc:
        vault.problems.append(f".state/compile.json을 읽지 못했습니다 ({exc})")
        return section
    for ident, item in pending:
        name = _one_line(item.get("name"), 60)
        created = str(item.get("created") or "")[:10]
        section.items.append({"id": str(ident), "name": item.get("name"), "created": created or None})
        section.lines.append(f"- [{ident}] {name}" + (f" · {created}" if created else ""))
    if section.items:
        section.notes.append("- [[wiki/_proposals|_proposals]] · 승인: `kb.py proposals accept <번호|all>`")
    return section


def _images(vault: _Vault) -> _Section:
    section = _Section("images", "미처리 이미지")
    cfg = vault.cfg
    inbox = cfg.path("inbox")
    if inbox.is_dir():
        for path in sorted(inbox.iterdir()):
            if path.suffix.lower() in kb_status.IMAGE_SUFFIXES and path.is_file() and core.is_scannable(path):
                if not path.with_suffix(".md").exists():
                    rel = f"inbox/{path.name}"
                    section.items.append({"image": rel, "state": "unprocessed"})
                    section.lines.append(f"- {_code(rel)}")
    assets = cfg.path("raw", "assets")
    for name in sorted(kb_status._embedded_assets(cfg)):
        if (assets / name).is_file() and not cfg.path("inbox", f"{Path(name).stem}.md").exists():
            rel = f"raw/assets/{name}"
            section.items.append({"image": rel, "state": "unprocessed"})
            section.lines.append(f"- {_code(rel)}")
    if section.items:
        section.notes.append("- `kb.py ingest`로 처리합니다(비전 프로필 필요).")
    try:
        ledger = core.load_state(cfg.root, kb_status.IMAGES_LEDGER)
    except (core.KbError, OSError) as exc:
        vault.problems.append(f".state/images.json을 읽지 못했습니다 ({exc})")
        ledger = {}
    skipped = ledger.get("skipped") if isinstance(ledger, Mapping) else None
    for sha, entry in sorted((skipped or {}).items()) if isinstance(skipped, Mapping) else ():
        if not isinstance(entry, Mapping):
            continue
        count = entry.get("count")
        given_up = entry.get("status") == "gave_up" or (
            isinstance(count, int) and not isinstance(count, bool) and count >= kb_status.IMAGE_GIVE_UP_AFTER
        )
        if not given_up:
            continue
        image = str(entry.get("image") or sha)
        section.items.append({"image": image, "state": "gave_up", "error": entry.get("error")})
        error = f" · {_one_line(entry.get('error'), 100)}" if entry.get("error") else ""
        section.lines.append(f"- 포기: {_code(image)}{error} → `kb.py ingest --force`")
    return section


def _gave_up(vault: _Vault) -> _Section:
    section = _Section("gave_up", "포기한 편찬 단위")
    try:
        ledger = core.load_state(vault.cfg.root, kb_status.COMPILE_LEDGER)
    except (core.KbError, OSError) as exc:
        vault.problems.append(f".state/compile.json을 읽지 못했습니다 ({exc})")
        return section
    if not isinstance(ledger, Mapping):
        return section
    for task in sorted(ledger):
        units = ledger[task]
        if str(task).startswith("_") or not isinstance(units, Mapping):
            continue
        for unit in sorted(units):
            record = units[unit]
            if isinstance(record, Mapping) and record.get("status") == "gave_up":
                section.items.append({"task": task, "unit": unit})
                section.lines.append(f"- {task}: {_code(unit)}")
    if section.items:
        section.notes.append("- 입력이 바뀌면 다시 시도합니다. 지금 다시 하려면 `kb.py compile --force`")
    return section


def _broken_markers(vault: _Vault) -> _Section:
    section = _Section("broken_markers", "마커 손상 파일")
    candidates = dict(vault.md)
    lint_path = vault.cfg.root / LINT_REL
    if lint_path.is_file():
        try:
            candidates[LINT_REL] = lint_path.read_bytes()
        except OSError:
            pass
    for rel, data in sorted(candidates.items()):
        if b"<!-- kb:" not in data:
            continue
        try:
            managed.spans(data)
        except managed.MarkerError as exc:
            section.items.append({"page": rel, "error": str(exc)})
            section.lines.append(f"- {_page_link(rel)}: {_one_line(exc, 160)}")
    for rel in sorted(vault.not_utf8):
        section.items.append({"page": rel, "error": "UTF-8이 아님"})
        section.lines.append(f"- {_code(rel)}: UTF-8이 아니라 읽지 못했습니다")
    if section.items:
        section.notes.append("- 도구는 이 파일을 고치지 않습니다(D24). 마커 짝을 손으로 맞춥니다.")
    return section


# ---------------------------------------------------------------- T5 모순 후보


@dataclass
class _Group:
    header: str
    items: list[tuple[str, str]]  # (id, 한 줄)


@dataclass
class _Entity:
    rel: str
    kind: str
    name: str
    summary: str
    keys: set[str]  # 프로젝트 키(프로젝트), 이름(사람), 이름·별칭(주제) — casefold


def _summary_body(text: str | None) -> str:
    lines = [line for line in notes.split_lines(text or "") if not _META_LINE.match(line)]
    return " ".join(" ".join(lines).split())


def _entities(vault: _Vault) -> list[_Entity]:
    out: list[_Entity] = []
    for sub, kind in ENTITY_DIRS:
        prefix = f"wiki/{sub}/"
        for rel, data in sorted(vault.md.items()):
            if not rel.startswith(prefix) or "/" in rel[len(prefix):]:
                continue
            stem = rel[len(prefix):-3]
            fields = _fm(data)
            if kind == "project":
                title = str(fields.get("title") or "").strip()
                name = f"{stem} — {title}" if title and title != stem else stem
                keys = {stem}
            elif kind == "person":
                name = str(fields.get("name") or stem).strip()
                keys = {_fold(name), _fold(stem)}
            else:
                name = str(fields.get("name") or stem).strip()
                raw = fields.get("aliases")
                aliases = [a for a in raw if isinstance(a, str) and a.strip()] if isinstance(raw, list) else []
                keys = {_fold(x.strip()) for x in (name, stem, *aliases) if x.strip()}
            summary = _summary_body(_region_text(data, "summary"))[:MAX_SUMMARY_CHARS]
            out.append(_Entity(rel, kind, name, summary, keys))
    return out


@dataclass(frozen=True)
class _Refs:
    """결정 하나가 가리키는 것: 링크로 풀린 파일, 프로젝트 키, 사람 이름(casefold), 전체 글(casefold)."""

    targets: frozenset[str]
    projects: frozenset[str]
    people: frozenset[str]
    folded: str


def _refs(vault: _Vault, decision: _Decision, project_pattern: re.Pattern[str] | None) -> _Refs:
    text = decision.text
    targets = frozenset(r for t in notes.wikilinks(text) for r in (vault.resolve("wiki/decisions.md", t) or ()))
    projects = frozenset(m.group(0).rsplit("-", 1)[0] for m in project_pattern.finditer(text)) if project_pattern else frozenset()
    prefix = str(vault.cfg.get("people_prefix") or "@")
    people = frozenset(_fold(name) for name in notes.find_people(text, prefix))
    return _Refs(targets, projects, people, _fold(text))


def _related(refs: _Refs, entity: _Entity) -> bool:
    if entity.rel in refs.targets:
        return True
    if entity.kind == "project":
        return bool(refs.projects & entity.keys)
    if entity.kind == "person":
        return bool(refs.people & entity.keys)
    return any(len(key) >= 2 and key in refs.folded for key in entity.keys)


def _decision_line(decision: _Decision) -> str:
    parts = [f"({decision.status or '상태 없음'})"]
    if decision.day:
        parts.append(decision.day)
    parts.append(_one_line(decision.title, 120))
    for name in ("내용", "근거", "관련"):
        if decision.fields.get(name):
            parts.append(f"/ {name}: {_one_line(decision.fields[name], MAX_DECISION_CHARS)}")
    return " ".join(parts)


def _t5_groups(vault: _Vault, decisions: Sequence[_Decision]) -> tuple[list[_Group], dict[str, str]]:
    """대상별 묶음과 id → 보고용 이름. 항목이 둘 이상인 묶음만(하나로는 모순이 없다)."""
    live = [d for d in decisions if d.status != "기각"]
    labels: dict[str, str] = {}
    dec_ids: dict[int, str] = {}
    for n, decision in enumerate(live, start=1):
        dec_ids[id(decision)] = f"D{n}"
        labels[f"D{n}"] = decision.label()
    stems = [s for s in {*vault.cfg.project_keys} if s]
    stems += [rel[len("wiki/projects/"):-3] for rel in vault.md if rel.startswith("wiki/projects/") and rel.count("/") == 2]
    project_pattern = notes.key_pattern(sorted({s for s in stems if _PROJECT_STEM.match(s)}))
    refs = {id(d): _refs(vault, d, project_pattern) for d in live}
    groups: list[_Group] = []
    used: set[int] = set()
    s_no = 0
    for entity in _entities(vault):
        items: list[tuple[str, str]] = []
        if entity.summary:
            s_no += 1
            ident = f"S{s_no}"
            labels[ident] = f"{_page_link(entity.rel)} 요약"
            items.append((ident, entity.summary))
        for decision in live:
            if _related(refs[id(decision)], entity):
                used.add(id(decision))
                items.append((dec_ids[id(decision)], _decision_line(decision)))
        if len(items) >= 2:
            groups.append(_Group(f"{KIND_LABEL[entity.kind]} {entity.name} ({entity.rel})", items))
    rest = [(dec_ids[id(d)], _decision_line(d)) for d in live if id(d) not in used]
    if len(rest) >= 2:
        groups.append(_Group("관련 대상이 없는 결정", rest))
    return groups, labels


def _group_tokens(group: _Group) -> int:
    return estimate_tokens("\n".join([f"## {group.header}", *(f"[{i}] {t}" for i, t in group.items)])) + 2


def _t5_chunks(groups: Sequence[_Group], budget: int) -> list[list[_Group]]:
    """입력 한도에 맞춰 묶음을 나눈다. 한 묶음이 너무 크면 결정을 나눠 요약을 되풀이한다(D41: T5는 부분 결과를 합친다)."""
    pieces: list[_Group] = []
    for group in groups:
        if _group_tokens(group) <= budget:
            pieces.append(group)
            continue
        head = [it for it in group.items if it[0].startswith("S")]
        current = _Group(group.header, list(head))
        for item in group.items:
            if item[0].startswith("S"):
                continue
            trial = _Group(group.header, [*current.items, item])
            if len(current.items) > len(head) and _group_tokens(trial) > budget:
                pieces.append(current)
                current = _Group(group.header, [*head, item])
            else:
                current = trial
        if len(current.items) >= 2:
            pieces.append(current)
    chunks: list[list[_Group]] = []
    size = 0
    for piece in pieces:
        tokens = _group_tokens(piece)
        if chunks and size + tokens <= budget:
            chunks[-1].append(piece)
            size += tokens
        else:
            chunks.append([piece])
            size = tokens
    return chunks


def _t5_user(groups: Sequence[_Group]) -> str:
    lines = ["대상별로 요약([S])과 관련 결정([D])을 묶었다. 같은 묶음 안에서 서로 어긋나는 쌍을 찾는다.", ""]
    for group in groups:
        lines.append(f"## {group.header}")
        lines += [f"[{ident}] {text}" for ident, text in group.items]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _fit(groups: Sequence[_Group], budget: int) -> list[_Group]:
    """받은 프로필의 한도에 맞게 앞에서부터 자른다(적어도 첫 묶음의 두 항목은 보낸다)."""
    out: list[_Group] = []
    for group in groups:
        trial = [*out, group]
        if estimate_tokens(_t5_user(trial)) <= budget:
            out = trial
            continue
        if not out:
            items = list(group.items[:2])
            for item in group.items[2:]:
                if estimate_tokens(_t5_user([_Group(group.header, [*items, item])])) > budget:
                    break
                items.append(item)
            out = [_Group(group.header, items)]
        break
    return out


def norm_id(value: Any) -> str | None:
    match = _ID.match(str(value or "").strip())
    return f"{match.group(1).upper()}{int(match.group(2))}" if match else None


def validate_contradictions(data: Mapping, sent: Iterable[str] | None = None) -> None:
    """T5 스키마. sent가 주어지면 a·b가 입력에 있던 id여야 한다."""
    known = set(sent) if sent is not None else None
    conflicts = data.get("conflicts") if isinstance(data, Mapping) else None
    if not isinstance(conflicts, list):
        raise SchemaError("conflicts는 목록이어야 합니다")
    for n, item in enumerate(conflicts, start=1):
        if not isinstance(item, Mapping):
            raise SchemaError(f"conflicts[{n}]는 객체여야 합니다")
        for key in ("a", "b", "why"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise SchemaError(f"conflicts[{n}].{key}는 비어 있지 않은 문자열이어야 합니다")
        a, b = norm_id(item["a"]), norm_id(item["b"])
        if known is not None and (a not in known or b not in known):
            raise SchemaError(f"conflicts[{n}]: 입력에 없는 출처입니다 ({item['a']!r}, {item['b']!r})")
        if a is not None and a == b:
            raise SchemaError(f"conflicts[{n}]: a와 b가 같습니다")


def contradictions(cfg: Config, vault: _Vault, decisions: Sequence[_Decision], *, client: LlmClient | None = None) -> dict:
    """T5. 반환 {status, note, conflicts:[{a,b,a_label,b_label,why}], calls, profile, groups}.

    status: refused(회사 공개 주소) | error | disabled | nothing | ok | partial | skipped.
    """
    result: dict[str, Any] = {"status": "ok", "note": "", "conflicts": [], "calls": 0, "profile": None, "groups": 0}
    try:
        client = client or LlmClient(cfg)  # 회사 차단(D44): enabled 검사보다 먼저
    except core.PublicHostRefused as exc:
        result.update(status="refused", note=f"회사 환경에서 공개 LLM 주소가 설정되어 모순 검사를 하지 않았습니다 ({core.redact(str(exc))})")
        return result
    except core.KbError as exc:
        result.update(status="error", note=f"LLM 설정을 읽지 못해 모순 검사를 하지 않았습니다 ({core.redact(str(exc))})")
        return result
    if not client.enabled:
        result.update(status="disabled", note="LLM이 꺼져 있어 모순 검사를 건너뜁니다 (llm.enabled: false)")
        return result
    try:
        return _run_t5(client, vault, decisions, result)
    except core.PublicHostRefused as exc:  # 요청 직전 검사(llm_client)
        result.update(status="refused", conflicts=[], note=f"회사 환경에서 공개 LLM 주소라 모순 검사를 멈췄습니다 ({core.redact(str(exc))})")
    except core.KbError as exc:  # 프롬프트·AGENTS.md 없음 등
        result.update(status="error", conflicts=[], note=f"모순 검사를 하지 못했습니다 ({core.redact(str(exc))})")
    return result


def _run_t5(client: LlmClient, vault: _Vault, decisions: Sequence[_Decision], result: dict) -> dict:
    groups, labels = _t5_groups(vault, decisions)
    result["groups"] = len(groups)
    if not groups:
        result.update(status="nothing", note="함께 볼 요약·결정이 없어 모순 검사를 하지 않았습니다")
        return result
    system_tokens = estimate_tokens(prompts.system_prompt(TASK)) + PROMPT_RESERVE_TOKENS
    budget = max(client.input_limit(TASK) - system_tokens, 200)
    chunks = _t5_chunks(groups, budget)
    seen: set[tuple[str, str]] = set()
    ok = 0
    for chunk in chunks:
        sent: set[str] = set()

        def messages_for(limit: int, chunk: Sequence[_Group] = chunk, sent: set[str] = sent) -> list[dict]:
            chosen = _fit(chunk, max(limit - system_tokens, 200))
            sent.clear()
            sent.update(ident for group in chosen for ident, _ in group.items)
            return prompts.build_messages(TASK, _t5_user(chosen))

        def validate(data: dict, sent: set[str] = sent) -> None:
            validate_contradictions(data, sent)

        outcome = client.run_task(TASK, messages_for, validate=validate)
        result["calls"] += sum(1 for a in outcome.attempts if a.get("status") not in ("missing", "no_vision"))
        if outcome.status != "ok" or not outcome.data:
            continue
        ok += 1
        result["profile"] = result["profile"] or outcome.profile
        for item in outcome.data["conflicts"]:
            a, b = norm_id(item["a"]), norm_id(item["b"])
            pair = tuple(sorted((a or item["a"], b or item["b"])))
            if pair in seen:
                continue
            seen.add(pair)  # type: ignore[arg-type]
            result["conflicts"].append({
                "a": a, "b": b, "why": _one_line(item["why"], MAX_WHY),
                "a_label": labels.get(a or "", _code(item["a"])), "b_label": labels.get(b or "", _code(item["b"])),
            })
    if ok == len(chunks):
        result["status"] = "ok"
    elif ok:
        result.update(status="partial", note=f"묶음 {len(chunks)}개 중 {len(chunks) - ok}개는 LLM 응답을 받지 못했습니다")
    else:
        result.update(status="skipped", note="LLM 응답을 받지 못해 모순 검사를 건너뛰었습니다 (연결·형식 오류, `.state/logs` 참고)")
    return result


def _previous_t5(current: bytes | None) -> list[str] | None:
    """이전 보고의 모순 후보 절(--llm으로 검사한 것)만. 없으면 None."""
    if not current:
        return None
    text = _region_text(current, REGION)
    if not text:
        return None
    lines = notes.split_lines(text)
    out: list[str] | None = None
    for line in lines:
        if line.startswith("## "):
            if out is not None:
                break
            if line[3:].startswith(T5_HEADING):
                out = []
            continue
        if out is not None:
            out.append(line)
    if not out:
        return None
    body = [line for line in out if line != T5_CARRIED]
    while body and not body[-1].strip():
        body.pop()
    if not any(line.startswith(T5_RUN_PREFIX) for line in body):
        return None
    return body


def _t5_section(vault: _Vault, outcome: dict | None, previous: list[str] | None) -> _Section:
    section = _Section("contradictions", T5_HEADING)
    if outcome is None:
        section.counted = False
        if previous:
            section.notes.append(T5_CARRIED)
            section.notes += previous
            section.title = T5_HEADING + " · 이전 결과"
        else:
            section.notes.append(T5_NEVER)
        return section
    stamp = vault.now.strftime("%Y-%m-%d %H:%M")
    section.counted = outcome["status"] in ("ok", "partial")
    if outcome["status"] in ("ok", "partial"):
        section.notes.append(
            f"{T5_RUN_PREFIX}{stamp}" + (f" ({outcome['profile']})" if outcome.get("profile") else "")
            + f" · 묶음 {outcome['groups']} · 호출 {outcome['calls']}회"
        )
    if outcome.get("note"):
        section.notes.append(f"- {outcome['note']}")
    for item in outcome["conflicts"]:
        section.items.append(item)
        section.lines.append(f"- {item['a_label']} ↔ {item['b_label']}: {item['why']}")
    if outcome["status"] in ("ok", "partial") and not outcome["conflicts"]:
        section.notes.append("- 모순 후보 없음")
    return section


# ---------------------------------------------------------------- 실행


SECTION_ORDER = (
    "orphans", "stale_stubs", "similar_titles", "broken_links", "stale_cache", "outbox",
    "old_proposed", "pending_proposals", "images", "gave_up", "broken_markers", "contradictions",
)


def _render(vault: _Vault, sections: Sequence[_Section]) -> str:
    total = sum(s.count for s in sections)
    lines = [f"- 점검: {vault.now.strftime('%Y-%m-%d %H:%M')} · 항목 {total}개 · `kb.py lint`가 다시 씁니다(손으로 고치지 않음)", ""]
    for section in sections:
        lines += section.render()
    for problem in vault.problems:
        lines.append(f"- 주의: {_one_line(problem, 300)}")
    return "\n".join(lines).rstrip() + "\n"


def _write(cfg: Config, body: str, current: bytes | None) -> bool:
    """`wiki/_lint.md`의 lint 구역만 바꾼다. 바뀌었으면 True."""
    path = cfg.root / LINT_REL
    if current is None:
        _rel, template = notes.page_template("lint", "", title=None, today=core.now(cfg.tz).date().isoformat())
        base = template.encode("utf-8")
    else:
        base = current
    new = managed.replace_region(base, REGION, body)
    if new == current:
        return False
    core.atomic_write(path, new, expect_sha256=core.sha256_hex(current) if current is not None else core.MUST_NOT_EXIST)
    return True


def run(cfg: Config, *, llm: bool = False, client: LlmClient | None = None) -> dict:
    """점검 한 번. 반환: {generated, path, written, write_error, total, counts, sections, contradictions, problems, body}."""
    vault = _load_vault(cfg)
    links = [link for rel, data in sorted(vault.md.items()) for link in _links(rel, data)]
    lint_path = cfg.root / LINT_REL
    try:
        current = lint_path.read_bytes() if lint_path.is_file() else None
    except OSError as exc:
        vault.problems.append(f"{LINT_REL}를 읽지 못했습니다 ({exc})")
        current = None
    decisions = _decisions(vault.md.get("wiki/decisions.md"))

    t5 = contradictions(cfg, vault, decisions, client=client) if llm else None
    sections = [
        _orphans(vault, links),
        _stale_stubs(vault),
        _similar_titles(vault),
        _broken_links(vault, links),
        _stale_cache(vault),
        _outbox(vault),
        _old_proposed(vault, decisions),
        _pending_proposals(vault),
        _images(vault),
        _gave_up(vault),
        _broken_markers(vault),
        _t5_section(vault, t5, None if llm else _previous_t5(current)),
    ]
    body = _render(vault, sections)
    written = False
    write_error: str | None = None
    try:
        written = _write(cfg, body, current)
    except (managed.MarkerError, core.KbError, OSError) as exc:
        write_error = core.redact(str(exc))
    return {
        "generated": vault.now.isoformat(timespec="seconds"),
        "path": LINT_REL,
        "written": written,
        "write_error": write_error,
        "total": sum(s.count for s in sections),
        "counts": {s.key: s.count for s in sections},
        "sections": {s.key: {"title": s.title, "count": s.count, "items": s.items} for s in sections},
        "contradictions": (
            {k: v for k, v in t5.items() if k != "conflicts"} if t5 is not None else {"status": "not_run"}
        ),
        "problems": list(vault.problems),
        "body": body,
    }


_SUMMARY_LABELS = (
    ("orphans", "고아 페이지"),
    ("stale_stubs", "오래된 stub"),
    ("similar_titles", "유사 제목"),
    ("broken_links", "깨진 링크"),
    ("stale_cache", "오래된 캐시"),
    ("outbox", "outbox"),
    ("old_proposed", "오래된 제안"),
    ("pending_proposals", "미처리 제안"),
    ("images", "미처리 이미지"),
    ("gave_up", "포기"),
    ("broken_markers", "마커 손상"),
)


def summary_lines(result: Mapping[str, Any]) -> list[str]:
    counts = result["counts"]
    lines = ["lint: " + " · ".join(f"{label} {counts.get(key, 0)}" for key, label in _SUMMARY_LABELS)]
    t5 = result["contradictions"]
    if t5.get("status") != "not_run":
        if t5.get("status") in ("ok", "partial"):
            lines.append(f"모순 후보: {counts.get('contradictions', 0)}개 (LLM 호출 {t5.get('calls', 0)}회)")
        if t5.get("note"):
            lines.append(f"모순 후보: {t5['note']}")
    if result.get("write_error"):
        lines.append(f"주의: {LINT_REL}를 쓰지 못했습니다 ({result['write_error']}). 보고를 아래에 출력합니다")
        lines.append(result["body"].rstrip("\n"))
    else:
        lines.append(f"→ {LINT_REL}" + ("" if result.get("written") else " (바뀐 것 없음)"))
    for problem in result.get("problems") or ():
        lines.append(f"주의: {problem}")
    return lines


def main(argv: list[str]) -> int:
    parser = core.make_parser("kb.py lint", "C단계 점검: wiki/_lint.md에 보고 (보고만 하고 항상 종료 0)")
    parser.add_argument("--llm", action="store_true", help="T5 모순 후보 검사(LLM 호출, 기본 꺼짐)")
    parser.add_argument("--json", action="store_true", help="결과를 JSON 객체로 출력")
    args = parser.parse_args(argv)
    cfg = config.load(args.root)
    result = run(cfg, llm=args.llm)
    if args.json:
        payload = {k: v for k, v in result.items() if k != "body"}
        print(core.redact(json.dumps(payload, ensure_ascii=False, indent=1, default=str)))
    else:
        for line in summary_lines(result):
            print(core.redact(line))
    return 0


if __name__ == "__main__":
    core.run_main(main)
