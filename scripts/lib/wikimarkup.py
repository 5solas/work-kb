"""Best-effort Jira wiki markup -> Markdown conversion (pure stdlib).

Public API: to_markdown(text), convert_or_raw(text), WikimarkupError.
"""
from __future__ import annotations

import re

__all__ = ["WikimarkupError", "to_markdown", "convert_or_raw", "FAIL_NOTE"]

FAIL_NOTE = "(Jira wiki markup 변환 실패, 원문)"


class WikimarkupError(Exception):
    """Conversion failed (wraps any internal error)."""


_CTRL_RE = re.compile(r"[\x00-\x05]")
_CODE_RE = re.compile(r"\{(code|noformat)(?::([^}\n]*))?\}(.*?)\{\1\}", re.DOTALL)
_QUOTE_RE = re.compile(r"\{quote\}(.*?)\{quote\}", re.DOTALL)
_PANEL_RE = re.compile(r"\{panel(?::([^}\n]*))?\}")
_COLOR_RE = re.compile(r"\{color(?::[^}\n]*)?\}")
_HEADING_RE = re.compile(r"^h([1-6])\.\s*(.*)$")
_BQ_RE = re.compile(r"^bq\.\s?(.*)$")
_LIST_RE = re.compile(r"^\s*([*#]+|-)\s+(.*)$")
_HR_RE = re.compile(r"^\s*-{4,}\s*$")
_MONO_RE = re.compile(r"\{\{(.+?)\}\}")
_IMAGE_RE = re.compile(r"!([^\s!|\[\]]+\.[A-Za-z0-9]{2,5})(?:\|[^!\n]*)?!")
_LINK_RE = re.compile(r"\[([^\]|\n]+)\|([^\]\n]+)\]")
_URL_LINK_RE = re.compile(r"\[((?:https?|ftp|mailto):[^\]\s|]+)\]")
_BOLD_RE = re.compile(r"(?<![A-Za-z0-9*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![A-Za-z0-9*])")
_ITALIC_RE = re.compile(r"(?<![A-Za-z0-9_])_(?=\S)([^_\n]+?)(?<=\S)_(?![A-Za-z0-9_])")
_PH_RE = re.compile(r"\x00(\d+)\x00")
_CODE_PH_RE = re.compile(r"\x05(\d+)\x05")
_QUOTE_MARK = "\x04"
_BOLD_MARK = "\x02"
_ITALIC_MARK = "\x03"


def _fence(content: str, lang: str = "") -> str:
    longest = max((len(m) for m in re.findall(r"`+", content)), default=0)
    ticks = "`" * max(3, longest + 1)
    body = content.strip("\n")
    return f"{ticks}{lang}\n{body}\n{ticks}"


def _code_lang(kind: str, params: str | None) -> str:
    if kind != "code" or not params:
        return ""
    parts = [p.strip() for p in params.split("|")]
    for p in parts:
        if p.lower().startswith("language="):
            return p.split("=", 1)[1].strip()
    first = parts[0]
    if first and "=" not in first:
        return first
    return ""


def _protect(line: str, store: list[str]) -> str:
    """Replace spans that must not be touched by emphasis with placeholders."""

    def keep(s: str) -> str:
        store.append(s)
        return f"\x00{len(store) - 1}\x00"

    line = _MONO_RE.sub(lambda m: keep("`" + m.group(1) + "`"), line)
    line = _IMAGE_RE.sub(lambda m: keep(f"![{m.group(1)}]"), line)

    def link(m: re.Match) -> str:
        url = m.group(2).split("|", 1)[0].strip()
        return keep(f"[{m.group(1).strip()}]({url})")

    line = _LINK_RE.sub(link, line)
    line = _URL_LINK_RE.sub(lambda m: keep(f"[{m.group(1)}]({m.group(1)})"), line)
    return line


def _emph(s: str) -> str:
    s = _BOLD_RE.sub(lambda m: f"{_BOLD_MARK}{m.group(1)}{_BOLD_MARK}", s)
    s = _ITALIC_RE.sub(lambda m: f"{_ITALIC_MARK}{m.group(1)}{_ITALIC_MARK}", s)
    return s


def _restore(s: str, store: list[str]) -> str:
    return _PH_RE.sub(lambda m: store[int(m.group(1))], s)


def _inline(s: str) -> str:
    store: list[str] = []
    return _restore(_emph(_protect(s, store)), store)


