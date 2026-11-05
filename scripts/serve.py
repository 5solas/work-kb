"""kb.py serve: 편집기(Neovim)용 상주 서버. 자주 쓰는 빠른 명령을 프로세스 시작 비용 없이 실행한다.

실행: `python -X utf8 scripts/kb.py serve [--idle-sec N]` (작업 폴더는 저장소, 명령줄은 ASCII).

틀(한 줄에 JSON 객체 하나, UTF-8, LF):
- 시작: 무거운 모듈을 미리 가져온 뒤 stdout에 `{"ready": true, "pid": <int>, "version": 1}`.
- `{"id": n, "argv": [...]}` → kb.py와 같은 진입점(core.run_main + kb.main, KB_AGENT 검사 포함)으로
  프로세스 안에서 실행하고 `{"id": n, "code": <종료 코드>, "out": "<stdout·stderr를 쓴 순서대로, LF>"}`.
- `{"id": n, "op": "ping"}` → `{"id": n, "ok": true}`. `{"op": "shutdown"}` → 종료 0(응답 없음).
- 빠른 로컬 명령(SERVED)만 실행한다. 그 밖의 kb.py 명령(SERVED에 없는 하위 명령 포함)과 잘못된 요청은
  실행하지 않고 `{"id": n, "refused": true, "reason": "..."}`로 답한다. 클라이언트는 그 명령을 한 번
  실행(one-shot)으로 돌린다. 예외: kb.py가 명령 모듈을 가져오기 전에 끝내는 argv(빈 argv, 첫 인수가
  도움말, 알 수 없는 명령 이름)는 부작용이 없어 그대로 실행한다 (한 번 실행과 같은 종료 0·4와 출력).
  줄을 읽지 못한 요청(UTF-8·JSON 오류)의 거부는 id를 알 수 없어 `"id": null`이다.
- 요청은 도착 순서대로 하나씩 처리한다. stdin EOF 또는 `--idle-sec`(기본 1800) 동안 요청이 없으면 종료 0.

stdout에는 응답 말고는 아무것도 나가지 않는다: 시작할 때 파일 서술자 0·1을 복제해 프로토콜 전용으로 쓰고,
0·1 자리는 NUL로 바꾼다(명령이나 하위 프로세스가 fd 1에 써도 프로토콜이 깨지지 않는다).
"""
from __future__ import annotations

import contextlib
import importlib
import io
import json
import logging
import os
import queue
import sys
import threading
import traceback
import warnings
from typing import Any, Callable, Iterator

from lib import core

PROTOCOL_VERSION = 1
DEFAULT_IDLE_SEC = 1800.0

# 서버에서 실행하는 명령: 이름 → 허용 하위 명령(None이면 하위 명령 구분 없음).
SERVED: dict[str, frozenset[str] | None] = {
    "status": None,
    "search": None,
    "today": None,
    "link": None,
    "proposals": frozenset({"list"}),
    "act": frozenset({"list", "transitions"}),
}
_HELP_WORDS = ("-h", "--help", "help")


# ---------------------------------------------------------------- 실행 대상 판정


def _kb_module():
    import kb  # scripts/kb.py. `python scripts/kb.py`로 실행되면 __main__과 별도로 한 번 더 읽힌다(부작용 없음)

    return kb


def refusal_reason(argv: list[str]) -> str | None:
    """서버에서 실행하면 안 되는 argv면 이유를, 실행해도 되면 None을 돌려준다.

    kb.py가 모듈을 가져오기 전에 끝내는 경로(인수 없음, 도움말, 알 수 없는 명령)는 부작용이 없으므로 실행한다.
    """
    if not argv or argv[0] in _HELP_WORDS:
        return None
    kb = _kb_module()
    name = kb.ALIASES.get(argv[0], argv[0])
    if name not in kb.COMMANDS:
        return None  # kb.main이 '알 수 없는 명령'(종료 4)으로 끝낸다
    if name not in SERVED:
        return f"서버에서 실행하지 않는 명령입니다: {argv[0]}"
    subs = SERVED[name]
    if subs is not None and (len(argv) < 2 or argv[1] not in subs):
        sub = argv[1] if len(argv) > 1 else ""
        return f"서버에서 실행하지 않는 하위 명령입니다: {argv[0]} {sub}".rstrip()
    return None


# ---------------------------------------------------------------- 한 요청 실행


def _new_capture() -> tuple[io.BytesIO, io.TextIOWrapper]:
    """stdout·stderr가 함께 쓰는 스트림. 쓴 순서가 그대로 남고, 한 번 실행처럼 UTF-8(errors=replace)로 바꾼다."""
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="\n", write_through=True)
    return raw, stream


