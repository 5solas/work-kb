"""B단계 이미지 읽기 (`kb.py ingest`, 작업 T6 `image_ingest`).

대상(§5.2, §6.5, D21, D64)
- `inbox/*.png|jpg|jpeg|webp`(드롭한 이미지). 읽기에 성공하면 이미지를
  `raw/assets/YYYY-MM-DD-HHMM-<원래이름>.<ext>`로 옮기고 사이드카 `inbox/<새 이름>.md`를 쓴다.
  실패하면 inbox에 그대로 두고 다음 실행에서 다시 시도한다. 이름이 이미 `YYYY-MM-DD-HHMM-`로
  시작하면 시각을 다시 붙이지 않는다. daily가 `![[inbox/<이름>]]`(또는 raw/assets에 없는 이름만)으로
  가리키는 inbox 이미지는 링크가 깨지지 않게 옮기지 않고 옆에 사이드카만 쓴다.
- daily `## 기록`에 `![[raw/assets/…]]`(또는 이름만 `![[파일.png]]`)으로 임베드된 `raw/assets`
  이미지 중 사이드카 `inbox/<이미지 이름>.md`가 없는 것. 이미지는 그대로 두고 사이드카만 쓴다.

중복(D21): 내용 sha256을 `.state/images.json`에 적는다. 이미 처리한 이미지와 같은 inbox 드롭은
`_trash/`로 옮기고(LLM 호출 없음), 같은 내용의 임베드 이미지는 `duplicate_of` 사이드카만 쓴다
(언급이 두 번 생기지 않게 추출 글은 원래 사이드카에만 있다). 원장이 없어져도 사이드카의
`source_image`로 다시 만든다.

LLM(§6.5, D42): pillow로 RGB로 바꾸고 긴 변을 1600px 이하로 줄여 JPEG(원본이 JPEG일 때) 또는 PNG로
한 장씩 보낸다(base64 `image_url`). 힌트는 파일명과 그 이미지를 붙인 daily 줄. 비전 탐지 결과가 참인
프로필만 쓴다(run_task needs_vision). 출력 `{"kind","text","summary","jira_keys","people"}` →
사이드카 front matter {source_image, ingested_at, model, kind} + `## 추출` 아래 `extracted` 구역
(키는 project_keys에 있는 것만 `[[KEY]]`, 사람은 이미지 글·힌트에 실제로 나온 이름만 `@이름`).

절대 하지 않는 것: daily 수정(바이트 불변), raw/assets 이미지 수정, 사이드카 덮어쓰기.
회사 환경 + 공개 호스트면 무엇보다 먼저 거부(LlmClient, 종료 5). `llm.enabled: false`, 비전 프로필
없음, 비전 프로필에 연결 안 됨 → 한 줄 알리고 종료 0("미처리 이미지 N개"). `--dry-run`은 LLM을
부르되 아무것도 쓰지 않고(원장도) 옮길 파일과 새 사이드카를 unified diff로 출력한다.

`run(cfg, ...)`은 다른 모듈(라이브 테스트)이 직접 부른다. 가져오기만 해서는 아무 일도 하지 않는다.
"""
from __future__ import annotations

import datetime as dt
import difflib
import errno
import io
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from lib import config, core, frontmatter, llm_client, managed, notes, prompts
from lib.config import Config

