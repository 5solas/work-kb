# work-kb — 업무 지식 저장고 + Jira 통합 환경 구축 지시서 (v3)

> Claude Code에게: 이 문서는 하나의 완결된 작업 지시서다. 사전 확인 질문은 이미 사용자가 답했고 §11에 확정 사항으로 적혀 있다. **추가 질문 없이** §8의 단계 순서대로 구축하라. 판단이 갈리는 지점에서는 §3의 설계 원칙을 기준으로 결정하고, 결정 내용을 `docs/DECISIONS.md`에 기록하라.

v3 변경점: 집·회사 **모두 Windows**. 집에서는 DeepInfra의 실제 Nemotron 3.5 Super VL로 B단계를 검증한다(허구 데이터만). 회사 Nemotron 서버는 OpenAI 호환이지만 세부(추론 토글·비전·JSON 모드)는 **자동 탐지가 필수**. 보조 모델 Gemma 존재. 비밀값은 Windows 자격 증명 저장소. Confluence 개인 스페이스는 처음부터 사용. Emacs 접두 키는 한 줄로 교체 가능해야 함. 이미지 캡처는 클립보드·파일 둘 다.

---

## 0. 이 문서의 사용법

- **구축 장소**: 사용자의 개인 Windows PC(집). 회사 자료는 전혀 없고 가져올 수도 없다. 개발·테스트는 **허구의 샘플 데이터와 모의(mock) 서버**로 하되, B단계(LLM)만은 DeepInfra의 실제 Nemotron으로도 검증한다. DeepInfra로 가는 입력은 허구 샘플뿐이어야 한다.
- **배포 장소**: 회사 Windows PC(사내망). 완성된 저장소 폴더를 통째로 복사해 간다. 복사 후 `python scripts/kb.py init`을 한 번 실행하면 연결 설정이 끝나야 한다.
- **사용자**: 키보드 중심 작업을 선호하고 Emacs와 Obsidian 모두 익숙하다. 스스로 "분류와 노트 작성에 약하다"고 말한다. 이 시스템은 그 약점을 **기계가 대신 메우는** 것이 목적이다.

---

## 1. 배경과 목표

사용자는 회사에서 Jira Data Center(일반 사용자 권한), Confluence, Outlook Classic을 쓴다. 업무 메모·지식·일감을 한 곳에서 다루고 싶지만, 폴더나 태그를 고르는 일 자체가 부담이라 기존 노트 체계가 유지되지 않았다.

목표는 다음 다섯 가지가 한 폴더에서 돌아가는 것이다.

1. **캡처**: 키 하나로 오늘 날짜 노트에 한 줄을 던진다. 분류는 하지 않는다. 글 대신 **스크린샷·사진을 던져도** 된다.
2. **자동 연결**: 스크립트가 노트 속 Jira 키와 사람 이름을 찾아 엔티티 페이지에 엮는다. LLM 없이 돈다.
3. **위키 편찬**: Nemotron이 파편과 이미지를 읽고 프로젝트·사람·주제 페이지, 결정 로그, 할 일을 갱신한다. LLM이 없으면 이 단계만 조용히 빠진다.
4. **Jira 통합**: 내 일감을 로컬 마크다운 캐시로 받아 검색·열람하고, 댓글·상태전환·생성 같은 작은 쓰기를 Emacs에서 보내며, 일감 파일을 열면 그 일감에 대해 적은 메모가 전부 모여 보인다.
5. **이식성**: 집에서 만든 묶음을 회사에 복사하고 마법사 한 번으로 연결한다.

Obsidian은 **열람 창**, Emacs는 **입력·명령 창**, 저장소는 **평범한 마크다운 폴더 하나**다.

---

## 2. 환경 제약 (절대 조건)

| 항목 | 집(구축) | 회사(배포) |
|---|---|---|
| OS | **Windows** | **Windows** |
| Python | 최신 안정(3.13+). `py` 런처 또는 `python` | 동일. pip 가능 가정, 불가 시 `wheels/` |
| Emacs / Obsidian | 최신 (Emacs 30+, Obsidian Bases) | 동일 |
| LLM main | DeepInfra `https://api.deepinfra.com/v1/openai`, 모델 `nvidia/NVIDIA-Nemotron-3.5-Super-VL`, 키는 환경변수 `DEEPINFRA_API_KEY` (262K 컨텍스트, JSON·함수호출·멀티모달) | 사내 Nemotron 3.5 Super VL, OpenAI 호환. 주소·키·추론 토글 방식은 **init이 자동 탐지** |
| LLM fallback | DeepInfra의 Gemma 계열 모델(`/models`에서 선택) | 사내 Gemma(gemma4-26b 급, thinking 없음) |
| Jira / Confluence | `tests/mock_*.py` | Jira DC REST v2 PAT, Confluence DC REST PAT, 개인 스페이스 **처음부터 사용** |
| 비밀값 | Windows 자격 증명 저장소(`keyring`), `DEEPINFRA_API_KEY`만 환경변수 허용 | Windows 자격 증명 저장소(`keyring`) |
| 연결 정보 | 코드·`config.yaml`에 넣지 않는다 | `kb.py init` → `config.local.yaml` + 자격 증명 저장소 |

