"""wiki 페이지를 Confluence 개인 스페이스로 게시한다 (§6.7, D56, D59).

`kb.py push [--dry-run] [--only GLOB|경로 ...]`

- 대상은 `confluence.push_globs`로 고른 `.md` 파일뿐이다. daily·inbox·jira·raw·_trash·.state 아래,
  `wiki/_*.md`·`wiki/index.md`·`wiki/actions.md`, 볼트 밖이면 globs에 적혀 있어도 코드에서 거부한다
  (종료 7, 아무것도 보내지 않음). glob이 적은 이름과 실제 위치(resolve) 둘 다 보며, 이름 끝의 점·공백과
  `:스트림`은 떼고 비교한다. 볼트 아래 경로에 심볼릭 링크·정션이 끼어 있어도 거부한다.
- 파일마다: front matter 제거 → `mentions` 구역은 본문까지, 나머지 관리 구역은 마커만 제거 →
  `wiki/decisions.md`는 상태가 `확정` 하나뿐인 항목만 남김 → HTML 주석 제거(닫히지 않은 `<!--`가 코드 밖에
  남으면 그 파일은 실패) → 첫 H1 제거(제목으로 씀) →
  위키링크 처리 → `markdown`(tables, fenced_code, 원시 HTML은 글자로) → storage XHTML(XML 검사).
- 위키링크: `[[KB-12]]` → Jira 링크, push_globs에 드는 페이지 → Confluence 페이지 링크(제목으로),
  그 밖(daily·사람 등) → 글자. 코드 안의 위키링크는 그대로 둔다.
- 제목: `title_prefix` + (front matter `title` | 첫 H1 | 파일 이름).
- 제목으로 찾아 있으면 갱신(버전 + 1, 409면 버전을 다시 읽어 한 번 더), 없으면 `.state`의 page_id로
  찾아 갱신(제목이 바뀐 경우, `expand=version,space`로 읽어 같은 스페이스일 때만. 아니면 그 state 항목을
  지운다), 그래도 없으면 부모 아래에 생성(`parent_page_id`가 비면 스페이스 홈 아래).
- `.state/confluence.json`: {rel: {page_id, version, sha256, pushed_at}}. sha256(스페이스·제목·storage)이
  같으면 보내지 않는다.
- `--dry-run`: 네트워크 없이 계획만 출력한다. 아무것도 쓰지 않는다.
- 종료 코드: 0 / 1 일부 파일 실패 / 2 인증 / 3 네트워크 / 4 설정 / 6 잠금 / 7 금지 경로·KB_AGENT.
"""
from __future__ import annotations

import fnmatch
import html
import html.entities
import os
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, TextIO

import markdown

from lib import config, core, frontmatter, managed
from lib.config import Config
from lib.confluence_client import ConfluenceClient, VersionConflict, page_version

STATE = "confluence"
LOCK_OWNER = "confluence_push"
DECISIONS_REL = "wiki/decisions.md"
FORBIDDEN_DIRS = frozenset({"daily", "inbox", "jira", "raw", "_trash", ".state"})
DENY_WIKI_FILES = frozenset({"index.md", "actions.md"})  # 그리고 wiki/_*.md
DROP_REGIONS = ("mentions",)
STORAGE_ROOT = (
    '<ac:root xmlns:ac="http://atlassian.com/content" xmlns:ri="http://atlassian.com/resource/identifier" '
    'xmlns:at="http://atlassian.com/template">'
)

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_WIKILINK = re.compile(r"(!?)\[\[([^\[\]\n]+?)\]\]")
_JIRA_KEY = re.compile(r"^(?:jira/)?([A-Z][A-Z0-9_]+-[1-9][0-9]*)$")
_H1 = re.compile(r"^#[ \t]+(.+?)[ \t]*#*[ \t]*$")
_ENTRY_HEADING = re.compile(r"^ {0,3}##(?:[ \t]|$)")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_CONFIRMED = re.compile(r"^\s*[-*+][ \t]*상태[ \t]*[:：][ \t]*\**확정\**(?:\s|<!--.*?-->)*$")
_STATUS = re.compile(r"^\s*(?:[-*+>][ \t]*)*[*_]*상태[*_]*[ \t]*[:：]")
_XML_BAD_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_ENTITY = re.compile(r"&([A-Za-z][A-Za-z0-9]*);")
_XML_ENTITIES = frozenset({"amp", "lt", "gt", "quot", "apos"})
_INLINE_CODE = re.compile(r"(`+)(?!`).*?(?<!`)\1(?!`)")
_CODE_SPAN = re.compile(r"<(pre|code)\b[^>]*>.*?</\1>", re.DOTALL)


