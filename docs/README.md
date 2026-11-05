# work-kb

업무 메모·지식·Jira 일감을 **평범한 마크다운 폴더 하나**에서 다루는 도구 묶음이다.
Obsidian은 열람 창, Neovim(Neovide)은 입력·명령 창, 폴더는 그대로 마크다운이다.

- 설치·이식: `docs/DEPLOY.md` · 스케줄: `docs/SCHEDULING.md` · 문제 해결: `docs/TROUBLESHOOTING.md`
- 설계 결정과 사용자 확인 대기 목록: `docs/DECISIONS.md` · 실 모델 검증 기록: `docs/LIVE.md`
- 에이전트 규칙: `AGENTS.md` (유일한 규칙 파일, `CLAUDE.md`는 이 파일을 읽으라는 한 줄)

## 1. 무엇을 하나

1. **캡처**: 키 하나로 오늘 날짜 노트에 한 줄을 던진다. 분류하지 않는다. 글 대신 스크린샷·사진도 된다.
2. **자동 연결**: 스크립트가 노트 속 Jira 키와 `@이름`을 찾아 엔티티 페이지에 엮는다. LLM이 필요 없다.
3. **위키 편찬**: LLM(Nemotron)이 기록과 이미지를 읽고 결정 로그·할 일·엔티티 요약·주제 제안을 갱신한다. LLM이 없으면 이 단계만 조용히 빠진다.
4. **Jira 통합**: 내 일감을 로컬 마크다운 캐시로 받아 검색·열람하고, 댓글·전환·로그·생성 같은 작은 쓰기를 큐(outbox)로 보낸다. 자동 상태 전환은 없다.
5. **이식성**: 저장소 폴더를 복사하고 `python scripts/kb.py init`을 한 번 실행하면 연결 설정이 끝난다.

원칙: 사람은 분류하지 않는다(구조는 날짜뿐). Jira가 진실의 원천이다. LLM 없이도 완결된다. 삭제 대신 `_trash/`. 기계는 관리 구역만 고친다.

## 2. 폴더 구조

```
work-kb/
├── AGENTS.md  CLAUDE.md          에이전트 규칙
├── config.yaml                   구조 설정 (연결 값 없음)
├── config.local.yaml             환경 설정 (git 제외, kb.py init이 생성)
├── config.local.example.yaml     집·회사 두 예시
├── requirements.txt              requests, pyyaml, markdown, keyring, pillow
├── wheels/                       오프라인 설치용 (선택, kb.py wheels)
├── daily/YYYY-MM-DD.md           사람이 씀
├── inbox/                        제목 있는 긴 글, 클립, 드롭한 이미지
├── raw/assets/                   이미지·첨부 (Obsidian 첨부 폴더)
├── jira/                         기계 소유 캐시 (KEY-123.md, _views/, outbox.jsonl, .cache.sqlite)
├── wiki/                         기계 소유 (index, projects/, people/, topics/, decisions.md, actions.md, _proposals.md, _lint.md)
├── _trash/                       삭제 대신 옮기는 곳
├── .state/                       원장·동기화 시각·로그 (git 제외)
├── prompts/                      LLM 프롬프트
├── scripts/                      kb.py (단일 진입점)와 모듈, lib/
├── nvim/                         work-kb Neovim 플러그인, capture.ahk (일반 설정은 nvim-config 저장소)
├── obsidian/setup.md             Obsidian 설정 안내
├── examples/sample-vault/        허구 샘플 볼트
└── docs/                         이 문서들
```

볼트(daily/ inbox/ wiki/ 등)를 코드 폴더와 따로 둘 수도 있다. 명령의 `--root`(또는 환경변수 `KB_ROOT`)로 볼트 루트를 준다(`wheels`는 `--root`를 받기만 하고 무시한다. 늘 저장소 폴더에서 동작한다).

## 3. daily 형식과 캡처 규칙

```markdown
---
date: 2026-11-05
---
## 오늘
<!-- kb:today:start -->
- [ ] [[KB-12]] 캐시 front matter 확정 · 진행중 · 마감 11-06
<!-- kb:today:end -->

## 이월
<!-- kb:carryover:start -->
- [ ] 개인: 경비 정산 (← 2026-11-04)
<!-- kb:carryover:end -->

## 기록
- 09:12 [[KB-12]] @김철수 와 front matter 논의. updated는 UTC로 저장하기로.
- 14:20 ![[raw/assets/2026-11-05-1420-whiteboard.png]] 회의 화이트보드
```

