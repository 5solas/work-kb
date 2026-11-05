"""탐지 실패의 CLI 종료 코드: `kb.py init --section llm --from-env --non-interactive`가 DetectError로
멈추면 종료 3이고 config.local.yaml은 한 바이트도 바뀌지 않는다(모의 LLM만 쓴다)."""
from __future__ import annotations

import pytest

import mock_llm
from lib import config

BASE = {
    "environment": "home",
    "jira": {"base_url": "http://127.0.0.1:9", "project_keys": ["KB"], "pat_ref": "keyring:work-kb/jira"},
    "confluence": {"enabled": False},
}


@pytest.mark.parametrize(
    "modes",
    [
        {"http_500_always": True},  # 기준선부터 계속 500
        {"reasoning_effort": "accept", "default_thinking": True, "rate_limit_on": "reasoning_effort"},  # 후보가 계속 429
        {"vision": "accept", "rate_limit_on": "image"},  # 비전이 계속 429
    ],
    ids=["500-always", "429-toggle", "429-vision"],
)
def test_init_section_llm_detect_error_is_exit_3_and_saves_nothing(run_kb, vault, modes):
    config.save_local(vault, BASE)
    local = vault / "config.local.yaml"
    before = local.read_bytes()
    with mock_llm.start(call_counter=True, **modes) as server:
        env = {"KB_INIT_LLM_URL": f"{server.base_url}/v1", "KB_INIT_LLM_MODEL": "mock"}
        result = run_kb("init", "--section", "llm", "--from-env", "--non-interactive", env=env)
        posts = server.counts["chat"]
    assert result.returncode == 3, result.stdout + result.stderr
    assert local.read_bytes() == before
    assert 1 <= posts <= 10
    if "rate_limit_on" in modes:
        assert "rate limited" in result.stderr and "--redetect" in result.stderr


def test_init_section_llm_control_saves_detected(run_kb, vault):
    """대조군: 같은 명령이 정상 서버에서는 종료 0이고 detected를 저장한다."""
    config.save_local(vault, BASE)
    with mock_llm.start(reasoning_effort="accept", vision="accept") as server:
        env = {"KB_INIT_LLM_URL": f"{server.base_url}/v1", "KB_INIT_LLM_MODEL": "mock"}
        result = run_kb("init", "--section", "llm", "--from-env", "--non-interactive", env=env)
    assert result.returncode in (0, 1), result.stdout + result.stderr  # 끝의 doctor 결과(jira ✗)는 무관
    saved = config.load(vault).profiles()["main"]["detected"]
    assert saved["reasoning_toggle"]["param_name"] == "reasoning_effort" and saved["vision"] is True