# ---------------------------------------------------------------- 경로 고르기


def _norm_rel(text: str) -> str:
    rel = unicodedata.normalize("NFC", str(text).strip()).replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


def _win_segment(part: str) -> str:
    """Windows가 실제로 여는 이름: `:스트림`(`::$INDEX_ALLOCATION` 등)과 끝의 점·공백을 뗀다."""
    return part.split(":", 1)[0].rstrip(". ")


def forbidden_reason(rel: str) -> str | None:
    """게시하면 안 되는 상대 경로(또는 glob 패턴)면 그 이유, 아니면 None.

    - 볼트 밖(절대 경로, `..`), 금지 폴더(daily·inbox·jira·raw·_trash·.state) 아래.
    - 자동 생성·미확정 목록 페이지: `wiki/_*.md`(_lint·_proposals 등), `wiki/index.md`, `wiki/actions.md`.
    각 폴더·파일 이름은 Windows처럼 끝의 점·공백과 `:스트림`을 떼고 대소문자 없이 비교한다
    (`daily./`, `DAILY /`, `daily::$INDEX_ALLOCATION/`도 daily/다).
    """
    rel = _norm_rel(rel)
    if not rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        return "볼트 밖(절대 경로)"
    raw_parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." for p in raw_parts):
        return "볼트 밖(..)"
    parts: list[str] = []
    for raw in raw_parts:
        seg = _win_segment(raw)
        if not seg:
            return f"'{raw}'는 쓸 수 없는 경로 이름"
        parts.append(seg.casefold())
    if parts and parts[0] in FORBIDDEN_DIRS:
        return f"'{_win_segment(raw_parts[0])}/'는 외부 게시 금지 폴더"
    if len(parts) == 2 and parts[0] == "wiki" and (parts[1].startswith("_") or parts[1] in DENY_WIKI_FILES):
        return "자동 생성·미확정 목록 페이지라 게시 금지 (wiki/_*.md · wiki/index.md · wiki/actions.md)"
    return None


def _link_in_path(root: Path, path: Path) -> str | None:
    """볼트 루트 아래 경로 가운데 심볼릭 링크나 정션인 첫 부분(볼트 상대 경로). 없으면 None."""
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink() or os.path.isjunction(current):
            return current.relative_to(root).as_posix()
    return None


def _glob_reason(pattern: str) -> str | None:
    """패턴 자체가 금지 폴더나 볼트 밖을 가리키면 그 이유.

    `*/…`·`**/…`처럼 첫 폴더가 와일드카드인 패턴은 여기서 막지 않고, 고른 파일마다 다시 확인한다.
    """
    return forbidden_reason(pattern)


def _check_file(root: Path, root_resolved: Path, path: Path, rel: str) -> tuple[str, str | None]:
    """(실제 볼트 상대 경로, 거부 이유 또는 None).

    glob이 적은 이름(`rel`)과 실제로 가리키는 곳(resolve한 경로) 둘 다 금지 규칙에 걸리지 않아야 하고,
    볼트 아래 경로 어느 부분도 심볼릭 링크·정션이면 안 된다.
    """
    reason = forbidden_reason(rel)
    if reason:
        return rel, reason
    try:
        rel_real = _norm_rel(path.resolve().relative_to(root_resolved).as_posix())
    except (OSError, ValueError):
        return rel, "볼트 밖(링크)"
    reason = forbidden_reason(rel_real)
    if reason:
        return rel_real, f"실제 위치 {rel_real}: {reason}"
    link = _link_in_path(root, path)
    if link:
        return rel_real, f"{link}가 심볼릭 링크·정션이라 게시하지 않음 (실제 위치 {rel_real})"
    return rel_real, None


@dataclass
class Selection:
    files: list[tuple[str, Path]]  # (rel, 경로), push_globs 전체
    refused: list[tuple[str, str]]  # (rel 또는 패턴, 이유)
    skipped: list[str]  # .md가 아니어서 뺀 것


