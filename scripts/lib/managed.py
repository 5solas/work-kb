"""관리 구역(`<!-- kb:NAME:start -->` … `<!-- kb:NAME:end -->`) 편집. 바이트만 다룬다.

규칙(D17·D24·D25):
- 마커는 줄 전체가 `<!-- kb:NAME:start -->` 또는 `<!-- kb:NAME:end -->`인 줄이다(NAME = [a-z_]+).
  줄 끝 공백·CR(\r\r\n 포함)은 허용하고, 앞에 공백이 있으면 마커가 아니다(들여쓴 코드 예시 보호).
  CR 전용 줄끝(\r)도 줄 경계로 본다. 그런 파일에 새로 쓰는 줄끝은 \r이다.
- 짝이 안 맞거나 같은 이름이 두 번이거나 겹치면 MarkerError. 그 파일은 고치지 않는다.
- 구역 밖 바이트는 절대 바꾸지 않는다. 새 텍스트는 파일의 주된 줄끝(EOL)을 쓴다. BOM은 그대로 둔다.
"""
from __future__ import annotations

import re
from typing import Iterable, Iterator, NamedTuple

BOM = b"\xef\xbb\xbf"
# 줄끝 앞의 CR은 몇 개든 공백처럼 본다(텍스트 모드로 CRLF를 쓰면 \r\r\n이 된다).
_MARKER = re.compile(rb"<!-- kb:([a-z_]+):(start|end) -->[ \t\r]*")
_NAME = re.compile(r"[a-z_]+")


class MarkerError(ValueError):
    """관리 구역 마커가 잘못됨. 그 파일은 건드리지 않는다(D24)."""


class _Region(NamedTuple):
    name: str
    start_line: int  # 시작 마커 줄의 첫 바이트(BOM 뒤)
    body_start: int  # 시작 마커 줄 바로 다음
    body_end: int  # 끝 마커 줄의 첫 바이트
    end_line: int  # 끝 마커 줄 다음(줄끝 포함)


# ---------------------------------------------------------------- 내부 도우미


def _lines(data: bytes) -> Iterator[tuple[int, int, int]]:
    """(줄 시작, 줄끝 문자 앞, 다음 줄 시작)을 차례로 낸다."""
    pos, size = 0, len(data)
    while pos < size:
        nl = data.find(b"\n", pos)
        if nl < 0:
            yield pos, size, size
            return
        yield pos, nl, nl + 1
        pos = nl + 1


def _marker_lines(data: bytes) -> Iterator[tuple[int, int, int]]:
    """마커 찾기용 줄 나누기. `\\n` 말고도 CR 전용 줄끝(뒤에 \\r·\\n이 없는 \\r)에서 끊는다.

    `\\r\\r\\n`의 앞 CR들은 줄 내용에 남고(_MARKER가 허용), 줄끝은 `\\n`이다.
    """
    pos, size = 0, len(data)
    while pos < size:
        nl = data.find(b"\n", pos)
        stop = size if nl < 0 else nl
        cr = data.find(b"\r", pos, stop)
        while cr >= 0 and cr + 1 < size and data[cr + 1 : cr + 2] in (b"\r", b"\n"):
            cr = data.find(b"\r", cr + 1, stop)
        if cr >= 0:
            yield pos, cr, cr + 1
            pos = cr + 1
        elif nl < 0:
            yield pos, size, size
            return
        else:
            yield pos, nl, nl + 1
            pos = nl + 1


def _line_no(data: bytes, offset: int) -> int:
    return sum(1 for start, _, _ in _marker_lines(data) if start <= offset) or 1


def _eol(data: bytes) -> bytes:
    """새로 쓰는 줄끝. CR 전용 파일이면 b'\\r', 아니면 detect_eol."""
    if b"\n" not in data and b"\r" in data:
        return b"\r"
    return detect_eol(data)


def _markers(data: bytes) -> Iterator[tuple[str, str, int, int]]:
    """(이름, 'start'|'end', 마커 줄 시작, 다음 줄 시작)."""
    for start, content_end, end in _marker_lines(data):
        pos = 3 if start == 0 and data.startswith(BOM) else start
        if data[pos : pos + 1] != b"<":
            continue
        match = _MARKER.fullmatch(data, pos, content_end)
        if match:
            yield match.group(1).decode("ascii"), match.group(2).decode("ascii"), pos, end


