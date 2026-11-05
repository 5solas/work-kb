"""kb.py wheels: 오프라인 설치용 wheels/ 묶음을 내려받는다 (지시서 §2·§9.2, D02).

하는 일:
- 대상 Python 버전마다(기본 3.13, 3.14) 이 venv의 pip로
  `pip download --only-binary=:all: --platform win_amd64 --implementation cp --python-version X --abi cpX
  -r requirements.txt -d <dir>`를 실행한다. 버전이 달라도 한 폴더에 받는다(순수 Python wheel은 함께 쓴다).
- Python 3.15용으로 PyYAML 원본(sdist)을 `<dir>/sdist/`에 받는다(`--no-binary pyyaml`). 3.15에는 PyYAML
  wheel이 없기 때문이다(D01·D02). 이 원본으로 집에서 cp315 wheel을 빌드할 수 있다(docs/PACKAGING.md).
- 결과 파일 목록과 `<dir>/MANIFEST.txt`(대상별 wheel, sha256)를 남긴다. 대상마다 requirements.txt의
  패키지가 모두 있는지 확인하고, 빠졌으면 종료 1.

네트워크를 쓰는 명령이라 KB_AGENT=1에서는 거부된다(kb.py 명령 표). `--dry-run`은 명령만 보여 준다.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from lib import core

CODE_ROOT = Path(__file__).resolve().parents[1]  # 저장소 폴더 (볼트 루트와 무관)
REQUIREMENTS = CODE_ROOT / "requirements.txt"
DEFAULT_DIR = "wheels"
DEFAULT_TARGETS = ("3.13", "3.14")
PLATFORM = "win_amd64"
SDIST_PACKAGE = "pyyaml"
SDIST_DIR = "sdist"
MANIFEST = "MANIFEST.txt"

_TARGET = re.compile(r"^3\.(\d{1,2})$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


def normalize(name: str) -> str:
    """PEP 503 이름 정규화: `PyYAML` → `pyyaml`, `jaraco.classes` → `jaraco-classes`."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_targets(text: str) -> list[str]:
    """`3.13,3.14` → ["3.13", "3.14"]. 3.13 미만이나 형식이 틀리면 ConfigError."""
    targets: list[str] = []
    for part in text.split(","):
        item = part.strip()
        if not item:
            continue
        match = _TARGET.match(item)
        if match is None:
            raise core.ConfigError(f"--targets: '{item}'은(는) 3.13 같은 Python 버전이 아닙니다")
        if int(match.group(1)) < 13:
            raise core.ConfigError(f"--targets: {item}은(는) 지원하지 않습니다 (3.13 이상)")
        if item not in targets:
            targets.append(item)
    if not targets:
        raise core.ConfigError("--targets: 대상 버전이 없습니다")
    return targets


def cp_tag(target: str) -> str:
    """"3.14" → "cp314"."""
    return "cp" + target.replace(".", "")


def download_command(python: str, target: str, requirements: Path, dest: Path) -> list[str]:
    return [
        python, "-m", "pip", "download",
        "--only-binary=:all:",
        "--platform", PLATFORM,
        "--implementation", "cp",
        "--python-version", target,
        "--abi", cp_tag(target),
        "-r", str(requirements),
        "-d", str(dest),
        "--disable-pip-version-check",
    ]  # fmt: skip


def sdist_command(python: str, spec: str, dest: Path) -> list[str]:
    return [
        python, "-m", "pip", "download",
        "--no-binary", SDIST_PACKAGE,
        "--no-deps",
        spec,
        "-d", str(dest),
        "--disable-pip-version-check",
    ]  # fmt: skip


# ---------------------------------------------------------------- wheel 파일명


@dataclass(frozen=True)
class Wheel:
    filename: str
    name: str  # 정규화된 배포 이름
    version: str
    pythons: tuple[str, ...]
    abis: tuple[str, ...]
    platforms: tuple[str, ...]


