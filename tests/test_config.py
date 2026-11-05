"""lib/config.py: 병합 순서, 환경변수 덮기, 환경 판정(안전 쪽), 공개 호스트, 저장."""
from __future__ import annotations

import dataclasses
import datetime as dt
from pathlib import Path

import pytest
import yaml

from lib import config, core

REPO = Path(__file__).resolve().parents[1]
TASKS = {"tag", "decisions", "entity_summary", "topic_proposal", "image_ingest", "contradictions"}


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def example_blocks() -> dict:
    return yaml.safe_load((REPO / "config.local.example.yaml").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 저장소 config.yaml


def test_repo_config_yaml_loads(vault):
    assert (vault / "config.yaml").exists()
    cfg = config.load(vault, environ={})
    assert cfg.root == Path(vault).resolve()
    assert cfg.environment == "company" and cfg.local_exists is False
    assert set(cfg.get("llm.tasks")) == TASKS
    for name in TASKS:
        task = cfg.llm_task(name)
        assert task["reasoning"] in ("on", "off")
        assert task["profile"] == "main"
        assert isinstance(task["max_output_tokens"], int) and task["max_output_tokens"] > 0  # Phase 4 (D41)
    assert cfg.llm_task("decisions")["reasoning"] == "on"
    assert cfg.llm_task("tag")["reasoning"] == "off"
    assert "updated >= '-30d'" in cfg.get("jira.jql_mine")
    assert config.CODE_BLOCKLIST <= cfg.blocklist
    assert cfg.get("confluence.title_prefix") == "[KB] "


def test_repo_config_yaml_quotes_reasoning_and_has_no_connection_values():
    raw = yaml.safe_load((REPO / "config.yaml").read_text(encoding="utf-8"))
    for task in raw["llm"]["tasks"].values():
        assert isinstance(task["reasoning"], str)  # 따옴표가 없으면 bool이 된다
    text = (REPO / "config.yaml").read_text(encoding="utf-8")
    for word in ("base_url", "_ref", "http://", "https://", "keyring:", "env:"):
        assert word not in text
    assert "environment" not in raw
    assert set(raw["llm"]["public_hosts_blocklist"]) == set(config.CODE_BLOCKLIST)


# ---------------------------------------------------------------- 병합 순서


def _layered(vault: Path) -> None:
    write(
        vault / "config.yaml",
        "timezone: Asia/Seoul\n"
        "jira:\n  max_results: 100\n  poll_minutes: 10\n"
        "  views:\n    today: 'a = 1'\n    blocked: 'b = 1'\n"
        "llm:\n  tasks:\n    tag: {profile: main, reasoning: 'off', max_input_tokens: 2000}\n",
    )
    write(
        vault / "config.local.yaml",
        "environment: home\n"
        "jira:\n  max_results: 200\n  views:\n    blocked: 'c = 1'\n"
        "confluence:\n  title_prefix: '[메모] '\n",
    )


def test_merge_precedence(vault):
    _layered(vault)
    cfg = config.load(vault, environ={"KB_JIRA_MAX_RESULTS": "50", "KB_LLM_ENABLED": "true"})
    assert cfg.get("jira.max_results") == 50  # 환경변수 > local > config.yaml
    assert cfg.get("jira.poll_minutes") == 10  # config.yaml > 코드 기본값
    assert cfg.get("jira.api_version") == 2  # 코드 기본값
    assert cfg.get("jira.views") == {"today": "a = 1", "blocked": "c = 1"}  # 묶음은 깊게 합친다
    assert cfg.get("confluence.title_prefix") == "[메모] "
    assert cfg.get("llm.enabled") is True
    assert cfg.environment == "home" and cfg.local_exists is True

    without_env = config.load(vault, environ={})
    assert without_env.get("jira.max_results") == 200
    assert without_env.get("llm.enabled") is False  # 코드 기본값은 꺼짐


def test_environ_argument_replaces_os_environ(vault, monkeypatch):
    _layered(vault)
    monkeypatch.setenv("KB_JIRA_MAX_RESULTS", "9")
    assert config.load(vault, environ={}).get("jira.max_results") == 200
    assert config.load(vault).get("jira.max_results") == 9


def test_parse_env_overrides():
    environ = {
        "KB_JIRA_MAX_RESULTS": "50",
        "KB_LLM_ENABLED": "false",
        "KB_LLM_TIMEOUT_SEC": "60.5",
        "KB_CONFLUENCE_TITLE_PREFIX": "'[메모] '",
        "KB_JIRA_PROJECT_KEYS": "[KB, OPS]",
        "KB_JIRA_AUTH": "pat",
        "KB_TIMEZONE": "UTC",
        "KB_NORMALIZE_KEYS": "no",
        # 예약·무관한 이름은 무시
        "KB_AGENT": "1",
        "KB_ROOT": "C:/x",
        "KB_REQUIRE_LIVE": "1",
        "KB_SKIP_NVIM": "1",
        "KB_FAKE_NOW": "2026-11-05T09:00:00",
        "KB_FAKE_KEYRING_FILE": "x.json",
        "KB_INIT_JIRA_URL": "http://127.0.0.1:1",
        "KB_INIT_ENVIRONMENT": "home",
        "KB_ENVIRONMENT": "home",
        "KB_KB_ROOT": "..",
        "KB_UNKNOWN_THING": "1",
        "KB_JIRA": "1",
        "PATH": "C:/Windows",
    }
    assert config.parse_env_overrides(environ) == {
        "jira": {"max_results": 50, "project_keys": ["KB", "OPS"], "auth": "pat"},
        "llm": {"enabled": False, "timeout_sec": 60.5},
        "confluence": {"title_prefix": "[메모] "},
        "timezone": "UTC",
        "normalize_keys": False,
    }
    parsed = config.parse_env_overrides({"KB_JIRA_MAX_RESULTS": "50", "KB_LLM_ENABLED": "false"})
    assert type(parsed["jira"]["max_results"]) is int
    assert parsed["llm"]["enabled"] is False


def test_env_override_bad_yaml_kept_as_text():
    assert config.parse_env_overrides({"KB_JIRA_AUTH": "a: b: c"}) == {"jira": {"auth": "a: b: c"}}


def test_env_override_cannot_replace_mapping(vault):
    with pytest.raises(core.ConfigError, match="KB_JIRA_VIEWS"):
        config.load(vault, environ={"KB_JIRA_VIEWS": "x"})
    with pytest.raises(core.ConfigError, match="KB_LLM_PROFILES"):
        config.parse_env_overrides({"KB_LLM_PROFILES": "{main: {base_url: x}}"})


def test_kb_environment_is_ignored(vault):
    cfg = config.load(vault, environ={"KB_ENVIRONMENT": "home"})
    assert cfg.environment == "company"


# ---------------------------------------------------------------- 환경 판정


@pytest.mark.parametrize(
    "local, expected",
    [
        (None, "company"),
        ("", "company"),
        ("jira:\n  max_results: 5\n", "company"),
        ("environment: staging\n", "company"),
        ("environment: yes\n", "company"),
        ("environment: [home]\n", "company"),
        ("environment: home\n", "home"),
        ("environment: Home\n", "home"),
        ("environment: company\n", "company"),
    ],
)
def test_environment_fail_closed(vault, local, expected):
    if local is not None:
        write(vault / "config.local.yaml", local)
    cfg = config.load(vault, environ={})
    assert cfg.environment == expected
    assert cfg.get("environment") == expected
    assert cfg.local_exists is (local is not None)


def test_environment_only_from_local(vault):
    text = (vault / "config.yaml").read_text(encoding="utf-8")
    write(vault / "config.yaml", "environment: home\n" + text)
    assert config.load(vault, environ={}).environment == "company"


# ---------------------------------------------------------------- 루트·파일


def test_root_resolution_order(vault, tmp_path, monkeypatch):
    other = tmp_path / "다른 볼트"
    other.mkdir()
    write(other / "config.yaml", "timezone: UTC\n")
    assert config.load(environ={"KB_ROOT": str(vault)}).root == vault.resolve()
    assert config.load(vault, environ={"KB_ROOT": str(other)}).root == vault.resolve()
    monkeypatch.setattr(config, "CODE_ROOT", other.resolve())
    cfg = config.load(environ={})
    assert cfg.root == other.resolve()
    assert cfg.get("timezone") == "UTC"


def test_missing_config_yaml(tmp_path):
    empty = tmp_path / "빈 폴더"
    empty.mkdir()
    with pytest.raises(core.ConfigError) as info:
        config.load(empty, environ={})
    assert info.value.exit_code == 4
    assert "config.yaml" in str(info.value)
    with pytest.raises(core.ConfigError):
        config.load(tmp_path / "없는 폴더", environ={})


def test_bom_and_crlf(vault):
    text = "environment: home\r\nconfluence:\r\n  title_prefix: '[메모] '\r\n"
    (vault / "config.local.yaml").write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    cfg = config.load(vault, environ={})
    assert cfg.environment == "home"
    assert cfg.get("confluence.title_prefix") == "[메모] "


def test_yaml_error_reports_line(vault):
    write(vault / "config.local.yaml", "environment: home\njira:\n  base_url: [\n")
    with pytest.raises(core.ConfigError, match="줄"):
        config.load(vault, environ={})


def test_non_utf8_and_non_mapping(vault):
    (vault / "config.local.yaml").write_bytes("environment: 집\n".encode("cp949"))
    with pytest.raises(core.ConfigError, match="UTF-8"):
        config.load(vault, environ={})
    write(vault / "config.local.yaml", "- a\n- b\n")
    with pytest.raises(core.ConfigError):
        config.load(vault, environ={})


def test_kb_root_resolves_against_config_folder(vault):
    text = (vault / "config.yaml").read_text(encoding="utf-8").replace("kb_root: .", "kb_root: 노트")
    write(vault / "config.yaml", text)
    (vault / "노트").mkdir()
    cfg = config.load(vault, environ={})
    assert cfg.root == vault.resolve() / "노트"
    assert cfg.config_dir == vault.resolve()


# ---------------------------------------------------------------- Config 메서드


def test_get_path_code_path_tz(vault):
    cfg = config.load(vault, environ={})
    assert cfg.get("jira.views.blocked").startswith("assignee")
    assert cfg.get("jira.nope", 7) == 7
    assert cfg.get("timezone.inner", "d") == "d"
    assert cfg.path() == cfg.root
    assert cfg.path("daily", "2026-11-05.md") == cfg.root / "daily" / "2026-11-05.md"
    with pytest.raises(core.ConfigError):
        cfg.path("..", "밖.md")
    with pytest.raises(core.ConfigError):
        cfg.code_path("..", "x")
    assert cfg.code_path("prompts") == REPO / "prompts"
    assert cfg.code_path() == Path(config.__file__).resolve().parents[2]
    assert cfg.tz.utcoffset(dt.datetime(2026, 11, 5)) == dt.timedelta(hours=9)


def test_config_is_frozen(vault):
    cfg = config.load(vault, environ={})
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.environment = "home"  # type: ignore[misc]


def test_direct_construction():
    cfg = config.Config(root=Path("C:/kb"), data={"jira": {"project_keys": ["kb"]}}, environment="home")
    assert cfg.config_dir == cfg.root
    assert cfg.local_exists is False
    assert cfg.project_keys == ["KB"]
    assert cfg.blocklist == config.CODE_BLOCKLIST


# ---------------------------------------------------------------- 프로젝트 키


@pytest.mark.parametrize(
    "yaml_value, expected",
    [
        ("[KB, OPS]", ["KB", "OPS"]),
        ("[kb, OPS, KB]", ["KB", "OPS"]),
        ("['ON', 'NO', A_1]", ["ON", "NO", "A_1"]),
        ("'KB, OPS'", ["KB", "OPS"]),
        ("[]", []),
        ("null", []),
    ],
)
def test_project_keys_valid(vault, yaml_value, expected):
    write(vault / "config.local.yaml", f"environment: home\njira:\n  project_keys: {yaml_value}\n")
    assert config.load(vault, environ={}).project_keys == expected


@pytest.mark.parametrize("yaml_value", ["[ON]", "[1AB]", "[K-B]", "[K]", "[{a: 1}]", "{a: 1}", "[123]"])
def test_project_keys_invalid(vault, yaml_value):
    write(vault / "config.local.yaml", f"environment: home\njira:\n  project_keys: {yaml_value}\n")
    cfg = config.load(vault, environ={})
    with pytest.raises(core.ConfigError):
        cfg.project_keys


def test_project_keys_from_env(vault):
    cfg = config.load(vault, environ={"KB_JIRA_PROJECT_KEYS": "KB,OPS"})
    assert cfg.project_keys == ["KB", "OPS"]


# ---------------------------------------------------------------- reasoning 정리


def test_reasoning_normalisation(vault):
    write(
        vault / "config.yaml",
        "llm:\n  tasks:\n"
        "    a: {reasoning: on}\n"
        "    b: {reasoning: off}\n"
        "    c: {reasoning: 'On'}\n"
        "    d: {reasoning: 'off'}\n"
        "    e: {reasoning: true}\n"
        "    f: {reasoning: no}\n"
        "    g: {max_input_tokens: 10}\n",
    )
    cfg = config.load(vault, environ={})
    got = {name: cfg.get(f"llm.tasks.{name}.reasoning") for name in "abcdef"}
    assert got == {"a": "on", "b": "off", "c": "on", "d": "off", "e": "on", "f": "off"}
    assert cfg.llm_task("g") == {"profile": "main", "reasoning": "off", "max_input_tokens": 10}
    with pytest.raises(core.ConfigError):
        cfg.llm_task("없는작업")


@pytest.mark.parametrize("value", ["maybe", "1", "[on]"])
def test_reasoning_invalid(vault, value):
    write(vault / "config.yaml", f"llm:\n  tasks:\n    a: {{reasoning: {value}}}\n")
    with pytest.raises(core.ConfigError, match="reasoning"):
        config.load(vault, environ={})


def test_llm_task_on_direct_config():
    cfg = config.Config(root=Path("C:/kb"), data={"llm": {"tasks": {"t": {"reasoning": True}}}}, environment="home")
    assert cfg.llm_task("t")["reasoning"] == "on"


# ---------------------------------------------------------------- JQL


def test_jql_mine_order_by_refused(vault):
    write(vault / "config.local.yaml", "environment: home\njira:\n  jql_mine: \"project = KB ORDER BY updated DESC\"\n")
    with pytest.raises(core.ConfigError, match="ORDER BY"):
        config.load(vault, environ={})


def test_jql_mine_order_by_inside_quotes_allowed(vault):
    write(
        vault / "config.local.yaml",
        "environment: home\njira:\n  jql_mine: \"summary ~ 'order by' AND (text ~ \\\"order   by\\\")\"\n",
    )
    assert "order by" in config.load(vault, environ={}).get("jira.jql_mine")


# ---------------------------------------------------------------- 공개 호스트


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://api.deepinfra.com/v1/openai", True),
        ("https://deepinfra.com./v1", True),
        ("https://API.DeepInfra.COM/v1", True),
        ("https://api.deepinfra.com.:443/v1", True),
        ("https://user@api.openai.com:8443/v1", True),
        ("http://generativelanguage.googleapis.com", True),
        ("api.deepinfra.com/v1/openai", True),
        ("https://notdeepinfra.com/v1", False),
        ("https://deepinfra.com.evil.invalid/v1", False),
        ("https://llm.example.invalid/v1", False),
        ("http://127.0.0.1:8083/v1", False),
        ("http://localhost:8083/v1", False),
        ("http://[::1]:8083/v1", False),
        ("  https://llm.example.invalid/v1  ", False),
        ("", False),
        ("   ", False),
        (None, False),
        # 호스트를 하나로 정할 수 없는 주소는 공개로 본다(안전 쪽, D44)
        ("http://[::1", True),
        ("https://", True),
        ("https://api.deepinfra.com\\@llm.corp.invalid/v1", True),
        ("https://llm.corp.invalid\\x/v1", True),
        ("https://user@llm.corp.invalid/v1", True),
        ("https://llm.corp.invalid#@x/v1", True),
        ("https://llm.corp .invalid/v1", True),
        ("https://llm.corp.invalid/v 1", True),
        ("https://llm.corp.invalid\t/v1", True),
        ("https://사내.invalid/v1", True),
        ("https://llm%2Ecorp.invalid/v1", True),
    ],
)
def test_is_public_host(url, expected):
    assert config.is_public_host(url, config.CODE_BLOCKLIST) is expected


