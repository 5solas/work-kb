"""W4.6: init LLM 단계(--section llm, 전체 마법사, --from-env, --dev 탐지)와 doctor 탐지 재검증·--redetect(D48).

모든 LLM 호출은 tests/mock_llm.py(127.0.0.1)로만 간다. 입력 함수를 주입해 프로세스 안에서 돌린다.
"""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest
import yaml

import doctor
import init
import mock_jira as mock_jira_mod
import mock_llm
from lib import config, core, llm_detect

JIRA_TOKEN = "jira-pat-llm-0003"  # 가짜 PAT
LLM_KEY = "llm-key-marker-0004"  # 가짜 LLM 키
RICH = {"reasoning_effort": "accept", "json_mode": "accept", "vision": "accept"}
LLM_CHECKS = [
    doctor.check_config,
    doctor.check_local,
    doctor.check_public_hosts,
    doctor.check_secrets,
    doctor.check_llm,
    doctor.check_detected,
]


class Script:
    """주입용 입력. 묻는 순서와 기본값을 기록한다. 답이 바닥나면 EOFError."""

    def __init__(self, answers: list[str], secrets: list[str]) -> None:
        self.answers = list(answers)
        self.secrets = list(secrets)
        self.asked: list[tuple[str, str | None]] = []
        self.secret_prompts: list[str] = []

    def ask(self, prompt: str, default: str | None = None) -> str:
        self.asked.append((prompt, default))
        if not self.answers:
            raise EOFError
        return self.answers.pop(0) or (default or "")

    def ask_secret(self, prompt: str) -> str:
        self.secret_prompts.append(prompt)
        if not self.secrets:
            raise EOFError
        return self.secrets.pop(0)


@pytest.fixture
def llm():
    with mock_llm.start(**RICH) as server:
        yield server


def _local(vault: Path) -> Path:
    return vault / "config.local.yaml"


def _read(vault: Path) -> dict:
    return yaml.safe_load(_local(vault).read_text(encoding="utf-8"))


def _keyring(tmp_path: Path) -> dict[str, str]:
    path = tmp_path / "keyring.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _base(environment: str = "home", llm_section: dict | None = None) -> dict:
    data = {
        "environment": environment,
        "jira": {"base_url": "http://127.0.0.1:9", "project_keys": ["KB"], "pat_ref": "keyring:work-kb/jira"},
        "confluence": {"enabled": False},
    }
    if llm_section is not None:
        data["llm"] = llm_section
    return data


def _wizard(vault, script, *, section="llm", values=None, out=None):
    lines: list[str] = [] if out is None else out
    code = init.run_wizard(
        vault, section=section, values=values, ask=script.ask, ask_secret=script.ask_secret, out=lines.append, run_doctor=False
    )
    return code, "\n".join(lines)


def _doctor(vault: Path, *, redetect: bool = False) -> doctor.Report:
    ctx = doctor.Context(root_arg=str(vault), report=doctor.Report(), redetect=redetect)
    doctor.run_checks(ctx, LLM_CHECKS)
    return ctx.report


def _line(report: doctor.Report, name: str) -> tuple[str, str]:
    found = [(c.state, c.text) for c in report.checks if c.name == name]
    assert found, [c.line() for c in report.checks]
    return found[0]


def _profile_config(url: str, *, detected=None, environment: str = "home", extra_profiles: dict | None = None) -> dict:
    main: dict = {"base_url": url, "model": "mock"}
    if detected is not None:
        main["detected"] = detected
    profiles = {"main": main, **(extra_profiles or {})}
    return _base(environment, {"enabled": True, "profiles": profiles, "fallback_order": list(profiles)})


# ---------------------------------------------------------------- init --section llm


