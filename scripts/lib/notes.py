"""노트(daily·inbox) 읽기와 위키 페이지 템플릿. LLM 없이 도는 A단계의 공통 기반.

- 줄 단위 파싱(NoteLine): 날짜·시각·해시·코드 블록·front matter·관리 구역 여부.
- Jira 키 `KEY-123`(맨 글자 또는 `[[KEY-123]]`)와 사람 `@이름` 탐지. URL·인라인 코드 안은 제외.
- `jira/_views/<이름>.md` 표 읽기(Phase 2의 jira_sync가 같은 형식으로 쓴다).
- daily 뼈대와 jira/wiki 페이지 템플릿(ensure_page). 있는 파일은 절대 덮지 않는다.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from lib import core, frontmatter, managed
from lib.config import Config

BOM = b"\xef\xbb\xbf"
DAILY_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
TIME_PREFIX = re.compile(r"^- (?:\[[ xX>]\] )?(\d{2}:\d{2})(?:\s|$)")
HEADING2 = re.compile(r"^## +(.+?)\s*$")
FENCE = re.compile(r"^(```|~~~)")
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
WIKILINK_RE = re.compile(r"\[\[([^\]\|#]+)(?:#[^\]\|]*)?(?:\|[^\]]*)?\]\]")
IMAGE_EMBED_RE = re.compile(r"!\[\[([^\]\|]+)(?:\|[^\]]*)?\]\]")

VIEW_COLUMNS = ("키", "제목", "상태", "담당", "마감", "갱신")
"""`jira/_views/<이름>.md` 표의 열. 형식(Phase 2 jira_sync가 씀):

    ---
    view: today
    jql: "..."
    synced_at: 2026-11-05T12:30:00+09:00
    ---
    # today

    | 키 | 제목 | 상태 | 담당 | 마감 | 갱신 |
    |---|---|---|---|---|---|
    | [[KB-12]] | 캐시 front matter 확정 | In Progress | 홍길동 | 2026-11-06 | 2026-11-04 |
