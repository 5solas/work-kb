# work-kb 저장소 심층 분석 보고서 (agy)

## 요약

저장소 전체(`main` 기반 핵심 모듈 + `nvim` 브랜치 프런트엔드, `scripts/`·`nvim/`·`prompts/`·`install.ps1`·`uninstall.ps1`·`tests/`·`docs/`)를 `work-kb-build-brief-v3.md`와 `docs/DECISIONS.md` 기준으로 교차 점검했다.
Python 3.14(`2170 passed`)·3.13(`2165 passed, 5 failed` → `pip` 시드 후 `5 passed`, 3.14 단일 대상으로 범위 밖)·병렬 `-n auto`·`chcp 949`·선택 마커(`perf`·`demo`·`packaging`)와 Neovim 모의 E2E·정적/속성 검증까지 전수 실행했다.
기존 `nvim` 브랜치 11건(`A-01`~`A-11`)에 더해 `link.py` 주제(`wiki/topics/*.md`) 연결 누락(D22), `jira_act` `uncertain`→`failed` 전락(D52), `wheels.py` `KB_AGENT=1` 거부 누락, 상대 `ca_bundle`·FTS 잠금 예외 누수·`ingest`/`lint`/`config` 잠금·경계 결함 16건을 새로 확인했다.
정확성 23건(높음 1, 중간 21, 낮음 1)과 개선 4건(낮음 4), 총 27건을 보고하며 21건은 `review/repro/` 최소 재현 스크립트로 실패를 직접 재현했다. (1단계-2 상호 검증에서 심각도 기준 및 코드 재확인에 따라 8건 심각도 정정 및 2건 설명 보완 반영)

## 검증 결과

| 도구 / 스위트 | 실행 명령 요약 | 결과 | 비고 / 요약 |
|---|---|---|---|
| Python 3.14 전체 pytest | `.venv\Scripts\python.exe -m pytest -q -p no:randomly` | **2170 passed, 2 skipped, 14 deselected** (`524.79s`, exit 0) | `pytest.ini` 기본 제외 마커(`live`·`perf`·`demo`·`packaging`) 적용 |
| Python 3.14 병렬 + 분기 커버리지 | `.venv\Scripts\python.exe -m pytest -q -p no:randomly -n auto --cov=scripts --cov-branch --cov-report=term-missing` | **2170 passed, 2 skipped** (`92.15s`, exit 0), 커버리지 **87%** | `13356` stmts / `1561` miss / `4810` branches / `604` partial (`serve.py` 14%는 하위 프로세스 미추적, `demo.py` 44%는 마커 제외, `doctor.py` 76%는 nvim 점검 미테스트) |
| Python 3.14 `perf` 마커 | `.venv\Scripts\python.exe -m pytest -q -p no:randomly -o addopts="-p no:cacheprovider" -m perf` | **1 passed, 2185 deselected** (`18.28s`, exit 0) | `test_link_perf.py` 예산 내 통과 |
| Python 3.14 `demo` 마커 | `.venv\Scripts\python.exe -m pytest -q -p no:randomly -o addopts="-p no:cacheprovider" -m demo` | **5 passed, 2181 deselected** (`8.67s`, exit 0) | 모의 서버 3종 전체 흐름 통과 |
| Python 3.14 `packaging` 마커 | `.venv\Scripts\python.exe -m pytest -q -p no:randomly -o addopts="-p no:cacheprovider" -m packaging` | **3 passed, 2183 deselected** (`26.43s`, exit 0) | 실제 wheel 내려받기·한글 경로 오프라인 venv 설치 통과 |
| Python 3.13 전체 pytest (3.14 단일 대상으로 범위 밖) | `.venv313\Scripts\python.exe -m pytest -q -p no:randomly` | **5 failed, 2165 passed, 2 skipped, 14 deselected** (`554.54s`, exit 1) → `pip` 시드 후 실패 5건 재실행 **5 passed** (`8.08s`) | 초기 5건 실패(`test_install_script.py` 3, `test_packaging.py` 2)는 `.venv313`에 `pip` 미포함 때문이었음 |
| `cp949` / `PYTHONUTF8=0` 인코딩 | `cmd /c "chcp 949 >nul && set PYTHONUTF8=0&& .venv\Scripts\python.exe -m pytest -q tests/test_cli_encoding.py tests/test_encoding_rule.py tests/test_kb_dispatch.py tests/test_serve.py"` | **144 passed** (`32.80s`, exit 0) | Windows `cp949` 콘솔에서 UTF-8 I/O 정상 동작 |
| Neovim + 모의 서버 E2E | `.venv\Scripts\python.exe -X utf8 ...\scratch\nvim_e2e.py` | **exit 0** | `mock_jira`·`mock_llm` + Lua 17개 모듈·캡처·검색·`open_key`·`jira`·`ingest_compile` 연동 통과 |
| CLI / `serve` 프로토콜 동적 점검 | `.venv\Scripts\python.exe ...\scratch\dyn_check.py` | **exit 0** | 잘못된 JSON/UTF-8 거부, 50KB 검색, 비허용 명령 후 정상 응답, `shutdown` 종료 확인 (`A-01` 동시 재현) |
| Hypothesis 속성 검사 | `.venv\Scripts\python.exe ...\scratch\hyp_check.py` | **6/6 passed** | `managed`·`frontmatter`·`link`·`daily_prepare`·`search` 불변식 확인 |
| `ruff` (기본 / 확장 규칙) | `ruff check . --statistics` / `--select E,F,W,B,S,SIM,PL,PTH,RUF,UP,ERA,BLE,TRY` | 기본 **386건** (`B023` 11건 오탐 확인, `BLE001` 29건 등) / 확장 **10,721건** (`scripts/` 2,730건) | `compile.py` `B023`은 동기 루프 즉시 실행이라 런타임 버그 아님 확인 |
| `mypy` / `pyright` | `.venv\Scripts\mypy.exe scripts` / `.venv\Scripts\pyright.exe --pythonpath .venv\Scripts\python.exe scripts` | `mypy`: **12파일 90오류** / `pyright`: **88오류** (exit 1) | `types-PyYAML` 미설치·옵셔널 narrowing 오탐 위주; 핵심 의심(`status.py:280`, `doctor.py:408` 등) 직접 확인 |
| `bandit` / `vulture` | `.venv\Scripts\bandit.exe -r scripts` / `.venv\Scripts\vulture.exe scripts --min-confidence 80` | `bandit`: **Low 20, Medium 7, High 0** / `vulture`: **80% 0건** (60% 14건) | `fts.py` `B608`은 화이트리스트 튜플, `frontmatter.py` `B506`은 `BaseLoader`, `confluence_push.py:441` `B314`는 로컬 생성 XHTML 래퍼 검증 |
| Lua (`luacheck` / `selene` / `lua-ls`) | `luacheck nvim --globals vim` / `selene --config ...\selene.toml nvim` / `lua-language-server --check nvim` | `luacheck`: **9경고/0오류** / `selene`: **1오류/2경고** / `lua-ls`: **341건** (대부분 `.luarc.json` 부재에 따른 `undefined-global vim`) | `server.lua:184/188` `if_same_then_else`는 의도 분기, `proc.lua:515` 미사용 매개변수 `err` |
| PowerShell `PSScriptAnalyzer` | `Invoke-ScriptAnalyzer` (`install.ps1`, `uninstall.ps1`, `scripts/clip_image.ps1`) | `install.ps1` **9경고**, `uninstall.ps1` **5경고**, `clip_image.ps1` **0건** (Error 0) | 빈 `catch` 블록 및 `ShouldProcess` 관련 경고 |
| **미실행 검증 (사유)** | `-m live` / `nvim/capture.ahk` / 실 `schtasks` / 실 사용자 프로필 설치·제거 / 실 회사 Jira·Confluence·LLM·Neovide IME | **미실행** | 지침상 라이브 외부 서비스·실제 사용자 프로필·레지스트리 변경 금지, AutoHotkey v2 미설치, 사내망·GUI 환경 부재 |