def test_section_llm_detects_and_saves(vault, llm):
    original = _base()
    config.save_local(vault, original)
    url = f"{llm.base_url}/v1"
    script = Script([url + "/", "1", "n"], [""])
    code, said = _wizard(vault, script)
    assert code == 0, said

    assert [p.split(" (")[0] for p, _ in script.asked] == ["LLM main 주소", "LLM main 모델", "보조(fallback) 프로필도 설정할까요?"]
    assert script.asked[2][1] == "n"  # 보조 프로필이 없으면 기본값 n
    assert len(script.secret_prompts) == 1 and "키가 없는 서버면 Enter" in script.secret_prompts[0]

    data = _read(vault)
    main = data["llm"]["profiles"]["main"]
    assert main["base_url"] == url and main["model"] == "mock"
    assert "api_key_ref" not in main
    detected = main["detected"]
    assert detected["reasoning_toggle"]["method"] == "param"
    assert detected["reasoning_toggle"]["param_name"] == "reasoning_effort"
    assert detected["json_mode"] is True and detected["vision"] is True
    assert detected["context_tokens"] == 32768 and detected["context_source"] == "models"
    assert detected["baseline_reasoning"] is False
    assert isinstance(detected["probes"], list) and detected["probes"]
    assert data["llm"]["enabled"] is True and data["llm"]["fallback_order"] == ["main"]
    # 다른 구역은 그대로
    assert data["jira"] == original["jira"] and data["environment"] == "home"

    assert 1 <= llm.counts["chat"] <= llm_detect.MAX_POSTS
    assert "1. mock" in said
    assert "detected:" in said and "reasoning_toggle:" in said and "probes" in said
    assert "추론 토글 param reasoning_effort" in said


def test_key_is_masked_retried_and_stored(vault, tmp_path):
    config.save_local(vault, _base())
    with mock_llm.start(require_key=LLM_KEY, **RICH) as server:
        url = f"{server.base_url}/v1"
        script = Script([url, "", "1", "n"], ["wrong-key-0009", LLM_KEY])
        code, said = _wizard(vault, script)
        assert code == 0, said
        chats = [e for e in server.log if e["path"].endswith("/chat/completions")]
        assert chats and all(e["has_auth"] for e in chats)
    assert "키가 거부되었습니다 (401)" in said
    assert len(script.secret_prompts) == 2
    text = _local(vault).read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert data["llm"]["profiles"]["main"]["api_key_ref"] == "keyring:work-kb/llm"
    assert LLM_KEY not in text and LLM_KEY not in said
    assert "wrong-key-0009" not in said
    assert _keyring(tmp_path)["work-kb\nllm"] == LLM_KEY


def test_wrong_key_does_not_touch_keyring_on_cancel(vault, tmp_path):
    config.save_local(vault, _base())
    before = _local(vault).read_bytes()
    with mock_llm.start(require_key=LLM_KEY) as server:
        script = Script([f"{server.base_url}/v1"], ["wrong-key-0010"])  # 다시 묻는 주소에서 입력이 끝남
        with pytest.raises(init.Cancelled):
            _wizard(vault, script)
    assert _local(vault).read_bytes() == before
    assert "work-kb\nllm" not in _keyring(tmp_path)


def test_no_models_list_takes_typed_name(vault):
    config.save_local(vault, _base())
    with mock_llm.start(no_models=True, **RICH) as server:
        script = Script([f"{server.base_url}/v1", "사내-모델-1", "n"], [""])
        code, said = _wizard(vault, script)
    assert code == 0, said
    assert "/models 목록이 없습니다" in said
    main = _read(vault)["llm"]["profiles"]["main"]
    assert main["model"] == "사내-모델-1"
    assert main["detected"]["context_source"] == "assumed"
    assert main["detected"]["context_tokens"] == 262144


def test_rerun_keeps_connection_settings_and_defaults(vault, llm):
    url = f"{llm.base_url}/v1"
    config.save_local(
        vault,
        _base(llm_section={"enabled": True, "profiles": {"main": {"base_url": url, "model": "mock", "trust_env": False, "detected": "pending"}}}),
    )
    script = Script(["", "", "n"], [""])  # 모두 Enter
    code, said = _wizard(vault, script)
    assert code == 0, said
    assert script.asked[0][1] == url and script.asked[1][1] == "mock"
    main = _read(vault)["llm"]["profiles"]["main"]
    assert main["trust_env"] is False
    assert isinstance(main["detected"], dict) and main["detected"]["vision"] is True


@pytest.mark.parametrize("public_url", ["https://api.openai.com/v1", "https://X.API.DeepInfra.com./v1/openai"])
def test_company_public_host_refused_before_any_call(vault, tmp_path, public_url):
    config.save_local(vault, _base("company"))
    before = _local(vault).read_bytes()
    script = Script([public_url], ["should-not-be-asked"])
    with pytest.raises(core.PublicHostRefused) as info:
        _wizard(vault, script)
    assert info.value.exit_code == 5
    assert script.secret_prompts == []  # 키를 묻기 전에 거부
    assert _local(vault).read_bytes() == before
    assert _keyring(tmp_path) == {}


