"""kb.py serve (Neovim 프런트엔드의 상주 서버) 계약 A.

서버는 `python -X utf8 scripts/kb.py serve`로 띄우고(run_kb와 같은 격리 환경), 같은 argv를 한 번 실행
(`kb.py --argv-stdin`, stdout·stderr 합침)과 서버에 각각 보내 종료 코드·출력·볼트 결과가 같은지 본다.
합친 출력의 순서: 서버는 쓴 순서 그대로다. 한 번 실행은 stdout이 파이프에서 블록 버퍼라
stdout·stderr를 둘 다 쓰는 명령은 순서가 달라질 수 있으므로, 기준은 PYTHONUNBUFFERED=1 한 번 실행이다.
한 스트림만 쓰는 경우는 버퍼가 있는 보통 한 번 실행과도 똑같아야 한다.
"""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import kb
import serve
from lib import config, outbox
from lib import secrets as kb_secrets

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TESTS = ROOT / "tests"
NOW = "2026-11-05T09:12:00+09:00"
TOKEN = "serve-test-token-1234"
_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")

YESTERDAY = (
    "---\n"
    "date: 2026-11-04\n"
    "---\n"
    "## 오늘\n"
    "<!-- kb:today:start -->\n"
    "<!-- kb:today:end -->\n"
    "\n"
    "## 이월\n"
    "<!-- kb:carryover:start -->\n"
    "<!-- kb:carryover:end -->\n"
    "\n"
    "## 기록\n"
    "- 09:12 [[KB-12]] @김철수 와 캐시 결정 😀 회의\n"
    "- [ ] 14:00 캐시 정리 숙제\n"
    "- 15:30 KB-7 리뷰 요청\n"
)


# ---------------------------------------------------------------- 준비


def child_env(tmp_path: Path, root: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    """conftest.run_kb와 같은 격리 환경(-X utf8 모드)."""
    drop = {"DEEPINFRA_API_KEY", "PYTHONUTF8", "PYTHONIOENCODING", "PYTHONUNBUFFERED", *_PROXY_KEYS}
    env = {k: v for k, v in os.environ.items() if not k.startswith("KB_") and k not in drop}
    env.update(
        {
            "PYTHONPATH": str(TESTS),
            "PYTHON_KEYRING_BACKEND": "fake_keyring.FileKeyring",
            "KB_FAKE_KEYRING_FILE": str(tmp_path / "keyring.json"),
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "NO_PROXY": "127.0.0.1,localhost",
            "KB_ROOT": str(root),
            "PYTHONUTF8": "1",
        }
    )
    if "KB_FAKE_NOW" in os.environ:
        env["KB_FAKE_NOW"] = os.environ["KB_FAKE_NOW"]
    if extra:
        env.update(extra)
    return env


def oneshot(env: dict[str, str], argv: list[str], *, unbuffered: bool = True, script: Path | None = None) -> tuple[int, str]:
    """`python -X utf8 scripts/kb.py --argv-stdin`, stdout·stderr를 한 파이프로 합쳐 읽는다(편집기와 같게)."""
    run_env = dict(env)
    if unbuffered:
        run_env["PYTHONUNBUFFERED"] = "1"
    command = [sys.executable, "-X", "utf8", str(script or "scripts/kb.py"), "--argv-stdin"]
    result = subprocess.run(
        command,
        input=json.dumps(argv, ensure_ascii=False).encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=run_env,
        cwd=str(ROOT),
        timeout=120,
    )
    return result.returncode, result.stdout.decode("utf-8").replace("\r\n", "\n")


class Server:
    """kb.py serve 하위 프로세스. stdout 줄은 스레드가 큐에 넣는다(시간 제한 읽기)."""

    def __init__(self, env: dict[str, str], *args: str, script: Path | None = None) -> None:
        command = [sys.executable, "-X", "utf8", str(script or "scripts/kb.py"), "serve", *args]
        self.proc = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=str(ROOT)
        )
        self.lines: queue.Queue[bytes | None] = queue.Queue()
        self.stderr: list[bytes] = []
        self._next = 0
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=lambda: self.stderr.extend(iter(self.proc.stderr.readline, b"")), daemon=True).start()
        self.ready = self.read(timeout=60)
        self.ready_at = time.monotonic()

    def _pump(self) -> None:
        for line in iter(self.proc.stdout.readline, b""):
            self.lines.put(line)
        self.lines.put(None)

    def read_raw(self, timeout: float = 60) -> bytes | None:
        try:
            return self.lines.get(timeout=timeout)
        except queue.Empty:
            raise AssertionError(f"서버 응답 없음. stderr={self.err()}") from None

    def read(self, timeout: float = 60) -> dict:
        line = self.read_raw(timeout)
        assert line is not None, f"서버가 끝남. stderr={self.err()}"
        assert line.endswith(b"\n") and not line.endswith(b"\r\n"), line
        return json.loads(line.decode("utf-8"))

    def send_raw(self, data: bytes) -> None:
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def send(self, obj: dict) -> None:
        self.send_raw((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))

    def request(self, argv: list[str]) -> dict:
        self._next += 1
        self.send({"id": self._next, "argv": argv})
        response = self.read()
        assert response.get("id") == self._next, response
        return response

    def run(self, argv: list[str]) -> tuple[int, str]:
        response = self.request(argv)
        assert not response.get("refused"), response
        return response["code"], response["out"]

    def ping(self) -> bool:
        self._next += 1
        self.send({"id": self._next, "op": "ping"})
        return self.read() == {"id": self._next, "ok": True}

    def err(self) -> str:
        return b"".join(self.stderr).decode("utf-8", "replace")

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        for stream in (self.proc.stdout, self.proc.stderr):
            if stream is not None:
                stream.close()


