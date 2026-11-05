"""tests/nvim_proc.py: 시간 제한 실행이 손주가 파이프를 쥐고 있어도 막히지 않고, 후손을 남기지 않는다."""
from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from pathlib import Path

import pytest

import nvim_proc

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object")

MODES = {"inherited-console": 0, "create-no-window": nvim_proc.CREATE_NO_WINDOW}

# 손주: 자기 pid를 찍고 (상속받은 stdout = 테스트의 파이프를 쥔 채) 오래 잔다.
GRANDCHILD = """
import os, sys, time
sys.stdout.write(f"GRAND {os.getpid()}\\n"); sys.stdout.flush()
time.sleep(120)
"""

# 자식: pid를 찍고 손주를 띄운다 (stdout·stderr 상속). SLEEP초 뒤 끝낸다.
CHILD = """
import os, subprocess, sys, time
sys.stdout.write(f"CHILD {os.getpid()} \\uD55C\\uAE00\\n"); sys.stdout.flush()
subprocess.Popen([sys.executable, sys.argv[1]], stdin=subprocess.DEVNULL, stdout=1, stderr=2)
sys.stderr.write("spawned\\n"); sys.stderr.flush()
time.sleep(float(sys.argv[2]))
sys.exit(3)
"""


def _alive(pid: int) -> bool:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    h = k32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not h:
        return False
    try:
        return k32.WaitForSingleObject(ctypes.c_void_p(h), 0) != 0  # WAIT_OBJECT_0 = 끝남
    finally:
        k32.CloseHandle(ctypes.c_void_p(h))


def _gone(pids: list[int], within: float = 3.0) -> list[int]:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        alive = [p for p in pids if _alive(p)]
        if not alive:
            return []
        time.sleep(0.05)
    return [p for p in pids if _alive(p)]


def _pids(text: str, tag: str) -> list[int]:
    return [int(line.split()[1]) for line in text.splitlines() if line.startswith(tag + " ")]


@pytest.fixture
def scripts(tmp_path) -> tuple[Path, Path]:
    child, grand = tmp_path / "child.py", tmp_path / "grand.py"
    child.write_text(CHILD, encoding="utf-8")
    grand.write_text(GRANDCHILD, encoding="utf-8")
    return child, grand


def _wait_for_grandchild(child: Path, grand: Path, sleep: float, timeout: float, mode: str, **kw):
    return nvim_proc.run([sys.executable, "-X", "utf8", str(child), str(grand), str(sleep)],
                         timeout=timeout, creationflags=MODES[mode], **kw)


@pytest.mark.parametrize("mode", list(MODES))
def test_timeout_kills_child_and_grandchild_holding_the_pipe(scripts, mode):
    child, grand = scripts
    started = time.monotonic()
    result = _wait_for_grandchild(child, grand, sleep=120, timeout=4, mode=mode)
    took = time.monotonic() - started
    assert result.timed_out, result.describe()
    assert result.in_job, result.describe()
    assert not result.pipes_abandoned, result.describe()
    assert took < 10, (took, result.describe())
    grands = _pids(result.stdout, "GRAND")
    children = _pids(result.stdout, "CHILD")
    assert grands and children, result.output
    assert "한글" in result.stdout
    assert _gone(grands + children) == []


@pytest.mark.parametrize("mode", list(MODES))
def test_child_exits_but_grandchild_keeps_pipe_open(scripts, mode):
    child, grand = scripts
    result = _wait_for_grandchild(child, grand, sleep=1.5, timeout=60, mode=mode, exit_grace=1.0)
    assert not result.timed_out, result.describe()
    assert result.returncode == 3, result.describe()
    assert result.elapsed < 10, result.describe()
    assert "spawned" in result.stderr
    grands = _pids(result.stdout, "GRAND")
    assert grands, result.output
    assert any(item.startswith(f"{grands[0]}:") for item in result.leftovers), result.leftovers
    assert _gone(grands + _pids(result.stdout, "CHILD")) == []


def test_without_job_object_falls_back_to_taskkill_tree(scripts, monkeypatch):
    monkeypatch.setattr(nvim_proc, "_new_job", lambda: None)
    child, grand = scripts
    result = _wait_for_grandchild(child, grand, sleep=120, timeout=4, mode="inherited-console")
    assert result.timed_out and not result.in_job, result.describe()
    assert result.elapsed < 20, result.describe()
    assert _gone(_pids(result.stdout, "GRAND") + _pids(result.stdout, "CHILD")) == []


def test_plain_run_returns_output_and_code():
    code = "import sys; print('\\ud55c\\uae00 out'); print('err', file=sys.stderr); sys.exit(7)"
    result = nvim_proc.run([sys.executable, "-X", "utf8", "-c", code], timeout=30)
    assert result.returncode == 7 and not result.timed_out, result.describe()
    assert result.stdout.strip() == "한글 out"
    assert result.stderr.strip() == "err"
    raw = nvim_proc.run([sys.executable, "-c", "print('x')"], timeout=30, encoding=None,
                        creationflags=nvim_proc.CREATE_NO_WINDOW)
    assert raw.stdout.strip() == b"x"
    assert raw.leftovers == [], raw.leftovers  # 자식의 conhost는 남은 후손으로 치지 않는다


def test_blocked_reader_is_cancelled():
    import os

    r, w = os.pipe()
    pipe = open(r, "rb", buffering=0)
    reader = nvim_proc._Reader(pipe)
    try:
        assert not reader.join(0.3)
        nvim_proc._cancel_read(reader.thread)
        assert reader.join(3), "CancelSynchronousIo가 읽기를 끊지 못함"
    finally:
        os.close(w)
        pipe.close()


def test_resolve_exe_follows_scoop_shim(tmp_path, monkeypatch):
    shims, real = tmp_path / "shims", tmp_path / "apps" / "neovim" / "current" / "bin" / "nvim.exe"
    shims.mkdir()
    real.parent.mkdir(parents=True)
    real.write_bytes(b"")
    (shims / "nvim.exe").write_bytes(b"")
    monkeypatch.setattr(nvim_proc.shutil, "which", lambda name: str(shims / "nvim.exe"))
    assert nvim_proc.resolve_exe("nvim") == str(real)  # .shim 없음: apps/neovim/current/bin
    (shims / "nvim.shim").write_text(f'path = "{real}"\nargs = \n', encoding="utf-8")
    assert nvim_proc.resolve_exe("nvim") == str(real)
    (shims / "nvim.shim").write_text('path = "C:\\nope\\nvim.exe"\n', encoding="utf-8")
    real.unlink()
    assert nvim_proc.resolve_exe("nvim") == str(shims / "nvim.exe")
    monkeypatch.setattr(nvim_proc.shutil, "which", lambda name: None)
    assert nvim_proc.resolve_exe("nvim") is None
