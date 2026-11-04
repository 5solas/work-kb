"""`kb.py doctor` (D32 세 상태)와 lib/net 연결 계층."""
from __future__ import annotations

import json
import re
import socket
from pathlib import Path

import pytest
import requests
import yaml
from requests import exceptions as rex
from urllib3 import exceptions as u3

import mock_llm
from lib import config, core, net
from lib import secrets as kb_secrets

ROOT = Path(__file__).resolve().parents[1]
LINE = re.compile(r"^([✓✗-]) ([^:]+): (.*)$")
SUMMARY = re.compile(r"^✓ (\d+) ✗ (\d+) - (\d+)$")


# ---------------------------------------------------------------- 도우미


def _lines(stdout: str) -> list[tuple[str, str, str]]:
    return [m.groups() for m in map(LINE.match, stdout.splitlines()) if m]


def _state(stdout: str, name: str) -> tuple[str, str]:
    found = [(s, t) for s, n, t in _lines(stdout) if n == name]
    assert found, f"'{name}' 줄이 없습니다:\n{stdout}"
    return found[0]


def _summary(stdout: str) -> tuple[int, int, int]:
    last = [line for line in stdout.splitlines() if line.strip()][-1]
    m = SUMMARY.match(last)
    assert m, last
    return tuple(int(x) for x in m.groups())


def _store_pats() -> None:
    kb_secrets.store("work-kb", "jira", "mock-token")
    kb_secrets.store("work-kb", "confluence", "mock-token")


def _mock_config(jira, confluence, llm, *, environment="home", profiles=None, enabled=True) -> dict:
    return {
        "environment": environment,
        "jira": {"base_url": jira.base_url, "project_keys": ["KB", "OPS"], "pat_ref": "keyring:work-kb/jira"},
        "confluence": {
            "enabled": True,
            "base_url": confluence.base_url,
            "space_key": "~honggildong",
            "parent_page_id": "",
            "pat_ref": "keyring:work-kb/confluence",
        },
        "llm": {
            "enabled": enabled,
            "profiles": profiles if profiles is not None else {"main": {"base_url": f"{llm.base_url}/v1", "model": "mock"}},
            "fallback_order": ["main"],
        },
    }


def _example_main_url() -> str:
    data = yaml.safe_load((ROOT / "config.local.example.yaml").read_text(encoding="utf-8"))
    return data["home"]["llm"]["profiles"]["main"]["base_url"]


# ---------------------------------------------------------------- doctor (하위 프로세스)


def test_after_init_dev(run_kb, mock_jira, mock_confluence, mock_llm):
    env = {
        "KB_INIT_JIRA_URL": mock_jira.base_url,
        "KB_INIT_CONFLUENCE_URL": mock_confluence.base_url,
        "KB_INIT_LLM_URL": f"{mock_llm.base_url}/v1",
    }
    assert run_kb("init", "--dev", env=env).returncode in (0, 1)
    result = run_kb("doctor")
    jira = _state(result.stdout, "Jira")
    assert jira[0] == "✓" and "홍길동" in jira[1]
    conf = _state(result.stdout, "Confluence")
    assert conf[0] == "✓" and "honggildong" in conf[1]
    assert _state(result.stdout, "LLM mock") == ("✓", f"mock 목록에 있음 ({mock_llm.base_url}/v1)")
    # 하위 프로세스에는 키도 외부망도 없다
    assert _state(result.stdout, "LLM main")[0] in ("✗", "-")
    assert _state(result.stdout, "탐지 mock")[1].startswith("탐지 안 됨")
    assert result.returncode == 1


def test_all_mock_profiles_exit_0_then_jira_down(run_kb, vault, mock_jira, mock_confluence, mock_llm):
    _store_pats()
    config.save_local(vault, _mock_config(mock_jira, mock_confluence, mock_llm))
    result = run_kb("doctor")
    assert result.returncode == 0, result.stdout + result.stderr
    ok, bad, info = _summary(result.stdout)
    assert bad == 0 and ok >= 8 and info >= 1
    assert _state(result.stdout, "LLM main")[0] == "✓"
    assert any(e["has_auth"] for e in mock_jira.log)

    mock_jira.stop()
    result = run_kb("doctor")
    state, text = _state(result.stdout, "Jira")
    assert state == "✗" and "→" in text
    assert _summary(result.stdout)[1] == 1
    assert result.returncode == 1


