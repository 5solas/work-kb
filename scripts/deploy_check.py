"""회사 이식용 복사본을 만들고 점검한다 (docs/DEPLOY.md 1단계, 지시서 §9.1, D60, D34).

사용법: python scripts/deploy_check.py <대상 폴더> [--source 저장소]

- 저장소를 <대상 폴더>로 복사한다. 대상은 없거나 빈 폴더여야 하고, 저장소 안이면 거부한다(종료 4).
- 복사 제외(§9.1 + D60):
  - 어디서든: `.git`, `.venv*`, `__pycache__`, `.pytest_cache`, `.obsidian`, 임시·백업 파일(`.*.tmp`, `*~`, `#*#`, `.#*`)
  - 최상위: `config.local.yaml`, `.env`, `.state/`, `_trash/`, `examples/`, `tests/`
  - 볼트 내용: `daily/`·`inbox/`·`raw/` 안의 파일 전부, `jira/*.md`, `jira/_views/*.md`, `jira/outbox.jsonl`,
    `jira/.cache.sqlite*`, `wiki/` 아래 `*.md`(생성 페이지). `.gitkeep`과 Bases 파일(`*.base`)은 남긴다.
  - `wheels/`는 복사한다(오프라인 설치용).
- 복사한 뒤 파일 수·크기를 보여 주고, 제외 대상이 하나도 없는지와 필수 파일이 있는지를 따로 다시 확인한다.
  문제가 있으면 종료 1.

`tests/`를 빼므로 복사본에서는 `kb.py demo`가 돌지 않는다(종료 4, 집 전용).
표준 라이브러리만 쓴다(pip 설치 전에도 실행된다).
"""
from __future__ import annotations

import fnmatch
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from lib import core  # noqa: E402  (표준 라이브러리만 쓰는 모듈)

CODE_ROOT = HERE.parent

ANYWHERE_DIRS = (".git", "__pycache__", ".pytest_cache", ".obsidian")
ANYWHERE_DIR_PREFIXES = (".venv",)
ANYWHERE_FILES = (".*.tmp", "*~", "#*#", ".#*")
TOP_LEVEL = ("config.local.yaml", ".env", ".state", "_trash", "examples", "tests")
CONTENT_DIRS = ("daily", "inbox", "raw")
KEEP_NAMES = (".gitkeep",)
JIRA_FILES = ("jira/*.md", "jira/_views/*.md", "jira/outbox.jsonl", "jira/.cache.sqlite*")

# 복사 뒤 확인용 목록. 위 규칙과 따로 적어 두 번째 확인이 같은 실수를 되풀이하지 않게 한다.
FORBIDDEN_GLOBS = (
    ".git", ".venv*", ".state", "_trash", "examples", "tests", "config.local.yaml", ".env",
    "**/__pycache__", "**/.pytest_cache", "**/.obsidian", "**/.*.tmp",
    "jira/.cache.sqlite*", "jira/outbox.jsonl", "jira/*.md", "jira/_views/*.md",
    "wiki/**/*.md", "daily/**/*", "inbox/**/*", "raw/**/*",
)  # fmt: skip
REQUIRED = (
    "scripts/kb.py", "scripts/lib/core.py", "config.yaml", "config.local.example.yaml",
    "requirements.txt", "AGENTS.md", "install.ps1", "obsidian/setup.md", "prompts",
)  # fmt: skip


def excluded(rel: str, is_dir: bool) -> bool:
    """저장소 기준 POSIX 상대 경로 `rel`을 복사에서 뺄지."""
    parts = rel.split("/")
    name = parts[-1]
    if any(p in ANYWHERE_DIRS or p.startswith(ANYWHERE_DIR_PREFIXES) for p in parts):
        return True  # `.git`이 파일(작업 트리 연결)이어도 뺀다
    if parts[0] in TOP_LEVEL:
        return True
    if is_dir:
        return False
    if any(fnmatch.fnmatchcase(name, pattern) for pattern in ANYWHERE_FILES):
        return True
    if name in KEEP_NAMES:
        return False
    if parts[0] in CONTENT_DIRS:
        return True
    if any(fnmatch.fnmatchcase(rel, pattern) and rel.count("/") == pattern.count("/") for pattern in JIRA_FILES):
        return True
    if parts[0] == "wiki" and name.lower().endswith(".md"):
        return True
    return False


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _check_destination(source: Path, dest: Path) -> None:
    if not (source / "scripts" / "kb.py").is_file():
        raise core.ConfigError(f"{source}: work-kb 저장소가 아닙니다 (scripts/kb.py 없음)")
    if _inside(dest, source) or _inside(source, dest):
        raise core.ConfigError(f"{dest}: 대상 폴더는 저장소 밖이어야 합니다 ({source})")
    if dest.exists():
        if not dest.is_dir():
            raise core.ConfigError(f"{dest}: 폴더가 아닙니다")
        if any(dest.iterdir()):
            raise core.ConfigError(f"{dest}: 비어 있지 않습니다. 새 폴더나 빈 폴더를 지정하세요")