TASK = "image_ingest"
STATE = "images"
REGION = "extracted"
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
JPEG_SUFFIXES = frozenset({".jpg", ".jpeg"})
MAX_SIDE = 1600
JPEG_QUALITY = 85
HINT_CHARS = 500
KINDS = ("screenshot", "photo", "document")
KIND_LABELS = {"screenshot": "화면 캡처", "photo": "사진", "document": "문서"}
STAMPED = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}-")
JIRA_KEY = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")
PERSON = re.compile(rf"^{notes._PERSON_CHARS}+$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_RETRY_WINERRORS = {5, 32, 33}

Out = Callable[[str], None]


# ---------------------------------------------------------------- 대상 찾기


@dataclass
class Target:
    origin: str  # "inbox" | "daily"
    path: Path  # 이미지 파일
    rel: str  # 볼트 기준 상대 경로(`/`)
    hint: str = ""  # 이미지를 붙인 daily 줄(없으면 "")
    note: str | None = None  # 그 daily의 상대 경로
    pinned: bool = False  # inbox 이미지지만 daily가 가리켜서 옮기지 않음

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def moves(self) -> bool:
        return self.origin == "inbox" and not self.pinned


def _asset_name(target: str) -> str | None:
    """임베드 대상 → raw/assets 안의 파일명. raw/assets 밖이거나 이미지가 아니면 None(status와 같은 규칙)."""
    target = target.replace("\\", "/").strip()
    if target.startswith("raw/assets/"):
        target = target[len("raw/assets/"):]
    if not target or "/" in target or Path(target).suffix.lower() not in IMAGE_SUFFIXES:
        return None
    return target


def scan_daily(cfg: Config) -> tuple[dict[str, tuple[str, str]], set[str]]:
    """(raw/assets 파일명 → (daily 상대 경로, 첫 임베드 줄), daily의 모든 임베드 대상).

    첫째는 `## 기록` 줄만(코드 블록·관리 구역 제외), 둘째는 파일 전체(코드 블록 제외)에서 모은다.
    """
    assets: dict[str, tuple[str, str]] = {}
    refs: set[str] = set()
    base = cfg.path("daily")
    if not base.is_dir():
        return assets, refs
    for path in sorted(base.glob("*.md")):
        if not core.is_scannable(path) or not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"![[" not in data:
            continue
        rel = f"daily/{path.name}"
        try:
            span = notes.section_span(data, "기록")
            lines = list(notes.iter_lines(rel, data))
        except UnicodeDecodeError:
            continue
        for line in lines:
            if line.in_code or "![[" not in line.text:
                continue
            embeds = notes.image_embeds(line.text)
            refs.update(t.replace("\\", "/").strip() for t in embeds)
            if line.region is not None or span is None or not span[0] <= line.offset < span[1]:
                continue
            for target in embeds:
                name = _asset_name(target)
                if name is not None and name not in assets:
                    assets[name] = (rel, line.text)
    return assets, refs


def sidecar_path(cfg: Config, image_name: str) -> Path:
    return cfg.path("inbox", f"{Path(image_name).stem}.md")


def collect(cfg: Config) -> list[Target]:
    """처리할 이미지(status의 '미처리 이미지'와 같은 기준). inbox 먼저, 그다음 임베드, 각각 이름순."""
    assets, refs = scan_daily(cfg)
    asset_dir = cfg.path("raw", "assets")
    targets: list[Target] = []
    inbox = cfg.path("inbox")
    if inbox.is_dir():
        for path in sorted(inbox.iterdir()):
            if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file() or not core.is_scannable(path):
                continue
            if path.with_suffix(".md").exists():
                continue  # 옆에 사이드카가 이미 있다(옮기지 않은 채 처리됨)
            pinned = f"inbox/{path.name}" in refs or (path.name in refs and not (asset_dir / path.name).is_file())
            targets.append(Target("inbox", path, f"inbox/{path.name}", pinned=pinned))
    for name in sorted(assets):
        image = asset_dir / name
        if not image.is_file() or sidecar_path(cfg, name).exists():
            continue
        note, hint = assets[name]
        targets.append(Target("daily", image, f"raw/assets/{name}", hint=hint, note=note))
    return targets


# ---------------------------------------------------------------- 원장


def _inside(cfg: Config, rel: str) -> Path | None:
    try:
        return cfg.path(*[p for p in rel.replace("\\", "/").split("/") if p])
    except core.KbError:
        return None


def load_ledger(cfg: Config) -> dict[str, dict]:
    """`.state/images.json` → {"images": {sha: 항목}, "skipped": {sha: 항목}}.

    원장에 없는 사이드카(원장을 지웠거나 옛 실행)는 `source_image`를 해시해 채운다(메모리에서만,
    다음 저장 때 함께 기록된다).
    """
    state = core.load_state(cfg.root, STATE)
    images = state.get("images") if isinstance(state, Mapping) else None
    skipped = state.get("skipped") if isinstance(state, Mapping) else None
    ledger: dict[str, dict] = {
        "images": {k: dict(v) for k, v in images.items() if isinstance(v, Mapping)} if isinstance(images, Mapping) else {},
        "skipped": {k: dict(v) for k, v in skipped.items() if isinstance(v, Mapping)} if isinstance(skipped, Mapping) else {},
    }
    covered = {str(v.get("source_image")) for v in ledger["images"].values()}
    inbox = cfg.path("inbox")
    if not inbox.is_dir():
        return ledger
    for md in sorted(inbox.glob("*.md")):
        if not core.is_scannable(md) or not md.is_file():
            continue
        try:
            data = md.read_bytes()
            if b"source_image" not in data[:4096]:
                continue
            fields = frontmatter.parse(data)
        except (OSError, core.KbError):
            continue
        source = fields.get("source_image")
        if not isinstance(source, str) or not source.strip() or source in covered:
            continue
        image = _inside(cfg, source)
        if image is None or not image.is_file():
            continue
        try:
            sha = core.sha256_hex(image.read_bytes())
        except OSError:
            continue
        covered.add(source)
        kind, model = fields.get("kind"), fields.get("model")
        ledger["images"].setdefault(
            sha,
            {
                "source_image": source,
                "sidecar": f"inbox/{md.name}",
                "kind": kind if isinstance(kind, str) else "",
                "model": model if isinstance(model, str) else "",
                "rebuilt": True,
            },
        )
    return ledger


# ---------------------------------------------------------------- 이미지 준비


def _pil():
    try:
        from PIL import Image, ImageOps
        from PIL.Image import open as open_image  # 이름이 open이면 인코딩 규칙 검사가 텍스트 open으로 본다
    except ImportError as exc:  # requirements.txt에 있지만 설치가 빠졌을 때
        raise core.ConfigError("pillow가 설치되어 있지 않습니다 → .venv에서 pip install -r requirements.txt") from exc
    return Image, ImageOps, open_image


def prepare_image(data: bytes, suffix: str) -> tuple[bytes, str]:
    """(보낼 바이트, MIME). RGB로 바꾸고(투명은 흰 바탕) 긴 변을 MAX_SIDE 이하로 줄인다(D42).

    원본이 JPEG면 JPEG, 그 밖(PNG·WebP)은 PNG. 읽을 수 없으면 ValueError.
    """
    Image, ImageOps, open_image = _pil()
    try:
        with open_image(io.BytesIO(data)) as opened:
            opened.load()
            image = ImageOps.exif_transpose(opened) or opened
            if image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info):
                rgba = image.convert("RGBA")
                rgb = Image.new("RGB", rgba.size, (255, 255, 255))
                rgb.paste(rgba, mask=rgba.getchannel("A"))
            else:
                rgb = image.convert("RGB")
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError(f"이미지를 읽지 못했습니다 ({type(exc).__name__}: {exc})") from exc
    if max(rgb.size) > MAX_SIDE:
        rgb.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    if suffix.lower() in JPEG_SUFFIXES:
        rgb.save(buf, "JPEG", quality=JPEG_QUALITY)
        return buf.getvalue(), "image/jpeg"
    rgb.save(buf, "PNG")
    return buf.getvalue(), "image/png"