---

## 분석 항목

### A-01
- **파일:줄**: `scripts/kb.py:116-124`, `scripts/serve.py:66-78`, `nvim/lua/work-kb/proc.lua:574-580`
- **분류**: 정확성
- **심각도**: 중간 *(정정: 높음→중간, 과잉 거부만 발생하며 쓰기를 허용하지 않아 복구 가능)*
- **문제**: `act list`·`act transitions`·`proposals list` 같은 2단어 하위 명령을 판정할 때 `argv[1]`(Lua는 `args[2]`) 고정 인덱스만 본다. `--root <경로>` 같은 옵션이 하위 명령 앞에 오면 `KB_AGENT=1`에서 읽기 허용 명령을 거부하고 `serve` 판정도 어긋난다.
- **근거·재현**:
  - 재현 스크립트: `.venv\Scripts\python.exe -X utf8 review/repro/A-01_kb_agent_flag_order.py`
  - 관찰 출력:
    - `CLI 'act --root <vault> list' exit=7 stderr="오류: KB_AGENT=1 세션에서는 'act'을(를) 할 수 없습니다"`
    - `SERVE 'proposals --root <vault> list' out='{"ready": true, ...}\n{"id": 1, "refused": true, "reason": "서버에서 실행하지 않는 하위 명령입니다: proposals --root"}'`
- **제안 수정**: 옵션 값(`--root` 등)을 건너뛰어 첫 번째 위치 인자(하위 명령)를 추출하는 공통 판정 함수를 두고 `kb.py`·`serve.py`·`proc.lua`가 같은 규칙으로 판정하게 바꾼다.
- **예상 수정 파일 목록**: `scripts/kb.py`, `scripts/serve.py`, `nvim/lua/work-kb/proc.lua`, `tests/test_serve.py`, `tests/nvim/spec/proc_spec.lua`

### A-02
- **파일:줄**: `nvim/lua/work-kb/init.lua:408-427`, `nvim/lua/work-kb/server.lua:253-263, 352-378, 412-432`, `nvim/lua/work-kb/health.lua:76-91`
- **분류**: 정확성
- **심각도**: 중간 *(정정: 높음→중간, `server.available(key)`가 다음 요청 시 재시작하므로 영구 불일치는 아니며 `setup()` 즉시 종료 누락·주석 불일치에 해당)*
- **문제**: `init.lua:422` 주석은 설정이 바뀌면 떠 있는 서버를 내린다고 적었지만, `init.lua:424`는 `not (cfg.server and cfg.server.enable)`일 때만 `server.stop()`을 부른다. `server.enable = true`인 채로 `root`·`vault`·`python`·`server.idle_sec`를 바꿔 `setup()`을 다시 불러도 기존 서버가 내려가지 않는다.
- **근거·재현**:
  - 재현 스크립트: `nvim --headless -u tests/nvim/minimal_init.lua -l review/repro/A-02_setup_server_restart.lua`
  - 관찰 출력: `stop_calls=0 after vault/idle_sec changed with server.enable=true` → `AssertionError`
- **제안 수정**: `setup()`에서 `server.status().key`와 새 설정 키를 비교해 다르면 `server.stop()`을 부르고 의도적 종료 시 `S.last_start` 재시작 제한을 초기화한다.
- **예상 수정 파일 목록**: `nvim/lua/work-kb/init.lua`, `nvim/lua/work-kb/server.lua`, `tests/nvim/spec/server_spec.lua`

### A-03
- **파일:줄**: `nvim/lua/work-kb/server.lua:316-328`, `scripts/serve.py:310-313, 327-330`
- **분류**: 정확성
- **심각도**: 중간
- **문제**: `server.lua:320`은 `code == 0 and S.state == "ready" and not S.inflight and #S.queue == 0`일 때만 정상 종료(`S.state = "stopped"`)로 본다. `kb.py serve`가 유휴 만료 등으로 `exit 0` 종료를 시작한 찰나에 요청이 `S.inflight`나 `S.queue`에 들어가면 `code == 0`인데도 `S.state = "dead"`가 되어 60초 재시작이 막힌다.
- **근거·재현**:
  - 재현 스크립트: `nvim --headless -u tests/nvim/minimal_init.lua -l review/repro/A-03_server_exit0_queue.lua`
  - 관찰 출력: `state=dead cb_code=nil can_restart=false last_error=서버가 끝났습니다 (종료 0)`
- **제안 수정**: `on_exit`에서 `code == 0 and S.state == "ready"`이면 대기 요청을 `fail_pending()`으로 단발 폴백시킨 뒤 `S.state = "stopped"`, `S.last_start = nil`로 둔다.
- **예상 수정 파일 목록**: `nvim/lua/work-kb/server.lua`, `tests/nvim/spec/server_spec.lua`