def test_checked_host_matches_urllib3():
    from urllib3.util import parse_url

    for url in ("https://API.Example.invalid.:8443/v1", "http://127.0.0.1:8083/v1", "llm.example.invalid/v1"):
        expected = parse_url(url if "://" in url else "//" + url).host.lower().rstrip(".")
        assert config.checked_host(url) == expected
    assert config.checked_host("http://[::1]:8083/v1") == "::1"
    assert config.checked_host("https://api.deepinfra.com\\@llm.corp.invalid/v1") is None
    assert config.checked_host(None) is None


def test_is_public_host_normalises_blocklist_entries():
    assert config.is_public_host("https://x.corp.invalid/v1", ["CORP.INVALID."])
    assert config.is_public_host("https://x.corp.invalid/v1", ["*.corp.invalid"])
    assert config.is_public_host("https://x.corp.invalid/v1", ["https://corp.invalid/"])
    assert not config.is_public_host("https://x.corp.invalid/v1", ["", None, "other.invalid"])


def test_blocklist_is_union(vault):
    write(vault / "config.local.yaml", "environment: home\nllm:\n  public_hosts_blocklist: [Cloud-LLM.invalid.]\n")
    cfg = config.load(vault, environ={})
    assert "cloud-llm.invalid" in cfg.blocklist
    assert config.CODE_BLOCKLIST <= cfg.blocklist  # 설정으로 뺄 수 없다
    # 환경변수 겹도 더하기만 한다. 빈 목록으로 줄일 수 없다(D44).
    cfg = config.load(vault, environ={"KB_LLM_PUBLIC_HOSTS_BLOCKLIST": "[]"})
    assert "cloud-llm.invalid" in cfg.blocklist
    assert config.CODE_BLOCKLIST <= cfg.blocklist
    cfg = config.load(vault, environ={"KB_LLM_PUBLIC_HOSTS_BLOCKLIST": "env-llm.invalid, other.invalid"})
    assert {"cloud-llm.invalid", "env-llm.invalid", "other.invalid"} <= cfg.blocklist