@pytest.fixture
def start_server():
    started: list[Server] = []

    def _start(env: dict[str, str], *args: str, script: Path | None = None) -> Server:
        server = Server(env, *args, script=script)
        started.append(server)
        assert server.ready.get("ready") is True, (server.ready, server.err())
        return server

    yield _start
    for server in started:
        server.close()


def prepare(vault: Path, *, jira_url: str | None = None) -> None:
    jira: dict = {"project_keys": ["KB", "OPS"]}
    if jira_url:
        kb_secrets.store("work-kb", "jira", TOKEN)
        jira.update(base_url=jira_url, pat_ref="keyring:work-kb/jira", trust_env=False)
    config.save_local(vault, {"environment": "home", "jira": jira})
    (vault / "daily" / "2026-11-04.md").write_bytes(YESTERDAY.encode("utf-8"))
    (vault / "inbox" / "20261104-0930 메모.md").write_bytes("캐시 만료 정책 메모 😀\n".encode("utf-8"))


# 실행마다 달라지는 파일: 잠금 소유자(pid·시각), link 캐시(방금 쓴 파일의 mtime). 있는지만 비교한다.
VOLATILE = {".state/kb.lock", ".state/links_cache.json"}


def tree(root: Path) -> dict[str, bytes]:
    """볼트의 모든 파일(로그 제외) → 바이트."""
    out: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_file() and not rel.startswith(".state/logs/"):
            out[rel] = b"(volatile)" if rel in VOLATILE else path.read_bytes()
    return out


def restore(root: Path, snapshot: Path) -> None:
    shutil.rmtree(root)  # 서버가 볼트 파일을 잡고 있으면 Windows에서 여기서 실패한다
    shutil.copytree(snapshot, root)


_ELAPSED = re.compile(r"\d+\.\d\d초")


def _norm(text: str) -> str:
    return _ELAPSED.sub("N초", text)


# ---------------------------------------------------------------- 시작·ping·종료


def test_ready_line_and_ping(vault, tmp_path, fake_now, start_server):
    fake_now(NOW)
    server = start_server(child_env(tmp_path, vault))
    # venv의 python.exe는 진짜 인터프리터를 띄우는 실행기라 Popen.pid와 다를 수 있다
    assert set(server.ready) == {"ready", "pid", "version"}
    assert server.ready["version"] == 1 and isinstance(server.ready["pid"], int) and server.ready["pid"] > 0
    assert server.ping()
    server.send({"op": "shutdown"})
    assert server.proc.wait(timeout=15) == 0
    assert server.read_raw() is None  # 종료 요청에는 응답 줄이 없다


def test_eof_exits_zero(vault, tmp_path, start_server):
    server = start_server(child_env(tmp_path, vault))
    server.proc.stdin.close()
    assert server.proc.wait(timeout=15) == 0
    assert server.read_raw() is None


def test_idle_timeout_exits_by_itself(vault, tmp_path, start_server):
    server = start_server(child_env(tmp_path, vault), "--idle-sec", "1")
    assert server.ping()  # 요청이 오면 시계가 다시 간다
    pinged = time.monotonic()
    assert server.proc.wait(timeout=20) == 0
    assert time.monotonic() - pinged >= 0.9