### A-04
- **파일:줄**: `nvim/lua/work-kb/jira.lua:186-210, 575-595`, `nvim/lua/work-kb/fileio.lua:444-448`
- **분류**: 정확성
- **심각도**: 중간
- **문제**:
  1. `M.promote()`(`jira.lua:579-588`)가 `line`을 먼저 읽고 `read_project()`·`confirm()`을 거친 뒤, 실행 시점에 `M.daily_line_ref()`를 다시 불러 확인한 줄과 다른 줄을 승격할 수 있다.
  2. 비동기 실행 중 사용자가 daily 버퍼를 수정하면 `fileio.refresh(bufnr)`(`fileio.lua:445`)가 `not vim.bo[bufnr].modified` 조건 때문에 ` [[NEW-1]]` 재읽기를 조용히 건너뛴다.
- **근거·재현**:
  - 재현 스크립트: `nvim --headless -u tests/nvim/minimal_init.lua -l review/repro/A-04_promote_cursor_shift.lua`
  - 관찰 출력: `confirm_msg=이 줄을 KB 일감으로 만들까요? - 09:00 첫째 줄 ` / `sent_args=... --from daily/2026-11-15.md:3 KB --yes` (2번 줄 확인 후 3번 줄 승격)
- **제안 수정**: `read_project()`·`confirm()` 전에 대상 `bufnr`와 1-based 줄 번호(`rel:lineno`)를 고정하고, `modified` 충돌 시 경고를 표시한다.
- **예상 수정 파일 목록**: `nvim/lua/work-kb/jira.lua`, `tests/nvim/spec/jira_spec.lua`

### A-05
- **파일:줄**: `nvim/lua/work-kb/init.lua:307-323`, `nvim/after/ftplugin/markdown.lua:27-52`, `nvim/lua/work-kb/config.lua:163-192`, `nvim/lua/work-kb/jira.lua:171-182`
- **분류**: 정확성
- **심각도**: 중간
- **문제**: `init.lua:in_vault`, `after/ftplugin/markdown.lua`, `jira.daily_relative`가 `vim.uv.fs_realpath`를 적용하지 않아 Windows 정션 경로 아래로 볼트 `.md` 파일을 열면 볼트 파일로 인식하지 못한다(`b:work_kb_vault`, `autoread`, `shiftwidth=2`, `promote` 누락).
- **근거·재현**:
  - 재현 스크립트: `nvim --headless -u tests/nvim/minimal_init.lua -l review/repro/A-05_junction_in_vault.lua`
  - 관찰 출력: `vault=.../real_vault ... buf_path=.../link_vault/daily/2026-11-15.md b:work_kb_vault=nil`
- **제안 수정**: `config.lua`에 `fs_realpath` 폴백 포함 볼트 경로 정규화 공용 헬퍼를 두고 `init.lua`·`after/ftplugin/markdown.lua`·`jira.lua`가 공유한다.
- **예상 수정 파일 목록**: `nvim/lua/work-kb/config.lua`, `nvim/lua/work-kb/init.lua`, `nvim/after/ftplugin/markdown.lua`, `nvim/lua/work-kb/jira.lua`, `tests/nvim/spec/ftplugin_spec.lua`

### A-06
- **파일:줄**: `scripts/serve.py:47, 307-325`, `scripts/lib/jira_client.py:323`, `nvim/lua/work-kb/server.lua:14`
- **분류**: 정확성
- **심각도**: 중간
- **문제**: `act transitions`가 `SERVED` 목록(`serve.py:47`)에 포함되어 단일 스레드 `serve()` 루프 안에서 동기 HTTP(`timeout` 30초)를 실행한다. Jira 응답이 지연되면 후속 `ping`/`status`/`search`까지 모두 블로킹되고, `server.lua` 30초 타임아웃에 걸려 서버가 강제 종료된다.
- **근거·재현**:
  - 재현 스크립트: `.venv\Scripts\python.exe -X utf8 review/repro/A-06_serve_sync_http_block.py`
  - 관찰 출력: `ping_latency=2.03s resp1={"id": 1, "code": 0, "out": "[]\n"} resp2={"id": 2, "ok": true}` → `AssertionError: serve ping blocked 2.03s behind slow act transitions HTTP call`
- **제안 수정**: 네트워크 I/O가 있는 `act transitions`는 `SERVED`에서 빼서 단발 비동기 프로세스로 돌리거나 `JiraClient` 타임아웃을 `request_timeout_ms`보다 충분히 짧게 둔다.
- **예상 수정 파일 목록**: `scripts/serve.py`, `nvim/lua/work-kb/proc.lua`, `tests/test_serve.py`, `tests/nvim/spec/proc_spec.lua`

### A-07
- **파일:줄**: `install.ps1:384-385, 425-451`, `tests/test_install_script.py:510-514, 557-562`
- **분류**: 정확성
- **심각도**: 중간
- **문제**: `install.ps1 -Offline`에서도 `uv venv --seed --python 3.14` / `3.13` (3.14 단일 대상으로 범위 밖)을 `--offline`·`--no-python-downloads` 없이 호출해 완전 오프라인 환경에서 시드/파이썬 내려받기를 시도할 위험이 있다(실제 폐쇄망 환경 실패는 미실행으로 **추정**).
- **근거·재현**:
  - 재현 스크립트(읽기 전용 AST 점검): `pwsh -NoProfile -File review/repro/A-07_install_offline_uv_venv.ps1`
  - 관찰 출력: `install.ps1:384: ... @('venv', '--seed', '--python', '3.14', $venv)` / `has_offline_or_no_python_downloads_in_install_ps1=False`
- **제안 수정**: `-Offline` 분기에서 `'--offline'`, `'--no-python-downloads'`를 추가하고 `-WhatIf` 안내 문구도 오프라인 설치 명령으로 맞춘다.
- **예상 수정 파일 목록**: `install.ps1`, `tests/test_install_script.py`