"""

_PERSON_CHARS = r"[0-9A-Za-z_À-ɏ가-힣ㄱ-ㆎ一-鿿぀-ヿ]"
_EMAIL_LOCAL = re.compile(r"[A-Za-z0-9._%+-]$")


# ---------------------------------------------------------------- 줄


@dataclass(frozen=True)
class NoteLine:
    rel: str  # 볼트 기준 상대 경로, 항상 `/` 구분
    lineno: int  # 1부터
    text: str  # 줄 끝 문자 없음
    day: str | None  # daily면 YYYY-MM-DD
    time: str | None  # `- HH:MM ` 접두가 있으면 HH:MM
    hash: str  # core.line_hash(text)
    region: str | None  # 관리 구역 안이면 구역 이름
    in_code: bool  # 코드 블록·front matter·마커 줄 (매칭·정규화 대상 아님)
    offset: int = 0  # 줄 시작의 바이트 위치(BOM 포함 원본 기준)


def decode(data: bytes) -> str:
    """BOM을 떼고 UTF-8로 해독한다. 실패하면 UnicodeDecodeError."""
    if data.startswith(BOM):
        data = data[len(BOM):]
    return data.decode("utf-8")


def day_of(rel: str) -> str | None:
    parts = rel.replace("\\", "/").split("/")
    if len(parts) == 2 and parts[0] == "daily":
        match = DAILY_NAME.match(parts[1])
        if match:
            return match.group(1)
    return None


def _region_lookup(data: bytes) -> list[tuple[str, int, int]]:
    try:
        return managed.spans(data)
    except managed.MarkerError:
        return []


def iter_lines(rel: str, data: bytes) -> Iterator[NoteLine]:
    """줄마다 NoteLine을 낸다. 해독 실패는 호출자가 처리한다(UnicodeDecodeError)."""
    rel = rel.replace("\\", "/")
    day = day_of(rel)
    spans = _region_lookup(data)
    text = decode(data)
    offset = len(BOM) if data.startswith(BOM) else 0
    in_fence = False
    in_front = False
    fence_mark = ""
    for lineno, raw in enumerate(text.splitlines(keepends=True), start=1):
        line = raw.rstrip("\r\n")
        stripped = line.strip()
        region = next((name for name, start, end in spans if start <= offset < end), None)
        is_marker = managed._MARKER.fullmatch(line.encode("utf-8")) is not None
        if lineno == 1 and stripped == "---":
            in_front = True
            in_code = True
        elif in_front:
            in_code = True
            if stripped == "---":
                in_front = False
        else:
            fence = FENCE.match(stripped)
            if fence and (not in_fence or stripped.startswith(fence_mark)):
                in_code = True
                if in_fence:
                    in_fence = False
                else:
                    in_fence = True
                    fence_mark = fence.group(1)
            else:
                in_code = in_fence or is_marker
        time_match = TIME_PREFIX.match(line)
        yield NoteLine(
            rel=rel,
            lineno=lineno,
            text=line,
            day=day,
            time=time_match.group(1) if time_match else None,
            hash=core.line_hash(line),
            region=region,
            in_code=in_code,
            offset=offset,
        )
        offset += len(raw.encode("utf-8"))


def section_span(data: bytes, heading: str) -> tuple[int, int] | None:
    """`## heading` 본문의 바이트 범위 (제목 줄 다음부터 다음 `## ` 전까지, 끝의 빈 줄 제외)."""
    text = decode(data)
    offset = len(BOM) if data.startswith(BOM) else 0
    start: int | None = None
    end: int | None = None
    last_nonblank_end: int | None = None
    for raw in text.splitlines(keepends=True):
        line = raw.rstrip("\r\n")
        match = HEADING2.match(line)
        if match:
            if start is not None:
                end = offset
                break
            if match.group(1).strip() == heading.strip():
                start = offset + len(raw.encode("utf-8"))
                last_nonblank_end = start
        elif start is not None and line.strip():
            last_nonblank_end = offset + len(raw.encode("utf-8"))
        offset += len(raw.encode("utf-8"))
    if start is None:
        return None
    if end is None:
        end = offset
    if last_nonblank_end is not None and last_nonblank_end < end:
        end = last_nonblank_end
    return (start, end)


def record_lines(rel: str, data: bytes) -> list[NoteLine]:
    """`## 기록` 절의 줄들."""
    span = section_span(data, "기록")
    if span is None:
        return []
    start, end = span
    return [line for line in iter_lines(rel, data) if start <= line.offset < end]


# ---------------------------------------------------------------- 키·사람·링크


@dataclass(frozen=True)
class KeyMatch:
    start: int  # text 안의 문자 위치
    end: int
    key: str
    bracketed: bool  # [[KEY-1]] 안에 있었는가


def _masked(text: str) -> str:
    """URL과 인라인 코드를 같은 길이의 공백으로 가린 문자열."""
    out = list(text)
    for pattern in (INLINE_CODE_RE, URL_RE):
        for match in pattern.finditer(text):
            for i in range(match.start(), match.end()):
                out[i] = " "
    return "".join(out)


def key_pattern(project_keys: list[str] | tuple[str, ...]) -> re.Pattern[str] | None:
    keys = [k for k in project_keys if k]
    if not keys:
        return None
    alternation = "|".join(re.escape(k) for k in sorted(keys, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9_\-])(?:{alternation})-\d+(?![A-Za-z0-9_])")


def find_keys(text: str, project_keys: list[str] | tuple[str, ...]) -> list[KeyMatch]:
    """프로젝트 키의 Jira 키를 찾는다. URL·인라인 코드 안은 제외. `[[KEY-1]]`·`[[KEY-1|별칭]]`은 bracketed."""
    pattern = key_pattern(project_keys)
    if pattern is None:
        return []
    masked = _masked(text)
    found: list[KeyMatch] = []
    for match in pattern.finditer(masked):
        start, end = match.start(), match.end()
        before = masked[max(0, start - 2):start]
        after = masked[end:end + 2]
        bracketed = before == "[[" and (after.startswith("]]") or after.startswith("|") or after.startswith("#"))
        found.append(KeyMatch(start, end, match.group(0), bracketed))
    return found


def find_people(text: str, prefix: str = "@") -> list[str]:
    """`@이름` 언급. 이메일(`a@b`)은 제외. 순서 유지, 중복 제거."""
    pattern = re.compile(re.escape(prefix) + rf"({_PERSON_CHARS}+)")
    masked = _masked(text)
    people: list[str] = []
    for match in pattern.finditer(masked):
        if match.start() > 0 and _EMAIL_LOCAL.search(masked[:match.start()]):
            continue
        name = match.group(1)
        if name not in people:
            people.append(name)
    return people


def wikilinks(text: str) -> list[str]:
    """`[[대상]]`·`[[대상|별칭]]`의 대상 목록(이미지 임베드 포함)."""
    return [m.group(1).strip() for m in WIKILINK_RE.finditer(text)]


def image_embeds(text: str) -> list[str]:
    return [m.group(1).strip() for m in IMAGE_EMBED_RE.finditer(text)]


# ---------------------------------------------------------------- 파일 찾기


def daily_path(cfg: Config, day: dt.date) -> Path:
    return cfg.path("daily", f"{day.isoformat()}.md")


def iter_sources(cfg: Config) -> Iterator[tuple[Path, bytes]]:
    """daily/*.md와 inbox/*.md(사이드카 포함)를 정렬해 낸다. 해독 못 하는 파일은 로그에 남기고 건너뛴다."""
    logger = core.get_logger(cfg.root)
    for folder in ("daily", "inbox"):
        base = cfg.path(folder)
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.md")):
            if not core.is_scannable(path) or not path.is_file():
                continue
            data = path.read_bytes()
            try:
                decode(data)
            except UnicodeDecodeError as exc:
                logger.warning("UTF-8이 아니라 건너뜀: %s (%s)", path, exc)
                continue
            yield path, data


def rel_path(cfg: Config, path: Path) -> str:
    return path.resolve().relative_to(cfg.root.resolve()).as_posix()


# ---------------------------------------------------------------- 템플릿


def daily_skeleton(day: dt.date) -> bytes:
    text = (
        "---\n"
        f"date: {day.isoformat()}\n"
        "---\n"
        "## 오늘\n"
        "<!-- kb:today:start -->\n"
        "<!-- kb:today:end -->\n"
        "\n"
        "## 이월\n"
        "<!-- kb:carryover:start -->\n"
        "<!-- kb:carryover:end -->\n"
        "\n"
        "## 기록\n"
    )
    return text.encode("utf-8")


def _region(name: str) -> str:
    return f"<!-- kb:{name}:start -->\n<!-- kb:{name}:end -->\n"


def page_template(kind: str, name: str, *, title: str | None, today: str) -> tuple[Path | None, str]:
    """(볼트 기준 상대 경로 조각, 본문). kind: jira·project·person·topic·decisions·actions·index·proposals·lint."""
    if kind == "jira":
        project = name.split("-", 1)[0]
        body = (
            f"---\nkey: {name}\nproject: {project}\nstub: true\nsummary: \"\"\nstatus: \"\"\n---\n"
            f"# {name}\n\n> 아직 동기화되지 않은 일감입니다. `kb.py sync --key {name}`로 받아옵니다.\n\n"
            f"## 노트에서 언급\n{_region('mentions')}"
        )
        return Path("jira") / f"{name}.md", body
    if kind == "project":
        heading = f"{name} — {title}" if title else name
        body = (
            f"---\ntype: project\nkey: {name}\ntitle: {title or name}\nupdated: {today}\n---\n"
            f"# {heading}\n\n## 개요\n\n## 현재 일감\n{_region('issues')}\n## 최근 요약\n{_region('summary')}\n## 언급\n{_region('mentions')}"
        )
        return Path("wiki") / "projects" / f"{name}.md", body
    if kind == "person":
        body = (
            f"---\ntype: person\nname: {name}\nupdated: {today}\n---\n"
            f"# {name}\n\n## 개요\n\n## 최근 요약\n{_region('summary')}\n## 언급\n{_region('mentions')}"
        )
        return Path("wiki") / "people" / f"{core.safe_name(name)}.md", body
    if kind == "topic":
        body = (
            f"---\ntype: topic\nname: {name}\naliases: []\nupdated: {today}\n---\n"
            f"# {name}\n\n## 개요\n\n## 최근 요약\n{_region('summary')}\n## 언급\n{_region('mentions')}"
        )
        return Path("wiki") / "topics" / f"{core.safe_name(name)}.md", body
    if kind == "decisions":
        return Path("wiki") / "decisions.md", f"# 결정 로그\n\n> LLM이 쓴 항목은 '제안'. 사람이 '확정'/'기각'으로 바꾼다.\n\n{_region('decisions')}"
    if kind == "actions":
        return Path("wiki") / "actions.md", f"# 할 일\n\n{_region('actions')}"
    if kind == "index":
        return Path("wiki") / "index.md", f"# 색인\n\n{_region('index')}"
    if kind == "proposals":
        return Path("wiki") / "_proposals.md", f"# 주제 제안\n\n> 승인: `python scripts/kb.py proposals accept <번호|all>`\n\n{_region('proposals')}"
    if kind == "lint":
        return Path("wiki") / "_lint.md", f"# 점검 보고\n\n{_region('lint')}"
    raise ValueError(f"알 수 없는 페이지 종류: {kind}")


def ensure_page(cfg: Config, kind: str, name: str | None = None, *, title: str | None = None) -> Path:
    """페이지가 없으면 템플릿으로 만들고, 있으면 그대로 둔 채 경로만 돌려준다."""
    today = core.now(cfg.tz).date().isoformat()
    rel, body = page_template(kind, name or "", title=title, today=today)
    path = cfg.root / rel
    if not path.exists():
        core.atomic_write(path, body.encode("utf-8"))
    return path


# ---------------------------------------------------------------- 뷰 표


def parse_table(text: str) -> list[dict[str, str]]:
    """첫 마크다운 표를 헤더 이름 → 셀 문자열의 dict 목록으로 읽는다."""
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if header is not None:
                break
            continue
        cells = [c.replace("\\|", "|").strip() for c in re.split(r"(?<!\\)\|", stripped.strip("|"))]
        if header is None:
            header = cells
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue
        rows.append({header[i]: cells[i] if i < len(cells) else "" for i in range(len(header))})
    return rows


def read_view(cfg: Config, name: str) -> list[dict[str, str]]:
    """`jira/_views/<name>.md`의 행. 각 행에 `key`(괄호 없는 키)를 더한다. 파일이 없으면 []."""
    path = cfg.path("jira", "_views", f"{name}.md")
    if not path.exists():
        return []
    _front, body = frontmatter.split(path.read_bytes())
    rows = parse_table(decode(body))
    for row in rows:
        raw = row.get("키", "")
        links = wikilinks(raw)
        row["key"] = links[0] if links else raw.strip()
    return rows
