# Stage 3 상호 코드 정밀 리뷰 (`agy` -> `claude`)

- 검토 대상: `nvim-review-claude` (`4372045`, `d168f6c..4372045`, Stage 2 `P-01..P-34` 34건)
- 통합 대상: `nvim-review-agy` (`ffcfca6`, Stage 2 `P-40..P-77`)
- 종합 판정: **수정 후 승인** (차단 0 / 중요 2 / 사소 3)

## 1. 요약 및 검증 결과

- 병합 시험: 임시 워크트리에서 `ffcfca6`와 `4372045`를 충돌 없이 병합(`ort`)해 확인했다.
  - 주요 9개 스위트(`pytest -q tests/test_dash_args_claude.py tests/test_docs.py tests/test_serve.py tests/test_kb_dispatch.py tests/test_packaging.py tests/test_doctor.py tests/test_install_script.py tests/test_repo_tree.py tests/test_nvim.py`): `464 passed, 1 skipped, 3 deselected`.
  - 전체 스위트(`pytest -q -n auto`): `1 failed, 2271 passed, 4 skipped` (`tests/test_serve.py::test_exits_after_code_change`만 실패, 단독 재실행 시 `1 passed`).
  - 정적 검사: `ruff check . --exclude review --statistics` 381건, `luacheck nvim tests/nvim --globals vim --no-max-line-length` 39개 파일 경고 0·오류 0.
- 재현 스크립트(`review/repro*`) 병합 트리 실행: `A-01`, `A-03`, `A-04`, `A-05`, `A-06`, `A-07`, `A-14`, `C-07`, `C-09`, `C-14`, `C-15`, `C-16`, `C-17`, `C-18`, `C-33`, `C-35` 통과. `C-03_capture_nonatomic_write.lua`는 임시 워크트리에 `.venv`가 없어 종료 1(동일 항목의 `capture_spec.lua` 스펙은 통과). **`C-12_C-13_name_parity.py`는 실패(종료 1, R-agy-01).**
- 계약·아키텍처 일관성: `P-01..P-34` 모두 합의(`PLAN.md`)의 의도를 충실히 따랐다. `kb.subcommand`와 `proc.served`, `fileio.write_bytes`, `config.in_dir`, `uninstall.ps1`의 클론 소유권 판정, `DECISIONS.md` D-NV1~D-NV4 기록 모두 코드와 문서가 일치한다.

---

## 2. 지적 사항

### R-agy-01 [중요] 교차 브랜치 NFD `@이름` 패리티 불일치 (`P-13` / `P-44`)

- **위치**: `nvim/lua/work-kb/open.lua:100-110, 173-197` · `tests/nvim/spec/open_spec.lua:490-506`
- **문제**: `open.lua`의 `PERSON_RANGES`는 한글 호환 자모(`0x3131..0x318E`)와 완성형 음절(`0xAC00..0xD7A3`)만 포함하고 조합형 자모(`0x1100..0x11FF`)를 허용하지 않는다. `person_at`이 NFC 정규화도 하지 않는다. `agy` 쪽 `P-44`(`notes.find_people`)와 병합하면 Python `link`는 NFD `@김철수`를 `김철수`로 연결하지만 Neovim `o`(`open.open_at_point`)는 `@`를 찾지 못한다. `open_spec.lua`에도 `C-12_C-13_name_parity.py:20`에 있던 `@김철수 x` 케이스가 빠져 있다.
- **근거**: 병합 워크트리에서 `review/repro/C-12_C-13_name_parity.py` 실행 결과:
  ```text
  DIFF @이름    in='@김철수 x'               python link page='김철수'          lua o looks for=None
  FAIL: 1 Lua/Python differences
  ```
- **수정안**: `PERSON_RANGES`에 `{ 0x1100, 0x11FF }`를 추가하고 `person_at`(또는 `open_person`)에서 `require("work-kb.names").nfc(...)`로 정규화해 돌려준다. `tests/nvim/spec/open_spec.lua`에 `"@김철수 x"` 케이스를 추가한다.

---

### R-agy-02 [중요] `test_exits_after_code_change` 병렬 실행 경쟁 상태 (`P-06`)

