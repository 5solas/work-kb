# work-kb

업무 메모·지식·Jira 일감을 **평범한 마크다운 폴더 하나**에서 다루는 도구 묶음이다.
Obsidian은 열람 창, Neovim(Neovide)은 입력·명령 창이다.

- 사용법·설치(Neovim): `docs/USER-GUIDE.md` · 회사 PC 이식: `docs/DEPLOY.md` · 포장(`wheels/`): `docs/PACKAGING.md`
- 스케줄: `docs/SCHEDULING.md` · 문제 해결: `docs/TROUBLESHOOTING.md`
- 설계 결정과 확인 대기 목록: `docs/DECISIONS.md` · 실 모델 검증: `docs/LIVE.md`
- 에이전트 규칙: `AGENTS.md` (`CLAUDE.md`는 이 파일을 읽으라는 한 줄)

## 1. 무엇을 하나

1. **캡처**: 키 하나로 오늘 노트에 한 줄을 던진다. 분류하지 않는다. 스크린샷·사진도 된다.
2. **자동 연결**: 노트 속 Jira 키·`@이름`·주제를 엔티티 페이지에 엮는다. LLM이 필요 없다.
3. **위키 편찬**: LLM이 기록과 이미지를 읽고 결정 로그·할 일·엔티티 요약·주제 제안을 갱신한다. LLM이 없으면 이 단계만 빠진다.
4. **Jira 통합**: 내 일감을 마크다운 캐시로 받는다. 댓글·전환·로그·생성 같은 작은 쓰기는 큐(outbox)로 보낸다. 자동 상태 전환은 없다.
5. **이식성**: 폴더를 복사하고 `python scripts/kb.py init`을 한 번 실행하면 된다.

원칙: 사람은 분류하지 않는다. Jira가 진실의 원천이다. LLM 없이도 완결된다. 삭제 대신 `_trash/`. 기계는 관리 구역만 고친다.

## 2. 폴더 구조

```
work-kb/
├── AGENTS.md  CLAUDE.md          에이전트 규칙
├── config.yaml                   구조 설정 (연결 값 없음)
├── config.local.yaml             환경 설정 (git 제외, kb.py init이 생성)
├── config.local.example.yaml     집·회사 두 예시
├── requirements.txt              requests, pyyaml, markdown, keyring, pillow
├── requirements-dev.txt          pytest, hypothesis (시험용)
├── wheels/                       오프라인 설치용 (선택, kb.py wheels)
├── daily/YYYY-MM-DD.md           사람이 씀
├── inbox/                        긴 글, 클립, 드롭한 이미지·사이드카
├── raw/assets/                   이미지·첨부 (Obsidian 첨부 폴더)
├── jira/                         기계 소유 캐시 (KEY-123.md, _views/, outbox.jsonl, .cache.sqlite)
├── wiki/                         기계 소유 (index, projects/, people/, topics/, decisions.md, actions.md, _proposals.md, _lint.md)
├── _trash/                       삭제 대신 옮기는 곳
├── .state/                       원장·동기화 시각·로그 (git 제외)
├── prompts/                      LLM 프롬프트
├── scripts/                      kb.py (단일 진입점)와 모듈, lib/
├── nvim/                         work-kb Neovim 플러그인, capture.ahk
├── obsidian/setup.md             Obsidian 설정 안내
├── examples/sample-vault/        허구 샘플 볼트
└── docs/                         이 문서들
```

볼트를 코드 폴더와 따로 둘 수 있다. 명령의 `--root`(또는 환경변수 `KB_ROOT`)로 볼트 루트를 준다.
Neovim은 `KB_ROOT`를 보지 않는다. setup의 `vault`(nvim-config에서는 `lua\plugins\work-kb.lua`의 `opts.vault`)를 같은 볼트로 정한다.

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

