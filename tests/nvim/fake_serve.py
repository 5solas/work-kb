"""계약 A를 말하는 가짜 `kb.py serve` (tests/nvim 서버 클라이언트 시험 전용).

python -X utf8 tests/nvim/fake_serve.py [--idle-sec N] [--mode ok|bad-ready|no-ready|garbage]

- 시작하면 준비 줄 {"ready": true, "pid": <pid>, "version": 1}을 낸다 (mode에 따라 틀리게).
- {"id": n, "op": "ping"} → {"id": n, "ok": true}, {"op": "shutdown"} → 종료 0.
- {"id": n, "argv": [...]}: 계약의 빠른 명령만 받는다. 그 밖은 refused.
  argv에 시험용 표지가 있으면: --fake-refuse 거부, --fake-die 답 없이 종료 3,
  --fake-sleep 5초 뒤 답, --fake-code N 종료 코드 N.
  답의 out은 "served <argv JSON>\npid=<pid>\n".
- stdin EOF 또는 --idle-sec 동안 요청이 없으면 종료 0.
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time

SERVED = {"status", "search", "today", "link"}
SERVED_SUB = {"proposals": {"list"}, "act": {"list", "transitions"}}


def send(obj: dict) -> None:
    sys.stdout.buffer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def served(argv: list[str]) -> bool:
    if not argv:
        return False
    if argv[0] in SERVED:
        return True
    return len(argv) > 1 and argv[1] in SERVED_SUB.get(argv[0], set())


def option(args: list[str], name: str, default: str) -> str:
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return default


def main() -> int:
    args = sys.argv[1:]
    idle = float(option(args, "--idle-sec", "1800"))
    mode = option(args, "--mode", "ok")
    if mode == "no-ready":
        time.sleep(30)
        return 0
    if mode == "bad-ready":
        send({"ready": True, "pid": os.getpid(), "version": 99})
    elif mode == "garbage":
        sys.stdout.buffer.write(b"Traceback: not json\n")
        sys.stdout.buffer.flush()
    else:
        send({"ready": True, "pid": os.getpid(), "version": 1})
    sys.stderr.buffer.write("가짜 서버 시작\n".encode("utf-8"))
    sys.stderr.buffer.flush()

    lines: queue.Queue[bytes] = queue.Queue()

    def reader() -> None:
        for raw in sys.stdin.buffer:
            lines.put(raw)
        lines.put(b"")

    threading.Thread(target=reader, daemon=True).start()
    while True:
        try:
            raw = lines.get(timeout=idle)
        except queue.Empty:
            return 0
        if raw == b"":
            return 0
        try:
            msg = json.loads(raw.decode("utf-8"))
        except ValueError:
            send({"id": None, "refused": True, "reason": "bad json"})
            continue
        rid = msg.get("id")
        if msg.get("op") == "shutdown":
            return 0
        if msg.get("op") == "ping":
            send({"id": rid, "ok": True})
            continue
        argv = msg.get("argv")
        if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
            send({"id": rid, "refused": True, "reason": "argv"})
            continue
        if "--fake-die" in argv:
            os._exit(3)
        if "--fake-refuse" in argv or not served(argv):
            send({"id": rid, "refused": True, "reason": "not served"})
            continue
        if "--fake-sleep" in argv:
            time.sleep(5)
        code = int(option(argv, "--fake-code", "0"))
        out = "served " + json.dumps(argv, ensure_ascii=False) + "\npid=" + str(os.getpid()) + "\n"
        send({"id": rid, "code": code, "out": out})


if __name__ == "__main__":
    sys.exit(main())