@contextlib.contextmanager
def _captured(argv0: str, stdin: bytes) -> Iterator[Callable[[], str]]:
    """sys.stdin/stdout/stderr/argv를 바꿔 끼우고, 끝나면 되돌린다. 안에서 읽을 출력 함수를 준다."""
    raw, stream = _new_capture()
    saved = (sys.stdin, sys.stdout, sys.stderr, sys.argv)
    sys.stdin = io.TextIOWrapper(io.BytesIO(stdin), encoding="utf-8", errors="replace")
    sys.stdout = stream
    sys.stderr = stream
    sys.argv = [argv0, "--argv-stdin"]

    def output() -> str:
        with contextlib.suppress(ValueError):
            stream.flush()
        return raw.getvalue().decode("utf-8", "replace")

    try:
        yield output
    finally:
        sys.stdin, sys.stdout, sys.stderr, sys.argv = saved


def _exit_code(exc: SystemExit) -> int:
    """인터프리터가 SystemExit를 종료 코드로 바꾸는 규칙(문자열 등은 stderr에 쓰고 1)."""
    code = exc.code
    if code is None:
        return 0
    if isinstance(code, int):
        return int(code)
    print(code, file=sys.stderr)
    return 1


def _reset_logging() -> None:
    """요청 사이에 work-kb 로그 핸들러를 닫는다(볼트 파일을 잡고 있지 않게, 다음 요청이 자기 볼트로 다시 만든다)."""
    for name, logger in list(logging.Logger.manager.loggerDict.items()):
        if not name.startswith("work-kb") or not isinstance(logger, logging.Logger):
            continue
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            with contextlib.suppress(Exception):
                handler.close()
        if hasattr(logger, "_kb_log_dir"):
            with contextlib.suppress(AttributeError):
                delattr(logger, "_kb_log_dir")


def locks_leaked() -> bool:
    """요청이 끝났는데 kb_lock이 남아 있는가. 정상 명령은 모두 `with`로 잡으므로 늘 거짓이다.

    남은 잠금은 그 잠금을 연 생성기가 서술자를 닫아야 하므로 여기서 억지로 풀지 않는다. 서버가 응답을 보낸 뒤
    스스로 끝나 운영체제가 잠금을 풀게 한다(요청 사이에 잠금을 쥐지 않는다).
    """
    return bool(core._HELD)


def run_argv(argv: list[str], dispatch: Callable[[list[str]], int | None] | None = None) -> tuple[int, str]:
    """argv를 `kb.py --argv-stdin`과 같은 경로로 실행한다. (종료 코드, 합친 출력).

    한 번 실행과 똑같이 stdin에 UTF-8 JSON 배열을 두고 core.run_main(kb.main, ["--argv-stdin"])을 부른다.
    run_main의 KbError·KeyboardInterrupt 처리를 그대로 쓰고, SystemExit는 인터프리터 규칙으로 종료 코드가 된다.
    run_main이 다시 던지는 예외는 인터프리터처럼 traceback을 출력에 쓰고 종료 코드 1로 바꾼다. 서버는 죽지 않는다.
    """
    main = dispatch or _kb_module().main
    stdin = json.dumps(list(argv), ensure_ascii=False).encode("utf-8")
    environ = dict(os.environ)
    cwd = os.getcwd()
    argv0 = sys.argv[0] if sys.argv else "kb.py"
    code, out = 1, ""
    try:
        with _captured(argv0, stdin) as output, warnings.catch_warnings():
            try:
                core.run_main(main, ["--argv-stdin"])
            except SystemExit as exc:
                code = _exit_code(exc)
            except Exception as exc:  # noqa: BLE001 - 서버는 요청 하나 때문에 죽지 않는다
                traceback.print_exception(exc, file=sys.stderr)
                code = 1
            out = output()
    finally:
        _reset_logging()
        if dict(os.environ) != environ:
            os.environ.clear()
            os.environ.update(environ)
        if os.getcwd() != cwd:
            with contextlib.suppress(OSError):
                os.chdir(cwd)
    return code, out.replace("\r\n", "\n")


# ---------------------------------------------------------------- 요청 처리


SHUTDOWN = object()


def _refused(ident: Any, reason: str) -> dict:
    return {"id": ident, "refused": True, "reason": reason}


def handle(request: Any) -> dict | object:
    """요청 객체 하나를 처리해 응답(dict) 또는 SHUTDOWN을 돌려준다."""
    if not isinstance(request, dict):
        return _refused(None, "요청은 JSON 객체여야 합니다")
    ident = request.get("id")
    if ident is not None and (not isinstance(ident, int) or isinstance(ident, bool)):
        return _refused(None, "id는 정수여야 합니다")
    op = request.get("op")
    if op is not None:
        if op == "shutdown":
            return SHUTDOWN
        if op == "ping":
            return {"id": ident, "ok": True}
        return _refused(ident, f"알 수 없는 op: {op!r}")
    argv = request.get("argv")
    if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
        return _refused(ident, "argv는 문자열 배열이어야 합니다")
    if ident is None:
        return _refused(None, "argv 요청에는 정수 id가 필요합니다")
    reason = refusal_reason(argv)
    if reason is not None:
        return _refused(ident, reason)
    code, out = run_argv(argv)
    return {"id": ident, "code": code, "out": out}