def copy_tree(source: Path, dest: Path) -> tuple[int, int, list[str]]:
    """제외 규칙대로 복사한다. (파일 수, 바이트, 경고) 반환."""
    files = 0
    total = 0
    warnings: list[str] = []
    dest.mkdir(parents=True, exist_ok=True)
    for current, dirnames, filenames in os.walk(source):
        here = Path(current)
        rel_dir = here.relative_to(source).as_posix()
        prefix = "" if rel_dir == "." else rel_dir + "/"
        dirnames[:] = sorted(d for d in dirnames if not excluded(prefix + d, True))
        for d in dirnames:
            (dest / prefix / d).mkdir(parents=True, exist_ok=True)
        for f in sorted(filenames):
            rel = prefix + f
            if excluded(rel, False):
                continue
            target = dest / rel
            try:
                shutil.copy2(here / f, target)
            except FileNotFoundError:
                warnings.append(f"{rel}: 복사하는 사이에 사라져 건너뜀")
                continue
            files += 1
            total += target.stat().st_size
    return files, total, warnings


def find_forbidden(dest: Path) -> list[str]:
    """복사본에 있으면 안 되는 경로(FORBIDDEN_GLOBS)를 찾는다. `.gitkeep`·폴더 자체는 내용 규칙에서 뺀다."""
    found: set[str] = set()
    for pattern in FORBIDDEN_GLOBS:
        content_rule = pattern.endswith("/**/*")
        for path in dest.glob(pattern):
            if content_rule and (path.is_dir() or path.name in KEEP_NAMES):
                continue
            found.add(path.relative_to(dest).as_posix())
    return sorted(found)


def find_missing(dest: Path) -> list[str]:
    return [rel for rel in REQUIRED if not (dest / rel).exists()]


def _size(n: int) -> str:
    return f"{n / (1 << 20):.1f} MB" if n >= 1 << 20 else f"{n / 1024:.1f} KB"


def _top_level_sizes(dest: Path) -> list[tuple[str, int, int]]:
    rows: dict[str, list[int]] = {}
    for path in dest.rglob("*"):
        if path.is_file():
            top = path.relative_to(dest).parts[0]
            key = top + ("/" if (dest / top).is_dir() else "")
            row = rows.setdefault(key, [0, 0])
            row[0] += 1
            row[1] += path.stat().st_size
    return sorted(((k, v[0], v[1]) for k, v in rows.items()), key=lambda r: (-r[2], r[0]))


def main(argv: list[str]) -> int:
    parser = core._Parser(
        prog="deploy_check.py",
        description="회사 이식용 복사본을 만들고 제외 대상이 없는지 확인한다 (DEPLOY 1단계)",
        allow_abbrev=False,
    )
    parser.add_argument("dest", help="복사할 대상 폴더 (없거나 빈 폴더, 저장소 밖)")
    parser.add_argument("--source", default=None, help="복사할 저장소 (기본: 이 스크립트의 저장소)")
    args = parser.parse_args(argv)

    source = Path(args.source).resolve() if args.source else CODE_ROOT
    dest = Path(args.dest).resolve()
    _check_destination(source, dest)

    files, total, warnings = copy_tree(source, dest)
    print(f"복사: {source} → {dest}")
    print(f"파일 {files}개, {_size(total)}")
    for name, count, size in _top_level_sizes(dest):
        print(f"  {name:<28} {count:>5}개  {_size(size):>10}")
    for warning in warnings:
        print(f"경고: {warning}")

    forbidden = find_forbidden(dest)
    missing = find_missing(dest)
    for rel in forbidden:
        print(f"✗ 제외 대상이 복사됨: {rel}")
    for rel in missing:
        print(f"✗ 필수 파일 없음: {rel}")
    if forbidden or missing:
        raise core.KbError(f"복사본 점검 실패: 제외 대상 {len(forbidden)}개, 필수 파일 누락 {len(missing)}개")
    print("✓ 제외 대상 없음, 필수 파일 있음")
    if not (dest / "wheels").is_dir():
        print("- wheels/ 없음: 회사에서 pip로 내려받을 수 없으면 집에서 `kb.py wheels`를 먼저 실행하세요")
    return 0


if __name__ == "__main__":
    core.run_main(main)