def test_bad_idle_sec_is_usage_error(vault, run_kb):
    result = run_kb("serve", "--idle-sec", "0")
    assert result.returncode == 4
    assert "--idle-sec" in result.stderr
    assert result.stdout == ""


# ---------------------------------------------------------------- 같은 결과 (one-shot과 비교)


def _parity_cases(v: str) -> list[tuple[list[str], dict]]:
    return [
        (["status", "--root", v], {}),
        (["status", "--json", "--root", v], {}),
        (["search", "캐시 😀", "--root", v], {}),
        (["search", "캐시", "--json", "--root", v], {}),
        (["search", "없는낱말", "--root", v], {}),
        (["today", "--date", "2026-11-05", "--root", v], {}),
        (["today", "--root", v], {}),
        (["today", "--check-done", "--root", v], {}),
        (["link", "--stub", "KB-99", "--root", v], {}),
        (["link", "--root", v], {"norm": _norm}),
        (["act", "list", "--root", v], {}),
        (["act", "list", "--json", "--all", "--root", v], {}),
        (["proposals", "list", "--root", v], {}),
        (["status", "--help"], {}),
        (["search", "--root", v], {"code": 4}),  # 빈 질의
        (["status", "--bogus", "--root", v], {"code": 4}),  # argparse 사용법 오류
        (["link", "--stub", "NOPE-1", "--root", v], {"code": 4}),
        (["today", "--date", "어제", "--root", v], {"code": 4}),
        (["nosuch😀", "한글 인수"], {"code": 4}),  # 알 수 없는 명령
        ([], {"code": 4}),
        (["--help"], {"code": 0}),
    ]


def test_served_commands_match_oneshot(vault, tmp_path, fake_now, start_server):
    fake_now(NOW)
    prepare(vault)
    env = child_env(tmp_path, vault)
    server = start_server(env)
    snapshot = tmp_path / "snapshot"
    shutil.copytree(vault, snapshot)
    problems: list[str] = []
    for argv, opts in _parity_cases(str(vault)):
        norm = opts.get("norm", lambda s: s)
        restore(vault, snapshot)
        one_code, one_out = oneshot(env, argv)
        one_tree = tree(vault)
        restore(vault, snapshot)
        buf_code, buf_out = oneshot(env, argv, unbuffered=False)
        restore(vault, snapshot)
        code, out = server.run(argv)
        srv_tree = tree(vault)
        if "code" in opts and code != opts["code"]:
            problems.append(f"{argv}: 종료 {code}, 기대 {opts['code']}\n{out}")
        if (code, norm(out)) != (one_code, norm(one_out)):
            problems.append(f"{argv}: 서버 ({code}) {out!r}\n  한 번 실행 ({one_code}) {one_out!r}")
        if (buf_code, norm(buf_out)) != (one_code, norm(one_out)):
            problems.append(f"{argv}: 버퍼 있는 한 번 실행이 다름 ({buf_code}) {buf_out!r}")
        if srv_tree != one_tree:
            changed = sorted(k for k in set(srv_tree) | set(one_tree) if srv_tree.get(k) != one_tree.get(k))
            problems.append(f"{argv}: 볼트 결과가 다름: {changed}")
    assert server.proc.poll() is None
    assert problems == [], "\n".join(problems)


def test_mixed_stdout_stderr_keeps_write_order(vault, tmp_path, fake_now, start_server):
    """stdout·stderr를 둘 다 쓰는 명령: 서버는 쓴 순서, 버퍼 없는 한 번 실행과 같다."""
    fake_now(NOW)
    prepare(vault)
    (vault / "daily" / "2026-11-03.md").write_bytes("- 캐시 깨진 파일 ".encode("utf-8") + b"\xff\xfe\n")
    env = child_env(tmp_path, vault)
    server = start_server(env)
    snapshot = tmp_path / "snapshot"
    shutil.copytree(vault, snapshot)
    argv = ["link", "--root", str(vault)]  # 요약(stdout) 다음에 건너뜀(stderr)
    code, out = server.run(argv)
    lines = out.splitlines()
    assert code == 0 and lines[0].startswith("link: 원천") and lines[-1].startswith("건너뜀: daily/2026-11-03.md"), out
    restore(vault, snapshot)
    assert (code, _norm(out)) == tuple(map(lambda x: _norm(x) if isinstance(x, str) else x, oneshot(env, argv)))
    restore(vault, snapshot)
    buffered_code, buffered = oneshot(env, argv, unbuffered=False)
    # 버퍼 있는 한 번 실행은 stdout이 끝날 때 나가므로 stderr 줄이 먼저 올 수 있다. 줄 모음은 같다.
    assert buffered_code == code and sorted(_norm(buffered).splitlines()) == sorted(_norm(out).splitlines())


