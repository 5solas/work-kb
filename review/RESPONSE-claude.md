# agy 리뷰 지적 처리 (claude, 3단계-2)

대상: `review/CODEREVIEW-agy.md`(agy 커밋 `14aa103`), 지적 5건(중요 2, 사소 3). 모두 **수정**했다. 반박은 없다.
커밋 범위: `d21456b`..`4f42e61`(지적 5건, 지적마다 커밋 하나). 이 파일은 따로 커밋한다.
고친 파일은 모두 claude 소유다(`nvim/lua/work-kb/open.lua`·`fileio.lua`, `install.ps1`, `tests/nvim/spec/open_spec.lua`·`capture_spec.lua`, `tests/test_serve.py`·`test_install_script.py`·`test_dash_args_claude.py`).

| R-ID | 처리 | 커밋 | 근거 |
|---|---|---|---|
| R-agy-01 (중요) | 수정 | `d21456b` | agy P-44(`2747ef6`)는 `notes.find_people`이 **줄 전체를 NFC로 바꾼 뒤** `_PERSON_CHARS`로 찾는다. Lua `open.lua`도 같게: `@` 뒤를 이름 글자와 한글 조합형 자모(U+1100..U+11FF)로 훑고, 그 글을 `names.nfc`로 바꾼 뒤 앞쪽 이름 글자만 남긴다(`person_name`). 홀로 남는 자모는 Python처럼 이름이 아니다. 시험(먼저 실패): `tests/nvim/spec/open_spec.lua` "reads @names like notes.find_people"에 NFD `@김철수 x`, `(@김철수)`, `@ᄀx` 3건 추가 → 수정 전 `@김철수 x 기대: "김철수"` 실패, 수정 뒤 open_spec 19/19 통과. **합친 상태 확인**: 임시 브랜치 `tmp/merge-check-claude`(내 `4f42e61` + agy `ffcfca6` 병합, 충돌 없음)에서 `review/repro/C-12_C-13_name_parity.py` → exit 0 `OK`(전에는 `DIFF @이름 in='@김철수 x' … lua o looks for=None`), `C-10_project_keys.lua` → exit 0, `pytest tests/test_nvim.py tests/test_serve.py tests/test_dash_args_claude.py` → 34 passed. 확인 뒤 임시 브랜치와 워크트리를 지웠다. 작업 브랜치에는 agy 커밋을 병합하지 않았다 |
| R-agy-02 (중요) | 수정(시험) | `a4b0707` | 원인: 서버는 응답을 보낸 **뒤** 모듈 mtime을 본다(`serve.py`의 `watch.changed()`). 옛 시험은 `ping` 응답을 읽자마자 파일을 바꿔, 서버의 ping 뒤 확인과 경쟁했다. 확인이 늦으면 다음 `status`를 받기 전에 끝난다. 동작 자체(응답 뒤 종료 0, 다음 요청이 새로 띄움)는 설계대로라 코드는 그대로 두고 시험을 고쳤다: (1) "안 바뀌면 계속 돈다"는 따로 확인하고 그 서버를 닫는다. (2) 바꾸기 시험은 새 서버의 준비 줄 뒤·첫 요청 전에 파일을 바꾼다. 감시 기준은 준비 줄 전에 잡히고(`main`에서 `CodeWatch()`를 만든 뒤 `serve`가 준비 줄을 보냄) 첫 요청 전에는 서버가 파일을 보지 않으므로 첫 응답 뒤 확인이 반드시 변경을 본다. 경쟁은 확률적이라 수정 전 실패를 일부러 만들 수는 없었다. 수정 전 실패 근거: 합친 상태 `-n auto`에서 두 번(내 1차 리뷰 실행, agy 실행) `AssertionError: 서버가 끝남 … 코드가 바뀌어 서버를 끝냅니다`. 수정 뒤: `pytest -q -n auto tests/test_serve.py` 5회 연속 `19 passed`, 전체 `-n auto` 5회는 아래 완료 기준 |
| R-agy-03 (사소) | 수정 | `0a8fe5f` | `install.ps1` Step-env의 옮긴 클론 판정 `($entry.value -ceq $cur)` → `(Test-SamePath $entry.value $cur)`(uninstall.ps1과 같은 규칙). 시험(먼저 실패): `tests/test_install_script.py::test_env_moved_clone_matches_path_case_insensitively[upper, trailing-backslash]` → 수정 전 `STEP env failed WORK_KB_ROOT is C:\USERS\…; rerun with -Yes`, 수정 뒤 통과. install 시험 전체 43 passed, 1 skipped. install.ps1은 여전히 ASCII |
| R-agy-04 (사소) | 수정 | `005c12b` | `fileio.write_bytes`의 바꾸기 직전 다시 읽기: 파일이 있는데 읽지 못하면(공유 없이 잠김) 사람 편집으로 보지 않고 이름 바꾸기와 같은 백오프로 기다렸다 다시 본다. 끝까지 읽지 못하면 "다른 프로그램이 잡고 있어 파일을 읽지 못했습니다. 쓰지 않았습니다"로 끝나고 파일은 그대로. 파일이 없어졌거나 바이트가 다르면 지금처럼 "읽은 뒤 바뀌었습니다". 시험(먼저 실패): `capture_spec.lua` "atomic write: a busy re-read is retried, not taken as a human edit (R-agy-04)"(읽기 두 번 EBUSY 뒤 성공 / 계속 EBUSY) → 수정 전 `잠깐 읽지 못한 것을 사람 편집으로 봄: … 읽은 뒤 바뀌었습니다` 실패, 수정 뒤 capture_spec 16/16 통과 |
| R-agy-05 (사소) | 수정(시험 추가) | `4f42e61` | `tests/test_dash_args_claude.py::test_sync_jql_equals_form_accepts_dash_values`: `jira_sync.main(["--root", V, "--jql=<-로 시작하는 글>"])`이 그 JQL을 그대로 `run_jql`에 넘기는지(설정·Jira·실행은 바꿔 끼워 네트워크 없음), 옛 꼴 `--jql -hong`은 거부되는지 본다. Python 쪽은 바뀐 것이 없어 이 시험은 처음부터 통과한다(계약을 고정하는 시험 추가). `--jql=` 꼴을 만드는 Lua 쪽은 `jira_spec.lua`가 이미 단언한다 |

