"""정적 규칙: 텍스트 파일 입출력과 텍스트 모드 하위 프로세스는 반드시 encoding= 을 지정한다.

Windows 기본 인코딩(cp949)에 기대는 코드를 AST로 잡아낸다. 지시서 §2·§10, D05.

잡는 것:
- 내장 open(모드는 두 번째 인수), `<식>.open`(Path.open: 모드는 첫 번째 인수), os.open은 예외
- `.read_text()`·`.write_text()`
- io.TextIOWrapper, os.fdopen, os.popen, tempfile.NamedTemporaryFile·TemporaryFile·SpooledTemporaryFile(텍스트 모드)
- logging.FileHandler·WatchedFileHandler·RotatingFileHandler·TimedRotatingFileHandler, basicConfig(filename=…)
- subprocess.run·Popen·check_output·check_call·call·getoutput·getstatusoutput
  (`from subprocess import run`처럼 이름으로 가져온 것 포함). text=·universal_newlines=가 참이거나
  정적으로 알 수 없을 때(변수, **kwargs) encoding= 이 있어야 한다.
- `encoding=None`은 없는 것과 같다.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = [ROOT / "scripts", ROOT / "tests"]
SUBPROCESS_FUNCS = {"run", "Popen", "check_output", "check_call", "call", "getoutput", "getstatusoutput"}
ALWAYS_TEXT_SUBPROCESS = {"getoutput", "getstatusoutput"}
BUILTIN_OPEN_MODULES = {"io", "codecs", "builtins"}  # 이 모듈의 open은 내장 open과 같은 인수 순서

# 이름 → (모드 위치 또는 None(항상 텍스트), 기본 모드가 이진인가, encoding 위치 또는 None)
FILE_LIKE: dict[str, tuple[int | None, bool, int | None]] = {
    "TextIOWrapper": (None, False, 1),
    "fdopen": (1, False, 3),
    "popen": (None, False, None),
    "NamedTemporaryFile": (0, True, 2),
    "TemporaryFile": (0, True, 2),
    "SpooledTemporaryFile": (1, True, 3),
    "FileHandler": (None, False, 2),
    "WatchedFileHandler": (None, False, 2),
    "RotatingFileHandler": (None, False, 4),
    "TimedRotatingFileHandler": (None, False, 4),
}


def _arg(call: ast.Call, index: int | None, keyword: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    if index is not None and len(call.args) > index and not any(isinstance(a, ast.Starred) for a in call.args[: index + 1]):
        return call.args[index]
    return None


def _has_encoding(call: ast.Call, index: int | None = None) -> bool:
    value = _arg(call, index, "encoding")
    return value is not None and not (isinstance(value, ast.Constant) and value.value is None)


def _mode_is_binary(call: ast.Call, index: int | None, default_binary: bool = False) -> bool | None:
    mode = _arg(call, index, "mode")
    if mode is None:
        return default_binary
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return "b" in mode.value
    return None  # 모드를 정적으로 알 수 없음 → encoding 요구


def _text_flag(call: ast.Call) -> bool | None:
    """text=/universal_newlines= 가 참이면 True, 없거나 거짓 상수면 False, 알 수 없으면 None."""
    unknown = any(kw.arg is None for kw in call.keywords)  # **kwargs
    for kw in call.keywords:
        if kw.arg in ("text", "universal_newlines"):
            if not isinstance(kw.value, ast.Constant):
                unknown = True
            elif kw.value.value:
                return True
    return None if unknown else False


def _subprocess_names(tree: ast.AST) -> tuple[set[str], dict[str, str]]:
    """(subprocess 모듈 별칭, 이름으로 가져온 함수 별칭 → 원래 이름)."""
    modules: set[str] = {"subprocess"}
    funcs: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                if alias.name in SUBPROCESS_FUNCS:
                    funcs[alias.asname or alias.name] = alias.name
    return modules, funcs


def _receiver(func: ast.expr) -> str | None:
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id
    return None


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    sub_modules, sub_funcs = _subprocess_names(tree)
    found: list[str] = []
    rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        if name is None:
            continue
        receiver = _receiver(func)
        where = f"{rel}:{node.lineno}"

        if name == "open":
            if receiver == "os":
                continue  # os.open은 파일 서술자 전용, 인코딩 없음
            if isinstance(func, ast.Name) or receiver in BUILTIN_OPEN_MODULES:
                mode_at, encoding_at = 1, 3  # open(file, mode, buffering, encoding)
            else:
                mode_at, encoding_at = 0, 2  # Path.open(mode, buffering, encoding)
            if _mode_is_binary(node, mode_at) is not True and not _has_encoding(node, encoding_at):
                found.append(f"{where}: open()에 encoding= 없음")
        elif name in ("read_text", "write_text") and isinstance(func, ast.Attribute):
            if not _has_encoding(node, 0 if name == "read_text" else 1):
                found.append(f"{where}: .{name}()에 encoding= 없음")
        elif name in FILE_LIKE:
            mode_at, default_binary, encoding_at = FILE_LIKE[name]
            binary = _mode_is_binary(node, mode_at, default_binary) if mode_at is not None else False
            if binary is not True and not _has_encoding(node, encoding_at):
                found.append(f"{where}: {name}()에 encoding= 없음")
        elif name == "basicConfig":
            if _arg(node, None, "filename") is not None and not _has_encoding(node):
                found.append(f"{where}: basicConfig(filename=)에 encoding= 없음")
        elif (isinstance(func, ast.Name) and name in sub_funcs) or (receiver in sub_modules and name in SUBPROCESS_FUNCS):
            real = sub_funcs.get(name, name) if isinstance(func, ast.Name) else name
            text = True if real in ALWAYS_TEXT_SUBPROCESS else _text_flag(node)
            if text is not False and not _has_encoding(node):
                found.append(f"{where}: subprocess.{real}() 텍스트 모드에 encoding= 없음")
        elif name in SUBPROCESS_FUNCS and isinstance(func, ast.Attribute):
            # subprocess 별칭이 아닌 `.run(...)` 등: 텍스트 모드가 상수로 명백할 때만
            if _text_flag(node) is True and not _has_encoding(node):
                found.append(f"{where}: .{name}(text=True)에 encoding= 없음")
    return found


def _py_files() -> list[Path]:
    files: list[Path] = []
    for base in SCAN_DIRS:
        if base.exists():
            files.extend(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)
    return sorted(files)


@pytest.mark.parametrize("path", _py_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_text_io_specifies_encoding(path):
    assert _violations(path) == []


BAD = [
    "open('a')",
    "open('a', 'w')",
    "open('a', mode)",
    "open('a', encoding=None)",
    "io.open('a', 'w')",
    "Path('a').read_text()",
    "Path('a').read_text(encoding=None)",
    "Path('a').write_text('x')",
    "Path('a').open()",
    "Path('a').open('w')",
    "Path('a').open('r', -1, None)",
    "subprocess.run(['x'], text=True)",
    "subprocess.run(['x'], universal_newlines=True)",
    "subprocess.run(['x'], text=flag)",
    "subprocess.run(['x'], **kwargs)",
    "subprocess.run(['x'], text=True, encoding=None)",
    "sp.check_output(['x'], text=True)",
    "subprocess.getoutput('x')",
    "run(['x'], text=True)",
    "co(['x'], universal_newlines=True)",
    "Popen(['x'], **kw)",
    "io.TextIOWrapper(buf)",
    "TextIOWrapper(buf, encoding=None)",
    "os.fdopen(fd, 'w')",
    "os.fdopen(fd)",
    "os.popen('dir')",
    "tempfile.NamedTemporaryFile('w')",
    "tempfile.NamedTemporaryFile(mode='w+')",
    "tempfile.TemporaryFile('r+')",
    "tempfile.SpooledTemporaryFile(10, 'w+')",
    "logging.FileHandler('a.log')",
    "logging.handlers.RotatingFileHandler('a.log', maxBytes=1)",
    "logging.handlers.TimedRotatingFileHandler('a.log')",
    "logging.handlers.WatchedFileHandler('a.log')",
    "logging.basicConfig(filename='a.log')",
]

GOOD = [
    "open('a', 'rb')",
    "open('a', encoding='utf-8')",
    "open('a', 'w', -1, 'utf-8')",
    "os.open('a', os.O_RDONLY)",
    "io.open('a', 'rb')",
    "Path('a').open('rb')",
    "Path('a').open(encoding='utf-8')",
    "Path('a').open('r', -1, 'utf-8')",
    "Path('a').read_bytes()",
    "Path('a').read_text('utf-8')",
    "Path('a').write_text('x', 'utf-8')",
    "subprocess.run(['x'])",
    "subprocess.run(['x'], capture_output=True)",
    "subprocess.run(['x'], text=False)",
    "subprocess.run(['x'], text=True, encoding='utf-8')",
    "subprocess.run(['x'], **kwargs, encoding='utf-8')",
    "subprocess.getoutput('x', encoding='utf-8')",
    "run(['x'])",
    "other.run(['x'], **kwargs)",
    "asyncio.run(main())",
    "io.TextIOWrapper(buf, encoding='utf-8')",
    "io.TextIOWrapper(buf, 'utf-8')",
    "os.fdopen(fd, 'wb')",
    "os.fdopen(fd, 'w', encoding='utf-8')",
    "tempfile.NamedTemporaryFile()",
    "tempfile.NamedTemporaryFile('wb')",
    "tempfile.NamedTemporaryFile('w', encoding='utf-8')",
    "tempfile.TemporaryFile()",
    "tempfile.SpooledTemporaryFile(10)",
    "logging.FileHandler('a.log', encoding='utf-8')",
    "logging.handlers.RotatingFileHandler('a.log', 'a', 1, 3, 'utf-8')",
    "logging.basicConfig(level=10)",
    "logging.basicConfig(filename='a.log', encoding='utf-8')",
]

HEADER = (
    "import io, os, subprocess, tempfile, logging, logging.handlers, asyncio\n"
    "import subprocess as sp\n"
    "from io import TextIOWrapper\n"
    "from pathlib import Path\n"
    "from subprocess import run, Popen, check_output as co\n"
)


def test_rule_catches_known_bad_code(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text(HEADER + "\n".join(BAD) + "\n", encoding="utf-8")
    first = HEADER.count("\n") + 1
    found = _violations(bad)
    lines = sorted(int(re.search(r"bad\.py:(\d+): ", item).group(1)) for item in found)
    missed = [BAD[i] for i in range(len(BAD)) if first + i not in lines]
    assert missed == [], found
    assert lines == list(range(first, first + len(BAD))), found


def test_rule_accepts_known_good_code(tmp_path):
    good = tmp_path / "good.py"
    good.write_text(HEADER + "\n".join(GOOD) + "\n", encoding="utf-8")
    assert _violations(good) == []
