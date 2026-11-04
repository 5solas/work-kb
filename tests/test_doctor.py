"""`kb.py doctor` (D32 세 상태)와 lib/net 연결 계층."""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import socket
import subprocess
import sys
import types
from pathlib import Path

import pytest
import requests
import yaml
from requests import exceptions as rex
from urllib3 import exceptions as u3

import doctor
import mock_confluence as mock_confluence_mod
import mock_jira as mock_jira_mod
import mock_llm
from lib import config, core, net
from lib import secrets as kb_secrets

ROOT = Path(__file__).resolve().parents[1]
LINE = re.compile(r"^([✓✗-]) ([^:]+): (.*)$")
SUMMARY = re.compile(r"^✓ (\d+) ✗ (\d+) - (\d+)$")
TOKEN = "mock-token"  # init --dev가 저장하는 PAT(D45). 모의 서버는 이 값만 받는다.
SKIPPED_PUBLIC = ("-", "공개 LLM 호스트라 호출하지 않았습니다")


# 이 파일의 Jira·Confluence 모의 서버는 정확한 PAT만 받는다(conftest의 같은 이름 fixture를 덮는다).
@pytest.fixture
def mock_jira():
    with mock_jira_mod.start(require_token=TOKEN) as server:
        yield server


@pytest.fixture
def mock_confluence():
    with mock_confluence_mod.start(require_token=TOKEN) as server:
        yield server


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


def _store_pats(jira: str = TOKEN) -> None:
    kb_secrets.store("work-kb", "jira", jira)
    kb_secrets.store("work-kb", "confluence", TOKEN)


def _models_calls(server) -> list[dict]:
    return [e for e in server.log if e["path"].endswith("/models")]


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
    assert _state(result.stdout, "LLM main") == SKIPPED_PUBLIC
    assert result.returncode == 5


def _company_mock_profile(run_kb, vault, jira, confluence, llm, *, base_url=None, local_extra=None, env=None):
    """회사 환경, main 프로필이 모의 LLM을 가리킨다. trust_env: false라 프록시 없이 바로 닿는다."""
    _store_pats()
    kb_secrets.store("work-kb", "llm", "company-llm-key")
    profile = {
        "base_url": base_url or f"{llm.base_url}/v1",
        "model": "mock",
        "api_key_ref": "keyring:work-kb/llm",
        "trust_env": False,
    }
    data = _mock_config(jira, confluence, llm, environment="company", profiles={"main": profile})
    for key, value in (local_extra or {}).items():
        data["llm"][key] = value
    config.save_local(vault, data)
    return run_kb("doctor", env=env)


def test_company_public_host_sends_nothing(run_kb, vault, mock_jira, mock_confluence, mock_llm):
    # 모의 서버 호스트를 차단 목록에 넣는다. 가드가 없으면 요청이 이 서버에 닿는다.
    result = _company_mock_profile(
        run_kb, vault, mock_jira, mock_confluence, mock_llm, local_extra={"public_hosts_blocklist": ["127.0.0.1"]}
    )
    assert _state(result.stdout, "LLM main 주소")[0] == "✗"
    assert _state(result.stdout, "LLM main") == SKIPPED_PUBLIC
    assert mock_llm.log == []
    assert result.returncode == 5


def test_company_mock_profile_is_called_without_blocklist_hit(run_kb, vault, mock_jira, mock_confluence, mock_llm):
    # 위 시험의 대조군: 같은 설정에서 차단 목록만 빼면 요청이 실제로 닿는다.
    result = _company_mock_profile(run_kb, vault, mock_jira, mock_confluence, mock_llm)
    assert _state(result.stdout, "공개 호스트 차단")[0] == "✓"
    assert _state(result.stdout, "LLM main")[0] == "✓"
    assert len(_models_calls(mock_llm)) == 1
    assert result.returncode == 0, result.stdout


def test_company_backslash_url_is_refused(run_kb, vault, mock_jira, mock_confluence, mock_llm):
    # urllib.parse는 호스트를 llm.corp.invalid로, urllib3는 127.0.0.1로 읽는다. 어긋나면 거부.
    base = f"{mock_llm.base_url}\\@llm.corp.invalid/v1"
    result = _company_mock_profile(run_kb, vault, mock_jira, mock_confluence, mock_llm, base_url=base)
    state, text = _state(result.stdout, "LLM main 주소")
    assert state == "✗" and "호스트를 하나로 정할 수 없습니다" in text
    assert _state(result.stdout, "LLM main") == SKIPPED_PUBLIC
    assert mock_llm.log == []
    assert result.returncode == 5


