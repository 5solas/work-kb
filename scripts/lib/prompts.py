"""LLM 프롬프트 조립: `prompts/<작업>.md`, AGENTS.md 절 추출, 메시지 만들기.

시스템 메시지 형식(모든 B단계 작업 공통):

    KB-TASK: <작업>            ← 첫 줄. mock_llm의 canned 모드가 이 줄로 답을 고른다
    (빈 줄)
    AGENTS.md의 ## 역할, ## 금지, ## 태그, ## 작업 스키마, ## 답변 규칙 절(제목 포함, 이 순서)
    (빈 줄)
    prompts/<작업>.md 전문

코드 루트(`prompts/`, `AGENTS.md`)는 볼트가 아니라 코드 위치에서 찾는다(D07). 테스트는 `root`로 바꾼다.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

from lib import core

CODE_ROOT = Path(__file__).resolve().parents[2]
MARKER_PREFIX = "KB-TASK: "
AGENTS_NAME = "AGENTS.md"
PROMPTS_DIR = "prompts"
# 시스템 메시지에 넣는 AGENTS.md 절(이 순서). 하나라도 없으면 ConfigError.
AGENTS_SECTIONS: tuple[str, ...] = ("역할", "금지", "태그", "작업 스키마", "답변 규칙")

_TASK_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")


def _root(root: Path | str | None) -> Path:
    return Path(root) if root is not None else CODE_ROOT


def _read_text(path: Path, what: str) -> str:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise core.ConfigError(f"{what} 파일이 없습니다: {path}") from None
    except OSError as exc:
        raise core.ConfigError(f"{what} 파일을 읽지 못했습니다: {path} ({exc.strerror or exc})") from exc
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise core.ConfigError(f"{what} 파일이 UTF-8이 아닙니다: {path}") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n")


def marker(task: str) -> str:
    """시스템 메시지 첫 줄."""
    _check_task(task)
    return f"{MARKER_PREFIX}{task}"


def _check_task(task: str) -> None:
    if not isinstance(task, str) or not _TASK_NAME.match(task):
        raise core.ConfigError(f"작업 이름이 잘못되었습니다: {task!r}")


def load_prompt(task: str, *, root: Path | str | None = None) -> str:
    """`prompts/<task>.md`의 내용(앞뒤 빈 줄 제거)."""
    _check_task(task)
    path = _root(root) / PROMPTS_DIR / f"{task}.md"
    return _read_text(path, "프롬프트").strip("\n")


def section(text: str, heading: str) -> str | None:
    """마크다운에서 `## heading` 절을 제목 줄째로 돌려준다. 다음 같은 수준 이하 제목 전까지.

    코드 펜스 안의 `#` 줄은 제목으로 보지 않는다. 없으면 None.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    fence: str | None = None
    start: int | None = None
    level = 0
    for i, line in enumerate(lines):
        match = _FENCE.match(line)
        if match:
            mark = match.group(1)
            if fence is None:
                fence = mark
            elif set(line.strip()) == {fence[0]} and len(line.strip()) >= len(fence):
                fence = None
            continue
        if fence is not None:
            continue
        head = _HEADING.match(line)
        if not head:
            continue
        depth = len(head.group(1))
        if start is None:
            if head.group(2).strip() == heading:
                start, level = i, depth
        elif depth <= level:
            return "\n".join(lines[start:i]).strip("\n")
    if start is None:
        return None
    return "\n".join(lines[start:]).strip("\n")


def agents_sections(
    headings: Iterable[str] = AGENTS_SECTIONS, *, root: Path | str | None = None
) -> str:
    """AGENTS.md에서 고른 절들을 순서대로 이어 붙인다. 빠진 절이 있으면 ConfigError."""
    text = _read_text(_root(root) / AGENTS_NAME, "AGENTS.md")
    parts: list[str] = []
    missing: list[str] = []
    for heading in headings:
        body = section(text, heading)
        if body is None:
            missing.append(heading)
        else:
            parts.append(body)
    if missing:
        raise core.ConfigError(
            "AGENTS.md에 필요한 절이 없습니다: " + ", ".join(f"## {h}" for h in missing)
        )
    return "\n\n".join(parts)


def system_prompt(task: str, *, root: Path | str | None = None) -> str:
    """작업 표시 줄 + AGENTS.md 절 + prompts/<task>.md."""
    return "\n\n".join((marker(task), agents_sections(root=root), load_prompt(task, root=root))) + "\n"


def image_url(data: bytes, mime: str = "image/png") -> str:
    """이미지 바이트를 `data:` URL로."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("이미지 데이터가 비어 있습니다")
    return f"data:{mime};base64,{base64.b64encode(bytes(data)).decode('ascii')}"


def user_content(text: str, images: Sequence[str] = ()) -> str | list[dict[str, Any]]:
    """이미지가 없으면 문자열, 있으면 OpenAI 호환 content 조각 목록(글 먼저, 이미지는 `data:` URL)."""
    if not images:
        return text
    parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for url in images:
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


def build_messages(
    task: str,
    user: str,
    *,
    images: Sequence[str] = (),
    root: Path | str | None = None,
) -> list[dict[str, Any]]:
    """[system, user] 두 메시지. `images`는 `image_url()`로 만든 `data:` URL."""
    if not isinstance(user, str):
        raise TypeError("user는 문자열이어야 합니다")
    return [
        {"role": "system", "content": system_prompt(task, root=root)},
        {"role": "user", "content": user_content(user, images)},
    ]