def test_act_transitions_parity_reads_only(vault, tmp_path, fake_now, start_server, mock_jira):
    fake_now(NOW)
    prepare(vault, jira_url=mock_jira.base_url)
    env = child_env(tmp_path, vault)
    server = start_server(env)
    for argv in (["act", "transitions", "KB-12", "--root", str(vault)],
                 ["act", "transitions", "KB-12", "--json", "--root", str(vault)]):  # fmt: skip
        code, out = server.run(argv)
        assert (code, out) == oneshot(env, argv)
        assert code == 0 and "In Review" in out
    assert mock_jira.log and all(entry["method"] == "GET" for entry in mock_jira.log)
    assert not outbox.path_for(vault).exists()


# ---------------------------------------------------------------- 거부


def test_refused_commands_do_not_run(vault, tmp_path, fake_now, start_server, mock_jira):
    fake_now(NOW)
    prepare(vault, jira_url=mock_jira.base_url)
    before = tree(vault)
    server = start_server(child_env(tmp_path, vault))
    v = str(vault)
    for argv in (
        ["sync", "--root", v],
        ["jira_sync", "--root", v],
        ["act", "comment", "KB-12", "--body", "보내면 안 됨", "--root", v],
        ["act", "flush", "--root", v],
        ["act"],
        ["compile", "--root", v],
        ["push", "--root", v],
        ["proposals", "accept", "1", "--root", v],
        ["proposals"],
        ["init"],
        ["doctor", "--root", v],
        ["serve"],
    ):
        response = server.request(argv)
        assert response["refused"] is True and response["reason"], (argv, response)
        assert "code" not in response and "out" not in response
    assert mock_jira.log == []
    assert not outbox.path_for(vault).exists()
    assert tree(vault) == before
    assert server.ping()


def test_served_set_is_agent_allowed():
    """서버가 실행하는 명령은 모두 KB_AGENT=1에서도 허용된다(거부 7이 나올 하위 명령이 서버 목록에 없다)."""
    assert kb.COMMANDS["serve"].agent_ok and kb.COMMANDS["serve"].module == "serve"
    for name, subs in serve.SERVED.items():
        command = kb.COMMANDS[name]
        if subs is None:
            assert command.agent_ok, name
        else:
            assert all(command.agent_ok or sub in command.agent_ok_sub for sub in subs), name


def test_agent_session_parity(vault, tmp_path, fake_now, start_server):
    fake_now(NOW)
    prepare(vault)
    env = child_env(tmp_path, vault, {"KB_AGENT": "1"})
    server = start_server(env)
    v = str(vault)
    for argv in (["act", "list", "--root", v], ["proposals", "list", "--root", v], ["status", "--root", v]):
        code, out = server.run(argv)
        assert (code, out) == oneshot(env, argv)
        assert code == 0
    assert "KB_AGENT=1" in server.run(["status", "--root", v])[1]
    # 게이트에 걸리는 것은 서버가 실행하지 않는다 → 클라이언트의 한 번 실행이 종료 7을 낸다
    argv = ["act", "comment", "KB-12", "--body", "x", "--root", v]
    assert server.request(argv)["refused"] is True
    assert oneshot(env, argv)[0] == 7


def test_malformed_requests_are_refused_and_server_survives(vault, tmp_path, start_server):
    server = start_server(child_env(tmp_path, vault))
    bad = [
        b"{not json\n",
        b"[1, 2]\n",
        b"\xff\xfe\n",
        b'{"id": "x", "argv": ["status"]}\n',
        b'{"id": 1, "argv": "status"}\n',
        b'{"id": 2, "argv": [1]}\n',
        b'{"argv": ["status"]}\n',
        b'{"id": 3, "op": "nope"}\n',
        b'{"id": 4}\n',
    ]
    for data in bad:
        server.send_raw(data)
        response = server.read()
        assert response["refused"] is True and response["reason"], (data, response)
        assert "code" not in response
    server.send_raw(b"\n  \n")  # 빈 줄은 무시(응답 없음)
    server.send_raw(b'{"id": 77, "op": "ping"}\r\n')
    assert server.read() == {"id": 77, "ok": True}
    assert server.ping()