def parse_wheel(filename: str) -> Wheel | None:
    """`name-ver(-build)?-py-abi-plat.whl`을 나눈다. wheel이 아니면 None."""
    if not filename.endswith(".whl"):
        return None
    parts = filename[:-4].split("-")
    if len(parts) not in (5, 6):
        return None
    py, abi, plat = parts[-3:]
    return Wheel(filename, normalize(parts[0]), parts[1], tuple(py.split(".")), tuple(abi.split(".")), tuple(plat.split(".")))


def compatible(wheel: Wheel, target: str) -> bool:
    """Windows x64 CPython `target`에서 pip가 이 wheel을 고를 수 있는가(pip 태그 규칙의 필요한 부분만)."""
    if not any(p in (PLATFORM, "any") for p in wheel.platforms):
        return False
    minor = int(target.split(".")[1])
    cp = cp_tag(target)
    for abi in wheel.abis:
        for py in wheel.pythons:
            if abi == cp and py == cp:
                return True
            if abi == "abi3" and py.startswith("cp3") and py[3:].isdigit() and int(py[3:]) <= minor:
                return True
            if abi == "none":
                if py in ("py3", cp):
                    return True
                if py.startswith("py3") and py[3:].isdigit() and int(py[3:]) <= minor:
                    return True
    return False


def requirement_names(path: Path) -> list[str]:
    """requirements.txt의 패키지 이름(정규화). 주석·옵션 줄(-r 등)은 건너뛴다."""
    names: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = _NAME.match(line)
        if match:
            name = normalize(match.group(0))
            if name not in names:
                names.append(name)
    return names


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sdist_version(filename: str) -> str | None:
    match = re.match(r"^pyyaml-(.+)\.(?:tar\.gz|zip)$", filename, re.IGNORECASE)
    return match.group(1) if match else None


def build_manifest(dest: Path, targets: list[str], required: list[str], *, now: str) -> tuple[str, dict[str, list[str]]]:
    """MANIFEST.txt 본문과 대상별 빠진 패키지를 돌려준다."""
    wheels = sorted((w for w in (parse_wheel(p.name) for p in dest.glob("*.whl")) if w), key=lambda w: (w.name, w.filename))
    sdist_dir = dest / SDIST_DIR
    sdists = sorted(p for p in sdist_dir.glob("*") if p.is_file()) if sdist_dir.is_dir() else []
    tags = ", ".join(cp_tag(t) for t in targets)
    lines = [
        "# work-kb 오프라인 설치 묶음 (kb.py wheels가 만듦. 손으로 고치지 않는다)",
        f"# 만든 때: {now}",
        f"# 플랫폼: {PLATFORM}, 대상: {tags}",
        f"# 설치: .venv\\Scripts\\python.exe -m pip install --no-index --find-links {dest.name} -r requirements.txt",
        "",
    ]
    missing: dict[str, list[str]] = {}
    for target in targets:
        chosen = [w for w in wheels if compatible(w, target)]
        have = {w.name for w in chosen}
        gone = [name for name in required if name not in have]
        if gone:
            missing[target] = gone
        lines.append(f"[{cp_tag(target)}] wheel {len(chosen)}개")
        lines.extend(f"{w.name} {w.version}  {w.filename}" for w in chosen)
        if gone:
            lines.append(f"누락: {', '.join(gone)}")
        lines.append("")
    lines.append(f"[sdist] Python 3.15용 {SDIST_PACKAGE} 원본 (빌드: docs/PACKAGING.md)")
    for path in sdists:
        lines.append(f"{SDIST_PACKAGE} {_sdist_version(path.name) or '?'}  {SDIST_DIR}/{path.name}")
    if not sdists:
        lines.append("(없음)")
    lines.append("")
    lines.append("[files] sha256  바이트  경로")
    files = sorted(p for p in dest.glob("*.whl")) + sdists
    for path in files:
        rel = path.relative_to(dest).as_posix()
        lines.append(f"{_sha256(path)}  {path.stat().st_size}  {rel}")
    return "\n".join(lines) + "\n", missing