def test_company_internal_host_allowed_and_public_leftover_removed(vault, llm):
    leftover = {"base_url": "https://api.openai.com/v1", "model": "x"}
    config.save_local(vault, _base("company", {"enabled": True, "profiles": {"fallback": leftover}, "fallback_order": ["main", "fallback"]}))
    script = Script([f"{llm.base_url}/v1", "1", "n", "y"], [""])
    code, said = _wizard(vault, script)
    assert code == 0, said
    assert script.asked[2][1] == "y"  # 기존 fallback이 있으면 기본값 y
    assert "공개 호스트를 가리킵니다" in said and "지웠습니다" in said
    data = _read(vault)
    assert set(data["llm"]["profiles"]) == {"main"}
    assert data["llm"]["fallback_order"] == ["main"]


def test_fallback_profile_reuses_main_key(vault, tmp_path):
    config.save_local(vault, _base())
    with mock_llm.start(require_key=LLM_KEY, **RICH) as server:
        url = f"{server.base_url}/v1"
        script = Script([url, "1", "y", url, "1"], [LLM_KEY, ""])
        code, said = _wizard(vault, script)
    assert code == 0, said
    assert "main 키" in script.secret_prompts[1]
    data = _read(vault)
    fallback = data["llm"]["profiles"]["fallback"]
    assert fallback["api_key_ref"] == "keyring:work-kb/llm" and fallback["detected"]["json_mode"] is True
    assert data["llm"]["fallback_order"] == ["main", "fallback"]
    assert _keyring(tmp_path) == {"work-kb\nllm": LLM_KEY}


def test_from_env_section_llm(vault, tmp_path, monkeypatch):
    config.save_local(vault, _base())
    with mock_llm.start(require_key=LLM_KEY, **RICH) as server:
        values = {name: "" for name in init.ENV_FIELDS.values()}
        values.update(KB_INIT_LLM_URL=f"{server.base_url}/v1", KB_INIT_LLM_MODEL="mock", KB_INIT_LLM_KEY=LLM_KEY)

        def no_ask(*args, **kwargs):
            raise AssertionError(args)

        lines: list[str] = []
        code = init.run_wizard(vault, section="llm", values=values, ask=no_ask, ask_secret=no_ask, out=lines.append, run_doctor=False)
    assert code == 0
    main = _read(vault)["llm"]["profiles"]["main"]
    assert main["model"] == "mock" and main["api_key_ref"] == "keyring:work-kb/llm"
    assert main["detected"]["reasoning_toggle"]["method"] == "param"
    assert _keyring(tmp_path)["work-kb\nllm"] == LLM_KEY
    assert LLM_KEY not in "\n".join(lines)


def test_from_env_network_failure_saves_nothing(vault):
    config.save_local(vault, _base())
    before = _local(vault).read_bytes()
    values = {name: "" for name in init.ENV_FIELDS.values()}
    values.update(KB_INIT_LLM_URL=f"http://127.0.0.1:{_closed_port()}/v1", KB_INIT_LLM_MODEL="mock")
    with pytest.raises(core.NetworkError):
        init.run_wizard(vault, section="llm", values=values, out=lambda _: None, run_doctor=False)
    assert _local(vault).read_bytes() == before


def test_section_llm_needs_existing_config(vault):
    with pytest.raises(core.ConfigError):
        init.run_wizard(vault, section="llm", ask=Script([], []).ask, out=lambda _: None, run_doctor=False)


def test_main_section_llm_dispatches(vault, llm, monkeypatch):
    """main(['--section', 'llm', '--from-env', '--non-interactive'])가 KB_INIT_LLM_*를 읽는다."""
    config.save_local(vault, _base())
    monkeypatch.setenv("KB_INIT_LLM_URL", f"{llm.base_url}/v1")
    monkeypatch.setenv("KB_INIT_LLM_MODEL", "mock")
    monkeypatch.setattr(doctor, "main", lambda argv: 0)
    assert init.main(["--root", str(vault), "--section", "llm", "--from-env", "--non-interactive"]) == 0
    assert _read(vault)["llm"]["profiles"]["main"]["detected"]["vision"] is True


# ---------------------------------------------------------------- 전체 마법사의 LLM 단계


@pytest.fixture
def jira():
    with mock_jira_mod.start(require_token=JIRA_TOKEN) as server:
        yield server


