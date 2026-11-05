"""하위 프로세스를 절대 막히지 않게 돌린다 (Neovim·PowerShell 테스트 드라이버용).

subprocess.run(..., timeout=)은 Windows에서 시간이 넘으면 자식 하나만 죽이고 시간 제한 없이
communicate()를 다시 부른다. 손주(scoop shim 뒤의 진짜 nvim.exe, nvim이 띄운 git 등)가 상속받은
파이프를 쥐고 있으면 그 자리에서 영원히 기다린다. run()은:

- 자식을 CREATE_SUSPENDED로 만들어 Windows Job Object(JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, 벗어나기
  금지)에 넣은 뒤 깨운다. 그래서 자식이 띄우는 모든 후손이 같은 작업에 들어간다. 작업에 넣지 못하면
  시간 초과 때 `taskkill /T /F /PID`로 나무를 죽인다.
- scoop shim은 .shim 파일이 가리키는 실제 실행 파일로 바꾼다 (resolve_exe).
- 출력은 자체 읽기 스레드로 모은다. 시간이 넘으면 작업 전체를 끝내고, 읽기를 KILL_GRACE초 기다리고,
  그래도 막혀 있으면 CancelSynchronousIo로 읽기를 끊고 그때까지 읽은 것을 돌려준다 (pipes_abandoned).
- 자식이 정상으로 끝나도 작업에 남은 후손(leftovers)을 기록하고 끝낸다 (파이프를 쥔 고아가 남지 않게).
"""
from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
CREATE_SUSPENDED = 0x00000004

KILL_GRACE = 5.0  # 작업을 끝낸 뒤 읽기 스레드가 EOF를 볼 때까지 기다리는 초
EXIT_GRACE = 5.0  # 자식이 끝난 뒤 후손이 파이프를 닫기를 기다리는 초 (넘으면 작업을 끝냄)


@dataclass
class Result:
    args: list[str]
    returncode: int | None
    stdout: str | bytes
    stderr: str | bytes
    timed_out: bool
    elapsed: float
    pipes_abandoned: bool = False  # 작업을 끝낸 뒤에도 파이프가 닫히지 않아 읽기를 끊었다
    in_job: bool = False  # 자식이 Job Object에 들어갔다 (False면 taskkill 대체 경로)
    leftovers: list[str] = field(default_factory=list)  # 자식이 끝난 뒤 남아 있던 후손 "pid:이름"

    @property
    def output(self) -> str | bytes:
        return self.stdout + self.stderr

    def describe(self) -> str:
        state = "timed out" if self.timed_out else f"exit {self.returncode}"
        extra = []
        if self.pipes_abandoned:
            extra.append("pipes abandoned")
        if not self.in_job:
            extra.append("no job object")
        if self.leftovers:
            extra.append("leftover descendants killed: " + ", ".join(self.leftovers))
        return f"{state} after {self.elapsed:.1f}s" + ("; " + "; ".join(extra) if extra else "")


# ---------------------------------------------------------------- 실행 파일

def _shim_target(found: Path) -> str | None:
    shim = found.with_suffix(".shim")
    if not shim.is_file():
        return None
    m = re.search(r'^\s*path\s*=\s*"?([^"\r\n]+?)"?\s*$', shim.read_text(encoding="utf-8", errors="replace"), re.MULTILINE)
    if m and Path(m.group(1)).is_file():
        return m.group(1)
    return None


def resolve_exe(name: str) -> str | None:
    """PATH의 NAME. scoop shim이면 실제 실행 파일 (.shim 파일, nvim은 apps/neovim/current/bin도 본다)."""
    found = shutil.which(name)
    if found is None:
        return None
    path = Path(found)
    target = _shim_target(path)
    if target:
        return target
    if path.parent.name.lower() == "shims" and Path(name).stem.lower() == "nvim":
        real = path.parent.parent / "apps" / "neovim" / "current" / "bin" / "nvim.exe"
        if real.is_file():
            return str(real)
    return found


# ---------------------------------------------------------------- Windows Job Object

if sys.platform == "win32":
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll")

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _BASIC_LIMIT(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BASIC_LIMIT),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    _k32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID]
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.OpenThread.restype = wintypes.HANDLE
    _k32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.CancelSynchronousIo.argtypes = [wintypes.HANDLE]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    _ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    _ntdll.NtResumeProcess.restype = ctypes.c_long

    _JobObjectBasicProcessIdList = 3
    _JobObjectExtendedLimitInformation = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

    def _new_job() -> int | None:
        job = _k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _EXTENDED_LIMIT()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _k32.SetInformationJobObject(job, _JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)):
            _k32.CloseHandle(job)
            return None
        return job

    def _job_pids(job: int) -> list[int]:
        class _LIST(ctypes.Structure):
            _fields_ = [("Assigned", wintypes.DWORD), ("InList", wintypes.DWORD), ("Ids", ctypes.c_size_t * 1024)]
        lst = _LIST()
        if not _k32.QueryInformationJobObject(job, _JobObjectBasicProcessIdList, ctypes.byref(lst), ctypes.sizeof(lst), None):
            return []
        return [int(lst.Ids[i]) for i in range(min(lst.InList, 1024))]

    def _image_name(pid: int) -> str:
        h = _k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return "?"
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value)
            return "?"
        finally:
            _k32.CloseHandle(h)

    def _cancel_read(thread: threading.Thread) -> None:
        tid = getattr(thread, "native_id", None)
        if not tid:
            return
        h = _k32.OpenThread(0x0001, False, tid)  # THREAD_TERMINATE (CancelSynchronousIo에 필요)
        if h:
            try:
                _k32.CancelSynchronousIo(h)
            finally:
                _k32.CloseHandle(h)


