"""`kb.py init`의 Jira 단계(W2.5): --from-env, --section jira, 재실행 기본값, 취소, doctor의 JQL·sprint·버전 점검."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

import init
import mock_jira as mock_jira_mod
from lib import config, core
from lib import secrets as kb_secrets

TOKEN = "jira-pat-test-0001"  # 가짜 PAT. 모의 서버는 이 값만 받는다
LINE = re.compile(r"^([✓✗-]) ([^:]+): (.*)$")


@pytest.fixture
def mock_jira():
    with mock_jira_mod.start(require_token=TOKEN) as server:
        yield server


# ---------------------------------------------------------------- 도우미


def _env(mock, **extra: str) -> dict[str, str]:
    env = {
        "KB_INIT_ENVIRONMENT": "home",
        "KB_INIT_JIRA_URL": mock.base_url,
        "KB_INIT_JIRA_PAT": TOKEN,
        "KB_INIT_JIRA_PROJECTS": "KB, OPS",
    }
    env.update(extra)
    return env


def _local(vault: Path) -> Path:
    return vault / "config.local.yaml"


def _data(vault: Path) -> dict:
    return yaml.safe_load(_local(vault).read_text(encoding="utf-8"))


def _keyring(tmp_path: Path) -> dict[str, str]:
    path = tmp_path / "keyring.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _lines(stdout: str) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for line in stdout.splitlines():
        m = LINE.match(line)
        if m and m.group(2) not in out:
            out[m.group(2)] = (m.group(1), m.group(3))
    return out


def _searches(server) -> list[dict]:
    return [e for e in server.log if e["path"] == "/rest/api/2/search"]


class Script:
    """주입용 입력. 답이 바닥나면 EOFError(입력 끝)."""

    def __init__(self, answers: list[str], secrets: list[str] | None = None) -> None:
        self.answers = list(answers)
        self.secrets = list(secrets or [])
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


def _wizard(vault: Path, script: Script, out: list[str], **kw):
    return init.run_wizard(vault, ask=script.ask, ask_secret=script.ask_secret, out=out.append, run_doctor=False, **kw)


# ---------------------------------------------------------------- --non-interactive --from-env (하위 프로세스)


def test_from_env_writes_config_and_doctor_passes(run_kb, vault, tmp_path, mock_jira):
    result = run_kb("init", "--non-interactive", "--from-env", env=_env(mock_jira))
    assert result.returncode == 0, result.stdout + result.stderr
    text = _local(vault).read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert data["environment"] == "home"
    assert data["jira"]["base_url"] == mock_jira.base_url
    assert data["jira"]["pat_ref"] == "keyring:work-kb/jira"
    assert data["jira"]["project_keys"] == ["KB", "OPS"]
    assert data["jira"]["sprint_field"] == "customfield_10020"
    assert data["confluence"] == {"enabled": False}
    assert "llm" not in data
    # PAT는 자격 증명 저장소에만
    assert TOKEN not in text
    assert TOKEN not in result.stdout + result.stderr
    assert _keyring(tmp_path)["work-kb\njira"] == TOKEN
    # Confluence·LLM 단계는 Phase 안내만
    assert "Phase 5" in result.stdout and "Phase 4" in result.stdout
    # 이어서 doctor: Jira, 서버 버전, sprint 필드, JQL 4개
    lines = _lines(result.stdout)
    assert lines["Jira"][0] == "✓" and "홍길동" in lines["Jira"][1]
    assert lines["Jira 서버"][0] == "-" and lines["Jira 서버"][1].startswith("9.17.0")
    assert lines["sprint 필드"] == ("-", "customfield_10020")
    for name in ("JQL jql_mine", "JQL 뷰 today", "JQL 뷰 blocked", "JQL 뷰 review"):
        assert lines[name] == ("✓", "유효"), name
    assert any(e["query"].get("maxResults") == "0" for e in _searches(mock_jira))


def test_from_env_project_numbers(run_kb, vault, mock_jira):
    result = run_kb("init", "--non-interactive", "--from-env", env=_env(mock_jira, KB_INIT_JIRA_PROJECTS="2,1,2"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert _data(vault)["jira"]["project_keys"] == ["OPS", "KB"]


def test_from_env_company_prints_public_host_warning(run_kb, vault, mock_jira):
    result = run_kb("init", "--non-interactive", "--from-env", env=_env(mock_jira, KB_INIT_ENVIRONMENT="company"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "공개 클라우드 LLM" in result.stdout
    assert _data(vault)["environment"] == "company"


def test_from_env_bad_pat_exit_2_writes_nothing(run_kb, vault, mock_jira):
    bad = "wrong-pat-9999"
    result = run_kb("init", "--non-interactive", "--from-env", env=_env(mock_jira, KB_INIT_JIRA_PAT=bad))
    assert result.returncode == 2, result.stdout + result.stderr
    assert "401" in result.stderr
    assert bad not in result.stdout + result.stderr
    assert not _local(vault).exists()


def test_from_env_refused_port_exit_3(run_kb, vault, mock_jira):
    url = mock_jira.base_url
    mock_jira.stop()
    result = run_kb("init", "--non-interactive", "--from-env", env=_env(mock_jira, KB_INIT_JIRA_URL=url))
    assert result.returncode == 3, result.stdout + result.stderr
    assert not _local(vault).exists()


@pytest.mark.parametrize(
    "override, needle",
    [
        ({"KB_INIT_JIRA_PROJECTS": "KB, NOPE"}, "KB_INIT_JIRA_PROJECTS"),
        ({"KB_INIT_JIRA_PROJECTS": "7"}, "KB_INIT_JIRA_PROJECTS"),
        ({"KB_INIT_JIRA_URL": ""}, "KB_INIT_JIRA_URL"),
        ({"KB_INIT_JIRA_URL": "jira.example.invalid"}, "KB_INIT_JIRA_URL"),
        ({"KB_INIT_ENVIRONMENT": ""}, "KB_INIT_ENVIRONMENT"),
        ({"KB_INIT_ENVIRONMENT": "office"}, "KB_INIT_ENVIRONMENT"),
        ({"KB_INIT_JIRA_PAT": ""}, "KB_INIT_JIRA_PAT"),
    ],
)
def test_from_env_bad_or_missing_values_exit_4(run_kb, vault, mock_jira, override, needle):
    result = run_kb("init", "--non-interactive", "--from-env", env=_env(mock_jira, **override))
    assert result.returncode == 4, result.stdout + result.stderr
    assert needle in result.stderr
    assert not _local(vault).exists()


def test_rerun_uses_existing_values(run_kb, vault, tmp_path, mock_jira):
    assert run_kb("init", "--non-interactive", "--from-env", env=_env(mock_jira)).returncode == 0
    first = _local(vault).read_bytes()
    # 값을 하나도 주지 않으면 기존 설정과 저장된 PAT를 쓴다
    empty = {name: "" for name in init.ENV_FIELDS.values()}
    result = run_kb("init", "--non-interactive", "--from-env", env=empty)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "저장된 PAT" in result.stdout
    assert _local(vault).read_bytes() == first


@pytest.mark.parametrize("flags", [("--non-interactive",), ("--from-env",), ("--dev", "--non-interactive")])
def test_flag_combinations_refused(run_kb, vault, mock_jira, flags):
    result = run_kb("init", *flags, env=_env(mock_jira))
    assert result.returncode == 4, result.stdout + result.stderr
    assert not _local(vault).exists()


@pytest.mark.parametrize("section, phase", [("confluence", "Phase 5"), ("llm", "Phase 4")])
def test_later_sections_print_phase_message(run_kb, vault, section, phase):
    result = run_kb("init", "--section", section)
    assert result.returncode == 4
    assert phase in result.stdout
    assert not _local(vault).exists()


def test_agent_refused(run_kb, vault, mock_jira):
    result = run_kb("init", "--non-interactive", "--from-env", env=dict(_env(mock_jira), KB_AGENT="1"))
    assert result.returncode == 7
    assert not _local(vault).exists()


def test_interactive_eof_cancels(run_kb, vault):
    result = run_kb("init", stdin="home\n")  # 환경만 답하고 입력이 끝남
    assert result.returncode == 1, result.stdout + result.stderr
    assert "취소" in result.stderr
    assert not _local(vault).exists()


# ---------------------------------------------------------------- --section jira


def _existing_config(vault: Path, mock_url: str) -> dict:
    data = {
        "environment": "home",
        "jira": {
            "base_url": mock_url,
            "project_keys": ["KB"],
            "pat_ref": "keyring:work-kb/jira",
            "trust_env": False,
            "views": {"mine_kb": "project = KB AND statusCategory != Done"},
        },
        "confluence": {"enabled": False, "space_key": "~honggildong", "parent_page_id": "123"},
        "llm": {"enabled": False, "profiles": {"mock": {"base_url": "http://127.0.0.1:9/v1", "model": "mock"}}},
    }
    config.save_local(vault, data)
    return data


def test_section_jira_only_changes_jira(run_kb, vault, mock_jira):
    before = _existing_config(vault, "http://127.0.0.1:9")
    env = {"KB_INIT_JIRA_URL": mock_jira.base_url, "KB_INIT_JIRA_PAT": TOKEN, "KB_INIT_JIRA_PROJECTS": "OPS"}
    result = run_kb("init", "--section", "jira", "--non-interactive", "--from-env", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[1] 환경" not in result.stdout and "[3] Confluence" not in result.stdout
    after = _data(vault)
    assert after["environment"] == "home"
    assert after["confluence"] == before["confluence"]
    assert after["llm"] == before["llm"]
    jira = after["jira"]
    assert jira["base_url"] == mock_jira.base_url
    assert jira["project_keys"] == ["OPS"]
    assert jira["trust_env"] is False
    assert jira["views"] == before["jira"]["views"]
    # 덮어쓴 뷰도 doctor가 확인한다
    assert _lines(result.stdout)["JQL 뷰 mine_kb"] == ("✓", "유효")


def test_section_jira_needs_existing_config(run_kb, vault, mock_jira):
    result = run_kb("init", "--section", "jira", "--non-interactive", "--from-env", env=_env(mock_jira))
    assert result.returncode == 4
    assert "config.local.yaml" in result.stderr
    assert not _local(vault).exists()


# ---------------------------------------------------------------- 대화형 (주입, 프로세스 안)


def test_interactive_retries_bad_pat_then_saves(vault, tmp_path, mock_jira):
    script = Script(["home", mock_jira.base_url, "", "2"], ["wrong-pat-0000", TOKEN])
    out: list[str] = []
    assert _wizard(vault, script, out) == 0
    text = "\n".join(out)
    assert "연결하지 못했습니다" in text and "401" in text
    assert "wrong-pat-0000" not in text and TOKEN not in text
    # 두 번째 주소 질문의 기본값은 처음 답
    url_questions = [d for p, d in script.asked if p.startswith("Jira 주소")]
    assert url_questions == [None, mock_jira.base_url]
    assert _data(vault)["jira"]["project_keys"] == ["OPS"]
    assert _keyring(tmp_path)["work-kb\njira"] == TOKEN


def test_interactive_project_choice_reasks(vault, mock_jira):
    script = Script(["home", mock_jira.base_url, "9", "ops, 1, OPS"], [TOKEN])
    out: list[str] = []
    assert _wizard(vault, script, out) == 0
    text = "\n".join(out)
    assert "1. KB" in text and "2. OPS" in text
    assert "번호 9은(는) 목록에 없습니다" in text
    assert _data(vault)["jira"]["project_keys"] == ["OPS", "KB"]


def test_interactive_gives_up_after_three_bad_answers(vault, mock_jira):
    script = Script(["office", "집2", "?"], [])
    with pytest.raises(core.ConfigError):
        _wizard(vault, script, [])
    assert not _local(vault).exists()


def test_interactive_eof_mid_jira_keeps_existing_file(vault, mock_jira):
    _existing_config(vault, mock_jira.base_url)
    original = _local(vault).read_bytes()
    script = Script(["company", mock_jira.base_url], [TOKEN])  # 프로젝트 질문에서 입력 끝
    with pytest.raises(init.Cancelled) as info:
        _wizard(vault, script, [])
    assert info.value.exit_code == 1
    assert "취소" in str(info.value)
    assert _local(vault).read_bytes() == original


def test_jql_problems_are_reported_but_saved(vault, mock_jira):
    _existing_config(vault, mock_jira.base_url)
    data = yaml.safe_load(_local(vault).read_text(encoding="utf-8"))
    data["jira"]["views"]["broken"] = "status = = Blocked"
    config.save_local(vault, data)
    kb_secrets.store("work-kb", "jira", TOKEN)
    script = Script(["", "", ""], [""])  # 모두 기존 값
    out: list[str] = []
    assert _wizard(vault, script, out, section="jira") == 0
    text = "\n".join(out)
    assert "✗ JQL 뷰 broken" in text
    assert "✓ JQL 뷰 mine_kb" in text
    assert _data(vault)["jira"]["views"]["broken"] == "status = = Blocked"


def test_parse_project_choice():
    projects = [{"key": "KB"}, {"key": "OPS"}]
    assert init.parse_project_choice("1, ops 2", projects) == ["KB", "OPS"]
    assert init.parse_project_choice("OPS,KB", projects) == ["OPS", "KB"]
    assert init.parse_project_choice("NEW1", []) == ["NEW1"]  # 목록이 안 보이면 형식만
    for raw in ("", "0", "3", "ZZ", "kb-1"):
        with pytest.raises(core.ConfigError):
            init.parse_project_choice(raw, projects)


# ---------------------------------------------------------------- doctor


def _doctor_config(vault: Path, mock, **jira_extra) -> None:
    jira = {"base_url": mock.base_url, "project_keys": ["KB", "OPS"], "pat_ref": "keyring:work-kb/jira"}
    jira.update(jira_extra)
    config.save_local(vault, {"environment": "home", "jira": jira, "confluence": {"enabled": False}})
    kb_secrets.store("work-kb", "jira", TOKEN)


def test_doctor_reports_jira_message_for_bad_view(run_kb, vault, mock_jira):
    bad = "status = = Blocked"
    _doctor_config(vault, mock_jira, views={"broken": bad})
    from lib import jira_client

    expected = jira_client.JiraClient(mock_jira.base_url, TOKEN).validate_jql(bad)
    assert expected
    result = run_kb("doctor")
    assert result.returncode == 1, result.stdout
    lines = _lines(result.stdout)
    state, text = lines["JQL 뷰 broken"]
    assert state == "✗" and expected in text and "jira.views.broken" in text
    assert lines["JQL 뷰 today"] == ("✓", "유효")
    assert lines["JQL jql_mine"] == ("✓", "유효")


def test_doctor_empty_jql_mine_fails(run_kb, vault, mock_jira):
    _doctor_config(vault, mock_jira, jql_mine="")
    result = run_kb("doctor")
    assert _lines(result.stdout)["JQL jql_mine"][0] == "✗"
    assert result.returncode == 1


def test_doctor_without_sprint_field(run_kb, vault):
    with mock_jira_mod.start(require_token=TOKEN, no_sprint_field=True) as server:
        _doctor_config(vault, server, sprint_field="customfield_10020")
        result = run_kb("doctor")
    lines = _lines(result.stdout)
    state, text = lines["sprint 필드"]
    assert state == "-" and text.startswith("없음") and "customfield_10020" in text
    # sprint 필드가 없는 Jira는 today 뷰의 `sprint in openSprints()`를 거부한다. Jira 메시지를 보여 준다
    state, text = lines["JQL 뷰 today"]
    assert state == "✗" and "sprint" in text and "jira.views.today" in text
    assert lines["JQL 뷰 blocked"] == ("✓", "유효")
    assert result.returncode == 1, result.stdout


def test_doctor_skips_details_when_jira_down(run_kb, vault, mock_jira):
    _doctor_config(vault, mock_jira)
    mock_jira.stop()
    result = run_kb("doctor")
    lines = _lines(result.stdout)
    assert lines["Jira"][0] == "✗"
    assert not [n for n in lines if n.startswith(("JQL", "sprint", "Jira 서버"))]
    assert sum(1 for s, _ in lines.values() if s == "✗") == 1
