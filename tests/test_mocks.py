"""모의 서버 세 개(Jira, Confluence, LLM)의 Phase 0 계약 시험. urllib만 쓴다."""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request

import pytest

import mock_all
import mock_confluence
import mock_jira
import mock_llm

JSON_CT = "application/json; charset=utf-8"
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 환경 프록시 무시
_send = _OPENER.open  # HTTP 요청(파일 open 아님)


def call(url, *, method="GET", token=None, body=None, raw=None, timeout=5.0):
    """(status, content-type, 파싱한 JSON)을 돌려준다."""
    data = raw if raw is not None else (json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method)
    if token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with _send(req, timeout=timeout) as resp:
            return resp.status, resp.headers.get("Content-Type"), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        try:
            return err.code, err.headers.get("Content-Type"), json.loads(err.read().decode("utf-8"))
        finally:
            err.close()


def _assert_port_free(module, port):
    again = module.start(port=port)
    try:
        assert again.port == port
        status, _, _ = call(again.base_url + "/nope")
        assert status == 404
    finally:
        again.stop()


# ---------------------------------------------------------------- 공통 계약


@pytest.mark.parametrize("module", [mock_jira, mock_confluence, mock_llm], ids=lambda m: m.__name__)
def test_running_mock_contract(module):
    server = module.start()
    try:
        assert server.port > 0
        assert server.base_url == f"http://127.0.0.1:{server.port}"
        assert server.log == []
        assert server.modes == {}
        assert server._server.server_address[0] == "127.0.0.1"
        assert server._server.allow_reuse_address is False
        status, ctype, body = call(server.base_url + "/no/such/path")
        assert status == 404
        assert ctype == JSON_CT
        assert "error" in body
        port = server.port
    finally:
        server.stop()
    server.stop()  # 두 번 불러도 안전
    _assert_port_free(module, port)


@pytest.mark.parametrize(("module", "port"), [(mock_jira, 8081), (mock_confluence, 8082), (mock_llm, 8083)], ids=lambda v: getattr(v, "__name__", str(v)))
def test_fixed_ports(module, port):
    assert module.FIXED_PORT == port


@pytest.mark.parametrize("module", [mock_jira, mock_confluence, mock_llm], ids=lambda m: m.__name__)
def test_unknown_mode_rejected(module):
    with pytest.raises(TypeError):
        module.start(no_such_mode=1)


# ---------------------------------------------------------------- Jira


def test_jira_endpoints(mock_jira):
    base = mock_jira.base_url
    status, ctype, me = call(base + "/rest/api/2/myself", token="tok")
    assert (status, ctype) == (200, JSON_CT)
    assert me == {"name": "honggildong", "key": "honggildong", "displayName": "홍길동", "emailAddress": "hong@example.invalid", "timeZone": "Asia/Seoul"}

    status, _, projects = call(base + "/rest/api/2/project", token="tok")
    assert status == 200
    assert projects == [{"id": "10000", "key": "KB", "name": "지식 저장고"}, {"id": "10001", "key": "OPS", "name": "운영"}]

    status, _, info = call(base + "/rest/api/2/serverInfo", token="tok")
    assert status == 200
    assert info == {"baseUrl": base, "version": "9.17.0", "deploymentType": "Server"}

    status, _, missing = call(base + "/rest/api/2/nothing", token="tok")
    assert status == 404 and "error" in missing


def test_jira_requires_bearer(mock_jira):
    base = mock_jira.base_url
    for path in ("/rest/api/2/myself", "/rest/api/2/project", "/rest/api/2/serverInfo", "/rest/api/2/nothing"):
        status, ctype, body = call(base + path)
        assert (status, ctype) == (401, JSON_CT)
        assert body == {"errorMessages": ["인증이 필요합니다"]}
    req = urllib.request.Request(base + "/rest/api/2/myself", headers={"Authorization": "Bearer "})
    with pytest.raises(urllib.error.HTTPError) as exc:
        _send(req, timeout=5)
    assert exc.value.code == 401
    exc.value.close()
    req = urllib.request.Request(base + "/rest/api/2/myself", headers={"Authorization": "Basic dXNlcjpwdw=="})
    with pytest.raises(urllib.error.HTTPError) as exc:
        _send(req, timeout=5)
    assert exc.value.code == 401
    exc.value.close()


def test_jira_log(mock_jira):
    base = mock_jira.base_url
    call(base + "/rest/api/2/myself")
    call(base + "/rest/api/2/myself?expand=groups", token="tok")
    assert mock_jira.log == [
        {"method": "GET", "path": "/rest/api/2/myself", "query": {}, "has_auth": False, "json": None},
        {"method": "GET", "path": "/rest/api/2/myself", "query": {"expand": "groups"}, "has_auth": True, "json": None},
    ]


def test_jira_bad_token_mode():
    server = mock_jira.start(bad_token="expired")
    try:
        assert server.modes == {"bad_token": "expired"}
        status, _, body = call(server.base_url + "/rest/api/2/myself", token="expired")
        assert status == 401 and body == {"errorMessages": ["인증이 필요합니다"]}
        status, _, body = call(server.base_url + "/rest/api/2/myself", token="good")
        assert status == 200 and body["displayName"] == "홍길동"
        assert [entry["has_auth"] for entry in server.log] == [True, True]
    finally:
        server.stop()


# ---------------------------------------------------------------- Confluence

PERSONAL = {"results": [{"id": 123, "key": "~honggildong", "name": "홍길동", "type": "personal"}], "size": 1}


