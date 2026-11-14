# 상호 검증: REVIEW-agy.md (claude 워커)

대상: agy 보고서 `review/REVIEW-agy.md` (커밋 `13a2761`, 27항목 A-01~A-27). 상대 워크트리는 읽기만 했다. 판정은 커밋 `13a2761`의 글 기준이다(그 뒤 상대 작업 사본에 커밋되지 않은 심각도 정정이 생겼지만 반영하지 않았다).
재현 파일 21개를 `review/repro-cross/`로 복사해 내 워크트리(`d168f6c` 기준, 소스 동일)에서 실행했다. 반증용 시험 1개(`X-A02_lazy_restart.lua`)를 더했다.
실행: Python은 `.venv\Scripts\python.exe -X utf8 review/repro-cross/<파일>`, Lua는 `nvim --headless -u tests/nvim/minimal_init.lua -l review/repro-cross/<파일>`(XDG는 %TEMP%), PS는 `pwsh -NoProfile -File`.
심각도 기준은 코디네이터 기준이다. 높음: 데이터 손실·손상, 사람 파일 침범, 보안, 핵심 흐름 중단. 중간: 잘못된 결과, 복구 가능한 실패, 문서·요구 불일치. 낮음: 품질·정리.

## 요약

- 판정: **동의 20, 반박 2, 보완 5, 판정 불가 0** (27개).
- 재현: 상대 재현 21개 모두 내 워크트리에서 주장한 증상으로 실패했다(종료 1). 다만 A-02·A-04는 재현 스크립트가 실제로 일어나지 않는 조건을 만든다(아래).
- 심각도 조정 15개:
  - 높음→중간: A-12, A-13, A-14
  - 높음→낮음: A-01
  - 높음→해당 없음(반박): A-02
  - 중간→낮음: A-03, A-04, A-07, A-10, A-22, A-26
  - 중간→해당 없음(반박): A-08
  - 낮음→중간: A-09, A-24, A-25
- 내가 놓친 항목: **완전 누락 14개**(A-04, A-07, A-11, A-12, A-13, A-15, A-17, A-18, A-19, A-20, A-21, A-22, A-23, A-24), **부분 누락 4개**(A-03, A-16 첫째, A-25 `--yes`, A-27 나머지).
- 상대가 놓친 내 항목: 42개(아래 목록).
- 수정안 충돌: **3건**. 같은 자리를 같이 고쳐야 하는 조정 필요 4건은 따로 적었다.
- 내 보고서 정정: 17개 심각도를 다시 매겼다(높음 2, 중간 29, 낮음 26). 틀린 항목은 없었다.

## 항목 표

