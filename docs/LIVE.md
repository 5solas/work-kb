# 실 모델 검증 (live)

지시서 §7의 '실 모델 검증'. 집에서만, DeepInfra 키가 있을 때만 돈다. 리드가 요점을 DECISIONS에 옮긴다.

## 실행

```powershell
$env:KB_REQUIRE_LIVE='1'   # 키가 없으면 skip 대신 fail
.venv\Scripts\python.exe -m pytest -q -m live tests/test_live.py -s
```

- `pytest.ini`가 `live`를 기본 실행에서 뺀다. `-m live`로만 돈다.
- `DEEPINFRA_API_KEY`가 환경에 있어야 한다. 없으면 skip(`KB_REQUIRE_LIVE=1`이면 fail).
- 저장소의 `config.local.yaml`이나 임시 볼트의 환경이 `home`이 아니면 fail(회사에서는 돌리지 않는다).
- 임시 볼트(한글 경로)에 `config.yaml`을 복사하고 `config.local.yaml`(home, KB·OPS, main=Nemotron, fallback=Gemma,
  키는 `env:DEEPINFRA_API_KEY` 참조만)을 `yaml.safe_dump`로 쓴다. 저장소의 볼트·설정은 건드리지 않는다.
- 채팅 POST 상한은 세션 전체 20회다. `requests.Session.request`에서 실제 전송(429 재시도 포함)을 센다.
  21번째는 보내기 전에 fail.

## 무엇을 보나

| 테스트 | 호출 | 검사(형태만, 문구는 보지 않음) |
|---|---|---|
| `test_detect_main` | `llm_detect.detect(client, "main")`, POST ≤ 10 | 블록에 json_mode·vision·reasoning_toggle·baseline_reasoning·context_tokens. 결과를 볼트 설정에 저장(probes 뺌) |
| `test_t1_tag` | `compile.run(cfg, tasks=["tag"], day=…)` | `.state/compile.json`의 tag 항목이 ok, 태그가 고정 10개 중 하나 |
| `test_t2_decisions` | `compile.run(cfg, tasks=["decisions"], day=…)` | `wiki/decisions.md`에 1건 이상, `상태: 제안`, 출처가 daily 줄로 풀림(src 해시), daily 바이트 불변 |
| `test_t6_image_ingest` | `ingest_images.run(cfg)`, inbox에 `whiteboard.png` | 사이드카(kind·요약·읽은 글), 모델 답의 `jira_keys`에 `KB-12` |
| `test_fallback_profile` | main 모델 id를 없는 이름으로 바꾼 설정에서 `run_task("tag", …)` | main은 http 4xx, fallback이 ok |

자료는 모두 허구다(홍길동·김철수, 회사 이름 없음).

## 요약 (2026-11-05 실행, .venv·.venv313 각 1회, 5 passed, 채팅 POST 10회)

- **추론 토글**: Nemotron-3.5-Super-VL은 `method: param`, `reasoning_effort`, 켜기 `medium` / 끄기 `none`.
  첫 후보에서 잡혔다. 근거는 생각 흔적(trace)이다: 기준선과 `medium`은 생각 있음(90토큰), `none`은 생각 없음(3토큰).
- **생각 필드**: DeepInfra는 생각을 `message.reasoning_content`로 준다. content 안 `<think>`는 없었다.
  `usage.completion_tokens_details.reasoning_tokens`도 온다(켜짐 86, 꺼짐 0).
- **기준선**: 아무 토글 없이 보내면 생각 켜짐(`baseline_reasoning: true`).
- **JSON 모드**: `response_format: json_object`가 된다(끄기 상태). T2에서 `reasoning_effort: medium` +
  `response_format`도 400 없이 content에 JSON을 줬다. D38 재시도는 일어나지 않았다.
- **비전**: 된다(빨강 PNG에 "빨강"). T6 화이트보드에서 `KB-12`·`김철수`를 읽었다(kind photo).
- **컨텍스트**: `/models`의 `metadata.context_length` = 262144(`context_source: models`).
- **탐지 POST 수**: 5회(기준선 1 + 토글 2 + JSON 1 + 비전 1), 약 6초.
- **대체 프로필**: 없는 모델 id는 **404** `model_not_found`(400 아님). run_task가 fallback(Gemma)으로 넘어가 ok.
  Gemma는 탐지 결과가 없어 `response_format` 없이 프롬프트 강제 JSON으로 보냈고, 생각 필드 없이 바로 JSON을 줬다.
- **T1**: 3줄 한 묶음 1회, 출력 28토큰(상한 2560). **T2**: 결정 1건·할 일 1건, 출력 1340토큰 중 생각 1233(두 번째 실행 972 중 863).

## 관찰 (자동 기록)

아래 구역은 테스트가 끝날 때 다시 쓴다. 비밀값은 들어가지 않는다(키가 보이면 테스트가 실패한다).