# ---------------------------------------------------------------- 요청 안의 오류·누수


TEST_CMD = '''\
import logging, os, sys
from pathlib import Path
from lib import core
LEAKED = []
if os.environ.get("WORKKB_TEST_NOISE") == "1":
    print("가져올 때 찍는 소리")
    sys.stderr.write("가져올 때 stderr\\n")
    os.write(1, b"fd1 noise at import\\n")

def main(argv):
    root = argv[argv.index("--root") + 1] if "--root" in argv else "."
    if "--boom" in argv:
        print("터지기 전 출력")
        raise RuntimeError("일부러 낸 오류 😀")
    if "--interrupt" in argv:
        raise KeyboardInterrupt
    if "--exit-str" in argv:
        raise SystemExit("문자열 종료 😀")
    if "--exit-none" in argv:
        sys.exit(None)
    if "--exit-9" in argv:
        sys.exit(9)
    if "--fd1" in argv:
        os.write(1, b"fd1 garbage\\n")
        print("보이는 출력")
        return 0
    if "--surrogate" in argv:
        print("깨진 \\udcff 문자")
        return 0
    if "--input" in argv:
        try:
            answer = input("질문? ")
        except EOFError:
            print("EOF")
            return 3
        print("답:", answer)
        return 0
    if "--leak" in argv:
        os.environ["WORKKB_LEAK"] = "1"
        os.chdir(Path(root).parent)
        core.get_logger(root).info("로그 한 줄")
        with core.kb_lock(root, "잠금 시험"):
            pass
        return 0
    if "--leak-lock" in argv:
        cm = core.kb_lock(root, "누수 시험")
        cm.__enter__()
        LEAKED.append(cm)
        print("잠금을 쥔 채 끝냄")
        return 0
    if "--check" in argv:
        handlers = len(logging.getLogger("work-kb.kb").handlers)
        print(f"env={os.environ.get('WORKKB_LEAK')} cwd={os.getcwd()} held={len(core._HELD)} handlers={handlers}")
        return 0
    if "--pid" in argv:
        print(f"pid={os.getpid()}")
        argv = [a for a in argv if a != "--pid"]
    import status
    return status.main(argv)
'''

WRAPPER = '''\
import sys
sys.path.insert(0, {scripts!r})
sys.path.insert(0, {here!r})
import kb
from lib import core
kb.COMMANDS["status"] = kb.Command("kb_test_cmd", help="시험용")
kb._preflight()
core.run_main(kb.main)
'''


@pytest.fixture
def wrapper(tmp_path) -> Path:
    here = tmp_path / "wrapper"
    here.mkdir()
    (here / "kb_test_cmd.py").write_text(TEST_CMD, encoding="utf-8")
    script = here / "kb_wrapper.py"
    script.write_text(WRAPPER.format(scripts=str(SCRIPTS), here=str(here)), encoding="utf-8")
    return script


def test_exceptions_inside_a_request(vault, tmp_path, fake_now, start_server, wrapper):
    fake_now(NOW)
    prepare(vault)
    env = child_env(tmp_path, vault)
    server = start_server(env, script=wrapper)
    v = str(vault)

    code, out = server.run(["status", "--boom", "--root", v])
    assert code == 1
    assert out.startswith("터지기 전 출력\nTraceback (most recent call last):\n")
    assert out.rstrip("\n").endswith("RuntimeError: 일부러 낸 오류 😀")
    one_code, one_out = oneshot(env, ["status", "--boom", "--root", v], script=wrapper)
    assert one_code == 1 and one_out.rstrip("\n").endswith("RuntimeError: 일부러 낸 오류 😀")

    for flag, expected in (("--interrupt", 1), ("--exit-str", 1), ("--exit-none", 0), ("--exit-9", 9),
                           ("--surrogate", 0), ("--input", 3)):  # fmt: skip
        argv = ["status", flag, "--root", v]
        result = server.run(argv)
        assert result == oneshot(env, argv, script=wrapper), flag
        assert result[0] == expected, (flag, result)

    code, out = server.run(["status", "--root", v])  # 다음 요청은 정상
    assert code == 0 and "환경: home" in out
    assert server.proc.poll() is None


def test_stdout_stays_clean(vault, tmp_path, start_server, wrapper):
    """가져올 때 찍는 출력, fd 1에 직접 쓰는 출력도 프로토콜 줄에 섞이지 않는다."""
    env = child_env(tmp_path, vault, {"WORKKB_TEST_NOISE": "1"})
    server = start_server(env, script=wrapper)  # 준비 줄이 첫 줄이고 JSON이다(start_server가 확인)
    code, out = server.run(["status", "--fd1", "--root", str(vault)])
    assert (code, out) == (0, "보이는 출력\n")
    assert server.ping()
    assert "가져올 때 찍는 소리" in server.err()


