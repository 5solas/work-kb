"""lib/llm_detect.py 테스트 (W4.3).

대부분 tests/mock_llm.py(W4.1)의 모드로 후보 × accept/reject/ignore, 흔적 전달 방식, 기본 켜짐 서버,
usage 없음, /models 없음, 비전·JSON을 확인한다. mock_llm에 없는 "뒤집힌 뒤 확인 실패" 서버는 이 파일의
작은 가짜 서버(`FlipNoConfirm`)로 만든다.
"""
from __future__ import annotations

import base64
import dataclasses
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml

import mock_llm
from lib import config, core, llm_detect, net
from lib.llm_client import LlmClient
from lib.llm_detect import MAX_POSTS, Budget, BudgetExhausted, DetectError

CANDIDATE_MODES = {
    "reasoning_effort": {"reasoning_effort": "accept"},
    "chat_template_kwargs": {"chat_template_kwargs": "accept"},
    "system_prefix": {"system_prefix": True},
    "reasoning": {"reasoning_object": "accept"},
}
EXPECTED_TOGGLE = {
    "reasoning_effort": {"method": "param", "param_name": "reasoning_effort", "on_value": "medium", "off_value": "none"},
    "chat_template_kwargs": {
        "method": "param", "param_name": "chat_template_kwargs",
        "on_value": {"enable_thinking": True}, "off_value": {"enable_thinking": False},
    },
    "system_prefix": {"method": "system_prefix", "on_value": "/think", "off_value": "/no_think"},
    "reasoning": {"method": "param", "param_name": "reasoning", "on_value": {"enabled": True}, "off_value": {"enabled": False}},
}
ORDER = list(CANDIDATE_MODES)
MODE_NAME = {"reasoning_effort": "reasoning_effort", "chat_template_kwargs": "chat_template_kwargs", "reasoning": "reasoning_object"}


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(net, "_sleep", lambda seconds: None)


def make_client(vault, url, *, model="mock", detected=None, environment="home"):
    profile = {"base_url": url, "model": model}
    if detected is not None:
        profile["detected"] = detected
    config.save_local(vault, {"environment": environment, "llm": {"enabled": True, "profiles": {"main": profile}, "fallback_order": ["main"]}})
    return LlmClient(config.load(vault))


@pytest.fixture
def serve(vault):
    """serve(**modes) → (mock, client). 끝나면 서버를 모두 닫는다."""
    started = []

    def _serve(**modes):
        mock = mock_llm.start(call_counter=True, **modes)
        started.append(mock)
        return mock, make_client(vault, mock.base_url + "/v1")

    yield _serve
    for mock in started:
        mock.stop()


@pytest.fixture
def run_detect(serve):
    def _run(**modes):
        mock, client = serve(**modes)
        return llm_detect.detect(client, "main"), mock

    return _run


def chat_bodies(mock):
    return [entry["json"] for entry in mock.log if entry["method"] == "POST"]


def toggle_core(toggle):
    return {k: toggle[k] for k in ("method", "param_name", "on_value", "off_value") if k in toggle}


def steps(detected):
    return [p["step"] for p in detected["probes"]]


# ---------------------------------------------------------------- 후보 × accept


@pytest.mark.parametrize("default_on", [False, True], ids=["default_off", "default_on"])
@pytest.mark.parametrize("name", ORDER)
def test_each_candidate_accepted_is_adopted(run_detect, name, default_on):
    detected, mock = run_detect(default_thinking=default_on, **CANDIDATE_MODES[name])
    toggle = detected["reasoning_toggle"]
    assert toggle_core(toggle) == EXPECTED_TOGGLE[name]
    assert toggle["evidence"].startswith("trace"), toggle["evidence"]
    assert toggle["source"] == "detected"
    assert detected["baseline_reasoning"] is default_on
    # 기준선 1 + 앞선 후보 1회씩(변화 없음) + 채택 후보 2 + JSON 1 + 비전 1
    index = ORDER.index(name)
    assert mock.counts["chat"] == 1 + index + 2 + 2 <= MAX_POSTS
    assert mock.counts["models"] == 1
    first_dir = "off" if default_on else "on"
    second_dir = "on" if default_on else "off"
    assert steps(detected)[-4:] == [f"{name}:{first_dir}", f"{name}:{second_dir}", "json_mode", "vision"]
    assert steps(detected)[:2] == ["context", "baseline"]