# ---------------------------------------------------------------- 메시지·검증


def user_text(name: str, hint: str, project_keys: list[str], limit: int | None = None) -> str:
    """사용자 메시지의 글 부분(이미지는 따로 붙는다). 힌트는 HINT_CHARS와 입력 한도 안으로 자른다."""
    cap = HINT_CHARS
    if limit is not None:
        cap = max(0, min(cap, int(limit * 2.5) - 400))
    hint = " ".join(hint.split())
    if len(hint) > cap:
        hint = hint[: cap - 1] + "…" if cap > 1 else ""
    return (
        "이미지 1장을 읽고 JSON 객체 하나로 답하세요.\n"
        f"- 파일명: {name}\n"
        f"- 이 이미지를 붙인 메모 줄: {hint or '(없음)'}\n"
        f"- 프로젝트 키: {', '.join(project_keys) or '(없음)'}"
    )


def messages_for(cfg: Config, target: Target, image_url: str) -> Callable[[int], list[dict]]:
    """run_task용 `messages_for(limit)`. 시스템 메시지는 prompts.build_messages(KB-TASK 표시 줄 포함)."""
    keys = cfg.project_keys

    def build(limit: int) -> list[dict]:
        return prompts.build_messages(TASK, user_text(target.name, target.hint, keys, limit), images=[image_url])

    return build