def test_nothing_leaks_between_requests(vault, tmp_path, start_server, wrapper):
    from lib import core

    server = start_server(child_env(tmp_path, vault), script=wrapper)
    v = str(vault)
    cwd_before = server.run(["status", "--check", "--root", v])[1]
    assert server.run(["status", "--leak", "--root", v]) == (0, "")
    assert server.run(["status", "--check", "--root", v])[1] == cwd_before
    assert "env=None" in cwd_before and "held=0" in cwd_before and "handlers=0" in cwd_before
    assert (vault / ".state" / "logs" / "kb.log").read_text(encoding="utf-8").count("로그 한 줄") == 1
    with core.kb_lock(vault, "다른 프로세스", timeout=2):  # 서버가 잠금을 쥐고 있지 않다
        pass
    shutil.rmtree(vault)  # 로그·잠금 파일도 닫혀 있다
    assert server.ping()


def test_leaked_lock_ends_the_server(vault, tmp_path, start_server, wrapper):
    """요청이 kb_lock을 풀지 않고 끝나면 응답을 보낸 뒤 서버가 끝나 잠금이 풀린다(요청 사이에 잠금을 쥐지 않는다)."""
    from lib import core

    server = start_server(child_env(tmp_path, vault), script=wrapper)
    assert server.run(["status", "--leak-lock", "--root", str(vault)]) == (0, "잠금을 쥔 채 끝냄\n")
    assert server.proc.wait(timeout=15) == 0
    with core.kb_lock(vault, "다른 프로세스", timeout=2):
        pass


def test_two_vaults_alternate_without_cross_talk(vault, tmp_path, fake_now, start_server):
    fake_now(NOW)
    other = tmp_path / "두 번째 볼트 (B)" / "work-kb"
    shutil.copytree(vault, other)
    for root, word in ((vault, "사과"), (other, "포도")):
        config.save_local(root, {"environment": "home", "jira": {"project_keys": ["KB"] if word == "사과" else ["OPS"]}})
        (root / "daily" / "2026-11-04.md").write_bytes(f"## 기록\n- 10:00 {word} 회의 [[KB-1]]\n".encode("utf-8"))
    env = child_env(tmp_path, vault)
    server = start_server(env)
    for _ in range(3):
        for root, mine, theirs in ((vault, "사과", "포도"), (other, "포도", "사과")):
            r = str(root)
            assert server.run(["search", mine, "--root", r]) == (0, f"daily/2026-11-04.md:2: - 10:00 {mine} 회의 [[KB-1]]\n")
            assert server.run(["search", theirs, "--root", r]) == (0, "결과 없음\n")
            status = server.run(["status", "--root", r])
            assert status == oneshot(env, ["status", "--root", r])
            assert f"볼트: {root}" in status[1]
            code, out = server.run(["link", "--stub", "OPS-5", "--root", r])
            assert code == (0 if root == other else 4), out  # 볼트마다 자기 config의 project_keys
    assert (other / "jira" / "OPS-5.md").exists() and not (vault / "jira" / "OPS-5.md").exists()


# ---------------------------------------------------------------- 속도


def test_twenty_requests_one_process_fast(vault, tmp_path, fake_now, start_server, wrapper):
    """실제 status를 20번(시험 명령이 앞에 pid만 찍는다). 같은 프로세스가 답하고 왕복 중앙값이 60 ms 미만."""
    fake_now(NOW)
    prepare(vault)
    server = start_server(child_env(tmp_path, vault), script=wrapper)
    pid = server.ready["pid"]
    times: list[float] = []
    for _ in range(20):
        started = time.perf_counter()
        code, out = server.run(["status", "--pid", "--root", str(vault)])
        times.append((time.perf_counter() - started) * 1000)
        assert code == 0 and "볼트:" in out
        assert out.startswith(f"pid={pid}\n"), out
        assert server.proc.poll() is None
    median = statistics.median(times)
    print(f"\nkb.py serve status 왕복 중앙값 {median:.1f} ms (최소 {min(times):.1f}, 최대 {max(times):.1f}), pid {pid}")
    assert median < 60, times  # 넉넉한 한계: 한 번 실행은 이 PC에서 약 235 ms
