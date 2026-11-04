"""tests/mock_llm.py Phase 4 모드 시험."""
from __future__ import annotations

import base64
import http.client
import json
import struct
import sys
import urllib.error
import urllib.request
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mock_llm  # noqa: E402


def call(mock, path="/v1/chat/completions", body=None, method=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(mock.base_url + path, data=data, method=method or ("POST" if body is not None else "GET"))
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def chat(mock, system="시스템", user="안녕", **extra):
    body = {"model": "m", "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], **extra}
    return call(mock, body=body)


def msg(result):
    return result[1]["choices"][0]["message"]


def png(color, size=8):
    def chunk(kind, data):
        raw = kind + data
        return struct.pack(">I", len(data)) + raw + struct.pack(">I", zlib.crc32(raw))

    row = b"\x00" + bytes(color) * size
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * size))
        + chunk(b"IEND", b"")
    )


def image_message(color):
    url = "data:image/png;base64," + base64.b64encode(png(color)).decode()
    return {"model": "m", "messages": [{"role": "user", "content": [{"type": "text", "text": "이 이미지는?"}, {"type": "image_url", "image_url": {"url": url}}]}]}


@pytest.fixture
def start():
    started = []

    def make(**modes):
        mock = mock_llm.start(**modes)
        started.append(mock)
        return mock

    yield make
    for mock in started:
        mock.stop()


def thinking(result):
    return result[1]["usage"]["completion_tokens"] >= 40


# ---------------------------------------------------------------- 기본 모양

def test_default_reply_unchanged(start):
    status, data = chat(start())
    assert status == 200
    assert data["choices"][0]["message"] == {"role": "assistant", "content": '{"ok": true}'}
    assert data["usage"]["completion_tokens"] == 5
    assert data["choices"][0]["finish_reason"] == "stop"


def test_unknown_mode_and_bad_enum_rejected():
    with pytest.raises(TypeError):
        mock_llm.start(nope=1)
    with pytest.raises(TypeError):
        mock_llm.start(trace_field="x")
    with pytest.raises(TypeError):
        mock_llm.start(vision="maybe")


# ---------------------------------------------------------------- 생각 스위치

def test_default_thinking_and_trace_size(start):
    assert not thinking(chat(start()))
    result = chat(start(default_thinking=True))
    assert thinking(result)
    assert msg(result)["reasoning"].count("생각") >= 40
    assert msg(result)["content"] == '{"ok": true}'


@pytest.mark.parametrize("effort,expected", [("none", False), ("low", True), ("high", True)])
def test_reasoning_effort_accept(start, effort, expected):
    mock = start(reasoning_effort="accept", default_thinking=(effort == "none"))
    assert thinking(chat(mock, reasoning_effort=effort)) is expected


def test_reasoning_effort_reject_and_ignore(start):
    reject = start(reasoning_effort="reject")
    assert chat(reject, reasoning_effort="low")[0] == 400
    assert chat(reject)[0] == 200
    ignore = start(reasoning_effort="ignore")
    result = chat(ignore, reasoning_effort="high")
    assert result[0] == 200 and not thinking(result)
    assert thinking(chat(start(reasoning_effort="ignore", default_thinking=True), reasoning_effort="none"))
    assert not thinking(chat(start(), reasoning_effort="high"))


def test_chat_template_kwargs(start):
    accept = start(chat_template_kwargs="accept")
    assert thinking(chat(accept, chat_template_kwargs={"enable_thinking": True}))
    assert not thinking(chat(accept, chat_template_kwargs={"enable_thinking": False}))
    on = start(chat_template_kwargs="accept", default_thinking=True)
    assert not thinking(chat(on, chat_template_kwargs={"enable_thinking": False}))
    assert chat(start(chat_template_kwargs="reject"), chat_template_kwargs={"enable_thinking": True})[0] == 400
    result = chat(start(chat_template_kwargs="ignore"), chat_template_kwargs={"enable_thinking": True})
    assert result[0] == 200 and not thinking(result)


def test_system_prefix(start):
    mock = start(system_prefix=True)
    assert thinking(chat(mock, system="/think\n규칙"))
    assert not thinking(chat(mock, system="/no_think\n규칙"))
    assert not thinking(chat(mock, system="규칙 /think"))
    assert not thinking(chat(start(system_prefix=True, default_thinking=True), system="/no_think 규칙"))
    assert thinking(chat(mock, system="KB-TASK: t1\n/think\n규칙"))
    assert not thinking(chat(start(), system="/think\n규칙"))


