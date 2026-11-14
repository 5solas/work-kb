# 구현 기록 (claude 담당, 2단계)

기준: `review/PLAN.md` (`afbd2c3`). claude 담당 `구현` 34항목. 커밋 범위 `5184f64`..(마지막 커밋은 이 파일 커밋).
작업 방식: 파일 묶음이 겹치지 않게 서브에이전트 셋으로 나눠 같은 워크트리에서 구현했다(서버·프로세스 / Neovim UI / 설치·문서). 각자 자기 파일만 `git commit -- <경로>`로 커밋했다. 마지막 확인은 내가 했다.
바뀐 파일은 모두 PLAN §4 claude 소유다(`nvim/lua/work-kb/misc.lua`·`daily.lua`는 `nvim/**`). agy 소유 파일과 `review/` 기존 파일은 고치지 않았다.

## 요약

- 완료 34, 부분 0, 미완 0.
- 새 파일: `tests/test_dash_args_claude.py` 하나.
- 남은 일: DECISIONS의 D-NV4(agy 구현 결과 문안)는 자리표시 줄만 있다. agy 문안을 받으면 기록한다(PLAN 5절 3번 의존).

## 항목별

| P-ID | 상태 | 커밋 | 추가·변경한 시험 (수정 전 실패 → 수정 후 통과) | PLAN과 달라진 점 |
|---|---|---|---|---|
| P-01 | 완료 | `569da95` | `tests/test_serve.py::test_act_transitions_is_not_served`(기존 `test_act_transitions_parity_reads_only` 대체; 전: `frozenset({'list','transitions'}) == frozenset({'list'})` 실패). `tests/nvim/spec/server_spec.lua` "write command waiting on kb_lock is not rerun after timeout"(실제 serve·실제 kb_lock; 전: nil 기대, 0), "write timeout is reported to the caller"(`misc.link`·`daily.ensure_today`), 기존 시간 초과 시험에 "1분 제한 없이 다시 뜸" 단언 추가(전: 2 기대, 1) | `on_exit`·`proc.wait`·`run_sync`가 셋째 값 `reason`("timeout")을 넘긴다(claude 내부 Lua API, 워커 간 계약 아님). `proc.timeout_message` 추가. 보내기 전 큐에 있던 요청은 기존처럼 한 번 실행. `today --check-done`도 이름상 `today`라 쓰기로 취급되어 시간 초과 때 다시 돌지 않는다. `fake_serve.py`에 `--sleep-on 명령,...` 시험 옵션 추가 |
| P-02 | 완료 | `9656876`, 주석 `f956d23` | `server_spec.lua` "exit 0 with queued request falls back and allows restart"(전: "stopped" 기대, "dead") | `fail_pending()` 전에 `state="stopped"`로 둔다(콜백이 죽은 서버에 새 요청을 넣지 않게) |
| P-03 | 완료 | `c4bbe07` | `tests/test_kb_dispatch.py::test_subcommand_skips_root_and_stops_at_double_dash`(전: `AttributeError`), `::test_agent_allows_root_before_subcommand`(전: `PolicyRefused`), `tests/test_serve.py::test_root_before_subcommand_is_served`, `proc_spec.lua` "served() skips --root" | `--root` 아닌 옵션이 하위 명령 앞에 오면 None(KB_AGENT에서 거부)으로 본다. 유일한 전역 옵션이 `--root`라 그 밖은 어차피 argparse 오류. `--`도 None. `proc.lua`에 같은 규칙의 `M.subcommand` |
| P-04 | 완료 | `48814b3` | `jira_spec.lua` "passes every free-text option value with =", `search_spec.lua` "puts -- before the query"(전: `"--body","-h 댓글"`, `assign KB-12 -hong` 등), 기존 comment·assign·worklog·create·query·search 단언 갱신. 새 `tests/test_dash_args_claude.py`(7 passed) | Python 시험은 새 Lua 꼴의 인수를 직접 넣으므로 Lua 수정 전에도 통과한다(실패 증거는 Lua spec). 옛 꼴이 실패함을 보이는 시험도 함께 둠 |
| P-05 | 완료 | `5184f64` | `capture_spec.lua` "concurrent reader never sees a truncated daily"(전: `잘린 daily를 읽음: 0 < 1397823`), "atomic write: disk changed after reading is not overwritten, temp file removed"(전: 덮어씀). 동시 독자 시험 3회 연속 통과 | 다른 프로세스가 파일을 계속 열면 이름 바꾸기를 재시도하느라 캡처가 느려진다. 8회(약 12.75초) 실패하면 "파일을 바꾸지 못했습니다". 디스크가 읽은 뒤 바뀌면 "파일이 읽은 뒤 바뀌었습니다. 다시 시도하세요" |
| P-06 | 완료 | `dc3ac44` | `tests/test_serve.py::test_exits_after_code_change`(scripts 복사본의 mtime 갱신; 전: 15초 시간 초과) | `CodeWatch` 클래스. 요청 중 처음 가져온 모듈도 이어서 감시 |
| P-07 | 완료 | `22b8c15` | `server_spec.lua` "handles a 5 MB response line" — PLAN 예고대로 수정 전에도 통과(81 ms → 37 ms) | 없음 |
| P-08 | 완료 | `0eeda9f` | `tests/test_install_script.py::test_uninstall_from_other_clone_keeps_variable`, `::test_second_clone_install_records_previous`(전: `removed WORK_KB_ROOT`), 기존 이동 클론 시험 갱신, manifest에 `repo` | "다른 클론" = 기록값과 현재값이 같고 **그 폴더가 아직 있음**. 폴더가 없으면 옮긴 클론으로 보고 원래 앞 값을 이어받는다. 이동 클론 시험은 `copytree` 대신 `rename`. uninstall은 다른 클론 기록을 지우지 않고 `skipped ... set by another clone` |
| P-09 | 완료 | `ca1da3f` | `::test_offline_uv_has_no_download_flags`(전: `--offline` 없음), `::test_venv_whatif_plan` 갱신 | 플래그는 `--python X` 뒤, 경로 앞 |
| P-10 | 완료 | `7297cab` | `open_spec.lua` "resolves page names that contain a dot"(전: nil) | `.md`도 이미지도 아닌 이름은 그대로 먼저, 다음 `name.md`(lint와 같은 순서). 이미지 확장자는 `capture.IMAGE_EXTENSIONS` |
| P-11 | 완료 | `a498087` | `jira_spec.lua` "check-done passes the Lua date"(전: `--date` 없음) | 없음 |
| P-12 | 완료 | `e46cc3b`, `381b700` | `names_spec.lua` "folds unicode spaces like core.safe_name"(전: `"회의　메모"` 그대로) | 예약 이름 판정에서 `ı`를 `I`로(Python `str.upper()`와 같게) — C-12 근거에 있던 경우를 함께 고침 |
| P-13 | 완료 | `7297cab` | `open_spec.lua` "reads @names like notes.find_people"(전: "Nguyễn") | 지금 `_PERSON_CHARS`와 정확히 같다. agy P-44가 `find_people`에 NFC를 넣으면 NFD 자모 `@이름`은 Python만 맞는다(다음 단계 확인 필요) |
| P-14 | 완료 | `7297cab` | `open_spec.lua` "syncs jira/KEY and KEY.md links"(전: stub을 동기화 없이 엶) | 기존 "open-person-and-link" 시험에서 옛 동작(동기화 없음)을 단언하던 두 경우를 뺐다 |
| P-15 | 완료 | `a498087`, `acd6096` | `jira_spec.lua` "daily_relative with junction root and non-ASCII case"(전: nil), `ftplugin_spec.lua` "applies through a junction"(전: nil), `config_spec.lua` "vault_dir·in_dir …"(수정 뒤 추가) | `config.in_dir(path, dir)`은 불리언이 아니라 상대 경로 또는 nil. 아직 없는 파일은 가장 가까운 있는 부모의 실제 경로로. `jira.key_at_point`의 `jira/` 판정도 이 함수 사용 |
| P-16 | 완료 | `f956d23` | `keys_spec.lua` "removes keys bound under the old mapleader"(전: 옛 리더 키 남음) | `rebind_prefix`가 접두 문자열이 같아도 우리 옛 키를 먼저 뗀다. `keytrans(lhsraw)`로 저장 |
| P-17 | 완료 | `d8eeb81` | `misc_spec.lua` "idle server is info, not warning"(전: 실패) | 없음 |
| P-18 | 완료 | `a498087` | `jira_spec.lua` "promotes the line that was confirmed"(전: `:11` 대신 `:12` 기대) | 줄 참조를 프로젝트 질문 전에 고정하므로, 수정된 daily 버퍼는 질문 전에 저장된다(취소해도 저장됨) |
| P-19 | 완료 | `da6606e` | `tests/test_packaging.py::test_wheels_main_refuses_agent`(프로세스 안·직접 실행 둘 다; 전: `DID NOT RAISE PolicyRefused`) | `if __name__ == "__main__": core.run_main(main)` 추가(없으면 직접 실행이 아무것도 안 하고 0). P-32용 `_has_pip()` 분리 |
| P-20 | 완료 | `64f14e2`(kb.py), `b96f110`, `a45cc48` | `test_kb_dispatch.py::test_preflight_rejects_313`(전: `DID NOT RAISE`), `test_packaging.py::test_default_targets_are_314_only`, `::test_parse_targets_rejects_313`, `test_install_script.py::test_venv_313_is_not_good`, `test_doctor.py::test_python_313_fails`, `test_docs.py::test_docs_name_python_314_and_nvim_012_only[5개 문서]`, 기존 단언 갱신 | `parse_targets`는 3.14만 받는다(3.15도 거부). install 실패 문구 "not 3.14" |
| P-21 | 완료 | `b96f110`, `a45cc48` | `test_install_script.py::test_nvim_011_is_old`, `test_doctor.py::test_nvim_011_warns` | doctor는 낡은 Neovim을 지금처럼 info(`-`) 줄로 알린다(새 경고 등급 없음) |
| P-22 | 완료 | `e87bbe8`(serve.py), `a45cc48` | `test_docs.py::test_agents_serve_row_matches_served`(serve.SERVED를 읽음; 전: status 누락) | `docs/README.md` §4·§5의 "읽기 계열만" 문장도 고침 |
| P-23 | 완료 | `a45cc48` | `::test_user_guide_search_row_names_backends` | 없음 |
| P-24 | 완료 | `a45cc48` | `::test_vault_setup_mentions_neovim_vault_option` | 없음 |
| P-25 | 완료 | `a45cc48` | `::test_prefix_rebinding_documented_two_ways` | USER-GUIDE 6.2, DEPLOY 7단계, docs/README §6, DECISIONS §0 |
| P-26 | 완료 | `a45cc48` | `::test_decisions_has_no_stale_emacs_rules`(전: `KB_SKIP_EMACS` 있음) | §3·부록 A의 Emacs 역사는 지우지 않고 표시만 했다. 그래서 낡은 문자열 검사는 부록 A 앞까지만(`KB_SKIP_EMACS`는 파일 전체). D01·D02는 D-NV1로 일부 대체 표시. 6장 10항에 D-NV1~3, D-NV4 자리표시 |
| P-27 | 완료 | `a45cc48` | `test_deploy_exclusions_and_interpreters`가 새 문구를 단언 | 없음 |
| P-28 | 완료 | `a45cc48` | 문서만(`test_docs.py` 통과) | 없음 |
| P-29 | 완료 | `a45cc48` | `::test_user_guide_capture_keys_match_capture_lua` | 5.4에 "경로는 슬래시로" 주의 |
| P-30 | 완료 | `a45cc48` | `::test_agents_region_table_matches_managed_names`(전: lint·proposals 누락) | 없음 |
| P-31 | 완료 | `a45cc48` | `test_docs.py`: README를 CHECKED_DOCS에, `REAL_HOSTS_ALLOWED = {"README.md": {"github.com"}}`, "안내서 없으면 건너뜀" 분기 삭제, 키 표를 `init.lua` `M.keys`·`jira.lua` 메뉴와 이름까지 대조. `test_repo_tree.py` DOCS에 USER-GUIDE·PACKAGING | 없음 |
| P-32 | 완료 | `6126924`, README `a45cc48` | `_fake_venv`가 `sys.base_prefix`의 venvlauncher와 실행 중 인터프리터의 purelib로 만든다. pip가 없으면 이유와 함께 skip. `test_packaging`은 `wheels._has_pip`도 가짜로. 증거: pip 없는 임시 venv(`uv venv --python 3.14`, 시드 없음)에서 `127 passed, 4 skipped`, 실패 0(수정 전 test_packaging은 같은 venv에서 `ConfigError: … pip가 없습니다`로 실패) | 작업 트리 `.venv`에는 이제 pip가 있어 install 시험의 수정 전 실패는 다시 보이지 못했다 |
| P-33 | 완료 | `b96f110` | `test_doctor.py::test_editors_nvim_version_parsing`(버전 파싱·읽기 실패·시간 초과·PATH 없음) | 시험 전용 항목이라 파싱 부분은 수정 전에도 통과. 0.12 문구 단언만 P-21 전에 실패 |
| P-34 | 완료 | `7f98b3b`, `317833e` | `luacheck` 대상 경고: proc.lua 1→0, inbox_spec·jira_spec 5→0(`--no-max-line-length` 기준; 긴 줄 경고 12개는 그대로) | `server.lua:188`은 그대로 |

