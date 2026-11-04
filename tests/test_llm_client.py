"""lib/llm_client.py, lib/prompts.py 테스트 (W4.2).

대부분은 이 파일의 `FakeLLM`(127.0.0.1, 테스트마다 응답을 정함)으로 요청 본문과 재시도 순서를
확인한다. 끝의 몇 개는 tests/mock_llm.py(W4.1)의 canned·echo_request·system_prefix 모드로
KB-TASK 표시 줄 약속을 확인한다.
"""
from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import keyring
import pytest

from lib import config, core, llm_client, net, prompts
from lib.llm_client import ChatHttpError, LlmClient, normalize_message

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------- 가짜 서버


def chat_reply(content, *, reasoning=None, reasoning_content=None, finish="stop", usage=True, model="fake-model"):
    message = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning"] = reasoning
    if reasoning_content is not None:
        message["reasoning_content"] = reasoning_content
    body = {"id": "x", "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish}]}
    if usage:
        body["usage"] = {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}
    return body


class FakeLLM:
    """요청을 기록하고 `replies` 큐(없으면 `default`)로 답한다. 답: (상태, 본문[, 머리글]) 또는 함수(req)."""

    def __init__(self):
        self.requests: list[dict] = []
        self.replies: list = []
        self.default = (200, chat_reply('{"ok": true}'))
        self.models = (200, {"object": "list", "data": [{"id": "fake-model"}]})
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _go(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else None
                req = {"method": self.command, "path": self.path, "json": body,
                       "auth": self.headers.get("Authorization")}
                fake.requests.append(req)
                if self.path.endswith("/models"):
                    reply = fake.models
                else:
                    reply = fake.replies.pop(0) if fake.replies else fake.default
                if callable(reply):
                    reply = reply(req)
                status, payload, *rest = reply
                headers = rest[0] if rest else {}
                data = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = _go

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    @property
    def chats(self) -> list[dict]:
        return [r for r in self.requests if r["path"].endswith("/chat/completions")]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def fake():
    server = FakeLLM()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def fake2():
    server = FakeLLM()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def dead_url():
    """닫힌 루프백 포트(연결 거부)."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}/v1"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(net, "_sleep", lambda seconds: None)


PARAM_TOGGLE = {"method": "param", "param_name": "reasoning_effort", "on_value": "medium", "off_value": "none"}


def detected(**kw):
    base = {"json_mode": True, "vision": False, "reasoning_toggle": dict(PARAM_TOGGLE), "context_tokens": 32768}
    base.update(kw)
    return base


def profile(url, **det):
    return {"base_url": url, "model": "fake-model", "detected": detected(**det)}


def make_cfg(vault, profiles, *, environment="home", enabled=True, order=("main", "fallback"), tasks=None, **llm):
    data = {"environment": environment,
            "llm": {"enabled": enabled, "profiles": profiles, "fallback_order": list(order), **llm}}
    if tasks:
        data["llm"]["tasks"] = tasks
    config.save_local(vault, data)
    return config.load(vault)


def msgs(text="허구 입력 줄"):
    return [{"role": "system", "content": "KB-TASK: tag\n\n규칙"}, {"role": "user", "content": text}]


def accept_all(data):
    return None


# ---------------------------------------------------------------- normalize_message


@pytest.mark.parametrize(
    "message, content, reasoning",
    [
        ({"content": '{"a": 1}'}, '{"a": 1}', None),
        ({"content": " 답 ", "reasoning": "생각"}, "답", "생각"),
        ({"content": "답", "reasoning_content": "생각"}, "답", "생각"),
        ({"content": "<think>생각</think>\n답"}, "답", "생각"),
        ({"content": "생각만 있고 여는 태그 없음</think>답"}, "답", "생각만 있고 여는 태그 없음"),
        ({"content": "<think>a</think>b</think>c"}, "c", "ab"),
        ({"content": None, "reasoning": '{"a": 1}'}, "", '{"a": 1}'),
        ({"content": "<think>잘린 생각"}, "", "잘린 생각"),
        ({"content": [{"type": "text", "text": "조각"}, {"type": "text", "text": "답"}]}, "조각답", None),
        ({"content": "", "reasoning": ""}, "", None),
        ({"content": "답", "reasoning": "같음", "reasoning_content": "같음"}, "답", "같음"),
    ],
)
def test_normalize_message(message, content, reasoning):
    assert normalize_message(message) == (content, reasoning)


def test_normalize_message_not_mapping():
    assert normalize_message(None) == ("", None)  # type: ignore[arg-type]


# ---------------------------------------------------------------- 분량


def test_estimate_tokens():
    assert llm_client.estimate_tokens("") == 0
    assert llm_client.estimate_tokens("abcde") == 2
    assert llm_client.estimate_tokens("한글여섯글자") == 3
    assert llm_client.estimate_tokens("a") == 1


def test_split_by_date_keeps_days_together():
    items = [("2026-11-01", "a" * 10), ("2026-11-01", "b" * 10), ("2026-11-02", "c" * 10), ("2026-11-03", "d" * 10)]
    # 한 줄 11자(줄바꿈 포함). 한도 10토큰 = 25자 → 11-01(22자) | 11-02+11-03(22자)
    chunks = llm_client.split_by_date(items, 10)
    assert chunks == [items[:2], items[2:]]
    assert llm_client.split_by_date(items, 8) == [items[0:1], items[1:2], items[2:3], items[3:]]  # 20자: 11-01도 줄 단위
    assert llm_client.split_by_date(items, 1000) == [items]
    assert [x for chunk in llm_client.split_by_date(items, 9) for x in chunk] == items


def test_split_by_date_splits_oversized_day_by_line():
    day = [("2026-11-01", f"{i}" * 20) for i in range(5)]  # 줄마다 21자
    chunks = llm_client.split_by_date([("2026-10-31", "x"), *day], 18)  # 45자
    assert chunks[0] == [("2026-10-31", "x")]
    assert chunks[1:] == [day[0:2], day[2:4], day[4:5]]


def test_split_by_date_huge_line_alone():
    items = [("d1", "a"), ("d2", "z" * 100), ("d3", "b")]
    assert llm_client.split_by_date(items, 4) == [[items[0]], [items[1]], [items[2]]]


def test_split_by_date_edges():
    assert llm_client.split_by_date([], 10) == []
    for bad in (0, -1, True, 2.5):
        with pytest.raises(ValueError):
            llm_client.split_by_date([("d", "x")], bad)


@pytest.mark.parametrize(
    "text, expected",
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('다음은 결과입니다.\n{"a": {"b": 2}}\n끝.', {"a": {"b": 2}}),
    ],
)
def test_parse_json_object(text, expected):
    assert llm_client.parse_json_object(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "[1, 2]", "{깨짐", "그냥 글"])
def test_parse_json_object_rejects(text):
    with pytest.raises(llm_client.SchemaError):
        llm_client.parse_json_object(text)


# ---------------------------------------------------------------- 출력·입력 한도


def test_max_output_and_input_limits(vault, fake):
    cfg = make_cfg(vault, {
        "main": profile(fake.base_url, context_tokens=10000),
        "fallback": {"base_url": fake.base_url, "model": "m"},
        "always": profile(fake.base_url, reasoning_toggle={"method": "none"}, baseline_reasoning=True),
        "none_off": profile(fake.base_url, reasoning_toggle={"method": "none"}, baseline_reasoning=False),
    })
    client = LlmClient(cfg)
    assert client.max_output_tokens("tag") == 64
    assert client.max_output_tokens("decisions") == 8192
    assert client.max_output_tokens("entity_summary", "main") == 2048
    assert client.max_output_tokens("tag", "always") == 2048  # D41 하한
    assert client.max_output_tokens("decisions", "always") == 8192
    assert client.max_output_tokens("tag", "none_off") == 64
    assert client.max_output_tokens("tag", "fallback") == 64
    # min(max_input_tokens, 0.8 × context)
    assert client.input_limit("tag") == 2000
    assert client.input_limit("decisions", "main") == 8000
    assert client.input_limit("decisions", "fallback") == 30000  # 탐지 없음 → assumed 262144
    assert client.input_limit("topic_proposal", "main") == 8000
    assert client.context_tokens("fallback") == llm_client.ASSUMED_CONTEXT


def test_max_output_defaults_when_config_lacks_key(vault, fake):
    cfg = make_cfg(vault, {"main": profile(fake.base_url)},
                   tasks={"tag": {"profile": "main", "reasoning": "off", "max_input_tokens": 100, "max_output_tokens": None},
                          "image_ingest": {"profile": "main", "reasoning": "off", "max_output_tokens": 99999}})
    client = LlmClient(cfg)
    assert client.max_output_tokens("tag") == 64
    assert client.max_output_tokens("image_ingest") == llm_client.OUTPUT_CAP


# ---------------------------------------------------------------- chat 요청 본문


def _client(vault, fake, *, order=("main",), **det):
    return LlmClient(make_cfg(vault, {"main": profile(fake.base_url, **det)}, order=order))


def test_chat_basic_body_and_result(vault, fake):
    fake.default = (200, chat_reply('{"x": 1}', reasoning_content="생각 생각"))
    client = _client(vault, fake)
    result = client.chat("main", msgs(), max_tokens=77)
    body = fake.chats[0]["json"]
    assert fake.chats[0]["path"] == "/v1/chat/completions"
    assert body["model"] == "fake-model" and body["max_tokens"] == 77 and body["temperature"] == 0
    assert "reasoning_effort" not in body and "response_format" not in body  # reasoning=None, json_mode=False
    assert result.content == '{"x": 1}' and result.reasoning == "생각 생각"
    assert result.finish_reason == "stop" and result.profile == "main" and result.model == "fake-model"
    assert result.usage["completion_tokens"] == 3
    assert result.raw["choices"][0]["message"]["reasoning_content"] == "생각 생각"


def test_chat_does_not_mutate_messages(vault, fake):
    client = _client(vault, fake, json_mode=False, reasoning_toggle={"method": "system_prefix"})
    original = msgs()
    snapshot = json.loads(json.dumps(original))
    client.chat("main", original, reasoning=True, json_mode=True)
    assert original == snapshot


@pytest.mark.parametrize(
    "toggle, on_expect, off_expect",
    [
        (PARAM_TOGGLE, {"reasoning_effort": "medium"}, {"reasoning_effort": "none"}),
        ({"method": "param", "param_name": "chat_template_kwargs", "on_value": {"enable_thinking": True}, "off_value": {"enable_thinking": False}},
         {"chat_template_kwargs": {"enable_thinking": True}}, {"chat_template_kwargs": {"enable_thinking": False}}),
        ({"method": "param", "param_name": "reasoning", "on_value": {"enabled": True}, "off_value": {"enabled": False}},
         {"reasoning": {"enabled": True}}, {"reasoning": {"enabled": False}}),
        ({"method": "param", "param_name": "reasoning_effort", "on_value": "high"}, {"reasoning_effort": "high"}, {}),
    ],
)
def test_chat_param_toggle(vault, fake, toggle, on_expect, off_expect):
    client = _client(vault, fake, reasoning_toggle=toggle)
    client.chat("main", msgs(), reasoning=True)
    client.chat("main", msgs(), reasoning=False)
    on_body, off_body = fake.chats[0]["json"], fake.chats[1]["json"]
    keys = {"reasoning_effort", "chat_template_kwargs", "reasoning"}
    assert {k: v for k, v in on_body.items() if k in keys} == on_expect
    assert {k: v for k, v in off_body.items() if k in keys} == off_expect


def test_chat_dict_toggle_merges_with_body_extra(vault, fake):
    toggle = {"method": "param", "param_name": "chat_template_kwargs", "on_value": {"enable_thinking": True}, "off_value": {"enable_thinking": False}}
    client = _client(vault, fake, reasoning_toggle=toggle)
    client.chat("main", msgs(), reasoning=False, body_extra={"chat_template_kwargs": {"medium_effort": True}, "top_p": 0.5})
    body = fake.chats[0]["json"]
    assert body["chat_template_kwargs"] == {"enable_thinking": False, "medium_effort": True}
    assert body["top_p"] == 0.5


def test_chat_system_prefix_after_marker(vault, fake):
    client = _client(vault, fake, reasoning_toggle={"method": "system_prefix", "on_value": "/think", "off_value": "/no_think"})
    client.chat("main", msgs(), reasoning=True)
    client.chat("main", msgs(), reasoning=False)
    client.chat("main", [{"role": "system", "content": "표시 없는 시스템"}, {"role": "user", "content": "q"}], reasoning=False)
    client.chat("main", [{"role": "user", "content": "q"}], reasoning=True)
    systems = [r["json"]["messages"][0]["content"] for r in fake.chats]
    assert systems[0].startswith("KB-TASK: tag\n/think\n")
    assert systems[1].startswith("KB-TASK: tag\n/no_think\n")
    assert systems[2] == "/no_think\n표시 없는 시스템"
    assert systems[3] == "/think" and fake.chats[3]["json"]["messages"][0]["role"] == "system"
    assert all("reasoning_effort" not in r["json"] for r in fake.chats)


@pytest.mark.parametrize("toggle", [{"method": "none"}, None, {"method": "param"}])
def test_chat_no_toggle_sends_nothing(vault, fake, toggle):
    client = _client(vault, fake, reasoning_toggle=toggle)
    client.chat("main", msgs(), reasoning=True)
    body = fake.chats[0]["json"]
    assert set(body) == {"model", "messages", "max_tokens", "temperature"}
    assert body["messages"] == msgs()


def test_chat_json_mode_response_format_or_prompt(vault, fake):
    client = _client(vault, fake, json_mode=True)
    client.chat("main", msgs(), json_mode=True)
    assert fake.chats[0]["json"]["response_format"] == {"type": "json_object"}
    assert llm_client.FORCED_JSON_RULE not in fake.chats[0]["json"]["messages"][0]["content"]

    client2 = LlmClient(make_cfg(vault, {"main": profile(fake.base_url, json_mode=False)}, order=("main",)))
    client2.chat("main", msgs(), json_mode=True)
    body = fake.chats[1]["json"]
    assert "response_format" not in body
    system = body["messages"][0]["content"]
    assert system.startswith("KB-TASK: tag") and system.rstrip().endswith(llm_client.FORCED_JSON_RULE)


def test_chat_sends_key_from_keyring(vault, fake):
    keyring.set_password("work-kb", "llm", "sk-test-secret-123456")
    prof = profile(fake.base_url)
    prof["api_key_ref"] = "keyring:work-kb/llm"
    client = LlmClient(make_cfg(vault, {"main": prof}, order=("main",)))
    client.chat("main", msgs())
    assert fake.chats[0]["auth"] == "Bearer sk-test-secret-123456"
    log = (vault / ".state" / "logs" / "kb.log").read_text(encoding="utf-8")
    assert "sk-test-secret-123456" not in log


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 500, 503])
def test_chat_http_error(vault, fake, status):
    fake.default = (status, {"error": {"message": f"오류 {status} 본문"}})
    client = _client(vault, fake)
    with pytest.raises(ChatHttpError) as info:
        client.chat("main", msgs())
    assert info.value.status == status and f"오류 {status} 본문" in info.value.body


def test_chat_redirect_not_followed(vault, fake, fake2):
    fake.default = (307, {}, {"Location": fake2.base_url + "/chat/completions"})
    client = _client(vault, fake)
    with pytest.raises(ChatHttpError) as info:
        client.chat("main", msgs())
    assert info.value.status == 307 and "리디렉션" in info.value.body
    assert len(fake.chats) == 1 and fake2.requests == []


def test_chat_non_json_and_no_choices(vault, fake):
    client = _client(vault, fake)
    fake.replies = [(200, "<html>로그인</html>"), (200, {"object": "x"})]
    with pytest.raises(ChatHttpError):
        client.chat("main", msgs())
    with pytest.raises(ChatHttpError):
        client.chat("main", msgs())


def test_chat_connection_refused(vault, dead_url):
    client = LlmClient(make_cfg(vault, {"main": profile(dead_url)}, order=("main",)))
    with pytest.raises(core.NetworkError):
        client.chat("main", msgs())


def test_chat_unknown_profile(vault, fake):
    client = _client(vault, fake)
    with pytest.raises(core.ConfigError):
        client.chat("nope", msgs())


def test_chat_logs_one_line_per_request_without_content(vault, fake):
    client = _client(vault, fake)
    client.chat("main", msgs("아주 비밀스러운 허구 문장 하나"))
    fake.default = (500, {"error": "x"})
    with pytest.raises(ChatHttpError):
        client.chat("main", msgs("아주 비밀스러운 허구 문장 하나"))
    lines = (vault / ".state" / "logs" / "kb.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert all("main" in line and "fake-model" in line for line in lines)
    assert "→ 200" in lines[0] and "→ 500" in lines[1]
    assert not any("비밀스러운" in line for line in lines)


# ---------------------------------------------------------------- D38


def test_d38_400_with_reasoning_on_retries_prompt_json_and_keeps_it(vault, fake):
    client = _client(vault, fake, json_mode=True)
    fake.replies = [(400, {"error": {"message": "response_format과 추론을 함께 쓸 수 없습니다"}}),
                    (200, chat_reply('{"ok": 1}')), (200, chat_reply('{"ok": 2}')), (200, chat_reply('{"ok": 3}'))]
    result = client.chat("main", msgs(), reasoning=True, json_mode=True)
    assert result.content == '{"ok": 1}'
    first, second = fake.chats[0]["json"], fake.chats[1]["json"]
    assert first["response_format"] == {"type": "json_object"} and first["reasoning_effort"] == "medium"
    assert "response_format" not in second and second["reasoning_effort"] == "medium"
    assert second["messages"][0]["content"].rstrip().endswith(llm_client.FORCED_JSON_RULE)
    # 이번 실행 동안 유지: 추론 켜짐은 바로 프롬프트 강제, 추론 꺼짐은 response_format 그대로
    client.chat("main", msgs(), reasoning=True, json_mode=True)
    client.chat("main", msgs(), reasoning=False, json_mode=True)
    assert "response_format" not in fake.chats[2]["json"]
    assert fake.chats[3]["json"]["response_format"] == {"type": "json_object"}
    assert len(fake.chats) == 4


def test_d38_empty_content_retries_once(vault, fake):
    client = _client(vault, fake, json_mode=True)
    fake.replies = [(200, chat_reply(None, reasoning='{"a": 1}')), (200, chat_reply('{"a": 1}'))]
    result = client.chat("main", msgs(), reasoning=True, json_mode=True)
    assert result.content == '{"a": 1}' and len(fake.chats) == 2
    assert "response_format" not in fake.chats[1]["json"]


def test_d38_not_applied_when_reasoning_off_or_length(vault, fake):
    client = _client(vault, fake, json_mode=True)
    fake.replies = [(400, {"error": "bad"})]
    with pytest.raises(ChatHttpError):
        client.chat("main", msgs(), reasoning=False, json_mode=True)
    fake.replies = [(200, chat_reply("", finish="length"))]
    result = client.chat("main", msgs(), reasoning=True, json_mode=True)
    assert result.finish_reason == "length" and len(fake.chats) == 2


# ---------------------------------------------------------------- /models·연결


def test_list_models_and_connected(vault, fake, fake2, dead_url):
    fake2.models = (401, {"error": "키가 틀렸습니다"})
    cfg = make_cfg(vault, {"main": profile(fake.base_url), "fallback": profile(fake2.base_url),
                           "down": profile(dead_url), "spare": profile(fake.base_url)},
                   order=("main", "fallback", "down"))
    client = LlmClient(cfg)
    assert client.list_models("main") == [{"id": "fake-model"}]
    with pytest.raises(ChatHttpError) as info:
        client.list_models("fallback")
    assert info.value.status == 401
    assert client.connected_profiles() == ["main"]  # spare는 작업·순서에 없어 보지 않는다
    assert "401" in client.last_probe["fallback"] and client.last_probe["down"]
    assert client.connected_profiles(["spare", "ghost"]) == ["spare"]
    assert client.last_probe["ghost"] == "프로필 없음"
    assert all(r["method"] == "GET" for r in fake.requests + fake2.requests)


def test_models_404_counts_as_connected(vault, fake):
    fake.models = (404, {"detail": "Not Found"})
    client = _client(vault, fake)
    assert client.list_models("main") is None
    assert client.connected_profiles() == ["main"]
    assert "없음" in client.last_probe["main"]


def test_models_sends_key_when_resolvable_else_without(vault, fake):
    prof = profile(fake.base_url)
    prof["api_key_ref"] = "keyring:work-kb/llm"
    client = LlmClient(make_cfg(vault, {"main": prof}, order=("main",)))
    assert client.connected_profiles() == ["main"]
    assert fake.requests[-1]["auth"] is None  # 저장된 키 없음 → 키 없이
    keyring.set_password("work-kb", "llm", "sk-models-key-0001")
    client = LlmClient(make_cfg(vault, {"main": prof}, order=("main",)))
    client.list_models("main")
    assert fake.requests[-1]["auth"] == "Bearer sk-models-key-0001"


def test_models_redirect_is_error(vault, fake, fake2):
    fake.models = (302, {}, {"Location": fake2.base_url + "/models"})
    client = _client(vault, fake)
    with pytest.raises(ChatHttpError) as info:
        client.list_models("main")
    assert info.value.status == 302
    assert client.connected_profiles() == []
    assert fake2.requests == []


def test_enabled_property(vault, fake):
    assert LlmClient(make_cfg(vault, {"main": profile(fake.base_url)}, enabled=True)).enabled is True
    assert LlmClient(make_cfg(vault, {"main": profile(fake.base_url)}, enabled=False)).enabled is False
    assert LlmClient(make_cfg(vault, {"main": profile(fake.base_url)}, enabled="false")).enabled is False


# ---------------------------------------------------------------- run_task


def two_profiles(vault, fake, fake2, *, main=None, fallback=None, **kw):
    return LlmClient(make_cfg(vault, {"main": main or profile(fake.base_url), "fallback": fallback or profile(fake2.base_url)}, **kw))


def test_run_task_ok_first_try(vault, fake, fake2):
    fake.default = (200, chat_reply('{"tags": {"L1": "회의"}}'))
    client = two_profiles(vault, fake, fake2)
    limits = []

    def build(limit):
        limits.append(limit)
        return msgs()

    seen = []
    outcome = client.run_task("tag", build, validate=seen.append)
    assert outcome.status == "ok" and outcome.profile == "main"
    assert outcome.data == {"tags": {"L1": "회의"}} and seen == [outcome.data]
    assert len(outcome.attempts) == 1 and outcome.attempts[0]["status"] == "ok"
    assert outcome.attempts[0]["tokens"] == {"prompt_tokens": 10, "completion_tokens": 3}
    assert limits == [2000]
    body = fake.chats[0]["json"]
    assert body["max_tokens"] == 64 and body["reasoning_effort"] == "none"  # tag: 추론 꺼짐
    assert body["response_format"] == {"type": "json_object"}
    assert fake2.requests == []


def test_run_task_reasoning_on_for_decisions(vault, fake, fake2):
    fake.default = (200, chat_reply('{"decisions": [], "actions": []}'))
    client = two_profiles(vault, fake, fake2)
    outcome = client.run_task("decisions", msgs(), validate=accept_all)  # 목록도 받는다
    assert outcome.status == "ok"
    assert fake.chats[0]["json"]["reasoning_effort"] == "medium" and fake.chats[0]["json"]["max_tokens"] == 8192


def test_run_task_disabled_makes_no_request(vault, fake, fake2):
    client = two_profiles(vault, fake, fake2, enabled=False)
    called = []
    outcome = client.run_task("tag", lambda limit: called.append(limit) or msgs(), validate=accept_all)
    assert outcome == llm_client.TaskOutcome("disabled", None, None, [])
    assert called == [] and fake.requests == [] and fake2.requests == []


def test_run_task_broken_json_once_retries_same_profile(vault, fake, fake2):
    fake.replies = [(200, chat_reply("{깨진 JSON")), (200, chat_reply('<think>음</think>{"tags": {}}'))]
    outcome = two_profiles(vault, fake, fake2).run_task("tag", msgs(), validate=accept_all)
    assert outcome.status == "ok" and outcome.profile == "main"
    assert [a["status"] for a in outcome.attempts] == ["schema", "ok"]
    assert [a["profile"] for a in outcome.attempts] == ["main", "main"]
    assert fake2.requests == []


def test_run_task_validate_failure_twice_moves_to_fallback(vault, fake, fake2):
    fake.default = (200, chat_reply('{"wrong": 1}'))
    fake2.default = (200, chat_reply('{"tags": {"L1": "결정"}}'))

    def validate(data):
        if "tags" not in data:
            raise KeyError("tags")

    outcome = two_profiles(vault, fake, fake2).run_task("tag", msgs(), validate=validate)
    assert outcome.status == "ok" and outcome.profile == "fallback"
    assert [(a["profile"], a["status"]) for a in outcome.attempts] == [("main", "schema"), ("main", "schema"), ("fallback", "ok")]
    assert "KeyError" in outcome.attempts[0]["error"]
    assert len(fake.chats) == 2 and len(fake2.chats) == 1


def test_run_task_content_null_is_schema_failure(vault, fake, fake2):
    fake.default = (200, chat_reply(None, reasoning='{"tags": {}}'))
    client = two_profiles(vault, fake, fake2, main=profile(fake.base_url, json_mode=False))
    outcome = client.run_task("tag", msgs(), validate=accept_all)
    assert outcome.profile == "fallback"
    assert [a["status"] for a in outcome.attempts] == ["schema", "schema", "ok"]


def test_run_task_length_doubles_budget(vault, fake, fake2):
    fake.replies = [(200, chat_reply("", finish="length")), (200, chat_reply('{"tags": {}}'))]
    outcome = two_profiles(vault, fake, fake2).run_task("tag", msgs(), validate=accept_all)
    assert outcome.status == "ok"
    assert [r["json"]["max_tokens"] for r in fake.chats] == [64, 128]
    assert [a["status"] for a in outcome.attempts] == ["length", "ok"]
    assert [a["max_tokens"] for a in outcome.attempts] == [64, 128]


def test_run_task_length_double_capped(vault, fake, fake2):
    fake.default = (200, chat_reply('{"partial": ', finish="length"))
    fake2.default = (200, chat_reply("", finish="length"))
    tasks = {"decisions": {"profile": "main", "reasoning": "on", "max_input_tokens": 30000, "max_output_tokens": 12000}}
    outcome = two_profiles(vault, fake, fake2, tasks=tasks).run_task("decisions", msgs(), validate=accept_all)
    assert outcome.status == "skipped" and outcome.data is None and outcome.profile is None
    assert [r["json"]["max_tokens"] for r in fake.chats + fake2.chats] == [12000, 16384, 12000, 16384]
    assert all(a["status"] == "length" for a in outcome.attempts)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 429, 500, 502])
def test_run_task_http_errors_move_to_next_profile(vault, fake, fake2, status):
    fake.default = (status, {"error": {"message": f"본문 {status}"}})
    fake2.default = (200, chat_reply('{"tags": {}}'))
    outcome = two_profiles(vault, fake, fake2).run_task("tag", msgs(), validate=accept_all)
    assert outcome.status == "ok" and outcome.profile == "fallback"
    first = outcome.attempts[0]
    assert first["profile"] == "main" and first["status"] == "http" and first["http_status"] == status
    assert f"본문 {status}" in first["error"]
    expected = 1 + llm_client.MAX_429 if status == 429 else 1  # 429는 net이 재시도한 뒤 넘어간다
    assert len(fake.chats) == expected


def test_run_task_connection_refused_moves_on(vault, fake2, dead_url):
    fake2.default = (200, chat_reply('{"tags": {}}'))
    client = LlmClient(make_cfg(vault, {"main": profile(dead_url), "fallback": profile(fake2.base_url)}))
    outcome = client.run_task("tag", msgs(), validate=accept_all)
    assert outcome.profile == "fallback"
    assert outcome.attempts[0]["status"] == "network" and outcome.attempts[0]["error"]


def test_run_task_timeout_moves_on(vault, fake, fake2):
    import time as _time

    def slow(req):
        _time.sleep(1.0)
        return 200, chat_reply('{"tags": {}}')

    fake.default = slow
    fake2.default = (200, chat_reply('{"tags": {}}'))
    client = two_profiles(vault, fake, fake2, timeout_sec=0.3)
    outcome = client.run_task("tag", msgs(), validate=accept_all)
    assert outcome.profile == "fallback" and outcome.attempts[0]["status"] == "network"


def test_run_task_all_fail_skipped(vault, fake, fake2):
    fake.default = (500, {"error": "x"})
    fake2.default = (200, chat_reply("JSON 아님"))
    outcome = two_profiles(vault, fake, fake2).run_task("tag", msgs(), validate=accept_all)
    assert outcome.status == "skipped"
    assert [(a["profile"], a["status"]) for a in outcome.attempts] == [("main", "http"), ("fallback", "schema"), ("fallback", "schema")]
    log = (vault / ".state" / "logs" / "kb.log").read_text(encoding="utf-8")
    assert "건너뜀" in log


def test_run_task_order_task_profile_first(vault, fake, fake2):
    fake.default = (200, chat_reply('{"from": "main"}'))
    fake2.default = (200, chat_reply('{"from": "fallback"}'))
    tasks = {"tag": {"profile": "fallback", "reasoning": "off", "max_input_tokens": 2000}}
    client = two_profiles(vault, fake, fake2, tasks=tasks)
    assert client.profile_order("tag") == ["fallback", "main"]
    outcome = client.run_task("tag", msgs(), validate=accept_all)
    assert outcome.data == {"from": "fallback"} and fake.requests == []


def test_run_task_missing_profile_in_order(vault, fake):
    fake.default = (200, chat_reply('{"a": 1}'))
    client = LlmClient(make_cfg(vault, {"fallback": profile(fake.base_url)}, order=("main", "fallback")))
    outcome = client.run_task("tag", msgs(), validate=accept_all)
    assert outcome.profile == "fallback"
    assert outcome.attempts[0] == {**outcome.attempts[0], "profile": "main", "status": "missing"}


def test_run_task_messages_for_gets_per_profile_limit(vault, fake, fake2):
    fake.default = (400, {"error": {"message": "context length exceeded"}})
    fake2.default = (200, chat_reply('{"ok": true}'))
    client = two_profiles(vault, fake, fake2, main=profile(fake.base_url, context_tokens=100000),
                          fallback=profile(fake2.base_url, context_tokens=1000))
    limits = []

    def build(limit):
        limits.append(limit)
        return msgs("x" * limit)

    outcome = client.run_task("decisions", build, validate=accept_all)
    assert outcome.profile == "fallback"
    assert limits == [30000, 800]
    assert fake2.chats[0]["json"]["messages"][1]["content"] == "x" * 800


def test_run_task_vision_only_vision_profiles(vault, fake, fake2):
    fake2.default = (200, chat_reply('{"kind": "photo"}'))
    client = two_profiles(vault, fake, fake2, main=profile(fake.base_url, vision=False), fallback=profile(fake2.base_url, vision=True))
    outcome = client.run_task("image_ingest", msgs(), validate=accept_all, needs_vision=True)
    assert outcome.profile == "fallback" and fake.requests == []
    assert outcome.attempts[0]["status"] == "no_vision"


def test_run_task_vision_none_available(vault, fake, fake2):
    client = two_profiles(vault, fake, fake2, main={"base_url": fake.base_url, "model": "m"})
    outcome = client.run_task("image_ingest", msgs(), validate=accept_all, needs_vision=True)
    assert outcome.status == "skipped" and fake.requests == [] and fake2.requests == []
    assert [a["status"] for a in outcome.attempts] == ["no_vision", "no_vision"]


def test_run_task_d41_floor_applied(vault, fake, fake2):
    fake.default = (200, chat_reply('{"tags": {}}'))
    client = two_profiles(vault, fake, fake2, main=profile(fake.base_url, reasoning_toggle={"method": "none"}, baseline_reasoning=True))
    client.run_task("tag", msgs(), validate=accept_all)
    body = fake.chats[0]["json"]
    assert body["max_tokens"] == 2048 and "reasoning_effort" not in body


def test_run_task_unknown_task(vault, fake):
    client = _client(vault, fake)
    with pytest.raises(core.ConfigError):
        client.run_task("no_such_task", msgs(), validate=accept_all)


# ---------------------------------------------------------------- prompts


def _code_root(tmp_path, agents: str, prompt: str | None = "작업 지시: 태그를 붙인다.") -> Path:
    root = tmp_path / "코드 루트"
    (root / "prompts").mkdir(parents=True)
    (root / "AGENTS.md").write_text(agents, encoding="utf-8")
    if prompt is not None:
        (root / "prompts" / "tag.md").write_text("\n" + prompt + "\n\n", encoding="utf-8")
    return root


AGENTS_SAMPLE = """# AGENTS.md

## 역할

편집자다.

## 폴더 소유권

표.

## 금지

- daily 수정 금지

```
## 금지 (코드 펜스 안, 제목 아님)
```

### 세부

하위 제목도 포함.

## 태그

회의, 결정

## 작업 스키마

| 작업 | 출력 |

## 답변 규칙

- 한국어

## 명령

kb.py
"""


def test_section_extraction():
    assert prompts.section(AGENTS_SAMPLE, "역할") == "## 역할\n\n편집자다."
    금지 = prompts.section(AGENTS_SAMPLE, "금지")
    assert 금지.startswith("## 금지\n") and "### 세부" in 금지 and "하위 제목도 포함." in 금지
    assert "## 태그" not in 금지 and "(코드 펜스 안, 제목 아님)" in 금지
    assert prompts.section(AGENTS_SAMPLE, "명령") == "## 명령\n\nkb.py"
    assert prompts.section(AGENTS_SAMPLE, "없는 절") is None
    assert prompts.section("## 역할\r\n\r\n줄\r\n", "역할") == "## 역할\n\n줄"


def test_build_messages_layout(tmp_path):
    root = _code_root(tmp_path, AGENTS_SAMPLE)
    messages = prompts.build_messages("tag", "L1 | 10:00 회의 메모", root=root)
    assert [m["role"] for m in messages] == ["system", "user"]
    system = messages[0]["content"]
    assert system.splitlines()[0] == "KB-TASK: tag"
    order = [system.index(h) for h in ("## 역할", "## 금지", "## 태그", "## 작업 스키마", "## 답변 규칙", "작업 지시: 태그를 붙인다.")]
    assert order == sorted(order)
    assert "## 폴더 소유권" not in system and "## 명령" not in system
    assert messages[1]["content"] == "L1 | 10:00 회의 메모"


def test_build_messages_with_images(tmp_path):
    root = _code_root(tmp_path, AGENTS_SAMPLE)
    url = prompts.image_url(b"\x89PNG fake", "image/png")
    assert url.startswith("data:image/png;base64,")
    messages = prompts.build_messages("tag", "힌트: 파일명", images=[url], root=root)
    assert messages[1]["content"] == [{"type": "text", "text": "힌트: 파일명"}, {"type": "image_url", "image_url": {"url": url}}]
    with pytest.raises(ValueError):
        prompts.image_url(b"")


def test_build_messages_errors(tmp_path):
    root = _code_root(tmp_path, AGENTS_SAMPLE.replace("## 답변 규칙", "## 다른 절"))
    with pytest.raises(core.ConfigError, match="답변 규칙"):
        prompts.build_messages("tag", "x", root=root)
    root2 = _code_root(tmp_path / "b", AGENTS_SAMPLE, prompt=None)
    with pytest.raises(core.ConfigError, match="프롬프트"):
        prompts.build_messages("tag", "x", root=root2)
    with pytest.raises(core.ConfigError):
        prompts.marker("Bad Name")


def test_repo_agents_md_has_required_rules():
    """실제 AGENTS.md에 시스템 메시지용 절이 모두 있고, 핵심 규칙이 들어간다."""
    text = prompts.agents_sections()
    for heading in prompts.AGENTS_SECTIONS:
        assert f"## {heading}" in text
    assert "회의, 결정, 요청, 이슈, 학습, 아이디어, 개인, 참고, 일정, 리뷰" in text
    assert "출처 없는 문장을 쓰지 않는다" in text
    assert "JSON 객체 하나만" in text
    assert "한국어로 답한다" in text
    assert "## 폴더 소유권" not in text


# ---------------------------------------------------------------- mock_llm(W4.1) 약속


def _mock_has(*modes):
    import mock_llm

    return set(modes) <= set(getattr(mock_llm, "KNOWN_MODES", ()))


@pytest.mark.skipif(not _mock_has("canned", "system_prefix", "call_counter"), reason="mock_llm Phase 4 모드 없음")
def test_mock_llm_canned_by_marker_with_system_prefix(vault, tmp_path):
    import mock_llm

    root = _code_root(tmp_path, AGENTS_SAMPLE)
    server = mock_llm.start(canned={"tag": '{"tags": {"L1": "회의"}}'}, system_prefix=True, call_counter=True)
    try:
        prof = {"base_url": server.base_url + "/v1", "model": "mock",
                "detected": detected(json_mode=False, reasoning_toggle={"method": "system_prefix"})}
        client = LlmClient(make_cfg(vault, {"main": prof}, order=("main",)))
        outcome = client.run_task("tag", lambda limit: prompts.build_messages("tag", "L1 | 회의", root=root), validate=accept_all)
        assert outcome.status == "ok" and outcome.data == {"tags": {"L1": "회의"}}
        sent = server.log[-1]["json"]["messages"][0]["content"]
        assert sent.startswith("KB-TASK: tag\n/no_think\n")
    finally:
        server.stop()


@pytest.mark.skipif(not _mock_has("echo_request"), reason="mock_llm Phase 4 모드 없음")
def test_mock_llm_echo_request_body(vault):
    import mock_llm

    server = mock_llm.start(echo_request=True)
    try:
        prof = {"base_url": server.base_url + "/v1", "model": "mock", "detected": detected()}
        client = LlmClient(make_cfg(vault, {"main": prof}, order=("main",)))
        result = client.chat("main", msgs(), reasoning=True, json_mode=True, max_tokens=321)
        echoed = json.loads(result.content)
        assert echoed["reasoning_effort"] == "medium" and echoed["max_tokens"] == 321
        assert echoed["response_format"] == {"type": "json_object"} and echoed["model"] == "mock"
    finally:
        server.stop()