@pytest.mark.parametrize("name", ORDER)
def test_json_and_vision_use_found_off_state(run_detect, name):
    detected, mock = run_detect(default_thinking=True, vision="accept", **CANDIDATE_MODES[name])
    assert detected["json_mode"] is True
    assert detected["vision"] is True
    bodies = chat_bodies(mock)
    json_body, vision_body = bodies[-2], bodies[-1]
    assert json_body["response_format"] == {"type": "json_object"}
    assert "response_format" not in vision_body
    for body in (json_body, vision_body):
        expected = EXPECTED_TOGGLE[name]
        if expected["method"] == "param":
            assert body[expected["param_name"]] == expected["off_value"]
        else:
            assert body["messages"][0]["content"].startswith("/no_think\n")
    # 끄기 상태로 보냈으니 JSON·비전 답에는 생각이 없다
    assert [p["trace"] for p in detected["probes"][-2:]] == [False, False]


def test_no_toggle_means_json_and_vision_without_parameters(run_detect):
    detected, mock = run_detect(vision="accept")
    assert detected["reasoning_toggle"]["method"] == "none"
    json_body, vision_body = chat_bodies(mock)[-2:]
    for body in (json_body, vision_body):
        assert not {"reasoning_effort", "chat_template_kwargs", "reasoning"} & set(body)
        assert not body["messages"][0]["content"].startswith("/")


# ---------------------------------------------------------------- reject / ignore


@pytest.mark.parametrize("default_on", [False, True], ids=["default_off", "default_on"])
@pytest.mark.parametrize("name", ["reasoning_effort", "chat_template_kwargs", "reasoning"])
def test_rejected_candidate_is_not_the_method(run_detect, name, default_on):
    detected, mock = run_detect(default_thinking=default_on, **{MODE_NAME[name]: "reject"})
    toggle = detected["reasoning_toggle"]
    assert toggle["method"] == "none"
    assert toggle["evidence"].startswith("none:")
    assert f"{name}: 거부(400" in toggle["evidence"]
    probe = next(p for p in detected["probes"] if p["step"].startswith(name + ":"))
    assert probe["status"] == "http" and probe["http_status"] == 400
    assert probe["verdict"].startswith("거부")
    # 모든 후보를 한 번씩 보내고 끝(뒤집힘 없음): 기준선 1 + 4 + JSON + 비전
    assert mock.counts["chat"] == 1 + 4 + 2
    assert detected["baseline_reasoning"] is default_on
    assert detected["warnings"]


@pytest.mark.parametrize("default_on", [False, True], ids=["default_off", "default_on"])
@pytest.mark.parametrize("name", ORDER)
def test_ignored_candidate_is_no_evidence(run_detect, name, default_on):
    modes = {MODE_NAME[name]: "ignore"} if name in MODE_NAME else {}  # system_prefix 무시 = 모드 없음
    detected, mock = run_detect(default_thinking=default_on, **modes)
    toggle = detected["reasoning_toggle"]
    assert toggle["method"] == "none"
    assert f"{name}: 200, 변화 없음" in toggle["evidence"]
    assert mock.counts["chat"] == 1 + 4 + 2
    assert all(p.get("verdict") == "변화 없음(200은 증거 아님)" for p in detected["probes"] if ":" in p["step"])


@pytest.mark.parametrize("later", ORDER[1:])
def test_reject_then_later_accept(run_detect, later):
    detected, _ = run_detect(reasoning_effort="reject", **CANDIDATE_MODES[later])
    assert toggle_core(detected["reasoning_toggle"]) == EXPECTED_TOGGLE[later]


