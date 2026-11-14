# work-kb `nvim` 브랜치 심층 분석 보고서 (agy)

## 요약

`nvim` 브랜치(`git diff main...HEAD`, merge-base `e8995b7`, 79개 파일)를 `work-kb-build-brief-v3.md`·`docs/DECISIONS.md`·`README.md`·`docs/USER-GUIDE.md` 기준으로 점검했다.  
전체 테스트는 2,162개 통과(10개 건너뜀, 14개 선택 제외)로 핵심 흐름은 안정적이나, 하위 명령 인자 파싱·상주 서버 수명주기·정션 경로 정규화·비동기 `promote` 경합에 버그가 있다.  
`install.ps1 -Offline`의 `uv venv` 플래그 누락, 미검증 `capture.ahk`의 프로세스 정리 누락, 문서와 실제 동작(`0.11`/`0.12`, 검색 방식, `rtp`) 불일치도 확인했다.  
정확성 9건(높음 2, 중간 6, 낮음 1)과 개선 2건(중간 1, 낮음 1), 총 11건을 보고한다.

## 테스트 결과

- 전체 suite (`uv run --no-project --python 3.14 --with pytest --with-requirements requirements.txt python -m pytest -q`): **2162 passed, 10 skipped, 14 deselected in 545.07s (0:09:05)** (`pytest.ini`의 `addopts = -m "not live and not perf and not demo and not packaging"` 적용).
- Neovim 통합 (`tests/test_nvim.py`, 동일 Python 3.14 환경 `pytest -s`): **2 passed in 44.84s**
  - `inherited-console`: `console code page=cp65001 (pytest 콘솔 65001); Ran 166 tests, 166 passed, 0 failed, 0 skipped; exit 0 after 24.4s`
  - `create-no-window`: `console code page=cp949 (pytest 콘솔 65001); Ran 166 tests, 166 passed, 0 failed, 0 skipped; exit 0 after 19.5s`
- 상주 서버 단독 (`tests/test_serve.py`, 동일 Python 3.14 환경 `pytest -q`): **17 passed in 37.58s**
- `install.ps1 -WhatIf` (`powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1 -WhatIf`): 정상 출력 및 `nvd: D:\work\nvim-config\bin\nvd.ps1` 탐지 확인.
- 단독 헤드리스 Lua 실행 (`nvim --headless -u tests/nvim/minimal_init.lua -l tests/nvim/run.lua`, `.venv` 미설치 워크트리): **Ran 165 tests, 135 passed, 29 failed, 1 skipped** (워크트리에 `.venv\Scripts\python.exe`가 없어 실제 Python을 띄우는 spec들이 `ENOENT`로 실패, `test_nvim.py` 경유 시 전수 통과).
- 미실행: `live`/`perf`/`demo`/`packaging` 마커 테스트(14건, `pytest.ini` 기본 제외) 및 `nvim/capture.ahk`(AutoHotkey v2 미설치로 실행 불가).

---

## 분석 항목

### A-01
- **파일:줄**: `scripts/kb.py:116-124`, `scripts/serve.py:66-78`, `nvim/lua/work-kb/proc.lua:574-580`
- **분류**: 정확성
- **심각도**: 높음
- **문제**: `act list`·`act transitions`·`proposals list` 같은 2단어 하위 명령을 판정할 때 `argv[0]`·`argv[1]`(Lua는 `args[1]`·`args[2]`) 고정 인덱스만 본다. `--root <경로>` 같은 옵션이 하위 명령 앞에 오면 `KB_AGENT=1`에서 허용 명령을 거부하거나 `serve` 판정이 어긋난다.
- **근거·재현**:
  - `KB_AGENT=1` 상태에서 `kb.main(['proposals', '--root', '.', 'list'])` 실행 시 `scripts/kb.py:120-122`가 `argv[1] == '--root'`만 보고 `PolicyRefused: KB_AGENT=1 세션에서는 'proposals'을(를) 할 수 없습니다`로 거부함을 재현 확인했다.
  - `serve.refusal_reason(['act', '--root', '.', 'list'])` 호출 시 `scripts/serve.py:75`가 `서버에서 실행하지 않는 하위 명령입니다: act --root`를 반환함을 확인했다. (`jira_act.py` 등 내부 쓰기 함수가 `refuse_if_agent`를 다시 검사하므로 쓰기 우회는 되지 않지만 읽기 경로가 깨지고 단발 실행으로 떨어진다.)