# ---------------------------------------------------------------- 실행


def _resolve_dir(text: str) -> Path:
    path = Path(text)
    return path if path.is_absolute() else CODE_ROOT / path


def _show(command: list[str]) -> str:
    return subprocess.list2cmdline(command)


def _run(command: list[str]) -> int:
    print(f"> {_show(command)}", flush=True)
    return subprocess.run(command, cwd=str(CODE_ROOT)).returncode


def main(argv: list[str]) -> int:
    parser = core._Parser(
        prog="kb.py wheels",
        description="오프라인 설치용 wheel 묶음을 내려받는다 (이 venv의 pip 사용, 네트워크 필요)",
        allow_abbrev=False,
    )
    parser.add_argument("--targets", default=",".join(DEFAULT_TARGETS), help="대상 Python 버전, 쉼표로 구분 (기본: 3.13,3.14)")
    parser.add_argument("--dir", default=DEFAULT_DIR, help="받을 폴더. 상대 경로는 저장소 폴더 기준 (기본: wheels)")
    parser.add_argument("--no-sdist", action="store_true", help="Python 3.15용 PyYAML 원본(sdist)을 받지 않는다")
    parser.add_argument("--dry-run", action="store_true", help="실행할 pip 명령만 보여 주고 아무것도 받지 않는다")
    # 모든 명령이 --root를 받는다(README). wheels는 저장소 폴더만 쓰므로 받기만 하고 무시한다.
    parser.add_argument("--root", default=None, help="받기만 하고 무시한다 (wheels는 저장소 폴더만 쓴다)")
    args = parser.parse_args(argv)

    targets = parse_targets(args.targets)
    dest = _resolve_dir(args.dir)
    if not REQUIREMENTS.is_file():
        raise core.ConfigError(f"{REQUIREMENTS}: requirements.txt가 없습니다")
    required = requirement_names(REQUIREMENTS)
    python = sys.executable

    commands = [download_command(python, t, REQUIREMENTS, dest) for t in targets]
    if args.dry_run:
        for command in commands:
            print(_show(command))
        if not args.no_sdist:
            print(_show(sdist_command(python, SDIST_PACKAGE, dest / SDIST_DIR)))
        print(f"(--dry-run: 내려받지 않았습니다. 대상 {', '.join(cp_tag(t) for t in targets)}, 폴더 {dest})")
        return 0

    if importlib.util.find_spec("pip") is None:
        raise core.ConfigError(f"{python}: pip가 없습니다. `{python} -m ensurepip` 후 다시 실행하세요")
    dest.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    for target, command in zip(targets, commands):
        code = _run(command)
        if code != 0:
            failures.append(f"{cp_tag(target)} wheel 내려받기 실패 (pip 종료 {code})")

    if not args.no_sdist:
        versions = sorted(
            {w.version for w in (parse_wheel(p.name) for p in dest.glob("*.whl")) if w and w.name == SDIST_PACKAGE},
            key=lambda v: [(0, int(x), "") if x.isdigit() else (1, 0, x) for x in v.split(".")],
        )
        spec = f"{SDIST_PACKAGE}=={versions[-1]}" if versions else SDIST_PACKAGE  # cp313·cp314 wheel과 같은 판
        code = _run(sdist_command(python, spec, dest / SDIST_DIR))
        if code != 0:
            failures.append(f"{SDIST_PACKAGE} 원본(sdist) 내려받기 실패 (pip 종료 {code})")

    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    text, missing = build_manifest(dest, targets, required, now=now)
    core.atomic_write(dest / MANIFEST, text.encode("utf-8"))
    print()
    print(text, end="")
    print(f"\n목록: {dest / MANIFEST}")
    for target, names in missing.items():
        failures.append(f"{cp_tag(target)}에 없는 패키지: {', '.join(names)}")
    if failures:
        raise core.KbError("; ".join(failures))
    return 0