- 캡처는 `## 기록` 끝에 `- HH:MM ` 접두로 붙는다. 그 밖은 자유롭게 편집해도 된다. 읽은 뒤 파일이 바뀌었으면 덮어쓰지 않는다.
- Jira 키는 `[[KEY-123]]` 또는 맨 글자 `KEY-123`(숫자는 ASCII). `kb.py link`가 맨 글자를 위키링크로 바꾼다.
- 사람은 `@이름`으로만 인식한다. 이름을 추정하지 않는다.
- `## 오늘`의 `[x]`는 전환 **제안 목록**(`kb.py today --check-done`)에만 오른다.
- `## 기록`의 키 없는 `- [ ]`는 다음 날 `## 이월`로 복사되고 원래 줄은 `- [>]`가 된다.
- 이미지: `kb.py ingest`가 추출 글을 사이드카 `inbox/<이름>.md`에 쓴다(그 이름의 다른 노트가 있으면 `inbox/<이름.확장자>.md`). daily는 바뀌지 않는다.
- `inbox/`에 드롭한 이미지(png·jpg·jpeg·webp)는 `raw/assets/`로 옮긴다. 노트가 이미 가리키는 이미지는 옮기지 않는다.

## 4. 명령 표

`python scripts/kb.py <명령> [옵션]`. 모든 명령이 `--root`를 받는다(`wheels`는 무시). 옵션 전체는 `<명령> --help`.
"에이전트" 열은 `KB_AGENT=1` 세션의 허용 여부다(5절).

| 명령 | 하는 일 | 주요 옵션 | 에이전트 |
|---|---|---|---|
| `kb.py init` | 연결 설정 마법사. 재실행하면 기존 값이 기본값. `--yes`는 확인 질문을 예로 처리 | `--dev` `--yes` `--section jira` `--non-interactive` `--from-env` `--redetect` | 거부 |
| `kb.py doctor` | 연결·환경 점검 | `--json` `--redetect` | 허용(`--redetect` 거부) |
| `kb.py today` | 오늘 daily 준비(오늘·이월 구역), 경로 출력 | `--date` `--no-carryover` `--print-path` `--check-done` `--json` | 허용 |
| `kb.py link` | A단계: 키·`@이름`·주제(있는 `wiki/topics/*.md`)를 엔티티 페이지에 연결 | `--dry-run` `--stub` `--rebuild` `--json` | 허용 |
| `kb.py status` | 상태 요약 (네트워크 안 씀) | `--json` | 허용 |
| `kb.py search` | daily·inbox·wiki·jira 낱말 검색 (공백은 AND) | `--json` `--limit` `--in wiki` | 허용 |
| `kb.py sync` | Jira 일감을 캐시로 받기 (읽기 전용) | `--full` `--key` `--jql` `--cache` `--dry-run` | 허용 |
| `kb.py act` | Jira 쓰기(outbox 경유). 하위: `comment` `transition` `transitions` `assign` `worklog` `create` `promote` `flush` `list` | `--yes` `--body` `--from-line` `--summary` `--retry-failed` `--retry-uncertain` | `list`·`transitions`만 |
| `kb.py compile` | B단계: 태그·결정·할 일·요약·주제 제안 (LLM). 기본은 최근 7일 daily(`--since 0`이면 전부). inbox는 이미지 사이드카만 읽는다 | `--date` `--since` `--task decisions` `--dry-run` `--force` | 허용 |
| `kb.py ingest` | B단계: 이미지 글 추출 → 사이드카 (비전 LLM) | `--dry-run` `--force` | 허용 |
| `kb.py proposals` | 주제 제안: `list` / `accept <번호\|all>` | | `list`만 |
| `kb.py lint` | C단계: 점검 보고 `wiki/_lint.md` (항상 종료 0) | `--llm` `--json` | 허용 |
| `kb.py push` | Confluence 개인 스페이스로 게시. 링크는 `http`·`https`·`mailto`만 남긴다 | `--dry-run` `--only` | 거부 |
| `kb.py schedule` | 작업 스케줄러 XML과 `schtasks` 명령 생성(등록 안 함) | `--print` `--xml-dir` `--company` | 거부 |
| `kb.py wheels` | 오프라인 설치용 `wheels/` 내려받기 | `--targets` `--dir` | 거부 |
| `kb.py demo` | 모의 서버로 전체 흐름 시연. `tests/`가 필요해 **집 전용** | `--keep` `--fixed-ports` | 거부 |
| `kb.py serve` | 편집기용 상주 서버. `status` `search` `today` `link` `proposals list` `act list`만 실행하고 `today`·`link`는 파일을 쓴다. 나머지는 편집기가 한 번 실행으로 돌린다 | `--idle-sec` | 허용 |

