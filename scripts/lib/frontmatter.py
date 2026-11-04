"""YAML front matter 읽기·쓰기. 바이트를 다루고, 고칠 때는 해당 키의 줄만 바꾼다(D27).

- 읽기는 yaml.BaseLoader라서 값이 모두 문자열이다(`2026-11-05`, `on`, `NO`가 그대로 남는다).
- 쓰기는 yaml.safe_dump로 한 키씩 만든다. 목록은 `[cache, schema]` 흐름 형식이 된다.
- CRLF와 BOM을 지킨다.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

import yaml

from lib import core, managed

BOM = b"\xef\xbb\xbf"
_FENCE = re.compile(rb"---[ \t]*\r?")
_KEY = re.compile(
    rb"""(?:"((?:[^"\\]|\\.)*)"|'((?:[^']|'')*)'|([^\s#'"\-\[\]{},:?!&*|>%@`][^\r\n]*?))[ \t]*:(?:[ \t]|$)"""
)
_WIDTH = 1 << 30  # safe_dump가 긴 값을 여러 줄로 접지 않게


# ---------------------------------------------------------------- 내부 도우미


def _locate(data: bytes) -> tuple[int, int, int] | None:
    """(내용 시작, 닫는 울타리 줄 시작, 본문 시작). front matter가 없으면 None."""
    start = 3 if data.startswith(BOM) else 0
    nl = data.find(b"\n", start)
    if nl < 0 or not _FENCE.fullmatch(data, start, nl):
        return None
    content_start = pos = nl + 1
    while pos < len(data):
        nl = data.find(b"\n", pos)
        end = len(data) if nl < 0 else nl
        if _FENCE.fullmatch(data, pos, end):
            return content_start, pos, len(data) if nl < 0 else nl + 1
        if nl < 0:
            break
        pos = nl + 1
    return None


class _FlowList(list):
    pass


class _FlowMap(dict):
    pass


class _Dumper(yaml.SafeDumper):
    """SafeDumper와 같되, 값 안의 목록·사전은 한 줄 흐름 형식(`[a, b]`, `{x: y}`)으로 쓴다.

    `safe_dump(default_flow_style=None)`는 키 하나짜리 바깥 사전까지 `{k: v}`로 만들어서
    바깥은 블록, 안쪽만 흐름이 되도록 표현기를 따로 둔다.
    """


_Dumper.add_representer(
    _FlowList, lambda d, v: d.represent_sequence("tag:yaml.org,2002:seq", v, flow_style=True)
)
_Dumper.add_representer(
    _FlowMap, lambda d, v: d.represent_mapping("tag:yaml.org,2002:map", v.items(), flow_style=True)
)


def _plain(value: Any) -> Any:
    """덤프할 형태로(튜플·집합 → 목록, 안쪽 컬렉션은 흐름 형식)."""
    if isinstance(value, Mapping):
        return _FlowMap((str(k), _plain(v)) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return _FlowList(_plain(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return _FlowList(sorted(_plain(v) for v in value))
    return value


def _item(key: str, value: Any, eol: bytes) -> bytes:
    text = yaml.dump(
        {str(key): _plain(value)},
        Dumper=_Dumper,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=_WIDTH,
    )
    return text.encode("utf-8").replace(b"\n", eol)


def _load(text: str) -> Any:
    return yaml.load(text, Loader=yaml.BaseLoader)


def _key_name(match: re.Match[bytes]) -> str:
    double, single, plain = match.groups()
    if double is not None:
        return double.decode("utf-8", "surrogateescape").replace('\\"', '"').replace("\\\\", "\\")
    if single is not None:
        return single.decode("utf-8", "surrogateescape").replace("''", "'")
    return plain.decode("utf-8", "surrogateescape").rstrip()


def _extents(block: bytes) -> list[tuple[str, int, int]]:
    """front matter 안에서 최상위 키마다 (키, 시작, 끝) 범위. 이어지는 들여쓴 줄과 `- ` 줄을 포함한다.

    빈 줄은 뒤에 이어지는 줄이 있을 때만 포함한다. 0열 주석과 그 밖의 줄은 경계다.
    """
    found: list[tuple[str, int, int]] = []
    current: list[Any] | None = None
    for start, content_end, end in managed._lines(block):
        content = block[start:content_end].rstrip(b"\r")
        if not content.strip():
            continue
        first = content[:1]
        if first in (b" ", b"\t") or (first == b"-" and content[1:2] in (b"", b" ", b"\t")):
            if current is not None:
                current[2] = end
            continue
        match = _KEY.match(content) if first != b"#" else None
        if current is not None:
            found.append((current[0], current[1], current[2]))
        current = [_key_name(match), start, end] if match else None
    if current is not None:
        found.append((current[0], current[1], current[2]))
    return found


def _same_value(existing: bytes, key: str, value: Any) -> bool:
    """기존 줄과 새 값이 BaseLoader 기준으로 같으면 True(따옴표 차이로 줄을 바꾸지 않게)."""
    try:
        old = _load(existing.decode("utf-8"))
        new = _load(_item(key, value, b"\n").decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError):
        return False
    return isinstance(old, dict) and old == new


# ---------------------------------------------------------------- 공개 API


def split(data: bytes) -> tuple[bytes, bytes]:
    """(울타리를 뺀 front matter, 본문). front matter가 없으면 (b'', data)."""
    loc = _locate(data)
    if loc is None:
        return b"", data
    content_start, close_start, body_start = loc
    return data[content_start:close_start], data[body_start:]


def parse(data: bytes) -> dict[str, Any]:
    """파일 바이트에서 front matter를 읽는다. 값은 모두 문자열(목록은 문자열 목록)."""
    raw, _ = split(data)
    if not raw.strip():
        return {}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise core.ConfigError("front matter가 UTF-8이 아닙니다") from exc
    try:
        value = _load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
        where = f" ({mark.line + 2}번째 줄)" if mark is not None else ""
        raise core.ConfigError(f"front matter YAML이 올바르지 않습니다{where}") from exc
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise core.ConfigError("front matter는 '키: 값' 형태여야 합니다")
    return value


def render(fields: Mapping[str, Any], eol: bytes = b"\n") -> bytes:
    """`---`로 감싼 front matter 바이트. 값이 None인 키는 뺀다."""
    out = [b"---", eol]
    for key, value in fields.items():
        if value is not None:
            out.append(_item(key, value, eol))
    out += [b"---", eol]
    return b"".join(out)


def set_fields(data: bytes, updates: Mapping[str, Any]) -> bytes:
    """지정한 키의 줄만 고친다. 없는 키는 닫는 `---` 앞에 붙인다. 값이 None이면 키를 지운다.

    front matter가 없으면 파일 맨 앞(BOM 뒤)에 새로 만든다. 나머지 바이트는 그대로다.
    """
    updates = dict(updates)
    eol = managed.detect_eol(data)
    loc = _locate(data)
    if loc is None:
        fields = {k: v for k, v in updates.items() if v is not None}
        if not fields:
            return data
        head = BOM if data.startswith(BOM) else b""
        return head + render(fields, eol) + data[len(head) :]

    content_start, close_start, _ = loc
    block = data[content_start:close_start]
    extents = _extents(block)
    edits: list[tuple[int, int, bytes]] = []
    tail: list[bytes] = []
    for key, value in updates.items():
        hits = [(s, e) for name, s, e in extents if name == key]
        if not hits:
            if value is not None:
                tail.append(_item(key, value, eol))
            continue
        first_start, first_end = hits[0]
        if value is None:
            edits.append((first_start, first_end, b""))
        elif not _same_value(block[first_start:first_end], key, value):
            edits.append((first_start, first_end, _item(key, value, eol)))
        edits.extend((s, e, b"") for s, e in hits[1:])  # 중복 키는 하나만 남긴다

    if not edits and not tail:
        return data
    for start, end, replacement in sorted(edits, reverse=True):
        block = block[:start] + replacement + block[end:]
    block += b"".join(tail)
    return data[:content_start] + block + data[close_start:]