- **제안 수정**: 옵션 값(`--root` 등)을 건너뛰어 첫 번째 위치 인자(하위 명령)를 추출하는 공통 판정 함수를 두고 `kb.py`·`serve.py`·`proc.lua`가 같은 규칙으로 판정하게 바꾼다.
- **예상 수정 파일 목록**: `scripts/kb.py`, `scripts/serve.py`, `nvim/lua/work-kb/proc.lua`, `tests/test_serve.py`, `tests/nvim/spec/proc_spec.lua`

### A-02
- **파일:줄**: `nvim/lua/work-kb/init.lua:408-427`, `nvim/lua/work-kb/server.lua:253-263, 352-378, 412-432`, `nvim/lua/work-kb/health.lua:76-91`
- **분류**: 정확성
- **심각도**: 높음
- **문제**: `init.lua:422` 주석은 "설정이 바뀌었으면 떠 있는 서버를 내린다 (다음 요청이 새 설정으로 다시 띄운다)"고 적었지만, `init.lua:424`는 `not (cfg.server and cfg.server.enable)`일 때만 `server.stop()`을 부른다. `server.enable = true`인 채로 `root`·`vault`·`python`·`server.idle_sec`·`server.delay_ms`를 바꿔 `setup()`을 다시 불러도 기존 `kb.py serve`가 내려가지 않는다. 또한 `server.stop()`(`server.lua:412-432`)이 `S.last_start`를 지우지 않아, `enable = false`→`true` 전환 시 60초 재시작 제한에 걸려 워밍업이 건너뛰어진다.
- **근거·재현**:
  - `init.lua:412-426`은 `proc._py_cache = nil`만 비우고 `server.enable == true`면 `server.stop()`을 부르지 않는다.
  - `define_autocmds()`→`schedule_server()`가 `server.start({ warmup = true })`를 불러도 `server.lua:257`에서 `S.state == "starting" or S.state == "ready"`로 즉시 반환한다.
  - `:checkhealth work-kb`(`health.lua:81-83`)는 `server.status()`를 그대로 읽어, 바뀐 설정의 서버가 아니라 이전 `pid` 서버를 `준비됨`으로 표시한다.
- **제안 수정**: `setup()`에서 서버가 돌고 있을 때 `server.status().key`와 새 설정 키를 비교해 다르면 `server.stop()`하고 `S.last_start`를 비운다(또는 `server.stop()` 자체가 의도적 종료 시 쿨다운을 초기화하거나, 호출처에서 제어).
- **예상 수정 파일 목록**: `nvim/lua/work-kb/init.lua`, `nvim/lua/work-kb/server.lua`, `tests/nvim/spec/server_spec.lua`

### A-03
- **파일:줄**: `nvim/lua/work-kb/server.lua:316-328`, `scripts/serve.py:310-313, 327-330`
- **분류**: 정확성
- **심각도**: 중간
- **문제**: `server.lua:320`은 `code == 0 and S.state == "ready" and not S.inflight and #S.queue == 0`일 때만 정상 종료(`S.state = "stopped"`, `S.last_start = nil`)로 본다. `kb.py serve`가 `--idle-sec` 만료나 `locks_leaked()`(`serve.py:327-330`)로 `exit 0` 종료를 막 시작한 찰나에 새 요청이 `S.inflight`나 `S.queue`에 들어가면, `code == 0`인데도 `kill_server(...)`가 불려 `S.state = "dead"`가 되고 60초 동안 상주 서버 재시작이 막힌다.
- **근거·재현**: `server.lua:320-327` 조건식과 `serve.py:310-313` 파이프 종료 타이밍 경합. `tests/nvim/spec/server_spec.lua`의 유휴 종료 시험은 큐가 빈 경우만 다룬다.
- **제안 수정**: `on_exit`에서 `code == 0 and S.state == "ready"`이면 미완료 요청이 있어도 `kill_server("dead")` 대신 대기 요청만 `fail_pending()`으로 단발 폴백시키고 `S.state = "stopped"`, `S.last_start = nil`로 둔다.
- **예상 수정 파일 목록**: `nvim/lua/work-kb/server.lua`, `tests/nvim/spec/server_spec.lua`

