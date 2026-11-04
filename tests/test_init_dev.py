"""`kb.py init --dev` (D45): home 블록, KB_INIT_* 덮어쓰기, 모의 PAT, company 거부."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from lib import core

ROOT = Path(__file__).resolve().parents[1]
DEEPINFRA_REF_LINE = re.compile(r"""^api_key_ref: ['"]?env:DEEPINFRA_API_KEY['"]?$""")


@pytest.fixture
def dev_env(mock_jira, mock_confluence, mock_llm):
    return {
        "KB_INIT_JIRA_URL": mock_jira.base_url,
        "KB_INIT_CONFLUENCE_URL": mock_confluence.base_url,
        "KB_INIT_LLM_URL": f"{mock_llm.base_url}/v1",
    }


def _local(vault: Path) -> Path:
    return vault / "config.local.yaml"


def _keyring(tmp_path: Path) -> dict[str, str]:
    path = tmp_path / "keyring.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _lines(stdout: str) -> list[tuple[str, str, str]]:
    out = []
    for line in stdout.splitlines():
        m = re.match(r"^([✓✗-]) ([^:]+): (.*)$", line)
        if m:
            out.append((m.group(1), m.group(2), m.group(3)))
    return out


def _example_home() -> dict:
    data = yaml.safe_load((ROOT / "config.local.example.yaml").read_text(encoding="utf-8"))
    return data["home"]


def test_writes_home_config_from_example(run_kb, vault, tmp_path, dev_env, mock_jira, mock_confluence, mock_llm):
    result = run_kb("init", "--dev", env=dev_env)
    path = _local(vault)
    assert path.is_file(), result.stdout + result.stderr
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    home = _example_home()

    assert data["environment"] == "home"
    assert data["jira"]["base_url"] == mock_jira.base_url
    assert data["confluence"]["base_url"] == mock_confluence.base_url
    assert data["llm"]["profiles"]["mock"]["base_url"] == f"{mock_llm.base_url}/v1"
    assert data["jira"]["pat_ref"] == "keyring:work-kb/jira"
    assert data["confluence"]["pat_ref"] == "keyring:work-kb/confluence"
    # main은 예시 그대로(D45)
    assert data["llm"]["profiles"]["main"] == home["llm"]["profiles"]["main"]
    assert data["jira"]["project_keys"] == home["jira"]["project_keys"]

    assert "mock-token" not in text
    stored = _keyring(tmp_path)
    assert stored["work-kb\njira"] == "mock-token"
    assert stored["work-kb\nconfluence"] == "mock-token"

    assert str(path) in result.stdout
    assert "mock-token" not in result.stdout + result.stderr
    # doctor가 이어서 돈다. 키·네트워크가 없는 main/fallback만 ✗
    lines = _lines(result.stdout)
    assert ("✓", "Jira") in [(s, n) for s, n, _ in lines]
    failed = [n for s, n, _ in lines if s == "✗"]
    assert failed and all("main" in n or "fallback" in n for n in failed), failed
    assert result.returncode == 1


def test_exit_0_when_all_mocks_run(run_kb, vault, dev_env):
    marker = "di-marker-0123456789"
    env = dict(dev_env, DEEPINFRA_API_KEY=marker, KB_LLM_FALLBACK_ORDER="[mock]")
    result = run_kb("init", "--dev", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = _lines(result.stdout)
    states = {n: s for s, n, _ in lines}
    assert states["Jira"] == "✓"
    assert states["Confluence"] == "✓"
    assert states["LLM mock"] == "✓"
    assert marker not in result.stdout + result.stderr
    assert marker not in _local(vault).read_text(encoding="utf-8")


def test_rerun_is_idempotent(run_kb, vault, dev_env):
    run_kb("init", "--dev", env=dev_env)
    first = _local(vault).read_bytes()
    run_kb("init", "--dev", env=dev_env)
    assert _local(vault).read_bytes() == first


def test_refuses_existing_company_config(run_kb, vault, tmp_path, dev_env):
    path = _local(vault)
    original = "environment: company\njira:\n  base_url: https://jira.example.invalid\n".encode("utf-8")
    path.write_bytes(original)
    result = run_kb("init", "--dev", env=dev_env)
    assert result.returncode == 7
    assert "company" in result.stderr
    assert path.read_bytes() == original
    assert _keyring(tmp_path) == {}


def test_agent_session_refused(run_kb, vault, dev_env):
    result = run_kb("init", "--dev", env=dict(dev_env, KB_AGENT="1"))
    assert result.returncode == 7
    assert not _local(vault).exists()


def test_direct_call_refused_in_agent_session(monkeypatch, vault):
    import init

    monkeypatch.setenv("KB_AGENT", "1")
    with pytest.raises(core.PolicyRefused):
        init.main(["--dev", "--root", str(vault)])
    assert not _local(vault).exists()


@pytest.mark.parametrize("args", [(), ("--section", "jira"), ("--from-env", "--non-interactive")])
def test_without_dev_is_phase_message(run_kb, vault, args):
    result = run_kb("init", *args)
    assert result.returncode == 4
    assert "대화형 마법사는 Phase 2에서 추가됩니다" in result.stdout
    assert not _local(vault).exists()


def test_redetect_is_phase_message(run_kb):
    result = run_kb("init", "--redetect")
    assert result.returncode == 4
    assert "Phase 4" in result.stdout


def test_no_deepinfra_secret_in_file(run_kb, vault, dev_env):
    marker = "di-secret-marker-42"
    run_kb("init", "--dev", env=dict(dev_env, DEEPINFRA_API_KEY=marker))
    text = _local(vault).read_text(encoding="utf-8")
    assert marker not in text
    hits = [line.strip() for line in text.splitlines() if "DEEPINFRA" in line]
    assert hits, "home 예시의 env 참조가 있어야 한다"
    assert all(DEEPINFRA_REF_LINE.match(line) for line in hits), hits


def test_llm_key_goes_to_keyring(run_kb, vault, tmp_path, dev_env):
    marker = "llm-key-marker-777"
    result = run_kb("init", "--dev", env=dict(dev_env, KB_INIT_LLM_KEY=marker))
    text = _local(vault).read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert data["llm"]["profiles"]["main"]["api_key_ref"] == "keyring:work-kb/llm"
    assert data["llm"]["profiles"]["fallback"]["api_key_ref"] == "keyring:work-kb/llm"
    assert marker not in text
    assert "DEEPINFRA" not in text  # env 참조가 keyring 참조로 바뀜
    assert marker not in result.stdout + result.stderr
    assert _keyring(tmp_path)["work-kb\nllm"] == marker


def test_wizard_runs_injected_steps(vault):
    import init

    asked = []

    def ask(prompt, default=None):
        asked.append(prompt)
        return "KB"

    def step(wizard, data):
        data.setdefault("jira", {})["project_keys"] = [wizard.ask("프로젝트 키", None)]

    wizard = init.Wizard(vault, ask=ask, ask_secret=lambda prompt: "x", steps=[step])
    assert wizard.run({"environment": "home"}) == {"environment": "home", "jira": {"project_keys": ["KB"]}}
    assert asked == ["프로젝트 키"]


@pytest.mark.parametrize("name", ["init.py", "doctor.py", "lib/net.py"])
def test_code_has_no_llm_host(name):
    text = (ROOT / "scripts" / name).read_text(encoding="utf-8").lower()
    assert "deepinfra" not in text