@pytest.mark.parametrize("later", ORDER[1:])
def test_ignore_then_later_accept(run_detect, later):
    detected, _ = run_detect(reasoning_effort="ignore", default_thinking=True, **CANDIDATE_MODES[later])
    assert toggle_core(detected["reasoning_toggle"]) == EXPECTED_TOGGLE[later]


def test_first_accepted_candidate_wins(run_detect):
    detected, mock = run_detect(reasoning_effort="accept", chat_template_kwargs="accept", reasoning_object="accept")
    assert detected["reasoning_toggle"]["param_name"] == "reasoning_effort"
    assert mock.counts["chat"] == 1 + 2 + 2


# ---------------------------------------------------------------- 흔적 전달 방식


@pytest.mark.parametrize("default_on", [False, True], ids=["default_off", "default_on"])
@pytest.mark.parametrize(
    "field, seen",
    [("reasoning", "reasoning"), ("reasoning_content", "reasoning_content"), ("think_tag", "think_tag"), ("orphan_think", "think_tag")],
)
def test_trace_fields(run_detect, field, seen, default_on):
    detected, _ = run_detect(trace_field=field, default_thinking=default_on, reasoning_effort="accept")
    toggle = detected["reasoning_toggle"]
    assert toggle["param_name"] == "reasoning_effort"
    assert toggle["evidence"].startswith("trace")
    traced = [p for p in detected["probes"] if p.get("trace")]
    assert traced and {p["trace_field"] for p in traced} == {seen}
    assert detected["baseline_reasoning"] is default_on


def test_orphan_think_answer_is_kept(run_detect):
    detected, _ = run_detect(trace_field="orphan_think", default_thinking=True, reasoning_effort="accept")
    baseline = next(p for p in detected["probes"] if p["step"] == "baseline")
    assert baseline["trace"] is True and baseline["trace_field"] == "think_tag"
    assert baseline["answer"] == '{"ok": true}'  # </think> 뒤의 글만 답
    assert detected["json_mode"] is True


def test_token_ratio_tiebreak_when_no_trace_field(run_detect):
    detected, _ = run_detect(trace_field="none", reasoning_effort="accept")
    toggle = detected["reasoning_toggle"]
    assert toggle["param_name"] == "reasoning_effort"
    assert toggle["evidence"].startswith("tokens"), toggle["evidence"]
    on, off = (p for p in detected["probes"] if p["step"].startswith("reasoning_effort:"))
    assert on["completion_tokens"] >= 2 * off["completion_tokens"]
    assert on["completion_tokens"] - off["completion_tokens"] >= 30


def test_system_prefix_needs_a_trace(run_detect):
    """/think는 프롬프트 자체를 바꾸므로 토큰 수로는 채택하지 않는다(D36)."""
    detected, _ = run_detect(trace_field="none", system_prefix=True)
    assert detected["reasoning_toggle"]["method"] == "none"


def test_no_usage_and_no_trace_is_no_evidence(run_detect):
    detected, _ = run_detect(trace_field="none", no_usage=True, reasoning_effort="accept")
    assert detected["reasoning_toggle"]["method"] == "none"
    assert all(p.get("completion_tokens") is None for p in detected["probes"] if p["step"] != "context")


def test_no_usage_with_trace_still_detects(run_detect):
    detected, _ = run_detect(no_usage=True, chat_template_kwargs="accept")
    assert detected["reasoning_toggle"]["param_name"] == "chat_template_kwargs"
    assert "토큰" not in detected["reasoning_toggle"]["evidence"]


def test_token_rule_thresholds():
    def result(tokens, content="20"):
        return llm_detect.ChatResult(content, None, "stop", {"completion_tokens": tokens}, "main", "m")

    cand = llm_detect.CANDIDATES[0]
    assert llm_detect._signal(result(40), result(10), cand) == "tokens"
    assert llm_detect._signal(result(39), result(10), cand) is None  # 차이 29
    assert llm_detect._signal(result(70), result(40), cand) is None  # 2배 미만
    assert llm_detect._signal(result(70), result(10, content=""), cand) is None  # 끄기 쪽 빈 답
    assert llm_detect._signal(result(70), result(10), llm_detect.CANDIDATES[2]) is None  # /think는 흔적만