def validate(data: Mapping[str, Any]) -> None:
    """T6 스키마. kind·text·summary는 필수, jira_keys·people은 없으면 빈 목록으로 본다."""
    if not isinstance(data, Mapping):
        raise ValueError("JSON 객체가 아닙니다")
    kind = data.get("kind")
    if not isinstance(kind, str) or kind.strip().lower() not in KINDS:
        raise ValueError(f"kind는 {', '.join(KINDS)} 중 하나여야 합니다: {kind!r}")
    for name in ("text", "summary"):
        if not isinstance(data.get(name), str):
            raise ValueError(f"{name}은(는) 문자열이어야 합니다")
    for name in ("jira_keys", "people"):
        value = data.get(name)
        if value is None:
            continue
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise ValueError(f"{name}은(는) 문자열 목록이어야 합니다")


@dataclass(frozen=True)
class Extraction:
    kind: str
    text: str
    summary: str
    jira_keys: tuple[str, ...]
    people: tuple[str, ...]


def _clean_line(text: str) -> str:
    return _CONTROL.sub("", text).strip()


def clean(data: Mapping[str, Any], project_keys: list[str], hint: str = "") -> Extraction:
    """검증된 답을 정리한다. 키는 project_keys의 것만, 사람은 이미지 글이나 힌트에 나온 이름만."""
    text = "\n".join(line for line in (_clean_line(x) for x in str(data.get("text") or "").splitlines()) if line)
    summary = " ".join(_clean_line(str(data.get("summary") or "")).split())
    allowed = set(project_keys)
    keys: list[str] = []
    for raw in data.get("jira_keys") or []:
        key = raw.strip().strip("[]").strip().upper()
        if key.startswith("JIRA/"):
            key = key[len("JIRA/"):]
        if JIRA_KEY.match(key) and key.rsplit("-", 1)[0] in allowed and key not in keys:
            keys.append(key)
    seen_in = f"{text}\n{hint}"
    people: list[str] = []
    for raw in data.get("people") or []:
        name = "".join(raw.split()).lstrip("@")
        if name and PERSON.match(name) and name in seen_in and name not in people:
            people.append(name)
    return Extraction(str(data["kind"]).strip().lower(), text, summary, tuple(keys), tuple(people))


# ---------------------------------------------------------------- 사이드카


def render_extracted(ex: Extraction, prefix: str = "@") -> str:
    """`extracted` 구역 본문. 읽은 글은 줄마다 `- `를 붙여(마커·제목·코드 울타리로 읽히지 않게) 둔다."""
    lines = [f"- 종류: {KIND_LABELS.get(ex.kind, ex.kind)}"]
    if ex.summary:
        lines.append(f"- 요약: {ex.summary}")
    if ex.jira_keys:
        lines.append("- 키: " + " · ".join(f"[[{key}]]" for key in ex.jira_keys))
    if ex.people:
        lines.append("- 사람: " + " · ".join(f"{prefix}{name}" for name in ex.people))
    if ex.text:
        lines += ["", "### 읽은 글", *(f"- {line}" for line in ex.text.splitlines())]
    return "\n".join(lines)


def render_sidecar(
    *,
    source_image: str,
    ingested_at: str,
    model: str,
    kind: str,
    extracted: str,
    duplicate_of: str | None = None,
) -> bytes:
    """사이드카 전체 바이트: front matter, 이미지 임베드, `## 추출`, `extracted` 구역."""
    fields = {
        "source_image": source_image,
        "ingested_at": ingested_at,
        "model": model,
        "kind": kind,
        "duplicate_of": duplicate_of,
    }
    head = frontmatter.render(fields) + f"![[{source_image}]]\n\n## 추출\n".encode("utf-8")
    return managed.replace_region(head, REGION, extracted)


