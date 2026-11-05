"""Neovim 헤드리스 테스트(tests/nvim/spec/*_spec.lua)를 두 콘솔 모드로 돌린다 (test_emacs_ert.py와 같은 꼴).

- 콘솔 상속(이 프로세스의 콘솔)과 CREATE_NO_WINDOW(콘솔 없음 → cp949) 두 번.
- 러너 tests/nvim/run.lua가 `WORK-KB-TEST-LOCALE: cpNNN`(활성 콘솔 코드 페이지)과
  마지막 줄 `Ran N tests, P passed, F failed, S skipped`를 낸다.
- 상속 모드는 이 프로세스의 콘솔 코드 페이지와 같아야 하고(콘솔이 없으면 단정하지 않음),
  CREATE_NO_WINDOW는 cp949여야 한다. 두 모드의 로캘이 같으면(이 콘솔이 cp949가 아닌데) 실패.
- 실패 0, 건너뜀 0, 최소 개수 이상. scripts/serve.py가 있으면 `NOT RUN` 줄(실제 서버 시험)이 없어야 한다.
- stdin은 DEVNULL, 출력은 UTF-8로 해독. 볼트는 WORK_KB_TEST_TMP(ASCII tmp) 아래 한글 경로.
- XDG_CONFIG_HOME·XDG_DATA_HOME·XDG_STATE_HOME·XDG_CACHE_HOME은 tmp: 사용자의 %LOCALAPPDATA%\\nvim·nvim-data를
  읽거나 쓰지 않는다.
- 플러그인 폴더는 PLUGIN_DIR 하나로 정하고 러너에 WORK_KB_TEST_PLUGIN_DIR로 넘긴다.
- 멈춤 방지: tests/nvim_proc.run (Job Object로 나무 전체를 묶고 제한 시간이 지나면 모두 끝냄, 읽기도 끊음).
  nvim은 scoop shim이 아니라 실제 nvim.exe를 띄운다 (nvim_proc.resolve_exe). 끝난 뒤 남은 후손(파이프를
  쥔 python 등)이 있으면 실패다.
- KB_SKIP_NVIM=1일 때만 건너뛴다. 그 밖에 nvim이 없으면 실패다.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import nvim_proc

ROOT = Path(__file__).resolve().parents[1]
# 플러그인 폴더 (plugin/·lua/work-kb/가 있는 곳). 옮기면 여기 하나만 고친다
# (tests/nvim 쪽은 tests/nvim/minimal_init.lua의 PLUGIN_REL; 이 값이 환경 변수로 그것을 덮는다).
PLUGIN_DIR = ROOT / "nvim"
RUNNER = ROOT / "tests" / "nvim" / "run.lua"
INIT = ROOT / "tests" / "nvim" / "minimal_init.lua"
# conftest의 autouse 픽스처가 KB_* 환경변수를 지우므로 가져올 때 읽어 둔다.
SKIP_NVIM = os.environ.get("KB_SKIP_NVIM") == "1"
MIN_TESTS = 50  # 러너가 실제로 돌았는지 확인 (0개 통과를 성공으로 보지 않음)
TIMEOUT_SEC = 300

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
    found = nvim_proc.resolve_exe("nvim")
    if found is None:
        pytest.fail("nvim을 PATH에서 찾지 못했습니다 (건너뛰려면 KB_SKIP_NVIM=1)")
    return found


def _child_env(tmp_path: Path) -> dict[str, str]:
    drop = {"PYTHONPATH", "PYTHONHOME", "VIMINIT", "MYVIMRC", "NVIM", "NVIM_APPNAME", "VIMRUNTIME", "NVIM_LOG_FILE"}
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.upper().startswith(("KB_", "XDG_", "WORK_KB_TEST_")) and k.upper() not in drop
    }
    env["WORK_KB_TEST_TMP"] = str(tmp_path / "work")
    env["WORK_KB_TEST_PYTHON"] = sys.executable
    env["WORK_KB_TEST_PLUGIN_DIR"] = str(PLUGIN_DIR)
    for name in ("CONFIG", "DATA", "STATE", "CACHE"):
        xdg = tmp_path / "xdg" / name.lower()
        xdg.mkdir(parents=True, exist_ok=True)
        env[f"XDG_{name}_HOME"] = str(xdg)
    return env


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 콘솔 로캘 검사")
@pytest.mark.parametrize("mode", list(MODES))
def test_nvim_suite(mode, tmp_path):
    assert RUNNER.is_file() and INIT.is_file()
    assert (PLUGIN_DIR / "plugin" / "work-kb.lua").is_file(), PLUGIN_DIR
    assert str(tmp_path).isascii(), f"임시 경로가 ASCII가 아님: {tmp_path}"
    command = [
        _nvim(),
        "--headless",
        "-u",
        str(INIT.relative_to(ROOT)).replace("\\", "/"),
        "-l",
        str(RUNNER.relative_to(ROOT)).replace("\\", "/"),
    ]
    result = nvim_proc.run(
        command,
        timeout=TIMEOUT_SEC,
        cwd=ROOT,
        env=_child_env(tmp_path),
        creationflags=MODES[mode],
    )
    output = (result.stdout + result.stderr).replace("\r\n", "\n")  # nvim_proc은 줄 끝을 바꾸지 않는다
    tail = output[-8000:]
    assert not result.timed_out, f"{result.describe()}\n{tail}"
    assert not result.pipes_abandoned, f"{result.describe()}\n{tail}"
    # 이름을 읽지 못한 항목("pid:?")은 nvim이 끝나며 막 끝내는 중이던 프로세스다 (작업 목록에는 잠깐 남는다).
    alive = [item for item in result.leftovers if not item.endswith(":?")]
    assert not alive, f"nvim이 끝난 뒤 남은 후손: {result.describe()}\n{tail}"
    assert result.returncode == 0, f"{result.describe()}\n{tail}"
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
    print(f"{mode}: console code page={locale} (pytest 콘솔 {_console_cp()}); {summary.group(0)}; {result.describe()}")
    for line in output.splitlines():
        if line.startswith("NOT RUN"):
            print(line)
    expected = _expected_locale(mode)
    if expected:
        assert locale == expected, tail
    _seen_locales[mode] = locale
    if len(_seen_locales) == len(MODES) and _console_cp() not in (0, 949):
        assert _seen_locales["inherited-console"] != _seen_locales["create-no-window"], _seen_locales