### A-08
- **파일:줄**: `nvim/capture.ahk:89-101, 109, 124-130`, `install.ps1:578`
- **분류**: 정확성
- **심각도**: 중간
- **문제**:
  1. `RpcCall`(`capture.ahk:92-96`)이 `cmd.exe /d /s /c ...`로 `nvim.exe`를 띄우고 타임아웃 시 `ProcessClose(pid)`로 `cmd.exe`만 종료해 자식 `nvim.exe`가 고아로 남을 수 있다(**추정** — AHK v2 런타임 미실행).
  2. `Capture()`(`capture.ahk:109`)는 `nvd.cmd`만 찾는 반면 `install.ps1:578`은 `Get-Command nvd`로 `.ps1`도 탐지한다.
- **근거·재현**: `nvim/capture.ahk:92-100, 109` 코드 확인 및 `install.ps1 -WhatIf`에서 `nvd: D:\work\nvim-config\bin\nvd.ps1` 탐지 확인.
- **제안 수정**: 프로세스 트리 종료(`taskkill /T /F`) 또는 직접 실행을 쓰고 `nvd.cmd`·`nvd.exe`·`nvd.ps1` 탐지 규칙을 맞춘다.
- **예상 수정 파일 목록**: `nvim/capture.ahk`

### A-09
- **파일:줄**: `nvim/lua/work-kb/health.lua:11-14`, `install.ps1:24, 75`, `scripts/doctor.py:1129`, `docs/DEPLOY.md:14`, `README.md:19`, `docs/USER-GUIDE.md:98-99, 170-175, 219`, `nvim/lua/work-kb/search.lua:200-209`, `tests/nvim/minimal_init.lua:45-47`
- **분류**: 정확성
- **심각도**: 중간 *(정정: 낮음→중간, 문서·요구사항 불일치 기준 적용)*
- **문제**:
  1. 최소 Neovim 버전: `health.lua:11-14`·`docs/TROUBLESHOOTING.md:87`은 `0.12` 미만을 오류로 보지만 `install.ps1:75`·`scripts/doctor.py:1129`·`docs/DEPLOY.md:14`·`README.md:19`는 `0.11` 이상으로 적혀 있다.
  2. `<leader>ks` 검색: `USER-GUIDE.md:219`는 공백 AND 검색이라 설명하나 `search.lua:201-209`는 `fzf-lua`(정규식)·`rg --fixed-strings`(고정 문자열) 우선이고 공백 AND는 `kb.py search` 폴백에만 적용된다.
  3. `USER-GUIDE.md:171-175`: 수동 `rtp`에 `"<repo>/nvim/after"` 추가를 안내하나 Neovim이 `nvim` 아래 `after/`를 자동 로드하므로 불필요하다.
- **근거·재현**: 각 파일 해당 줄 직접 대조 확인.
- **제안 수정**: 최소 버전 기준(`0.11` vs `0.12`)을 코드·설치 스크립트·문서 전체에서 통일하고 `USER-GUIDE.md` 검색·`rtp` 안내를 바로잡는다.
- **예상 수정 파일 목록**: `docs/USER-GUIDE.md`, `README.md`, `docs/DEPLOY.md`, `install.ps1`, `scripts/doctor.py`, `nvim/lua/work-kb/health.lua`, `tests/nvim/minimal_init.lua`

### A-10
- **파일:줄**: `tests/nvim/spec/server_spec.lua`, `tests/nvim/spec/proc_spec.lua`, `tests/test_serve.py`, `tests/test_install_script.py`
- **분류**: 개선
- **심각도**: 낮음 *(정정: 중간→낮음, 테스트 품질 기준 적용)*
- **문제**: `A-01`~`A-03`, `A-07` 경계 조건(`act --root ... list`, `setup()` 설정 변경 재시작, `on_exit(0)` 중 대기 요청, `-Offline` `uv venv` 플래그)에 대한 회귀 테스트가 없다.
- **근거·재현**: `tests/test_serve.py:279-281`, `tests/test_install_script.py:510-564`, `tests/nvim/spec/server_spec.lua`, `tests/nvim/spec/proc_spec.lua` 점검 결과.
- **제안 수정**: 해당 수정과 함께 경계 회귀 테스트를 추가한다.
- **예상 수정 파일 목록**: `tests/nvim/spec/server_spec.lua`, `tests/nvim/spec/proc_spec.lua`, `tests/test_serve.py`, `tests/test_install_script.py`

### A-11
- **파일:줄**: `nvim/lua/work-kb/init.lua:307-323`, `nvim/after/ftplugin/markdown.lua:27-52`, `nvim/lua/work-kb/jira.lua:171-182`, `nvim/lua/work-kb/capture.lua:255-262`, `scripts/kb.py:120`, `scripts/serve.py:75`, `nvim/lua/work-kb/proc.lua:574-580`
- **분류**: 개선
- **심각도**: 낮음
- **문제**: 볼트 루트 경로 판정이 Lua 4곳(`capture.lua:258`·`jira.lua:177`의 끝 `/` 중복 포함)에, 2단어 하위 명령 판정이 `kb.py`·`serve.py`·`proc.lua` 3곳에 중복 구현되어 있다.
- **근거·재현**: 각 파일 해당 줄 코드 구조 대조.
- **제안 수정**: 볼트 경로 판정은 `config.lua`로, Python 하위 명령 판정은 공용 헬퍼로 모은다.
- **예상 수정 파일 목록**: `nvim/lua/work-kb/config.lua`, `nvim/lua/work-kb/init.lua`, `nvim/after/ftplugin/markdown.lua`, `nvim/lua/work-kb/jira.lua`, `nvim/lua/work-kb/capture.lua`, `scripts/kb.py`, `scripts/serve.py`

### A-12
- **파일:줄**: `scripts/link.py:41, 250-260, 304-312`, `scripts/compile.py:80, 1168, 1179, 1185-1186` (`work-kb-build-brief-v3.md:149`, `docs/DECISIONS.md:378` D22)
- **분류**: 정확성
- **심각도**: 중간 *(정정: 높음→중간, 데이터 손상·핵심 흐름 중단이 아닌 잘못된 결과·D22 요구사항 불일치)*
- **문제**: 스펙과 D22는 `link`가 `wiki/topics/*.md`의 명시적 링크와 2자 이상 주제명/별칭을 매칭해 `mentions` 구역을 갱신한다고 정했지만, `link.py:41` `MANAGED_DIRS`와 `_extract`/`_targets`가 `jira`·`projects`·`people`만 처리하고 `topics`를 완전히 누락한다. 그 결과 `compile.py`의 `entity_summary`(T3)가 승인된 주제의 `mentions`를 읽지 못해 주제 요약도 비어 있게 된다.
- **근거·재현**:
  - 재현 스크립트: `.venv\Scripts\python.exe -X utf8 review/repro/A-12_link_topics_mentions.py`
  - 관찰 출력: `link exit: 0 out: link: 원천 8개...` 뒤에도 `wiki/topics/캐시.md`의 `<!-- kb:mentions:start -->\n<!-- kb:mentions:end -->`가 비어 있음 → `AssertionError`
