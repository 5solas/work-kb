"""맨 `kb.py init` 흐름: environment → Jira → Confluence → LLM → 저장 → doctor.

입력 함수를 주입해 프로세스 안에서 돌린다. 집 경로는 끝까지(doctor 포함), 회사 경로는 안내와 저장까지,
재실행 기본값과 중간 취소를 확인한다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

import init
import mock_jira as mock_jira_mod
from lib import config

TOKEN = "jira-pat-full-0002"  # 가짜 PAT
LINE = re.compile(r"^([✓✗-]) ([^:]+): (.*)$")


@pytest.fixture
def mock_jira():
    with mock_jira_mod.start(require_token=TOKEN) as server:
        yield server


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


def _local(vault: Path) -> Path:
    return vault / "config.local.yaml"


def _keyring(tmp_path: Path) -> dict[str, str]:
    path = tmp_path / "keyring.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _doctor_lines(stdout: str) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for line in stdout.splitlines():
        m = LINE.match(line)
        if m and m.group(2) not in out:
            out[m.group(2)] = (m.group(1), m.group(3))
    return out


def test_home_path_end_to_end(vault, tmp_path, mock_jira, capsys):
    script = Script(["home", mock_jira.base_url + "/", "1, 2", "n", "n"], [TOKEN])  # 마지막 둘: Confluence·LLM 설정 안 함
    out: list[str] = []
    code = init.run_wizard(vault, ask=script.ask, ask_secret=script.ask_secret, out=out.append)
    stdout = capsys.readouterr().out
    assert code == 0, "\n".join(out) + stdout

    # 묻는 순서: 환경 → 주소 → PAT(가림) → 프로젝트 → Confluence 여부 → LLM 여부. 첫 실행이라 환경 기본값은 안전 쪽(company)
    assert [p.split(" ")[0] for p, _ in script.asked] == ["환경", "Jira", "동기화할", "Confluence를", "LLM을"]
    assert script.asked[0][1] == "company"
    assert script.asked[3][1] == "y"  # Confluence 설정이 없으면 기본값은 설정하기(D59)
    assert script.asked[4][1] == "y"  # llm 구역이 없으면 기본값은 설정하기
    assert script.asked[1][1] is None
    assert len(script.secret_prompts) == 1 and "PAT" in script.secret_prompts[0]

    text = _local(vault).read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert data["environment"] == "home"
    assert data["jira"] == {
        "base_url": mock_jira.base_url,  # 끝의 / 제거
        "pat_ref": "keyring:work-kb/jira",
        "project_keys": ["KB", "OPS"],
        "sprint_field": "customfield_10020",
    }
    assert data["confluence"] == {"enabled": False}
    assert TOKEN not in text and TOKEN not in "\n".join(out) + stdout
    assert _keyring(tmp_path)["work-kb\njira"] == TOKEN

    said = "\n".join(out)
    assert "연결했습니다: 홍길동 (honggildong)" in said
    assert "공개 클라우드" not in said  # 집 환경은 안내 없음
    for label in ("jql_mine", "뷰 today", "뷰 blocked", "뷰 review"):
        assert f"✓ JQL {label}" in said
    # 저장 뒤 doctor가 돌았다
    lines = _doctor_lines(stdout)
    assert lines["config.local.yaml"] == ("✓", "environment: home")
    assert lines["Jira"][0] == "✓"
    assert lines["sprint 필드"] == ("-", "customfield_10020")
    assert lines["Confluence"] == ("-", "비활성")
    assert lines["LLM"] == ("-", "비활성")


def test_rerun_shows_existing_values_as_defaults(vault, tmp_path, mock_jira):
    first = Script(["home", mock_jira.base_url, "OPS", "n", "n"], [TOKEN])
    assert init.run_wizard(vault, ask=first.ask, ask_secret=first.ask_secret, out=lambda _: None, run_doctor=False) == 0
    saved = _local(vault).read_bytes()

    again = Script(["", "", "", "n", "n"], [""])  # 모두 Enter, Confluence·LLM은 설정 안 함(base_url·llm 구역이 없어 기본값이 y)
    out: list[str] = []
    assert init.run_wizard(vault, ask=again.ask, ask_secret=again.ask_secret, out=out.append, run_doctor=False) == 0
    assert [d for _, d in again.asked] == ["home", mock_jira.base_url, "OPS", "y", "y"]
    assert "비우면 저장된 값 유지" in again.secret_prompts[0]
    assert any("기존 설정을 기본값으로" in line for line in out)
    assert any("저장된 PAT" in line for line in out)
    assert _local(vault).read_bytes() == saved


def test_rerun_keeps_other_sections(vault, mock_jira):
    existing = {
        "environment": "home",
        "jira": {"base_url": mock_jira.base_url, "project_keys": ["KB"], "pat_ref": "keyring:work-kb/jira"},
        "confluence": {"enabled": True, "base_url": "http://127.0.0.1:9", "space_key": "~honggildong", "pat_ref": "keyring:work-kb/confluence"},
        "llm": {"enabled": False, "profiles": {"mock": {"base_url": "http://127.0.0.1:9/v1", "model": "mock"}}},
    }
    config.save_local(vault, existing)
    script = Script(["", "", "KB", "", ""], [TOKEN])  # Confluence·LLM 질문도 Enter: 기존 구역이 있으니 기본값 n
    out: list[str] = []
    assert init.run_wizard(vault, ask=script.ask, ask_secret=script.ask_secret, out=out.append, run_doctor=False) == 0
    assert [(p.split(" ")[0], d) for p, d in script.asked[-2:]] == [("Confluence를", "n"), ("LLM을", "n")]
    data = yaml.safe_load(_local(vault).read_text(encoding="utf-8"))
    assert data["confluence"] == existing["confluence"]
    assert data["llm"] == existing["llm"]
    assert "  기존 Confluence 설정을 그대로 둡니다" in out and "  기존 LLM 설정을 그대로 둡니다" in out


def test_company_path_warns_about_public_hosts(vault, mock_jira):
    example = init.load_example_block("home")  # 집에서 쓰던 공개 LLM 프로필이 남아 있는 경우
    example["jira"]["base_url"] = mock_jira.base_url
    config.save_local(vault, example)
    script = Script(["company", "", "KB", "n", "n"], [TOKEN])  # Confluence·LLM은 설정 안 함
    out: list[str] = []
    assert init.run_wizard(vault, ask=script.ask, ask_secret=script.ask_secret, out=out.append, run_doctor=False) == 0
    assert script.asked[0] == ("환경 (home=집 개발용, company=회사)", "home")
    said = "\n".join(out)
    assert "공개 클라우드 LLM 주소" in said
    assert "LLM 프로필 'main'이(가) 공개 호스트" in said
    assert "LLM 프로필 'mock'" not in said
    assert yaml.safe_load(_local(vault).read_text(encoding="utf-8"))["environment"] == "company"


def test_abort_midway_writes_nothing(vault, tmp_path, mock_jira):
    script = Script(["home", mock_jira.base_url], [])  # PAT 입력에서 끝남
    with pytest.raises(init.Cancelled):
        init.run_wizard(vault, ask=script.ask, ask_secret=script.ask_secret, out=lambda _: None)
    assert not _local(vault).exists()
    assert "work-kb\njira" not in _keyring(tmp_path)


def test_from_env_values_in_process(vault, mock_jira, monkeypatch):
    values = {
        "KB_INIT_ENVIRONMENT": "회사",
        "KB_INIT_JIRA_URL": mock_jira.base_url,
        "KB_INIT_JIRA_PAT": TOKEN,
        "KB_INIT_JIRA_PROJECTS": "ops",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    asked: list[str] = []

    def no_ask(prompt, default=None):  # 비대화형은 묻지 않는다
        asked.append(prompt)
        raise AssertionError(prompt)

    out: list[str] = []
    code = init.run_wizard(vault, values=values, ask=no_ask, ask_secret=no_ask, out=out.append, run_doctor=False)
    assert code == 0 and asked == []
    data = yaml.safe_load(_local(vault).read_text(encoding="utf-8"))
    assert data["environment"] == "company"
    assert data["jira"]["project_keys"] == ["OPS"]