def test_company_public_host_exit_5(run_kb, vault, mock_jira, mock_confluence, mock_llm):
    _store_pats()
    kb_secrets.store("work-kb", "llm", "dummy-llm-key")
    profiles = {"main": {"base_url": _example_main_url(), "model": "x", "api_key_ref": "keyring:work-kb/llm"}}
    config.save_local(vault, _mock_config(mock_jira, mock_confluence, mock_llm, environment="company", profiles=profiles))
    result = run_kb("doctor")
    state, text = _state(result.stdout, "LLM main 주소")
    assert state == "✗" and "공개 LLM 호스트" in text
    assert _state(result.stdout, "LLM main")[0] != "✓"
    assert result.returncode == 5


def test_company_public_host_even_when_llm_disabled(run_kb, vault, mock_jira, mock_confluence, mock_llm):
    _store_pats()
    profiles = {"main": {"base_url": _example_main_url(), "model": "x"}}
    data = _mock_config(mock_jira, mock_confluence, mock_llm, environment="company", profiles=profiles, enabled=False)
    config.save_local(vault, data)
    result = run_kb("doctor")
    assert _state(result.stdout, "LLM")[0] == "-"
    assert result.returncode == 5


def test_missing_secret_ref(run_kb, vault, mock_jira, mock_confluence, mock_llm):
    kb_secrets.store("work-kb", "confluence", "mock-token")
    config.save_local(vault, _mock_config(mock_jira, mock_confluence, mock_llm))
    result = run_kb("doctor")
    state, text = _state(result.stdout, "비밀값 jira.pat_ref")
    assert state == "✗"
    assert kb_secrets.describe_missing("keyring:work-kb/jira") in text
    assert _state(result.stdout, "Jira")[0] == "✗"
    assert result.returncode == 1


def test_llm_disabled_is_info(run_kb, vault, mock_jira, mock_confluence, mock_llm):
    _store_pats()
    config.save_local(vault, _mock_config(mock_jira, mock_confluence, mock_llm, enabled=False))
    result = run_kb("doctor")
    assert _state(result.stdout, "LLM") == ("-", "비활성")
    assert result.returncode == 0, result.stdout


def test_llm_key_rejected(run_kb, vault, mock_jira, mock_confluence):
    _store_pats()
    kb_secrets.store("work-kb", "llm", "wrong-llm-key")
    llm = mock_llm.start(require_key="right-llm-key")
    try:
        profiles = {"main": {"base_url": f"{llm.base_url}/v1", "model": "mock", "api_key_ref": "keyring:work-kb/llm"}}
        config.save_local(vault, _mock_config(mock_jira, mock_confluence, llm, profiles=profiles))
        result = run_kb("doctor")
    finally:
        llm.stop()
    state, text = _state(result.stdout, "LLM main")
    assert state == "✗" and "키 거부" in text
    assert "wrong-llm-key" not in result.stdout + result.stderr
    assert result.returncode == 1


def test_model_not_listed_is_info(run_kb, vault, mock_jira, mock_confluence, mock_llm):
    _store_pats()
    profiles = {"main": {"base_url": f"{mock_llm.base_url}/v1", "model": "no-such-model"}}
    config.save_local(vault, _mock_config(mock_jira, mock_confluence, mock_llm, profiles=profiles))
    result = run_kb("doctor")
    state, text = _state(result.stdout, "LLM main")
    assert state == "-" and "연결됨, 모델 목록에 없음" in text
    assert result.returncode == 0


def test_missing_local_config(run_kb):
    result = run_kb("doctor")
    state, text = _state(result.stdout, "config.local.yaml")
    assert state == "✗" and "kb.py init 을 실행하세요" in text
    assert result.returncode == 1