- **제안 수정**: `link.py` `MANAGED_DIRS`에 `("wiki", "topics")`를 추가하고 `wiki/topics/*.md`의 제목·`aliases`(2자 이상)·`[[topics/...]]` 링크를 추출해 `mentions` 구역을 갱신한다.
- **예상 수정 파일 목록**: `scripts/link.py`, `tests/test_link.py`

### A-13
- **파일:줄**: `scripts/jira_act.py:485-487, 529, 535-536` (`docs/DECISIONS.md:189` D52)
- **분류**: 정확성
- **심각도**: 높음
- **문제**: D52는 `uncertain` 항목이 몇 번을 재시도해도 `failed`가 되어서는 안 된다고 정한다(`--retry-failed`를 통한 중복 쓰기 방지). 그러나 `attempts >= 5`인 `uncertain` 항목을 `--retry-uncertain`으로 재시도하면 `_claim`이 `attempts=6`으로 올리고, 이때 429 `RateLimited`(또는 5xx/네트워크 오류)가 나면 `_keep(..., attempts - 1, str(exc), ...)`에 전달된 `error != outbox.UNCERTAIN`이라 `jira_act.py:485`가 참이 되어 레코드가 `failed`로 덮어써진다.
- **근거·재현**:
  - 재현 스크립트: `.venv\Scripts\python.exe -X utf8 review/repro/A-13_act_uncertain_becomes_failed.py`
  - 관찰 출력: `attempts: 5, last_error: "uncertain"` 레코드가 429 응답 후 `"status": "failed", "last_error": "5번 시도했지만..."`으로 전락
- **제안 수정**: `deliver` / `_keep`에서 원래 레코드가 이미 `uncertain`(`record.get("last_error") == outbox.UNCERTAIN`)이었을 때는 429/일시 오류에도 `last_error = outbox.UNCERTAIN`을 보존해 `failed`로 바꾸지 않는다.
- **예상 수정 파일 목록**: `scripts/jira_act.py`, `tests/test_jira_act.py`

### A-14
- **파일:줄**: `scripts/wheels.py:222-281` (대조: `scripts/kb.py:96`, `scripts/init.py:1385`, `scripts/jira_act.py:979`, `scripts/compile.py:1799`)
- **분류**: 정확성
- **심각도**: 중간 *(정정: 높음→중간, `kb.py wheels`는 이미 종료 7로 거부되며 직접 모듈 실행만 D57 요구사항 불일치)*
- **문제**: 다른 모든 `KB_AGENT=1` 거부 명령(`init`, `jira_act`, `compile`, `confluence_push`, `demo`, `schedule`, `doctor`)은 모듈 `main()` 첫머리에서도 `core.refuse_if_agent(...)`를 호출하지만, `wheels.py`만 누락되어 `wheels.main` 직접 실행 시 `KB_AGENT=1` 거부를 우회한다.
- **근거·재현**:
  - 재현 스크립트: `.venv\Scripts\python.exe -X utf8 review/repro/A-14_wheels_direct_kb_agent.py`
  - 관찰 출력: `wheels.main(['--dry-run']) under KB_AGENT=1 returned 0 instead of raising PolicyRefused`
- **제안 수정**: `wheels.main` 시작 부분에 `core.refuse_if_agent("wheels")`를 추가하고, 필요하면 `if __name__ == "__main__": core.run_main(main)`도 둔다.
- **예상 수정 파일 목록**: `scripts/wheels.py`, `tests/test_packaging.py`

### A-15
- **파일:줄**: `scripts/lib/net.py:65-70`, `scripts/lib/jira_client.py:347`, `scripts/lib/confluence_client.py:143` (대조: `scripts/lib/llm_client.py:471-475`, `scripts/doctor.py:141-143`, `scripts/init.py:312-314`)
- **분류**: 정확성
- **심각도**: 중간
- **문제**: `LlmClient._conn`·`doctor.py`·`init.py`는 상대 경로 `ca_bundle`을 `cfg.config_dir` 기준으로 해석하는 반면, `JiraClient.from_config`·`ConfluenceClient.from_config`는 상대 경로를 변환하지 않고 `net.session_for`에 넘겨 프로세스 CWD 기준으로 찾는다. 볼트 밖 폴더에서 `--root <볼트>`로 실행하면 `doctor`는 통과하는데 `sync`·`act`·`push`는 실패한다.
- **근거·재현**:
  - 재현 스크립트: `.venv\Scripts\python.exe -X utf8 review/repro/A-15_relative_ca_bundle_cwd.py`
  - 관찰 출력: `sync exit: 4 stderr: 오류: ca_bundle 파일이 없습니다: certs\corp.pem. 회사 루트 인증서(PEM) 경로를 확인하세요`
- **제안 수정**: `JiraClient.from_config`와 `ConfluenceClient.from_config`(또는 `net.session_for` 호출 전 공용 `_resolve_conn`)에서 상대 경로 `ca_bundle`을 `cfg.config_dir` 기준으로 정규화한다.
- **예상 수정 파일 목록**: `scripts/lib/jira_client.py`, `scripts/lib/confluence_client.py`, `scripts/lib/net.py`, `tests/test_jira_client.py`, `tests/test_confluence_push.py`

### A-16
- **파일:줄**: `scripts/ingest_images.py:196-197, 221-228, 392-431, 452`
- **분류**: 정확성
- **심각도**: 중간
- **문제**:
  1. `sidecar_path`(`196-197`줄)가 확장자를 버리고 `inbox/<stem>.md`를 써서 `shot.png`와 `shot.jpg`가 함께 있으면 첫 파일 처리 후 `collect()`(`227`줄)가 두 번째 파일을 영구 건너뛴다.
  2. `clean`/`render_extracted`/`render_sidecar`가 OCR `text`/`summary` 안의 `<!--`·`-->`를 이스케이프하지 않아 관리 구역 마커 문자열이 그대로 삽입된다.