@pytest.mark.parametrize(
    "local_extra, env",
    [
        ({}, {"KB_LLM_PUBLIC_HOSTS_BLOCKLIST": "5"}),
        ({"public_hosts_blocklist": {"deepinfra.com": True}}, None),
        ({"public_hosts_blocklist": 5}, None),
    ],
    ids=["env-scalar", "local-mapping", "local-scalar"],
)
def test_company_malformed_blocklist_fails_closed(run_kb, vault, mock_jira, mock_confluence, mock_llm, local_extra, env):
    result = _company_mock_profile(run_kb, vault, mock_jira, mock_confluence, mock_llm, local_extra=local_extra, env=env)
    state, text = _state(result.stdout, "config.yaml")
    assert state == "✗" and "public_hosts_blocklist" in text
    assert mock_llm.log == []
    assert result.returncode == 5, result.stdout


def test_home_malformed_blocklist_is_exit_1(run_kb, vault, mock_jira, mock_confluence, mock_llm):
    _store_pats()
    config.save_local(vault, _mock_config(mock_jira, mock_confluence, mock_llm))
    result = run_kb("doctor", env={"KB_LLM_PUBLIC_HOSTS_BLOCKLIST": "5"})
    assert _state(result.stdout, "config.yaml")[0] == "✗"
    assert mock_llm.log == []
    assert result.returncode == 1


def _in_process(vault, monkeypatch, checks=None):
    """doctor를 프로세스 안에서 돌린다. HTTP는 `_get`을 바꿔 기록만 한다."""
    calls: list[str] = []

    def fake_get(url, token, conn, *, auth_raises):
        calls.append(url)
        return None, "시험에서 막음"

    monkeypatch.setattr(doctor, "_get", fake_get)
    ctx = doctor.Context(root_arg=str(vault), report=doctor.Report())
    doctor.run_checks(ctx, checks)
    return ctx, calls


_NETWORK_CHECKS = [
    doctor.check_dependencies,
    doctor.check_config,
    doctor.check_local,
    doctor.check_public_hosts,
    doctor.check_secrets,
    doctor.check_llm,
]


def test_public_host_check_crash_fails_closed(vault, monkeypatch, mock_jira, mock_confluence, mock_llm):
    _store_pats()
    profiles = {"main": {"base_url": "https://llm.corp.invalid/v1", "model": "x"}}
    config.save_local(vault, _mock_config(mock_jira, mock_confluence, mock_llm, environment="company", profiles=profiles))

    def boom(url, blocklist):
        raise RuntimeError("검사 중 오류")

    monkeypatch.setattr(config, "is_public_host", boom)
    ctx, calls = _in_process(vault, monkeypatch, _NETWORK_CHECKS)
    guard = [c for c in ctx.report.checks if c.name == "공개 호스트 차단"]
    assert guard and guard[0].state == "✗" and guard[0].public_host
    assert "main" in ctx.public_profiles
    assert [(c.state, c.text) for c in ctx.report.checks if c.name == "LLM main"] == [SKIPPED_PUBLIC]
    assert calls == []
    assert ctx.report.exit_code() == 5


def test_company_llm_skipped_when_public_check_did_not_run(vault, monkeypatch, mock_jira, mock_confluence, mock_llm):
    _store_pats()
    profiles = {"main": {"base_url": "https://llm.corp.invalid/v1", "model": "x"}}
    config.save_local(vault, _mock_config(mock_jira, mock_confluence, mock_llm, environment="company", profiles=profiles))
    checks = [c for c in _NETWORK_CHECKS if c is not doctor.check_public_hosts]
    ctx, calls = _in_process(vault, monkeypatch, checks)
    assert calls == []
    assert [c.state for c in ctx.report.checks if c.name == "LLM main"] == ["-"]