### A-04
- **파일:줄**: `nvim/lua/work-kb/jira.lua:186-210, 575-595`, `nvim/lua/work-kb/fileio.lua:444-448`
- **분류**: 정확성
- **심각도**: 중간
- **문제**:
  1. `M.promote()`(`jira.lua:579-588`)가 `line`과 `bufnr`을 먼저 읽은 뒤 `M.read_project()`(`vim.wait` 포함)와 `ui().confirm()`을 거치고, 확인 뒤에야 `M.daily_line_ref()`를 호출해 그 시점의 현재 버퍼·커서 줄을 다시 읽는다. 대기 중 커서/창이 바뀌면 확인 문구와 다른 줄을 승격한다.
  2. 비동기 `act promote`가 실행되는 동안 사용자가 해당 `daily` 버퍼를 수정하면, 완료 콜백의 `M.revert_if_stale(bufnr)`→`fileio.refresh(bufnr)`(`fileio.lua:445`)가 `not vim.bo[bufnr].modified` 조건 때문에 디스크에 덧붙은 ` [[NEW-1]]`을 반영하지 않고 조용히 건너뛴다.
- **근거·재현**: `jira.lua:579-592`와 `jira.lua:196-210`, `fileio.lua:444-448` 코드 흐름 대조.
- **제안 수정**: `read_project()`·`confirm()` 전에 대상 `bufnr`과 1-based 줄 번호(`rel:lineno`)를 고정하고, `on_exit`에서 `vim.bo[bufnr].modified`이면 ` [[KEY]]` 자동 재동기화 실패 경고를 띄워 사용자가 덮어쓰지 않게 알린다.
- **예상 수정 파일 목록**: `nvim/lua/work-kb/jira.lua`, `tests/nvim/spec/jira_spec.lua`

### A-05
- **파일:줄**: `nvim/lua/work-kb/init.lua:307-323`, `nvim/after/ftplugin/markdown.lua:27-52`, `nvim/lua/work-kb/config.lua:163-192`, `nvim/lua/work-kb/jira.lua:171-182`
- **분류**: 정확성
- **심각도**: 중간
- **문제**: `opts.root`를 주지 않았을 때 `config.root()`는 `vim.uv.fs_realpath` 기반 실제 경로를 반환(`config.lua:168-183`)한다. 반면 `init.lua:in_vault`, `after/ftplugin/markdown.lua`, `jira.daily_relative`는 버퍼 경로와 `vault`에 `fs_realpath`를 적용하지 않고 `vim.fs.normalize` 문자열만 비교한다. Windows 정션 경로 아래로 볼트 `.md` 파일을 열면 볼트 파일로 인식하지 못한다(`autoread`·`shiftwidth=2`·`promote` 등이 빠진다).
- **근거·재현**: `fileio.find_buffer`(`fileio.lua:336-368`)는 정션 불일치를 피하려고 `realpath()` 비교를 따로 구현했으나, `init.lua:320`·`after/ftplugin/markdown.lua:50`·`jira.lua:176`에는 빠져 있다.
- **제안 수정**: `config.lua`에 `fs_realpath` 폴백까지 포함한 경로 정규화/`in_vault` 공용 헬퍼를 두고 `init.lua`·`ftplugin`·`jira.lua`가 공유한다.
- **예상 수정 파일 목록**: `nvim/lua/work-kb/config.lua`, `nvim/lua/work-kb/init.lua`, `nvim/after/ftplugin/markdown.lua`, `nvim/lua/work-kb/jira.lua`, `tests/nvim/spec/ftplugin_spec.lua`

### A-06
- **파일:줄**: `scripts/serve.py:47, 307-325`, `scripts/lib/jira_client.py:323`, `nvim/lua/work-kb/server.lua:14`
- **분류**: 정확성
- **심각도**: 중간
- **문제**: `act transitions`가 `SERVED` 목록(`serve.py:47`)에 포함되어 단일 스레드 `serve()` 루프 안에서 동기 Jira HTTP 호출(`JiraClient.timeout` 기본 30초)을 실행한다. 회사 VPN/Jira 서버 응답이 지연되면 `serve.py` 직렬 큐 전체가 막히고, `server.lua:14`의 `M.request_timeout_ms = 30000`에 걸려 클라이언트가 `kill_server()`로 서버를 죽이고 60초 동안 쿨다운에 들어간다.
- **근거·재현**: `serve.py:47`과 `318-322`, `jira_client.py:323`(`timeout or 30`), `server.lua:14, 234-242` 대조.
- **제안 수정**: 네트워크 I/O가 있는 `act transitions`는 `SERVED`에서 빼서 단발 비동기 프로세스로 돌리거나, `JiraClient` 타임아웃을 `request_timeout_ms`보다 충분히 짧게 둔다.
- **예상 수정 파일 목록**: `scripts/serve.py`, `nvim/lua/work-kb/proc.lua`, `tests/test_serve.py`, `tests/nvim/spec/proc_spec.lua`