**따라서**
- 외부 SaaS, 벡터 DB, 무거운 프레임워크에 의존하지 않는다. 클라우드 LLM API는 **집에서 B단계 테스트에만**, 같은 OpenAI 호환 클라이언트로 쓴다. DeepInfra 전용 코드는 없어야 한다.
- Python 의존성: `requests`, `pyyaml`, `markdown`, `keyring`, `pillow`(이미지 축소). 그 외는 표준 라이브러리.
- **Windows 우선**: 모든 파일 입출력 `encoding="utf-8"`; 진입점에서 `sys.stdout/stderr.reconfigure(encoding="utf-8")`과 `PYTHONUTF8=1` 안내; 경로는 `pathlib`; 한글 경로·파일명 테스트; `schtasks` 스케줄; PowerShell로 클립보드 이미지; 모의 서버는 `127.0.0.1` 바인딩. pytest는 Windows에서 통과해야 한다.
- `config.local.yaml`에 `environment: home | company`를 둔다. `company`일 때 LLM 프로필 주소가 공개 클라우드 호스트(deepinfra.com, openai.com, together.xyz, openrouter.ai, anthropic.com, googleapis.com 등 목록)이면 `doctor`가 ✗로 막고 `compile`·`ingest`가 실행을 거부한다. 회사 자료가 밖으로 나가는 사고를 코드로 막는 장치다.

---

## 3. 설계 원칙 (판단 기준)

1. **사람은 분류하지 않는다.** 사람이 만드는 구조는 날짜뿐이다. 캡처 시점에 폴더·태그·제목을 요구하는 설계는 전부 기각.
2. **Jira가 진실의 원천이다.** 로컬 `jira/`는 읽기 캐시이고, 쓰기는 작은 추가 동작(댓글·전환·로그·생성)만 큐를 거쳐 보낸다.
3. **LLM 없이도 완결된다.** A단계·동기화·캡처·검색은 LLM 없이 가치가 있어야 하고, B단계가 빠져도 아무것도 깨지지 않는다.
4. **모델 능력에 맞춰 단위를 조절한다.** Nemotron은 긴 입력과 추론 모드를 쓸 수 있으므로 하루치·주치 단위로 묶어 보내되, 출력은 항상 **스키마가 정해진 JSON**. 추론 모드는 작업별로 켜고 끈다.
5. **서버 차이는 탐지로 흡수한다.** 추론 토글·비전·JSON 모드는 가정하지 말고 `init`이 실제 호출로 판정해 `config.local.yaml`에 기록한다.
6. **도구는 자기 구역만 고친다.** 관리 구역(§5.0)만 기계가 다시 쓰고, 그 밖은 사람 영역이다.
7. **되돌릴 수 있다.** 삭제 대신 `_trash/`. 쓰기 동작은 `outbox.jsonl`에 기록. 자동 상태전환 금지.
8. **형식은 평범한 마크다운.** 위키링크 `[[...]]`와 YAML front matter만.
9. **설정은 세 겹.** 구조(`config.yaml`) → 환경(`config.local.yaml`) → 비밀(자격 증명 저장소). 회사에서 바뀌는 값은 두 번째·세 번째 겹에만.
10. **한국어.** 사용자 대면 문구·문서·프롬프트 출력은 한국어. 코드 식별자는 영어.

---

## 4. 산출물: 저장소 레이아웃

```
work-kb/
├── AGENTS.md                 에이전트 규칙 (유일한 규칙 파일, §5.6)
├── CLAUDE.md                 한 줄: "AGENTS.md를 읽어라"
├── config.yaml               구조 설정 (커밋). 연결 값 없음
├── config.local.yaml         환경 설정 (git 제외). kb.py init이 생성
├── config.local.example.yaml 집·회사 두 예시 포함
├── requirements.txt
├── wheels/                   오프라인 설치용 (선택)
├── daily/                    YYYY-MM-DD.md  ← 사람이 씀
├── inbox/                    제목 있는 긴 글, 클립, 드롭한 이미지 ← 사람이 던짐
├── raw/                      불변 원문 보관
│   └── assets/               이미지·첨부 (Obsidian 첨부 폴더)
├── jira/                     ← 기계 소유 캐시
│   ├── KB-12.md
│   ├── _views/               저장 JQL별 결과표 + Obsidian Bases 파일
│   ├── outbox.jsonl
│   └── .cache.sqlite
├── wiki/                     ← 기계 소유 (관리 구역 밖은 사람이 써도 됨)
│   ├── index.md
│   ├── projects/KB.md
│   ├── people/김철수.md
│   ├── topics/
│   ├── decisions.md
│   ├── actions.md
│   ├── _proposals.md
│   └── _lint.md
├── _trash/
├── .state/                   원장, 동기화 시각, 매핑. git 제외
├── prompts/                  tag, decisions, entity_summary, topic_proposal, image_ingest, contradictions
├── scripts/
│   ├── kb.py                 단일 진입점
│   ├── init.py               연결 설정 마법사 (§6.11)
│   ├── doctor.py             연결·환경 점검 (§6.11)
│   ├── link.py               A단계
│   ├── jira_sync.py
│   ├── jira_act.py
│   ├── daily_prepare.py
│   ├── compile.py            B단계
│   ├── ingest_images.py      B단계 VL
│   ├── lint.py               C단계
│   ├── confluence_push.py
│   ├── clip_image.ps1        클립보드 이미지 → PNG 저장 (Emacs가 호출)
│   └── lib/                  config, secrets, frontmatter, managed, jira_client, llm_client, llm_detect, wikimarkup
├── emacs/
│   ├── work-kb.el
│   └── capture.ahk           전역 단축키 (AutoHotkey, 선택)
├── obsidian/setup.md
├── tests/
│   ├── mock_jira.py
│   ├── mock_confluence.py
│   ├── mock_llm.py
│   ├── fixtures/             허구 데이터만 (이미지 3장 포함)
│   └── test_*.py
├── examples/sample-vault/
└── docs/                     README, DEPLOY, SCHEDULING, DECISIONS, TROUBLESHOOTING
```

`.gitignore`: `config.local.yaml`, `.state/`, `jira/.cache.sqlite`, `.env`, `_trash/`, `wheels/`(선택).

---

## 5. 데이터 포맷 스펙

### 5.0 관리 구역 규약 (모든 파일 공통)