def test_blocklist_local_cannot_shrink_config_yaml(vault):
    text = (vault / "config.yaml").read_text(encoding="utf-8")
    text = text.replace("public_hosts_blocklist: [", "public_hosts_blocklist: [repo-llm.invalid, ", 1)
    assert "repo-llm.invalid" in text
    write(vault / "config.yaml", text)
    write(vault / "config.local.yaml", "environment: company\nllm:\n  public_hosts_blocklist: []\n")
    cfg = config.load(vault, environ={})
    assert "repo-llm.invalid" in cfg.blocklist
    assert config.is_public_host("https://x.repo-llm.invalid/v1", cfg.blocklist)


@pytest.mark.parametrize(
    "local, environ, environment",
    [
        ("environment: company\nllm:\n  public_hosts_blocklist: 5\n", {}, "company"),
        ("environment: company\nllm:\n  public_hosts_blocklist: {deepinfra.com: true}\n", {}, "company"),
        ("environment: home\nllm:\n  public_hosts_blocklist: [a.invalid, 7]\n", {}, "home"),
        ("environment: company\n", {"KB_LLM_PUBLIC_HOSTS_BLOCKLIST": "5"}, "company"),
        ("environment: home\n", {"KB_LLM_PUBLIC_HOSTS_BLOCKLIST": "on"}, "home"),
        ("environment: nope\n", {"KB_LLM_PUBLIC_HOSTS_BLOCKLIST": "1"}, "company"),
    ],
)
def test_blocklist_malformed_refused_at_load(vault, local, environ, environment):
    write(vault / "config.local.yaml", local)
    with pytest.raises(config.BlocklistError, match="public_hosts_blocklist") as info:
        config.load(vault, environ=environ)
    assert isinstance(info.value, core.ConfigError)
    assert info.value.environment == environment