def test_full_wizard_llm_step(vault, jira, llm):
    script = Script(["home", jira.base_url, "1", "n", "", f"{llm.base_url}/v1", "1", "n"], [JIRA_TOKEN, ""])  # Confluence는 안 함
    code, said = _wizard(vault, script, section=None)
    assert code == 0, said
    prompts = [p for p, _ in script.asked]
    assert prompts[3].startswith("Confluence를 설정할까요?")
    assert prompts[4].startswith("LLM을 설정할까요?") and script.asked[4][1] == "y"  # 처음이면 기본값 y
    assert "[4] LLM" in said and "Phase 4" not in said
    data = _read(vault)
    assert data["confluence"] == {"enabled": False}
    assert data["llm"]["enabled"] is True
    assert data["llm"]["profiles"]["main"]["detected"]["json_mode"] is True


def test_full_wizard_llm_step_declined_keeps_existing(vault, jira):
    existing_llm = {"enabled": False, "profiles": {"mock": {"base_url": "http://127.0.0.1:9/v1", "model": "mock"}}}
    config.save_local(vault, {**_base(llm_section=existing_llm), "jira": {"base_url": jira.base_url, "project_keys": ["KB"], "pat_ref": "keyring:work-kb/jira"}})
    script = Script(["", "", "KB", "n", ""], [JIRA_TOKEN])  # Confluence는 안 함, LLM은 Enter
    code, said = _wizard(vault, script, section=None)
    assert code == 0, said
    assert script.asked[3][0].startswith("Confluence를 설정할까요?")
    assert script.asked[4][0].startswith("LLM을 설정할까요?") and script.asked[4][1] == "n"  # 기존 LLM 설정이 있으면 기본값 n
    assert "기존 LLM 설정을 그대로 둡니다" in said
    assert _read(vault)["llm"] == existing_llm


def test_full_wizard_from_env_without_llm_url_skips(vault, jira):
    values = {name: "" for name in init.ENV_FIELDS.values()}
    values.update(KB_INIT_ENVIRONMENT="home", KB_INIT_JIRA_URL=jira.base_url, KB_INIT_JIRA_PAT=JIRA_TOKEN, KB_INIT_JIRA_PROJECTS="KB")
    lines: list[str] = []
    assert init.run_wizard(vault, values=values, out=lines.append, run_doctor=False) == 0
    assert "KB_INIT_LLM_URL이 없어 LLM 단계를 건너뜁니다" in "\n".join(lines)
    assert "llm" not in _read(vault)


# ---------------------------------------------------------------- init --dev


def _dev_block(main: dict) -> dict:
    block = init.load_example_block("home")
    block["llm"]["profiles"]["main"] = main
    block["llm"]["profiles"].pop("fallback", None)
    block["llm"]["fallback_order"] = ["main"]
    return block


def _run_dev(vault, monkeypatch, block: dict) -> None:
    monkeypatch.setattr(init, "load_example_block", lambda name="home", path=None: json.loads(json.dumps(block)))
    monkeypatch.setattr(doctor, "main", lambda argv: 0)
    assert init.run_dev(vault, environ=dict(os.environ)) == 0


def test_dev_detects_main_when_key_resolves(vault, llm, monkeypatch, capsys):
    monkeypatch.setenv("DEEPINFRA_API_KEY", "dev-key-marker-0005")  # home의 env: 참조
    _run_dev(vault, monkeypatch, _dev_block({"base_url": f"{llm.base_url}/v1", "model": "mock", "api_key_ref": "env:DEEPINFRA_API_KEY"}))
    detected = _read(vault)["llm"]["profiles"]["main"]["detected"]
    assert detected["reasoning_toggle"]["method"] == "param" and detected["vision"] is True
    out = capsys.readouterr().out
    assert "LLM main 탐지: 추론 토글 param" in out
    assert "dev-key-marker-0005" not in out + _local(vault).read_text(encoding="utf-8")
    assert all(e["has_auth"] for e in llm.log)


def test_dev_marks_pending_without_key(vault, llm, monkeypatch, capsys):
    _run_dev(vault, monkeypatch, _dev_block({"base_url": f"{llm.base_url}/v1", "model": "mock", "api_key_ref": "env:DEEPINFRA_API_KEY"}))
    assert _read(vault)["llm"]["profiles"]["main"]["detected"] == "pending"
    assert llm.counts["chat"] == 0
    assert "detected: pending" in capsys.readouterr().out