## 완료 기준 확인 (Python 3.14, 이 브랜치 단독)

| 기준 | 명령 | 결과 |
|---|---|---|
| 전체 시험 | `.venv\Scripts\python.exe -m pytest -q` | 2209 passed, 4 skipped, 14 deselected, 실패 0 (570초) |
| 병렬 | `-m pytest -q -n auto` | 2209 passed, 4 skipped, 실패 0 (105초) |
| perf | `-m pytest -q -m perf` | 1 passed |
| demo | `-m pytest -q -m demo` | 5 passed |
| packaging | `-m pytest -q -m packaging` | 3 passed |
| ruff | `ruff check . --exclude review` | 386 (기준 `d168f6c`도 386, 증가 없음). `review/`는 기준 커밋에 없던 폴더라 뺐다 |
| nvim 헤드리스 | `tests/test_nvim.py`(두 콘솔 모드)는 전체 시험에 포함되어 통과. Neovim spec 전체 186개 통과(Neovim UI 담당 확인) | 통과 |
| 문서 | `tests/test_docs.py`, `tests/test_repo_tree.py` 통과(설치·문서 묶음 411 passed, 1 skipped) | 통과 |

건너뜀 4개: `test_clip_image.py` 2개("이 세션에서는 클립보드를 열 수 없습니다" — 이 PC의 지금 세션에서 클립보드 접근이 거부됨. `clip_image.ps1`과 그 시험은 바꾸지 않았다. 1단계에서는 통과했다), `test_confluence_push.py:761`(심볼릭 링크 권한), `test_install_script.py:757`(nvim-config의 `nvd-rpc.lua` 없음). 1단계 대비 늘어난 2개는 환경 때문이다.