def _taskkill_tree(pid: int) -> None:
    try:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
                       creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        pass


# ---------------------------------------------------------------- 읽기

class _Reader:
    def __init__(self, pipe):
        self.pipe = pipe
        self.data = bytearray()
        self.thread = threading.Thread(target=self._run, daemon=True, name="nvim_proc-reader")
        self.thread.start()

    def _run(self):
        try:
            while True:
                chunk = self.pipe.read(65536)
                if not chunk:
                    break
                self.data += chunk
        except (OSError, ValueError):
            pass

    def join(self, timeout: float) -> bool:
        self.thread.join(timeout)
        return not self.thread.is_alive()


# ---------------------------------------------------------------- 실행

def run(
    args: list[str],
    *,
    timeout: float,
    cwd: str | os.PathLike | None = None,
    env: dict[str, str] | None = None,
    creationflags: int = 0,
    encoding: str | None = "utf-8",
    errors: str = "replace",
    stdin=subprocess.DEVNULL,
    kill_grace: float = KILL_GRACE,
    exit_grace: float = EXIT_GRACE,
) -> Result:
    """ARGS를 돌려 Result를 돌려준다. 어떤 경우에도 대략 timeout + exit_grace + 2*kill_grace 안에 돌아온다.

    encoding=None이면 stdout·stderr는 bytes. 시간 초과는 예외가 아니라 Result.timed_out으로 알린다.
    """
    args = [str(a) for a in args]
    started = time.monotonic()
    win = sys.platform == "win32"
    job = _new_job() if win else None
    flags = creationflags | (CREATE_SUSPENDED if job else 0)
    try:
        proc = subprocess.Popen(args, cwd=None if cwd is None else str(cwd), env=env, stdin=stdin,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0, creationflags=flags)
    except BaseException:
        if job:
            _k32.CloseHandle(job)
        raise
    in_job = False
    if job:
        in_job = bool(_k32.AssignProcessToJobObject(job, int(proc._handle)))
        _ntdll.NtResumeProcess(int(proc._handle))
    out, err = _Reader(proc.stdout), _Reader(proc.stderr)

    def kill_all():
        if in_job:
            _k32.TerminateJobObject(job, 1)
        else:
            if win:
                _taskkill_tree(proc.pid)
            try:
                proc.kill()
            except OSError:
                pass

    timed_out = False
    leftovers: list[str] = []
    try:
        proc.wait(max(0.0, timeout - (time.monotonic() - started)))
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_all()
    else:
        # 자식은 끝났다. 남은 후손이 파이프를 쥐고 있을 수 있다.
        if in_job:
            remaining = [f"{pid}:{_image_name(pid)}" for pid in _job_pids(job)]
            if remaining:
                _k32.TerminateJobObject(job, 1)
            # CREATE_NO_WINDOW 자식의 conhost는 자식이 끝난 직후 잠깐 남는다: 보고하지 않는다 (끝내기는 한다)
            leftovers = [item for item in remaining if not item.lower().endswith(":conhost.exe")]
        if not (out.join(exit_grace) and err.join(0.1)):
            kill_all()

    pipes_abandoned = False
    if not (out.join(kill_grace) and err.join(kill_grace)):
        for reader in (out, err):
            if reader.thread.is_alive() and win:
                _cancel_read(reader.thread)
        pipes_abandoned = not (out.join(kill_grace) and err.join(0.5))
    try:
        proc.wait(kill_grace)
    except subprocess.TimeoutExpired:
        pass
    if job:
        _k32.CloseHandle(job)  # KILL_ON_JOB_CLOSE: 남은 것이 있으면 여기서 모두 끝난다
    for reader in (out, err):
        if not reader.thread.is_alive():
            try:
                reader.pipe.close()
            except OSError:
                pass

    def decode(b: bytearray):
        return bytes(b) if encoding is None else bytes(b).decode(encoding, errors)

    return Result(
        args=args,
        returncode=proc.returncode,
        stdout=decode(out.data),
        stderr=decode(err.data),
        timed_out=timed_out,
        elapsed=time.monotonic() - started,
        pipes_abandoned=pipes_abandoned,
        in_job=in_job,
        leftovers=leftovers,
    )