- 캡처는 항상 `## 기록` 끝에 `- HH:MM ` 접두로 추가된다. 그 밖은 자유롭게 편집해도 된다.
- Jira 키는 `[[KEY-123]]` 또는 맨 글자 `KEY-123`. `kb.py link`가 맨 글자를 위키링크로 정규화한다(daily를 기계가 고치는 유일한 예외, 멱등).
- 사람은 `@이름` 접두로만 인식한다. 한국어 이름을 추정하지 않는다.
- 이미지는 `![[raw/assets/...]]`. `kb.py ingest`가 사이드카(`같은이름.md`)에 추출 글을 쓴다. daily 본문은 바뀌지 않는다.
- `## 오늘`의 `[x]`는 전환 **제안 목록**(`kb.py today --check-done`)에만 오른다. 자동 전환은 없다.
- `## 기록`의 `- [ ]` 중 Jira 키 없는 줄은 개인 할 일로 다음 날 `## 이월`에 복사되고 원래 줄은 `- [>] (날짜로 이월)`이 된다.
- 드롭한 이미지(`inbox/*.png|jpg|jpeg|webp`)는 `kb.py ingest`가 `raw/assets/`로 옮기고 사이드카를 남긴다.

## 4. 명령 표

모든 명령은 `python scripts/kb.py <명령> [옵션]`. 모든 명령이 `--root`를 받는다(`wheels`만은 받아도 무시하고 저장소 폴더에서 동작한다). 옵션 전체는 `python scripts/kb.py <명령> --help`.
`KB_AGENT=1` 세션에서는 읽기 계열만 허용된다(5절 참고, 아래 표의 "에이전트" 열).

| 명령 | 하는 일 | 주요 옵션 | 에이전트 |
|---|---|---|---|
| `kb.py init` | 연결 설정 마법사. 재실행하면 기존 값이 기본값 | `--dev` `--yes` `--section jira` `--non-interactive` `--from-env` `--redetect` | 거부 |
| `kb.py doctor` | 연결·환경 점검 | `--json` `--redetect` | 허용 |
| `kb.py today` | 오늘 daily 준비(오늘·이월 구역), 경로 출력 | `--date` `--no-carryover` `--print-path` `--check-done` `--json` | 허용 |
| `kb.py link` | A단계: 키·`@이름`을 엔티티 페이지에 연결 (LLM 없음) | `--dry-run` `--stub` `--rebuild` `--json` | 허용 |
| `kb.py status` | 상태 요약 (네트워크 안 씀) | `--json` | 허용 |
| `kb.py search` | daily·inbox·wiki·jira에서 낱말 검색 (공백은 AND) | `--json` `--limit` `--in wiki` | 허용 |
| `kb.py sync` | Jira 일감을 캐시로 받기 (읽기 전용) | `--full` `--key` `--jql` `--cache` `--dry-run` | 허용 |
| `kb.py act` | Jira 쓰기(outbox 경유). 하위: `comment` `transition` `transitions` `assign` `worklog` `create` `promote` `flush` `list` | `--yes` `--body` `--from-line` `--summary` `--retry-failed` `--retry-uncertain` | `list`·`transitions`만 |
| `kb.py compile` | B단계: 태그·결정·할 일·엔티티 요약·주제 제안 (LLM) | `--date` `--since` `--task decisions` `--dry-run` `--force` | 허용 |
| `kb.py ingest` | B단계: 이미지에서 글 추출 → 사이드카 (비전 LLM) | `--dry-run` `--force` | 허용 |
| `kb.py proposals` | 주제 제안: `list` / `accept <번호\|all>` | | `list`만 |
| `kb.py lint` | C단계: 점검 보고 `wiki/_lint.md` (항상 종료 0) | `--llm` `--json` | 허용 |
| `kb.py push` | Confluence 개인 스페이스로 wiki 페이지 게시 | `--dry-run` `--only` | 거부 |
| `kb.py schedule` | 작업 스케줄러 XML과 `schtasks` 명령 생성(등록은 하지 않음) | `--print` `--xml-dir` `--company` | 거부 |
| `kb.py wheels` | 오프라인 설치용 `wheels/` 내려받기 | `--targets` `--dir` | 거부 |
| `kb.py demo` | 모의 서버 3개로 전체 흐름 시연 (임시 볼트) | `--keep` `--fixed-ports` | 거부 |
| `kb.py serve` | 편집기용 상주 서버: 표준 입출력으로 JSON을 한 줄씩 주고받으며 빠른 명령(`status` `search` `today` `link` 등)을 같은 프로세스에서 실행 | `--idle-sec` | 허용 |

