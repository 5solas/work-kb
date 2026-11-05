"""선택 기능 '로그온 사전 시작'(리드 결정 A3)의 헤드리스 시험: nvim/bin/nvim-server.ps1, neovide-attach.ps1.

- 시험마다 고유한 파이프 이름(\\\\.\\pipe\\work-kb-nvim-test-<pid>-<난수>)을 쓴다. 실제 work-kb-nvim 파이프는 건드리지 않는다.
- 서버 nvim은 임시 XDG_CONFIG_HOME·XDG_DATA_HOME·XDG_STATE_HOME으로 떠서 사용자 설정·shada를 읽거나 쓰지 않는다.
- 확인하는 것: 런처가 분리된(detached) 서버를 띄우고 곧바로 끝난다, `nvim --headless --server <파이프> --remote-expr 1+1`이
  2를 돌려준다, 두 번째 시작(차례로·동시에)은 서버를 하나 더 만들지 않는다, :qa!를 보내면 프로세스와 파이프가 사라진다.
- 확인하지 못하는 것: Neovide가 실제로 붙어 창을 그리는지(화면이 필요). neovide-attach.ps1은 -WhatIf로 어떤 명령을
  고르는지만 본다. 수동 확인 절차는 neovide-attach.ps1 머리말에 있다.
- 관찰: nvim 0.12.6에서 --headless 없이 `nvim --server <파이프> --remote-expr "1+1"`을 출력 캡처로 돌리면 2 대신
  TUI 제어 문자만 나온다. 그래서 런처·capture.ahk·이 시험 모두 --headless를 붙인다.
- KB_SKIP_NVIM=1일 때만 건너뛴다.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "nvim" / "bin" / "nvim-server.ps1"
ATTACH = ROOT / "nvim" / "bin" / "neovide-attach.ps1"
SYSROOT = Path(os.environ.get("SystemRoot", r"C:\Windows"))
PS51 = SYSROOT / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
PWSH = shutil.which("pwsh")
SHELLS = {"ps51": str(PS51)}
if PWSH:
    SHELLS["pwsh"] = PWSH
SKIP_NVIM = os.environ.get("KB_SKIP_NVIM") == "1"

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Windows 전용(이름 있는 파이프)"),
    pytest.mark.skipif(SKIP_NVIM, reason="KB_SKIP_NVIM=1"),
]

STILL_ACTIVE = 259
ERROR_SEM_TIMEOUT = 121


def _nvim() -> str:
    found = shutil.which("nvim")
    if found is None:
        pytest.fail("nvim을 PATH에서 찾지 못했습니다 (건너뛰려면 KB_SKIP_NVIM=1)")
    return found


def pipe_exists(name: str) -> bool:
    """capture.ahk와 같은 판정: WaitNamedPipeW가 성공하거나, 있지만 바쁘면(121) 있는 것이다."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitNamedPipeW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    if kernel32.WaitNamedPipeW(name, 1):
        return True
    return ctypes.get_last_error() == ERROR_SEM_TIMEOUT


def alive(pid: int) -> bool:
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = ctypes.c_ulong()
    try:
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    finally:
        kernel32.CloseHandle(handle)
    return code.value == STILL_ACTIVE


def servers_on(pipe: str) -> list[int]:
    """명령줄에 이 파이프 이름이 있는 nvim.exe 프로세스 ID (서버만: --listen)."""
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='nvim.exe'\" | "
        "ForEach-Object { [Console]::Out.WriteLine([string]$_.ProcessId + '|' + $_.CommandLine) }"
    )
    proc = subprocess.run([str(PS51), "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
    pids = []
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        pid, _, cmdline = line.partition("|")
        if "--listen" in cmdline and pipe in cmdline:
            pids.append(int(pid))
    return pids


@pytest.fixture
def pipe():
    name = rf"\\.\pipe\work-kb-nvim-test-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    yield name
    for pid in servers_on(name):  # 시험이 실패해도 서버를 남기지 않는다
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)


@pytest.fixture
def server_env(tmp_path) -> dict[str, str]:
    drop = ("KB_", "NVIM", "VIM", "XDG_", "PSMODULEPATH", "PSEXECUTIONPOLICYPREFERENCE")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(drop)}
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        d = tmp_path / var.lower()
        d.mkdir()
        env[var] = str(d)
    return env