def test_reasoning_object(start):
    accept = start(reasoning_object="accept")
    assert thinking(chat(accept, reasoning={"enabled": True}))
    assert not thinking(chat(accept, reasoning={"enabled": False}))
    assert chat(start(reasoning_object="reject"), reasoning={"enabled": True})[0] == 400
    result = chat(start(reasoning_object="ignore"), reasoning={"enabled": True})
    assert result[0] == 200 and not thinking(result)


# ---------------------------------------------------------------- 흔적 전달

def test_trace_fields(start):
    for field in ("reasoning", "reasoning_content"):
        message = msg(chat(start(default_thinking=True, trace_field=field)))
        assert message[field].startswith("생각") and message["content"] == '{"ok": true}'
        other = "reasoning_content" if field == "reasoning" else "reasoning"
        assert other not in message
    tagged = msg(chat(start(default_thinking=True, trace_field="think_tag")))
    assert tagged["content"].startswith("<think>생각") and tagged["content"].endswith('</think>{"ok": true}')
    orphan = msg(chat(start(default_thinking=True, trace_field="orphan_think")))
    assert "<think>" not in orphan["content"] and orphan["content"].endswith('</think>{"ok": true}')
    hidden = chat(start(default_thinking=True, trace_field="none"))
    assert msg(hidden) == {"role": "assistant", "content": '{"ok": true}'}
    assert thinking(hidden)


def test_trace_absent_when_not_thinking(start):
    assert msg(chat(start(trace_field="think_tag")))["content"] == '{"ok": true}'


def test_content_null_when_thinking(start):
    message = msg(chat(start(default_thinking=True, content_null_when_thinking=True)))
    assert message["content"] is None and message["reasoning"].endswith('{"ok": true}')
    rc = msg(chat(start(default_thinking=True, content_null_when_thinking=True, trace_field="reasoning_content")))
    assert rc["content"] is None and rc["reasoning_content"].endswith('{"ok": true}')
    assert msg(chat(start(content_null_when_thinking=True)))["content"] == '{"ok": true}'


def test_always_reasoning_truncate(start):
    mock = start(always_reasoning_truncate=True)
    result = chat(mock, max_tokens=30)
    assert result[1]["choices"][0]["finish_reason"] == "length"
    assert msg(result)["content"] == ""
    assert msg(result)["reasoning"]
    assert result[1]["usage"]["completion_tokens"] == 30
    assert chat(mock)[1]["choices"][0]["finish_reason"] == "length"


def test_max_tokens_truncation_when_trace_too_long(start):
    result = chat(start(default_thinking=True), max_tokens=20)
    assert result[1]["choices"][0]["finish_reason"] == "length"
    assert msg(result)["content"] == ""
    assert chat(start(default_thinking=True), max_tokens=500)[1]["choices"][0]["finish_reason"] == "stop"
    tagged = msg(chat(start(default_thinking=True, trace_field="think_tag"), max_tokens=20))
    assert "</think>" not in tagged["content"]


# ---------------------------------------------------------------- JSON / 비전

def test_json_mode(start):
    fmt = {"response_format": {"type": "json_object"}}
    assert chat(start(json_mode="reject"), **fmt)[0] == 400
    assert chat(start(json_mode="reject"))[0] == 200
    assert json.loads(msg(chat(start(json_mode="accept"), **fmt))["content"]) == {"ok": True}
    ignored = chat(start(json_mode="ignore"), **fmt)
    assert ignored[0] == 200
    with pytest.raises(ValueError):
        json.loads(msg(ignored)["content"])
    assert json.loads(msg(chat(start(json_mode="ignore")))["content"]) == {"ok": True}


def test_vision(start):
    red = image_message((255, 0, 0))
    content = json.loads(msg(call(start(vision="accept"), body=red))["content"])
    assert content["color"] == "빨강" and content["color_en"] == "red"
    blue = json.loads(msg(call(start(vision="accept"), body=image_message((0, 0, 255))))["content"])
    assert blue["color_en"] == "blue"
    assert call(start(vision="reject"), body=red)[0] == 400
    ignored = call(start(vision="ignore"), body=red)
    assert ignored[0] == 200 and msg(ignored)["content"] == '{"ok": true}'
    assert chat(start(vision="reject"))[0] == 200


# ---------------------------------------------------------------- 오류와 계량