`kb.py demo`는 `tests/`의 모의 서버를 쓴다. 회사 배포는 `tests/`를 제외하므로 **demo는 집 전용**이다.

`kb.py act` 자주 쓰는 형태:

```
python scripts/kb.py act comment KB-12 --body "내용"
python scripts/kb.py act comment KB-12 --from-line daily/2026-11-05.md:14
python scripts/kb.py act transitions KB-12 --json
python scripts/kb.py act transition KB-12 완료 --yes
python scripts/kb.py act worklog KB-12 1h30m --comment "리뷰"
python scripts/kb.py act create KB --summary "제목" --type Task --yes
python scripts/kb.py act promote KB --from daily/2026-11-05.md:14 --yes
python scripts/kb.py act flush --retry-uncertain
python scripts/kb.py act list --all
```

`transition`·`create`·`promote`는 `--yes`가 없으면 확인을 묻는다. 워크로그 기간은 `1h30m`·`2h`·`45m` 형태만 된다.

### 종료 코드

| 코드 | 의미 |
|---|---|
| 0 | 성공 (LLM 비활성으로 건너뛴 경우도 0) |
| 1 | 일반 오류, 전송 실패(`failed`), 확인 거부·EOF |
| 2 | 인증 실패 (PAT 만료·거부) |
| 3 | 네트워크 오류 (다운, 타임아웃, 결과 불확실 포함) |
| 4 | 설정·사용법 오류 (설정 누락, project_keys 밖 프로젝트, 전환 후보 0개·여럿) |
| 5 | 회사 환경에서 공개 LLM 호스트라 실행 거부 |
| 6 | 다른 프로세스가 잠금·파일을 잡고 있음 (잠시 뒤 다시) |
| 7 | 정책 거부 (`KB_AGENT=1` 세션에서 쓰기 계열 명령) |

## 5. 에이전트

에이전트(Claude Code 등)는 `AGENTS.md`를 따르고, 세션에 `KB_AGENT=1`을 둔다. 읽기 계열(`status` `search` `link` `today` 등)만 허용되고
쓰기·설정 명령(`init` `act`의 쓰기 하위 명령 `proposals accept` `push` `schedule` `wheels` `demo`)은 종료 7로 거부된다.
에이전트는 `kb.py status`로 마지막 동기화, 대기 outbox, 미처리 제안·이미지, 프로필별 탐지 결과, environment를 먼저 본다.

## 6. Neovim

Neovim 쪽 일반 설정(Neovim, Neovide, 폰트, LSP, `nvd` 데몬)은 이 저장소에 없다. 별도 저장소 nvim-config가 맡고, 이 저장소의 `nvim/` 폴더는 work-kb 플러그인 하나다.
`install.ps1`이 사용자 환경 변수 `WORK_KB_ROOT`를 이 저장소 폴더로 정하면 nvim-config가 `<WORK_KB_ROOT>\nvim`을 불러오고 접두 키 `<leader>k`로 키와 `:WorkKb`가 생긴다.
설치, 파일·폴더 위치, 키 표, 사용법: `docs/USER-GUIDE.md`

### 클립보드 요구 사항

`C`는 `scripts/clip_image.ps1`을 **Windows PowerShell 5.1**(`%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe`, `-NoProfile -ExecutionPolicy Bypass -STA`)로 실행한다.
회사 정책(GPO)이 PowerShell 실행을 막으면 `kb.py doctor`가 ✗로 알린다. 이때는 `F`(파일 선택)를 쓴다. 클립보드에 이미지가 없으면 안내만 나온다.

## 7. Obsidian

볼트 루트를 열고 `obsidian/setup.md`를 따른다(위키링크, Daily notes 폴더 `daily`·형식 `YYYY-MM-DD`, 첨부 폴더 `raw/assets`, Bases 파일 `jira/_views/일감.base`·`wiki/엔티티.base`).
`.obsidian/`은 커밋하지 않는다.

