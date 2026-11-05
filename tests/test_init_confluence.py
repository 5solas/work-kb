"""`kb.py init`의 Confluence 단계(W5.3)와 doctor의 Confluence 세부 점검.

- PAT는 `/rest/api/user/current`로 확인한 뒤에만 저장한다(Jira와 같은 방식). 익명 응답은 인증 실패.
- 개인 스페이스 탐지 순서(D55): user/current → `space?spaceKey=~username` → `~userKey` → `type=personal` 목록(번호, 기본값 없음).
- 부모 페이지는 선택. `GET /rest/api/content/{id}`로 같은 스페이스의 페이지인지 확인한다.
- `enabled: true`가 기본이다(D59). 푸시는 하지 않는다.
- doctor: 스페이스 있음, 부모 페이지 읽힘, push_globs에 맞는 파일 수('-').

Confluence는 이 파일 안의 작은 가짜 서버로 흉내 낸다(사람·스페이스·페이지를 시험마다 바꾸려고).
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest
import yaml

import doctor
import init
import mock_jira as mock_jira_mod
from lib import config, core
from lib import secrets as kb_secrets

JIRA_TOKEN = "jira-pat-conf-0003"
CONF_TOKEN = "conf-pat-test-0004"  # 가짜 PAT. 가짜 서버는 이 값만 받는다
LINE = re.compile(r"^([✓✗-]) ([^:]+): (.*)$")
ME = {"type": "known", "username": "honggildong", "userKey": "8a7f1c2e3d4b5a6f", "displayName": "홍길동"}
MY_SPACE = {"id": 1, "key": "~honggildong", "name": "홍길동", "type": "personal"}


# ---------------------------------------------------------------- 가짜 Confluence


class FakeConfluence:
    """127.0.0.1의 작은 Confluence DC 흉내. `spaces`·`pages`·`user`를 시험에서 바꾼다."""

    def __init__(self, *, token: str = CONF_TOKEN) -> None:
        self.token = token
        self.user: dict = dict(ME)
        self.spaces: list[dict] = [dict(MY_SPACE)]
        self.pages: dict[str, dict] = {}
        self.redirect = False
        self.log: list[tuple[str, str, dict]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server 이름
                parts = urlsplit(self.path)
                query = dict(parse_qsl(parts.query, keep_blank_values=True))
                fake.log.append(("GET", parts.path, query))
                status, body, headers = fake.handle(parts.path, query, self.headers.get("Authorization") or "")
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if not self.thread.is_alive():
            return
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)

    def add_page(self, page_id: str, title: str, space_key: str) -> None:
        self.pages[page_id] = {"id": page_id, "type": "page", "title": title, "space": {"key": space_key}, "version": {"number": 3}}

    def paths(self) -> list[tuple[str, dict]]:
        return [(p, q) for _, p, q in self.log]

    def handle(self, path: str, query: dict, auth: str):
        if auth != f"Bearer {self.token}":
            return 401, {"message": "인증이 필요합니다"}, {}
        if self.redirect:
            return 302, {}, {"Location": "https://login.example.invalid/sso"}
        if path == "/rest/api/user/current":
            return 200, self.user, {}
        if path == "/rest/api/space":
            if "spaceKey" in query:
                found = [s for s in self.spaces if s["key"] == query["spaceKey"]]
                return 200, {"results": found, "size": len(found)}, {}
            kind = query.get("type")
            items = [s for s in self.spaces if kind is None or s.get("type") == kind]
            start, limit = int(query.get("start", 0)), int(query.get("limit", 25))
            page = items[start : start + limit]
            return 200, {"results": page, "start": start, "limit": limit, "size": len(page)}, {}
        m = re.fullmatch(r"/rest/api/content/(\d+)", path)
        if m:
            page = self.pages.get(m.group(1))
            return (200, page, {}) if page else (404, {"message": "없는 페이지"}, {})
        return 404, {"message": f"없는 경로 {path}"}, {}


@pytest.fixture
def conf():
    server = FakeConfluence()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def mock_jira():
    with mock_jira_mod.start(require_token=JIRA_TOKEN) as server:
        yield server


# ---------------------------------------------------------------- 도우미


class Script:
    """주입용 입력. 묻는 순서와 기본값을 기록한다. 답이 바닥나면 EOFError."""

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


def _env(jira, conf, **extra: str) -> dict[str, str]:
    env = {
        "KB_INIT_ENVIRONMENT": "home",
        "KB_INIT_JIRA_URL": jira.base_url,
        "KB_INIT_JIRA_PAT": JIRA_TOKEN,
        "KB_INIT_JIRA_PROJECTS": "KB",
        "KB_INIT_CONFLUENCE_URL": conf.base_url,
        "KB_INIT_CONFLUENCE_PAT": CONF_TOKEN,
    }
    env.update(extra)
    return env


def _values(conf, **extra: str) -> dict[str, str]:
    """--section confluence --from-env 값(프로세스 안)."""
    values = {name: "" for name in init.ENV_FIELDS.values()}
    values.update(KB_INIT_CONFLUENCE_URL=conf.base_url, KB_INIT_CONFLUENCE_PAT=CONF_TOKEN)
    values.update(extra)
    return values


def _base(vault: Path, jira_url: str = "http://127.0.0.1:9", confluence: dict | None = None) -> dict:
    data = {
        "environment": "home",
        "jira": {"base_url": jira_url, "project_keys": ["KB"], "pat_ref": "keyring:work-kb/jira"},
        "confluence": confluence if confluence is not None else {"enabled": False},
        "llm": {"enabled": False, "profiles": {"mock": {"base_url": "http://127.0.0.1:9/v1", "model": "mock"}}},
    }
    config.save_local(vault, data)
    return data


def _no_ask(*args, **kwargs):
    raise AssertionError(f"비대화형인데 물었습니다: {args}")


def _section(vault: Path, values: dict, out: list[str] | None = None) -> int:
    return init.run_wizard(
        vault, section="confluence", values=values, ask=_no_ask, ask_secret=_no_ask,
        out=(out.append if out is not None else lambda _: None), run_doctor=False,
    )


def _interactive(vault: Path, script: Script, out: list[str], section: str | None = "confluence") -> int:
    return init.run_wizard(vault, section=section, ask=script.ask, ask_secret=script.ask_secret, out=out.append, run_doctor=False)


# ---------------------------------------------------------------- 전체 마법사 --from-env (하위 프로세스, doctor까지)


def test_from_env_full_wizard_detects_personal_space_and_doctor_passes(run_kb, vault, tmp_path, mock_jira, conf):
    (vault / "wiki" / "projects" / "KB.md").write_text("# KB\n", encoding="utf-8")
    (vault / "wiki" / "decisions.md").write_text("# 결정\n", encoding="utf-8")
    result = run_kb("init", "--non-interactive", "--from-env", env=_env(mock_jira, conf))
    assert result.returncode == 0, result.stdout + result.stderr
    text = _local(vault).read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert data["confluence"] == {
        "enabled": True,
        "base_url": conf.base_url,
        "space_key": "~honggildong",
        "parent_page_id": "",
        "pat_ref": "keyring:work-kb/confluence",
    }
    # PAT는 자격 증명 저장소에만
    assert CONF_TOKEN not in text and CONF_TOKEN not in result.stdout + result.stderr
    assert _keyring(tmp_path)["work-kb\nconfluence"] == CONF_TOKEN
    assert "개인 스페이스: ~honggildong (홍길동)" in result.stdout
    assert "Phase 5" not in result.stdout
    # D55: user/current 다음 spaceKey=~username. 찾았으니 type=personal 목록은 부르지 않는다
    wizard_calls = conf.paths()[:2]
    assert wizard_calls == [("/rest/api/user/current", {}), ("/rest/api/space", {"spaceKey": "~honggildong"})]
    assert not any(q.get("type") == "personal" for _, q in conf.paths())
    # doctor
    lines = _lines(result.stdout)
    assert lines["Confluence"][0] == "✓" and "honggildong" in lines["Confluence"][1]
    assert lines["Confluence 스페이스"] == ("✓", "~honggildong (홍길동)")
    assert lines["Confluence 부모 페이지"] == ("-", "없음 (스페이스 홈 아래에 만듭니다)")
    state, said = lines["Confluence push_globs"]
    assert state == "-" and said.startswith("2개 파일")


def test_from_env_full_wizard_without_confluence_url_skips(vault, mock_jira):
    values = {name: "" for name in init.ENV_FIELDS.values()}
    values.update(KB_INIT_ENVIRONMENT="home", KB_INIT_JIRA_URL=mock_jira.base_url, KB_INIT_JIRA_PAT=JIRA_TOKEN, KB_INIT_JIRA_PROJECTS="KB")
    out: list[str] = []
    assert init.run_wizard(vault, values=values, ask=_no_ask, ask_secret=_no_ask, out=out.append, run_doctor=False) == 0
    assert "  KB_INIT_CONFLUENCE_URL이 없어 Confluence 단계를 건너뜁니다 (Confluence 비활성)" in out
    assert _data(vault)["confluence"] == {"enabled": False}


def test_from_env_bad_pat_exit_2_writes_nothing(run_kb, vault, tmp_path, mock_jira, conf):
    kb_secrets.store("work-kb", "confluence", CONF_TOKEN)
    bad = "wrong-conf-pat-9999"
    result = run_kb("init", "--non-interactive", "--from-env", env=_env(mock_jira, conf, KB_INIT_CONFLUENCE_PAT=bad))
    assert result.returncode == 2, result.stdout + result.stderr
    assert "401" in result.stderr
    assert bad not in result.stdout + result.stderr
    assert not _local(vault).exists()
    # Jira PAT도 Confluence 실패로 저장되지 않는다(마지막에 한꺼번에 저장)
    assert _keyring(tmp_path) == {"work-kb\nconfluence": CONF_TOKEN}


def test_section_confluence_cli(run_kb, vault, tmp_path, conf):
    before = _base(vault)
    env = {"KB_INIT_CONFLUENCE_URL": conf.base_url, "KB_INIT_CONFLUENCE_PAT": CONF_TOKEN}
    result = run_kb("init", "--section", "confluence", "--non-interactive", "--from-env", env=env)
    # Jira 주소가 죽어 있어 doctor는 ✗로 끝나지만 Confluence 구역은 저장된다
    assert result.returncode == 1, result.stdout + result.stderr
    assert "[Confluence]" in result.stdout and "[1] 환경" not in result.stdout
    after = _data(vault)
    assert after["jira"] == before["jira"] and after["llm"] == before["llm"]
    assert after["confluence"]["enabled"] is True and after["confluence"]["space_key"] == "~honggildong"
    lines = _lines(result.stdout)
    assert lines["Confluence"][0] == "✓" and lines["Confluence 스페이스"][0] == "✓"


def test_section_confluence_needs_existing_config(run_kb, vault, conf):
    env = {"KB_INIT_CONFLUENCE_URL": conf.base_url, "KB_INIT_CONFLUENCE_PAT": CONF_TOKEN}
    result = run_kb("init", "--section", "confluence", "--non-interactive", "--from-env", env=env)
    assert result.returncode == 4
    assert "config.local.yaml" in result.stderr
    assert not _local(vault).exists()


# ---------------------------------------------------------------- --section confluence (프로세스 안)


def test_section_keeps_other_confluence_keys_and_order(vault, conf):
    _base(vault, confluence={"enabled": False, "title_prefix": "[지식] ", "trust_env": False})
    assert _section(vault, _values(conf)) == 0
    block = _data(vault)["confluence"]
    assert list(block)[:5] == ["enabled", "base_url", "space_key", "parent_page_id", "pat_ref"]
    assert block["enabled"] is True
    assert block["title_prefix"] == "[지식] " and block["trust_env"] is False


def test_user_key_space_used_when_username_space_missing(vault, conf):
    _base(vault)
    conf.spaces = [{"key": "~8a7f1c2e3d4b5a6f", "name": "홍길동", "type": "personal"}, {"key": "~other", "name": "남", "type": "personal"}]
    assert _section(vault, _values(conf)) == 0
    assert _data(vault)["confluence"]["space_key"] == "~8a7f1c2e3d4b5a6f"
    keys = [q.get("spaceKey") for p, q in conf.paths() if p == "/rest/api/space"]
    assert keys == ["~honggildong", "~8a7f1c2e3d4b5a6f"]


def test_listing_needs_explicit_space_when_non_interactive(vault, conf):
    _base(vault)
    before = _local(vault).read_bytes()
    conf.spaces = [{"key": "~kimcs", "name": "김철수", "type": "personal"}, {"key": "~leeyh", "name": "이영희", "type": "personal"}]
    with pytest.raises(core.ConfigError) as info:
        _section(vault, _values(conf))
    assert "KB_INIT_CONFLUENCE_SPACE" in str(info.value)
    assert _local(vault).read_bytes() == before  # 첫 번째를 몰래 고르지 않는다(D55)


@pytest.mark.parametrize("choice, expected", [("2", "~leeyh"), ("~kimcs", "~kimcs"), ("TEAM", "TEAM")])
def test_explicit_space_from_env(vault, conf, choice, expected):
    _base(vault)
    conf.spaces = [
        {"key": "~kimcs", "name": "김철수", "type": "personal"},
        {"key": "~leeyh", "name": "이영희", "type": "personal"},
        {"key": "TEAM", "name": "팀", "type": "global"},
    ]
    assert _section(vault, _values(conf, KB_INIT_CONFLUENCE_SPACE=choice)) == 0
    assert _data(vault)["confluence"]["space_key"] == expected


def test_explicit_space_overrides_detection(vault, conf):
    _base(vault)
    conf.spaces.append({"key": "TEAM", "name": "팀", "type": "global"})
    assert _section(vault, _values(conf, KB_INIT_CONFLUENCE_SPACE="TEAM")) == 0
    assert _data(vault)["confluence"]["space_key"] == "TEAM"


def test_explicit_unknown_space_exit_4(vault, conf):
    _base(vault)
    with pytest.raises(core.ConfigError) as info:
        _section(vault, _values(conf, KB_INIT_CONFLUENCE_SPACE="NOPE"))
    assert info.value.exit_code == 4
    assert "KB_INIT_CONFLUENCE_SPACE" in str(info.value) and "NOPE" in str(info.value)


def test_interactive_numbered_choice_without_default(vault, tmp_path, conf):
    _base(vault)
    conf.spaces = [{"key": "~kimcs", "name": "김철수", "type": "personal"}, {"key": "~leeyh", "name": "이영희", "type": "personal"}]
    script = Script([conf.base_url, "9", "2", ""], [CONF_TOKEN])
    out: list[str] = []
    assert _interactive(vault, script, out) == 0
    said = "\n".join(out)
    assert "1. ~kimcs (김철수)" in said and "2. ~leeyh (이영희)" in said
    assert "다른 사람의 개인 스페이스일 수 있으니" in said
    assert "번호 9은(는) 목록에 없습니다" in said
    space_questions = [d for p, d in script.asked if p.startswith("쓸 스페이스")]
    assert space_questions == [None, None]  # 기본값 없음
    assert _data(vault)["confluence"]["space_key"] == "~leeyh"
    assert _keyring(tmp_path)["work-kb\nconfluence"] == CONF_TOKEN


def test_personal_list_is_paged(vault, conf):
    _base(vault)
    conf.spaces = [{"key": f"~user{i:02d}", "name": f"사람{i}", "type": "personal"} for i in range(30)]
    script = Script([conf.base_url, "27", ""], [CONF_TOKEN])
    out: list[str] = []
    assert _interactive(vault, script, out) == 0
    assert _data(vault)["confluence"]["space_key"] == "~user26"
    starts = [q.get("start") for p, q in conf.paths() if q.get("type") == "personal"]
    assert starts == ["0", "25"]
    assert "30. ~user29 (사람29)" in "\n".join(out)


def test_no_personal_space_tells_user_to_create_one(vault, conf):
    _base(vault)
    conf.spaces = [{"key": "TEAM", "name": "팀", "type": "global"}]
    script = Script([conf.base_url, "TEAM", ""], [CONF_TOKEN])
    out: list[str] = []
    assert _interactive(vault, script, out) == 0
    assert any("개인 스페이스를 만든 뒤" in line for line in out)
    assert _data(vault)["confluence"]["space_key"] == "TEAM"


# ---------------------------------------------------------------- 부모 페이지


def test_parent_page_validated_and_saved(vault, conf):
    _base(vault)
    conf.add_page("4242", "업무 지식", "~honggildong")
    out: list[str] = []
    assert _section(vault, _values(conf, KB_INIT_CONFLUENCE_PARENT="4242"), out) == 0
    assert _data(vault)["confluence"]["parent_page_id"] == "4242"
    assert "  부모 페이지: 업무 지식 (4242)" in out
    assert ("/rest/api/content/4242", {"expand": "space,version"}) in conf.paths()


@pytest.mark.parametrize(
    "parent, needle",
    [("777", "찾지 못했습니다"), ("5000", "스페이스 TEAM에 있습니다"), ("abc", "페이지 ID가 아닙니다")],
)
def test_bad_parent_exit_4(vault, conf, parent, needle):
    _base(vault)
    conf.add_page("5000", "남의 페이지", "TEAM")
    before = _local(vault).read_bytes()
    with pytest.raises(core.ConfigError) as info:
        _section(vault, _values(conf, KB_INIT_CONFLUENCE_PARENT=parent))
    assert needle in str(info.value) and "KB_INIT_CONFLUENCE_PARENT" in str(info.value)
    assert _local(vault).read_bytes() == before


def test_interactive_parent_url_reask_and_clear(vault, conf):
    conf.add_page("4242", "업무 지식", "~honggildong")
    _base(vault, confluence={"enabled": True, "base_url": conf.base_url, "space_key": "~honggildong", "parent_page_id": "4242", "pat_ref": "keyring:work-kb/confluence"})
    kb_secrets.store("work-kb", "confluence", CONF_TOKEN)
    # 1) 주소 기본값 → 저장된 PAT → 부모: 잘못된 ID → pageId 주소
    script = Script(["", "999", f"{conf.base_url}/pages/viewpage.action?pageId=4242"], [""])
    out: list[str] = []
    assert _interactive(vault, script, out) == 0
    parent_questions = [d for p, d in script.asked if p.startswith("부모 페이지")]
    assert parent_questions == ["4242", "4242"]
    assert any("부모 페이지 999를 찾지 못했습니다" in line for line in out)
    assert _data(vault)["confluence"]["parent_page_id"] == "4242"
    # 2) '-'면 스페이스 홈
    script = Script(["", "-"], [""])
    assert _interactive(vault, script, []) == 0
    assert _data(vault)["confluence"]["parent_page_id"] == ""


def test_existing_parent_is_default_from_env(vault, conf):
    conf.add_page("4242", "업무 지식", "~honggildong")
    _base(vault, confluence={"enabled": True, "base_url": conf.base_url, "space_key": "~honggildong", "parent_page_id": "4242", "pat_ref": "keyring:work-kb/confluence"})
    kb_secrets.store("work-kb", "confluence", CONF_TOKEN)
    before = _local(vault).read_bytes()
    assert _section(vault, {name: "" for name in init.ENV_FIELDS.values()}) == 0
    assert _local(vault).read_bytes() == before  # 다시 실행해도 같다


def test_parse_parent():
    assert init.parse_parent("") == ""
    assert init.parse_parent(" - ") == ""
    assert init.parse_parent("00123") == "123"
    assert init.parse_parent("https://c.example.invalid/pages/viewpage.action?spaceKey=X&pageId=98765") == "98765"
    for raw in ("abc", "12a", "１２", "https://c.example.invalid/display/X/Title"):
        with pytest.raises(core.ConfigError):
            init.parse_parent(raw)


# ---------------------------------------------------------------- 인증·오류


def test_anonymous_user_is_auth_error(vault, tmp_path, conf):
    _base(vault)
    conf.user = {"type": "anonymous", "displayName": "Anonymous"}
    with pytest.raises(core.AuthError) as info:
        _section(vault, _values(conf))
    assert info.value.exit_code == 2 and "익명" in str(info.value)
    assert "work-kb\nconfluence" not in _keyring(tmp_path)
    assert [p for p, _ in conf.paths()] == ["/rest/api/user/current"]  # 스페이스를 찾지 않는다


def test_redirect_not_followed(vault, conf):
    _base(vault)
    conf.redirect = True
    with pytest.raises(core.ConfigError) as info:
        _section(vault, _values(conf))
    assert "다른 주소" in str(info.value)
    assert len(conf.log) == 1


def test_interactive_retries_bad_pat_then_saves(vault, tmp_path, conf):
    _base(vault)
    script = Script([conf.base_url, "", ""], ["wrong-conf-0000", CONF_TOKEN])
    out: list[str] = []
    assert _interactive(vault, script, out) == 0
    said = "\n".join(out)
    assert "연결하지 못했습니다" in said and "401" in said
    assert "wrong-conf-0000" not in said and CONF_TOKEN not in said
    assert [d for p, d in script.asked if p.startswith("Confluence 주소")] == [None, conf.base_url]
    assert _keyring(tmp_path)["work-kb\nconfluence"] == CONF_TOKEN


def test_eof_mid_confluence_keeps_file_and_keyring(vault, tmp_path, conf):
    _base(vault)
    before = _local(vault).read_bytes()
    kb_secrets.store("work-kb", "confluence", "prior-conf-1111")
    conf.spaces = [{"key": "~kimcs", "name": "김철수", "type": "personal"}]
    script = Script([conf.base_url], [CONF_TOKEN])  # PAT는 확인됐지만 스페이스 질문에서 입력 끝
    with pytest.raises(init.Cancelled):
        _interactive(vault, script, [])
    assert _local(vault).read_bytes() == before
    assert _keyring(tmp_path) == {"work-kb\nconfluence": "prior-conf-1111"}


# ---------------------------------------------------------------- 전체 마법사 대화형: 설정할지 묻기


def test_full_wizard_asks_with_default_yes_first_time(vault, mock_jira, conf):
    script = Script(["home", mock_jira.base_url, "KB", "", conf.base_url, "", "n"], [JIRA_TOKEN, CONF_TOKEN])
    out: list[str] = []
    assert _interactive(vault, script, out, section=None) == 0
    question = next((p, d) for p, d in script.asked if p.startswith("Confluence를 설정할까요?"))
    assert question[1] == "y"
    assert "\n[3] Confluence" in out
    data = _data(vault)
    assert data["confluence"]["enabled"] is True and data["confluence"]["space_key"] == "~honggildong"


def test_full_wizard_decline_first_time_disables(vault, mock_jira):
    script = Script(["home", mock_jira.base_url, "KB", "n", "n"], [JIRA_TOKEN])
    out: list[str] = []
    assert _interactive(vault, script, out, section=None) == 0
    assert _data(vault)["confluence"] == {"enabled": False}
    assert any("kb.py init --section confluence" in line for line in out)


def test_full_wizard_existing_confluence_defaults_to_keep(vault, mock_jira):
    existing = {"enabled": True, "base_url": "http://127.0.0.1:9", "space_key": "~honggildong", "parent_page_id": "", "pat_ref": "keyring:work-kb/confluence"}
    _base(vault, jira_url=mock_jira.base_url, confluence=existing)
    script = Script(["", "", "KB", "", ""], [JIRA_TOKEN])  # Confluence·LLM 질문 모두 Enter
    out: list[str] = []
    assert _interactive(vault, script, out, section=None) == 0
    question = next(d for p, d in script.asked if p.startswith("Confluence를 설정할까요?"))
    assert question == "n"
    assert "  기존 Confluence 설정을 그대로 둡니다" in out
    assert _data(vault)["confluence"] == existing


# ---------------------------------------------------------------- doctor


def _doctor(vault: Path) -> dict[str, tuple[str, str]]:
    report = doctor.Report()
    ctx = doctor.Context(root_arg=str(vault), report=report)
    checks = [
        doctor.check_dependencies,
        doctor.check_config,
        doctor.check_local,
        doctor.check_secrets,
        doctor.check_confluence,
        doctor.check_confluence_details,
    ]
    doctor.run_checks(ctx, checks)
    return {c.name: (c.state, c.text) for c in report.checks}


def _doctor_config(vault: Path, conf, **extra) -> None:
    block = {"enabled": True, "base_url": conf.base_url, "space_key": "~honggildong", "parent_page_id": "", "pat_ref": "keyring:work-kb/confluence"}
    block.update(extra)
    _base(vault, confluence=block)
    kb_secrets.store("work-kb", "confluence", CONF_TOKEN)


def test_doctor_space_and_parent_ok(vault, conf):
    conf.add_page("4242", "업무 지식", "~honggildong")
    _doctor_config(vault, conf, parent_page_id="4242")
    lines = _doctor(vault)
    assert lines["Confluence 스페이스"] == ("✓", "~honggildong (홍길동)")
    assert lines["Confluence 부모 페이지"] == ("✓", "업무 지식 (4242)")
    state, text = lines["Confluence push_globs"]
    assert state == "-" and "맞는 파일이 없습니다" in text


@pytest.mark.parametrize(
    "extra, name, needle",
    [
        ({"space_key": "~nobody"}, "Confluence 스페이스", "없거나 볼 권한이 없습니다"),
        ({"space_key": ""}, "Confluence 스페이스", "confluence.space_key가 없습니다"),
        ({"parent_page_id": "777"}, "Confluence 부모 페이지", "777: 없거나 볼 권한이 없습니다"),
        ({"parent_page_id": "5000"}, "Confluence 부모 페이지", "스페이스 TEAM의 페이지"),
        ({"parent_page_id": "abc"}, "Confluence 부모 페이지", "숫자 페이지 ID가 아닙니다"),
    ],
)
def test_doctor_problems_are_fail(vault, conf, extra, name, needle):
    conf.add_page("5000", "남의 페이지", "TEAM")
    _doctor_config(vault, conf, **extra)
    lines = _doctor(vault)
    state, text = lines[name]
    assert state == "✗" and needle in text and "kb.py init --section confluence" in text


def test_doctor_push_globs_counts_files(vault, conf):
    _doctor_config(vault, conf)
    for rel in ("wiki/projects/KB.md", "wiki/topics/배포.md", "wiki/decisions.md", "wiki/people/홍길동.md", "daily/2026-11-05.md"):
        path = vault / rel
        path.write_text("# x\n", encoding="utf-8")
    state, text = _doctor(vault)["Confluence push_globs"]
    assert state == "-" and text.startswith("3개 파일 (wiki/projects/*.md, wiki/topics/*.md, wiki/decisions.md)")


def test_doctor_skips_details_when_confluence_down_or_disabled(vault, conf):
    _doctor_config(vault, conf)
    conf.stop()
    lines = _doctor(vault)
    assert lines["Confluence"][0] == "✗"
    assert "Confluence 스페이스" not in lines and "Confluence 부모 페이지" not in lines
    assert lines["Confluence push_globs"][0] == "-"  # 로컬 점검은 그대로
    _base(vault, confluence={"enabled": False})
    lines = _doctor(vault)
    assert lines["Confluence"] == ("-", "비활성")
    assert not [n for n in lines if n.startswith("Confluence ")]


def test_doctor_with_phase0_mock(vault, mock_confluence):
    """저장소의 모의 서버(mock_confluence)로도 스페이스 점검이 ✓다(init --dev·demo가 쓰는 경로)."""
    config.save_local(
        vault,
        {
            "environment": "home",
            "jira": {"base_url": "http://127.0.0.1:9", "project_keys": ["KB"], "pat_ref": "keyring:work-kb/jira"},
            "confluence": {"enabled": True, "base_url": mock_confluence.base_url, "space_key": "~honggildong", "parent_page_id": "", "pat_ref": "keyring:work-kb/confluence"},
        },
    )
    kb_secrets.store("work-kb", "confluence", "mock-token")
    lines = _doctor(vault)
    assert lines["Confluence"][0] == "✓"
    assert lines["Confluence 스페이스"][0] == "✓"
    assert lines["Confluence 부모 페이지"][0] == "-"