def test_broken_json_once(start):
    mock = start(broken_json_once=True)
    with pytest.raises(ValueError):
        json.loads(msg(chat(mock))["content"])
    assert json.loads(msg(chat(mock))["content"]) == {"ok": True}


def test_no_models_and_context_length_models(start):
    assert call(start(no_models=True), "/v1/models")[0] == 404
    assert call(start(no_models=True), "/models")[0] == 404
    status, data = call(start(context_length=1000), "/v1/models")
    assert status == 200 and data["data"][0]["metadata"]["context_length"] == 1000
    assert mock_llm.MODELS["data"][0]["metadata"]["context_length"] == 32768
    assert call(start(), "/v1/models")[0] == 200


def test_no_usage(start):
    result = chat(start(no_usage=True))
    assert result[0] == 200 and "usage" not in result[1]


def test_connection_drop_once(start):
    mock = start(connection_drop_once=True)
    with pytest.raises(Exception):
        chat(mock)
    assert chat(mock)[0] == 200
    assert mock.counts["chat"] == 2


def test_http_500_once(start):
    mock = start(http_500_once=True)
    assert chat(mock)[0] == 500
    assert chat(mock)[0] == 200


def test_faults_are_consumed_in_order(start):
    mock = start(http_500_once=True, broken_json_once=True)
    assert chat(mock)[0] == 500
    with pytest.raises(ValueError):
        json.loads(msg(chat(mock))["content"])
    assert json.loads(msg(chat(mock))["content"]) == {"ok": True}


def test_context_length(start):
    mock = start(context_length=100)
    assert chat(mock, user="가" * 100)[0] == 200
    status, data = chat(mock, user="가" * 400)
    assert status == 400 and "context length exceeded" in data["error"]["message"]


def test_call_counter(start):
    mock = start(call_counter=True)
    assert mock.counts == {"models": 0, "chat": 0}
    call(mock, "/v1/models")
    call(mock, "/models")
    for _ in range(3):
        chat(mock)
    assert mock.counts == {"models": 2, "chat": 3}


# ---------------------------------------------------------------- canned / echo

def test_canned_by_marker(start):
    canned = {"t1": '{"tags": {"a": "x"}}', "t2": '{"summary": "요약"}'}
    mock = start(canned=canned)
    assert msg(chat(mock, system="KB-TASK: t1\n규칙"))["content"] == canned["t1"]
    assert msg(chat(mock, system="KB-TASK: t2\n규칙"))["content"] == canned["t2"]
    assert msg(chat(mock, system="KB-TASK: t9\n규칙"))["content"] == '{"ok": true}'
    assert msg(chat(mock, system="규칙\nKB-TASK: t1"))["content"] == '{"ok": true}'
    assert msg(chat(mock, system="규칙"))["content"] == '{"ok": true}'


def test_canned_with_trace(start):
    mock = start(canned={"t1": '{"a": 1}'}, default_thinking=True, trace_field="think_tag")
    assert msg(chat(mock, system="KB-TASK: t1\n규칙"))["content"].endswith('</think>{"a": 1}')


def test_echo_request(start):
    result = chat(start(echo_request=True), temperature=0.2)
    echoed = json.loads(msg(result)["content"])
    assert echoed["temperature"] == 0.2
    assert echoed["messages"][1]["content"] == "안녕"
    assert result[1]["usage"]["completion_tokens"] > 5


def test_usage_computed_from_actual_text(start):
    assert chat(start(canned={"t": "가" * 400}), system="KB-TASK: t\n")[1]["usage"]["completion_tokens"] == 100
    on = chat(start(default_thinking=True))[1]["usage"]
    assert on["completion_tokens"] == 60 + 3 and on["completion_tokens_details"]["reasoning_tokens"] == 60
    assert chat(start())[1]["usage"]["prompt_tokens"] == (len("시스템") + len("안녕")) // 4


# ---------------------------------------------------------------- 기존 모드 유지

def test_existing_modes_still_work(start):
    assert call(start(require_key="k"), "/v1/models")[0] == 401
    redirected = start(redirect_to="http://127.0.0.1:1/x")

    conn = http.client.HTTPConnection("127.0.0.1", redirected.port, timeout=5)
    conn.request("GET", "/models")
    resp = conn.getresponse()
    resp.read()
    conn.close()
    assert resp.status == 302 and resp.getheader("Location") == "http://127.0.0.1:1/x"