def test_blocklist_malformed_in_config_yaml_refused(vault):
    write(vault / "config.yaml", "llm:\n  public_hosts_blocklist: 5\n")
    with pytest.raises(config.BlocklistError):
        config.load(vault, environ={})


@pytest.mark.parametrize("raw", [5, {"a.invalid": True}, True])
def test_blocklist_property_never_raises(raw):
    cfg = config.Config(root=Path("C:/kb"), data={"llm": {"public_hosts_blocklist": raw}}, environment="company")
    assert cfg.blocklist == config.CODE_BLOCKLIST
    cfg = config.Config(root=Path("C:/kb"), data={"llm": {"public_hosts_blocklist": ["x.invalid", 5, None]}}, environment="company")
    assert cfg.blocklist == config.CODE_BLOCKLIST | {"x.invalid"}


# ---------------------------------------------------------------- 프로필


def test_profiles(vault):
    write(vault / "config.local.yaml", yaml.safe_dump(example_blocks()["home"], allow_unicode=True))
    cfg = config.load(vault, environ={})
    assert set(cfg.profiles()) == {"main", "fallback", "mock"}
    assert cfg.profile("mock")["base_url"] == "http://127.0.0.1:8083/v1"
    cfg.profiles()["main"]["model"] = "changed"
    assert cfg.profile("main")["model"] == "nvidia/NVIDIA-Nemotron-3.5-Super-VL"  # 사본을 돌려준다
    with pytest.raises(core.ConfigError, match="없는프로필"):
        cfg.profile("없는프로필")


