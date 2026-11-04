"""pytest 공통 설정.

- `scripts/`를 sys.path에 넣어 `from lib import core`가 되게 한다(`scripts/lib`는 넣지 않는다).
- 모든 테스트에서: DEEPINFRA_API_KEY·KB_* 제거, keyring은 파일 기반 가짜, 소켓은 루프백만.
- `live` 마커 테스트는 실제 네트워크와 키를 쓴다.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TESTS = ROOT / "tests"
FIXTURES = TESTS / "fixtures"

for entry in (str(SCRIPTS), str(TESTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def _isolate(request, monkeypatch, tmp_path):
    """환경변수·keyring·네트워크를 격리한다."""
    live = request.node.get_closest_marker("live") is not None
    if not live:
        monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)
    for key in list(os.environ):
        if key.startswith("KB_"):
            monkeypatch.delenv(key, raising=False)

    import keyring

    import fake_keyring

    previous = keyring.get_keyring()
    fake = fake_keyring.FileKeyring(tmp_path / "keyring.json")
    keyring.set_keyring(fake)

    if not live:
        real_connect = socket.socket.connect

        def guarded_connect(self, address):
            host = address[0] if isinstance(address, tuple) and address else None
            if host is not None and host not in _LOOPBACK:
                raise RuntimeError(f"테스트에서 외부 접속 차단: {address!r}")
            return real_connect(self, address)

        monkeypatch.setattr(socket.socket, "connect", guarded_connect)

    yield
    keyring.set_keyring(previous)


@pytest.fixture
def vault(tmp_path) -> Path:
    """한글·공백 경로 아래의 빈 볼트. 저장소의 config.yaml이 있으면 복사한다."""
    root = tmp_path / "한글 경로 (테스트)" / "work-kb"
    for sub in ("daily", "inbox", "raw/assets", "jira/_views", "wiki/projects", "wiki/people", "wiki/topics", "_trash", ".state"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    config = ROOT / "config.yaml"
    if config.exists():
        shutil.copy(config, root / "config.yaml")
    return root


@pytest.fixture
def fake_now(monkeypatch):
    """`fake_now("2026-11-05T09:12:00")`로 core.now를 고정한다. 하위 프로세스에도 전달된다."""

    def _set(value: str) -> None:
        monkeypatch.setenv("KB_FAKE_NOW", value)

    return _set


@pytest.fixture
def run_kb(vault, tmp_path):
    """`python scripts/kb.py …`를 하위 프로세스로 실행한다. 외부 네트워크·실제 keyring은 막혀 있다."""

    def _run(*args: str, env: dict[str, str] | None = None, stdin: str | None = None, root: Path | None = None, timeout: int = 120):
        child_env = {k: v for k, v in os.environ.items() if not k.startswith("KB_") and k != "DEEPINFRA_API_KEY"}
        child_env.update(
            {
                "PYTHONUTF8": "1",
                "PYTHONPATH": str(TESTS),
                "PYTHON_KEYRING_BACKEND": "fake_keyring.FileKeyring",
                "KB_FAKE_KEYRING_FILE": str(tmp_path / "keyring.json"),
                "HTTP_PROXY": "http://127.0.0.1:9",
                "HTTPS_PROXY": "http://127.0.0.1:9",
                "NO_PROXY": "127.0.0.1,localhost",
                "KB_ROOT": str(root or vault),
            }
        )
        if "KB_FAKE_NOW" in os.environ:
            child_env["KB_FAKE_NOW"] = os.environ["KB_FAKE_NOW"]
        if env:
            child_env.update(env)
        return subprocess.run(
            [sys.executable, "-X", "utf8", str(SCRIPTS / "kb.py"), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env,
            cwd=str(ROOT),
            input=stdin,
            timeout=timeout,
        )

    return _run


def _mock_fixture(module_name: str):
    @pytest.fixture
    def _fixture():
        module = __import__(module_name)
        server = module.start()
        try:
            yield server
        finally:
            server.stop()

    return _fixture


mock_jira = _mock_fixture("mock_jira")
mock_confluence = _mock_fixture("mock_confluence")
mock_llm = _mock_fixture("mock_llm")
