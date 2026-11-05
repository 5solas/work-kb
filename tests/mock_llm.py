"""OpenAI 호환 LLM 모의 서버 (Phase 0 골격).

표준 라이브러리만 쓴다. 127.0.0.1에만 바인딩한다.

- `start(port=0, **modes) -> RunningMock`
- `python tests/mock_llm.py` → 고정 포트 8083에서 Ctrl+C까지 실행.

모드:
- `require_key="k"`: `Authorization: Bearer k`가 아니면 401.
- `delay_sec=1.5`: 모든 응답 전에 그만큼 잔다(타임아웃 시험용).
- `redirect_to="http://…"`: `GET /models`(`/v1/models`)에 302와 `Location: <값>`으로 답한다.

Phase 4 모드(자세한 뜻은 `KNOWN_MODES`와 tests/test_mock_llm.py):
reasoning_effort / chat_template_kwargs / reasoning_object / json_mode / vision ∈ accept|reject|ignore,
system_prefix, default_thinking, trace_field, content_null_when_thinking, broken_json_once, no_models,
no_usage, always_reasoning_truncate, connection_drop_once, http_500_once, context_length, call_counter
(`RunningMock.counts`), canned(`KB-TASK: <이름>` 첫 줄로 고른 답), echo_request.

Phase 4 리뷰 뒤 더한 모드:
- `rate_limit_on="reasoning_effort"`(또는 목록): 그 최상위 매개변수가 실린 요청에 429. 이름 `"image"`는
  이미지가 실린 요청, `"*"`는 모든 채팅 요청. 429도 `counts["chat"]`에 센다.
- `http_500_always=True`: 모든 채팅 요청에 500(계속 죽은 서버).
- `json_answer="20"`: response_format이 실린 요청의 답 content를 이 문자열로(JSON 객체가 아닌 JSON 시험용).
- `reasoning_tokens_in_usage=False`: 생각해도 `usage.completion_tokens_details.reasoning_tokens`를 보내지
  않는다(기본은 보냄 — DeepInfra처럼). `trace_field="none"`과 함께 쓰면 흔적이 전혀 없는 서버.

라우팅은 `ROUTES` 표(메서드, 경로 정규식, 처리 함수)로 한다. 이후 단계(Phase 4)는 표에 줄을
더하고 `KNOWN_MODES`에 모드 이름을 더한다.
"""
from __future__ import annotations

import base64
import json
import re
import socket
import socketserver
import struct
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

FIXED_PORT = 8083
KNOWN_MODES = {
    "require_key", "delay_sec", "redirect_to",
    # Phase 4
    "reasoning_effort", "chat_template_kwargs", "system_prefix", "reasoning_object", "default_thinking",
    "trace_field", "content_null_when_thinking", "json_mode", "vision", "broken_json_once", "no_models",
    "no_usage", "always_reasoning_truncate", "connection_drop_once", "http_500_once", "context_length",
    "call_counter", "canned", "echo_request",
    # Phase 4 리뷰 뒤
    "rate_limit_on", "http_500_always", "json_answer", "reasoning_tokens_in_usage",
}
_TRIPLE = {"accept", "reject", "ignore"}
_ENUM_MODES = {
    "reasoning_effort": _TRIPLE, "chat_template_kwargs": _TRIPLE, "reasoning_object": _TRIPLE,
    "json_mode": _TRIPLE, "vision": _TRIPLE,
    "trace_field": {"reasoning", "reasoning_content", "think_tag", "orphan_think", "none"},
}

# ---------------------------------------------------------------- 공통 골격


class Request:
    """처리 함수에 넘기는 요청 정보."""

    def __init__(self, *, method, path, query, headers, json_body, bad_json, token, mock):
        self.method = method
        self.path = path
        self.query = query
        self.headers = headers
        self.json = json_body
        self.bad_json = bad_json
        self.token = token
        self.mock = mock
        self.params: dict[str, str] = {}