def run_launcher(shell: str, script: Path, *args: str, env: dict[str, str], cwd: Path) -> tuple[int, str, float]:
    started = time.monotonic()
    proc = subprocess.run(
        [SHELLS[shell], "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script), *args],
        capture_output=True, stdin=subprocess.DEVNULL, env=env, cwd=str(cwd), timeout=90,
    )
    out = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
    return proc.returncode, out, time.monotonic() - started


def remote(nvim: str, pipe: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([nvim, "--headless", "--server", pipe, *args],
                          capture_output=True, stdin=subprocess.DEVNULL, timeout=30)


def wait_until(predicate, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def _started_pid(out: str, pipe: str) -> int:
    line = next((ln for ln in out.splitlines() if ln.startswith("STARTED ")), None)
    assert line, out
    pid_part, name = line.split()[1:3]
    assert name == pipe, out
    return int(pid_part.split("=", 1)[1])


def test_launchers_are_ascii():
    for path in (SERVER, ATTACH):
        path.read_bytes().decode("ascii")


@pytest.mark.parametrize("shell", list(SHELLS))
def test_server_start_answer_no_second_server_shutdown(shell, pipe, server_env, tmp_path):
    nvim = _nvim()
    assert not pipe_exists(pipe)

    code, out, seconds = run_launcher(shell, SERVER, "-Pipe", pipe, "-WorkingDirectory", str(tmp_path),
                                      env=server_env, cwd=tmp_path)
    assert code == 0, out
    pid = _started_pid(out, pipe)
    assert seconds < 60, f"런처가 서버를 기다리며 붙잡혀 있었다 ({seconds:.1f}s)"  # 분리 실행 확인
    assert alive(pid) and pipe_exists(pipe)

    answer = remote(nvim, pipe, "--remote-expr", "1+1")
    assert answer.returncode == 0, answer.stderr
    assert answer.stdout.decode("utf-8").strip() == "2"
    cwd = remote(nvim, pipe, "--remote-expr", "getcwd()").stdout.decode("utf-8").strip()
    assert os.path.normcase(cwd) == os.path.normcase(str(tmp_path))

    code, out, _ = run_launcher(shell, SERVER, "-Pipe", pipe, env=server_env, cwd=tmp_path)
    assert code == 0, out
    assert out.strip().splitlines()[-1] == f"RUNNING {pipe}", out
    assert servers_on(pipe) == [pid]

    sent = remote(nvim, pipe, "--remote-send", r"<C-\><C-N>:qa!<CR>")
    assert sent.returncode == 0, sent.stderr
    assert wait_until(lambda: not alive(pid), 15), "서버가 :qa! 뒤에도 살아 있다"
    assert wait_until(lambda: not pipe_exists(pipe), 5)
    assert servers_on(pipe) == []


def test_concurrent_starts_make_one_server(pipe, server_env, tmp_path):
    nvim = _nvim()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_launcher, "ps51", SERVER, "-Pipe", pipe, env=server_env, cwd=tmp_path) for _ in range(2)]
        results = [f.result() for f in futures]
    outs = [out for _, out, _ in results]
    assert all(code == 0 for code, _, _ in results), outs
    last = sorted(out.strip().splitlines()[-1].split()[0] for out in outs)
    assert last == ["RUNNING", "STARTED"], outs
    pids = servers_on(pipe)
    assert len(pids) == 1, (pids, outs)
    remote(nvim, pipe, "--remote-send", r"<C-\><C-N>:qa!<CR>")
    assert wait_until(lambda: not alive(pids[0]), 15)


def test_server_rejects_bad_pipe_name(server_env, tmp_path):
    code, out, _ = run_launcher("ps51", SERVER, "-Pipe", r"C:\not-a-pipe", env=server_env, cwd=tmp_path)
    assert code == 1 and "FAILED not a pipe name" in out


def test_server_whatif_starts_nothing(pipe, server_env, tmp_path):
    code, out, _ = run_launcher("ps51", SERVER, "-Pipe", pipe, "-WhatIf", env=server_env, cwd=tmp_path)
    assert code == 0 and f"WHATIF start " in out and f"--headless --listen {pipe}" in out, out
    assert not pipe_exists(pipe) and servers_on(pipe) == []


@pytest.mark.parametrize("shell", list(SHELLS))
def test_attach_launcher_picks_server_or_fallback(shell, pipe, server_env, tmp_path):
    """Neovide는 띄우지 않는다(-WhatIf). 파이프가 없으면 대체 시작, 있으면 --server로 붙는다."""
    fake = r"C:\fake\neovide.exe"
    code, out, _ = run_launcher(shell, ATTACH, "-Pipe", pipe, "-Neovide", fake, "-WhatIf", env=server_env, cwd=tmp_path)
    assert code == 0, out
    assert f"WHATIF START {fake} -- --listen {pipe}" in out, out

    code, out, _ = run_launcher(shell, SERVER, "-Pipe", pipe, env=server_env, cwd=tmp_path)
    assert code == 0, out
    pid = _started_pid(out, pipe)
    code, out, _ = run_launcher(shell, ATTACH, "-Pipe", pipe, "-Neovide", fake, "-WhatIf", env=server_env, cwd=tmp_path)
    assert code == 0, out
    assert f"WHATIF ATTACH {fake} --server {pipe}" in out, out
    remote(_nvim(), pipe, "--remote-send", r"<C-\><C-N>:qa!<CR>")
    assert wait_until(lambda: not alive(pid), 15)