def test_dev_marks_pending_when_detection_fails(vault, monkeypatch, capsys):
    monkeypatch.setenv("DEEPINFRA_API_KEY", "dev-key-marker-0006")
    url = f"http://127.0.0.1:{_closed_port()}/v1"
    _run_dev(vault, monkeypatch, _dev_block({"base_url": url, "model": "mock", "api_key_ref": "env:DEEPINFRA_API_KEY"}))
    assert _read(vault)["llm"]["profiles"]["main"]["detected"] == "pending"
    out = capsys.readouterr().out
    assert "실패해 미룹니다" in out and "dev-key-marker-0006" not in out


# ---------------------------------------------------------------- doctor 재검증(D48)·--redetect


def test_doctor_redetect_saves_then_verify_is_read_only(vault, llm):
    original = _profile_config(f"{llm.base_url}/v1")
    config.save_local(vault, original)
    report = _doctor(vault, redetect=True)
    state, text = _line(report, "탐지 main")
    assert state == "✓" and text.startswith("다시 탐지해 저장했습니다: 추론 토글 param reasoning_effort")
    data = _read(vault)
    assert data["jira"] == original["jira"]
    assert data["llm"]["profiles"]["main"]["detected"]["vision"] is True
    assert llm.counts["chat"] <= llm_detect.MAX_POSTS

    saved = _local(vault).read_bytes()
    before = llm.counts["chat"]
    report = _doctor(vault)
    state, text = _line(report, "탐지 main")
    assert state == "✓", text
    assert "호출 3회" in text and "저장 안 함" in text
    assert llm.counts["chat"] - before == 3
    assert _local(vault).read_bytes() == saved
    assert report.exit_code() in (0, 1)  # jira 비밀값 ✗는 이 테스트와 무관


def test_verify_uses_recorded_toggle_and_json_mode(vault, llm):
    config.save_local(vault, _profile_config(f"{llm.base_url}/v1"))
    _doctor(vault, redetect=True)
    start = len(llm.log)
    _doctor(vault)
    bodies = [e["json"] for e in llm.log[start:] if e["path"].endswith("/chat/completions")]
    assert len(bodies) == 3
    assert bodies[0]["reasoning_effort"] == "none" and bodies[0]["response_format"] == {"type": "json_object"}
    assert bodies[1]["reasoning_effort"] == "medium" and "response_format" not in bodies[1]
    assert bodies[2]["reasoning_effort"] == "none"
    assert any(part.get("type") == "image_url" for part in bodies[2]["messages"][-1]["content"])


def test_verify_mismatch_is_fail_within_three_calls(vault):
    claimed = {
        "context_tokens": 32768,
        "context_source": "models",
        "json_mode": True,
        "vision": True,
        "reasoning_toggle": {"method": "param", "param_name": "reasoning_effort", "on_value": "medium", "off_value": "none"},
        "baseline_reasoning": False,
    }
    with mock_llm.start() as server:  # 모든 토글·이미지를 조용히 무시하는 서버
        config.save_local(vault, _profile_config(f"{server.base_url}/v1", detected=claimed))
        saved = _local(vault).read_bytes()
        report = _doctor(vault)
        assert server.counts["chat"] == 3
    state, text = _line(report, "탐지 main")
    assert state == "✗"
    assert "추론 켜기가 듣지 않습니다" in text and "비전" in text and "--redetect" in text
    assert report.exit_code() == 1
    assert _local(vault).read_bytes() == saved


def test_verify_method_none_baseline_on_is_one_call(vault):
    with mock_llm.start(default_thinking=True) as server:  # 토글이 없고 늘 생각하는 서버(D41)
        config.save_local(vault, _profile_config(f"{server.base_url}/v1"))
        _doctor(vault, redetect=True)
        detected = _read(vault)["llm"]["profiles"]["main"]["detected"]
        assert detected["reasoning_toggle"]["method"] == "none" and detected["baseline_reasoning"] is True
        assert detected["vision"] is False
        before = server.counts["chat"]
        report = _doctor(vault)
        assert server.counts["chat"] - before == 1
    assert _line(report, "탐지 main")[0] == "✓"


def test_verify_detects_baseline_change(vault):
    recorded = {"json_mode": False, "vision": False, "reasoning_toggle": {"method": "none"}, "baseline_reasoning": False}
    with mock_llm.start(default_thinking=True) as server:
        config.save_local(vault, _profile_config(f"{server.base_url}/v1", detected=recorded))
        report = _doctor(vault)
        assert server.counts["chat"] == 1
    state, text = _line(report, "탐지 main")
    assert state == "✗" and "기준 상태가 기록(생각 꺼짐)과 다릅니다" in text