def _regions(data: bytes) -> list[_Region]:
    regions: list[_Region] = []
    seen: set[str] = set()
    open_: tuple[str, int, int] | None = None
    for name, kind, line_start, line_end in _markers(data):
        where = f"{_line_no(data, line_start)}번째 줄"
        if kind == "start":
            if open_ is not None:
                raise MarkerError(f"구역 '{open_[0]}' 안에서 구역 '{name}'이 시작됩니다 ({where})")
            if name in seen:
                raise MarkerError(f"구역 '{name}'이 두 번 있습니다 ({where})")
            open_ = (name, line_start, line_end)
            continue
        if open_ is None:
            raise MarkerError(f"구역 '{name}'의 끝 표시만 있고 시작이 없습니다 ({where})")
        if open_[0] != name:
            raise MarkerError(f"구역 '{open_[0]}'과 '{name}'이 겹칩니다 ({where})")
        seen.add(name)
        regions.append(_Region(name, open_[1], open_[2], line_start, line_end))
        open_ = None
    if open_ is not None:
        where = f"{_line_no(data, open_[1])}번째 줄"
        raise MarkerError(f"구역 '{open_[0]}'의 시작 표시만 있고 끝이 없습니다 ({where})")
    return regions


def _find(data: bytes, name: str) -> _Region | None:
    _check_name(name)
    for region in _regions(data):
        if region.name == name:
            return region
    return None


def _check_name(name: str) -> None:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError(f"구역 이름이 잘못되었습니다: {name!r} (소문자와 _만)")


def _marker(name: str, kind: str) -> bytes:
    return f"<!-- kb:{name}:{kind} -->".encode("ascii")


def _encode_lines(text: str, eol: bytes) -> bytes:
    """텍스트를 UTF-8로 바꾸고 줄끝을 eol로 맞춘다. 마지막 줄도 eol로 끝난다."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n"):
        text += "\n"
    encoded = text.encode("utf-8")
    if any(True for _ in _markers(encoded)):
        raise MarkerError("새 내용에 구역 표시 줄이 들어 있습니다")
    return encoded.replace(b"\n", eol)


def _append_new(data: bytes, name: str, body: bytes, eol: bytes) -> bytes:
    """파일 끝에 구역을 만든다. 마지막 줄이 줄끝 없이 끝나면 줄끝 하나만 붙인다."""
    head = data
    if head and head != BOM and not head.endswith((b"\n", b"\r")):
        head += eol
    return head + _marker(name, "start") + eol + body + _marker(name, "end") + eol


# ---------------------------------------------------------------- 공개 API


def detect_eol(data: bytes) -> bytes:
    """CRLF가 LF보다 많으면 b'\\r\\n', 아니면 b'\\n'."""
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    return b"\r\n" if crlf > lf else b"\n"


def spans(data: bytes) -> list[tuple[str, int, int]]:
    """(이름, 본문 시작, 본문 끝) 바이트 위치. 본문은 두 마커 줄 사이."""
    return [(r.name, r.body_start, r.body_end) for r in _regions(data)]


def read_region(data: bytes, name: str) -> bytes | None:
    region = _find(data, name)
    return None if region is None else data[region.body_start : region.body_end]


def replace_region(data: bytes, name: str, body: str) -> bytes:
    """구역 본문을 통째로 바꾼다. 구역이 없으면 파일 끝에 만든다. 빈 본문이면 마커만 남는다."""
    region = _find(data, name)
    eol = _eol(data)
    new_body = _encode_lines(body, eol) if body else b""
    if region is None:
        return _append_new(data, name, new_body, eol)
    return data[: region.body_start] + new_body + data[region.body_end :]


def append_to_region(data: bytes, name: str, entries: Iterable[str]) -> bytes:
    """끝 마커 바로 앞에 항목을 넣는다. 기존 바이트는 바꾸지 않는다. 구역이 없으면 만든다."""
    region = _find(data, name)
    eol = _eol(data)
    payload = b"".join(_encode_lines(entry, eol) for entry in entries)
    if region is None:
        return _append_new(data, name, payload, eol)
    if not payload:
        return data
    return data[: region.body_end] + payload + data[region.body_end :]


def strip_markers(data: bytes, *, drop_regions: Iterable[str] = ()) -> bytes:
    """마커 줄을 모두 지운다. drop_regions에 든 구역은 본문까지 지운다."""
    drop = set(drop_regions)
    out: list[bytes] = []
    pos = 0
    for region in _regions(data):
        out.append(data[pos : region.start_line])
        if region.name not in drop:
            out.append(data[region.body_start : region.body_end])
        pos = region.end_line
    out.append(data[pos:])
    return b"".join(out)
