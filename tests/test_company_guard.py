"""회사 차단(D44): LlmClient가 LLM HTTP의 유일한 통로이고, 회사 환경에서 공개 호스트를 막는다.

- 만들 때: 어느 프로필이든 공개 호스트면 PublicHostRefused(종료 5). `llm.enabled: false`여도.
- 부를 때: chat·run_task·connected_profiles·list_models 모두 보내기 직전에 다시 막는다.
- `KB_ENVIRONMENT`는 무시, environment가 없거나 잘못되면 회사로 본다.
- 리디렉션은 따라가지 않는다(공개 호스트로 보내는 302/307 포함).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from lib import config, core, llm_client, net
from lib.llm_client import ChatHttpError, LlmClient

from test_llm_client import FakeLLM, chat_reply

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = "https://api.deepinfra.com/v1/openai"
PRIVATE = "http://127.0.0.1:9/v1"


@pytest.fixture
def no_http(monkeypatch):
    """네트워크 계층이 한 번이라도 불리면 실패."""
    calls = []

    def boom(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError(f"차단되어야 할 요청이 나갔습니다: {args[1:3]}")

    monkeypatch.setattr(net, "request", boom)
    return calls


def write_local(vault, data):
    config.save_local(vault, data)
    return config.load(vault)


def llm_block(profiles, *, enabled=True, order=("main", "fallback"), **extra):
    return {"enabled": enabled, "profiles": profiles, "fallback_order": list(order), **extra}


def company(vault, profiles, **kw):
    return write_local(vault, {"environment": "company", "llm": llm_block(profiles, **kw)})


# ---------------------------------------------------------------- 만들 때


@pytest.mark.parametrize(
    "url",
    [
        PUBLIC,
        "https://API.DeepInfra.COM/v1/openai",
        "https://api.deepinfra.com./v1/openai",
        "https://deepinfra.com:443/v1",
        "https://x.y.openai.com/v1",
        "https://openrouter.ai/api/v1",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "http://127.0.0.1@api.deepinfra.com/v1",  # 사용자 정보로 속이기
        "http://127.0.0.1\\@api.deepinfra.com/v1",
        "https://api.deepinfra.com。/v1",  # 비ASCII
    ],
)
def test_company_refuses_public_main(vault, no_http, url):
    cfg = company(vault, {"main": {"base_url": url, "model": "m"}})
    with pytest.raises(core.PublicHostRefused) as info:
        LlmClient(cfg)
    assert info.value.exit_code == 5
    assert "main" in str(info.value)
    assert no_http == []


def test_company_refuses_even_when_disabled(vault, no_http):
    cfg = company(vault, {"main": {"base_url": PUBLIC, "model": "m"}}, enabled=False)
    with pytest.raises(core.PublicHostRefused):
        LlmClient(cfg)


def test_company_refuses_any_profile_not_only_used_ones(vault, no_http):
    cfg = company(vault, {"main": {"base_url": PRIVATE, "model": "m"}, "spare": {"base_url": PUBLIC, "model": "m"}}, order=("main",))
    with pytest.raises(core.PublicHostRefused) as info:
        LlmClient(cfg)
    assert "spare" in str(info.value)


def test_company_refuses_non_string_url(vault, no_http):
    cfg = company(vault, {"main": {"base_url": ["https://corp.example/v1"], "model": "m"}})
    with pytest.raises(core.PublicHostRefused):
        LlmClient(cfg)


def test_company_blocklist_extension(vault, no_http):
    cfg = company(vault, {"main": {"base_url": "https://llm.partner-cloud.example/v1"}},
                  public_hosts_blocklist=["partner-cloud.example"])
    with pytest.raises(core.PublicHostRefused):
        LlmClient(cfg)


@pytest.mark.parametrize("environment", [None, "", "office", "Home ", "COMPANY"])
def test_missing_or_invalid_environment_is_company(vault, no_http, environment):
    data = {"llm": llm_block({"main": {"base_url": PUBLIC}})}
    if environment is not None:
        data["environment"] = environment
    cfg = write_local(vault, data)
    if environment == "Home ":  # 공백·대소문자는 정리해서 home으로 읽는다(config 규칙)
        assert cfg.environment == "home"
        LlmClient(cfg)
        return
    assert cfg.environment == "company"
    with pytest.raises(core.PublicHostRefused):
        LlmClient(cfg)


def test_kb_environment_variable_is_ignored(vault, no_http, monkeypatch):
    config.save_local(vault, {"environment": "company", "llm": llm_block({"main": {"base_url": PUBLIC}})})
    monkeypatch.setenv("KB_ENVIRONMENT", "home")
    monkeypatch.setenv("KB_LLM_PUBLIC_HOSTS_BLOCKLIST", "[]")  # 줄일 수 없다(합집합)
    cfg = config.load(vault)
    assert cfg.environment == "company"
    with pytest.raises(core.PublicHostRefused):
        LlmClient(cfg)


def test_home_allows_public_and_company_allows_private(vault, no_http):
    LlmClient(write_local(vault, {"environment": "home", "llm": llm_block({"main": {"base_url": PUBLIC}})}))
    LlmClient(company(vault, {"main": {"base_url": PRIVATE}, "fallback": {"base_url": "https://llm.corp.example/v1"}}))
    LlmClient(company(vault, {"main": {"base_url": ""}, "fallback": {"model": "주소 없음"}}))
    assert no_http == []  # 만들기만 해서는 아무것도 보내지 않는다


def test_assert_host_allowed(vault):
    home = write_local(vault, {"environment": "home"})
    llm_client.assert_host_allowed(home, PUBLIC)
    corp = write_local(vault, {"environment": "company"})
    llm_client.assert_host_allowed(corp, PRIVATE)
    llm_client.assert_host_allowed(corp, "")
    for url in (PUBLIC, "https://api.openai.com/v1", "https://anthropic.com.", "http://a@b.example"):
        with pytest.raises(core.PublicHostRefused):
            llm_client.assert_host_allowed(corp, url)
    assert llm_client.is_public_host("https://sub.groq.com/v1", config.CODE_BLOCKLIST)
    assert not llm_client.is_public_host("https://notgroq.com/v1", config.CODE_BLOCKLIST)


# ---------------------------------------------------------------- 부를 때


def _mutated_client(vault):
    """사내 주소로 만든 뒤 내부 프로필이 공개 주소로 바뀐 클라이언트(방어 2겹 확인용)."""
    cfg = company(vault, {"main": {"base_url": PRIVATE, "model": "m", "detected": {"vision": True, "json_mode": True}},
                          "fallback": {"base_url": PRIVATE, "model": "m"}})
    client = LlmClient(cfg)
    client._profiles["main"]["base_url"] = PUBLIC
    return client


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.chat("main", [{"role": "user", "content": "q"}]),
        lambda c: c.list_models("main"),
        lambda c: c.connected_profiles(),
        lambda c: c.run_task("tag", lambda limit: [{"role": "user", "content": "q"}], validate=lambda d: None),
        lambda c: c.run_task("image_ingest", [{"role": "user", "content": "q"}], validate=lambda d: None, needs_vision=True),
    ],
    ids=["chat", "list_models", "connected_profiles", "run_task", "run_task_vision"],
)
def test_every_caller_refuses_before_sending(vault, no_http, call):
    client = _mutated_client(vault)
    with pytest.raises(core.PublicHostRefused):
        call(client)
    assert no_http == []


def test_run_task_refusal_is_not_swallowed_as_fallback(vault, no_http):
    client = _mutated_client(vault)
    client._profiles["fallback"]["base_url"] = PUBLIC
    with pytest.raises(core.PublicHostRefused):
        client.run_task("tag", [{"role": "user", "content": "q"}], validate=lambda d: None)


# ---------------------------------------------------------------- 리디렉션


def test_models_redirect_to_public_host_not_followed(vault):
    import mock_llm as mock_module

    server = mock_module.start(redirect_to=PUBLIC + "/models")
    try:
        cfg = company(vault, {"main": {"base_url": server.base_url + "/v1", "model": "mock"}}, order=("main",))
        client = LlmClient(cfg)
        with pytest.raises(ChatHttpError) as info:
            client.list_models("main")
        assert info.value.status == 302 and "deepinfra" in info.value.body
        assert client.connected_profiles() == []
        assert len([e for e in server.log if e["path"].endswith("/models")]) == 2
    finally:
        server.stop()


def test_chat_redirect_to_public_host_not_followed(vault):
    fake = FakeLLM()
    try:
        fake.default = (307, {}, {"Location": PUBLIC + "/chat/completions"})
        cfg = company(vault, {"main": {"base_url": fake.base_url, "model": "m"}, "fallback": {"base_url": fake.base_url, "model": "m"}})
        client = LlmClient(cfg)
        with pytest.raises(ChatHttpError) as info:
            client.chat("main", [{"role": "user", "content": "q"}])
        assert info.value.status == 307
        outcome = client.run_task("tag", [{"role": "user", "content": "q"}], validate=lambda d: None)
        assert outcome.status == "skipped"
        assert [a["http_status"] for a in outcome.attempts] == [307, 307]
        assert len(fake.chats) == 3
    finally:
        fake.stop()


def test_company_private_host_works(vault):
    fake = FakeLLM()
    try:
        fake.default = (200, chat_reply('{"tags": {}}'))
        cfg = company(vault, {"main": {"base_url": fake.base_url, "model": "m"}}, order=("main",))
        outcome = LlmClient(cfg).run_task("tag", [{"role": "user", "content": "q"}], validate=lambda d: None)
        assert outcome.status == "ok"
    finally:
        fake.stop()


# ---------------------------------------------------------------- 유일한 통로


LLM_CALLERS = ["scripts/compile.py", "scripts/ingest_images.py", "scripts/lib/llm_detect.py", "scripts/lint.py"]
HTTP_MODULES = {"requests", "urllib.request", "urllib3", "http.client", "httpx", "aiohttp", "socket", "lib.net"}


def _http_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name in HTTP_MODULES or a.name.split(".")[0] in {"requests", "httpx", "aiohttp", "urllib3"}]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in HTTP_MODULES or module.split(".")[0] in {"requests", "httpx", "aiohttp", "urllib3"}:
                found.append(module)
            if module == "lib":
                found += [f"lib.{a.name}" for a in node.names if a.name == "net"]
            if module == "urllib":
                found += [f"urllib.{a.name}" for a in node.names if a.name == "request"]
    return found


@pytest.mark.parametrize("rel", LLM_CALLERS)
def test_llm_callers_have_no_http_of_their_own(rel):
    path = ROOT / rel
    if not path.exists():
        pytest.skip(f"{rel} 아직 없음")
    assert _http_imports(path) == [], f"{rel}는 LLM HTTP를 llm_client로만 보내야 합니다"


def test_http_import_rule_catches(tmp_path):
    sample = tmp_path / "x.py"
    sample.write_text("import requests\nfrom lib import net, core\nfrom urllib import request\nimport http.client\nfrom lib import llm_client\n", encoding="utf-8")
    assert _http_imports(sample) == ["requests", "lib.net", "urllib.request", "http.client"]