def test_pending_and_missing_detected_make_no_calls(vault, llm):
    config.save_local(vault, _profile_config(f"{llm.base_url}/v1", detected="pending", extra_profiles={"fallback": {"base_url": f"{llm.base_url}/v1", "model": "mock"}}))
    report = _doctor(vault)
    assert _line(report, "탐지 main") == ("-", doctor._PENDING)
    assert _line(report, "탐지 fallback")[1].startswith("탐지 안 됨")
    assert llm.counts["chat"] == 0


def test_unreachable_profile_is_not_verified(vault):
    detected = {"json_mode": False, "vision": False, "reasoning_toggle": {"method": "none"}, "baseline_reasoning": False}
    config.save_local(vault, _profile_config(f"http://127.0.0.1:{_closed_port()}/v1", detected=detected))
    report = _doctor(vault)
    assert _line(report, "LLM main")[0] == "✗"
    state, text = _line(report, "탐지 main")
    assert state == "-" and "연결되지 않아" in text


@pytest.mark.parametrize("redetect", [False, True])
def test_company_public_profile_blocks_all_chat(vault, llm, redetect):
    detected = {"json_mode": True, "vision": True, "reasoning_toggle": {"method": "none"}, "baseline_reasoning": False}
    cfg = _profile_config(
        f"{llm.base_url}/v1",
        detected=detected,
        environment="company",
        extra_profiles={"fallback": {"base_url": "https://api.openai.com/v1", "model": "x"}},
    )
    config.save_local(vault, cfg)
    saved = _local(vault).read_bytes()
    report = _doctor(vault, redetect=redetect)
    assert llm.counts["chat"] == 0
    state, text = _line(report, "탐지 main")
    assert state == "-" and "차단" in text
    assert report.exit_code() == 5
    assert _local(vault).read_bytes() == saved


def test_redetect_failure_is_fail_and_keeps_old_value(vault):
    old = {"json_mode": False, "vision": False, "reasoning_toggle": {"method": "none"}, "baseline_reasoning": False}
    with mock_llm.start(http_500_once=True) as server:
        server.faults = ["http_500_once"] * 5  # 기준선이 계속 500
        config.save_local(vault, _profile_config(f"{server.base_url}/v1", detected=old))
        report = _doctor(vault, redetect=True)
    state, text = _line(report, "탐지 main")
    assert state == "✗" and "다시 탐지하지 못했습니다" in text
    assert _read(vault)["llm"]["profiles"]["main"]["detected"] == old


def test_redetect_refused_for_agent(vault, monkeypatch):
    monkeypatch.setenv("KB_AGENT", "1")
    with pytest.raises(core.PolicyRefused):
        doctor.main(["--root", str(vault), "--redetect"])
    with pytest.raises(core.PolicyRefused):
        init.main(["--root", str(vault), "--redetect"])


def test_init_redetect_rejects_other_modes(vault):
    with pytest.raises(core.ConfigError):
        init.main(["--root", str(vault), "--redetect", "--section", "llm"])


def test_init_redetect_subprocess(run_kb, vault, llm):
    config.save_local(vault, _profile_config(f"{llm.base_url}/v1"))
    result = run_kb("init", "--redetect")
    assert "✓ 탐지 main: 다시 탐지해 저장했습니다" in result.stdout, result.stdout + result.stderr
    assert _read(vault)["llm"]["profiles"]["main"]["detected"]["json_mode"] is True
    agent = run_kb("doctor", "--redetect", env={"KB_AGENT": "1"})
    assert agent.returncode == 7


def test_summarize_detected():
    assert init.summarize_detected("pending") == "탐지 대기"
    assert init.summarize_detected(None) == "탐지 안 됨"
    text = init.summarize_detected(
        {"reasoning_toggle": {"method": "system_prefix"}, "baseline_reasoning": True, "json_mode": False, "vision": True,
         "context_tokens": 262144, "context_source": "assumed"}
    )
    assert text == "추론 토글 system_prefix, 기준 생각 켜짐, JSON 모드 아니오, 비전 예, 컨텍스트 262,144(assumed)"


@pytest.mark.parametrize("name", ["init.py", "doctor.py"])
def test_no_vendor_host_in_code(name):
    text = (Path(__file__).resolve().parents[1] / "scripts" / name).read_text(encoding="utf-8").lower()
    assert "deepinfra" not in text