def collect(cfg: Config) -> Selection:
    """push_globs에 드는 파일. 금지 경로는 `refused`에 모은다(호출자가 종료 7로 멈춘다)."""
    globs = cfg.get("confluence.push_globs") or []
    if isinstance(globs, str):
        globs = [globs]
    if not isinstance(globs, (list, tuple)) or not all(isinstance(g, str) and g.strip() for g in globs):
        raise core.ConfigError("confluence.push_globs: 경로 패턴(문자열) 목록이어야 합니다")
    root = Path(cfg.root)
    root_resolved = root.resolve()
    found: dict[str, Path] = {}
    refused: list[tuple[str, str]] = []
    skipped: list[str] = []
    for pattern in globs:
        reason = _glob_reason(pattern)
        if reason:
            refused.append((pattern, reason))
            continue
        try:
            matches = sorted(root.glob(_norm_rel(pattern)))
        except (ValueError, NotImplementedError) as exc:
            refused.append((pattern, f"쓸 수 없는 패턴 ({exc})"))
            continue
        for path in matches:
            if not path.is_file() or not core.is_scannable(path):
                continue
            rel = _norm_rel(path.relative_to(root).as_posix())
            rel_real, reason = _check_file(root, root_resolved, path, rel)
            if reason:
                refused.append((rel, reason))
                continue
            if path.suffix.lower() != ".md":
                skipped.append(rel_real)
                continue
            found.setdefault(rel_real, path)
    files = sorted(found.items(), key=lambda item: item[0])
    return Selection(files=files, refused=refused, skipped=sorted(set(skipped)))


def apply_only(cfg: Config, files: list[tuple[str, Path]], only: Iterable[str]) -> list[tuple[str, Path]]:
    """`--only` 패턴이나 경로로 좁힌다. 금지 경로면 PolicyRefused, 아무것도 안 걸리면 ConfigError."""
    patterns: list[str] = []
    root = Path(cfg.root)
    for raw in only:
        text = str(raw).strip()
        if not text:
            continue
        candidate = Path(text)
        if candidate.is_absolute():
            try:
                text = candidate.relative_to(root).as_posix()
            except ValueError:
                raise core.PolicyRefused(f"--only {raw}: 볼트 밖 경로는 게시하지 않습니다") from None
        rel = _norm_rel(text)
        reason = forbidden_reason(rel)
        if reason:
            raise core.PolicyRefused(f"--only {raw}: {reason}라 게시하지 않습니다")
        patterns.append(rel)
    if not patterns:
        return files
    picked: list[tuple[str, Path]] = []
    for rel, path in files:
        key = rel.casefold()
        if any(key == p.casefold() or fnmatch.fnmatchcase(key, p.casefold()) for p in patterns):
            picked.append((rel, path))
    if not picked:
        raise core.ConfigError(
            "--only: push_globs에 드는 파일 중 맞는 것이 없습니다 (" + ", ".join(patterns) + "). "
            "config.yaml의 confluence.push_globs를 확인하세요"
        )
    return picked


# ---------------------------------------------------------------- 마크다운 손질


def _entry_confirmed(entry: list[str]) -> bool:
    """항목의 상태 줄(코드 블록·HTML 주석 밖)이 꼭 하나이고 그것이 `확정`이면 True. 아니면 False(게시 안 함)."""
    text = _COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), "".join(entry))
    fence: str | None = None
    statuses: list[str] = []
    for line in text.split("\n")[1:]:  # 첫 줄은 `## ` 제목
        bare = unicodedata.normalize("NFC", line.rstrip("\r"))
        fence_match = _FENCE.match(bare)
        if fence is not None:
            if fence_match and fence_match.group(1)[0] == fence[0] and len(fence_match.group(1)) >= len(fence):
                fence = None
            continue
        if fence_match:
            fence = fence_match.group(1)
            continue
        if _STATUS.match(bare):
            statuses.append(bare)
    return len(statuses) == 1 and bool(_CONFIRMED.match(statuses[0]))