- **근거·재현**:
  - 재현 스크립트: `.venv\Scripts\python.exe -X utf8 review/repro/A-16_ingest_stem_and_marker.py`
  - 관찰 출력: `raw/assets/shot.jpg`가 `collect(cfg)`에서 누락되고(`shot.md` 충돌), `raw <!-- present in extracted: True` 확인
- **제안 수정**: 충돌 시 확장자를 포함한 이름 폴백을 두고, `link.py`/`lint.py`처럼 추출 문자열의 `<!--`를 `&lt;!--` 등으로 중화한다.
- **예상 수정 파일 목록**: `scripts/ingest_images.py`, `tests/test_ingest_images.py`

### A-17
- **파일:줄**: `scripts/jira_act.py:760-761`
- **분류**: 정확성
- **심각도**: 중간
- **문제**: `cmd_promote`가 이미 승격된 줄인지 검사할 때 `cfg.project_keys` 전체가 아니라 대상 `project` 하나(`rf"\[\[{re.escape(project)}-[1-9][0-9]*\]\]\s*$"`)만 확인한다. 이미 `[[OPS-1]]`로 승격된 줄을 `KB`로 다시 승격하면 일감이 중복 생성되고 `[[OPS-1]] [[KB-16]]`이 붙는다.
- **근거·재현**:
  - 재현 스크립트: `.venv\Scripts\python.exe -X utf8 review/repro/A-17_promote_cross_project_dup.py`
  - 관찰 출력: `promote exit: 0`, `daily line after: - 09:00 결제 오류 확인 [[OPS-1]] [[KB-16]]`
- **제안 수정**: `project_keys` 전체(또는 일반 Jira 키 패턴)로 끝 `[[KEY-N]]` 여부를 검사해 이미 승격된 줄은 거부한다.
- **예상 수정 파일 목록**: `scripts/jira_act.py`, `tests/test_jira_act.py`

### A-18
- **파일:줄**: `scripts/lib/notes.py:218-240`, `scripts/daily_prepare.py:521` (`docs/DECISIONS.md:113, 376` D20)
- **분류**: 정확성
- **심각도**: 중간
- **문제**: D20은 비-키 위키링크(`[[KB-12-설계]]`)와 이미지 임베드(`![[raw/assets/KB-12.png]]`) 안의 키를 매칭하지 않도록 정했으나, `notes.find_keys`는 바로 앞/뒤 토큰만 확인해 `KeyMatch(..., bracketed=False)`를 반환한다. `link.py`는 자체 `_wikilink_spans`로 우회하지만 `daily_prepare.py:521`은 `notes.find_keys`를 직접 불러 개인 할 일 이월에서 해당 줄을 잘못 제외한다.
- **근거·재현**:
  - 재현 스크립트: `.venv\Scripts\python.exe -X utf8 review/repro/A-18_notes_find_keys_embed.py`
  - 관찰 출력: `notes.find_keys: [KeyMatch(..., key='KB-12', bracketed=False)]` 및 `kb.py today` 결과 `- [ ] 개인: ![[raw/assets/KB-12-whiteboard.png]] 정리`만 `## 이월`에서 누락됨
- **제안 수정**: `notes.find_keys`가 `_masked` 또는 위키링크/임베드 스팬 마스킹으로 D20 규칙을 직접 준수하게 수정한다.
- **예상 수정 파일 목록**: `scripts/lib/notes.py`, `tests/test_notes.py`, `tests/test_daily_prepare.py`

### A-19
- **파일:줄**: `scripts/confluence_push.py:288-302, 341-343`
- **분류**: 정확성
- **심각도**: 중간
- **문제**: `prepare()`의 `341`줄이 `_COMMENT.sub("", text)`를 코드 블록 보호 전에 전체 본문에 실행한다. `_unclosed_comment`(`288-302`줄)는 펜스 코드 블록·인라인 코드를 건너뛰지만, 이미 직전에서 코드 예시 안의 `<!-- ... -->`가 모두 지워진다.
- **근거·재현**:
  - 재현 스크립트: `.venv\Scripts\python.exe -X utf8 review/repro/A-19_confluence_strips_code_comments.py`
  - 관찰 출력: 펜스 코드 안 `<!-- example marker -->`와 인라인 코드 `` `<!-- keep -->` `` 모두 공백으로 제거됨
- **제안 수정**: 코드 블록과 인라인 백틱을 먼저 보호한 뒤 그 밖의 HTML 주석만 제거한다.
- **예상 수정 파일 목록**: `scripts/confluence_push.py`, `tests/test_confluence_push.py`

### A-20
- **파일:줄**: `scripts/lib/wikimarkup.py:60-86, 131-141, 182-184, 210`
- **분류**: 정확성
- **심각도**: 중간
- **문제**:
  1. `_protect`가 `{{mono}}`·`!img!`를 `\x00N\x00`로 바꾼 뒤 `[label|url]` 링크를 `\x00M\x00`로 감싸 중첩 자리표가 생기지만 `_restore`는 한 번만 치환해 안쪽 `\x00N\x00` NUL 바이트가 그대로 남는다.
  2. `{quote}` 블록 안에 여러 줄 `{code}`가 오면 첫 줄만 `> ` 접두어가 붙고 둘째 줄부터 인용문 밖으로 빠진다.
- **근거·재현**:
  - 재현 스크립트: `.venv\Scripts\python.exe -X utf8 review/repro/A-20_wikimarkup_placeholder_leak.py`
  - 관찰 출력: `nested: '[see \x000\x00](https://example.com)'` 및 `quoted_code:\n> ```\nline1\nline2\n``` `
- **제안 수정**: `_restore`를 자리표가 없어질 때까지 반복(또는 깊이 역순 치환)하고 `{quote}` 확장 뒤에도 `{code}` 각 줄 앞에 `> `를 붙인다.
- **예상 수정 파일 목록**: `scripts/lib/wikimarkup.py`, `tests/test_wikimarkup.py`