class RunningMock:
    """실행 중인 모의 서버. `stop()`은 서버를 닫고 스레드를 합쳐 포트를 돌려준다."""

    def __init__(self, server: "_Server", modes: dict):
        self._server = server
        self._thread: threading.Thread | None = None
        self.port: int = server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.log: list[dict] = []
        self.modes = dict(modes)
        self.counts: dict[str, int] = {"models": 0, "chat": 0}
        self.lock = threading.Lock()
        # 첫 채팅 호출에 한 번씩 순서대로 소모되는 오류들
        self.faults: list[str] = [n for n in ("connection_drop_once", "http_500_once", "broken_json_once") if modes.get(n)]

    def stop(self) -> None:
        thread, self._thread = self._thread, None
        if thread is None:
            return
        self._server.shutdown()
        self._server.server_close()
        thread.join(timeout=10)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop()


class _Server(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True
    verbose = False
    mock: RunningMock

    def server_bind(self):
        # HTTPServer.server_bind의 getfqdn(역방향 조회)을 피한다.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def handle_error(self, request, client_address):
        if self.verbose:
            super().handle_error(request, client_address)


def _bearer(value: str | None) -> str | None:
    if not value:
        return None
    scheme, _, token = value.strip().partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        return None
    return token


class _Handler(BaseHTTPRequestHandler):
    server_version = "mock-llm/0"

    def _dispatch(self):
        mock = self.server.mock
        parts = urlsplit(self.path)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
        body, bad_json = None, False
        if raw:
            try:
                body = json.loads(raw)
            except ValueError:
                bad_json = True
        token = _bearer(self.headers.get("Authorization"))
        mock.log.append({"method": self.command, "path": parts.path, "query": query, "has_auth": token is not None, "json": body})
        req = Request(method=self.command, path=parts.path, query=query, headers=self.headers, json_body=body, bad_json=bad_json, token=token, mock=mock)
        status, payload = handle(req)
        headers: dict[str, str] = {}
        if isinstance(payload, Drop):
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            return
        if isinstance(payload, Redirect):
            headers["Location"] = payload.location
            payload = {"redirect": payload.location}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _dispatch

    def log_message(self, format, *args):
        if self.server.verbose:
            super().log_message(format, *args)


def _start(port: int, modes: dict, verbose: bool) -> RunningMock:
    unknown = set(modes) - KNOWN_MODES
    if unknown:
        raise TypeError(f"알 수 없는 모드: {sorted(unknown)}")
    for name, allowed in _ENUM_MODES.items():
        if name in modes and modes[name] not in allowed:
            raise TypeError(f"모드 {name}의 값은 {sorted(allowed)} 중 하나여야 한다: {modes[name]!r}")
    server = _Server(("127.0.0.1", port), _Handler)
    server.verbose = verbose
    mock = RunningMock(server, modes)
    server.mock = mock
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, name=f"{__name__}:{mock.port}", daemon=True)
    mock._thread = thread
    thread.start()
    return mock


def start(port: int = 0, **modes) -> RunningMock:
    return _start(port, modes, verbose=False)


def main() -> None:
    mock = _start(FIXED_PORT, {}, verbose=True)
    print(mock.base_url, flush=True)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        mock.stop()


# ---------------------------------------------------------------- LLM 동작

MODELS = {"object": "list", "data": [{"id": "mock", "object": "model", "metadata": {"context_length": 32768}}]}
COMPLETION_TOKENS = 5
TRACE_UNIT = "생각 "
TRACE_REPEAT = 80  # 80 * 3자 = 240자 → 60 토큰(문자 수 // 4). 기본 답(5 토큰)의 2배를 크게 넘는다.
DEFAULT_ANSWER = '{"ok": true}'


class Redirect:
    """처리 함수가 돌려주면 `Location` 머리글을 붙인다."""

    def __init__(self, location: str):
        self.location = location


class Drop:
    """처리 함수가 돌려주면 응답 없이 연결을 끊는다."""


def _models(req: Request):
    mock = req.mock
    with mock.lock:
        mock.counts["models"] += 1
    target = mock.modes.get("redirect_to")
    if target:
        return 302, Redirect(str(target))
    if mock.modes.get("no_models"):
        return 404, {"error": {"message": "모델 목록이 없습니다"}}
    ctx = mock.modes.get("context_length")
    if isinstance(ctx, int) and not isinstance(ctx, bool):
        data = json.loads(json.dumps(MODELS))
        data["data"][0]["metadata"]["context_length"] = ctx
        return 200, data
    return 200, MODELS