기계가 다시 쓰는 영역은 HTML 주석 마커로 감싼다. 각 도구는 **자기 이름의 구역만** 통째로 다시 쓰고, 마커 밖은 절대 건드리지 않는다. 구역이 없으면 파일 끝에 새로 만든다.

```markdown
<!-- kb:mentions:start -->
...
<!-- kb:mentions:end -->
```

| 구역 | 소유 | 쓰이는 파일 |
|---|---|---|
| `today` | daily_prepare | daily |
| `carryover` | daily_prepare | daily |
| `mentions` | link | jira/*, wiki/projects/*, people/*, topics/* |
| `issues` | jira_sync | wiki/projects/* |
| `summary` | compile | wiki/projects/*, people/*, topics/* |
| `decisions` | compile | wiki/decisions.md (append-only) |
| `actions` | compile | wiki/actions.md |
| `index` | link | wiki/index.md |
| `extracted` | ingest_images | 이미지 사이드카 .md |

`scripts/lib/managed.py`가 담당하고, 단위 테스트로 "다른 구역과 마커 밖 텍스트가 바이트 단위로 보존됨"을 보장한다.

### 5.1 daily/YYYY-MM-DD.md

```markdown
---
date: 2026-11-05
---
## 오늘
<!-- kb:today:start -->
- [ ] [[KB-12]] 캐시 front matter 확정 · 진행중 · 마감 11-06
- [ ] [[OPS-3]] 배포 체크리스트 · 막힘
<!-- kb:today:end -->

## 이월
<!-- kb:carryover:start -->
- [ ] 개인: 경비 정산 (← 2026-11-04)
<!-- kb:carryover:end -->

## 기록
- 09:12 [[KB-12]] @김철수 와 front matter 논의. updated는 UTC로 저장하기로.
- 11:40 개인: PR 리뷰 요청 옴
- [ ] 개인: 11월 회의실 예약
- 14:20 ![[raw/assets/2026-11-05-1420-whiteboard.png]] 회의 화이트보드
- 16:05 KB-12 댓글 올림. 설명의 {code} 블록은 best-effort 변환으로 합의
```

규칙
- 캡처는 항상 `## 기록` 끝에 `- HH:MM ` 접두로 추가. 사람이 그 밖을 자유롭게 편집해도 된다.
- Jira 키는 `[[KEY-123]]` 또는 맨 글자 `KEY-123`. link.py가 맨 글자를 위키링크로 **정규화**(daily를 기계가 고치는 유일한 예외, 멱등).
- 사람은 `@이름` 접두로만 인식. 한국어 이름을 추정하지 않는다.
- 이미지는 `![[raw/assets/...]]`. `ingest_images`가 사이드카(`같은이름.md`)에 추출 텍스트를 쓰고 link.py가 사이드카도 훑는다. daily 본문은 바뀌지 않는다.
- `## 오늘` 체크박스 `[x]`는 Jira 상태전환 **제안 목록**에 오를 뿐 자동 전환은 없다.
- `## 기록`의 `- [ ]` 중 Jira 키 없는 것은 개인 할 일 → 다음 날 `## 이월`에 복사, 원래 줄은 `- [>] (2026-11-06로 이월)`.

### 5.2 inbox/

`inbox/YYYYMMDD-HHMM-제목.md`(front matter: created, title)와 **드롭한 이미지 파일**(`inbox/*.png|jpg|jpeg|webp`, Obsidian·탐색기에서 끌어다 놓은 것). 이미지는 `ingest_images`가 `raw/assets/YYYY-MM-DD-HHMM-<원래이름>.<ext>`로 옮기고 사이드카 `.md`를 inbox에 남긴다. inbox는 정리하지 않는다.

### 5.3 jira/KEY-123.md (jira_sync 소유)

```markdown
---
key: KB-12
summary: 캐시 front matter 확정
status: In Progress
status_category: indeterminate
issuetype: Task
priority: Medium
assignee: 홍길동
reporter: 김철수
project: KB
sprint: KB 스프린트 7
labels: [cache, schema]
components: []
created: 2026-10-28T09:00:00+09:00
updated: 2026-11-04T16:05:11+09:00
due: 2026-11-06
url: https://<jira>/browse/KB-12
synced_at: 2026-11-04T16:20:03+09:00
---
# KB-12 캐시 front matter 확정

> 동기화: 2026-11-04 16:20. 이 파일은 자동 생성되며 손으로 고치면 덮입니다.

## 설명
(Jira wiki markup → 마크다운 best-effort. 실패 시 원문을 ``` 블록에)

## 댓글
- 2026-11-01 10:02 김철수: 필드는 최소로 가자

## 노트에서 언급
<!-- kb:mentions:start -->
- [[2026-11-04]] 09:12 @김철수 와 front matter 논의…
<!-- kb:mentions:end -->
```

### 5.4 wiki 엔티티 페이지

`wiki/projects/KB.md`

```markdown
---
type: project
key: KB
title: 지식 저장고
updated: 2026-11-05
---
# KB — 지식 저장고

## 개요
(사람 또는 사람이 승인한 LLM 초안. 기계가 건드리지 않음)

## 현재 일감
<!-- kb:issues:start -->
| 키 | 제목 | 상태 | 담당 | 마감 | 갱신 |
|---|---|---|---|---|---|
<!-- kb:issues:end -->

## 최근 요약
<!-- kb:summary:start -->
<!-- kb:summary:end -->

## 언급
<!-- kb:mentions:start -->
<!-- kb:mentions:end -->
```

`people/*.md`는 `type: person`, `topics/*.md`는 `type: topic`. 사람 페이지에는 업무 맥락만, 개인 정보는 쓰지 않는다(AGENTS.md 명시).

### 5.5 wiki/decisions.md, wiki/actions.md

decisions.md는 **append-only**:

```markdown
## 2026-11-04 캐시 updated 시각은 UTC로 저장
- 상태: 제안   <!-- LLM이 쓴 것은 '제안'. 사람이 '확정'/'기각'으로 바꾼다 -->
- 관련: [[KB-12]] · [[projects/KB]] · @김철수
- 내용: …
- 근거: …
- 출처: [[2026-11-04]] 09:12
```

actions.md는 `- [ ] 내용 · 관련 [[KB-12]] · 출처 [[2026-11-04]] 11:40` 꼴. 중복 방지는 (출처 파일, 줄 해시) 원장.

### 5.6 AGENTS.md (에이전트 규칙)

- 역할: "이 저장소는 사람의 업무 메모와 Jira 캐시를 바탕으로 위키를 유지하는 곳이다. 너는 편집자다."
- 폴더 소유권 표(§4)와 관리 구역 규약(§5.0).
- **금지**: daily/inbox/raw 수정, jira/ 수동 수정, 관리 구역 밖 수정, 파일 삭제(→ `_trash/`), Jira 쓰기(에이전트는 읽기 전용), 출처 없는 문장, 개인 정보 기록.
- 고정 태그 10개: 회의, 결정, 요청, 이슈, 학습, 아이디어, 개인, 참고, 일정, 리뷰.
- B단계 작업의 입력·출력 JSON 스키마(§6.5).
- 답변 언어: 한국어. 문장은 짧게. 모르면 모른다고 쓴다.
- 자주 쓰는 명령: `python scripts/kb.py status|sync|link|compile|ingest|lint|today|search "..."`.

### 5.7 설정 세 겹

**`config.yaml` (구조, 커밋)**

```yaml
kb_root: .
timezone: Asia/Seoul
people_prefix: "@"
normalize_keys: true

jira:
  api_version: 2
  auth: pat
  jql_mine: "(assignee = currentUser() OR reporter = currentUser() OR watcher = currentUser()) AND updated >= -30d"
  views:
    today: "assignee = currentUser() AND statusCategory != Done AND (duedate <= endOfDay() OR sprint in openSprints())"
    blocked: "assignee = currentUser() AND status = Blocked"
    review: "reporter = currentUser() AND status = 'In Review'"
  poll_minutes: 15
  max_results: 500

confluence:
  title_prefix: "[KB] "
  push_globs: ["wiki/projects/*.md", "wiki/topics/*.md", "wiki/decisions.md"]

llm:
  tasks:
    tag:            {profile: main, reasoning: off, max_input_tokens: 2000}
    decisions:      {profile: main, reasoning: on,  max_input_tokens: 30000}
    entity_summary: {profile: main, reasoning: off, max_input_tokens: 20000}
    topic_proposal: {profile: main, reasoning: on,  max_input_tokens: 60000}
    image_ingest:   {profile: main, reasoning: off, max_input_tokens: 8000}
    contradictions: {profile: main, reasoning: on,  max_input_tokens: 60000}
  temperature: 0
  timeout_sec: 180
  public_hosts_blocklist: [deepinfra.com, openai.com, together.xyz, openrouter.ai, anthropic.com, googleapis.com, groq.com, fireworks.ai, mistral.ai, baseten.co, nebius.com]
```

**`config.local.yaml` (환경, git 제외, `kb.py init`이 생성)** — 집 예시:

```yaml
environment: home
jira:
  base_url: "http://127.0.0.1:8081"        # mock_jira
  project_keys: [KB, OPS]
  pat_ref: "keyring:work-kb/jira"           # mock은 아무 값이나 받음
confluence:
  enabled: true
  base_url: "http://127.0.0.1:8082"        # mock_confluence
  space_key: "~dev"
  parent_page_id: ""
  pat_ref: "keyring:work-kb/confluence"
llm:
  enabled: true
  profiles:
    main:
      base_url: "https://api.deepinfra.com/v1/openai"
      model: "nvidia/NVIDIA-Nemotron-3.5-Super-VL"
      api_key_ref: "env:DEEPINFRA_API_KEY"
      detected:                            # init/doctor가 채움. 손으로 고쳐도 됨
        vision: true
        json_mode: true
        reasoning_toggle: {method: "param", param_name: "reasoning_effort", on_value: "medium", off_value: "none"}
        context_tokens: 262144
    fallback:
      base_url: "https://api.deepinfra.com/v1/openai"
      model: "<DeepInfra /models에서 고른 Gemma 계열 id>"
      api_key_ref: "env:DEEPINFRA_API_KEY"
      detected: {vision: false, json_mode: true, reasoning_toggle: {method: "none"}}
    mock:
      base_url: "http://127.0.0.1:8083/v1"
      model: "mock"
  fallback_order: [main, fallback]
```

회사 예시는 같은 꼴에 `environment: company`, 사내 주소, `pat_ref: keyring:…`, `api_key_ref: keyring:work-kb/llm`(키가 있으면). `config.local.example.yaml`에 둘 다 싣는다.

**비밀(세 번째 겹)**: `keyring:<service>/<username>`은 Windows 자격 증명 저장소, `env:NAME`은 환경변수. `lib/secrets.py`가 해석. 둘 다 없으면 `doctor`가 무엇이 빠졌는지 알려준다.

병합 순서: 코드 기본값 → `config.yaml` → `config.local.yaml` → 환경변수 `KB_<SECTION>_<KEY>`.

### 5.8 jira/outbox.jsonl

```json
{"id":"01JC…","ts":"2026-11-05T16:05:00+09:00","action":"comment","key":"KB-12","payload":{"body":"…"},"source":"daily/2026-11-05.md:14","status":"pending","attempts":0,"last_error":null,"done_at":null}
```

### 5.9 .state/

`links.json`, `sync.json`, `compile.json`, `images.json`, `confluence.json`.

---

## 6. 컴포넌트 스펙

### 6.1 scripts/link.py — A단계 (LLM 불필요)

입력 `daily/`, `inbox/`(사이드카 포함). `project_keys`의 `KEY-\d+`와 `@이름`을 찾아 `jira/KEY-123.md`(없으면 최소 stub), `wiki/projects/KEY.md`, `wiki/people/이름.md`의 `mentions` 구역에 `- [[날짜]] HH:MM 원문(120자)` 추가. (파일, 줄 해시) 원장으로 멱등. `normalize_keys`면 맨 글자 키를 `[[...]]`로(코드 블록·URL 제외). `wiki/index.md`의 `index` 구역 갱신. daily 1,000개 2초 이내. `--dry-run`.

### 6.2 scripts/jira_sync.py

Bearer PAT. 전체(`jql_mine` 페이징)·증분(`updated >= last_sync-5분`). 각 이슈 → §5.3, 임시 파일 후 rename, `mentions` 보존. `views` → `jira/_views/*.md` 표. 프로젝트 페이지 `issues` 구역. `.cache.sqlite` + FTS5(불가 시 LIKE). `lib/wikimarkup.py` 최소 변환, 실패 시 원문. 옵션 `--full`, `--key`, `--jql`(`--cache`), `--dry-run`. 종료 코드 0/2(인증)/3(네트워크)/4(설정).

### 6.3 scripts/jira_act.py — 쓰기 (큐 경유)

`comment`, `transition`, `assign`, `worklog`, `create`, `promote --from daily/…:14 PROJECT`, `flush`, `list`. outbox `pending` → 즉시 전송 → 2xx면 `done` + `jira_sync --key` → 네트워크 오류면 유지 → `flush` 재시도(5회 후 `failed`). `transition`은 이름→id, 후보 여럿이면 목록 출력 후 중단. `KB_AGENT=1`이면 쓰기 전부 거부. `create`·`transition`은 `--yes` 없이는 확인.

### 6.4 scripts/daily_prepare.py

오늘 파일 생성, `today` 구역(`_views/today`, `_views/blocked`), `carryover` 구역, `--check-done`(완료 전환 제안 출력).

### 6.5 scripts/compile.py, scripts/ingest_images.py — B단계 (LLM)

**공통(`lib/llm_client.py`)**
- `llm.enabled` 거짓이거나 어떤 프로필도 연결되지 않으면 로그 한 줄, 종료 0. `environment: company`에서 프로필 주소가 `public_hosts_blocklist`에 걸리면 **실행 거부**(종료 5).
- OpenAI 호환 `/chat/completions`. 작업의 `reasoning`을 프로필의 `detected.reasoning_toggle.method`에 따라 적용(§6.11의 탐지 결과). `detected.json_mode`면 `response_format: {"type":"json_object"}`, 아니면 프롬프트로 JSON 강제. 응답의 `reasoning_content` 필드 또는 `<think>…</think>` 블록은 버리고 `content`만 파싱. 스키마 검증 실패 1회 재시도, 그래도 실패면 `skipped`. `fallback_order`로 프로필 전환(비전 작업은 `detected.vision` 참인 프로필만).
- 입력이 `max_input_tokens`를 넘으면 날짜 단위로 분할. 토큰 추정 `len(text)/2.5`.
- 프롬프트 `prompts/*.md`, 시스템 메시지에 AGENTS.md 관련 절. `--dry-run` diff. 원장 `.state/compile.json`.
- 절대 하지 않는 것: daily 수정, 페이지 삭제, 출처 없는 문장, 사람 페이지에 업무 외 정보.

**작업**

| 작업 | 추론 | 입력 | 출력 | 반영 |
|---|---|---|---|---|
| T1 tag | off | 기록 한 줄 + 태그 목록 | `{"tag": "결정"}` | 원장, index |
| T2 decisions | on | 하루치 `## 기록`(사이드카 포함) + 관련 jira 캐시 요약 | `{"decisions":[{title,content,rationale,related,source_line}],"actions":[{text,related,source_line}]}` | decisions.md '제안', actions.md |
| T3 entity summary | off | 현재 `summary` + 새 멘션 + (프로젝트) 미완료 일감 표 | `{"summary":"...", "sources":[...]}` 3~5문장 | `summary` 구역 |
| T4 topic proposal | on, 주 1회 | 최근 2주 기록 전문 + 기존 topics | `{"proposals":[{name, why, sample_sources, merge_with?}]}` | `_proposals.md`; 승인 `kb.py proposals accept <n|all>` |
| T5 contradictions | on, lint `--llm` | 엔티티 summary + 관련 decisions | `{"conflicts":[{a, b, why}]}` | `_lint.md` |
| T6 image ingest | off | 이미지(base64 `image_url`) + 힌트(파일명, 같은 줄 텍스트) | `{"kind":"screenshot\|photo\|document","text":"...","summary":"...","jira_keys":[...],"people":[...]}` | 사이드카 `extracted` 구역 |

T6(`ingest_images.py`): 대상은 `inbox/*` 이미지와 daily에 `![[...]]`로 끼운 `raw/assets/*`. 해시로 중복 제외, `pillow`로 긴 변 1600px 축소 후 전송, 원본은 `raw/assets/` 보존. 사이드카 front matter: `source_image`, `ingested_at`, `model`. 비전 프로필 없으면 `status`에 "미처리 이미지 N개".

### 6.6 scripts/lint.py — C단계

`wiki/_lint.md`: 고아 페이지, 30일 멘션 없는 stub, 유사 제목 쌍, 깨진 위키링크, 오래된 캐시, `pending`/`failed` outbox, 14일 넘은 '제안', 미처리 `_proposals`, 미처리 이미지. `--llm`이면 T5(기본 꺼짐).

### 6.7 scripts/confluence_push.py

`push_globs`만(daily/inbox/jira/raw는 코드에서 차단). front matter·마커 제거 → `markdown`(tables, fenced_code) → storage format. `[[KB-12]]`→Jira 링크, 같이 밀리는 페이지→Confluence 링크, 나머지→텍스트. 제목 `title_prefix + (title|H1)` 조회 → 갱신(버전+1)/생성(부모 지정). `.state/confluence.json`. `--dry-run`, `--only`. Confluence는 **기본 활성**이므로 Phase 5부터 데모 흐름에 포함.

### 6.8 emacs/work-kb.el

Emacs 30+ Windows. 외부 패키지 필수 아님(`transient` 내장, `markdown-mode`·`consult` 권장). 연결 정보는 Emacs가 전혀 모른다.

**접두 키 교체(필수 요건)**: 모든 명령은 `work-kb-command-map`(prefix keymap)에 담는다. `defcustom work-kb-prefix-key "C-c k"`와 함수 `work-kb-rebind-prefix`로 바꿀 수 있고, 사용자가 `(setq work-kb-prefix-key "C-c c")` 한 줄만 바꾸고 재로드하면 끝나야 한다. `(keymap-set global-map "<f9>" work-kb-command-map)`처럼 직접 붙이는 것도 되게 `docs/README.md`에 두 방법을 적는다.

| 키 | 함수 | 동작 |
|---|---|---|
| `c` | `work-kb-capture` | 캡처 버퍼. `C-c C-c`로 오늘 `## 기록` 끝에 `- HH:MM 내용`. 오늘 파일 없으면 `daily_prepare` |
| `C` | `work-kb-capture-clipboard-image` | `scripts/clip_image.ps1`(PowerShell `Get-Clipboard -Format Image` → `raw/assets/YYYY-MM-DD-HHMM-<입력>.png`) 호출 후 `![[...]]` 줄 캡처. 클립보드에 이미지 없으면 안내 |
| `F` | `work-kb-capture-image-file` | 파일 선택(`read-file-name`, dired에서 호출 시 현재 파일 기본값) → `raw/assets/`로 복사 → `![[...]]` 줄 캡처 |
| `i` | `work-kb-capture-inbox` | 제목 → `inbox/` 파일 |
| `t` | `work-kb-today` | 오늘 daily |
| `s` | `work-kb-search` | `consult-ripgrep` 또는 `rgrep` |
| `o` | `work-kb-open-at-point` | `KEY-123`/`[[...]]`/`@이름` 열기. 캐시 없으면 `jira_sync --key` |
| `j` | `work-kb-jira` | transient: `v` 보기 · `c` 댓글(기본 현재 줄) · `t` 전환(후보 선택) · `w` 워크로그 · `n` 새 일감 · `p` 현재 줄 승격 · `q` 라이브 JQL · `S` 동기화 · `f` flush · `d` 완료 전환 제안 |
| `l` | `work-kb-link` | link 실행 후 요약 |
| `g` | `work-kb-ingest` | ingest_images + compile 실행 |
| `a` | `work-kb-proposals` | `_proposals.md` 열고 승인 명령 |
| `?` | `work-kb-status` | `kb.py status` 버퍼 |

외부 실행은 `make-process` 비동기, 결과 `*work-kb*` 버퍼, `process-coding-system` utf-8, Windows에서 `python` 경로는 `work-kb-python`(기본 `"py"`, 인수 `-3`). `markdown-enable-wiki-links t`. `capture.ahk`: Win+Shift+C 같은 전역 키로 `emacsclient -n -e "(work-kb-capture)"`, Win+Shift+V로 클립보드 이미지 캡처 호출 예시(서버 모드 필요, README에 설정).

### 6.9 Obsidian

`.obsidian/` 커밋 안 함. `obsidian/setup.md`: 볼트 루트, 위키링크, Daily notes 폴더 `daily`·형식 `YYYY-MM-DD`, 첨부 폴더 `raw/assets`, 드롭한 이미지는 `inbox/`에 두면 ingest가 처리한다는 안내. **Bases** 기본: `jira/_views/일감.base`(status·assignee·due·updated, 미완료 필터, 프로젝트별), `wiki/엔티티.base`. 문법은 현재 Obsidian 공식 문서로 확인하고 확인 버전을 `DECISIONS.md`에 기록. Dataview는 한 블록 예시만.

### 6.10 에이전트 연동

`AGENTS.md` 하나. `CLAUDE.md`는 "AGENTS.md를 읽어라". 에이전트는 `kb.py` 읽기 계열만, 세션에 `KB_AGENT=1`. `kb.py status`: 마지막 동기화, pending outbox, 미처리 제안·이미지, 프로필별 연결·탐지 결과, environment.

### 6.11 scripts/init.py, scripts/doctor.py, scripts/lib/llm_detect.py

**`kb.py init`** (대화형; 재실행 시 기존 값이 기본값)
1. `environment` 선택(home/company). company면 공개 호스트 금지 안내.
2. Jira: base_url → PAT(가려서 입력) → 자격 증명 저장소에 저장(`keyring`, 서비스 `work-kb`, 사용자 `jira`) → `/rest/api/2/myself` 확인 → `/rest/api/2/project` 목록에서 **프로젝트 키 선택**(복수, 번호 입력) → `project_keys` 기록. (프로젝트 키는 회사에서 이 단계에서 정한다.)
3. Confluence(기본 사용): base_url, PAT → 개인 스페이스 자동 탐지(`/rest/api/space?type=personal`, 없으면 `/rest/api/user/current`의 키로 `~username`) → 부모 페이지(선택).
4. LLM main: base_url, api_key 여부(있으면 자격 증명 저장소) → `/models` 있으면 목록에서 선택, 없으면 모델명 입력 → **자동 탐지 실행**(아래) → `detected` 기록. fallback 프로필 추가 여부(같은 절차).
5. 스케줄 등록 여부 → `schtasks /Create` 명령 생성(실행은 확인 후).
6. `config.local.yaml` 저장 → `doctor` 실행.
옵션: `--dev`(집: 모의 Jira/Confluence 주소, main=DeepInfra `env:DEEPINFRA_API_KEY`, mock 프로필 추가), `--non-interactive --from-env`, `--section jira|confluence|llm`, `--redetect`(LLM 탐지만 다시).

**자동 탐지(`lib/llm_detect.py`)** — 각 항목을 실제 호출로 판정, 결과와 근거를 `detected`에 기록, `doctor`로 재검증 가능.
- `context_tokens`: `/models`의 메타데이터 → 없으면 프로필 기본 262144를 기록하되 `source: assumed` 표시.
- `json_mode`: `response_format: json_object`로 작은 요청 → 400/파라미터 오류면 false.
- `vision`: 1×1 PNG(base64 `image_url`)를 넣은 요청 → 오류면 false.
- `reasoning_toggle`: 같은 짧은 문제를 다음 후보로 "켜기/끄기" 두 번씩 보내, (a) 응답에 `reasoning_content` 또는 `<think>` 존재 여부, (b) `usage.completion_tokens` 차이(켜기가 2배 이상)로 켜고 끄기가 **둘 다 구분되는 첫 후보**를 채택.
  1. `reasoning_effort` 파라미터 (`"medium"` / `"none"`)
  2. `chat_template_kwargs: {"enable_thinking": true|false}` (vLLM/SGLang 계열)
  3. 시스템 프롬프트 접두 `/think` / `/no_think`
  4. `reasoning: {"enabled": true|false}` 또는 `max_reasoning_tokens` (게이트웨이 계열)
  5. 전부 실패 → `method: none`(항상 기본 상태), 사용자에게 알리고 `reasoning_budget_tokens` 수동 설정 안내.
- 탐지에 쓰는 프롬프트는 짧은 산수 문제 하나로 고정하고 허구 텍스트만 쓴다. 전체 탐지는 10회 이하 호출로 끝낸다.

**`kb.py doctor`**: Python·의존성·`PYTHONUTF8`, `config.local.yaml`, 비밀값 해석, Jira/Confluence/LLM 실제 호출, 프로필별 탐지 결과 재검증(`--redetect`로 갱신), company 모드 공개 호스트 ✗, Emacs·Obsidian 경로 안내, 쓰기 권한, 한글 경로, PowerShell 실행 정책(클립보드 스크립트용). 한 줄에 하나씩 ✓/✗와 해결 방법. 종료 코드 반영.

---

## 7. 테스트와 모의 환경

- `tests/mock_jira.py`(:8081), `tests/mock_confluence.py`(:8082), `tests/mock_llm.py`(:8083): 표준 라이브러리 `http.server`, `127.0.0.1`. mock_llm은 `/models`, `/chat/completions`를 지원하고 모드로 깨진 JSON 1회, `<think>` 섞기, `reasoning_content` 필드, `reasoning_effort` 수용/거부, `chat_template_kwargs` 수용/거부, 비전 거부를 흉내 내 **탐지 로직 전부를 테스트**한다.
- `tests/fixtures/`: 프로젝트 `KB`, `OPS`, 이슈 15개, 한국어 본문, wiki markup, 이미지 3장(허구로 그린 화이트보드·메일 캡처·문서 캡처). 실제 회사명·인명·URL 금지.
- pytest(Windows에서 통과): 관리 구역 보존 / link 멱등·정규화 / sync 생성·mentions 보존·증분 / outbox 다운→복구→done / daily_prepare / compile: enabled=false, 깨진 JSON, think 제거, fallback, daily 불변, company 모드 공개 호스트 거부 / ingest_images / lint / confluence / init `--from-env`·doctor / llm_detect 각 후보 / utf-8·CRLF·한글 파일명·한글 경로.
- **실 모델 검증(선택, 집에서만)**: `pytest -m live`는 `DEEPINFRA_API_KEY`가 있을 때만 돌며, 허구 샘플로 T1·T2·T6와 탐지를 실제 Nemotron에 1회씩 보낸다. 결과를 `docs/DECISIONS.md`에 요약(어떤 토글 방식이 잡혔는지 포함).
- `examples/sample-vault/`와 `python scripts/kb.py demo`(모의 서버 3개 기동 → init --dev → sync → link → ingest → compile → lint → confluence_push → 결과 트리).

---

## 8. 작업 순서와 완료 기준

**Phase 0 — 골격과 설정**
레이아웃, 설정 세 겹(`lib/config.py`, `lib/secrets.py` with keyring), `AGENTS.md` 초안, `lib/managed.py`·`lib/frontmatter.py`, `init.py --dev`, `doctor.py` 뼈대, mock 서버 3개 뼈대.
완료: 관리 구역·설정 병합·비밀 해석 테스트 통과. `init --dev` 후 `doctor`에서 mock 3개 ✓, DeepInfra main ✓(키 있을 때).

**Phase 1 — 캡처와 연결 (LLM 없음)**
`daily_prepare.py`, `link.py`, `kb.py`, `clip_image.ps1`, `work-kb.el`의 capture/clipboard/file/today/search/open/link + 접두 키 교체.
완료: 샘플 볼트 `link` 두 번째 실행 diff 0. Emacs에서 텍스트·클립보드 이미지·파일 이미지 캡처 각 1회, 접두 키 교체 1회 사용자 확인.

**Phase 2 — Jira 읽기**
`jira_sync.py`, 뷰, Bases 파일, 프로젝트 표, sqlite, wikimarkup.
완료: 전체·증분, mentions 보존, `--jql`, Obsidian Bases 확인.

**Phase 3 — Jira 쓰기**
`jira_act.py`, outbox, transient, `--check-done`, `KB_AGENT`.
완료: 다운→pending→복구→flush→done→캐시 갱신.

**Phase 4 — 편찬 (Nemotron)**
`lib/llm_detect.py`, `lib/llm_client.py`, `compile.py` T1~T4, `ingest_images.py`, `prompts/`.
완료: mock으로 탐지 후보 전부 테스트. DeepInfra 실 모델로 `pytest -m live` 통과, 탐지 결과 기록. `enabled: false` 통과, company 모드 공개 호스트 거부, daily 바이트 불변.

**Phase 5 — 점검과 공유**
`lint.py`(+T5), `confluence_push.py`(기본 활성).
완료: 보고서, 모의 Confluence 생성·갱신, 금지 폴더 거부, demo 흐름에 포함.

**Phase 6 — 포장**
`docs/` 전부, `SCHEDULING.md`(`schtasks`: `jira_sync` 15분, `daily_prepare` 08:30, `link`+`ingest`+`compile` 12:30·18:30, `lint` 금 17:00), `wheels/` 생성 명령, `kb.py demo`, 전체 테스트, 최종 트리.

---

## 9. 회사 이식 체크리스트 (`docs/DEPLOY.md`에 그대로)

1. 저장소 폴더 복사(`config.local.yaml`, `.state/`, `_trash/`, `examples/`, 집에서 만든 daily 제외).
2. Python 최신 확인, `pip install -r requirements.txt`(불가 시 `--no-index --find-links wheels`). 환경변수 `PYTHONUTF8=1`.
3. Jira·Confluence PAT 생성.
4. `python scripts/kb.py init` → environment=company → Jira(프로젝트 키 선택) → Confluence(개인 스페이스) → LLM main(사내 Nemotron, 자동 탐지) → fallback(Gemma) → 스케줄.
5. `python scripts/kb.py doctor` 전부 ✓. 공개 호스트 ✗가 하나라도 있으면 진행 금지.
6. `jira_sync --jql "key = <실제키>" --dry-run` 확인 후 `kb.py sync --full`.
7. Emacs: `(load "…/emacs/work-kb.el")`, `work-kb-root`, 필요하면 `work-kb-prefix-key` 변경, 캡처 테스트. 서버 모드 + `capture.ahk`(선택).
8. Obsidian: 볼트 열기, `obsidian/setup.md`.
9. 스케줄 등록(`SCHEDULING.md` 또는 init이 만든 명령).
10. 첫 2주: 캡처 + `link` + `sync`. 그다음 `ingest`·`compile`, 마지막 Confluence 푸시 검토 후 상시화.
11. 보안: 회사에서 생긴 파일은 집으로 가져오지 않는다. 개선점은 말로 적어 집에서 반영.

---

## 10. 하지 말 것 (Claude Code 행동 제약)

- 실제 회사 이름·URL·인명·이슈·사내 서버 주소를 추측해 넣지 않는다. 연결 값은 `config.local.yaml`에서만.
- DeepInfra 전용 코드 금지. 클라우드 LLM은 집 테스트 전용이며 `environment: company`에서는 코드가 막는다.
- 벡터 DB, 웹 프레임워크 금지. 모의 서버는 표준 라이브러리.
- daily/inbox/raw 수정 금지(키 정규화, inbox 이미지 이동, 사이드카 생성만 예외).
- Jira 자동 상태전환 금지. 삭제 금지(`_trash/`). 토큰 원문을 파일에 쓰지 않는다(`*_ref`만).
- `encoding` 미지정 파일 열기 금지. 경로는 `pathlib`. Windows에서 테스트가 통과하지 않으면 완료가 아니다.
- 한 Phase에서 다음 Phase 기능을 미리 만들지 않는다.
- 보고는 "무엇이 되고, 무엇을 확인해 달라"를 앞에, 과정은 뒤에.

---

## 11. 확정 사항 (사용자 답변, 더 묻지 않는다)

| 항목 | 결정 |
|---|---|
| 집 PC | Windows |
| 집 LLM | DeepInfra `https://api.deepinfra.com/v1/openai`, 키 `env:DEEPINFRA_API_KEY`, main=`nvidia/NVIDIA-Nemotron-3.5-Super-VL`, fallback=DeepInfra Gemma 계열(`/models`에서 선택, 선택 근거를 DECISIONS에) |
| 회사 LLM | Nemotron 3.5 Super VL, OpenAI 호환. 추론 토글·비전·JSON·컨텍스트는 **반드시 자동 탐지**(§6.11). 보조 모델 Gemma 존재 → fallback 프로필 |
| 비밀값 | Windows 자격 증명 저장소(`keyring`) 기본. 집의 `DEEPINFRA_API_KEY`만 환경변수 허용 |
| Jira 프로젝트 키 | 회사에서 `init`의 프로젝트 목록에서 선택. 개발은 허구 `KB`, `OPS` |
| Confluence | 개인 스페이스 **처음부터 사용**(`enabled: true` 기본, init에서 자동 탐지) |
| 사람 표기 | `@이름` |
| 고정 태그 | 회의, 결정, 요청, 이슈, 학습, 아이디어, 개인, 참고, 일정, 리뷰 |
| Emacs 접두 키 | 기본 `C-c k`, **한 줄로 교체 가능**(`work-kb-prefix-key` + `work-kb-command-map`) |
| 이미지 캡처 | 클립보드(`C`)와 파일(`F`) **둘 다**, inbox 드롭도 ingest가 처리 |