### A-21
- **파일:줄**: `scripts/lib/fts.py:222-283` (`docs/DECISIONS.md:149`)
- **분류**: 정확성
- **심각도**: 중간
- **문제**: `Cache.__init__`·`upsert`·`delete`와 달리 `count`·`get`·`search_fts`·`search_like`·`search` 읽기 메서드는 SQLite `locked`/`busy` 오류를 `core.FileBusy`(종료 6)로 바꾸지 않는다. 특히 `search()`(`276-279`줄)는 `search_fts`의 `OperationalError`를 종류 무관하게 삼키고 `search_like`로 내려가 결국 미처리 `sqlite3.OperationalError`(종료 1 트레이스백)를 낸다.
- **근거·재현**:
  - 재현 스크립트: `.venv\Scripts\python.exe -X utf8 review/repro/A-21_fts_read_locked.py`
  - 관찰 출력: `Leaked raw OperationalError instead of FileBusy: database is locked` (`fts.py:264`에서 `sqlite3.OperationalError`)
- **제안 수정**: FTS 읽기 메서드에도 쓰기와 동일한 `_is_busy(exc)` → `core.FileBusy` 변환을 적용하고, `search()`는 FTS 부재 오류와 잠금 오류를 구분한다.
- **예상 수정 파일 목록**: `scripts/lib/fts.py`, `tests/test_fts.py`

### A-22
- **파일:줄**: `scripts/lib/config.py:433-444, 506-510`
- **분류**: 정확성
- **심각도**: 중간
- **문제**: `config.local.yaml`에 `jira:`(null)처럼 비-dict 섹션이 들어간 상태에서 `KB_JIRA_MAX_RESULTS=50` 같은 환경변수 덮어쓰기가 있으면, `_validate(data)`(`510`줄) 실행 전에 `_apply_overrides`(`506`줄)가 `section.get(sub)`를 불러 `ConfigError`(종료 4) 대신 미처리 `AttributeError`(종료 1)로 죽는다.
- **근거·재현**:
  - 재현 스크립트: `.venv\Scripts\python.exe -X utf8 review/repro/A-22_config_null_section_env_crash.py`
  - 관찰 출력: `AttributeError: 'NoneType' object has no attribute 'get'` (`scripts/lib/config.py:438`)
- **제안 수정**: `_apply_overrides` 전에 섹션 dict 여부를 확인하거나 `isinstance(section, Mapping)` 검사를 둔다.
- **예상 수정 파일 목록**: `scripts/lib/config.py`, `tests/test_config.py`

### A-23
- **파일:줄**: `scripts/ingest_images.py:545, 643-662, 775-777`, `scripts/lint.py:1075-1087`, `scripts/lib/config.py:560-575`, `scripts/init.py:1124-1133`, `scripts/doctor.py:815`, `scripts/link.py:728-737, 861-865`, `scripts/daily_prepare.py:572-578, 625-636`, `scripts/lib/core.py:338-340`
- **분류**: 정확성
- **심각도**: 중간
- **문제**: 여러 쓰기 경로에 잠금·동시성 보호가 빠져 있다.
  1. `ingest_images._Run`이 `kb_lock` 밖(`545`줄)에서 `self.ledger`를 한 번만 읽고 `_skip`/`_write` 시 잠금 안에서 디스크 원장을 다시 읽지 않아 동시 실행 갱신을 덮어쓴다.
  2. `lint._write`(`1075-1087`줄)와 `config.save_local`(`560-575`줄, `doctor --redetect` → `init.save_detected`)가 `kb_lock` 없이 쓴다.
  3. `link._sources_unchanged`는 계획 당시 있던 파일의 `stat`만 확인해 중간에 새로 생긴 `daily`/`inbox` 파일을 감지하지 못하고, `daily_prepare`는 `atomic_write` 시 `FileBusy`를 잡지 않으며, `core.atomic_write`는 Windows CRT `errno.EACCES`(`winerror is None`) 재시도가 빠져 있다.
- **근거·재현**:
  - 재현 스크립트: `.venv\Scripts\python.exe -X utf8 review/repro/A-23_ingest_ledger_overwrite.py`
  - 관찰 출력: `ledger keys after _skip: []` (`concurrent-sha` 항목 소실 확인), 나머지는 해당 줄 직접 대조 확인.
- **제안 수정**: `ingest_images`는 `kb_lock` 안에서 원장을 다시 읽어 병합·저장하고, `lint`/`save_local`에도 `kb_lock`을 적용하며 `daily_prepare`와 `core.py` 오류 처리를 보강한다.
- **예상 수정 파일 목록**: `scripts/ingest_images.py`, `scripts/lint.py`, `scripts/lib/config.py`, `scripts/link.py`, `scripts/daily_prepare.py`, `scripts/lib/core.py`

### A-24
- **파일:줄**: `scripts/lib/llm_detect.py:474-479` (`docs/DECISIONS.md:250`), `scripts/lint.py:852-858`
- **분류**: 정확성
- **심각도**: 낮음
- **문제**:
  1. `llm_detect.detect_reasoning`의 기준선 요청(`475`줄)만 `_post_settled`(2초 뒤 1회 재시도) 대신 `_post`를 직접 써서 단발 500/429에도 재시도 없이 `DetectError`로 실패한다.
  2. `lint._fit`(`852-858`줄)은 fallback 모델의 토큰 예산이 첫 2항목보다 작아도 `group.items[:2]`를 그대로 반환해 작은 컨텍스트 fallback에서 400으로 묶음을 건너뛸 수 있다(코드 대조 **추정**).
- **근거·재현**:
  - 재현 스크립트: `.venv\Scripts\python.exe -X utf8 review/repro/A-24_llm_detect_baseline_no_retry.py`
  - 관찰 출력: `http_500_once=True` 모의 서버에서 `chat calls=1` 뒤 즉시 `DetectError` 발생 → `AssertionError: saw chat calls=1`
- **제안 수정**: 기준선 호출도 `_post_settled`로 바꾸고, `lint._fit`이 최소 2개 항목이 예산을 넘기면 건너뛰거나 잘라낸다.
- **예상 수정 파일 목록**: `scripts/lib/llm_detect.py`, `scripts/lint.py`, `tests/test_llm_detect.py`, `tests/test_lint.py`