## 재현 파일 재실행 (claude 담당 항목)

| 재현 | 명령 | 결과 | 판정 |
|---|---|---|---|
| C-03 (P-05) | `pwsh -NoProfile -File review/repro/run_lua.ps1 review/repro/C-03_capture_nonatomic_write.lua` | exit 0 (가장 작은 읽기 = 전체 크기) | 고쳐짐 |
| C-07 (P-08) | `pwsh -NoProfile -File review/repro/C-07_uninstall_other_clone.ps1` | exit 0 `OK` | 고쳐짐 |
| C-09 (P-10) | `run_lua.ps1 review/repro/C-09_dotted_link.lua` | exit 0 | 고쳐짐 |
| C-12·C-13 (P-12, P-13) | `.venv\Scripts\python.exe -X utf8 review/repro/C-12_C-13_name_parity.py` | exit 0 (모두 SAME) | 고쳐짐 |
| C-14 (P-14) | `run_lua.ps1 review/repro/C-14_jira_path_link.lua` | exit 0 | 고쳐짐 |
| C-15 (P-15) | `pwsh -NoProfile -File review/repro/C-15_junction_root.ps1` | exit 0 | 고쳐짐 |
| C-16 (P-15) | `$env:WHICH='upper'; run_lua.ps1 review/repro/C-16_case_fold.lua` | exit 0 `OK` | 고쳐짐 |
| C-17 (P-16) | `run_lua.ps1 review/repro/C-17_leader.lua` | exit 0 | 고쳐짐 |
| C-18 (P-17) | `run_lua.ps1 review/repro/C-18_health.lua` | exit 0 | 고쳐짐 |
| C-33 (P-03) | `python review/repro/C-33_agent_root_first.py` | exit 0 (`false refusal: False`) | 고쳐짐 |
| C-35 (P-19) | `python review/repro/C-35_wheels_agent.py` | exit 0 (직접 실행도 7) | 고쳐짐 |
| A-01 (P-03) | `python review/repro-cross/A-01_kb_agent_flag_order.py` | exit 0 | 고쳐짐 |
| A-03 (P-02) | `nvim --headless -u tests/nvim/minimal_init.lua -l review/repro-cross/A-03_server_exit0_queue.lua` | exit 0 | 고쳐짐 |
| A-04 (P-18) | 같은 방식, `A-04_promote_cursor_shift.lua` | exit 0 (`:2` 승격) | 고쳐짐 |
| A-05 (P-15) | 같은 방식, `A-05_junction_in_vault.lua` | exit 0 (`b:work_kb_vault=true`) | 고쳐짐 |
| A-06 (P-01) | `python review/repro-cross/A-06_serve_sync_http_block.py` | exit 0 (`ping_latency=0.00s`, transitions 거부) | 고쳐짐 |
| A-07 (P-09) | `pwsh -NoProfile -File review/repro-cross/A-07_install_offline_uv_venv.ps1` | exit 0 (install.ps1 글자 검사) | 고쳐짐 |
| A-14 (P-19) | `python review/repro-cross/A-14_wheels_direct_kb_agent.py` | exit 0 | 고쳐짐 |
| C-01 (P-01) | `run_lua.ps1 review/repro/C-01_server_timeout_reruns.lua` | exit 1: `link exit=nil via=server after 2.0s; server state=dead …; next status via=process` | 계획대로 바뀜. link는 더 이상 한 번 실행으로 다시 돌지 않는다(5.6초 → 2.0초, exit nil). 서버를 죽이는 것은 K1에서 정한 설계다. 재현은 "state=dead이고 다음 요청이 process"만 보는데, 이제 재시작 제한이 없어 바로 새 서버를 띄우지만 준비 전인 그 한 요청은 한 번 실행으로 간다. 재현 판정 조건이 옛 증상 기준이라 1로 끝난다 |
| C-02 ×2 (P-04) | `python review/repro/C-02_argparse_dash_value.py`, `C-02_argparse_dash_all.py` | exit 1 (`unrecognized arguments: -foo`, `expected one argument`) | 계획대로 안 바뀜. 재현은 Python CLI에 **옛** 인수 꼴을 직접 넣는다. PLAN은 Python을 그대로 두고 Neovim이 `--옵션=값`·`--`를 보내게 했다. 새 꼴은 `tests/test_dash_args_claude.py`와 jira·search spec이 검증 |
| C-11 (P-11) | `$env:TZ='XXX12'; run_lua.ps1 review/repro/C-11_timezone.lua` | exit 1 (Lua 날짜와 `status` 날짜가 다름) | 계획대로 안 바뀜. 재현은 Lua `time.day()`와 Python `status` 날짜를 비교한다. PLAN은 Lua가 날짜에 기대는 호출(`today --check-done`)에 `--date`를 넘기게 했다(`jira_spec` "check-done passes the Lua date") |
| C-47 (P-23) | `run_lua.ps1 review/repro/C-47_search_and.lua` | exit 1 (rg 0건, kb 1건) | 계획대로 안 바뀜(K6: 구현 유지, 문서만 고침). 재현의 "USER-GUIDE says spaces are AND" 문구는 이제 낡았다 |