### A-07
- **파일:줄**: `install.ps1:384-385, 425-451`, `tests/test_install_script.py:510-514, 557-562`
- **분류**: 정확성
- **심각도**: 중간
- **문제**: `install.ps1 -Offline`에서도 `uv venv --seed --python 3.14` / `3.13`을 `--offline`·`--no-python-downloads` 없이 호출한다. `uv venv --seed`는 시드 패키지와 Python 탐색/내려받기를 시도할 수 있어 완전 오프라인 환경에서 실패할 위험이 있다(실제 폐쇄망 환경 차단은 미실행으로 **추정**).
- **근거·재현**: `uv venv --help` 출력에서 `--offline`·`--no-python-downloads` 플래그 지원을 확인했고, `install.ps1:384-385`는 `-Offline` 분기 없이 고정 인자를 넘김을 확인했다. `tests/test_install_script.py:512, 561`도 가짜 `uv` 대역을 써서 `-Offline`인데도 `venv --seed --python 3.14` 형태만 단언해 누락이 가려졌다.
- **제안 수정**: `-Offline`이 켜졌을 때 `uv venv` 후보 인자에 `'--offline'`, `'--no-python-downloads'`를 추가하고 `-WhatIf` 안내 문구(`428`줄)도 `-Offline`이면 `--no-index --find-links` 기준으로 표시한다.
- **예상 수정 파일 목록**: `install.ps1`, `tests/test_install_script.py`

### A-08
- **파일:줄**: `nvim/capture.ahk:89-101, 109, 124-130`, `install.ps1:578`
- **분류**: 정확성
- **심각도**: 중간
- **문제**:
  1. `RpcCall`(`capture.ahk:92-96`)이 `cmd.exe /d /s /c ... > out`으로 `nvim.exe`를 띄우고 타임아웃 시 `ProcessClose(pid)`로 `cmd.exe`만 종료한다. 자식 `nvim.exe`가 살아 남아 `out` 임시 파일 핸들을 계속 쥘 수 있다(`Capture`의 `Run(nvd)`도 `.cmd` 래퍼일 때 동일).
  2. `Capture()`(`capture.ahk:109`)는 `FindOnPath("nvd.cmd")`로 `.cmd`만 찾는다. 반면 `install.ps1:578`은 `Get-Command nvd`로 확장자를 제한하지 않으며, 실제 이 PC의 `install.ps1 -WhatIf` 출력에서도 `nvd: D:\work\nvim-config\bin\nvd.ps1`이 탐지되었다(동일 폴더에 `nvd.cmd` 병존 여부는 저장소 밖이라 미확인).
- **근거·재현**: `nvim/capture.ahk:5-11`(미확인 명시), `92-100`, `109` 코드 확인 및 `install.ps1 -WhatIf` 실행 출력.
- **제안 수정**: `RpcCall`은 `cmd.exe`를 거치지 않고 리디렉션/프로세스 트리 종료(`taskkill /T /F`) 또는 직접 프로세스 제어를 쓰고, `nvd` 탐색은 `nvd.cmd` 외 `nvd.exe`·`nvd.ps1` 등 실행 형태에 맞게 조정한다.
- **예상 수정 파일 목록**: `nvim/capture.ahk`

### A-09
- **파일:줄**: `nvim/lua/work-kb/health.lua:11-14`, `install.ps1:75`, `scripts/doctor.py:1129`, `docs/DEPLOY.md:14`, `README.md:19`, `docs/USER-GUIDE.md:99, 170-175, 219`, `nvim/lua/work-kb/search.lua:2, 7-19, 123-133, 200-209`, `tests/nvim/minimal_init.lua:45-47`
- **분류**: 정확성
- **심각도**: 낮음
- **문제**: 문서와 구현 사이 불일치가 3곳 있다.
  1. **최소 Neovim 버전**: `health.lua:11-14`와 `docs/TROUBLESHOOTING.md:87`은 `0.12` 미만을 오류로 처리하지만, `install.ps1:75`, `scripts/doctor.py:1129`, `docs/DEPLOY.md:14`, `README.md:19`는 `0.11` 이상으로 적혀 있다(`USER-GUIDE.md:99`만 주석으로 차이를 설명).
  2. **검색 동작**: `USER-GUIDE.md:219`는 `<leader>ks`가 `daily·inbox·wiki·jira에서 낱말을 검색한다(공백은 AND)`고 설명하지만, `search.lua:201-209`는 `fzf-lua`(`cwd = vault`, 정규식)와 `rg`(`--fixed-strings`, 공백 포함 고정 문자열)를 우선 쓰고, 공백 AND는 `kb.py search` 폴백에만 적용된다.
  3. **`runtimepath` 안내**: `USER-GUIDE.md:171-175`는 `nvim/after/ftplugin/markdown.lua` 적용을 위해 `"<repo>/nvim/after"`도 `rtp`에 추가하라고 적었으나, Neovim은 `rtp` 항목 아래의 `after/`를 자동 로드하므로 `nvim/after`를 `rtp`에 넣으면 `nvim/after/after/`를 찾게 된다(`minimal_init.lua:45-47`의 불필요한 한 줄을 그대로 옮겨 적은 것으로 **추정**).