| 상대 ID | 내 ID(대응) | 판정 | 심각도(상대→제안) | 근거 |
|---|---|---|---|---|
| A-01 | C-33 | 동의 | 높음→낮음 | `A-01_kb_agent_flag_order.py` → `act --root <v> list exit=7`, serve `refused … proposals --root`. 재현됨. 다만 잘못 **거부**만 하고 통과시키지 않는다(`kb.py:120`). serve 거부는 한 번 실행으로 돌아가 결과가 같다(`proc.lua:674-679`). Neovim은 늘 하위 명령을 먼저 넣는다(`proc.lua:51-57` `sub_args`). 사용자 영향이 없어 낮음 |
| A-02 | — | 반박 | 높음→해당 없음 | `X-A02_lazy_restart.lua` → `before: via=server pid=25716 idle_sec=60` / `after: first via=process, second via=server pid=28864 idle_sec=10 starts=2` / `second status shows new vault: true` / `OK`. setup()이 바로 멈추지 않아도 다음 요청의 `server.available(key)`(`server.lua:352-365`)가 열쇠(root·python·idle_sec·환경) 차이를 보고 멈추고 새로 띄운다. vault는 요청마다 `--root`로 넘겨 서버 상태가 아니다(`proc.lua:43`). 상대 재현은 setup 안의 `stop` 호출 수만 센다. 오해를 부르는 것은 `init.lua:422` 주석뿐이다 |
| A-03 | C-01(관련) | 동의 | 중간→낮음 | `A-03_server_exit0_queue.lua` → `state=dead cb_code=nil can_restart=false`. `server.lua:320` 조건 그대로 재현. 대기 요청은 `kill_server→fail_pending→cb(nil)`로 한 번 실행으로 가서 결과는 맞다(`server.lua:110-127`, `proc.lua:674-679`). 손해는 60초 동안 서버 없이 도는 속도뿐이라 낮음. C-01과 같은 "죽은 뒤 60초 재시작 제한" 문제다 |
| A-04 | — | 보완 | 중간→낮음 | 재현은 `read_project`를 가짜로 바꿔 그 안에서 커서를 옮긴다(`A-04…lua:21-25`). 실제 프롬프트는 모두 동기다: `ui.input`=`vim.fn.input`, `ui.select`=`inputlist`/`input`, `ui.confirm`=`getcharstr`(`ui.lua`). 그 사이 사용자는 커서를 못 옮긴다. 남는 경로는 `run_output`의 `vim.wait` 중 다른 플러그인 콜백뿐이다. 둘째 지적(수정된 버퍼는 `[[NEW-1]]` 재읽기를 건너뜀, `fileio.lua:444-448`)은 맞다. 사용자가 저장하면 Neovim이 "file changed" 경고를 내므로 복구 가능하다. 줄 번호를 먼저 고정하는 수정안은 위생상 찬성 |
| A-05 | C-15, C-16 | 동의 | 중간→중간 | `A-05_junction_in_vault.lua` → `buf_path=…/link_vault/daily/2026-11-15.md b:work_kb_vault=nil`. 재현됨. 원인은 C-15·C-16과 같다: `init.lua:307-323`, `after/ftplugin/markdown.lua:46-50`, `jira.lua:107-117`이 실제 경로로 풀지 않는다. 세 항목은 한 헬퍼로 같이 고친다 |
| A-06 | C-01 | 동의 | 중간→중간 | `A-06_serve_sync_http_block.py` → `ping_latency=2.02s`. 재현됨. C-01은 같은 현상의 결과(30초 뒤 서버 죽임, 같은 Jira 호출을 두 번, 60초 재시작 제한)를 `C-01_server_timeout_reruns.lua`로 보였다 |
| A-07 | — | 보완 | 중간→낮음 | `A-07_install_offline_uv_venv.ps1` → `has_offline_or_no_python_downloads_in_install_ps1=False`. 플래그 누락은 맞다(`install.ps1:384-385`). 그러나 후보 반복(`install.ps1:398-409`)이 uv 실패 시 `py -3.14`·`python`으로 넘어가므로 설치가 실패하지는 않는다. 실제 위험은 (1) 폐쇄망에서 시간 초과만큼 지연, (2) 프록시가 열린 회사 PC에서 `-Offline`인데도 uv가 Python 바이너리를 내려받는 정책 문제다. `--offline --no-python-downloads` 추가에 찬성 |
| A-08 | — | 반박 | 중간→해당 없음 | (1) 고아 nvim: `nvd-rpc.lua:8-10`이 5초 뒤 스스로 `os.exit(3)`하고 `RpcWaitSeconds`는 8초(`capture.ahk:46`)라 정상적으로는 AHK 시간 초과 전에 끝난다. (2) `nvd.cmd`만 찾음: `Get-Command nvd -All` → `D:\work\nvim-config\bin\nvd.ps1`, `nvd.cmd` 둘 다 있다. install.ps1이 `.ps1`을 먼저 보일 뿐이며 capture.ahk는 `nvd.cmd`를 찾는다. 상대도 AHK를 실행하지 못해 추정으로 적었다 |
| A-09 | C-46, C-47 | 보완 | 낮음→중간 | (1) 버전 0.11/0.12 불일치 = C-46, (2) 검색 "공백 AND" = C-47(`C-47_search_and.lua` → rg 0건, kb 1건). 둘 다 동의. (3) "Neovim이 nvim 아래 after/를 자동 로드" 주장은 틀렸다: `nvim --headless --clean --cmd "set rtp+=<repo>/nvim"`에서 `after_in_rtp=false`. USER-GUIDE의 `after` 추가 안내는 맞다. 문서 불일치라 중간 |
| A-10 | C-56 | 동의 | 중간→낮음 | 시험 공백은 맞다(`tests/test_serve.py`에 `act --root … list` 없음 등). 단 A-02는 반박되어 그 시험은 필요 없다. 시험 추가는 품질이라 낮음 |
| A-11 | — | 동의 | 낮음→낮음 | 볼트 경로 판정 Lua 4곳(`init.lua:307`, `after/ftplugin/markdown.lua:27`, `jira.lua:107`, `capture.lua:255`), 하위 명령 판정 3곳(`kb.py:120`, `serve.py:75`, `proc.lua:574-580`) 중복 확인 |
| A-12 | — | 동의 | 높음→중간 | `A-12_link_topics_mentions.py` → `wiki/topics/캐시.md` mentions가 빈 채 `AssertionError`. `docs/DECISIONS.md:378` D22 "link matches explicit links and literal names or aliases of 2+ chars", §5.0 표 `mentions` 대상에 `topics/*`. 요구 불일치·잘못된 결과(주제 T3 요약 입력 비어 있음). 데이터 손실은 없어 중간 |
| A-13 | C-32(관련) | 동의 | 높음→중간 | `A-13_act_uncertain_becomes_failed.py` → `"status": "failed", "attempts": 5`. `docs/DECISIONS.md:189` "uncertain은 failed가 되지 않는다(5번째 시도도)" 위반 확인. 중복 쓰기는 사람이 `--retry-failed`를 한 번 더 해야 생기고 조건(시도 5회 이상 uncertain + 429)도 드물어 중간 |
| A-14 | C-35 | 동의 | 높음→중간 | `A-14_wheels_direct_kb_agent.py` → `returned 0 instead of raising PolicyRefused`. 내 `C-35_wheels_agent.py`도 `kb.py wheels → 7, wheels.py → 0`. wheels는 Jira·볼트에 쓰지 않고 패키지만 받는다. D57 요구 불일치라 중간 |
| A-15 | — | 동의 | 중간→중간 | `A-15_relative_ca_bundle_cwd.py` → `sync exit: 4 … ca_bundle 파일이 없습니다: certs\corp.pem`. Neovim·예약 작업은 작업 폴더가 코드 루트라 볼트를 따로 두고 상대 경로 ca_bundle을 쓰면 회사에서 그대로 걸린다 |
| A-16 | C-41, C-39 | 동의 | 중간→중간 | `A-16_ingest_stem_and_marker.py` → `shot.jpg skipped …`, `raw <!-- present in extracted: True`. (1) 확장자만 다른 두 이미지의 사이드카 충돌은 C-41(같은 이름 사용자 노트)과 같은 `ingest_images.py:221-228` 판정 문제다. (2) 마커 미중화는 C-39가 `ingest_images.clean`에도 적용하자고 한 것과 같다 |
| A-17 | C-34(관련) | 동의 | 중간→중간 | `A-17_promote_cross_project_dup.py` → `- 09:00 결제 오류 확인 [[OPS-1]] [[KB-16]]`. `jira_act.py:760-761`이 대상 프로젝트 키만 본다. 외부(Jira)에 중복 일감 생성 |
| A-18 | — | 동의 | 중간→중간 | `A-18_notes_find_keys_embed.py` → `embed matched [KeyMatch(… key='KB-12', bracketed=False)] and skipped carryover`. D20(`DECISIONS.md:59`, `:376` "strip embed `!`", 위키링크 내부 제외) 위반. 개인 할 일 이월 누락 |
| A-19 | — | 동의 | 중간→중간 | `A-19_confluence_strips_code_comments.py` → `HTML comment inside fenced/inline code was stripped`. 게시본의 코드 예시가 바뀐다 |
| A-20 | — | 동의 | 중간→중간 | `A-20_wikimarkup_placeholder_leak.py` → `'[see \x000\x00](https://example.com)'`. 캐시 파일에 NUL 바이트가 남는다 |
| A-21 | — | 동의 | 중간→중간 | `A-21_fts_read_locked.py` → `got sqlite3.OperationalError: database is locked`. 예약 sync가 쓰는 동안 검색하면 종료 6 대신 traceback·종료 1 |
| A-22 | — | 동의 | 중간→낮음 | `A-22_config_null_section_env_crash.py` → `AttributeError: 'NoneType' object has no attribute 'get'`. 잘못된 `config.local.yaml`(빈 섹션)+환경 변수가 함께 있어야 하고, 어차피 오류로 끝난다. 오류 문구·종료 코드 품질이라 낮음 |
| A-23 | — | 보완 | 중간→중간 | (1) `A-23_ingest_ledger_overwrite.py` → `ledger keys after _skip: []` 재현, 동의. (2) lint·save_local 잠금 없음: 코드 확인, 동의. (3) "atomic_write가 Windows CRT EACCES(winerror None)를 재시도 못 함"은 틀렸다: 열린 파일에 `os.replace` → `PermissionError winerror=5 errno=13`, `core.py:338-340`이 재시도한다. link 새 파일 미감지·daily_prepare FileBusy는 코드 대조만(판정 보류) |
| A-24 | C-45(같은 모듈, 다른 점) | 동의 | 낮음→중간 | `A-24_llm_detect_baseline_no_retry.py` → `saw chat calls=1`. `DECISIONS.md:250` "5xx … 2초 뒤 1회 재전송"인데 `llm_detect.py:475` 기준선만 `_post`. 요구 불일치라 중간. 둘째(`lint._fit`)는 추정이라 판정하지 않았다 |
| A-25 | C-50, C-53 | 동의 | 낮음→중간 | `A-25_init_yes_dead_flag.py` → `AST references to '.yes': []`, 빈 stdin에서 프롬프트 후 종료 1. `docs/README.md:83`에 적힌 옵션이 동작하지 않는다(문서 불일치). DECISIONS의 Emacs 잔재는 C-50과 같다 |
| A-26 | C-56, C-54 | 동의 | 중간→낮음 | `doctor.py` 76%, Neovim 점검 미실행은 내 커버리지 결과와 같다. 시험 공백은 품질이라 낮음 |
| A-27 | C-21 | 보완 | 낮음→낮음 | (2)의 `section_span` 코드 펜스 문제는 C-21과 같고 이월 누락(잘못된 결과)이라 중간으로 따로 본다(`C-21_heading_in_fence.py`). 나머지(`page_template` safe_name, `parse_table` 끝 열, `read_view` 디코드, gave_up 유령 경고, 단일 스레드 전제)는 코드로 확인했고 품질 수준이라 낮음 유지 |