# ---------------------------------------------------------------- 파일 옮기기


def _move(src: Path, dst: Path, retries: int = 8) -> None:
    """src → dst. dst가 있으면 ConcurrentEdit. 다른 프로그램이 잡고 있으면 잠시 기다렸다 다시."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    delay = 0.05
    for attempt in range(retries + 1):
        if dst.exists():
            raise core.ConcurrentEdit(f"{dst}: 이미 있어 옮기지 않았습니다")
        try:
            os.rename(src, dst)
            return
        except FileExistsError as exc:
            raise core.ConcurrentEdit(f"{dst}: 이미 있어 옮기지 않았습니다") from exc
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in _RETRY_WINERRORS or attempt == retries:
                raise core.FileBusy(f"{src}: 다른 프로그램이 파일을 잡고 있어 옮기지 못했습니다 ({exc})") from exc
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            shutil.copy2(src, dst)  # 다른 드라이브
            os.unlink(src)
            return
        time.sleep(delay)
        delay = min(delay * 2, 1.0)


def asset_destination(cfg: Config, original: Path, when: dt.datetime, taken: set[str] = frozenset()) -> Path:
    """`raw/assets/YYYY-MM-DD-HHMM-<원래이름>.<ext>`. 같은 이름의 이미지나 사이드카가 있으면 `-2`, `-3`…

    `taken`은 시험 실행에서 이미 배정한 이름(소문자 파일명)이다.
    """
    stem = core.link_safe_name(original.stem)
    if not STAMPED.match(stem):
        stem = f"{when:%Y-%m-%d-%H%M}-{stem}"
    suffix = original.suffix.lower()
    assets = cfg.path("raw", "assets")
    for i in range(1, 10_000):
        name = stem if i == 1 else f"{stem}-{i}"
        candidate = assets / f"{name}{suffix}"
        if candidate.name.lower() in taken or candidate.exists() or sidecar_path(cfg, candidate.name).exists():
            continue
        return candidate
    raise core.KbError(f"{original}: 빈 이름을 찾지 못했습니다")


def _rel(cfg: Config, path: Path) -> str:
    return notes.rel_path(cfg, path)


# ---------------------------------------------------------------- 실행


@dataclass
class Report:
    status: str = "ok"  # ok | empty | disabled | no_vision | no_connection
    pending: int = 0  # 시작할 때 미처리 이미지 수
    processed: list[dict] = field(default_factory=list)
    duplicates: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)  # LLM 실패(이미지는 그대로, 다음 실행에서 다시)
    failed: list[dict] = field(default_factory=list)  # 읽기·쓰기 실패
    dry_run: bool = False

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "pending": self.pending,
            "processed": self.processed,
            "duplicates": self.duplicates,
            "skipped": self.skipped,
            "failed": self.failed,
            "dry_run": self.dry_run,
        }


class _Run:
    def __init__(self, cfg: Config, client: llm_client.LlmClient, *, dry_run: bool, out: Out) -> None:
        self.cfg = cfg
        self.client = client
        self.dry_run = dry_run
        self.out = out
        self.report = Report(dry_run=dry_run)
        self.ledger = load_ledger(cfg)
        self.failed_hashes: set[str] = set()
        self.prefix = str(cfg.get("people_prefix") or "@")
        self.planned: set[str] = set()  # 시험 실행에서 배정한 이름(소문자)

    # ------------------------------------------------------------ 공통

    def _now(self) -> dt.datetime:
        return core.now(self.cfg.tz)

    def _model(self, profile: str | None) -> str:
        try:
            value = self.cfg.profiles().get(profile or "", {}).get("model")
        except core.KbError:
            value = None
        return str(value) if value else (profile or "")

    def _save_ledger(self) -> None:
        if not self.dry_run:
            core.save_state(self.cfg.root, STATE, self.ledger)

    def _show_new_file(self, rel: str, data: bytes) -> None:
        text = data.decode("utf-8")
        for line in difflib.unified_diff([], text.splitlines(keepends=True), fromfile="/dev/null", tofile=f"b/{rel}"):
            self.out(line.rstrip("\n"))

    def _fail(self, target: Target, why: str) -> None:
        self.report.failed.append({"image": target.rel, "error": why})
        self.out(f"- 실패: {target.rel} — {why}")

    # ------------------------------------------------------------ 한 장

    def handle(self, target: Target) -> None:
        try:
            data = target.path.read_bytes()
        except OSError as exc:
            self._fail(target, f"읽지 못했습니다 ({exc.strerror or exc})")
            return
        sha = core.sha256_hex(data)
        known = self.ledger["images"].get(sha)
        if known is not None:
            self._duplicate(target, sha, known)
            return
        if sha in self.failed_hashes:
            self._skip(target, sha, "같은 이미지가 이번 실행에서 이미 실패했습니다", record=False)
            return
        try:
            payload, mime = prepare_image(data, target.path.suffix)
        except ValueError as exc:
            self.failed_hashes.add(sha)
            self._fail(target, str(exc))
            return
        url = prompts.image_url(payload, mime)
        outcome = self.client.run_task(TASK, messages_for(self.cfg, target, url), validate=validate, needs_vision=True)
        if outcome.status != "ok" or outcome.data is None:
            self.failed_hashes.add(sha)
            tried = ", ".join(f"{a.get('profile')}={a.get('status')}" for a in outcome.attempts) or outcome.status
            self._skip(target, sha, f"LLM이 읽지 못했습니다 ({tried})")
            return
        self._write(target, sha, clean(outcome.data, self.cfg.project_keys, target.hint), outcome.profile)

    def _skip(self, target: Target, sha: str, why: str, *, record: bool = True) -> None:
        self.report.skipped.append({"image": target.rel, "error": why})
        self.out(f"- 건너뜀: {target.rel} — {why}. 다음 실행에서 다시 시도합니다")
        if not record or self.dry_run:
            return
        entry = self.ledger["skipped"].get(sha) or {}
        self.ledger["skipped"][sha] = {
            "image": target.rel,
            "count": int(entry.get("count") or 0) + 1,
            "at": self._now().isoformat(timespec="seconds"),
            "error": core.redact(why)[:300],
        }
        with core.kb_lock(self.cfg.root, "ingest"):
            self._save_ledger()

    # ------------------------------------------------------------ 중복

    def _duplicate(self, target: Target, sha: str, known: Mapping[str, Any]) -> None:
        original = str(known.get("sidecar") or known.get("source_image") or "?")
        if target.moves:
            self._trash(target, sha, original)
            return
        # 임베드(또는 daily가 가리키는 inbox) 이미지: 옮기지 않고 duplicate_of 사이드카만
        sidecar = sidecar_path(self.cfg, target.name)
        rel = _rel(self.cfg, sidecar)
        link = original[:-3] if original.endswith(".md") else original
        body = render_sidecar(
            source_image=target.rel,
            ingested_at=self._now().isoformat(timespec="seconds"),
            model=str(known.get("model") or ""),
            kind=str(known.get("kind") or ""),
            extracted=f"- 같은 이미지를 이미 읽었습니다: [[{link}]]",
            duplicate_of=original,
        )
        if self.dry_run:
            self.report.duplicates.append({"image": target.rel, "sidecar": rel, "same_as": original})
            self.out(f"- 중복(예정): {target.rel} → 사이드카 {rel} (이미 읽은 {original}와 같은 이미지)")
            self._show_new_file(rel, body)
            return
        try:
            with core.kb_lock(self.cfg.root, "ingest"):
                core.atomic_write(sidecar, body, expect_sha256=core.MUST_NOT_EXIST)
        except core.LockBusy:
            raise
        except (core.KbError, OSError) as exc:
            self._fail(target, f"사이드카를 쓰지 못했습니다 ({exc})")
            return
        self.report.duplicates.append({"image": target.rel, "sidecar": rel, "same_as": original})
        self.out(f"- 중복: {target.rel} → 사이드카 {rel} (이미 읽은 {original}와 같은 이미지)")

    def _trash(self, target: Target, sha: str, original: str) -> None:
        def destination() -> Path:
            path = core.unique_path(self.cfg.path("_trash", target.name))
            while path.name.lower() in self.planned:
                path = core.unique_path(path.with_name(f"{path.stem}-2{path.suffix}"))
            return path

        if self.dry_run:
            dest = destination()
            self.planned.add(dest.name.lower())
            rel = _rel(self.cfg, dest)
            self.report.duplicates.append({"image": target.rel, "moved_to": rel, "same_as": original})
            self.out(f"- 중복(예정): {target.rel} → {rel} (이미 읽은 {original}와 같은 이미지)")
            return
        try:
            with core.kb_lock(self.cfg.root, "ingest"):
                if not target.path.is_file() or core.sha256_hex(target.path.read_bytes()) != sha:
                    raise core.ConcurrentEdit(f"{target.rel}: 읽은 뒤에 바뀌었거나 없어졌습니다")
                dest = destination()
                _move(target.path, dest)
        except core.LockBusy:
            raise
        except (core.KbError, OSError) as exc:
            self._fail(target, f"_trash로 옮기지 못했습니다 ({exc})")
            return
        rel = _rel(self.cfg, dest)
        self.report.duplicates.append({"image": target.rel, "moved_to": rel, "same_as": original})
        self.out(f"- 중복: {target.rel} → {rel} (이미 읽은 {original}와 같은 이미지)")

    # ------------------------------------------------------------ 쓰기

    def _plan(self, target: Target, ex: Extraction, profile: str | None, now: dt.datetime) -> tuple[Path, Path, bytes, dict]:
        """(이미지 최종 위치, 사이드카, 사이드카 바이트, 원장 항목)."""
        asset = asset_destination(self.cfg, target.path, now, self.planned) if target.moves else target.path
        sidecar = sidecar_path(self.cfg, asset.name)
        source_rel, sidecar_rel = _rel(self.cfg, asset), _rel(self.cfg, sidecar)
        model = self._model(profile)
        at = now.isoformat(timespec="seconds")
        body = render_sidecar(
            source_image=source_rel, ingested_at=at, model=model, kind=ex.kind,
            extracted=render_extracted(ex, self.prefix),
        )
        entry = {
            "source_image": source_rel, "sidecar": sidecar_rel, "origin": target.origin, "original": target.rel,
            "profile": profile, "model": model, "kind": ex.kind, "at": at,
        }
        return asset, sidecar, body, entry

    def _write(self, target: Target, sha: str, ex: Extraction, profile: str | None) -> None:
        now = self._now()
        if self.dry_run:
            asset, sidecar, body, entry = self._plan(target, ex, profile, now)
            self.planned.add(asset.name.lower())
            self.ledger["images"][sha] = entry  # 이번 실행 안의 중복 판정용(저장하지 않음)
            self.report.processed.append(self._done(target, entry))
            if target.moves:
                self.out(f"- 이동(예정): {target.rel} → {entry['source_image']}")
            self._show_new_file(entry["sidecar"], body)
            return
        try:
            with core.kb_lock(self.cfg.root, "ingest"):
                # LLM을 기다리는 동안 바뀐 것이 없는지 잠금 안에서 다시 본다(D26)
                if not target.path.is_file() or core.sha256_hex(target.path.read_bytes()) != sha:
                    raise core.ConcurrentEdit(f"{target.rel}: 읽은 뒤에 바뀌었거나 없어졌습니다")
                asset, sidecar, body, entry = self._plan(target, ex, profile, now)
                if target.moves:
                    _move(target.path, asset)
                try:
                    core.atomic_write(sidecar, body, expect_sha256=core.MUST_NOT_EXIST)
                except BaseException:
                    if target.moves:  # 사이드카 없이 raw/assets에 남지 않게 되돌린다
                        try:
                            _move(asset, target.path)
                        except (core.KbError, OSError) as exc:
                            self.out(f"  되돌리지 못했습니다: {entry['source_image']} ({exc})")
                    raise
                self.ledger["images"][sha] = entry
                self.ledger["skipped"].pop(sha, None)
                self._save_ledger()
        except core.LockBusy:
            raise
        except (core.KbError, OSError) as exc:
            self._fail(target, f"쓰지 못했습니다 ({exc})")
            return
        self.report.processed.append(self._done(target, entry))
        moved = f" → {entry['source_image']}" if target.moves else ""
        self.out(f"- 처리: {target.rel}{moved}, 사이드카 {entry['sidecar']} ({profile})")

    @staticmethod
    def _done(target: Target, entry: Mapping[str, Any]) -> dict:
        return {
            "image": target.rel,
            "source_image": entry["source_image"],
            "sidecar": entry["sidecar"],
            "profile": entry["profile"],
            "kind": entry["kind"],
        }


def vision_profiles(client: llm_client.LlmClient) -> list[str]:
    """작업 프로필 순서(task.profile → fallback_order) 중 `detected.vision`이 참인 것."""
    names = set(client.profile_names())
    return [n for n in client.profile_order(TASK) if n in names and client.detected(n).get("vision") is True]


def run(
    cfg: Config,
    *,
    dry_run: bool = False,
    client: llm_client.LlmClient | None = None,
    out: Out = print,
) -> dict:
    """이미지 읽기 한 번. 결과는 Report.as_dict(). 회사 환경 공개 호스트는 PublicHostRefused(종료 5)."""
    client = client if client is not None else llm_client.LlmClient(cfg)  # D44: 무엇보다 먼저
    state = _Run(cfg, client, dry_run=dry_run, out=out)
    report = state.report
    targets = collect(cfg)
    report.pending = len(targets)

    def stop(status: str, message: str) -> dict:
        report.status = status
        out(message)
        if not dry_run:
            core.get_logger(cfg.root).info("ingest: %s", message)
        return report.as_dict()

    if not targets:
        report.status = "empty"
        out("이미지: 읽을 이미지가 없습니다")
        return report.as_dict()
    if not client.enabled:
        return stop("disabled", f"LLM이 꺼져 있어 이미지 읽기를 건너뜁니다 (미처리 이미지 {len(targets)}개)")
    vision = vision_profiles(client)
    if not vision:
        return stop(
            "no_vision",
            f"비전 프로필이 없어 이미지 읽기를 건너뜁니다 (미처리 이미지 {len(targets)}개). "
            "탐지: kb.py init --section llm 또는 kb.py doctor --redetect",
        )
    if not client.connected_profiles(vision):
        detail = "; ".join(f"{name}: {client.last_probe.get(name, '?')}" for name in vision)
        return stop(
            "no_connection",
            f"비전 프로필에 연결하지 못해 이미지 읽기를 건너뜁니다 (미처리 이미지 {len(targets)}개; {detail})",
        )

    head = "이미지 읽기(시험 실행, 아무것도 쓰지 않음)" if dry_run else "이미지 읽기"
    n_inbox = sum(1 for t in targets if t.origin == "inbox")
    out(f"{head}: {len(targets)}개 (inbox {n_inbox}, daily 임베드 {len(targets) - n_inbox}) · 비전 프로필 {', '.join(vision)}")
    for target in targets:
        state.handle(target)
    summary = (
        f"결과: 읽음 {len(report.processed)}, 중복 {len(report.duplicates)}, "
        f"건너뜀 {len(report.skipped)}, 실패 {len(report.failed)}"
    )
    out(summary)
    if not dry_run:
        core.get_logger(cfg.root).info("ingest: %s", summary)
    return report.as_dict()


def main(argv: list[str]) -> int:
    parser = core.make_parser("kb.py ingest", "B단계: 이미지에서 글을 읽어 사이드카(inbox/<이름>.md)에 쓴다 (비전 LLM)")
    parser.add_argument("--dry-run", action="store_true", help="LLM은 부르되 아무것도 쓰지 않고 바꿀 내용을 출력")
    args = parser.parse_args(argv)
    cfg = config.load(args.root)
    run(cfg, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    core.run_main(main)