def test_json_matches_text(run_kb, vault, mock_jira, mock_confluence, mock_llm):
    _store_pats()
    config.save_local(vault, _mock_config(mock_jira, mock_confluence, mock_llm))
    text_run = run_kb("doctor")
    json_run = run_kb("doctor", "--json")
    items = json.loads(json_run.stdout)
    assert [(i["state"], i["name"]) for i in items] == [(s, n) for s, n, _ in _lines(text_run.stdout)]
    counts = tuple(sum(1 for i in items if i["state"] == s) for s in ("✓", "✗", "-"))
    assert counts == _summary(text_run.stdout)
    assert json_run.returncode == text_run.returncode == 0


def test_korean_vault_path_without_mojibake(run_kb, vault):
    result = run_kb("doctor")
    assert "한글 경로 (테스트)" in result.stdout
    assert str(vault.resolve()) in result.stdout
    assert "�" not in result.stdout + result.stderr
    state, text = _state(result.stdout, "볼트 경로")
    assert state == "-" and "ASCII 아닌" in text
    assert _state(result.stdout, "쓰기 권한")[0] == "✓"
    assert _state(result.stdout, "PowerShell 실행 정책")[0] == "-"
    assert not list((vault / ".state").glob(".doctor-*"))


def test_redetect_is_phase_4_and_refused_for_agent(run_kb):
    assert run_kb("doctor", "--redetect").returncode == 4
    result = run_kb("doctor", "--redetect", env={"KB_AGENT": "1"})
    assert result.returncode == 7
    assert run_kb("doctor", "--json", env={"KB_AGENT": "1"}).returncode == 1  # 그냥 doctor는 허용


# ---------------------------------------------------------------- lib/net (프로세스 안)


class FakeSession:
    """session.request를 흉내 낸다. 응답 또는 예외를 차례로 돌려준다."""

    def __init__(self, *items):
        self.items = list(items)
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _resp(status: int, body: bytes = b"{}", headers: dict | None = None) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = body
    resp._content_consumed = True
    resp.headers.update(headers or {})
    resp.url = "http://127.0.0.1/x"
    return resp


@pytest.fixture
def sleeps(monkeypatch):
    waited: list[float] = []
    monkeypatch.setattr(net, "_sleep", waited.append)
    return waited


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_session_for_headers_and_options(tmp_path):
    ca = tmp_path / "회사 인증서.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    s = net.session_for({"ca_bundle": str(ca), "trust_env": False, "proxies": {"https": "http://127.0.0.1:1"}}, "tok-secret-1")
    assert s.headers["Authorization"] == "Bearer tok-secret-1"
    assert s.headers["Accept"] == "application/json"
    assert s.headers["User-Agent"] == "work-kb"
    assert s.verify == str(ca)
    assert s.trust_env is False
    assert s.proxies["https"] == "http://127.0.0.1:1"
    assert core.redact("x tok-secret-1 y") == "x *** y"
    assert "Authorization" not in net.session_for({"verify": False}, None).headers
    assert net.session_for({"verify": False}, None).verify is False
    with pytest.raises(core.ConfigError):
        net.session_for({"ca_bundle": str(tmp_path / "없음.pem")}, None)


def test_auth_errors(sleeps):
    with pytest.raises(core.AuthError, match="401"):
        net.request(FakeSession(_resp(401)), "GET", "http://127.0.0.1/a")
    with pytest.raises(core.AuthError, match="403") as info:
        net.request(FakeSession(_resp(403)), "GET", "http://127.0.0.1/a")
    assert info.value.exit_code == 2 and info.value.hint
    assert net.request(FakeSession(_resp(401)), "GET", "http://x/a", auth_raises=False).status_code == 401


def test_429_retry_after_then_ok(sleeps):
    session = FakeSession(_resp(429, headers={"Retry-After": "2"}), _resp(429, headers={"Retry-After": "1000"}), _resp(200))
    assert net.request(session, "GET", "http://127.0.0.1/a").status_code == 200
    assert sleeps == [2.0, 60.0]
    assert len(session.calls) == 3