- `serve` 요청이 90초를 넘으면 Neovim은 쓰기 명령(`today`·`link`)을 다시 돌리지 않고 실패로 끝낸다.
- `sync`는 범위 밖 일감이 403이면 `in_scope: false`로 표시하고 계속한다. 토큰이 무효면 종료 2.

`kb.py act` 예:

```
python scripts/kb.py act comment KB-12 --body "내용"
python scripts/kb.py act comment KB-12 --from-line daily/2026-11-05.md:14
python scripts/kb.py act transition KB-12 완료 --yes
python scripts/kb.py act worklog KB-12 1h30m --comment "리뷰"
python scripts/kb.py act create KB --summary "제목" --type Task --yes
python scripts/kb.py act promote KB --from daily/2026-11-05.md:14 --yes
python scripts/kb.py act list --all
```

- `transition`·`create`·`promote`는 `--yes`가 없으면 확인을 묻는다. 워크로그 기간은 `1h30m`·`2h`·`45m` 꼴만 된다.
- 오프라인이면 숫자 id 전환(`act transition KEY <id>`)만 큐에 넣고 종료 3. 이름 전환은 실패한다.
- `act promote`는 이미 키가 붙었거나 승격 기록이 남은 줄을 거부한다(종료 4).

### 종료 코드

| 코드 | 의미 |
|---|---|
| 0 | 성공 (LLM 비활성으로 건너뛴 경우도 0) |
| 1 | 일반 오류, 전송 실패(`failed`), 확인 거부·EOF, 동시 편집 충돌 |
| 2 | 인증 실패 (PAT 만료·거부) |
| 3 | 네트워크 오류 (다운, 타임아웃, 결과 불확실 포함) |
| 4 | 설정·사용법 오류 (Python 3.14 미만·패키지 누락, 설정 누락, project_keys 밖 프로젝트, 전환 후보 0개·여럿) |
| 5 | 회사 환경에서 공개 LLM 호스트라 실행 거부 |
| 6 | 다른 프로세스가 잠금·파일을 잡고 있음 (잠시 뒤 다시) |
| 7 | 정책 거부 (`KB_AGENT=1` 세션에서 쓰기 계열 명령) |

## 5. 에이전트

에이전트(Claude Code 등)는 `AGENTS.md`를 따르고 세션에 `KB_AGENT=1`을 둔다.

- 허용: 볼트 안에서 도는 명령. `link`·`today` 등은 관리 구역과 허용된 daily만 쓴다.
- 거부(종료 7): `init`, `act` 쓰기, `proposals accept`, `push`, `schedule`, `wheels`, `demo`, `doctor --redetect`. `scripts/*.py`를 직접 실행해도 거부된다.
- 시작할 때 `kb.py status`로 동기화·outbox·미처리 항목·탐지 결과를 본다.

## 6. Neovim

일반 설정(Neovim, Neovide, 폰트, LSP, `nvd` 데몬)은 별도 저장소 nvim-config가 맡는다. 이 저장소의 `nvim/`은 work-kb 플러그인 하나다.
`install.ps1`이 `WORK_KB_ROOT`를 이 폴더로 정하면 nvim-config가 플러그인을 불러오고 접두 키 `<leader>k`와 `:WorkKb`가 생긴다. 키 표와 사용법은 `docs/USER-GUIDE.md`.

접두 키를 바꾸는 두 방법:

- 전체: setup에 `prefix`를 준다(nvim-config `lua\plugins\work-kb.lua`의 `opts = { prefix = "<F8>" }`). `prefix = false`면 키를 붙이지 않는다.
- 키 하나: `vim.keymap.set("n", "<F8>c", function() require("work-kb").call("capture") end)`. 이름은 `:WorkKb`와 같다.