def handle_line(line: bytes) -> dict | object:
    try:
        request = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return _refused(None, f"JSON을 읽지 못했습니다: {exc}")
    return handle(request)


# ---------------------------------------------------------------- 입출력


class _Channel:
    """프로토콜 전용 서술자. stdin은 별도 스레드가 날 바이트로 읽어 줄 단위로 큐에 넣는다(Windows 파이프)."""

    def __init__(self, in_fd: int, out_fd: int) -> None:
        self.in_fd = in_fd
        self.out_fd = out_fd
        self.lines: queue.Queue[bytes | None] = queue.Queue()
        self._thread = threading.Thread(target=self._read, name="kb-serve-stdin", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _read(self) -> None:
        pending = b""
        try:
            while True:
                chunk = os.read(self.in_fd, 65536)
                if not chunk:
                    break
                pending += chunk
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    self.lines.put(line)
        except OSError:
            pass
        if pending:
            self.lines.put(pending)
        self.lines.put(None)

    def send(self, obj: dict) -> None:
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8", "replace")
        view = memoryview(data)
        while view:
            written = os.write(self.out_fd, view)
            view = view[written:]


def _take_stdio() -> _Channel:
    """fd 0·1을 복제해 프로토콜 전용으로 두고, 0·1 자리는 NUL로 바꾼다."""
    if sys.stdin is None or sys.stdout is None:
        raise core.ConfigError("kb.py serve는 표준 입출력(파이프)이 있어야 합니다")
    with contextlib.suppress(Exception):
        sys.stdout.flush()
    in_fd = os.dup(0)
    out_fd = os.dup(1)
    null = os.open(os.devnull, os.O_RDWR)
    try:
        os.dup2(null, 0)
        os.dup2(null, 1)
    finally:
        os.close(null)
    return _Channel(in_fd, out_fd)


def preimport() -> None:
    """서버가 실행하는 명령의 모듈을 미리 가져온다. 가져오면서 무엇을 출력해도 프로토콜에 섞이지 않는다."""
    kb = _kb_module()
    names = ["lib.config", "yaml"]
    names += [kb.COMMANDS[name].module for name in SERVED if name in kb.COMMANDS]
    raw, stream = _new_capture()
    saved = (sys.stdout, sys.stderr)
    sys.stdout = sys.stderr = stream
    try:
        for name in names:
            try:
                importlib.import_module(name)
            except Exception:  # noqa: BLE001 - 실패하면 그 명령의 요청이 한 번 실행과 같은 오류를 낸다
                traceback.print_exc()
    finally:
        sys.stdout, sys.stderr = saved
    text = raw.getvalue().decode("utf-8", "replace")
    if text and sys.stderr is not None:
        sys.stderr.write(text)
        with contextlib.suppress(Exception):
            sys.stderr.flush()


def serve(channel: _Channel, idle_sec: float) -> int:
    channel.start()
    channel.send({"ready": True, "pid": os.getpid(), "version": PROTOCOL_VERSION})
    while True:
        try:
            line = channel.lines.get(timeout=idle_sec)
        except queue.Empty:
            return 0
        if line is None:
            return 0
        line = line.rstrip(b"\r")
        if not line.strip():
            continue
        try:
            response = handle_line(line)
        except Exception as exc:  # noqa: BLE001 - 처리기 자체의 버그도 서버를 죽이지 않는다
            response = _refused(None, f"요청을 처리하지 못했습니다: {exc!r}")
        if response is SHUTDOWN:
            return 0
        try:
            channel.send(response)  # type: ignore[arg-type]
        except OSError:
            return 0  # 클라이언트가 사라짐
        if locks_leaked():
            if sys.stderr is not None:
                sys.stderr.write("kb.py serve: 요청이 kb 잠금을 풀지 않아 서버를 끝냅니다\n")
            return 0


def main(argv: list[str]) -> int:
    parser = core.make_parser("kb.py serve", "편집기용 상주 서버: stdin/stdout에 한 줄 JSON 요청·응답 (빠른 명령만 실행)")
    parser.add_argument("--idle-sec", type=float, default=DEFAULT_IDLE_SEC, help=f"요청 없이 이만큼 지나면 종료 (기본 {DEFAULT_IDLE_SEC:g}초)")
    args = parser.parse_args(argv)
    if not args.idle_sec > 0:
        raise core.ConfigError("--idle-sec은 0보다 커야 합니다")
    channel = _take_stdio()
    preimport()
    return serve(channel, args.idle_sec)


if __name__ == "__main__":
    core.run_main(main)