## 상대만 찾은 항목 (내가 놓친 이유)

- A-04: Lua 승격 흐름을 읽었지만 비동기 실행 뒤 버퍼 재읽기 조건(`modified`)은 따라가지 않았다.
- A-07: install 시험을 이음매로 돌렸지만 `-Offline` 경로의 uv 인수는 대조하지 않았다.
- A-11: 중복은 품질 항목이라 따로 적지 않았다(C-15·C-16 수정안에서 한 헬퍼를 권하는 데 그쳤다).
- A-12: §6.1 link 대상(jira·projects·people)만 보고, D22·§5.0 표의 `topics` 요구와 대조하지 않았다.
- A-13: outbox 상태 시험은 status 표시(C-32)에 머물렀고 `--retry-uncertain` + 429 조합은 보지 않았다.
- A-15: 연결 설정은 공개 호스트 차단 위주로 봤고, 상대 경로 `ca_bundle`의 작업 폴더 의존은 보지 않았다.
- A-17: 승격 중복은 done·linked 경로(C-34)만 봤고 다른 프로젝트 키는 보지 않았다.
- A-18: D20의 임베드·위키링크 제외 규칙을 link에만 대조하고 `daily_prepare`의 `notes.find_keys` 직접 호출은 놓쳤다.
- A-19: Confluence 변환은 링크·이미지 거르기(C-42)만 봤고 주석 제거 순서는 보지 않았다.
- A-20: wikimarkup은 커버리지 93%로 보고 넘겼고 중첩 자리표를 시험하지 않았다.
- A-21: FTS는 bandit SQL 경고만 확인했고 잠금 중 읽기 경로는 시험하지 않았다.
- A-22: 설정 병합은 hypothesis로 순서만 봤고 비-dict 섹션은 생성기에 넣지 않았다.
- A-23: 동시 실행은 serve와 link(잠금 안)만 봤고 ingest 원장의 잠금 밖 읽기는 보지 않았다.
- A-24: 탐지는 호출 수 상한(C-45)만 대조했고 후보별 재시도 규칙은 대조하지 않았다.
- 부분 누락: A-03(C-01에서 시간 초과 경로만 봄), A-16 첫째(사용자 노트 충돌만 봄), A-25 `init --yes`(문서 쪽만 봄), A-27 나머지 품질 항목.