def keep_confirmed_decisions(text: str) -> str:
    """`## ` 항목 가운데 상태가 `확정`인 것만 남긴다(안전 쪽으로 닫힘).

    - 첫 `## ` 항목 앞의 줄은 그대로 둔다.
    - 첫 항목이 시작되면 다음 `## `까지 모든 줄(`# `·`### ` 포함)이 그 항목에 속한다.
    - 상태는 코드 블록·HTML 주석 밖의 `- 상태:` 줄로 본다. 꼭 하나이고 `확정`일 때만 게시한다
      (상태 줄이 없거나 둘 이상이면 게시하지 않는다).
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    entry: list[str] | None = None
    fence: str | None = None

    def flush() -> None:
        if entry is not None and _entry_confirmed(entry):
            out.extend(entry)

    for line in lines:
        bare = line.rstrip("\r\n")
        fence_match = _FENCE.match(bare)
        if fence is not None:
            if fence_match and fence_match.group(1)[0] == fence[0] and len(fence_match.group(1)) >= len(fence):
                fence = None
        elif fence_match:
            fence = fence_match.group(1)
        elif _ENTRY_HEADING.match(bare):
            flush()
            entry = [line]
            continue
        (entry if entry is not None else out).append(line)
    flush()
    return "".join(out)


def _unclosed_comment(text: str) -> bool:
    """닫힌 주석을 지운 뒤에도 코드(펜스 블록·`…`) 밖에 `<!--`가 남았으면 True."""
    fence: str | None = None
    for line in text.split("\n"):
        fence_match = _FENCE.match(line)
        if fence is not None:
            if fence_match and fence_match.group(1)[0] == fence[0] and len(fence_match.group(1)) >= len(fence):
                fence = None
            continue
        if fence_match:
            fence = fence_match.group(1)
            continue
        if "<!--" in _INLINE_CODE.sub("", line):
            return True
    return False


def _split_h1(text: str) -> tuple[str | None, str]:
    """맨 앞(빈 줄 제외)의 `# 제목` 줄을 떼어 (제목, 나머지)로."""
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        match = _H1.match(line.rstrip("\r"))
        if match:
            return match.group(1).strip(), "\n".join(lines[index + 1 :])
        break
    return None, text


@dataclass
class Doc:
    rel: str
    title: str
    body: str  # 손질한 마크다운(위키링크는 그대로)


def prepare(rel: str, data: bytes, prefix: str) -> Doc:
    """파일 바이트 → 제목과 손질한 마크다운. 마커가 깨졌거나 UTF-8이 아니면 KbError."""
    fm_raw, body = frontmatter.split(data)
    fields = frontmatter.parse(data) if fm_raw.strip() else {}
    try:
        body = managed.strip_markers(body, drop_regions=DROP_REGIONS)
    except managed.MarkerError as exc:
        raise core.KbError(f"{rel}: 관리 구역 표시가 깨져 게시하지 않았습니다 ({exc})") from exc
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise core.KbError(f"{rel}: UTF-8이 아니어서 게시하지 않았습니다") from exc
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFC", text)
    if _norm_rel(rel).casefold() == DECISIONS_REL:
        text = keep_confirmed_decisions(text)
    text = _COMMENT.sub("", text)
    if _unclosed_comment(text):
        raise core.KbError(f"{rel}: HTML 주석이 닫히지 않음 (`<!--`에 맞는 `-->`가 없습니다). 고친 뒤 다시 게시하세요")
    h1, text = _split_h1(text)
    fm_title = fields.get("title") if isinstance(fields, Mapping) else None
    name = fm_title.strip() if isinstance(fm_title, str) and fm_title.strip() else (h1 or PurePosixPath(rel).stem)
    title = unicodedata.normalize("NFC", f"{prefix}{name}").strip()
    return Doc(rel=rel, title=title, body=text.strip("\n") + "\n")


# ---------------------------------------------------------------- 위키링크와 storage


def _link_key(target: str) -> str:
    key = unicodedata.normalize("NFC", target).strip().replace("\\", "/").strip("/")
    if key.lower().endswith(".md"):
        key = key[:-3]
    return key.casefold()


class LinkResolver:
    """위키링크 대상 → ('jira', 키) | ('page', 제목) | ('text', None)."""

    def __init__(self, pages: Mapping[str, str], jira_base_url: str | None) -> None:
        self._exact: dict[str, str] = {}
        self._by_suffix: list[tuple[str, str]] = []
        for rel, title in pages.items():
            key = _link_key(rel)
            self._exact[key] = title
            self._by_suffix.append((key, title))
        base = (jira_base_url or "").strip().rstrip("/")
        self.jira_base = base or None

    def resolve(self, target: str) -> tuple[str, str | None]:
        bare = target.split("#", 1)[0].strip()
        match = _JIRA_KEY.match(bare)
        if match:
            return ("jira", match.group(1)) if self.jira_base else ("text", None)
        key = _link_key(bare)
        if not key:
            return ("text", None)
        if key in self._exact:
            return ("page", self._exact[key])
        if f"wiki/{key}" in self._exact:
            return ("page", self._exact[f"wiki/{key}"])
        hits = {title for rel, title in self._by_suffix if rel.endswith("/" + key)}
        if len(hits) == 1:
            return ("page", hits.pop())
        return ("text", None)


def _cdata(text: str) -> str:
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def _render_link(resolver: LinkResolver, embed: bool, inner: str) -> str:
    target, _, alias = inner.partition("|")
    target = target.rstrip("\\").strip()
    alias = alias.strip()
    kind, value = ("text", None) if embed else resolver.resolve(target)
    if kind == "jira":
        label = alias or value
        href = f"{resolver.jira_base}/browse/{value}"
        return f'<a href="{html.escape(href, quote=True)}">{html.escape(label, quote=False)}</a>'
    if kind == "page":
        label = alias or target.split("#", 1)[0].strip()
        return (
            f'<ac:link><ri:page ri:content-title="{html.escape(value, quote=True)}"/>'
            f"<ac:plain-text-link-body>{_cdata(label)}</ac:plain-text-link-body></ac:link>"
        )
    label = alias or target
    return html.escape(label, quote=False)


def _fix_entities(text: str) -> str:
    """HTML 이름 엔티티를 XML에서 읽히는 숫자 참조로. 모르는 이름은 글자 그대로(&amp;)로."""

    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in _XML_ENTITIES:
            return match.group(0)
        code = html.entities.name2codepoint.get(name)
        if code is not None:
            return f"&#{code};"
        return f"&amp;{name};"

    return _ENTITY.sub(repl, text)


def _markdown() -> markdown.Markdown:
    md = markdown.Markdown(extensions=["tables", "fenced_code"], output_format="xhtml")
    # 원시 HTML은 통과시키지 않고 글자로 보인다(storage를 깨뜨리거나 매크로를 끼워 넣지 못하게)
    md.preprocessors.deregister("html_block")
    md.inlinePatterns.deregister("html")
    return md


def check_storage(storage: str) -> None:
    """storage가 잘 짜인 XML인지 확인한다. 아니면 KbError."""
    try:
        ET.fromstring(STORAGE_ROOT + storage + "</ac:root>")
    except ET.ParseError as exc:
        raise core.KbError(f"storage 변환 결과가 올바른 XML이 아닙니다 ({exc})") from exc


def to_storage(body: str, resolver: LinkResolver) -> str:
    """손질한 마크다운 → Confluence storage XHTML."""
    text = _XML_BAD_CHARS.sub("", body)
    nonce = core.sha256_hex(text.encode("utf-8"))[:8]
    links: dict[str, tuple[str, str]] = {}  # 자리표 → (원문, 렌더링)

    def hold(match: re.Match[str]) -> str:
        token = f"KBWL{nonce}N{len(links)}X"
        links[token] = (match.group(0), _render_link(resolver, bool(match.group(1)), match.group(2)))
        return token

    text = _WIKILINK.sub(hold, text)
    out = _markdown().convert(text)
    if links:
        # 코드 안에 들어간 자리표는 원래 글자로 되돌린다
        def restore_code(match: re.Match[str]) -> str:
            chunk = match.group(0)
            for token, (original, _) in links.items():
                if token in chunk:
                    chunk = chunk.replace(token, html.escape(original, quote=False))
            return chunk

        out = _CODE_SPAN.sub(restore_code, out)
        for token, (_, rendered) in links.items():
            out = out.replace(token, rendered)
    out = _fix_entities(out)
    check_storage(out)
    return out


# ---------------------------------------------------------------- 게시


@dataclass
class Item:
    rel: str
    title: str
    storage: str
    sha256: str
    action: str = "push"  # push | unchanged
    previous: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class Report:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


def content_sha(space_key: str, title: str, storage: str) -> str:
    return core.sha256_hex(f"{space_key}\n{title}\n{storage}".encode("utf-8"))


def _save_entry(cfg: Config, rel: str, entry: Mapping[str, Any] | None) -> None:
    """state 항목을 쓴다. entry가 None이면 그 항목을 지운다."""
    with core.kb_lock(cfg.root, LOCK_OWNER):
        state = core.load_state(cfg.root, STATE)
        if entry is None:
            if rel not in state:
                return
            state.pop(rel)
        else:
            state[rel] = dict(entry)
        core.save_state(cfg.root, STATE, state)


def _same_space(page: Mapping[str, Any] | None, space_key: str) -> bool:
    """페이지 응답의 `space.key`가 있고 space_key와 같으면(대소문자 무시) True. 없으면 False(안전 쪽)."""
    space = page.get("space") if isinstance(page, Mapping) else None
    key = space.get("key") if isinstance(space, Mapping) else None
    return isinstance(key, str) and bool(key.strip()) and key.strip().casefold() == space_key.strip().casefold()


def push_one(
    client: ConfluenceClient,
    space_key: str,
    item: Item,
    parent: Callable[[], str | None],
    *,
    forget: Callable[[], None] | None = None,
) -> tuple[str, dict]:
    """('created'|'updated', 페이지). 제목 → state의 page_id(같은 스페이스일 때만) → 생성 순서로 찾는다.

    state의 page_id가 없어졌거나 다른 스페이스의 페이지면 `forget()`으로 그 state 항목을 지우고 새로 만든다.
    """
    page = client.find_page(space_key, item.title)
    if page is None:
        prev_id = item.previous.get("page_id") if isinstance(item.previous, Mapping) else None
        if prev_id:
            candidate = client.get_page(str(prev_id))
            if candidate is not None and _same_space(candidate, space_key):
                page = candidate
            elif forget is not None:
                forget()
    if page is None:
        created = client.create_page(space_key, item.title, item.storage, parent())
        return "created", created
    page_id = str(page["id"])
    current = page_version(page)
    if current is None:
        fresh = client.get_page(page_id)
        current = page_version(fresh or {}) or 0
    try:
        updated = client.update_page(page_id, item.title, item.storage, current + 1, space_key=space_key)
    except VersionConflict:
        fresh = client.get_page(page_id)
        if fresh is None or page_version(fresh) is None:
            raise
        updated = client.update_page(page_id, item.title, item.storage, page_version(fresh) + 1, space_key=space_key)
    return "updated", updated


def run(
    cfg: Config,
    *,
    only: Iterable[str] = (),
    dry_run: bool = False,
    out: TextIO | None = None,
    client: ConfluenceClient | None = None,
) -> int:
    out = out or sys.stdout
    conf = cfg.get("confluence") or {}
    if not isinstance(conf, Mapping):
        raise core.ConfigError("confluence: '키: 값' 묶음이어야 합니다")
    if not config_truthy(conf.get("enabled", True)):
        print("Confluence가 꺼져 있어(confluence.enabled: false) 게시하지 않습니다.", file=out)
        return 0

    selection = collect(cfg)
    if selection.refused:
        lines = "\n".join(f"  - {rel}: {reason}" for rel, reason in selection.refused)
        raise core.PolicyRefused(
            "push_globs가 게시 금지 경로를 고릅니다. 아무것도 보내지 않았습니다:\n" + lines + "\n"
            "  config.yaml의 confluence.push_globs를 wiki/ 아래로만 고치세요"
        )
    targets = apply_only(cfg, selection.files, only)
    space_key = str(conf.get("space_key") or "").strip()
    if not space_key:
        raise core.ConfigError("confluence.space_key가 비어 있습니다. `python scripts/kb.py init --section confluence`로 설정하세요")
    prefix = conf.get("title_prefix")
    prefix = "" if prefix is None else str(prefix)

    report = Report()
    docs: dict[str, Doc] = {}
    for rel, path in selection.files:
        try:
            docs[rel] = prepare(rel, path.read_bytes(), prefix)
        except core.KbError as exc:
            if any(rel == t for t, _ in targets):
                report.failed.append((rel, str(exc)))
        except OSError as exc:
            if any(rel == t for t, _ in targets):
                report.failed.append((rel, f"읽지 못했습니다 ({exc})"))
    resolver = LinkResolver({rel: doc.title for rel, doc in docs.items()}, cfg.get("jira.base_url"))

    state = core.load_state(cfg.root, STATE)
    items: list[Item] = []
    titles: dict[str, str] = {}
    for rel, _ in targets:
        doc = docs.get(rel)
        if doc is None:
            continue
        if doc.title in titles:
            report.failed.append((rel, f"제목 '{doc.title}'이(가) {titles[doc.title]}와 같습니다. front matter title을 바꾸세요"))
            continue
        titles[doc.title] = rel
        try:
            storage = to_storage(doc.body, resolver)
        except core.KbError as exc:
            report.failed.append((rel, f"{rel}: {exc}"))
            continue
        sha = content_sha(space_key, doc.title, storage)
        previous = state.get(rel) if isinstance(state.get(rel), Mapping) else {}
        item = Item(rel=rel, title=doc.title, storage=storage, sha256=sha, previous=previous or {})
        if previous and previous.get("sha256") == sha and previous.get("page_id"):
            item.action = "unchanged"
        items.append(item)

    if dry_run:
        print(f"push (시험 실행, 보내지 않음) → 스페이스 {space_key}", file=out)
        for item in items:
            if item.action == "unchanged":
                print(f"  = {item.rel}: 변경 없음 ({item.title})", file=out)
            elif item.previous.get("page_id"):
                print(f"  ~ {item.rel}: 갱신 예정 → {item.title} (page {item.previous['page_id']}, {len(item.storage)}자)", file=out)
            else:
                print(f"  + {item.rel}: 게시 예정(제목으로 찾아 갱신, 없으면 생성) → {item.title} ({len(item.storage)}자)", file=out)
        for rel, reason in report.failed:
            print(f"  ! {rel}: {reason}", file=out)
        pending = sum(1 for i in items if i.action == "push")
        print(f"요약: 보낼 것 {pending} · 변경 없음 {len(items) - pending} · 실패 {len(report.failed)}", file=out)
        return 1 if report.failed else 0

    to_send = [item for item in items if item.action == "push"]
    report.unchanged.extend(item.rel for item in items if item.action == "unchanged")
    if to_send:
        owned = client is None
        client = client or ConfluenceClient.from_config(cfg)
        logger = core.get_logger(cfg.root)
        parent_cache: list[str | None] = []

        def parent() -> str | None:
            if not parent_cache:
                configured = str(conf.get("parent_page_id") or "").strip()
                parent_cache.append(configured or client.homepage_id(space_key))
            return parent_cache[0]

        try:
            for item in to_send:
                try:
                    kind, page = push_one(
                        client, space_key, item, parent, forget=lambda rel=item.rel: _save_entry(cfg, rel, None)
                    )
                except (core.AuthError, core.NetworkError, core.ConfigError):
                    raise
                except core.KbError as exc:
                    report.failed.append((item.rel, core.redact(str(exc))))
                    logger.warning(f"push 실패 {item.rel}: {exc}")
                    continue
                entry = {
                    "page_id": str(page["id"]),
                    "version": page_version(page),
                    "sha256": item.sha256,
                    "pushed_at": core.now(cfg.tz).isoformat(timespec="seconds"),
                }
                _save_entry(cfg, item.rel, entry)
                (report.created if kind == "created" else report.updated).append(item.rel)
                logger.info(f"push {kind} {item.rel} → page {entry['page_id']} v{entry['version']}")
                mark = "+" if kind == "created" else "~"
                word = "생성" if kind == "created" else "갱신"
                print(f"  {mark} {item.rel}: {word} → {item.title} (page {entry['page_id']}, 버전 {entry['version']})", file=out)
        finally:
            if owned:
                client.close()

    for rel in report.unchanged:
        print(f"  = {rel}: 변경 없음", file=out)
    for rel, reason in report.failed:
        print(f"  ! {rel}: {reason}", file=out)
    print(
        f"요약: 생성 {len(report.created)} · 갱신 {len(report.updated)} · 변경 없음 {len(report.unchanged)} · "
        f"실패 {len(report.failed)} (스페이스 {space_key})",
        file=out,
    )
    return 1 if report.failed else 0


def config_truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "no", "off", "0", "")
    return bool(value) if value is not None else True


def main(argv: list[str]) -> int:
    parser = core.make_parser("kb.py push", "wiki 페이지를 Confluence 개인 스페이스로 게시한다 (push_globs만)")
    parser.add_argument("--dry-run", action="store_true", help="보내지 않고 계획만 출력한다(네트워크 없음)")
    parser.add_argument(
        "--only", action="append", default=[], metavar="GLOB|경로", help="push_globs 가운데 이것만(여러 번 가능)"
    )
    args = parser.parse_args(argv)
    core.refuse_if_agent("push")
    cfg = config.load(args.root)
    return run(cfg, only=args.only, dry_run=args.dry_run)


if __name__ == "__main__":
    core.run_main(main)