# ---------------------------------------------------------------- 기본 켜짐 + content null


def test_default_on_content_null_with_toggle(run_detect):
    detected, mock = run_detect(default_thinking=True, content_null_when_thinking=True, reasoning_effort="accept", vision="accept")
    assert detected["baseline_reasoning"] is True
    assert toggle_core(detected["reasoning_toggle"]) == EXPECTED_TOGGLE["reasoning_effort"]
    baseline = next(p for p in detected["probes"] if p["step"] == "baseline")
    assert baseline["answer"] == "" and baseline["trace"] is True
    assert detected["json_mode"] is True and detected["vision"] is True
    assert "notes" not in detected


def test_default_on_content_null_without_toggle(run_detect, vault):
    detected, mock = run_detect(default_thinking=True, content_null_when_thinking=True, vision="accept")
    assert detected["reasoning_toggle"]["method"] == "none"
    assert detected["baseline_reasoning"] is True
    assert detected["json_mode"] is True
    assert detected["vision"] is True
    assert set(detected["notes"]) == {"json_in_reasoning", "vision_in_reasoning"}
    assert "2048" in detected["warnings"][0]
    # D41: 이 기록을 쓰면 출력 상한 하한 2048이 적용된다
    client = make_client(vault, mock.base_url + "/v1", detected=detected)
    assert client.max_output_tokens("tag", "main") == 2048


def test_always_truncated_reasoning(run_detect):
    detected, _ = run_detect(always_reasoning_truncate=True, reasoning_effort="accept")
    assert detected["baseline_reasoning"] is True
    assert detected["reasoning_toggle"]["method"] == "none"  # 끄기를 보내도 계속 생각한다
    assert detected["json_mode"] is False
    json_probe = next(p for p in detected["probes"] if p["step"] == "json_mode")
    assert json_probe["finish_reason"] == "length"


# ---------------------------------------------------------------- 예산