def test_429_backoff_then_gives_up(sleeps):
    session = FakeSession(*[_resp(429) for _ in range(4)])
    assert net.request(session, "GET", "http://127.0.0.1/a", max_429=3).status_code == 429
    assert sleeps == [1.0, 2.0, 4.0]


def test_5xx_is_returned(sleeps):
    assert net.request(FakeSession(_resp(503)), "GET", "http://127.0.0.1/a").status_code == 503
    assert sleeps == []


def test_read_timeout_is_after_send():
    with pytest.raises(core.NetworkError) as info:
        net.request(FakeSession(rex.ReadTimeout("slow")), "POST", "http://127.0.0.1/a", timeout=5)
    assert info.value.after_send is True and "응답 시간" in str(info.value)


def test_dns_failure_is_before_send():
    gai = socket.gaierror(11001, "getaddrinfo failed")
    reason = u3.NameResolutionError("jira.example.invalid", None, gai)
    exc = rex.ConnectionError(u3.MaxRetryError(None, "/x", reason=reason))
    with pytest.raises(core.NetworkError) as info:
        net.request(FakeSession(exc), "GET", "https://jira.example.invalid/x")
    assert info.value.after_send is False and "주소를 찾지 못했습니다" in str(info.value)


def test_error_text_is_redacted():
    core.register_secret("leaky-token-99")
    with pytest.raises(core.NetworkError) as info:
        net.request(FakeSession(rex.ConnectionError("boom leaky-token-99")), "GET", "http://127.0.0.1/a")
    assert "leaky-token-99" not in str(info.value) and "***" in str(info.value)


def test_refused_connection_real_socket():
    url = f"http://127.0.0.1:{_closed_port()}/x"
    with pytest.raises(core.NetworkError) as info:
        net.request(net.session_for({"trust_env": False}, None), "GET", url, timeout=5)
    assert info.value.after_send is False and "거부" in str(info.value)
    status, text = net.probe(url, conn={"trust_env": False}, timeout=5)
    assert status is None and "거부" in text


def test_proxy_failure_real_socket(mock_jira):
    conn = {"trust_env": False, "proxies": {"http": f"http://127.0.0.1:{_closed_port()}"}}
    with pytest.raises(core.NetworkError) as info:
        net.request(net.session_for(conn, None), "GET", f"{mock_jira.base_url}/rest/api/2/myself", timeout=5)
    assert "프록시" in str(info.value) and info.value.after_send is False


def test_ssl_failure_real_socket(mock_jira):
    https = mock_jira.base_url.replace("http://", "https://")
    with pytest.raises(core.NetworkError) as info:
        net.request(net.session_for({"trust_env": False}, None), "GET", f"{https}/rest/api/2/myself", timeout=5)
    assert "인증서" in str(info.value)


def test_get_json_and_probe_against_mock(mock_jira):
    session = net.session_for({"trust_env": False}, "mock-token")
    assert net.get_json(session, f"{mock_jira.base_url}/rest/api/2/myself")["displayName"] == "홍길동"
    with pytest.raises(core.KbError) as info:
        net.get_json(session, f"{mock_jira.base_url}/rest/api/2/nosuch")
    assert info.value.status == 404
    assert net.probe(f"{mock_jira.base_url}/rest/api/2/myself", conn={"trust_env": False}) == (401, "인증 거부 (401)")
    assert net.probe(f"{mock_jira.base_url}/rest/api/2/myself", token="t", conn={"trust_env": False})[0] == 200
    assert net.probe("not a url")[0] is None


def test_get_json_rejects_non_json():
    with pytest.raises(core.KbError, match="JSON이 아닙니다") as info:
        net.get_json(FakeSession(_resp(200, b"<html>login</html>")), "http://127.0.0.1/a")
    assert info.value.status == 200
    with pytest.raises(core.NetworkError):
        net.get_json(FakeSession(_resp(502)), "http://127.0.0.1/a")