# ---------------------------------------------------------------- save_local


@pytest.mark.parametrize(
    "data, where",
    [
        ({"jira": {"pat": "abc123secretvalue"}}, "jira.pat"),
        ({"jira": {"pat_ref": "abc123secretvalue"}}, "jira.pat_ref"),
        ({"llm": {"profiles": {"main": {"api_key": "sk-abc123secretvalue"}}}}, "llm.profiles.main.api_key"),
        ({"llm": {"profiles": {"main": {"api_key_ref": "abc123secretvalue"}}}}, "llm.profiles.main.api_key_ref"),
        ({"x": {"password": "abc123secretvalue"}}, "x.password"),
        ({"x": {"client_secret": "abc123secretvalue"}}, "x.client_secret"),
        ({"x": {"accessToken": "abc123secretvalue"}}, "x.accessToken"),
        ({"x": {"apiKey": "abc123secretvalue"}}, "x.apiKey"),
        ({"x": [{"token": 12345}]}, "x[0].token"),
        ({"token": {"value": "abc123secretvalue"}}, "token"),
    ],
)
def test_save_local_refuses_secrets(vault, data, where):
    with pytest.raises(core.ConfigError) as info:
        config.save_local(vault, data)
    assert where in str(info.value)
    assert "abc123secretvalue" not in str(info.value)
    assert not (vault / "config.local.yaml").exists()