## 내가 찾았는데 상대가 놓친 항목

C-02(`-` 시작 입력, `-h` 거짓 성공), C-03(캡처 비원자 쓰기, 0바이트 읽힘), C-04(서버가 옛 코드로 계속 실행), C-05(AGENTS·serve 문서 "읽기 전용"), C-06(응답 버퍼 제곱 비용), C-07(다른 클론 uninstall), C-08(클립보드 임시 파일 위치), C-09(점 있는 링크), C-10(status --json에 project_keys 없음), C-11(시간대), C-12·C-13(Lua/Python 이름 규칙 차이), C-14(jira/ 경로 링크 동기화 생략), C-17(mapleader 옛 키), C-18(checkhealth 경고), C-19(닫히지 않은 `---`로 `## 기록` 반복 추가), C-20(4-백틱 코드 정규화), C-22(유니코드 숫자 키), C-23(NFD `@이름`), C-24(CR 전용 관리 구역), C-25(`\r\r\n` front matter 중복), C-26(NFD 제목), C-27(환경 변수 대괄호 → 목록), C-28(NEL), C-29(safe_name 81자), C-30(403 하나로 전체 sync 중단), C-31(전환 오프라인 큐 없음), C-32(status의 uncertain 표시), C-36(페이지 밀림, 추정), C-37(`</think>` 리터럴), C-38(대문자 think), C-40(참조 중인 inbox 이미지 이동), C-42(Confluence 위험 링크), C-43(IP 호스트), C-44(스키마 재시도 동일 요청), C-45(탐지 호출 수), C-48(KB_ROOT 무시), C-49(접두 키 문서 누락), C-51(DEPLOY §9 "그대로"), C-52("Python 같다"), C-55(시험의 pip 의존), C-57(정적 분석 잔여). 합계 42개(일부 겹치는 C-01·C-15·C-16·C-21·C-33·C-34·C-35·C-39·C-41·C-46·C-47·C-50·C-53·C-54·C-56은 제외).