## 완료 기준 (Python 3.14, 이 브랜치 단독)

| 기준 | 명령 | 결과 |
|---|---|---|
| 전체 | `.venv\Scripts\python.exe -m pytest -q` | 2217 passed, 4 skipped, 실패 0 (580초) |
| 병렬 5회 | `-m pytest -q -n auto` ×5 | 5회 모두 2217 passed, 4 skipped (101~127초) |
| perf / demo / packaging | `-m pytest -q -m perf` / `-m demo` / `-m packaging` | 1 / 5 / 3 passed |
| Neovim spec | `tests/test_nvim.py`(두 콘솔 모드, spec 전체)가 전체 실행에 포함되어 통과. 고친 spec: open_spec 19/19, capture_spec 16/16 | 통과 |
| ruff | `ruff check . --exclude review` | 386 (증가 없음) |

건너뜀 4개는 2단계와 같다(이 세션의 클립보드 접근 거부 2, 심볼릭 링크 권한 1, nvim-config `nvd-rpc.lua` 없음 1).

## 정적 분석

- `ruff check . --exclude review`: 386건(기준 `d168f6c` 386, 증가 없음). 고친 시험 파일의 경고는 모두 전부터 있던 것이다.
- `luacheck nvim/lua/work-kb/open.lua nvim/lua/work-kb/fileio.lua tests/nvim/spec/open_spec.lua tests/nvim/spec/capture_spec.lua --globals vim describe it --no-max-line-length`: 0 warnings / 0 errors.
