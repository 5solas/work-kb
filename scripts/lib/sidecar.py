"""이미지 사이드카 경로 판정(K5). ingest_images와 status가 같이 쓴다."""
from __future__ import annotations

import os
import unicodedata
from pathlib import Path

from lib import core, frontmatter
from lib.config import Config


def _norm_rel(rel: str) -> str:
    cleaned = rel.replace("\\", "/").strip()
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    cleaned = cleaned.lstrip("/")
    return os.path.normcase(unicodedata.normalize("NFC", cleaned))


def _image_rel(cfg: Config, image: Path) -> str:
    try:
        return image.relative_to(cfg.root).as_posix()
    except ValueError:
        pass
    try:
        return image.resolve().relative_to(cfg.root.resolve()).as_posix()
    except (OSError, ValueError):
        return image.as_posix()


def sidecar_for(cfg: Config, image: Path) -> Path:
    """이미지의 사이드카 경로를 정한다.

    `inbox/<stem>.md`가 없으면 그것을 쓴다. 이미 있으면 front matter의 `source_image`가
    이 이미지를 가리킬 때만 그것이고, 아니면(사용자 노트·다른 확장자) `inbox/<name.ext>.md`.
    """
    stem_md = cfg.path("inbox", f"{image.stem}.md")
    if not stem_md.exists():
        return stem_md
    expected = _norm_rel(_image_rel(cfg, image))
    try:
        fields = frontmatter.parse(stem_md.read_bytes())
    except (OSError, core.KbError):
        fields = {}
    source = fields.get("source_image")
    if isinstance(source, str) and source.strip() and _norm_rel(source) == expected:
        return stem_md
    return cfg.path("inbox", f"{image.name}.md")