## 수정안 충돌 목록

1. **serve 시간 초과 방향 (A-06 ↔ C-01)**: A-06은 `JiraClient` 시간 초과를 `request_timeout_ms`보다 짧게 하자고 하고, C-01은 요청 시간 초과를 잠금 대기 이상으로 늘리거나 하트비트를 두자고 한다. 같은 상수(`server.lua:14`, `jira_client.py:323`)를 반대로 움직인다. 조정안: 둘 다 동의하는 "`act transitions`를 SERVED에서 뺀다"를 먼저 하고, 남는 잠금 명령(`today`·`link`)은 C-01처럼 시간 초과 때 죽이고 다시 돌리지 않게 한다. Jira 시간 초과는 그대로 둔다.
2. **추출·모델 글의 HTML 주석 처리 (A-16 둘째 ↔ C-39)**: A-16은 `<!--`를 `&lt;!--`로 바꾸자 하고, C-39는 바뀌지 않을 때까지 지우거나 `<`·`>`를 지우자고 한다. 같은 `ingest_images.clean`을 다르게 고친다. 조정안: `lib`에 한 함수를 두고 compile·ingest가 같이 쓴다. Obsidian 표시 그대로를 원하면 이스케이프, 평문이 목표면 반복 제거다. 하나로 정해야 한다.
3. **USER-GUIDE의 `rtp` 안내 (A-09 셋째 ↔ 현재 문서 유지)**: A-09는 `"<repo>/nvim/after"` 추가 안내를 지우자 하지만, 실험상 수동 `rtp+=`는 `after`를 넣지 않는다(`after_in_rtp=false`). 지우면 ftplugin이 적용되지 않는다. 안내는 유지해야 한다.