- **근거·재현**: 위 파일 해당 줄 직접 대조 확인.
- **제안 수정**: 최소 지원 버전(`0.11` vs `0.12`) 기준을 코드·설치 스크립트·문서 전체에서 하나로 통일하고, `USER-GUIDE.md`의 검색 설명과 수동 `rtp` 예시(`"<repo>/nvim"`만 추가)를 바로잡는다.
- **예상 수정 파일 목록**: `docs/USER-GUIDE.md`, `README.md`, `docs/DEPLOY.md`, `install.ps1`, `scripts/doctor.py`, `nvim/lua/work-kb/health.lua`, `tests/nvim/minimal_init.lua`

### A-10
- **파일:줄**: `tests/nvim/spec/server_spec.lua`, `tests/nvim/spec/proc_spec.lua`, `tests/test_serve.py`, `tests/test_install_script.py`
- **분류**: 개선
- **심각도**: 중간
- **문제**: 새로 확인된 경계 조건에 대한 회귀 테스트 공백이 있다.
  - `server_spec.lua`: 유휴 종료(`code == 0`) 중 대기 요청이 있을 때(`A-03`), `setup()` 재호출로 `root`/`python`/`idle_sec`가 바뀔 때(`A-02`)의 검증이 없다.
  - `test_serve.py` / `proc_spec.lua`: `act --root . list`·`proposals --root . list`처럼 옵션이 하위 명령 앞에 오는 형태(`A-01`)의 테스트가 없다(기존 테스트는 모두 `act list --root ...` 순만 다룬다).
  - `test_install_script.py`: `-Offline` 시험이 가짜 `uv` 대역으로 `venv --seed --python 3.14`만 검사해 `--offline`·`--no-python-downloads` 누락(`A-07`)을 잡지 못한다.
- **근거·재현**: `tests/test_serve.py:279-281`, `tests/test_install_script.py:510-564`, `tests/nvim/spec/server_spec.lua`, `tests/nvim/spec/proc_spec.lua:152-158` 점검 결과.
- **제안 수정**: `A-01`~`A-03`, `A-07` 수정 시 각 파일에 최소 재현 케이스를 회귀 테스트로 추가한다.
- **예상 수정 파일 목록**: `tests/nvim/spec/server_spec.lua`, `tests/nvim/spec/proc_spec.lua`, `tests/test_serve.py`, `tests/test_install_script.py`

### A-11
- **파일:줄**: `nvim/lua/work-kb/init.lua:307-323`, `nvim/after/ftplugin/markdown.lua:27-52`, `nvim/lua/work-kb/jira.lua:171-182`, `nvim/lua/work-kb/capture.lua:255-262`, `scripts/kb.py:120`, `scripts/serve.py:75`, `nvim/lua/work-kb/proc.lua:574-580`
- **분류**: 개선
- **심각도**: 낮음
- **문제**:
  1. 볼트 루트 기준 경로 판정(`in_vault`, `after/ftplugin/markdown.lua`, `daily_relative`, `vault_relative`)이 4곳에 흩어져 있으며, `capture.lua:258`과 `jira.lua:177`은 `v`가 드라이브 루트 `"D:/"`처럼 `/`로 끝날 때 `"D://..."`가 되는 사소한 경계 차이도 각각 안고 있다.
  2. `act`/`proposals` 하위 명령 판정이 `kb.py`·`serve.py`·`proc.lua` 3곳에 중복 구현되어 있다(`A-01`의 직접 원인).
- **근거·재현**: 각 파일 해당 줄 코드 구조 대조.
- **제안 수정**: 볼트 경로 판정·상대 경로 추출을 `config.lua`로 모으고, Python 쪽 `kb.py`·`serve.py`의 명령/하위 명령 해석을 공통 헬퍼로 단순화한다.
- **예상 수정 파일 목록**: `nvim/lua/work-kb/config.lua`, `nvim/lua/work-kb/init.lua`, `nvim/after/ftplugin/markdown.lua`, `nvim/lua/work-kb/jira.lua`, `nvim/lua/work-kb/capture.lua`, `scripts/kb.py`, `scripts/serve.py`