def test_save_local_round_trip(vault):
    data = {
        "environment": "home",
        "jira": {"base_url": "http://127.0.0.1:8081", "project_keys": ["KB", "OPS"], "pat_ref": "keyring:work-kb/jira"},
        "confluence": {"enabled": True, "space_key": "~honggildong", "title_prefix": "[지식] ", "pat_ref": None},
        "llm": {
            "enabled": True,
            "profiles": {
                "main": {
                    "base_url": "https://api.deepinfra.com/v1/openai",
                    "model": "nvidia/NVIDIA-Nemotron-3.5-Super-VL",
                    "api_key_ref": "env:DEEPINFRA_API_KEY",
                    "detected": {"context_tokens": 262144, "reasoning_toggle": {"method": "none"}},
                },
                "mock": {"base_url": "http://127.0.0.1:8083/v1", "model": "mock", "api_key_ref": ""},
            },
            "tasks": {"tag": {"max_input_tokens": 100}},
        },
    }
    config.save_local(vault, data)
    raw = (vault / "config.local.yaml").read_bytes()
    text = raw.decode("utf-8")
    assert "[지식]" in text  # allow_unicode
    assert not raw.startswith(b"\xef\xbb\xbf")
    body = [line for line in text.splitlines() if line and not line.startswith("#")]
    assert body[0] == "environment: home"  # sort_keys=False
    assert yaml.safe_load(text) == data
    assert [p.name for p in vault.iterdir() if p.name.endswith(".tmp")] == []

    cfg = config.load(vault, environ={})
    assert cfg.environment == "home"
    assert cfg.project_keys == ["KB", "OPS"]
    assert cfg.get("confluence.title_prefix") == "[지식] "
    assert cfg.profile("main")["detected"]["context_tokens"] == 262144
    assert cfg.get("llm.tasks.tag.max_input_tokens") == 100
    assert cfg.get("llm.tasks.decisions.reasoning") == "on"  # config.yaml 값과 합쳐짐