def test_confluence_endpoints(mock_confluence):
    base = mock_confluence.base_url
    status, ctype, user = call(base + "/rest/api/user/current", token="tok")
    assert (status, ctype) == (200, JSON_CT)
    assert user == {"type": "known", "username": "honggildong", "userKey": "8a7f1c2e3d4b5a6f", "displayName": "홍길동"}

    status, _, spaces = call(base + "/rest/api/space?spaceKey=~honggildong", token="tok")
    assert status == 200 and spaces == PERSONAL
    status, _, spaces = call(base + "/rest/api/space?spaceKey=%7Ehonggildong", token="tok")
    assert status == 200 and spaces == PERSONAL
    status, _, spaces = call(base + "/rest/api/space?type=personal", token="tok")
    assert status == 200 and spaces == PERSONAL
    status, _, spaces = call(base + "/rest/api/space?spaceKey=~kimcheolsu", token="tok")
    assert status == 200 and spaces == {"results": [], "size": 0}

    assert mock_confluence.log[1]["query"] == {"spaceKey": "~honggildong"}


def test_confluence_requires_bearer(mock_confluence):
    base = mock_confluence.base_url
    for path in ("/rest/api/user/current", "/rest/api/space?type=personal"):
        status, ctype, body = call(base + path)
        assert (status, ctype) == (401, JSON_CT)
        assert body == {"message": "인증이 필요합니다"}
    assert [(e["method"], e["path"], e["has_auth"]) for e in mock_confluence.log] == [
        ("GET", "/rest/api/user/current", False),
        ("GET", "/rest/api/space", False),
    ]


def test_confluence_bad_token_mode():
    server = mock_confluence.start(bad_token="expired")
    try:
        status, _, body = call(server.base_url + "/rest/api/user/current", token="expired")
        assert status == 401 and body == {"message": "인증이 필요합니다"}
        status, _, body = call(server.base_url + "/rest/api/user/current", token="good")
        assert status == 200 and body["displayName"] == "홍길동"
    finally:
        server.stop()


# ---------------------------------------------------------------- LLM

MODELS = {"object": "list", "data": [{"id": "mock", "object": "model", "metadata": {"context_length": 32768}}]}


def test_llm_models(mock_llm):
    for path in ("/models", "/v1/models"):
        status, ctype, body = call(mock_llm.base_url + path)
        assert (status, ctype) == (200, JSON_CT)
        assert body == MODELS
    assert [(e["method"], e["path"], e["has_auth"]) for e in mock_llm.log] == [("GET", "/models", False), ("GET", "/v1/models", False)]


def test_llm_chat(mock_llm):
    messages = [
        {"role": "system", "content": "가" * 10},
        {"role": "user", "content": "abcdefgh"},
        {"role": "user", "content": ["abcd", {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]},
        {"role": "assistant", "content": None},
    ]
    request = {"model": "test-model", "messages": messages}
    for path in ("/chat/completions", "/v1/chat/completions"):
        status, ctype, body = call(mock_llm.base_url + path, method="POST", body=request)
        assert (status, ctype) == (200, JSON_CT)
        p = (10 + 8 + 4) // 4
        assert body == {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "model": "test-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"ok": true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": p, "completion_tokens": 5, "total_tokens": p + 5},
        }
        assert json.loads(body["choices"][0]["message"]["content"]) == {"ok": True}
    assert mock_llm.log[0]["method"] == "POST"
    assert mock_llm.log[0]["path"] == "/chat/completions"
    assert mock_llm.log[0]["json"] == request


def test_llm_malformed_json(mock_llm):
    status, ctype, body = call(mock_llm.base_url + "/chat/completions", method="POST", raw=b"{not json")
    assert (status, ctype) == (400, JSON_CT)
    assert body == {"error": {"message": "잘못된 JSON"}}
    assert mock_llm.log[0]["json"] is None


def test_llm_require_key_mode():
    server = mock_llm.start(require_key="k")
    try:
        status, _, body = call(server.base_url + "/models")
        assert status == 401 and "error" in body
        status, _, _ = call(server.base_url + "/models", token="wrong")
        assert status == 401
        status, _, body = call(server.base_url + "/v1/models", token="k")
        assert status == 200 and body == MODELS
        status, _, body = call(server.base_url + "/chat/completions", method="POST", token="k", body={"model": "m", "messages": []})
        assert status == 200 and body["usage"]["prompt_tokens"] == 0
        assert [e["has_auth"] for e in server.log] == [False, True, True, True]
    finally:
        server.stop()


def test_llm_delay_mode():
    server = mock_llm.start(delay_sec=1.2)
    try:
        began = time.monotonic()
        status, _, _ = call(server.base_url + "/models")
        elapsed = time.monotonic() - began
        assert status == 200
        assert elapsed >= 1.0
        with pytest.raises((TimeoutError, socket.timeout, urllib.error.URLError)):
            call(server.base_url + "/models", timeout=0.3)
    finally:
        server.stop()


# ---------------------------------------------------------------- mock_all


def test_start_all():
    mocks = mock_all.start_all(jira_port=0, confluence_port=0, llm_port=0)
    jira, confluence, llm = mocks
    ports = [m.port for m in mocks]
    try:
        assert isinstance(jira, mock_jira.RunningMock)
        assert isinstance(confluence, mock_confluence.RunningMock)
        assert isinstance(llm, mock_llm.RunningMock)
        assert len(set(ports)) == 3
        assert call(jira.base_url + "/rest/api/2/myself", token="t")[2]["displayName"] == "홍길동"
        assert call(confluence.base_url + "/rest/api/user/current", token="t")[2]["displayName"] == "홍길동"
        assert call(llm.base_url + "/models")[2] == MODELS
    finally:
        mock_all.stop_all(mocks)
    for module, port in zip((mock_jira, mock_confluence, mock_llm), ports):
        _assert_port_free(module, port)