## 8. 스케줄

`python scripts/kb.py schedule`이 작업 스케줄러용 XML과 `schtasks` 명령을 만든다(등록은 사람이 실행). 표와 절차는 `docs/SCHEDULING.md`.

## 9. LLM 프로필, 탐지, 회사 가드

LLM 설정은 `config.local.yaml`의 프로필(main, fallback 등)이다. 모두 OpenAI 호환 `/chat/completions`이고 특정 업체 전용 코드는 없다.
`kb.py init`(또는 `kb.py init --redetect`, `kb.py doctor --redetect`)이 실제 호출로 다음을 **탐지**해 프로필의 `detected`에 기록한다.

- `context_tokens`: `/models` 메타데이터, 없으면 기본값을 `assumed`로 기록
- `json_mode`: `response_format: json_object` 수용 여부
- `vision`: 이미지 입력 수용 여부
- `reasoning_toggle`: 추론 켜기/끄기 방식. `reasoning_effort` 파라미터 → `chat_template_kwargs.enable_thinking` → 시스템 프롬프트 `/think` `/no_think` → 게이트웨이식 `reasoning` 중 켜고 끄기가 둘 다 구분되는 첫 방식. 모두 실패하면 `method: none`이며 이때는 `reasoning_budget_tokens`를 손으로 설정한다(`docs/TROUBLESHOOTING.md`).

기본 `kb.py doctor`는 저장하지 않고 기록된 탐지를 프로필당 몇 번의 호출로 재검증한다. 실 모델에서 잡힌 방식의 예는 `docs/LIVE.md`.

**회사 가드**: `config.local.yaml`의 `environment: company`에서 LLM 프로필 주소가 `public_hosts_blocklist`(deepinfra.com, openai.com 등 공개 클라우드 목록, `config.yaml`)에 걸리면
`kb.py doctor`가 ✗로 막고 `kb.py compile`·`kb.py ingest`는 실행을 거부한다(종료 5). 회사 자료가 외부로 나가는 사고를 코드로 막는 장치다. 이 경우 진행하지 말고 프로필 주소를 사내 서버로 고친다(`kb.py init --section llm`).

LLM이 꺼져 있거나(`llm.enabled` 거짓) 연결된 프로필이 없으면 B단계는 한 줄 로그만 남기고 종료 0이다.

## 10. outbox 상태

Jira 쓰기는 `jira/outbox.jsonl`에 먼저 기록되고 즉시 전송을 시도한다.

| 상태 | 뜻 |
|---|---|
| `pending` | 아직 보내지 못함. 다음 `kb.py act flush`가 보낸다 |
| `sending` | 보내는 중으로 점유됨. 중단되면 이 상태로 남고 오래되면 `uncertain`으로 표시된다 |
| `done` | 성공 (이후 `sync --key`로 캐시 갱신) |
| `failed` | 5번 실패하거나 권한 없음·4xx 등 되풀이해도 소용없는 오류 |
| `uncertain` | 전송 뒤 끊김·타임아웃·형태가 다른 2xx 응답 등 서버가 받았는지 모름. 자동 재전송 없음 |

flush 옵션:

- `kb.py act flush`: 대기(`pending`) 항목만 id 순서로 보낸다. `uncertain`은 건너뛴다.
- `kb.py act flush --retry-failed [ID ...]`: `failed`도 다시 보낸다(시도 횟수 초기화). id를 주면 그 기록만.
- `kb.py act flush --retry-uncertain [ID ...]`: `uncertain`도 다시 보낸다. **먼저 Jira에서 실제로 올라갔는지 확인**한다(중복 방지). id를 주면 그 기록만.
- `kb.py act list`는 대기·실패 항목, `--all`은 완료 포함, `--json`은 기계 판독용.

## 11. 점검 `kb.py doctor`

한 줄에 하나씩: `✓ 이름: 설명`(정상), `✗ 이름: 문제 → 해결 방법`(문제, 종료 1; 회사 환경의 공개 LLM 호스트면 종료 5), `- 이름: 참고`(선택·안내, 종료 코드 무관).
자주 보는 `-` 줄: 비활성 서비스, keyring 백엔드, Neovim·Neovide·Obsidian 경로, 볼트 경로의 한글, 시간대, `/models` 없음. 해결은 `docs/TROUBLESHOOTING.md`.