def test_save_local_rejects_non_mapping(vault):
    with pytest.raises(core.ConfigError):
        config.save_local(vault, ["a"])  # type: ignore[arg-type]


# ---------------------------------------------------------------- 예시 파일


def test_example_has_home_and_company():
    blocks = example_blocks()
    assert set(blocks) == {"home", "company"}


def test_example_home_block(vault):
    home = example_blocks()["home"]
    config.save_local(vault, home)  # 비밀값 검사 통과
    cfg = config.load(vault, environ={})
    assert cfg.environment == "home"
    assert cfg.get("jira.base_url") == "http://127.0.0.1:8081"
    assert cfg.get("confluence.base_url") == "http://127.0.0.1:8082"
    assert cfg.get("confluence.space_key") == "~honggildong"
    assert cfg.project_keys == ["KB", "OPS"]
    main, fallback, mock = cfg.profile("main"), cfg.profile("fallback"), cfg.profile("mock")
    assert main["base_url"] == "https://api.deepinfra.com/v1/openai"
    assert main["model"] == "nvidia/NVIDIA-Nemotron-3.5-Super-VL"
    assert main["api_key_ref"] == "env:DEEPINFRA_API_KEY"
    assert fallback["model"] == "google/gemma-4-26B-A4B-it"
    assert mock == {"base_url": "http://127.0.0.1:8083/v1", "model": "mock"}
    assert config.is_public_host(main["base_url"], cfg.blocklist)
    for profile in cfg.profiles().values():
        assert "detected" not in profile  # init이 채운다


def test_example_company_block(vault):
    company = example_blocks()["company"]
    config.save_local(vault, company)
    cfg = config.load(vault, environ={})
    assert cfg.environment == "company"
    refs = [cfg.get("jira.pat_ref"), cfg.get("confluence.pat_ref")]
    refs += [p.get("api_key_ref") for p in cfg.profiles().values()]
    assert refs and all(isinstance(r, str) and r.startswith("keyring:") for r in refs)
    urls = [cfg.get("jira.base_url"), cfg.get("confluence.base_url")] + [p["base_url"] for p in cfg.profiles().values()]
    for url in urls:
        assert not config.is_public_host(url, cfg.blocklist)
        assert config._hostname(url).endswith(".invalid")
    for profile in cfg.profiles().values():
        assert "detected" not in profile