def _table_cells(line: str, header: bool) -> list[str]:
    s = line.strip()
    if header:
        s = s[2:]
        if s.endswith("||"):
            s = s[:-2]
        elif s.endswith("|"):
            s = s[:-1]
        parts = re.split(r"\|\|?", s)
    else:
        s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        parts = s.split("|")
    return parts


def _render_table(rows: list[tuple[bool, str]]) -> list[str]:
    store: list[str] = []
    parsed = []
    for is_header, raw in rows:
        protected = _protect(raw, store)
        cells = [
            _restore(_emph(c.strip()), store).replace("|", "\\|") for c in _table_cells(protected, is_header)
        ]
        parsed.append(cells)
    width = max(len(c) for c in parsed)
    parsed = [c + [""] * (width - len(c)) for c in parsed]
    out = ["| " + " | ".join(parsed[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    out += ["| " + " | ".join(c) + " |" for c in parsed[1:]]
    return out


def _convert(text: str) -> str:
    text = _CTRL_RE.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    codes: list[str] = []

    def code_sub(m: re.Match) -> str:
        codes.append(_fence(m.group(3), _code_lang(m.group(1), m.group(2))))
        return f"\n\x05{len(codes) - 1}\x05\n"

    text = _CODE_RE.sub(code_sub, text)

    def quote_sub(m: re.Match) -> str:
        body = m.group(1).strip("\n")
        return "\n" + "\n".join(_QUOTE_MARK + ln for ln in body.split("\n")) + "\n"

    text = _QUOTE_RE.sub(quote_sub, text)
    text = _COLOR_RE.sub("", text)

    out: list[str] = []
    table: list[tuple[bool, str]] = []
    list_widths: list[int] = []  # unused state kept simple

    def flush_table() -> None:
        if table:
            out.extend(_render_table(table))
            table.clear()

    for line in text.split("\n"):
        quoted = line.startswith(_QUOTE_MARK)
        if quoted:
            line = line[1:]
        pm = _PANEL_RE.search(line)
        if pm:
            rest = _PANEL_RE.sub("", line)
            title = ""
            if pm.group(1):
                for p in pm.group(1).split("|"):
                    if p.strip().lower().startswith("title="):
                        title = p.split("=", 1)[1].strip()
            if title:
                flush_table()
                out.append(f"**{title}**")
            if not rest.strip():
                continue
            line = rest

        stripped = line.strip()
        if stripped.startswith("|") and not quoted:
            table.append((stripped.startswith("||"), stripped))
            continue
        flush_table()

        prefix = "> " if quoted else ""
        if not stripped:
            out.append("")
            continue
        if _CODE_PH_RE.fullmatch(stripped):
            out.append(prefix + stripped if quoted else stripped)
            continue
        if _HR_RE.match(line):
            out.append(prefix + "---")
            continue
        m = _HEADING_RE.match(stripped)
        if m:
            out.append(prefix + "#" * int(m.group(1)) + " " + _inline(m.group(2)))
            continue
        m = _BQ_RE.match(stripped)
        if m:
            out.append("> " + prefix + _inline(m.group(1)))
            continue
        m = _LIST_RE.match(line)
        if m:
            marker, content = m.group(1), m.group(2)
            if marker == "-":
                marker = "*"
            indent = sum(3 if ch == "#" else 2 for ch in marker[:-1])
            bullet = "1." if marker[-1] == "#" else "-"
            out.append(prefix + " " * indent + bullet + " " + _inline(content))
            continue
        out.append(prefix + _inline(line.rstrip()))
    flush_table()

    result = "\n".join(out)
    result = result.replace(_BOLD_MARK, "**").replace(_ITALIC_MARK, "*")
    result = _CODE_PH_RE.sub(lambda m: codes[int(m.group(1))], result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip("\n")


def to_markdown(text: str | None) -> str:
    """Convert Jira wiki markup to Markdown. None/empty -> ''."""
    if text is None:
        return ""
    if not isinstance(text, str):
        raise WikimarkupError(f"str expected, got {type(text).__name__}")
    if not text.strip():
        return ""
    try:
        return _convert(text)
    except WikimarkupError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise WikimarkupError(str(exc)) from exc


def convert_or_raw(text: str | None) -> str:
    """Never raises. On failure returns the original in a fenced block with a note."""
    try:
        return to_markdown(text)
    except Exception:  # noqa: BLE001
        try:
            raw = text if isinstance(text, str) else str(text)
        except Exception:  # noqa: BLE001
            raw = ""
        return FAIL_NOTE + "\n\n" + _fence(raw)
