"""pytest 공통 설정.

- `scripts/`를 sys.path에 넣어 `from lib import core`가 되게 한다(`scripts/lib`는 넣지 않는다).
- 모든 테스트에서: DEEPINFRA_API_KEY·KB_* 제거, keyring은 파일 기반 가짜, 소켓은 루프백만,
  프록시(환경변수·Windows 레지스트리)는 쓰지 않는다.
- 하위 프로세스(run_kb)는 tests/sitecustomize.py가 같은 루프백 전용 소켓 감시를 건다.
- `live` 마커 테스트는 실제 네트워크와 키를 쓴다.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
import requests.compat  # 패치 전에 가져온다: 안에서 처음 가져오면 가짜 getproxies가 영구히 묶인다
import requests.utils

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TESTS = ROOT / "tests"
FIXTURES = TESTS / "fixtures"

for entry in (str(SCRIPTS), str(TESTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


def _load_net_guard():
    """tests/sitecustomize.py를 다른 이름으로 읽는다(테스트 프로세스에서 자동 설치되지 않게)."""
    spec = importlib.util.spec_from_file_location("_kb_net_guard", TESTS / "sitecustomize.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_NET_GUARD = _load_net_guard()


def _no_proxies() -> dict[str, str]:
    return {}


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
        monkeypatch.setattr(socket.socket, "connect", _NET_GUARD.guard(socket.socket.connect))
        monkeypatch.setattr(socket.socket, "connect_ex", _NET_GUARD.guard(socket.socket.connect_ex))
        # 루프백의 시스템 프록시(Clash·Fiddler 등)를 거쳐 밖으로 나가지 않게 프록시를 끈다.
        for key in _PROXY_KEYS:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr(urllib.request, "getproxies", _no_proxies)
        monkeypatch.setattr(requests.utils, "getproxies", _no_proxies)  # from urllib.request import로 묶인 이름
        monkeypatch.setattr(requests.compat, "getproxies", _no_proxies)

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
    """`python scripts/kb.py …`를 하위 프로세스로 실행한다. 외부 네트워크·실제 keyring은 막혀 있다.

    utf8=True(기본): `-X utf8`·PYTHONUTF8=1로 실행하고 출력은 str.
    utf8=False: UTF-8 모드 없이(Windows면 cp949 로캘) 실행하고 stdout·stderr를 bytes로 돌려준다.
    core.setup_stdio가 제 일을 하는지 테스트가 엄격한 UTF-8 해독으로 확인한다. stdin은 str이면 UTF-8로 보낸다.
    """

    def _run(
        *args: str,
        env: dict[str, str] | None = None,
        stdin: str | bytes | None = None,
        root: Path | None = None,
        timeout: int = 120,
        utf8: bool = True,
    ):
        drop = {"DEEPINFRA_API_KEY", "PYTHONUTF8", "PYTHONIOENCODING", *_PROXY_KEYS}
        child_env = {k: v for k, v in os.environ.items() if not k.startswith("KB_") and k not in drop}
        child_env.update(
            {
                "PYTHONPATH": str(TESTS),  # tests/sitecustomize.py: 루프백 밖 접속 차단
                "PYTHON_KEYRING_BACKEND": "fake_keyring.FileKeyring",
                "KB_FAKE_KEYRING_FILE": str(tmp_path / "keyring.json"),
                "HTTP_PROXY": "http://127.0.0.1:9",
                "HTTPS_PROXY": "http://127.0.0.1:9",
                "NO_PROXY": "127.0.0.1,localhost",
                "KB_ROOT": str(root or vault),
            }
        )
        if utf8:
            child_env["PYTHONUTF8"] = "1"
        if "KB_FAKE_NOW" in os.environ:
            child_env["KB_FAKE_NOW"] = os.environ["KB_FAKE_NOW"]
        if env:
            child_env.update(env)
        command = [sys.executable, *(("-X", "utf8") if utf8 else ()), str(SCRIPTS / "kb.py"), *args]
        if utf8:
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=child_env,
                cwd=str(ROOT),
                input=stdin.decode("utf-8") if isinstance(stdin, bytes) else stdin,
                timeout=timeout,
            )
        return subprocess.run(
            command,
            capture_output=True,
            env=child_env,
            cwd=str(ROOT),
            input=stdin.encode("utf-8") if isinstance(stdin, str) else stdin,
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