같은 자리를 같이 고쳐야 하는 조정 필요(충돌은 아님):
- 하위 명령 판정 공용 함수(A-01·A-11 ↔ C-33)와 C-02의 `--` 삽입: 공용 함수는 `--`에서 멈춰야 한다.
- `server.lua` `on_exit`·`kill_server`(A-03 ↔ C-01): 정상 종료 판정과 시간 초과 처리를 한 번에 고친다.
- 볼트 경로 실제 경로 비교(A-05·A-11 ↔ C-15·C-16): `config.lua`에 헬퍼 하나로 모은다.
- `cmd_promote` 중복 검사(A-17 ↔ C-34): 모든 프로젝트 키의 끝 링크와 done·`linked:false` 기록을 함께 본다.

## 정정한 내 항목 목록

`review/REVIEW-claude.md` 제목 줄에 `[정정: 이전→이후]`로 표시했다. 틀린 항목은 없었고 모두 심각도만 바꿨다.

| 내 ID | 이전→이후 | 이유 |
|---|---|---|
| C-03 | 중간→높음 | 캡처 중 죽거나 디스크가 차면 daily 전체 손실, 사람 파일 |
| C-19 | 중간→높음 | 실행할 때마다 daily(사람 파일)에 `## 기록`을 덧붙여 손상 |
| C-07 | 낮음→중간 | 다른 클론의 설정을 지워 플러그인이 안 불림(잘못된 결과) |
| C-15 | 낮음→중간 | 정션 설치는 코드가 예상하는 배치다(`config.lua:62-63` 주석). 승격 거부·`--body` 오송신 |
| C-20 | 낮음→중간 | daily 코드 블록 안을 고침(허용 예외 범위 밖 수정) |
| C-21 | 낮음→중간 | 개인 할 일 이월 누락(잘못된 결과). A-27과 같은 자리 |
| C-26 | 낮음→중간 | daily에 제목 중복 추가(사람 파일). 입력이 특이해 높음까진 아님 |
| C-27 | 낮음→중간 | 기본 제목 꼴 `"[KB] "`을 환경 변수로 주면 그대로 깨짐 |
| C-32 | 낮음→중간 | 사람 판단이 필요한 레코드를 "전송 중"으로 잘못 보임 |
| C-35 | 낮음→중간 | D57 요구 불일치(A-14와 같음) |
| C-41 | 낮음→중간 | 이미지가 영영 처리되지 않고 status도 0(A-16과 같은 판정 문제) |
| C-42 | 낮음→중간 | 외부 비컨·javascript 링크 게시 가능성(보안 관련, Confluence 쪽 동작은 추정이라 높음 아님) |
| C-45 | 낮음→중간 | §6.11 "10회 이하" 요구 불일치 |
| C-47 | 낮음→중간 | 문서 불일치 |
| C-51 | 낮음→중간 | 문서·요구(§9 "그대로") 불일치 |
| C-52 | 낮음→중간 | 문서 불일치 |
| C-53 | 낮음→중간 | 문서 불일치 |

정정 뒤 집계: 높음 2, 중간 29, 낮음 26 (전 0/16/41). 특이 입력에서만 생기는 문제(C-22·C-23·C-24·C-25·C-28·C-29)와 발생 조건이 사실상 없는 C-11은 낮음으로 두었다.