class FlipNoConfirm:
    """토글 비슷한 것(파라미터든 /think·/no_think든)이 하나라도 있으면 생각한다. 없으면 안 한다.

    그래서 모든 후보가 첫 호출(켜기)에서 뒤집히고 확인 호출(끄기)에서 실패한다.
    """

    TOGGLE_KEYS = {"reasoning_effort", "chat_template_kwargs", "reasoning"}

    def __init__(self):
        self.chats: list[dict] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _go(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else {}
                if self.path.endswith("/models"):
                    payload = {"object": "list", "data": [{"id": "mock", "max_model_len": 8192}]}
                else:
                    fake.chats.append(body)
                    payload = fake.reply(body)
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
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

    def reply(self, body):
        system = body["messages"][0]["content"]
        thinking = bool(self.TOGGLE_KEYS & set(body)) or system.startswith(("/think", "/no_think"))
        user = body["messages"][-1]["content"]
        answer = "빨강" if isinstance(user, list) else ('{"answer": 20}' if "response_format" in body else "20")
        message = {"role": "assistant", "content": answer}
        if thinking:
            message["reasoning"] = "생각 " * 40
        return {"choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 60 if thinking else 2}}

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def flip_no_confirm():
    server = FlipNoConfirm()
    try:
        yield server
    finally:
        server.stop()


def test_worst_case_stays_within_budget(vault, flip_no_confirm):
    client = make_client(vault, flip_no_confirm.base_url)
    detected = llm_detect.detect(client, "main")
    assert len(flip_no_confirm.chats) == MAX_POSTS == 10
    toggle = detected["reasoning_toggle"]
    assert toggle["method"] == "none"
    assert toggle["evidence"].startswith("budget_exhausted"), toggle["evidence"]
    assert "reasoning(확인 못 함)" in toggle["evidence"]
    for name in ("reasoning_effort", "chat_template_kwargs", "system_prefix"):
        assert f"{name}: 뒤집혔으나 반대 방향으로 확인되지 않음" in toggle["evidence"]
    assert "budget_exhausted" in detected["notes"]
    assert detected["warnings"] and "호출 제한" in detected["warnings"][0]
    # JSON·비전 몫은 남겨 두었으므로 둘 다 판정된다
    assert detected["json_mode"] is True and detected["vision"] is True
    assert detected["context_tokens"] == 8192 and detected["context_source"] == "models"
    assert steps(detected)[-2:] == ["json_mode", "vision"]


def test_budget_counts_and_raises():
    budget = Budget(3)
    for _ in range(3):
        budget.take()
    assert budget.used == 3 and budget.remaining == 0
    with pytest.raises(BudgetExhausted):
        budget.take("하나 더")
    assert budget.used == 3


def test_sub_budget_spends_parent():
    parent = Budget(MAX_POSTS)
    child = parent.sub(MAX_POSTS - 2)
    for _ in range(MAX_POSTS - 2):
        child.take()
    with pytest.raises(BudgetExhausted):
        child.take()
    assert parent.used == MAX_POSTS - 2 and parent.remaining == 2
    parent.take()
    parent.take()
    with pytest.raises(BudgetExhausted):
        parent.take()
    small = Budget(1)
    big = small.sub(5)
    big.take()
    with pytest.raises(BudgetExhausted):
        big.take()


def test_detect_reasoning_alone_is_capped(vault, flip_no_confirm):
    """예약 없이 전체 예산을 줘도 후보 탐지는 9회(기준선 + 4×2)로 끝난다."""
    client = make_client(vault, flip_no_confirm.base_url)
    budget = Budget(MAX_POSTS)
    result = llm_detect.detect_reasoning(client, "main", budget)
    assert budget.used == len(flip_no_confirm.chats) == 9
    assert result["reasoning_toggle"]["method"] == "none"
    assert result["reasoning_toggle"]["evidence"].startswith("none:")


# ---------------------------------------------------------------- 컨텍스트


def test_context_from_models(run_detect):
    detected, mock = run_detect()
    assert detected["context_tokens"] == 32768
    assert detected["context_source"] == "models"
    assert detected["probes"][0]["verdict"] == "/models metadata.context_length=32768"


def test_context_length_mode(run_detect):
    detected, _ = run_detect(context_length=4096)
    assert detected["context_tokens"] == 4096


def test_models_missing_means_assumed(run_detect):
    detected, mock = run_detect(no_models=True)
    assert detected["context_tokens"] == 262144
    assert detected["context_source"] == "assumed"
    assert "/models 없음" in detected["probes"][0]["verdict"]
    assert mock.counts["chat"] == 7  # GET /models는 예산에 들지 않음


def test_models_without_this_model_means_assumed(vault):
    mock = mock_llm.start()
    try:
        client = make_client(vault, mock.base_url + "/v1", model="other-model")
        context = llm_detect.detect_context(client, "main")
    finally:
        mock.stop()
    assert context["context_source"] == "assumed"
    assert "other-model" in context["evidence"]


# ---------------------------------------------------------------- JSON·비전


@pytest.mark.parametrize("mode, expected", [("accept", True), ("reject", False), ("ignore", False)])
def test_json_mode(run_detect, mode, expected):
    detected, _ = run_detect(json_mode=mode)
    assert detected["json_mode"] is expected
    probe = next(p for p in detected["probes"] if p["step"] == "json_mode")
    if mode == "reject":
        assert probe["http_status"] == 400 and probe["verdict"].startswith("거부")
    if mode == "ignore":
        assert "json.loads 실패" in probe["verdict"]


@pytest.mark.parametrize("mode, expected", [("accept", True), ("reject", False), ("ignore", False)])
def test_vision(run_detect, mode, expected):
    detected, mock = run_detect(vision=mode)
    assert detected["vision"] is expected
    body = chat_bodies(mock)[-1]
    parts = body["messages"][-1]["content"]
    assert parts[1]["image_url"]["url"] == llm_detect.red_png_data_url()


def test_red_png_is_64_by_64_solid_red():
    data = llm_detect.solid_png()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    pixels = mock_llm._decode_png_pixels(data)
    assert len(pixels) == 64 * 64 and set(pixels) == {(255, 0, 0)}
    url = llm_detect.red_png_data_url()
    assert base64.b64decode(url.split(",", 1)[1]) == data
    assert mock_llm._dominant_colour(url) == ("빨강", "red")


@pytest.mark.parametrize(
    "answer, expected",
    [("빨강", True), ("빨간색입니다", True), ("Red.", True), ("빨갛다", True), ("파랑", False), ("not colored", False), ("hundred", False)],
)
def test_red_words(answer, expected):
    assert bool(llm_detect._RED.search(answer)) is expected


# ---------------------------------------------------------------- 실패·차단


def test_baseline_failure_raises_detect_error(run_detect):
    with pytest.raises(DetectError) as info:
        run_detect(http_500_once=True)
    assert [p["step"] for p in info.value.probes] == ["context", "baseline"]
    assert info.value.probes[1]["http_status"] == 500


def test_unauthorized_baseline_raises(run_detect):
    with pytest.raises(DetectError) as info:
        run_detect(require_key="secret-key-for-test")
    assert "401" in str(info.value)


def test_company_refuses_before_any_request(vault):
    config.save_local(vault, {"environment": "home", "llm": {"enabled": True, "profiles": {
        "main": {"base_url": "https://api.deepinfra.com/v1/openai", "model": "m"}}, "fallback_order": ["main"]}})
    cfg_home = config.load(vault)
    client = LlmClient(cfg_home)
    client.cfg = dataclasses.replace(cfg_home, environment="company")
    with pytest.raises(core.PublicHostRefused):
        llm_detect.detect(client, "main")
    with pytest.raises(core.PublicHostRefused):
        LlmClient(client.cfg)


def test_company_internal_host_is_allowed(vault):
    mock = mock_llm.start(reasoning_effort="accept")
    try:
        client = make_client(vault, mock.base_url + "/v1", environment="company")
        detected = llm_detect.detect(client, "main")
    finally:
        mock.stop()
    assert detected["reasoning_toggle"]["param_name"] == "reasoning_effort"


# ---------------------------------------------------------------- 기록 형식


def test_detected_block_saves_and_drives_client(run_detect, vault):
    detected, mock = run_detect(chat_template_kwargs="accept", vision="accept")
    assert set(detected) >= {"context_tokens", "context_source", "json_mode", "vision", "reasoning_toggle", "baseline_reasoning", "probes"}
    json.dumps(detected, ensure_ascii=False)
    client = make_client(vault, mock.base_url + "/v1", detected=detected)  # save_local: 비밀값 검사 통과
    text = (vault / "config.local.yaml").read_text(encoding="utf-8")
    assert yaml.safe_load(text)["llm"]["profiles"]["main"]["detected"]["reasoning_toggle"]["param_name"] == "chat_template_kwargs"
    on = client.chat("main", [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}], reasoning=True)
    off = client.chat("main", [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}], reasoning=False)
    assert llm_detect.has_trace(on) and not llm_detect.has_trace(off)
    assert chat_bodies(mock)[-2]["chat_template_kwargs"] == {"enable_thinking": True}


def test_probes_never_carry_the_image(run_detect):
    detected, _ = run_detect(vision="accept")
    assert "base64" not in json.dumps(detected)


def test_arithmetic_prompt_is_fixed(run_detect):
    _, mock = run_detect(reasoning_effort="accept")
    bodies = chat_bodies(mock)
    users = {b["messages"][-1]["content"] for b in bodies[:3]}
    assert users == {llm_detect.ARITHMETIC_PROMPT}
    assert all(b["max_tokens"] == llm_detect.PROBE_MAX_TOKENS for b in bodies)