def test_wrong_pat_in_keyring_is_jira_fail(run_kb, vault, mock_jira, mock_confluence, mock_llm):
    _store_pats(jira="stale-token")
    config.save_local(vault, _mock_config(mock_jira, mock_confluence, mock_llm))
    result = run_kb("doctor")
    state, text = _state(result.stdout, "Jira")
    assert state == "✗" and "401" in text
    assert _state(result.stdout, "Confluence")[0] == "✓"
    assert "stale-token" not in result.stdout + result.stderr
    assert result.returncode == 1


def test_llm_redirect_is_not_followed(run_kb, vault, mock_jira, mock_confluence):
    _store_pats()
    target = mock_llm.start()
    source = mock_llm.start(redirect_to=f"{target.base_url}/v1/models")
    try:
        profiles = {"main": {"base_url": f"{source.base_url}/v1", "model": "mock"}}
        config.save_local(vault, _mock_config(mock_jira, mock_confluence, source, profiles=profiles))
        result = run_kb("doctor")
    finally:
        source.stop()
        target.stop()
    state, text = _state(result.stdout, "LLM main")
    assert state == "✗" and "리디렉션" in text
    assert len(_models_calls(source)) == 1
    assert target.log == []
    assert result.returncode == 1


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
    assert _state(result.stdout, "PowerShell 실행 정책")[0] in ("-", "✗")
    assert _state(result.stdout, "시간대")[0] == "-"
    assert not list((vault / ".state").glob(".doctor-*"))


def test_redetect_is_phase_4_and_refused_for_agent(run_kb):
    assert run_kb("doctor", "--redetect").returncode == 4
    result = run_kb("doctor", "--redetect", env={"KB_AGENT": "1"})
    assert result.returncode == 7
    assert run_kb("doctor", "--json", env={"KB_AGENT": "1"}).returncode == 1  # 그냥 doctor는 허용


# ---------------------------------------------------------------- PowerShell·시간대 (프로세스 안)


def _policy_ctx(monkeypatch, stdout: str, returncode: int = 0, stderr: str = "") -> doctor.Context:
    done = subprocess.CompletedProcess(["powershell.exe"], returncode, stdout=stdout, stderr=stderr)
    monkeypatch.setattr(doctor, "_run_powershell", lambda exe, command: done)
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "C:/fake/powershell.exe")
    monkeypatch.setenv("SystemRoot", "C:/없는/폴더")
    ctx = doctor.Context(root_arg=None, report=doctor.Report())
    doctor.check_powershell(ctx)
    return ctx


def _policy_list(**scopes: str) -> str:
    names = ("MachinePolicy", "UserPolicy", "Process", "CurrentUser", "LocalMachine")
    lines = [f"{n}={scopes.get(n, 'Undefined')}" for n in names]
    return "\r\n".join(lines + [f"Effective={scopes.get('Effective', 'RemoteSigned')}"]) + "\r\n"


@pytest.mark.parametrize("scope", ["MachinePolicy", "UserPolicy"])
@pytest.mark.parametrize("policy", ["Restricted", "AllSigned"])
def test_powershell_gpo_block_is_fail(monkeypatch, scope, policy):
    ctx = _policy_ctx(monkeypatch, _policy_list(**{scope: policy, "LocalMachine": "RemoteSigned", "Effective": policy}))
    (item,) = ctx.report.checks
    assert item.state == "✗" and scope in item.text and policy in item.text
    assert "F(파일) 캡처를 쓰세요" in item.text
    assert ctx.report.exit_code() == 1


def test_powershell_local_policy_is_info_with_scope(monkeypatch):
    ctx = _policy_ctx(monkeypatch, _policy_list(LocalMachine="RemoteSigned", CurrentUser="Restricted", Effective="Restricted"))
    (item,) = ctx.report.checks
    assert item.state == "-"
    assert item.text.startswith("Restricted, CurrentUser 범위")
    ctx = _policy_ctx(monkeypatch, _policy_list(Effective="Restricted"))
    assert ctx.report.checks[0].text.startswith("Restricted, 기본값")


def test_powershell_unreadable_is_info(monkeypatch):
    ctx = _policy_ctx(monkeypatch, "", returncode=1, stderr="오류 한 줄\r\n둘째 줄")
    (item,) = ctx.report.checks
    assert item.state == "-" and "오류 한 줄" in item.text