### A-25
- **파일:줄**: `scripts/init.py:1388`, `docs/README.md:83, 91`, `docs/DECISIONS.md:3, 9-43, 277-280, 429, 435-436`, `scripts/compile.py:73, 648`, `scripts/schedule.py:67-78`, `AGENTS.md:41-47`, `prompts/image_ingest.md:7-11`, `docs/DEPLOY.md:28-39`
- **분류**: 정확성
- **심각도**: 중간 *(정정: 낮음→중간, 문서·요구사항 불일치 기준 적용; `DECISIONS.md` ch.6 우선순위 누락 및 `KB_SKIP_EMACS` 잔재 보완)*
- **문제**: 구현·스펙·문서 불일치 묶음이다.
  1. `scripts/init.py:1388`의 `--yes` 옵션(`docs/README.md:83` 기재)이 `init.py` 전체에서 한 번도 참조되지 않는 dead flag다.
  2. `scripts/compile.py:648`은 `inbox/`의 이미지 사이드카만 읽고 일반 `inbox/` 노트는 편찬하지 않으며(`73`줄 `DEFAULT_SINCE_DAYS=7`), `docs/README.md:91`에 7일 기본 범위가 빠져 있다.
  3. `docs/DECISIONS.md`에 Emacs 키/ERT 잔재(`11-18`, `277-280`, `D64`, `D65`)와 `D58` 계층형 이름/5개 작업 목록(`429`줄 vs `schedule.py` 평면 6개 `work-kb-ingest` 포함)이 남아 있다.
  4. `AGENTS.md:41-47` 구역 소유 표에 `proposals`·`lint`가 빠져 있고, `prompts/image_ingest.md:7-11`은 코드 블록 금지를 적고 바로 아래에 ```` ```json ```` 블록을 넣었으며, `docs/DEPLOY.md:32-39` 제외 범위 표현이 `scripts/deploy_check.py`·`docs/PACKAGING.md`보다 좁다.
- **근거·재현**:
  - 재현 스크립트(`--yes`): `.venv\Scripts\python.exe -X utf8 review/repro/A-25_init_yes_dead_flag.py`
  - 관찰 출력: `AST references to '.yes' in scripts/init.py: []`, `kb.py init --yes` 빈 stdin 실행 시 여전히 대화형 프롬프트를 묻고 `exit=1` 실패. 나머지는 해당 파일·줄 직접 대조.
- **제안 수정**: `init --yes`를 구현하거나 도움말·문서에서 제거하고, `compile`의 `inbox` 정책·`DECISIONS.md`·`AGENTS.md`·`prompts/image_ingest.md`·`DEPLOY.md`를 실제 코드 기준으로 맞춘다.
- **예상 수정 파일 목록**: `scripts/init.py`, `docs/README.md`, `docs/DECISIONS.md`, `AGENTS.md`, `prompts/image_ingest.md`, `docs/DEPLOY.md`, `scripts/compile.py`

### A-26
- **파일:줄**: `pytest.ini:3`, `scripts/doctor.py:1120-1154`, `tests/test_doctor.py`, `tests/test_schedule.py`, `tests/test_clip_image.py`, `tests/test_install_script.py`, `tests/test_init_full.py`, `tests/test_docs.py`
- **분류**: 개선
- **심각도**: 낮음 *(정정: 중간→낮음, 테스트 품질 기준 적용)*
- **문제**: 저장소 전역 회귀 테스트 공백이 있다.
  - `doctor.py`의 Neovim(`< 0.11`)·Neovide·Obsidian 점검(`1120-1154`줄)이 커버리지 측정(`76%`)에서 미실행으로 확인됐다.
  - `init --yes` 테스트가 없어 dead flag가 검출되지 않았고, `test_docs.py`는 버전 모순(`0.11`/`0.12`)·`DECISIONS.md`·`AGENTS.md`·`prompts/*.md` 일관성을 검사하지 않는다.
  - `pytest.ini:3` 제외 마커 4종(`live`·`perf`·`demo`·`packaging`)과 Windows 실환경 경로(`schtasks`, 실제 클립보드, `capture.ahk`, 실 user env)는 수동/조건부 실행에 의존한다.
- **근거·재현**: `pytest --cov=scripts --cov-branch` 결과(`doctor.py` lines 1131-1154 uncovered) 및 각 테스트 파일 대조.
- **제안 수정**: `doctor.py` 도구 점검 단위 테스트, `init` CLI 옵션 테스트, `test_docs.py` 문서·프롬프트 일관성 검사를 추가한다.
- **예상 수정 파일 목록**: `tests/test_doctor.py`, `tests/test_init_full.py`, `tests/test_docs.py`

### A-27
- **파일:줄**: `scripts/lib/notes.py:147-173, 365, 371, 408, 418-429`, `scripts/status.py:211-226`, `scripts/lint.py:611-623`, `scripts/lib/core.py:320, 406-413`
- **분류**: 개선
- **심각도**: 낮음
- **문제**: 소규모 경계·정리 개선점 4건이다.
  1. `notes.page_template`(`365, 371`줄)가 `link_safe_name` 대신 `safe_name`을 써서 직접 호출 시 `#` 같은 위키링크 금지 문자가 파일명에 들어갈 수 있다.
  2. `notes.section_span`(`147-173`줄)이 코드 펜스 안 `## `를 제목으로 끊고 *(정정: `C-21` 재현으로 개인 할 일 이월 누락(`중간`) 영향이 확인됨)*, `parse_table`(`408`줄)이 `strip("|")`로 빈 끝 열 `||`을 잘라내며, `read_view`(`424`줄)가 `UnicodeDecodeError`를 잡지 않는다.
  3. `status.py`·`lint.py`의 `gave_up` 이미지 카운트가 `.state/images.json`에서 원본 이미지 삭제 여부와 비전 프로필 변경을 확인하지 않아 유령 경고가 남을 수 있다.
  4. `core.py` `_HELD`와 `atomic_write` 임시 파일명이 단일 스레드 실행을 전제한다(현재 `kb.py`/`serve.py` 직렬 실행에서는 안전).
- **근거·재현**: 각 파일 해당 줄 코드 직접 확인.
- **제안 수정**: `link_safe_name` 통일, `section_span` 펜스 추적 및 `parse_table` 끝 파이프 1개만 제거, `status`/`lint`의 `gave_up` 실제 파일·프로필 대조를 반영한다.
- **예상 수정 파일 목록**: `scripts/lib/notes.py`, `scripts/status.py`, `scripts/lint.py`, `tests/test_notes.py`, `tests/test_status.py`