- **위치**: `tests/test_serve.py:634-639` · `scripts/serve.py:366-379`
- **문제**: `serve()`는 `channel.send(response)`로 응답을 보낸 **뒤** `watch.changed()`를 검사한다. `test_exits_after_code_change`는 `server.ping()`이 응답을 읽자마자(`serve`가 아직 `ping` 반복문의 `watch.changed()`에 닿기 전) 곧바로 `os.utime(target, ...)`로 `status.py`의 `mtime`을 바꾼다. CPU 부하가 큰 `pytest -n auto` 실행에서는 서버가 `ping` 요청 뒤의 `watch.changed()`에서 변경을 먼저 감지해 다음 `server.run(["status", ...])`를 받기 전에 종료 0으로 끝난다.
- **근거**: 병합 워크트리 전체 `pytest -q -n auto` 실행에서 유일하게 실패했다(`tests/test_serve.py:639`):
  ```text
  AssertionError: 서버가 끝남. stderr=kb.py serve: 코드가 바뀌어 서버를 끝냅니다 (...\scripts-copy\status.py)
  ```
  단독 실행(`pytest -q tests/test_serve.py -k test_exits_after_code_change`)에서는 통과한다.
- **수정안**: `test_exits_after_code_change`가 `server.ping()` 직후 바로 `os.utime`을 호출해 이전 루프 끝단의 `watch.changed()`와 경쟁하지 않도록, `server.run(["status", ...])` 뒤에 바로 `os.utime`하거나(`ping`은 별도 케이스로 분리), 루프 다음 대기 지점에 들어간 뒤 `mtime`을 갱신하도록 정리한다.

---

### R-agy-03 [사소] `install.ps1` `Step-env`의 대소문자 구분 경로 비교 (`P-08`)