<!-- live:observed:start -->
마지막 실행: 2026-11-05 07:45 (자동 기록, tests/test_live.py)

```yaml
detect:
  model: nvidia/NVIDIA-Nemotron-3.5-Super-VL
  method: param
  param_name: reasoning_effort
  on_value: medium
  off_value: none
  evidence: 'trace: 기준선 생각 있음(reasoning_content) 90토큰 → reasoning_effort="none" 생각 없음 3토큰 → reasoning_effort="medium" 생각 있음(reasoning_content)
    90토큰'
  baseline_reasoning: true
  json_mode: true
  vision: true
  context_tokens: 262144
  context_source: models
  notes: null
  warnings: null
  probes:
  - step: context
    request: GET /models
    status: models
    verdict: /models metadata.context_length=262144
  - step: baseline
    request: 토글 없음
    status: ok
    trace: true
    trace_field: reasoning_content
    completion_tokens: 90
    finish_reason: stop
    answer: '20'
  - step: reasoning_effort:off
    request: reasoning_effort="none"
    status: ok
    trace: false
    completion_tokens: 3
    finish_reason: stop
    answer: '20'
    verdict: 뒤집힘(trace)
  - step: reasoning_effort:on
    request: reasoning_effort="medium"
    status: ok
    trace: true
    trace_field: reasoning_content
    completion_tokens: 90
    finish_reason: stop
    answer: '20'
    verdict: 확인(trace)
  - step: json_mode
    request: response_format=json_object, 끄기 상태(reasoning_effort="none")
    status: ok
    trace: false
    completion_tokens: 8
    finish_reason: stop
    answer: '{"answer": 20}'
    verdict: content가 json.loads로 JSON 객체
  - step: vision
    request: 64×64 빨강 PNG, 끄기 상태(reasoning_effort="none")
    status: ok
    trace: false
    completion_tokens: 4
    finish_reason: stop
    answer: 빨강
    verdict: 답에 빨강이 있음
  trace_fields:
  - reasoning_content
  posts: 5
  elapsed_s: 6.5
t1_tag:
  status: ok
  summary:
  - 'compile: 태그 3줄 · 결정 0건 · 할 일 0건 · 요약 0쪽 · 새 제안 0건 · LLM 호출 1회'
  ledger:
    daily/2026-11-03.md#4f300e1e:
      status: ok
      tag: 요청
      profile: main
    daily/2026-11-03.md#9e905408:
      status: ok
      tag: 학습
      profile: main
    daily/2026-11-03.md#c0ad3fa1:
      status: ok
      tag: 결정
      profile: main
  runs:
  - task: tag
    status: ok
    profile: main
    attempts:
    - profile: main
      status: ok
      tokens:
        prompt_tokens: 1316
        completion_tokens: 28
      max_tokens: 2560
      finish_reason: stop
    forced_json_profiles: []
  posts: 1
t2_decisions:
  status: ok
  summary:
  - 'compile: 태그 0줄 · 결정 1건 · 할 일 1건 · 요약 0쪽 · 새 제안 0건 · LLM 호출 1회'
  problems: []
  decision_entries: 1
  action_entries: 1
  entries_with_state_proposal: 1
  related_lines:
  - '- 관련: [[KB-12]] · [[projects/KB]] · @김철수'
  runs:
  - task: decisions
    status: ok
    profile: main
    attempts:
    - profile: main
      status: ok
      tokens:
        prompt_tokens: 1555
        completion_tokens: 972
      max_tokens: 8192
      finish_reason: stop
    forced_json_profiles: []
  posts: 1
t6_image:
  status: ok
  vision_detected: true
  processed:
  - image: inbox/whiteboard.png
    source_image: raw/assets/2026-11-05-0745-whiteboard.png
    sidecar: inbox/2026-11-05-0745-whiteboard.md
    profile: main
    kind: photo
  skipped: []
  failed: []
  model_kind: photo
  model_jira_keys:
  - KB-12
  model_people:
  - 김철수
  text_lines: 13
  sidecar_kind: photo
  sidecar_has:
    요약: true
    키: true
    읽은 글: true
  runs:
  - task: image_ingest
    status: ok
    profile: main
    attempts:
    - profile: main
      status: ok
      tokens:
        prompt_tokens: 1973
        completion_tokens: 175
      max_tokens: 2048
      finish_reason: stop
    forced_json_profiles: []
  posts: 1
fallback:
  bad_main_model: kb-live-test/no-such-model
  status: ok
  profile: fallback
  data:
    tags:
      L1: 요청
  attempts:
  - profile: main
    status: http
    error: '{"error":{"message":"The model `kb-live-test/no-such-model` does not exist","type":"invalid_request_error","param":null,"code":"model_not_found"}}'
    http_status: 404
    max_tokens: 64
  - profile: fallback
    status: ok
    tokens:
      prompt_tokens: 1289
      completion_tokens: 12
    max_tokens: 64
    finish_reason: stop
  posts: 2
total:
  chat_posts: 10
  other_requests: 6
  cap: 20
wire:
- stage: detect
  model: nvidia/NVIDIA-Nemotron-3.5-Super-VL
  max_tokens: 1024
  images: 0
  elapsed_s: 0.9
  http: 200
  finish_reason: stop
  message_fields:
  - content
  - reasoning_content
  - role
  content_chars: 2
  think_tag_in_content: false
  usage:
    prompt_tokens: 58
    completion_tokens: 90
    reasoning_tokens: 86
- stage: detect
  model: nvidia/NVIDIA-Nemotron-3.5-Super-VL
  max_tokens: 1024
  params:
    reasoning_effort: none
  images: 0
  elapsed_s: 0.6
  http: 200
  finish_reason: stop
  message_fields:
  - content
  - role
  content_chars: 2
  think_tag_in_content: false
  usage:
    prompt_tokens: 58
    completion_tokens: 3
    reasoning_tokens: 0
- stage: detect
  model: nvidia/NVIDIA-Nemotron-3.5-Super-VL
  max_tokens: 1024
  params:
    reasoning_effort: medium
  images: 0
  elapsed_s: 2.0
  http: 200
  finish_reason: stop
  message_fields:
  - content
  - reasoning_content
  - role
  content_chars: 2
  think_tag_in_content: false
  usage:
    prompt_tokens: 58
    completion_tokens: 90
    reasoning_tokens: 86
- stage: detect
  model: nvidia/NVIDIA-Nemotron-3.5-Super-VL
  max_tokens: 1024
  params:
    reasoning_effort: none
    response_format:
      type: json_object
  images: 0
  elapsed_s: 0.9
  http: 200
  finish_reason: stop
  message_fields:
  - content
  - role
  content_chars: 14
  think_tag_in_content: false
  usage:
    prompt_tokens: 70
    completion_tokens: 8
    reasoning_tokens: 0
- stage: detect
  model: nvidia/NVIDIA-Nemotron-3.5-Super-VL
  max_tokens: 1024
  params:
    reasoning_effort: none
  images: 1
  elapsed_s: 1.2
  http: 200
  finish_reason: stop
  message_fields:
  - content
  - role
  content_chars: 2
  think_tag_in_content: false
  usage:
    prompt_tokens: 304
    completion_tokens: 4
    reasoning_tokens: 0
- stage: t1_tag
  model: nvidia/NVIDIA-Nemotron-3.5-Super-VL
  max_tokens: 2560
  params:
    reasoning_effort: none
    response_format:
      type: json_object
  images: 0
  elapsed_s: 1.6
  http: 200
  finish_reason: stop
  message_fields:
  - content
  - role
  content_chars: 46
  think_tag_in_content: false
  usage:
    prompt_tokens: 1316
    completion_tokens: 28
    reasoning_tokens: 0
- stage: t2_decisions
  model: nvidia/NVIDIA-Nemotron-3.5-Super-VL
  max_tokens: 8192
  params:
    reasoning_effort: medium
    response_format:
      type: json_object
  images: 0
  elapsed_s: 5.8
  http: 200
  finish_reason: stop
  message_fields:
  - content
  - reasoning_content
  - role
  content_chars: 241
  think_tag_in_content: false
  usage:
    prompt_tokens: 1555
    completion_tokens: 972
    reasoning_tokens: 863
- stage: t6_image
  model: nvidia/NVIDIA-Nemotron-3.5-Super-VL
  max_tokens: 2048
  params:
    reasoning_effort: none
    response_format:
      type: json_object
  images: 1
  elapsed_s: 2.5
  http: 200
  finish_reason: stop
  message_fields:
  - content
  - role
  content_chars: 346
  think_tag_in_content: false
  usage:
    prompt_tokens: 1973
    completion_tokens: 175
    reasoning_tokens: 0
- stage: fallback
  model: kb-live-test/no-such-model
  max_tokens: 64
  params:
    reasoning_effort: none
    response_format:
      type: json_object
  images: 0
  elapsed_s: 0.6
  http: 404
  body: '{"error":{"message":"The model `kb-live-test/no-such-model` does not exist","type":"invalid_request_error","param":null,"code":"model_not_found"}}'
- stage: fallback
  model: google/gemma-4-26B-A4B-it
  max_tokens: 64
  forced_json_prompt: true
  images: 0
  elapsed_s: 1.1
  http: 200
  finish_reason: stop
  message_fields:
  - content
  - role
  content_chars: 22
  think_tag_in_content: false
  usage:
    prompt_tokens: 1289
    completion_tokens: 12
```
<!-- live:observed:end -->
