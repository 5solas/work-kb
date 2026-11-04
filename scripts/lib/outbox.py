"""Jira 쓰기 큐 `jira/outbox.jsonl` (지시서 §5.8, D26·D52).

한 줄에 기록 하나(JSON, UTF-8, LF):

    {"id":"01JC…","ts":"2026-11-05T16:05:00+09:00","action":"comment","key":"KB-12",
     "payload":{"body":"…"},"source":"daily/2026-11-05.md:14","status":"pending",
     "attempts":0,"last_error":null,"done_at":null}

- `id`: ULID 꼴(앞 10자 = 밀리초 시각, 뒤 16자 = 난수, Crockford base32). 문자열 순서 = 만든 순서.
  같은 프로세스 안에서는 늘 커진다. 시각은 실제 시계를 쓴다(KB_FAKE_NOW와 무관하게 순서가 맞게).
- `status`: pending · done · failed. `last_error`가 "uncertain"이면 보냈지만 결과를 모른다.
- 덧붙는 필드: `result`(예: {"key": "KB-16"}), `error_detail`, 그 밖은 그대로 보존한다.

쓰기(append·update)는 `core.kb_lock` 안에서 파일 전체를 읽어 같은 폴더 임시 파일 → os.replace로 바꾼다.
고치지 않은 줄(읽지 못한 줄 포함)은 바이트 그대로 둔다. 바뀐 것이 없으면 쓰지 않는다.
네트워크 요청은 이 모듈에서 하지 않는다(잠금을 쥔 채 보내지 않게, D26).
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from lib import core

REL = ("jira", "outbox.jsonl")
LOCK_OWNER = "jira_act"
PENDING, DONE, FAILED = "pending", "done", "failed"
STATUSES = (PENDING, DONE, FAILED)
UNCERTAIN = "uncertain"
MAX_ATTEMPTS = 5
FIELD_ORDER = ("id", "ts", "action", "key", "payload", "source", "status", "attempts", "last_error", "done_at")

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ID_LEN = 26
_id_lock = threading.Lock()
_last_id = ""


# ---------------------------------------------------------------- id


def _encode(number: int, width: int) -> str:
    out = []
    for _ in range(width):
        out.append(_CROCKFORD[number & 31])
        number >>= 5
    return "".join(reversed(out))


def _decode(text: str) -> int:
    value = 0
    for ch in text:
        value = value * 32 + _CROCKFORD.index(ch)
    return value


def new_id(now_ms: int | None = None) -> str:
    """시각순으로 정렬되는 26자 id. 같은 밀리초에 여럿 만들어도 앞의 것보다 크다."""
    global _last_id
    ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    candidate = _encode(ms & ((1 << 48) - 1), 10) + _encode(int.from_bytes(os.urandom(10), "big"), 16)
    with _id_lock:
        if _last_id and candidate <= _last_id:
            candidate = _encode(_decode(_last_id) + 1, _ID_LEN)
        _last_id = candidate
    return candidate


# ---------------------------------------------------------------- 읽기


@dataclass
class _Line:
    record: dict | None  # 읽지 못한 줄이면 None
    raw: bytes  # 줄 끝(LF) 포함 원본


def path_for(root: Path | str) -> Path:
    return Path(root).joinpath(*REL)


def _parse(data: bytes) -> list[_Line]:
    lines: list[_Line] = []
    for raw in data.splitlines(keepends=True):
        text = raw.strip()
        record: dict | None = None
        if text:
            try:
                value = json.loads(text.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                value = None
            if isinstance(value, dict) and isinstance(value.get("id"), str):
                record = value
        lines.append(_Line(record, raw))
    return lines


def _read(root: Path | str) -> tuple[list[_Line], bytes | None]:
    path = path_for(root)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return [], None
    return _parse(data), data


def records(root: Path | str) -> list[dict]:
    """읽을 수 있는 기록 전부(파일 순서 = id 순서). 파일이 없으면 []."""
    lines, _data = _read(root)
    return [line.record for line in lines if line.record is not None]


def get(root: Path | str, record_id: str) -> dict | None:
    for record in records(root):
        if record.get("id") == record_id:
            return record
    return None


def broken_lines(root: Path | str) -> int:
    """읽지 못한 줄 수(빈 줄 제외)."""
    lines, _data = _read(root)
    return sum(1 for line in lines if line.record is None and line.raw.strip())


# ---------------------------------------------------------------- 쓰기


def render(record: Mapping[str, Any]) -> bytes:
    """기록 하나 → 한 줄(LF 포함). §5.8 필드 순서 뒤에 나머지 필드."""
    ordered = {name: record.get(name) for name in FIELD_ORDER}
    for name, value in record.items():
        if name not in ordered:
            ordered[name] = value
    return (json.dumps(ordered, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def make_record(
    *,
    action: str,
    key: str,
    payload: Mapping[str, Any],
    ts: str,
    source: str | None = None,
) -> dict:
    """새 pending 기록(아직 파일에 쓰지 않음)."""
    return {
        "id": new_id(),
        "ts": ts,
        "action": action,
        "key": key,
        "payload": dict(payload),
        "source": source,
        "status": PENDING,
        "attempts": 0,
        "last_error": None,
        "done_at": None,
    }


def _write(root: Path | str, lines: Iterable[_Line], original: bytes | None) -> None:
    data = b"".join(line.raw for line in lines)
    if original is not None and data == original:
        return
    expect = core.MUST_NOT_EXIST if original is None else core.sha256_hex(original)
    core.atomic_write(path_for(root), data, expect_sha256=expect)


def append(root: Path | str, record: Mapping[str, Any]) -> dict:
    """기록을 끝에 더한다(잠금 안). 같은 id가 이미 있으면 ValueError."""
    record = dict(record)
    with core.kb_lock(root, LOCK_OWNER):
        lines, original = _read(root)
        if any(line.record is not None and line.record.get("id") == record["id"] for line in lines):
            raise ValueError(f"outbox에 같은 id가 이미 있습니다: {record['id']}")
        if lines and not lines[-1].raw.endswith(b"\n"):
            lines[-1] = _Line(lines[-1].record, lines[-1].raw + b"\n")
        lines.append(_Line(record, render(record)))
        _write(root, lines, original)
    return record


def update(root: Path | str, record_id: str, changes: Mapping[str, Any]) -> dict:
    """id의 기록에 changes를 덮어 쓴다(잠금 안). 바뀐 값이 없으면 파일을 건드리지 않는다.

    없는 id면 KeyError. 고친 기록(새 dict)을 돌려준다.
    """
    with core.kb_lock(root, LOCK_OWNER):
        lines, original = _read(root)
        for index, line in enumerate(lines):
            if line.record is not None and line.record.get("id") == record_id:
                merged = {**line.record, **dict(changes)}
                if merged != line.record:
                    eol_missing = not line.raw.endswith(b"\n")
                    raw = render(merged)
                    lines[index] = _Line(merged, raw[:-1] if eol_missing else raw)
                    _write(root, lines, original)
                return merged
    raise KeyError(record_id)


def counts(items: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    out = {status: 0 for status in STATUSES}
    for item in items:
        status = item.get("status")
        if status in out:
            out[status] += 1
    return out