- **위치**: `install.ps1:480`
- **문제**: `uninstall.ps1:176, 182`는 `WORK_KB_ROOT` 비교에 대소문자·끝 `\`를 무시하는 `Test-SamePath`를 쓰지만, `install.ps1:480`은 moved-clone 판정에 `($entry.value -ceq $cur)`로 대소문자를 엄격히 비교한다. Windows에서 드라이브 문자나 대소문자만 다른 경우 기존 기록의 `previous` 승계를 놓칠 수 있다.
- **근거**: `install.ps1:480`: `$ours = $entry -and $cur -and ($entry.value -ceq $cur) -and -not (Test-Path -LiteralPath $cur -PathType Container)`.
- **수정안**: `(Test-SamePath $entry.value $cur)`로 통일한다.

---

### R-agy-04 [사소] `fileio.write_bytes` 재시도 안 `M.read_bytes`의 잠금 구분 없음 (`P-05`)

- **위치**: `nvim/lua/work-kb/fileio.lua:91-94`
- **문제**: `write_bytes`는 `uv.fs_rename`이 `EBUSY`/`EACCES`/`EPERM`이면 최대 8회 지수 백오프로 재시도한다. 그러나 같은 루프 안의 `if expect ~= nil and M.read_bytes(path) ~= expect`는 다른 프로세스가 읽기 공유 없이 잠금을 잡아 `read_bytes`가 `nil, err`를 돌려줄 때도 바이트 불일치로 보고 재시도 없이 `"파일이 읽은 뒤 바뀌었습니다"` 오류로 즉시 끝낸다.
- **근거**: `nvim/lua/work-kb/fileio.lua:24-27, 92-94`.
- **수정안**: `read_bytes`가 반환한 오류 문자열(두 번째 반환값)이 있으면 즉시 사람 편집 충돌로 단정하지 않고 재시도하거나 잠금 오류를 구분해 보고한다.

---

### R-agy-05 [사소] `test_dash_args_claude.py`의 `sync --jql=` 파서 검사 누락 (`P-04`)

- **위치**: `tests/test_dash_args_claude.py:22-42`
- **문제**: `jira_spec.lua:925`는 `jira.query`가 `{ "sync", "--root", vault, "--jql=-project = KB" }`를 만드는지 검증하지만, 대응 Python 계약 시험인 `tests/test_dash_args_claude.py`는 `jira_act`와 `search`만 검사하고 `jira_sync`(`sync --root V --jql=-h`) 파서 검증이 빠져 있다.
- **근거**: `PLAN.md` P-04 수정 범위에는 `jira.lua`의 `--jql=`이 포함되나 `tests/test_dash_args_claude.py`는 `comment`, `assign`, `worklog`, `create`, `search`만 다룬다.
- **수정안**: `tests/test_dash_args_claude.py`에 `jira_sync` 또는 `run_kb("sync", ...)` 기반 `--jql={text}` 파서 검증을 추가한다.

---

## 3. 변이 검증 결과 (병합 워크트리)

주요 구현 9건(10개 지점)에 대해 수정 코드를 임시로 되돌린 뒤 해당 시험을 실행하고 모두 즉시 복원했다.

| 항목 | 되돌린 부분 | 테스트 실패 여부 |
|---|---|---|
| `P-01` (`scripts/serve.py`) | `SERVED["act"]`에 `"transitions"` 복원 | 실패 (`test_act_transitions_is_not_served`, `RC=1`) |
| `P-01` (`nvim/lua/work-kb/server.lua`) | `call(item.cb, nil, nil, "timeout")` -> `call(item.cb, nil)` | 실패 (`write command waiting on kb_lock`, `RC=1`) |
| `P-03` (`scripts/kb.py`) | `subcommand(argv[1:])` -> `argv[1]` | 실패 (`test_agent_allows_root_before_subcommand`, `RC=1`) |
| `P-04` (`nvim/lua/work-kb/jira.lua`) | `"--body=" .. body` -> `"--body", body` | 실패 (`passes every free-text option value with =`, `RC=1`) |
| `P-05` (`nvim/lua/work-kb/fileio.lua`) | `tmp` 경로를 `path`로 직접 쓰기 | 실패 (`concurrent reader never sees a truncated daily`, `RC=1`) |
| `P-06` (`scripts/serve.py`) | `watch.changed()` 호출 비활성화(`None`) | 실패 (`test_exits_after_code_change` 15초 시간 초과, `RC=1`) |
| `P-08` (`uninstall.ps1`) | `Test-SamePath $owner $RepoRoot` 검사 우회 | 실패 (`test_uninstall_from_other_clone_keeps_variable`, `RC=1`) |
| `P-15` (`nvim/lua/work-kb/config.lua`) | `in_dir`의 `real_path` 폴백 비활성화 | 실패 (`applies through a junction`, `RC=1`) |
| `P-19` (`scripts/wheels.py`) | `core.refuse_if_agent("wheels")` 제거 | 실패 (`test_wheels_main_refuses_agent`, `RC=1`) |
| `P-20` (`scripts/kb.py`) | `_preflight` Python 최소 버전을 `(3, 13)`으로 복원 | 실패 (`test_preflight_rejects_313`, `RC=1`) |

---

## 4. 재확인 (3단계-2, `claude` `d21456b..4f42e61` · 기록 `b72f46d`)

내 브랜치(`f186c65`)에서 파생한 임시 브랜치 `tmp/recheck-claude-stage3`에 `b72f46d`를 충돌 없이 병합해 확인한 뒤 임시 브랜치·워크트리를 삭제했다(작업 브랜치 `5solas/nvim-review-agy`에는 미병합).

| R-ID | 커밋 | 판정 | 확인 근거 |
|---|---|---|---|
| `R-agy-01` | `d21456b` | **해소** | `open.lua` `scan_char`/`person_name`이 `0x1100..0x11FF` 포함 후 `names.nfc` 변환·`name_char` 절단을 수행하며 `open_spec.lua`에 NFD·단독 자모 3건 추가. 병합 상태에서 `C-12_C-13_name_parity.py` 전 항목 `SAME` · `OK` (종료 0) |
| `R-agy-02` | `a4b0707` | **해소** | `tests/test_serve.py::test_exits_after_code_change`가 `ping` 루프와 `utime` 경합을 분리(준비 줄 뒤·첫 요청 전 파일 갱신). 병합 상태에서 대상 4개 스위트 `77 passed, 1 skipped` 및 전체 `pytest -q -n auto` 2회 연속 각 `2284 passed, 4 skipped` |
| `R-agy-03` | `0a8fe5f` | **해소** | `install.ps1:480`이 `Test-SamePath $entry.value $cur` 사용, `test_env_moved_clone_matches_path_case_insensitively` 통과 |
| `R-agy-04` | `005c12b` | **해소** | `fileio.lua` `write_bytes`가 `current == nil and uv.fs_stat(path) ~= nil`일 때 `RENAME_TRIES` 백오프 재시도 후 잠금 오류 구분 보고, `capture_spec.lua`(`test_nvim.py` 포함) 통과 |
| `R-agy-05` | `4f42e61` | **해소** | `tests/test_dash_args_claude.py::test_sync_jql_equals_form_accepts_dash_values`가 `--jql=...` 허용 및 `--jql -hong` 거부를 검증·통과 |

- **`docs/DECISIONS.md` D-NV4 (`124fbca`) 확인**: `P-51`, `P-55`, `P-56`, `P-58`, `P-61`, `P-67`, `P-69`, `P-70`, `P-74` 요약 모두 실제 Python 구현과 일치한다(불일치 없음).
- **최종 판정**: **승인**