def test_powershell_child_env_drops_parent_policy(monkeypatch):
    monkeypatch.setenv("PSExecutionPolicyPreference", "Bypass")
    monkeypatch.setenv("PSModulePath", "C:/pwsh7/Modules")
    env = {k.upper() for k in doctor._powershell_env()}
    assert "PSEXECUTIONPOLICYPREFERENCE" not in env and "PSMODULEPATH" not in env


def _powershell_exe() -> str | None:
    if sys.platform != "win32":
        return None
    import os

    builtin = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(builtin) if builtin.is_file() else shutil.which("powershell.exe")


@pytest.mark.skipif(_powershell_exe() is None, reason="Windows PowerShell 5.1 없음")
def test_powershell_real_process_policy_not_inherited(monkeypatch):
    # 부모의 Process 범위 Bypass가 새면 '…, Process 범위'로 보인다.
    monkeypatch.setenv("PSExecutionPolicyPreference", "Bypass")
    ctx = doctor.Context(root_arg=None, report=doctor.Report())
    doctor.check_powershell(ctx)
    (item,) = ctx.report.checks
    assert "Process 범위" not in item.text, item.text


def _oem_codepage() -> int:
    import ctypes

    return ctypes.windll.kernel32.GetOEMCP()


@pytest.mark.skipif(_powershell_exe() is None or _oem_codepage() != 949, reason="한국어 OEM 코드 페이지(949)에서만 의미가 있다")
def test_powershell_output_decoded_from_oem():
    result = doctor._run_powershell(_powershell_exe(), "Write-Output '한글 확인'; Write-Error '한글 오류'; exit 3")
    assert not isinstance(result, str)
    assert "한글 확인" in result.stdout
    assert "한글 오류" in result.stderr and "�" not in result.stderr


def _tz_ctx(timezone: str) -> doctor.Context:
    ctx = doctor.Context(root_arg=None, report=doctor.Report())
    ctx.cfg = config.Config(root=Path("C:/kb"), data={"timezone": timezone}, environment="home")
    doctor.check_timezone(ctx)
    return ctx


def _os_offset() -> dt.timedelta:
    return dt.datetime.now(dt.timezone.utc).astimezone().utcoffset()


def test_timezone_line_format():
    (item,) = _tz_ctx("Asia/Seoul").report.checks
    assert item.state == "-" and item.name == "시간대"
    off = _os_offset()
    minutes = int(off.total_seconds() // 60)
    os_text = f"{'+' if minutes >= 0 else '-'}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"
    assert item.text.startswith(f"설정 Asia/Seoul(+09:00) · OS {os_text}")
    assert ("다릅니다" in item.text) is (off != dt.timedelta(hours=9))


def test_timezone_differs_is_flagged():
    name = "UTC" if _os_offset() != dt.timedelta(0) else "Asia/Seoul"
    (item,) = _tz_ctx(name).report.checks
    assert item.state == "-" and "OS 시간대가 설정과 다릅니다" in item.text


def test_timezone_fixed_table_fallback_is_flagged(monkeypatch):
    def no_zone(name):
        raise LookupError("tzdata 없음")

    monkeypatch.setitem(sys.modules, "zoneinfo", types.SimpleNamespace(ZoneInfo=no_zone))
    (item,) = _tz_ctx("Asia/Seoul").report.checks
    assert item.text.startswith("설정 Asia/Seoul(+09:00)")
    assert "tzdata가 없어 고정 표를 씁니다" in item.text
    (item,) = _tz_ctx("Europe/Paris").report.checks
    assert "고정 표에도 없어 OS 시간대를 씁니다" in item.text


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
    assert net.probe(f"{mock_jira.base_url}/rest/api/2/myself", token="t", conn={"trust_env": False})[0] == 401
    assert net.probe(f"{mock_jira.base_url}/rest/api/2/myself", token=TOKEN, conn={"trust_env": False})[0] == 200
    assert net.probe("not a url")[0] is None


def test_get_json_rejects_non_json():
    with pytest.raises(core.KbError, match="JSON이 아닙니다") as info:
        net.get_json(FakeSession(_resp(200, b"<html>login</html>")), "http://127.0.0.1/a")
    assert info.value.status == 200
    with pytest.raises(core.NetworkError):
        net.get_json(FakeSession(_resp(502)), "http://127.0.0.1/a")
