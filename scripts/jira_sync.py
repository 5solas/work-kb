"""Jira 읽기 동기화 (`kb.py sync`, 별칭 `jira_sync`). Phase 2, 지시서 §6.2, D23·D49·D50·D51·D63.

- 범위: `project in ("KB","OPS") AND (<jql_mine>)`(D49). 증분은 `AND updated >= '-Nm'`,
  N = 마지막 동기화(실행 시작 시각) 뒤 지난 분 + 5(D50). 다음이면 전체: `--full`, 검색 캐시 없음,
  범위(project_keys·jql_mine)가 지난 동기화와 다름, 마지막 동기화가 없거나 25일보다 오래됐거나 미래(시계가
  뒤로 감), 마지막 전체 동기화가 `jira.full_every_hours`(기본 24)시간보다 오래됨.
- 전체 동기화는 범위가 돌려주지 않은 로컬 일감(동기화됨·stub 아님·완료 아님)을 `key in (…)`(범위로 감싸지
  않음, 50개씩)으로 다시 받아 고친 뒤 front matter `in_scope: false`를 단다. Jira에 없으면(404) 기존 파일에
  `in_scope: false`만, 다른 키로 옮겨졌으면 `moved_to: 새 키`도 달고 새 키 파일을 쓴다. `in_scope: false`인
  일감은 프로젝트 표와 검색 캐시에서 빠진다. `--key KB-12`가 OPS-40을 받으면 KB-12.md에 같은 표시를 한다.
- 이슈마다 `jira/KEY.md`(§5.3)를 통째로 다시 만든다. `mentions` 구역만 바이트 그대로 둔다(D23).
  내용이 그대로면 쓰지 않는다(`synced_at`만 바뀌는 쓰기를 하지 않음).
- `jira.views` → `jira/_views/<이름>.md` 표(notes.VIEW_COLUMNS 형식). 뷰 JQL이 400이면 Jira의
  문장을 알리고 이전 파일을 둔 채 나머지를 마친 뒤 종료 4.
- 프로젝트 페이지(`wiki/projects/KEY.md`)의 `issues` 구역: 열린 일감(상태 범주 done 제외)을
  갱신 시각 내림차순으로. 구역이 바뀐 때만 front matter `updated`를 오늘로.
- 검색 캐시 `jira/.cache.sqlite`(lib/fts.py).
- `.state/sync.json`: {last_sync, last_full, last_run, scope, counts}. `scope`는 범위의 해시. 쓰지 못한
  이슈 파일이 있으면 last_sync·scope를 옮기지 않는다(다음 증분이 같은 구간을 다시 받는다).

네트워크는 잠금 밖에서 모두 끝내고, 쓰기만 `core.kb_lock` 안에서 한다(D26). daily·inbox·raw는
건드리지 않는다. 종료 코드: 0 / 2 인증 / 3 네트워크 / 4 설정·JQL·없는 키 / 6 잠금.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, TextIO

from lib import config, core, frontmatter, fts, managed, notes, wikimarkup
from lib.config import Config
from lib.jira_client import JiraClient, JqlError, NotFoundError, parse_datetime, parse_sprint

STATE = "sync"
LOCK_OWNER = "jira_sync"
FULL_AFTER = dt.timedelta(days=25)
FULL_EVERY_HOURS = 24  # jira.full_every_hours 기본값: 마지막 전체 동기화가 이보다 오래면 전체
OUT_OF_SCOPE_BATCH = 50  # 범위 밖 일감을 다시 받을 때 `key in (…)` 하나에 넣는 키 수
MARGIN_MINUTES = 5
COMMENT_CHARS = 200
BOM = b"\xef\xbb\xbf"
KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]+-[1-9][0-9]*$")
VIEW_NAME_RE = re.compile(r"^[\w\-]+$")
VIEW_FIELDS = ["summary", "status", "assignee", "duedate", "updated", "project"]
# 생성한 본문에 관리 구역 표시와 같은 줄이 들어가면 앞에 공백을 붙여 표시가 아니게 한다
_MARKER_LINE = re.compile(r"^(?=<!-- kb:[a-z_]+:(?:start|end) -->[ \t\r]*$)", re.MULTILINE)
NO_DESCRIPTION = "(설명 없음)"
NO_COMMENTS = "(댓글 없음)"


# ---------------------------------------------------------------- 이슈 → 문서


@dataclass
class IssueDoc:
    key: str
    project: str
    project_name: str
    summary: str
    status: str
    status_category: str
    issuetype: str
    priority: str
    assignee: str
    reporter: str
    sprint: str
    labels: list[str]
    components: list[str]
    created: str
    updated: str
    due: str
    url: str
    description: str  # 마크다운(변환 실패 시 원문 울타리)
    comment_lines: list[str]
    comment_text: str  # 검색 캐시용 댓글 전문
    in_scope: bool = True  # False면 front matter `in_scope: false`(프로젝트 표·캐시에서 빠짐)

    def record(self) -> dict[str, str]:
        """표·캐시에 쓰는 요약."""
        return {
            "key": self.key,
            "project": self.project,
            "summary": self.summary,
            "status": self.status,
            "status_category": self.status_category,
            "assignee": self.assignee,
            "updated": self.updated,
            "due": self.due,
        }

    def cache_row(self) -> dict[str, str]:
        row = self.record()
        row["body"] = f"{self.description}\n\n{self.comment_text}".strip()
        return row


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _one_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _person(value: Any) -> str:
    if isinstance(value, Mapping):
        return _text(value.get("displayName")) or _text(value.get("name"))
    return ""


def _named(value: Any) -> str:
    return _text(value.get("name")) if isinstance(value, Mapping) else ""


def _iso(value: Any) -> str:
    """Jira 시각 → ISO 8601(초 단위, Jira가 준 오프셋 그대로). 해석 못 하면 원문."""
    parsed = parse_datetime(value)
    if parsed is None:
        return _text(value)
    return parsed.isoformat(timespec="seconds")


def _local(value: Any, tz: dt.tzinfo) -> dt.datetime | None:
    parsed = parse_datetime(value) if not isinstance(value, dt.datetime) else value
    if parsed is None and isinstance(value, str) and value.strip():
        try:
            parsed = dt.datetime.fromisoformat(value.strip())
        except ValueError:
            parsed = None
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed.astimezone(tz)


def _date_of(value: Any, tz: dt.tzinfo) -> str:
    local = _local(value, tz)
    return local.date().isoformat() if local is not None else _text(value)[:10]


def neutralize(text: str) -> str:
    """관리 구역 표시처럼 보이는 줄 앞에 공백을 붙인다(Jira 본문이 구역을 만들지 못하게)."""
    return _MARKER_LINE.sub(" ", text)


_HEADING = re.compile(r"^(#{1,6})(?=\s)")
_FENCE = re.compile(r"^\s*(```|~~~)")


def demote_headings(markdown: str, levels: int = 2) -> str:
    """설명 안의 제목을 levels만큼 내린다(최대 ######). 파일의 `## 설명`·`## 댓글` 구조와 섞이지 않게.

    코드 울타리 안은 건드리지 않는다.
    """
    out: list[str] = []
    fence: str | None = None
    for line in markdown.split("\n"):
        match = _FENCE.match(line)
        if match:
            if fence is None:
                fence = match.group(1)
            elif match.group(1) == fence:
                fence = None
        elif fence is None:
            line = _HEADING.sub(lambda m: "#" * min(6, len(m.group(1)) + levels), line)
        out.append(line)
    return "\n".join(out)


def _first_line(body: str) -> str:
    """댓글 본문(wiki markup)의 첫 줄을 마크다운으로. 울타리 줄·변환 실패 안내는 건너뛴다."""
    text = body.replace("\r\n", "\n").replace("\r", "\n")
    converted = wikimarkup.convert_or_raw(text) if text.strip() else ""
    for line in notes.split_lines(converted):
        stripped = line.strip()
        if not stripped or _FENCE.match(stripped) or stripped == wikimarkup.FAIL_NOTE:
            continue
        return _one_line(stripped)
    return ""


def _comment_line(comment: Mapping[str, Any], tz: dt.tzinfo) -> str:
    local = _local(comment.get("created"), tz)
    when = local.strftime("%Y-%m-%d %H:%M") if local is not None else "????-??-?? ??:??"
    author = _person(comment.get("author")) or "(알 수 없음)"
    first = _first_line(str(comment.get("body") or ""))
    if len(first) > COMMENT_CHARS:
        first = first[: COMMENT_CHARS - 1] + "…"
    return f"- {when} {_one_line(author)}: {first}"


def build_doc(
    issue: Mapping[str, Any],
    comments: list[Mapping[str, Any]],
    *,
    base_url: str,
    sprint_field: str | None,
    tz: dt.tzinfo,
) -> IssueDoc:
    """검색·이슈 응답 하나와 댓글 목록으로 IssueDoc을 만든다."""
    key = _text(issue.get("key")).upper()
    if not KEY_RE.match(key):
        raise core.KbError(f"Jira 응답의 이슈 키가 올바르지 않습니다: {issue.get('key')!r}")
    fields = issue.get("fields") if isinstance(issue.get("fields"), Mapping) else {}
    status = fields.get("status") if isinstance(fields.get("status"), Mapping) else {}
    category = status.get("statusCategory") if isinstance(status.get("statusCategory"), Mapping) else {}
    project = fields.get("project") if isinstance(fields.get("project"), Mapping) else {}
    raw_description = fields.get("description")
    description = ""
    if _text(raw_description):
        # 홀로 선 CR도 줄 경계로 맞춘다(managed는 CR 전용 줄끝도 줄로 본다, neutralize가 놓치지 않게)
        description = wikimarkup.convert_or_raw(raw_description.replace("\r\n", "\n").replace("\r", "\n"))
        description = demote_headings(description.replace("\r\n", "\n").replace("\r", "\n"))
    texts = []
    for comment in comments:
        body = str(comment.get("body") or "").strip()
        if body:
            texts.append(f"{_person(comment.get('author'))}: {body}")
    # front matter의 글자 값은 모두 한 줄로(NEL·U+2028·U+2029·CR·LF → 공백). YAML이 줄바꿈 문자를 접어
    # 다시 읽은 값이 Jira 값과 달라지지 않게.
    labels = (_one_line(x) for x in fields.get("labels") or [])
    components = (_one_line(_named(c)) for c in fields.get("components") or [])
    return IssueDoc(
        key=key,
        project=_one_line(_text(project.get("key"))) or key.split("-", 1)[0],
        project_name=_one_line(_text(project.get("name"))),
        summary=_one_line(fields.get("summary")),
        status=_one_line(_named(status)),
        status_category=_one_line(_text(category.get("key"))),
        issuetype=_one_line(_named(fields.get("issuetype"))),
        priority=_one_line(_named(fields.get("priority"))),
        assignee=_one_line(_person(fields.get("assignee"))),
        reporter=_one_line(_person(fields.get("reporter"))),
        sprint=_one_line(parse_sprint(fields.get(sprint_field))) if sprint_field else "",
        labels=[x for x in labels if x],
        components=[x for x in components if x],
        created=_one_line(_iso(fields.get("created"))),
        updated=_one_line(_iso(fields.get("updated"))),
        due=_one_line(_text(fields.get("duedate"))),
        url=f"{base_url.rstrip('/')}/browse/{key}",
        description=description.strip("\n"),
        comment_lines=[_comment_line(c, tz) for c in comments],
        comment_text="\n".join(texts),
    )


def render_doc(doc: IssueDoc, stamp: dt.datetime) -> str:
    """§5.3 형식의 본문(LF). `mentions` 구역은 비어 있다."""
    fields = {
        "key": doc.key,
        "summary": doc.summary,
        "status": doc.status,
        "status_category": doc.status_category,
        "issuetype": doc.issuetype,
        "priority": doc.priority,
        "assignee": doc.assignee,
        "reporter": doc.reporter,
        "project": doc.project,
        "sprint": doc.sprint,
        "labels": doc.labels,
        "components": doc.components,
        "created": doc.created,
        "updated": doc.updated,
        "due": doc.due,
        "url": doc.url,
        "in_scope": None if doc.in_scope else False,  # 범위 안이면 쓰지 않는다(기존 파일 그대로)
        "synced_at": stamp.isoformat(timespec="seconds"),
    }
    head = frontmatter.render(fields).decode("utf-8")
    title = f"# {doc.key} {doc.summary}".rstrip()
    description = neutralize(doc.description) if doc.description else NO_DESCRIPTION
    comments = "\n".join(neutralize(line) for line in doc.comment_lines) if doc.comment_lines else NO_COMMENTS
    return (
        f"{head}{title}\n\n"
        f"> 동기화: {stamp.strftime('%Y-%m-%d %H:%M')}. 이 파일은 자동 생성되며 손으로 고치면 덮입니다.\n\n"
        f"## 설명\n{description}\n\n"
        f"## 댓글\n{comments}\n\n"
        "## 노트에서 언급\n<!-- kb:mentions:start -->\n<!-- kb:mentions:end -->\n"
    )


def _old_stamp(old: bytes, tz: dt.tzinfo) -> dt.datetime | None:
    try:
        value = frontmatter.parse(old).get("synced_at")
    except core.KbError:
        return None
    local = _local(value, tz) if isinstance(value, str) else None
    return local


def _keep_region(new: bytes, name: str, body: bytes | None) -> bytes:
    """new의 (빈) name 구역 본문 자리에 body 바이트를 그대로 끼운다."""
    if not body:
        return new
    for region, start, end in managed.spans(new):
        if region == name:
            return new[:start] + body + new[end:]
    return new


def compose(
    render: Callable[[dt.datetime], str],
    stamp: dt.datetime,
    old: bytes | None,
    tz: dt.tzinfo,
    *,
    keep: str | None = None,
) -> bytes | None:
    """새 파일 바이트. 이전 파일과 내용이 같으면(동기화 시각만 다르면) None.

    이전 파일의 줄끝(EOL)·BOM을 따르고, keep 구역(이슈는 mentions)은 바이트 그대로 옮긴다.
    이전 파일의 관리 구역 표시가 잘못됐으면 managed.MarkerError.
    """
    eol = managed.detect_eol(old) if old else b"\n"
    head = BOM if old and old.startswith(BOM) else b""
    kept = managed.read_region(old, keep) if (old is not None and keep) else None

    def make(at: dt.datetime) -> bytes:
        data = render(at).encode("utf-8")
        if eol != b"\n":
            data = data.replace(b"\n", eol)
        if keep:
            data = _keep_region(data, keep, kept)
        return head + data

    if old is not None:
        previous = _old_stamp(old, tz)
        if previous is not None and make(previous) == old:
            return None
    return make(stamp)


# ---------------------------------------------------------------- 쓰기 보고


@dataclass
class Report:
    created: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)

    def merge(self, other: "Report") -> None:
        self.created += other.created
        self.changed += other.changed
        self.unchanged += other.unchanged
        self.skipped += other.skipped

    @property
    def written(self) -> list[str]:
        return self.created + self.changed


def _write(path: Path, rel: str, new: bytes | None, old: bytes | None, report: Report, *, dry_run: bool) -> None:
    if new is None:
        report.unchanged.append(rel)
        return
    if not dry_run:
        expect = core.MUST_NOT_EXIST if old is None else core.sha256_hex(old)
        try:
            core.atomic_write(path, new, expect_sha256=expect)
        except (core.ConcurrentEdit, core.FileBusy) as exc:
            report.skipped.append((rel, f"쓰지 못함: {exc}"))
            return
    (report.created if old is None else report.changed).append(rel)


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def write_issues(cfg: Config, docs: Iterable[IssueDoc], stamp: dt.datetime, *, dry_run: bool = False) -> Report:
    """jira/KEY.md들을 쓴다(잠금은 호출자가). mentions는 바이트 그대로 둔다."""
    report = Report()
    tz = cfg.tz
    for doc in docs:
        rel = f"jira/{doc.key}.md"
        path = cfg.path("jira", f"{doc.key}.md")
        try:
            old = _read(path)
            new = compose(lambda at, doc=doc: render_doc(doc, at), stamp, old, tz, keep="mentions")
        except managed.MarkerError as exc:
            report.skipped.append((rel, f"관리 구역 표시 오류라 고치지 않음: {exc}"))
            continue
        except OSError as exc:
            report.skipped.append((rel, f"읽지 못함: {exc}"))
            continue
        _write(path, rel, new, old, report, dry_run=dry_run)
    return report


def mark_files(cfg: Config, marks: Mapping[str, Mapping[str, Any]], *, dry_run: bool = False) -> Report:
    """기존 jira/KEY.md의 front matter에 표시(`in_scope: false`, `moved_to`)만 단다. 파일이 없으면 넘어간다."""
    report = Report()
    for key in sorted(marks):
        rel = f"jira/{key}.md"
        path = cfg.path("jira", f"{key}.md")
        try:
            old = _read(path)
            if old is None:
                continue
            new = frontmatter.set_fields(old, marks[key])
        except OSError as exc:
            report.skipped.append((rel, f"읽지 못함: {exc}"))
            continue
        except core.KbError as exc:
            report.skipped.append((rel, f"front matter 오류라 고치지 않음: {exc}"))
            continue
        _write(path, rel, None if new == old else new, old, report, dry_run=dry_run)
    return report


def update_cache(cfg: Config, docs: Iterable[IssueDoc], *, drop: Iterable[str] = ()) -> int:
    """범위 안 문서는 넣거나 바꾸고, 범위 밖 문서와 drop 키는 지운다."""
    docs = list(docs)
    rows = [doc.cache_row() for doc in docs if doc.in_scope]
    gone = sorted({doc.key for doc in docs if not doc.in_scope} | set(drop))
    if not rows and not gone:
        return 0
    with fts.open_cache(cfg) as cache:
        if gone:
            cache.delete(gone)
        return cache.upsert(rows)


# ---------------------------------------------------------------- 표 (뷰·프로젝트)


def _cell(value: Any) -> str:
    return _one_line(value).replace("|", "\\|")


def render_table(rows: Iterable[Mapping[str, str]], tz: dt.tzinfo) -> str:
    """notes.VIEW_COLUMNS 표. 키 칸은 `[[KEY]]`, 갱신은 날짜(설정 시간대)."""
    lines = ["| " + " | ".join(notes.VIEW_COLUMNS) + " |", "|" + "---|" * len(notes.VIEW_COLUMNS)]
    for row in rows:
        cells = [
            f"[[{row['key']}]]",
            _cell(row.get("summary")),
            _cell(row.get("status")),
            _cell(row.get("assignee")),
            _cell(row.get("due")),
            _cell(_date_of(row.get("updated"), tz)),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _view_row(issue: Mapping[str, Any]) -> dict[str, str]:
    fields = issue.get("fields") if isinstance(issue.get("fields"), Mapping) else {}
    return {
        "key": _text(issue.get("key")).upper(),
        "summary": _one_line(fields.get("summary")),
        "status": _named(fields.get("status")),
        "assignee": _person(fields.get("assignee")),
        "due": _text(fields.get("duedate")),
        "updated": _text(fields.get("updated")),
    }


@dataclass
class ViewResult:
    name: str
    jql: str
    rows: list[dict[str, str]] | None = None
    error: str | None = None


def configured_views(cfg: Config) -> list[tuple[str, Any]]:
    views = cfg.get("jira.views") or {}
    if not isinstance(views, Mapping):
        raise core.ConfigError("jira.views: '이름: JQL' 묶음이어야 합니다")
    return [(str(name), jql) for name, jql in views.items()]


def fetch_views(cfg: Config, client: JiraClient) -> list[ViewResult]:
    """뷰마다 따로 묻는다. JQL 400·이름 오류는 그 뷰의 error에 담고 계속한다(D49)."""
    out: list[ViewResult] = []
    for name, jql in configured_views(cfg):
        result = ViewResult(name, jql if isinstance(jql, str) else "")
        if not VIEW_NAME_RE.match(name) or name.startswith((".", "-")):
            result.error = "뷰 이름은 글자·숫자·_·-만 쓸 수 있습니다"
        elif not isinstance(jql, str) or not jql.strip():
            result.error = "JQL이 비어 있습니다"
        else:
            try:
                result.rows = [_view_row(issue) for issue in client.search(jql, fields=VIEW_FIELDS)]
            except JqlError as exc:
                result.error = "; ".join(exc.messages) or str(exc)
        out.append(result)
    return out


def render_view(view: ViewResult, tz: dt.tzinfo) -> Callable[[dt.datetime], str]:
    def render(at: dt.datetime) -> str:
        head = frontmatter.render({"view": view.name, "jql": view.jql, "synced_at": at.isoformat(timespec="seconds")})
        return f"{head.decode('utf-8')}# {view.name}\n\n{render_table(view.rows or [], tz)}"

    return render


def write_views(cfg: Config, views: Iterable[ViewResult], stamp: dt.datetime, *, dry_run: bool = False) -> Report:
    """오류 없는 뷰만 쓴다. 오류 난 뷰의 이전 파일은 그대로 둔다."""
    report = Report()
    for view in views:
        if view.error is not None:
            continue
        rel = f"jira/_views/{view.name}.md"
        path = cfg.path("jira", "_views", f"{view.name}.md")
        try:
            old = _read(path)
            new = compose(render_view(view, cfg.tz), stamp, old, cfg.tz)
        except OSError as exc:
            report.skipped.append((rel, f"읽지 못함: {exc}"))
            continue
        _write(path, rel, new, old, report, dry_run=dry_run)
    return report


def _out_of_scope(meta: Mapping[str, Any]) -> bool:
    return str(meta.get("in_scope", "")).strip().lower() == "false"


def _disk_records(cfg: Config) -> dict[str, dict[str, str]]:
    """jira/*.md front matter의 요약(동기화된 것만, stub·`in_scope: false` 제외)."""
    out: dict[str, dict[str, str]] = {}
    base = cfg.path("jira")
    if not base.is_dir():
        return out
    for path in sorted(base.glob("*.md")):
        if not core.is_scannable(path) or not KEY_RE.match(path.stem):
            continue
        try:
            meta = frontmatter.parse(path.read_bytes())
        except (OSError, core.KbError):
            continue
        if str(meta.get("stub", "")).strip().lower() == "true" or not meta.get("status_category") or _out_of_scope(meta):
            continue
        out[path.stem] = {name: str(meta.get(name) or "") for name in ("key", "project", "summary", "status", "status_category", "assignee", "updated", "due")}
        out[path.stem]["key"] = path.stem
    return out


def _sort_stamp(value: str) -> float:
    try:
        parsed = dt.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return float("-inf")
    if parsed.tzinfo is None:
        return float("-inf")
    return parsed.timestamp()


def project_rows(records: Mapping[str, Mapping[str, str]], project: str) -> list[Mapping[str, str]]:
    """열린(상태 범주 done 아님, 범위 안) 일감, 갱신 내림차순(같으면 키 순)."""
    rows = [
        r
        for r in records.values()
        if r.get("project") == project and r.get("status_category") != "done" and not _out_of_scope(r)
    ]
    rows.sort(key=lambda r: r["key"])
    rows.sort(key=lambda r: _sort_stamp(r.get("updated", "")), reverse=True)
    return rows


def update_projects(
    cfg: Config,
    projects: Iterable[str],
    docs: Iterable[IssueDoc],
    *,
    exclude: Iterable[str] = (),
    dry_run: bool = False,
) -> Report:
    """프로젝트 페이지의 `issues` 구역. 없으면 notes.ensure_page로 만든다(시험 실행이면 만들지 않음).

    범위 밖 문서(in_scope False)와 exclude 키(없어짐·옮겨짐 표시를 단 파일)는 표에서 뺀다.
    """
    report = Report()
    docs = list(docs)
    records = _disk_records(cfg)
    for doc in docs:
        if doc.in_scope:
            records[doc.key] = doc.record()
        else:
            records.pop(doc.key, None)
    for key in exclude:
        records.pop(key, None)
    names = {doc.project: doc.project_name for doc in docs if doc.project_name}
    today = core.now(cfg.tz).date().isoformat()
    for project in projects:
        rel = f"wiki/projects/{project}.md"
        path = cfg.path("wiki", "projects", f"{project}.md")
        table = render_table(project_rows(records, project), cfg.tz)
        if not path.exists():
            if dry_run:
                report.created.append(rel)
                continue
            notes.ensure_page(cfg, "project", project, title=names.get(project))
        try:
            old = path.read_bytes()
            new = managed.replace_region(old, "issues", table)
            if new != old and frontmatter.split(new)[0]:
                new = frontmatter.set_fields(new, {"updated": today})
        except managed.MarkerError as exc:
            report.skipped.append((rel, f"관리 구역 표시 오류라 고치지 않음: {exc}"))
            continue
        except (OSError, core.KbError) as exc:
            report.skipped.append((rel, f"읽지 못함: {exc}"))
            continue
        _write(path, rel, None if new == old else new, old, report, dry_run=dry_run)
    return report


# ---------------------------------------------------------------- 범위·증분


def quote_jql(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def scope_jql(cfg: Config) -> str:
    """`project in ("KB","OPS") AND (<jql_mine>)` (D49)."""
    keys = cfg.project_keys
    if not keys:
        raise core.ConfigError("jira.project_keys가 비어 있습니다. `python scripts/kb.py init --section jira`로 프로젝트를 고르세요")
    mine = cfg.get("jira.jql_mine")
    if not isinstance(mine, str) or not mine.strip():
        raise core.ConfigError("jira.jql_mine이 비어 있습니다. config.yaml을 확인하세요")
    return f"project in ({','.join(quote_jql(k) for k in keys)}) AND ({mine.strip()})"


def scope_hash(cfg: Config) -> str:
    """범위(정렬한 project_keys, jql_mine)의 해시. 바뀌면 다음 동기화는 전체."""
    mine = cfg.get("jira.jql_mine")
    data = json.dumps([sorted(cfg.project_keys), mine.strip() if isinstance(mine, str) else ""], ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def full_every(cfg: Config) -> dt.timedelta:
    """`jira.full_every_hours`(기본 24): 마지막 전체 동기화가 이보다 오래면 전체."""
    raw = cfg.get("jira.full_every_hours")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raw = FULL_EVERY_HOURS
    try:
        hours = float(raw) if not isinstance(raw, bool) else math.nan
    except (TypeError, ValueError):
        hours = math.nan
    if not math.isfinite(hours) or hours <= 0:
        raise core.ConfigError(f"jira.full_every_hours: 0보다 큰 시간(숫자)이어야 합니다 (지금 값: {raw!r})")
    return dt.timedelta(hours=hours)


def _state_time(state: Mapping[str, Any], name: str, tz: dt.tzinfo) -> dt.datetime | None:
    value = state.get(name)
    return _local(value, tz) if isinstance(value, str) else None


def plan_scope(cfg: Config, state: Mapping[str, Any], start: dt.datetime, *, full: bool) -> tuple[str, str]:
    """(종류 'full'|'incremental', JQL). D50."""
    base = scope_jql(cfg)
    every = full_every(cfg)
    if full:
        return "full", base
    if not fts.cache_path(cfg).exists():
        return "full", base
    if state.get("scope") != scope_hash(cfg):  # 프로젝트·jql_mine이 바뀜(또는 기록 없음) → 새 범위를 다 받는다
        return "full", base
    last = _state_time(state, "last_sync", cfg.tz)
    # 미래의 last_sync(시계가 뒤로 감·다른 기계의 상태)는 믿지 않는다
    if last is None or last > start or start - last > FULL_AFTER:
        return "full", base
    last_full = _state_time(state, "last_full", cfg.tz)
    if last_full is None or last_full > start or start - last_full > every:
        return "full", base
    minutes = max(0, math.ceil((start - last).total_seconds() / 60)) + MARGIN_MINUTES
    return "incremental", f"{base} AND updated >= '-{minutes}m'"


# ---------------------------------------------------------------- 실행


def _docs_for(cfg: Config, client: JiraClient, issues: Iterable[Mapping[str, Any]]) -> list[IssueDoc]:
    sprint_field = client.sprint_field_id()
    return [
        build_doc(issue, client.issue_comments(issue), base_url=client.base_url, sprint_field=sprint_field, tz=cfg.tz)
        for issue in issues
    ]


def fetch_doc(cfg: Config, client: JiraClient, key: str) -> IssueDoc:
    key = key.strip().upper()
    if not KEY_RE.match(key):
        raise core.ConfigError(f"이슈 키 형식이 아닙니다: {key!r}. 예: KB-12")
    issue = client.issue(key, fields=client.sync_fields())
    return _docs_for(cfg, client, [issue])[0]


def _moved_mark(new_key: str) -> dict[str, Any]:
    return {"in_scope": False, "moved_to": new_key}


def fetch_out_of_scope(
    cfg: Config, client: JiraClient, returned: Iterable[str]
) -> tuple[list[IssueDoc], dict[str, dict[str, Any]]]:
    """전체 동기화가 돌려주지 않은 로컬 일감(동기화됨·stub 아님·완료 아님)을 다시 받는다(잠금 밖, 네트워크만).

    `key in (…)`로 묻는다(범위·jql_mine으로 감싸지 않음, OUT_OF_SCOPE_BATCH개씩). 없어진 키가 섞이면
    Jira가 400을 주므로 그 묶음은 키마다 issue()로 묻는다. 돌려주는 것:
    - 다시 받은 문서(in_scope False). 옮겨진 일감의 새 키 문서도(새 키가 범위 안이면 빼고).
    - 표시: {옛 키: {in_scope: False}} (404) 또는 {옛 키: {in_scope: False, moved_to: 새 키}}.
    """
    returned = {key.upper() for key in returned}
    records = _disk_records(cfg)
    candidates = sorted(k for k, r in records.items() if r.get("status_category") != "done" and k not in returned)
    found: dict[str, Mapping[str, Any]] = {}
    marks: dict[str, dict[str, Any]] = {}
    if not candidates:
        return [], marks
    fields = client.sync_fields()
    for i in range(0, len(candidates), OUT_OF_SCOPE_BATCH):
        chunk = candidates[i : i + OUT_OF_SCOPE_BATCH]
        try:
            issues = list(client.search(f"key in ({', '.join(chunk)})", fields=fields))
        except JqlError:
            issues = []  # 없어진 키가 있다 → 아래에서 하나씩
        got = {_text(issue.get("key")).upper(): issue for issue in issues if isinstance(issue, Mapping)}
        for key in chunk:
            if key in got:
                found[key] = got[key]
                continue
            try:
                issue = client.issue(key, fields=fields)
            except NotFoundError:
                marks[key] = {"in_scope": False}  # 지워졌거나 볼 권한이 없다
                continue
            new_key = _text(issue.get("key")).upper()
            if new_key == key:
                found[key] = issue
            elif KEY_RE.match(new_key):
                marks[key] = _moved_mark(new_key)
                if new_key not in returned:
                    found.setdefault(new_key, issue)
            else:
                marks[key] = {"in_scope": False}
    docs = _docs_for(cfg, client, list(found.values()))
    for doc in docs:
        doc.in_scope = False
    return docs, marks


def _write_docs(
    cfg: Config,
    docs: list[IssueDoc],
    stamp: dt.datetime,
    projects: Iterable[str],
    *,
    marks: Mapping[str, Mapping[str, Any]] | None = None,
) -> Report:
    """잠금 안에서 이슈 파일·표시 → 캐시 → 프로젝트 페이지. 쓰지 못한 파일은 캐시에 반영하지 않는다."""
    marks = dict(marks or {})
    report = write_issues(cfg, docs, stamp)
    report.merge(mark_files(cfg, marks))
    failed = {rel for rel, _ in report.skipped}
    update_cache(
        cfg,
        [doc for doc in docs if f"jira/{doc.key}.md" not in failed],
        drop=[key for key in marks if f"jira/{key}.md" not in failed],
    )
    report.merge(update_projects(cfg, projects, docs, exclude=marks))
    return report


def sync_key(cfg: Config, client: JiraClient, key: str) -> Path:
    """계약(부록 A §2): 이슈 하나를 받아 jira/KEY.md를 쓰고 경로를 돌려준다.

    없는 키는 NotFoundError. 그 파일을 쓰지 못하면 KbError(종료 1). 일감이 다른 키로 옮겨졌으면
    새 키 파일을 쓰고 그 경로를 돌려주며, 옛 키 파일이 있으면 `in_scope: false`·`moved_to: 새 키`를 단다.
    """
    doc = fetch_doc(cfg, client, key)
    stamp = core.now(cfg.tz)
    requested = key.strip().upper()
    marks: dict[str, dict[str, Any]] = {}
    if doc.key != requested and cfg.path("jira", f"{requested}.md").exists():
        marks[requested] = _moved_mark(doc.key)
    touched = {doc.project, requested.split("-", 1)[0]}
    projects = [p for p in cfg.project_keys if p in touched]
    with core.kb_lock(cfg.root, LOCK_OWNER):
        report = _write_docs(cfg, [doc], stamp, projects, marks=marks)
    rel = f"jira/{doc.key}.md"
    for skipped_rel, reason in report.skipped:
        if skipped_rel == rel:
            raise core.KbError(f"{rel}: {reason}")
    return cfg.path("jira", f"{doc.key}.md")


def _log(cfg: Config, dry_run: bool) -> Any:
    return None if dry_run else core.get_logger(cfg.root)


def _print_report(report: Report, out: TextIO, *, dry_run: bool) -> None:
    if dry_run:
        for label, items in (("만들 예정", report.created), ("바뀔 예정", report.changed)):
            for rel in items[:30]:
                print(f"  {label}: {rel}", file=out)
            if len(items) > 30:
                print(f"  … {label} {len(items) - 30}개 더", file=out)
    for rel, reason in report.skipped:
        print(f"건너뜀: {rel}: {reason}", file=sys.stderr)


def run_scope(cfg: Config, client: JiraClient, *, full: bool, dry_run: bool, out: TextIO) -> int:
    start = core.now(cfg.tz)
    state = core.load_state(cfg.root, STATE)
    kind, jql = plan_scope(cfg, state, start, full=full)
    issues = list(client.search(jql, fields=client.sync_fields()))
    docs = _docs_for(cfg, client, issues)
    extra: list[IssueDoc] = []
    marks: dict[str, dict[str, Any]] = {}
    if kind == "full":  # 범위를 벗어난 로컬 일감을 고쳐 표시한다(재배정·지움·옮김)
        extra, marks = fetch_out_of_scope(cfg, client, {doc.key for doc in docs})
    all_docs = docs + extra
    views = fetch_views(cfg, client)
    logger = _log(cfg, dry_run)
    for view in views:
        if view.error is not None:
            print(f"뷰 '{view.name}' 건너뜀(이전 파일 유지): {view.error}", file=sys.stderr)
            if logger is not None:
                logger.warning("jira_sync 뷰 '%s' 오류: %s (JQL: %s)", view.name, view.error, view.jql)

    report = Report()
    if dry_run:
        report.merge(write_issues(cfg, all_docs, start, dry_run=True))
        report.merge(mark_files(cfg, marks, dry_run=True))
        view_report = write_views(cfg, views, start, dry_run=True)
        report.merge(view_report)
        report.merge(update_projects(cfg, cfg.project_keys, all_docs, exclude=marks, dry_run=True))
    else:
        with core.kb_lock(cfg.root, LOCK_OWNER):
            report.merge(_write_docs(cfg, all_docs, start, cfg.project_keys, marks=marks))
            issue_failed = any(rel.startswith("jira/") and not rel.startswith("jira/_views/") for rel, _ in report.skipped)
            report.merge(write_views(cfg, views, start))
            counts = {
                "issues": len(docs),
                "out_of_scope": len(extra) + len(marks),
                "written": len(report.written),
                "unchanged": len(report.unchanged),
                "skipped": len(report.skipped),
                "views": {v.name: (len(v.rows) if v.rows is not None else "error") for v in views},
            }
            new_state = dict(state)
            new_state["counts"] = counts
            new_state["last_run"] = start.isoformat(timespec="seconds")
            if not issue_failed:  # 쓰지 못한 이슈가 있으면 다음 증분이 같은 구간을 다시 받게 둔다
                new_state["last_sync"] = start.isoformat(timespec="seconds")
                new_state["scope"] = scope_hash(cfg)
                if kind == "full":
                    new_state["last_full"] = start.isoformat(timespec="seconds")
            core.save_state(cfg.root, STATE, new_state)
        if logger is not None:
            for rel, reason in report.skipped:
                logger.warning("jira_sync 건너뜀: %s: %s", rel, reason)

    errors = [v for v in views if v.error is not None]
    head = "sync(시험 실행, 쓰지 않음)" if dry_run else "sync"
    label = "전체" if kind == "full" else "증분"
    outside = f" · 범위 밖 정리 {len(extra) + len(marks)}" if (extra or marks) else ""
    print(
        f"{head} {label}: 받은 일감 {len(docs)}건 · 새 파일 {len(report.created)} · 바뀜 {len(report.changed)} · "
        f"그대로 {len(report.unchanged)} · 뷰 {len(views) - len(errors)}/{len(views)}{outside}",
        file=out,
    )
    _print_report(report, out, dry_run=dry_run)
    return 4 if errors else 0


def run_keys(cfg: Config, client: JiraClient, keys: list[str], *, dry_run: bool, out: TextIO) -> int:
    code = 0
    for raw in keys:
        try:
            requested = raw.strip().upper()
            if dry_run:
                doc = fetch_doc(cfg, client, raw)
                report = write_issues(cfg, [doc], core.now(cfg.tz), dry_run=True)
                state = "만들 예정" if report.created else ("바뀔 예정" if report.changed else "그대로")
                print(f"jira/{doc.key}.md: {state} (시험 실행, 쓰지 않음)", file=out)
                if doc.key != requested:
                    print(f"{requested}는 {doc.key}로 옮겨진 일감입니다", file=out)
                _print_report(report, out, dry_run=False)
            else:
                path = sync_key(cfg, client, raw)
                print(f"jira/{path.name}: 동기화함", file=out)
                if path.stem != requested:
                    print(f"{requested}는 {path.stem}로 옮겨진 일감입니다 (jira/{requested}.md에 moved_to 표시)", file=out)
        except NotFoundError as exc:
            print(f"오류: {raw}: {exc}", file=sys.stderr)
            code = max(code, 4)
        except core.ConfigError as exc:
            print(f"오류: {exc}", file=sys.stderr)
            code = max(code, 4)
    return code


def run_jql(cfg: Config, client: JiraClient, jql: str, *, cache: bool, dry_run: bool, out: TextIO) -> int:
    """`--jql`: 범위로 감싸지 않는다(D49). --cache가 없으면 출력만."""
    if not cache:
        count = 0
        for issue in client.search(jql, fields=VIEW_FIELDS):
            row = _view_row(issue)
            parts = [row["key"], row["status"] or "-", row["assignee"] or "-", row["summary"]]
            print(" · ".join(parts), file=out)
            count += 1
        print(f"{count}건 (저장하지 않음. 저장하려면 --cache)", file=out)
        return 0
    issues = list(client.search(jql, fields=client.sync_fields()))
    docs = _docs_for(cfg, client, issues)
    stamp = core.now(cfg.tz)
    projects = [p for p in cfg.project_keys if any(doc.project == p for doc in docs)]
    if dry_run:
        report = write_issues(cfg, docs, stamp, dry_run=True)
        report.merge(update_projects(cfg, projects, docs, dry_run=True))
    else:
        with core.kb_lock(cfg.root, LOCK_OWNER):
            report = _write_docs(cfg, docs, stamp, projects)
    head = "sync --jql --cache(시험 실행, 쓰지 않음)" if dry_run else "sync --jql --cache"
    print(
        f"{head}: 받은 일감 {len(docs)}건 · 새 파일 {len(report.created)} · 바뀜 {len(report.changed)} · 그대로 {len(report.unchanged)}",
        file=out,
    )
    _print_report(report, out, dry_run=dry_run)
    return 0


def main(argv: list[str]) -> int:
    parser = core.make_parser("kb.py sync", "Jira 일감을 jira/*.md·뷰·프로젝트 표·검색 캐시로 받아 온다 (읽기 전용)")
    parser.add_argument("--full", action="store_true", help="증분 대신 전체 범위(project_keys × jql_mine)를 받는다")
    parser.add_argument("--key", action="append", default=[], metavar="KEY", help="이 이슈만 받는다(여러 번 가능)")
    parser.add_argument("--jql", default=None, help="이 JQL의 결과를 출력만 한다(범위로 감싸지 않음)")
    parser.add_argument("--cache", action="store_true", help="--jql 결과를 jira/*.md와 캐시에 저장한다")
    parser.add_argument("--dry-run", action="store_true", help="받기만 하고 아무것도 쓰지 않는다(last_sync도 그대로)")
    args = parser.parse_args(argv)
    if args.cache and args.jql is None:
        raise core.ConfigError("--cache는 --jql과 함께 씁니다")
    if args.jql is not None and (args.key or args.full):
        raise core.ConfigError("--jql은 --key·--full과 함께 쓸 수 없습니다")
    if args.key and args.full:
        raise core.ConfigError("--key는 --full과 함께 쓸 수 없습니다")
    if args.jql is not None and not args.jql.strip():
        raise core.ConfigError("--jql: JQL이 비어 있습니다")
    cfg = config.load(args.root)
    out = sys.stdout
    with JiraClient.from_config(cfg) as client:
        if args.jql is not None:
            return run_jql(cfg, client, args.jql, cache=args.cache, dry_run=args.dry_run, out=out)
        if args.key:
            return run_keys(cfg, client, args.key, dry_run=args.dry_run, out=out)
        return run_scope(cfg, client, full=args.full, dry_run=args.dry_run, out=out)


if __name__ == "__main__":
    core.run_main(main)