def _text_tokens(text) -> int:
    return len(text) // 4 if isinstance(text, str) else 0


def _all_text_chars(messages) -> int:
    total = 0
    if isinstance(messages, list):
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str):
                total += len(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, str):
                        total += len(part)
                    elif isinstance(part, dict) and isinstance(part.get("text"), str):
                        total += len(part["text"])
    return total


# ---- PNG 해석(표준 라이브러리만): 비전 모드의 "지배적인 색" 계산

_PALETTE = [
    ("빨강", "red", (255, 0, 0)),
    ("초록", "green", (0, 160, 0)),
    ("파랑", "blue", (0, 0, 255)),
    ("노랑", "yellow", (255, 255, 0)),
    ("주황", "orange", (255, 140, 0)),
    ("보라", "purple", (128, 0, 128)),
    ("검정", "black", (0, 0, 0)),
    ("흰색", "white", (255, 255, 255)),
    ("회색", "gray", (128, 128, 128)),
]


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _decode_png_pixels(data: bytes):
    """8비트·비인터레이스 PNG의 (r,g,b) 목록. 해석할 수 없으면 None."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos, idat, plte, header = 8, [], b"", None
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            plte = body
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if header is None or not idat:
        return None
    width, height, depth, ctype, _, _, interlace = header
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype)
    if depth != 8 or interlace != 0 or channels is None or width * height > 1_000_000:
        return None
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error:
        return None
    stride = width * channels
    if len(raw) < (stride + 1) * height:
        return None
    rows, prev = [], bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        ft, line = raw[start], bytearray(raw[start + 1 : start + 1 + stride])
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            if ft == 1:
                line[i] = (line[i] + a) & 255
            elif ft == 2:
                line[i] = (line[i] + b) & 255
            elif ft == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 255
            elif ft == 4:
                line[i] = (line[i] + _paeth(a, b, c)) & 255
            elif ft != 0:
                return None
        rows.append(line)
        prev = line
    pixels = []
    for line in rows:
        for x in range(width):
            px = line[x * channels : (x + 1) * channels]
            if ctype in (0, 4):
                pixels.append((px[0], px[0], px[0]))
            elif ctype == 3:
                idx = px[0] * 3
                if idx + 3 > len(plte):
                    return None
                pixels.append(tuple(plte[idx : idx + 3]))
            else:
                pixels.append((px[0], px[1], px[2]))
    return pixels


def _dominant_colour(url) -> tuple[str, str]:
    unknown = ("알 수 없음", "unknown")
    if not isinstance(url, str) or not url.startswith("data:image/png;base64,"):
        return unknown
    try:
        pixels = _decode_png_pixels(base64.b64decode(url.split(",", 1)[1]))
    except Exception:
        return unknown
    if not pixels:
        return unknown
    votes: dict[int, int] = {}
    for r, g, b in pixels:
        best = min(range(len(_PALETTE)), key=lambda i: (_PALETTE[i][2][0] - r) ** 2 + (_PALETTE[i][2][1] - g) ** 2 + (_PALETTE[i][2][2] - b) ** 2)
        votes[best] = votes.get(best, 0) + 1
    top = max(votes, key=lambda i: votes[i])
    return _PALETTE[top][0], _PALETTE[top][1]


def _image_urls(messages) -> list:
    urls = []
    if isinstance(messages, list):
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        image = part.get("image_url")
                        urls.append(image.get("url") if isinstance(image, dict) else image)
    return urls


def _system_text(messages) -> str:
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, dict) and message.get("role") == "system":
                content = message.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


_TASK_RE = re.compile(r"\AKB-TASK:[ \t]*(\S+)")


def _task_marker(system: str) -> str | None:
    match = _TASK_RE.match(system.lstrip("﻿"))
    return match.group(1) if match else None


def _bad_request(message: str):
    return 400, {"error": {"message": message}}


def _thinking_state(req: Request):
    """(생각 켜짐 여부, 400 응답 또는 None). 기준 상태 → 스위치 순서로 적용한다."""
    modes, body = req.mock.modes, req.json
    thinking = bool(modes.get("default_thinking", False))

    if modes.get("reasoning_effort") == "reject" and "reasoning_effort" in body:
        return thinking, _bad_request("reasoning_effort 매개변수를 지원하지 않습니다")
    if modes.get("reasoning_effort") == "accept" and "reasoning_effort" in body:
        value = body["reasoning_effort"]
        thinking = value not in (None, False, "none", "off", "disabled")

    kwargs = body.get("chat_template_kwargs")
    if modes.get("chat_template_kwargs") == "reject" and "chat_template_kwargs" in body:
        return thinking, _bad_request("chat_template_kwargs 매개변수를 지원하지 않습니다")
    if modes.get("chat_template_kwargs") == "accept" and isinstance(kwargs, dict):
        for key in ("enable_thinking", "thinking"):
            if key in kwargs:
                thinking = bool(kwargs[key])

    if modes.get("system_prefix"):
        system = _system_text(body.get("messages"))
        if _task_marker(system) is not None:
            system = system.split("\n", 1)[1] if "\n" in system else ""
        system = system.lstrip()
        if system.startswith("/no_think"):
            thinking = False
        elif system.startswith("/think"):
            thinking = True

    reasoning = body.get("reasoning")
    if modes.get("reasoning_object") == "reject" and "reasoning" in body:
        return thinking, _bad_request("reasoning 매개변수를 지원하지 않습니다")
    if modes.get("reasoning_object") == "accept" and isinstance(reasoning, dict) and "enabled" in reasoning:
        thinking = bool(reasoning["enabled"])

    if modes.get("always_reasoning_truncate"):
        thinking = True
    return thinking, None


def _answer_for(req: Request, structured: bool):
    """(답 문자열 또는 오류 응답). echo → canned → 비전 → 기본 순."""
    modes, body = req.mock.modes, req.json
    if modes.get("echo_request"):
        return json.dumps(body, ensure_ascii=False), None
    canned = modes.get("canned")
    if isinstance(canned, dict):
        name = _task_marker(_system_text(body.get("messages")))
        if name is not None and name in canned:
            value = canned[name]
            return (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)), None
    urls = _image_urls(body.get("messages"))
    answer = DEFAULT_ANSWER
    json_answer = modes.get("json_answer")
    if structured and isinstance(json_answer, str):
        answer = json_answer
    if urls:
        vision = modes.get("vision")
        if vision == "reject":
            return None, _bad_request("이미지 입력을 지원하지 않습니다")
        if vision == "accept":
            ko, en = _dominant_colour(urls[0])
            answer = json.dumps({"ok": True, "color": ko, "color_en": en, "description": f"이미지의 지배적인 색은 {ko}({en})입니다"}, ensure_ascii=False)
    return answer, None


def _rate_limited(setting, body: dict) -> bool:
    """`rate_limit_on` 모드: 이 요청이 429를 받을 대상인가."""
    if not setting:
        return False
    names = [setting] if isinstance(setting, str) else list(setting)
    for name in names:
        if name == "*":
            return True
        if name == "image":
            if _image_urls(body.get("messages")):
                return True
        elif name in body:
            return True
    return False


def _chat(req: Request):
    mock, modes = req.mock, req.mock.modes
    with mock.lock:
        mock.counts["chat"] += 1
        fault = mock.faults.pop(0) if mock.faults else None
    if fault == "connection_drop_once":
        return 200, Drop()
    if fault == "http_500_once":
        return 500, {"error": {"message": "서버 내부 오류"}}
    if modes.get("http_500_always"):
        return 500, {"error": {"message": "서버 내부 오류(계속)"}}
    if req.bad_json or not isinstance(req.json, dict):
        return _bad_request("잘못된 JSON")
    body = req.json
    messages = body.get("messages")
    if _rate_limited(modes.get("rate_limit_on"), body):
        return 429, {"error": {"message": "요청이 너무 많습니다(rate limited)"}}

    ctx = modes.get("context_length")
    if isinstance(ctx, int) and not isinstance(ctx, bool) and _all_text_chars(messages) / 2.5 > ctx:
        return _bad_request(f"This model's maximum context length is {ctx} tokens. context length exceeded")
    if "response_format" in body and modes.get("json_mode") == "reject":
        return _bad_request("response_format 매개변수를 지원하지 않습니다")

    thinking, error = _thinking_state(req)
    if error is not None:
        return error
    answer, error = _answer_for(req, "response_format" in body)
    if error is not None:
        return error
    if modes.get("json_mode") == "ignore" and "response_format" in body:
        answer = f"결과는 다음과 같습니다. {answer}"  # 제약을 무시한 서버: 말이 섞인다
    if fault == "broken_json_once":
        answer = '{"ok": tru'

    max_tokens = body.get("max_completion_tokens") or body.get("max_tokens")
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
        max_tokens = None

    trace = TRACE_UNIT * TRACE_REPEAT if thinking else ""
    finish = "stop"
    if thinking and (modes.get("always_reasoning_truncate") or (max_tokens is not None and _text_tokens(trace) >= max_tokens)):
        cap = max_tokens if max_tokens is not None else _text_tokens(trace)
        trace = (TRACE_UNIT * (cap * 4 // len(TRACE_UNIT) + 2))[: cap * 4]
        answer, finish = "", "length"
    elif max_tokens is not None and _text_tokens(trace) + _text_tokens(answer) > max(max_tokens, 1):
        answer = answer[: max(max_tokens - _text_tokens(trace), 0) * 4]
        finish = "length"

    field = modes.get("trace_field") or "reasoning"
    message: dict = {"role": "assistant", "content": answer}
    if trace:
        if modes.get("content_null_when_thinking") and finish != "length":
            message["content"] = None
            message["reasoning_content" if field == "reasoning_content" else "reasoning"] = trace + answer
        elif field in ("reasoning", "reasoning_content"):
            message[field] = trace
        elif field == "think_tag":
            message["content"] = f"<think>{trace}</think>{answer}" if finish != "length" else f"<think>{trace}"
        elif field == "orphan_think":
            message["content"] = f"{trace}</think>{answer}" if finish != "length" else trace
        # "none": 생각은 토큰만 쓰고 흔적은 전달되지 않는다.

    prompt = _prompt_tokens(messages)
    trace_tokens = _text_tokens(trace)
    completion = max(COMPLETION_TOKENS, trace_tokens + _text_tokens(answer)) if finish == "stop" else max(trace_tokens + _text_tokens(answer), 1)
    result = {
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "model": body.get("model"),
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
    }
    if not modes.get("no_usage"):
        usage = {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}
        if trace_tokens and modes.get("reasoning_tokens_in_usage", True):
            usage["completion_tokens_details"] = {"reasoning_tokens": trace_tokens}
        result["usage"] = usage
    return 200, result


def _prompt_tokens(messages) -> int:
    """모든 메시지 content의 문자 수 합 // 4. 문자열만 센다(문자열이 아닌 content 조각은 무시)."""
    total = 0
    if isinstance(messages, list):
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str):
                total += len(content)
            elif isinstance(content, list):
                total += sum(len(part) for part in content if isinstance(part, str))
    return total // 4


ROUTES = [
    ("GET", r"(/v1)?/models", _models),
    ("POST", r"(/v1)?/chat/completions", _chat),
]


def handle(req: Request):
    delay = req.mock.modes.get("delay_sec")
    if delay:
        time.sleep(float(delay))
    key = req.mock.modes.get("require_key")
    if key is not None and req.token != key:
        return 401, {"error": {"message": "인증이 필요합니다"}}
    for method, pattern, func in ROUTES:
        if method != req.method:
            continue
        match = re.fullmatch(pattern, req.path)
        if match:
            req.params = match.groupdict()
            return func(req)
    return 404, {"error": f"없는 경로: {req.method} {req.path}"}


if __name__ == "__main__":
    main()
