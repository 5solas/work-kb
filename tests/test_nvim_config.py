"""Neovim 설정(nvim/) 스펙(tests/nvim/config_spec/*_spec.lua)을 전체 설정으로 두 콘솔 모드에서 돌린다.

- 공용 러너 tests/nvim/run.lua를 쓰고 WORK_KB_TEST_SPEC_DIR=tests/nvim/config_spec로 폴더를 바꾼다.
- 설정: XDG_CONFIG_HOME=<저장소>(정션 없이 <저장소>/nvim이 설정), `-u nvim/init.lua`(`-l`은 init을
  읽지 않으므로 명시). 플러그인은 실제 데이터 폴더(%LOCALAPPDATA%\\nvim-data\\lazy)의 것.
  XDG_STATE_HOME은 tmp (shada·로그를 실제 상태 폴더에 쓰지 않는다).
- 콘솔 상속과 CREATE_NO_WINDOW(cp949) 두 번 (locale_spec의 한글 경로 왕복이 두 모드에서 돌게).
- 실패 0, 건너뜀 0, `NOT RUN` 0, 최소 개수 이상. KB_SKIP_NVIM=1일 때만 건너뛴다.
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
INIT = ROOT / "nvim" / "init.lua"
SPEC_DIR = "tests/nvim/config_spec"
SKIP_NVIM = os.environ.get("KB_SKIP_NVIM") == "1"
MIN_TESTS = 40

pytestmark = pytest.mark.skipif(SKIP_NVIM, reason="KB_SKIP_NVIM=1")

MODES = {
    "inherited-console": 0,
    "create-no-window": getattr(subprocess, "CREATE_NO_WINDOW", 0),
}
LOCALE_RE = re.compile(r"WORK-KB-TEST-LOCALE: (\S+)")
SUMMARY_RE = re.compile(r"^Ran (\d+) tests, (\d+) passed, (\d+) failed, (\d+) skipped$", re.MULTILINE)


def _nvim() -> str:
    found = shutil.which("nvim")
    if found is None:
        pytest.fail("nvim을 PATH에서 찾지 못했습니다 (건너뛰려면 KB_SKIP_NVIM=1)")
    return found


def _child_env(tmp_path: Path) -> dict[str, str]:
    drop = {"PYTHONPATH", "PYTHONHOME", "MYVIMRC", "WORK_KB_TEST_FILTER"}
    env = {
        k: v for k, v in os.environ.items()
        if not k.upper().startswith(("KB_", "NVIM", "VIM", "XDG_")) and k not in drop
    }
    env["XDG_CONFIG_HOME"] = str(ROOT)
    env["XDG_STATE_HOME"] = str(tmp_path / "state")
    env["WORK_KB_TEST_TMP"] = str(tmp_path)
    env["WORK_KB_TEST_PYTHON"] = sys.executable
    env["WORK_KB_TEST_SPEC_DIR"] = SPEC_DIR
    return env


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 설정")
@pytest.mark.parametrize("mode", list(MODES))
def test_nvim_config_suite(mode, tmp_path):
    assert RUNNER.is_file() and INIT.is_file()
    assert str(tmp_path).isascii(), f"임시 경로가 ASCII가 아님: {tmp_path}"
    command = [_nvim(), "--headless", "-u", "nvim/init.lua", "-l", "tests/nvim/run.lua"]
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
    assert "NOT RUN" not in output, tail
    found = LOCALE_RE.search(output)
    assert found, tail
    if mode == "create-no-window":
        assert found.group(1) == "cp949", tail
    print(f"{mode}: console code page={found.group(1)}; {summary.group(0)}")
