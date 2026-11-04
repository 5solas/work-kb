"""정적 규칙: 텍스트 파일 입출력과 텍스트 모드 하위 프로세스는 반드시 encoding= 을 지정한다.

Windows 기본 인코딩(cp949)에 기대는 코드를 AST로 잡아낸다. 지시서 §2·§10.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = [ROOT / "scripts", ROOT / "tests"]
TEXT_METHODS = {"read_text", "write_text"}
SUBPROCESS_FUNCS = {"run", "Popen", "check_output", "check_call", "call"}


def _mode_is_binary(call: ast.Call) -> bool | None:
    mode = None
    if len(call.args) >= 2:
        mode = call.args[1]
    for kw in call.keywords:
        if kw.arg == "mode":
            mode = kw.value
    if mode is None:
        return False  # 기본 모드 'r' = 텍스트
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return "b" in mode.value
    return None  # 모드를 정적으로 알 수 없음 → encoding 요구


def _has_kw(call: ast.Call, name: str) -> bool:
    return any(kw.arg == name for kw in call.keywords)


def _kw_true(call: ast.Call, name: str) -> bool:
    for kw in call.keywords:
        if kw.arg == name and isinstance(kw.value, ast.Constant) and kw.value.value is True:
            return True
    return False


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        if name is None:
            continue
        rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        where = f"{rel}:{node.lineno}"
        if name == "open":
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "os":
                continue  # os.open은 파일 서술자 전용, 인코딩 없음
            binary = _mode_is_binary(node)
            if binary is not True and not _has_kw(node, "encoding"):
                found.append(f"{where}: open()에 encoding= 없음")
        elif name in TEXT_METHODS and isinstance(func, ast.Attribute):
            if not _has_kw(node, "encoding"):
                found.append(f"{where}: .{name}()에 encoding= 없음")
        elif name in SUBPROCESS_FUNCS and isinstance(func, ast.Attribute):
            if (_kw_true(node, "text") or _kw_true(node, "universal_newlines")) and not _has_kw(node, "encoding"):
                found.append(f"{where}: subprocess.{name}(text=True)에 encoding= 없음")
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


def test_rule_catches_known_bad_code(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text(
        "import subprocess\nfrom pathlib import Path\n"
        "open('a')\nopen('a', 'w')\nPath('a').read_text()\nPath('a').write_text('x')\n"
        "subprocess.run(['x'], text=True)\n"
        "open('a', 'rb')\nopen('a', encoding='utf-8')\nPath('a').read_bytes()\n",
        encoding="utf-8",
    )
    found = _violations(bad)
    assert len(found) == 5, found