클립보드 이미지(`C`)는 `scripts/clip_image.ps1`을 **Windows PowerShell 5.1**로 실행한다. 정책(GPO)이 막으면 `kb.py doctor`가 ✗로 알린다. 이때는 `F`(파일 선택)를 쓴다.

## 7. Obsidian

볼트 루트를 열고 `obsidian/setup.md`를 따른다. `.obsidian/`은 커밋하지 않는다.

## 8. 스케줄

`python scripts/kb.py schedule`이 작업 스케줄러 XML과 `schtasks` 명령을 만든다. 등록은 사람이 한다. 절차는 `docs/SCHEDULING.md`.

## 9. LLM 프로필, 탐지, 회사 가드

LLM 설정은 `config.local.yaml`의 프로필(main, fallback 등)이다. 모두 OpenAI 호환 `/chat/completions`다.
`kb.py init`(또는 `--redetect`)이 실제 호출로 다음을 탐지해 프로필의 `detected`에 적는다. `kb.py doctor`는 저장하지 않고 재검증만 한다.

| 항목 | 내용 |
|---|---|
| `context_tokens` | `/models` 메타데이터. 없으면 기본값을 `assumed`로 |
| `json_mode` | `response_format: json_object` 수용 여부 |
| `vision` | 이미지 입력 수용 여부 |
| `reasoning_toggle` | 추론 켜기/끄기 방식. 못 찾으면 `method: none`, 이때는 `reasoning_budget_tokens`를 손으로 정한다(`docs/TROUBLESHOOTING.md`, 실제 예는 `docs/LIVE.md`) |

**회사 가드**: `environment: company`에서 LLM 주소가 공개 호스트면 막는다. 회사 자료 유출을 코드로 막는 장치다.

- 공개 호스트: `public_hosts_blocklist`(`config.yaml`)의 도메인, 또는 공개 IP. 사설·루프백 IP는 목록에 없으면 허용한다.
- `kb.py doctor`는 ✗, `kb.py compile`·`kb.py ingest`는 종료 5. `kb.py lint --llm`은 모순 검사만 건너뛴다.
- 진행하지 말고 주소를 사내 서버로 고친다(`kb.py init --section llm`).

LLM이 꺼져 있거나 연결된 프로필이 없으면 B단계는 한 줄 로그만 남기고 종료 0이다.

## 10. outbox 상태

Jira 쓰기는 `jira/outbox.jsonl`에 먼저 기록하고 바로 전송을 시도한다.

| 상태 | 뜻 |
|---|---|
| `pending` | 아직 못 보냄. 다음 `kb.py act flush`가 보낸다 |
| `sending` | 보내는 중. 주인 프로세스가 죽었거나 `2 × jira.write_timeout_sec`(기본 60초, 합 120초)가 지나면 `uncertain`으로 본다 |
| `done` | 성공 (이후 `sync --key`로 캐시 갱신) |
| `failed` | 5번 실패, 또는 권한 없음·4xx처럼 되풀이해도 소용없는 오류 |
| `uncertain` | 서버가 받았는지 모름(전송 뒤 끊김·타임아웃·이상한 2xx). 자동 재전송 없음. 다시 보내다 일시 오류가 나도 `failed`가 되지 않는다 |

- `kb.py act flush`: `pending`만 보낸다.
- `kb.py act flush --retry-failed [ID ...]`: `failed`도 다시 보낸다(시도 횟수 초기화).
- `kb.py act flush --retry-uncertain [ID ...]`: `uncertain`도 다시 보낸다. **먼저 Jira에 실제로 올라갔는지 확인**한다.
- `kb.py act list`: 대기·실패 항목. `--all`은 완료 포함, `--json`은 기계 판독용.

## 11. 점검 `kb.py doctor`

한 줄에 하나: `✓ 이름: 설명`(정상), `✗ 이름: 문제 → 해결 방법`(종료 1, 회사 공개 LLM 호스트면 5), `- 이름: 참고`(종료 코드 무관).
`-` 줄(비활성 서비스, keyring 백엔드, 프로그램 경로, 볼트 경로의 한글, 시간대 등)의 해결은 `docs/TROUBLESHOOTING.md`.
