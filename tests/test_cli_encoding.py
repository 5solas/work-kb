"""CLI 인코딩과 하위 프로세스 격리.

- UTF-8 모드가 아닌 하위 프로세스(Windows cp949 로캘)에서도 kb.py의 출력은 UTF-8이어야 한다(D05).
  core.setup_stdio가 하는 일이다. 출력은 바이트로 받아 엄격하게 UTF-8로 해독한다.
- 테스트의 하위 Python은 tests/sitecustomize.py 덕분에 루프백 밖으로 접속하지 못한다.
- 테스트 프로세스 안에서는 환경·레지스트리 프록시를 쓰지 않는다.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TESTS = ROOT / "tests"


def _strict(data: bytes) -> str:
    assert isinstance(data, bytes)
    return data.decode("utf-8")  # errors='strict': cp949 바이트면 여기서 실패한다


def _plain_env(**extra: str) -> dict[str, str]:
    """UTF-8 모드를 강제하지 않는 하위 프로세스 환경."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING") and not k.startswith("KB_")}
    env.update(extra)
    return env


# ---------------------------------------------------------------- kb.py 출력 (utf8=False)


def test_usage_is_utf8_without_utf8_mode(run_kb):
    result = run_kb("--help", utf8=False)
    assert result.returncode == 0, result.stderr
    assert "사용법" in _strict(result.stdout)
    assert "연결 설정 마법사" in _strict(result.stdout)


def test_unknown_command_error_is_utf8_without_utf8_mode(run_kb):
    result = run_kb("nosuch", utf8=False)
    assert result.returncode == 4
    err = _strict(result.stderr)
    assert "알 수 없는 명령: nosuch" in err
    assert "사용법" in err


def test_argv_stdin_raw_utf8_without_utf8_mode(run_kb):
    raw = json.dumps(["없는명령😀", "한글 인수"], ensure_ascii=False).encode("utf-8")
    result = run_kb("--argv-stdin", stdin=raw, utf8=False)
    assert result.returncode == 4
    assert "알 수 없는 명령: 없는명령😀" in _strict(result.stderr)


def test_run_kb_utf8_false_drops_utf8_mode(run_kb, monkeypatch):
    """utf8=False가 하위 프로세스의 UTF-8 모드를 정말 끄는지(위 테스트들이 헛되이 초록이 되지 않게)."""
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"], seen["kwargs"] = command, kwargs
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setenv("PYTHONUTF8", "1")
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    monkeypatch.setenv("KB_FAKE_NOW", "2026-11-05T09:12:00")
    monkeypatch.setattr(subprocess, "run", fake_run)
    run_kb("--argv-stdin", stdin="[\"한글\"]", utf8=False)
    command, kwargs = seen["command"], seen["kwargs"]
    assert "-X" not in command and "utf8" not in command
    assert "PYTHONUTF8" not in kwargs["env"] and "PYTHONIOENCODING" not in kwargs["env"]
    assert kwargs["env"]["KB_FAKE_NOW"] == "2026-11-05T09:12:00"
    assert not kwargs.get("text") and "encoding" not in kwargs
    assert kwargs["input"] == '["한글"]'.encode("utf-8")
    run_kb("--help")
    assert seen["command"][1:3] == ["-X", "utf8"]
    assert seen["kwargs"]["env"]["PYTHONUTF8"] == "1"


STDIO_CHILD = (
    "import sys; sys.path.insert(0, sys.argv[1]); from lib import core; core.setup_stdio(); "
    "print('✓ 한글 😀'); print('✗ 실패', file=sys.stderr)"
)


def test_setup_stdio_makes_check_marks_utf8():
    result = subprocess.run(
        [sys.executable, "-c", STDIO_CHILD, str(SCRIPTS)],
        capture_output=True,
        env=_plain_env(),
        cwd=str(ROOT),
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert _strict(result.stdout).splitlines() == ["✓ 한글 😀"]
    assert _strict(result.stderr).splitlines() == ["✗ 실패"]


# ---------------------------------------------------------------- 하위 프로세스 소켓 차단 (tests/sitecustomize.py)

CONNECT_CHILD = r"""
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(1)
try:
    s.connect((sys.argv[1], int(sys.argv[2])))
    print("connected")
except RuntimeError as exc:
    print("blocked", exc)
except OSError as exc:
    print("attempted", type(exc).__name__)
finally:
    s.close()
print("guard", bool(getattr(socket.socket.connect, "kb_network_guard", False)))
"""


def _connect_child(host: str, port: int, **extra: str) -> list[str]:
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", CONNECT_CHILD, host, str(port)],
        capture_output=True,
        env=_plain_env(PYTHONPATH=str(TESTS), **extra),
        cwd=str(ROOT),
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return _strict(result.stdout).splitlines()


def test_child_process_blocks_non_loopback():
    lines = _connect_child("203.0.113.7", 9)  # TEST-NET-3, 실제로 시도되면 안 된다
    assert lines[0].startswith("blocked") and "외부 접속 차단" in lines[0]
    assert lines[1] == "guard True"


def test_child_process_allows_loopback():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        assert _connect_child("127.0.0.1", server.getsockname()[1]) == ["connected", "guard True"]
    finally:
        server.close()


def test_child_process_guard_off_with_kb_allow_network():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        lines = _connect_child("127.0.0.1", server.getsockname()[1], KB_ALLOW_NETWORK="1")
    finally:
        server.close()
    assert lines == ["connected", "guard False"]


# ---------------------------------------------------------------- 테스트 프로세스 안 격리 (conftest._isolate)


def test_in_process_connect_ex_is_guarded():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(RuntimeError, match="외부 접속 차단"):
            s.connect_ex(("203.0.113.7", 9))
    finally:
        s.close()


def test_in_process_proxies_are_ignored(monkeypatch):
    import urllib.request

    import requests

    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        assert key not in os.environ
    # 테스트 중 누가 프록시를 설정해도(또는 Windows 레지스트리에 시스템 프록시가 있어도) 쓰지 않는다
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    assert urllib.request.getproxies() == {}
    settings = requests.Session().merge_environment_settings("https://example.invalid/", {}, None, None, None)
    assert settings["proxies"] == {}
