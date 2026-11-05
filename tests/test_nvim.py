"""Neovim 헤드리스 테스트(tests/nvim/spec/*_spec.lua)를 두 콘솔 모드로 돌린다 (test_emacs_ert.py와 같은 꼴).

- 콘솔 상속(이 프로세스의 콘솔)과 CREATE_NO_WINDOW(콘솔 없음 → cp949) 두 번.
- 러너 tests/nvim/run.lua가 `WORK-KB-TEST-LOCALE: cpNNN`(활성 콘솔 코드 페이지)과
  마지막 줄 `Ran N tests, P passed, F failed, S skipped`를 낸다.
- 상속 모드는 이 프로세스의 콘솔 코드 페이지와 같아야 하고(콘솔이 없으면 단정하지 않음),
  CREATE_NO_WINDOW는 cp949여야 한다. 두 모드의 로캘이 같으면(이 콘솔이 cp949가 아닌데) 실패.
- 실패 0, 건너뜀 0, 최소 개수 이상. scripts/serve.py가 있으면 `NOT RUN` 줄(실제 서버 시험)이 없어야 한다.
- stdin은 DEVNULL, 출력은 UTF-8로 해독, 제한 300초. 볼트는 WORK_KB_TEST_TMP(ASCII tmp) 아래 한글 경로.
- KB_SKIP_NVIM=1일 때만 건너뛴다. 그 밖에 nvim이 없으면 실패다.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tests" / "nvim" / "run.lua"
INIT = ROOT / "tests" / "nvim" / "minimal_init.lua"
# conftest의 autouse 픽스처가 KB_* 환경변수를 지우므로 가져올 때 읽어 둔다.
SKIP_NVIM = os.environ.get("KB_SKIP_NVIM") == "1"
MIN_TESTS = 50  # 러너가 실제로 돌았는지 확인 (0개 통과를 성공으로 보지 않음)

pytestmark = pytest.mark.skipif(SKIP_NVIM, reason="KB_SKIP_NVIM=1")

MODES = {
    "inherited-console": 0,
    "create-no-window": getattr(subprocess, "CREATE_NO_WINDOW", 0),
}
NO_WINDOW_LOCALE = "cp949"
LOCALE_RE = re.compile(r"WORK-KB-TEST-LOCALE: (\S+)")
SUMMARY_RE = re.compile(r"^Ran (\d+) tests, (\d+) passed, (\d+) failed, (\d+) skipped$", re.MULTILINE)
_seen_locales: dict[str, str] = {}


def _console_cp() -> int:
    """이 프로세스가 물려줄 콘솔의 입력 코드 페이지 (콘솔이 없으면 0)."""
    try:
        import ctypes

        return int(ctypes.windll.kernel32.GetConsoleCP())
    except (AttributeError, OSError):
        return 0


def _expected_locale(mode: str) -> str | None:
    if mode == "create-no-window":
        return NO_WINDOW_LOCALE
    cp = _console_cp()
    return f"cp{cp}" if cp else None


def _nvim() -> str:
    found = shutil.which("nvim")
    if found is None:
        pytest.fail("nvim을 PATH에서 찾지 못했습니다 (건너뛰려면 KB_SKIP_NVIM=1)")
    return found


def _child_env(tmp_path: Path) -> dict[str, str]:
    drop = {"PYTHONPATH", "PYTHONHOME", "VIMINIT", "MYVIMRC", "NVIM", "NVIM_APPNAME", "VIMRUNTIME", "WORK_KB_TEST_FILTER"}
    env = {k: v for k, v in os.environ.items() if not k.startswith("KB_") and k not in drop}
    env["WORK_KB_TEST_TMP"] = str(tmp_path)
    env["WORK_KB_TEST_PYTHON"] = sys.executable
    return env


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 콘솔 로캘 검사")
@pytest.mark.parametrize("mode", list(MODES))
def test_nvim_suite(mode, tmp_path):
    assert RUNNER.is_file() and INIT.is_file()
    assert str(tmp_path).isascii(), f"임시 경로가 ASCII가 아님: {tmp_path}"
    command = [
        _nvim(),
        "--headless",
        "-u",
        str(INIT.relative_to(ROOT)).replace("\\", "/"),
        "-l",
        str(RUNNER.relative_to(ROOT)).replace("\\", "/"),
    ]
    result = subprocess.run(
        command,
        cwd=str(ROOT),
        env=_child_env(tmp_path),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        creationflags=MODES[mode],
    )
    output = result.stdout + result.stderr
    tail = output[-8000:]
    assert result.returncode == 0, tail
    summary = SUMMARY_RE.search(output)
    assert summary, tail
    ran, passed, failed, skipped = (int(x) for x in summary.groups())
    assert failed == 0, tail
    assert skipped == 0, tail
    assert ran == passed >= MIN_TESTS, tail
    if (ROOT / "scripts" / "serve.py").exists():
        assert "NOT RUN" not in output, tail
    found = LOCALE_RE.search(output)
    assert found, tail
    locale = found.group(1)
    print(f"{mode}: console code page={locale} (pytest 콘솔 {_console_cp()}); {summary.group(0)}")
    for line in output.splitlines():
        if line.startswith("NOT RUN"):
            print(line)
    expected = _expected_locale(mode)
    if expected:
        assert locale == expected, tail
    _seen_locales[mode] = locale
    if len(_seen_locales) == len(MODES) and _console_cp() not in (0, 949):
        assert _seen_locales["inherited-console"] != _seen_locales["create-no-window"], _seen_locales
