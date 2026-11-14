# 1단계-2 상호 검증 보고서 (agy → claude)

상대 보고서 `REVIEW-claude.md`(커밋 `0acd13d`, 57개 항목 `C-01`~`C-57`)와 재현 스크립트 37개 + Hypothesis 31개 시험을 내 워크트리의 `review/repro-cross/`로 복사해 독립 재실행·코드 대조로 검증했다.

## 요약

- **판정별 개수 (총 57건)**: **동의 46건**, **보완 11건**(`C-01`, `C-07`, `C-08`, `C-15`, `C-33`, `C-39`, `C-41`, `C-45`, `C-50`, `C-53`, `C-57`), **반박 0건**, **판정 불가 0건**
  - 재현 검증: `review/repro-cross/C-*` 37개 실행에서 비교용 `C-09_lint_compare.py`(exit 0) 외 **36개 모두 exit 1로 버그 재현**, `hypothesis` 스위트 **10 failed, 21 passed** (`C-22`·`C-23`·`C-24`·`C-25`·`C-28`·`C-29` 반례 확인)
- **심각도를 조정한 목록 (12건)**:
  - `중간 → 높음` (**2건**): `C-03`(daily 비원자적 제자리 덮어쓰기로 사람 파일 데이터 손실 위험), `C-30`(일감 1건 403으로 전체 `jira sync` 핵심 흐름 영구 중단)
  - `낮음 → 중간` (**10건**): `C-07`(활성 클론의 `WORK_KB_ROOT` 삭제), `C-11`(시간대 불일치로 다른 날짜 `daily` 기록), `C-15`(정션 환경 `promote`/댓글 실패), `C-20`(4-백틱 코드 블록 내 키 정규화로 §10 규칙 위반), `C-21`(`## ` 펜스 뒤 개인 할 일 이월 누락), `C-33`(`act --root ... list` 과잉 거부), `C-34`(승격 중복 생성 가능성), `C-35`(D57 요구사항 불일치), `C-47`(`USER-GUIDE` 검색 규칙 불일치), `C-53`(캡처·`G`·Lua 경로·`status` 문서 불일치)
- **내가 놓친 항목 수**: **45건** (`C-01`~`C-57` 중 직접·부분 대응 12건 제외; 대응 항목 중에서도 `C-01` 잠금 60s·재실행, `C-39` `compile` 우회, `C-50` `DECISIONS` ch.6 우선순위 누락 등은 추가로 놓친 부분 존재)
- **상대가 놓친 내 항목 수**: 단독 발견 **17건** + 부분 누락 **4건** (`A-02`~`A-04`, `A-07`, `A-08`, `A-11`~`A-13`, `A-15`, `A-17`~`A-24` 등)
- **수정안 충돌 수**: **5건** · **정정한 내 항목 수**: **9건** (`REVIEW-agy.md`에 반영 완료)

---

## 항목별 검증 표

| 상대 ID | 내 ID(대응) | 판정 | 심각도(상대→제안) | 근거 |
|---|---|---|---|---|
| `C-01` | `A-06` (부분) | 보완 | 중간→중간 | `C-01_server_timeout_reruns.lua` exit=1 (`link exit=0 via=process after 5.6s; server state=dead`). 30초 타임아웃 vs `kb_lock` 60초·중복 재실행 지적 맞으나, `act transitions`를 `SERVED`에서 빼는 편(`A-06`)이 하트비트 확장보다 단순함 |
| `C-02` | `-` | 동의 | 중간→중간 | `C-02_argparse_dash_all.py`·`C-02_argparse_dash_value.py` 모두 exit=1 (`act assign KB-12 -hong -> exit 0` 도움말만 출력). 놓친 이유: `-` 접두 사용자 입력에 대한 `argparse` 모호성 퍼징 미수행 |
| `C-03` | `-` | 동의 | 중간→**높음** | `C-03_capture_nonatomic_write.lua` exit=1 (`smallest size a concurrent reader saw=0`), `fileio.lua:62-72` `"w"` 절단 쓰기 확인. 사람 소유 `daily/` 전체 손실 위험으로 높음 상향. 놓친 이유: Python `atomic_write`만 보고 Lua `write_bytes` 누락 |
| `C-04` | `-` | 동의 | 낮음→낮음 | `server.lua:85-92` `M.key`에 `scripts/` 버전/mtime 없음 확인. 놓친 이유: `git pull` 후 장기 실행 `nvd` 데몬의 코드 불일치 미점검 |
| `C-05` | `-` | 동의 | 중간→중간 | `AGENTS.md:118,134`, `serve.py:41-48` (`today`·`link` 쓰기, `act transitions` 네트워크 호출), `docs/README.md:79,99` 대조 확인. 놓친 이유: `AGENTS.md`의 "빠른 읽기" 문구와 `SERVED` 기능 대조 누락 |
| `C-06` | `-` | 동의 | 낮음→낮음 | `server.lua:192-207` 청크마다 `S.linebuf .. data` + `find("\n")` 반복 확인. 놓친 이유: 대용량 응답 청크 누적 복잡도 미점검 |
| `C-07` | `-` | 보완 | 낮음→**중간** | `C-07_uninstall_other_clone.ps1` exit=1 (`WORK_KB_ROOT after A uninstall: (removed)`). `uninstall.ps1:169` 외에 `install.ps1:474,481`이 B 클론 설치 시 기존 값을 `$ours`로 보고 `hadPrevious=false`를 승계·덮어쓰는 점이 원인 보완. 활성 클론 로딩 깨짐으로 중간 상향. 놓친 이유: 다중 클론 순차 설치·제거 시나리오 미시험 |
| `C-08` | `-` | 보완 | 낮음→낮음 | `capture.lua:348,381-383` `.state/tmp/`가 `config.root()` 아래 생성됨. 단 `cwd=root`에 순수 ASCII 상대 경로 `-Out`을 넘겨 `clip_image.ps1` 한글 경로 문제를 피하려던 의도가 있으므로 `cwd=vault` 전환 시 스크립트 절대 경로 인코딩 처리 필요. 놓친 이유: 분리 볼트 시 임시 파일 위치 미점검 |
| `C-09` | `-` | 동의 | 중간→중간 | `C-09_dotted_link.lua` exit=1 (`resolve [[Node.js]] -> nil`), `C-09_lint_compare.py` exit=0 (`['wiki/topics/Node.js.md']`). `open.lua:358-361` 확인. 놓친 이유: 점(`.`) 포함 노트명 위키링크 해석 미시험 |
| `C-10` | `-` | 동의 | 중간→중간 | `C-10_project_keys.lua` exit=1 (`has project_keys: false`), `status.py:311-333`에 `project_keys` 없음 확인. 놓친 이유: `jira_spec.lua` 목업 JSON에 가려져 실 `status --json` 키 대조 누락 |
| `C-11` | `-` | 동의 | 낮음→**중간** | `$env:TZ='XXX12'` `C-11_timezone.lua` exit=1 (`lua 2026-11-14` vs `python 2026-11-15`). 다른 날짜 `daily/` 기록 오류이므로 중간 상향. 놓친 이유: 로컬 OS 시간대가 `Asia/Seoul`이라 시간대 차이 미시험 |
| `C-12` | `-` | 동의 | 낮음→낮음 | `C-12_C-13_name_parity.py` exit=1 (`safe_name` 7건 차이, `names.lua:157,166`). 놓친 이유: Python `core.safe_name`과 Lua `names.safe_name` 유니코드 공백 차분 비교 미수행 |
| `C-13` | `-` | 동의 | 낮음→낮음 | `C-12_C-13_name_parity.py` exit=1 (`@Nguyễn`, `@김철수µ`, `@김×철수` 불일치, `open.lua:100-112`). 놓친 이유: Lua `name_char`와 Python `_PERSON_CHARS` 차분 비교 미수행 |
| `C-14` | `-` | 동의 | 낮음→낮음 | `C-14_jira_path_link.lua` exit=1 (`[[jira/KB-12]] -> spawned kb.py: none`, `open.lua:220-223,518`). 놓친 이유: 경로·확장자 붙은 Jira 위키링크 stub 동기화 여부 미시험 |
| `C-15` | `A-05` (부분) | 보완 | 낮음→**중간** | `C-15_junction_root.ps1` exit=1 (`jira.daily_relative -> nil`). `jira.lua:107-117` 외에 `init.lua`·`after/ftplugin/markdown.lua`·`capture.lua`의 `vault` 정션 처리(`A-05`)도 함께 묶어야 함. 정션 환경 기능 중단으로 중간 상향 |
| `C-16` | `-` | 동의 | 낮음→낮음 | `$env:WHICH='upper'` `C-16_case_fold.lua` exit=1 (`JÖRG NOTES -> nil`, `shiftwidth: 4`). 놓친 이유: 비ASCII 대소문자 NTFS 경로 차이 미시험 |
| `C-17` | `-` | 동의 | 낮음→낮음 | `C-17_leader.lua` exit=1 (`after prefix=false, ',kc' still mapped: true`, `init.lua:128-147`). 놓친 이유: `mapleader` 변경 후 `setup()` 재호출 미시험 |
| `C-18` | `-` | 동의 | 낮음→낮음 | `C-18_health.lua` exit=1 (`WARNING 쓰지 못함 (stopped)`, `health.lua:81-90`). 놓친 이유: 정상 `stopped` 상태의 `:checkhealth` 등급 점검 누락 |
| `C-19` | `-` | 동의 | 중간→중간 | `C-19_skeleton_leading_hr.py` exit=1 (`## 기록` 2→3→4개), `C-19_unclosed_frontmatter_all_code.py` exit=1 (`in_code=True`). 놓친 이유: 닫히지 않은 선두 `---` 구분선 입력 미시험 |
| `C-20` | `-` | 동의 | 낮음→**중간** | `C-20_link_fence4.py` exit=1 (4-백틱 블록 내 `[[KB-1]]` 정규화, `notes.py:23,122-129`). §5.1·§10 `daily` 코드 블록 불변 위반으로 중간 상향. 놓친 이유: 4-백틱 중첩 울타리 미시험 |
| `C-21` | `A-27` (부분) | 동의 | 낮음→**중간** | `C-21_heading_in_fence.py` exit=1 (`할 일 B` 이월 누락, `notes.py:147-173`). 나도 `A-27(2)`에서 같은 원인을 잡았으나 이월 누락(`중간`) 영향까지 확인하지 못했음 |
| `C-22` | `-` | 동의 | 낮음→낮음 | `C-22_unicode_digits.py` exit=1 + `test_prop_link.py` 실패 (`KB-１２` 정규화·`KB-١KB-1` 비멱등). 놓친 이유: 키 정규식에 전각/유니코드 숫자 입력 미시험 |
| `C-23` | `-` | 동의 | 낮음→낮음 | `C-23_people_nfd.py` exit=1 + `test_prop_link.py` 실패 (`NFD: []`). 놓친 이유: NFD 자모 `@이름` 입력 미시험 |
| `C-24` | `-` | 동의 | 낮음→낮음 | `C-24_managed_cr_only.py` exit=1 + `test_prop_managed.py` 실패 (`CR` 전용 파일 비멱등). 놓친 이유: 구 Mac CR-only 줄끝 미시험 |
| `C-25` | `-` | 동의 | 낮음→낮음 | `C-25_frontmatter_crcrlf.py` exit=1 (이중 front matter 생성, `frontmatter.py:17`). 놓친 이유: `\r\r\n` 변칙 줄끝 front matter 미시험 |
| `C-26` | `-` | 동의 | 낮음→낮음 | `C-26_nfd_heading.py` exit=1 (`section_span(NFD) -> None`, `## 기록` 2개). 놓친 이유: NFD 제목 파일 미시험 |
| `C-27` | `-` | 동의 | 낮음→낮음 | `C-27_env_override_list.py` exit=1 (`"[TEAM] "` → `['TEAM']`, `config.py:401-410`). 놓친 이유: `[`로 시작하는 문자열 환경변수 덮어쓰기 미시험 |
| `C-28` | `-` | 동의 | 낮음→낮음 | `C-28_frontmatter_nel.py` exit=1 (`배포\x85체크` → `배포 체크`, `frontmatter.py:80-89`). 놓친 이유: U+0085(NEL) 포함 front matter 왕복 미시험 |
| `C-29` | `-` | 동의 | 낮음→낮음 | `C-29_safe_name_len.py` exit=1 + `test_prop_core.py` 실패 (80자 예약어 → 81자 비멱등, `core.py:467-471`). 놓친 이유: 80자 경계 예약 이름 퍼징 미수행 |
| `C-30` | `-` | 동의 | 중간→**높음** | `C-30_sync_403_out_of_scope.py` exit=1 (`403` 일감 1건으로 `sync --full` exit 2 중단, `jira_sync.py:739-743`). 24시간 이후 모든 `sync` 핵심 흐름 영구 중단으로 높음 상향(단 실 DC 403 반환 여부는 추정). 놓친 이유: `sync` out-of-scope 루프의 403 예외 분기 미확인 |
| `C-31` | `-` | 동의 | 중간→중간 | `C-31_transition_offline.py` exit=1 (`act transition -> exit 3, queued: False`, `jira_act.py:666-685`). 놓친 이유: 오프라인 상태의 `transition` 큐잉 여부 미시험 |
| `C-32` | `-` | 동의 | 낮음→낮음 | `C-32_status_sending.py` exit=1 (`list=uncertain`, `status=전송 중 1개`, `status.py:92-117,356`). 놓친 이유: 낡은 `sending` 레코드에 대한 `act list` vs `status` 불일치 미대조 |
| `C-33` | `A-01` | 보완 | 낮음→**중간** | `C-33_agent_root_first.py` exit=1 + `A-01` 재현 확인. `proposals --root V list`와 `proc.lua:574-580`(`args[2]`)도 동일 결함이며, `jira_act.main` 위임만으로는 `proposals`가 빠져 공통 파서 필요. 과잉 거부지만 복구 가능한 실패이므로 중간(`A-01` 정정과 일치) |
| `C-34` | `-` | 동의 | 낮음→**중간** | `jira_act.py:578-580, 763-770` 코드 직접 확인(`link_promoted` 잠금/`outbox.update` 미보호, `cmd_promote` 중복 검사가 `done` 제외). Jira 중복 일감·남은 flush 중단 유발 가능하므로 중간 상향(추정). 놓친 이유: 승격 완료 직후 `_after_done` `FileBusy` 경로 미추적 |
| `C-35` | `A-14` | 동의 | 낮음→**중간** | `C-35_wheels_agent.py` exit=1 + `A-14` 재현(`wheels.py:222` `refuse_if_agent` 누락). D57 요구사항 불일치로 중간 제안(`A-14`도 높음→중간 정정) |
| `C-36` | `-` | 동의 | 낮음→낮음 | `jira_client.py:489-505` 페이지마다 동일 상대 JQL 재요청 확인(추정). 놓친 이유: 500건 초과 페이징 경계 시간 이동 미검토 |
| `C-37` | `-` | 동의 | 중간→중간 | `C-37_think_and_hosts_probe.py` exit=1 (`</think>` 인용 시 JSON 절단, `llm_client.py:177-182`). 놓친 이유: 본문/JSON 내 리터럴 `</think>` 등장 케이스 미시험 |
| `C-38` | `-` | 동의 | 낮음→낮음 | `C-37_think_and_hosts_probe.py` exit=1 (`<THINK>`·`<thinking>` 미제거). 놓친 이유: 변형 think 태그 케이스 미시험 |
| `C-39` | `A-16` (부분) | 보완 | 중간→중간 | `C-39_one_line_comment.py` exit=1 (`<!<!---- src:X ---->>` → `<!-- src:X -->`, `compile.py:487`). `A-16`에서 `ingest_images` 원시 `<!--`만 잡고 `compile._one_line` 1회 치환 재조립을 놓쳤음. `ingest_images`까지 함께 중화해야 함 |
| `C-40` | `-` | 동의 | 중간→중간 | `C-40_inbox_links.py` exit=1 (`inbox/현장 메모.md`·`wiki/topics/배포.md` 내 `![[사진.png]]` 링크 깨짐, `ingest_images.py:156-193,223`). 놓친 이유: `scan_daily`가 `inbox/`·`wiki/` 참조를 보는지 미확인 |
| `C-41` | `A-16` (부분) | 보완 | 낮음→낮음 | `C-40_inbox_links.py` exit=1 (`inbox/회의.md` 존재 시 `회의.png` 영구 스킵). `ingest_images.py:221`·`status.py:176` 수정 시 `A-16`의 다른 확장자(`shot.png`·`shot.jpg`) stem 충돌도 동시 해결 필요 |
| `C-42` | `-` | 동의 | 낮음→낮음 | `C-42_storage_links.py` exit=1 (`javascript:`·외부 `https://` `<img>` 그대로 출력, `confluence_push.py:446-473`). 놓친 이유: storage 변환 시 Markdown 링크/이미지 스킴 필터 미점검 |
| `C-43` | `-` | 동의 | 낮음→낮음 | `C-37_think_and_hosts_probe.py` (`104.18.0.1` → `False`) 및 `config.py:157-173` 확인(D44 도메인 차단 규격상 개선). 놓친 이유: 공개 IP 리터럴 입력 미점검 |
| `C-44` | `-` | 동의 | 낮음→낮음 | `llm_client.py:752-788` `round_no == 2` 재시도 시 `messages`에 검증 오류를 붙이지 않음 확인(추정). 놓친 이유: 스키마 재시도 프롬프트 개선 여지 미점검 |
| `C-45` | `-` | 보완 | 낮음→낮음 | `llm_detect.py:410,736`, `DECISIONS.md:223,250`, `llm_client.py:506,526` 확인. `DECISIONS`와 `llm_detect.py:410`은 의도적으로 "POST ≤ 10"으로 재정의하고 `/models`를 제외했으나, `detect_context`가 `_no_429_retry()`를 쓰지 않아 429 시 GET 3회 호출 가능. 놓친 이유: `GET /models`의 `MAX_429` 재시도 미추적 |
| `C-46` | `A-09` (1) | 동의 | 중간→중간 | `README.md:19`, `DEPLOY.md:14`, `install.ps1:75`, `doctor.py:1136-1139` (0.11) vs `health.lua:11-14`, `init.lua:1` (0.12) 직접 확인 (`A-09`도 중간으로 상향) |
| `C-47` | `A-09` (2) | 동의 | 낮음→**중간** | `C-47_search_and.lua` exit=1 (`rg 'front 캐시' -> 0건`, `kb -> 1건`, `USER-GUIDE.md:219`). 문서·요구사항 불일치로 중간 상향(`A-09`와 동일) |
| `C-48` | `-` | 동의 | 중간→중간 | `proc.lua:43,52`가 항상 `--root config().vault()`를 넘겨 `KB_ROOT` 무시 확인(`USER-GUIDE.md:92`, `docs/README.md:45`). 놓친 이유: `KB_ROOT` 안내와 `proc.kb_args`의 `--root` 강제 전달 대조 누락 |
| `C-49` | `-` | 동의 | 중간→중간 | `USER-GUIDE.md:229`, `DEPLOY.md:117-129`, `work-kb-build-brief-v3.md` §6.8 대조 확인(`setup({prefix=...})`, `rebind_prefix`, `call()` 안내 누락). 놓친 이유: §6.8의 "접두 키 한 줄 교체 두 방법" 및 §9 step 7 원문 요소 누락 점검 부족 |
| `C-50` | `A-25` (3) | 보완 | 중간→중간 | `DECISIONS.md:3,9-43` 및 `config.py:77` 확인. 상대 지적(ch.6 우선순위·§1 표·`KB_SKIP_EMACS`)이 더 포괄적이며 `A-25`의 `init --yes`·`AGENTS.md`·`prompts/image_ingest.md`와 합쳐 정리 필요 |
| `C-51` | `-` | 동의 | 낮음→낮음 | `DEPLOY.md:4,117`, `tests/test_docs.py:5,38`, `DECISIONS.md:277` "원문 그대로" vs 7단계 Neovim 개작 확인. 놓친 이유: DEPLOY 인용문 문구 정합성 세부 미점검 |
| `C-52` | `-` | 동의 | 낮음→낮음 | `README.md:9`, `USER-GUIDE.md:61` "Python 쪽은 두 브랜치가 같다" vs `git diff main...HEAD -- scripts` 차이 확인. 놓친 이유: 양 브랜치 Python 동일 문구 미검증 |
| `C-53` | `-` | 보완 | 낮음→**중간** | `capture.lua:104-160`, `USER-GUIDE.md:171-172,214,225`, `AGENTS.md:123`, `status.py` 확인. 단 `5.4절` 예시는 `"<repo>/nvim"`이라 리터럴 자체에 역슬래시는 없고 `D:\...` 치환 시 발생하는 함정임. 기능 오안내 4건으로 중간 상향. 놓친 이유: 키 표 문구와 실제 버퍼·LLM 호출·`status` 네트워크 유무 대조 누락 |
| `C-54` | `A-26` (2) | 동의 | 낮음→낮음 | `tests/test_docs.py:27-29,218-220`, `tests/test_repo_tree.py:73` 확인(`README.md`·`USER-GUIDE`·`PACKAGING` 누락) |
| `C-55` | `-` | 동의 | 낮음→낮음 | `.venv313` 초기 실행에서 같은 5건 실패 후 `pip` 시드로 5 passed 재현 확인(`wheels.py:253`, `test_packaging.py:192`). 놓친 이유: 결과 표에만 적고 테스트 하네스 결함 항목으로 분리하지 않음 |
| `C-56` | `A-10`, `A-26` | 동의 | 낮음→낮음 | 분기 커버리지 `87%`(stmts miss `1561`, branch partial `604`) 및 `jira_act.py:559-565`·`core.py:336-340`·`net.py:217-224`·`outbox.py:269-274` 미테스트 확인 |
| `C-57` | `-` | 보완 | 낮음→낮음 | `wikimarkup.py:146`, `lint.py:419-420,1059`, `proc.lua:515` 미사용 확인. 단 `schedule.py:89` `Planned.arguments`는 `tests/test_schedule.py:282`가 사용하므로 죽은 코드가 아니며, `server.lua:188`도 의도된 폴백 분기임. 놓친 이유: `vulture`/`selene` 결과를 별도 리포트 항목으로 올리지 않음 |

---

## 상대가 놓친 내 항목 목록

1. **단독 발견 (17건)**:
   - `A-02` (`init.lua:424` `server.enable=true` 설정 변경 시 `setup()` 즉시 `server.stop()` 미호출)
   - `A-03` (`server.lua:320` 유휴 `exit 0` 종료 순간 `inflight`/`queue`가 있으면 `dead` 판정 및 60초 재시작 차단)
   - `A-04` (`jira.lua:575-595` `promote` 확인 후 비동기 실행 전 커서 이동 시 다른 줄 승격 + `modified` 버퍼 재읽기 생략)
   - `A-07` (`install.ps1 -Offline`에서도 `uv venv`에 `--offline`·`--no-python-downloads` 미전달)
   - `A-08` (`capture.ahk` `cmd.exe` 타임아웃 시 자식 `nvim.exe` 고아 가능성 및 `.ps1` 미탐지, 추정)
   - `A-11` (볼트 경로 판정 4곳·2단어 하위 명령 판정 3곳 중복)
   - `A-12` (`link.py` `MANAGED_DIRS`·`_extract`·`_targets`에서 `wiki/topics/*.md` `mentions` 연결 전면 누락 — D22·`compile` T3 연계 결함)
   - `A-13` (`jira_act.py:485-487` `--retry-uncertain`으로 `attempts >= 5` 레코드 재시도 시 429에서 `failed`로 전락 — D52 위반)
   - `A-15` (`JiraClient`·`ConfluenceClient`의 상대 `ca_bundle`이 `cfg.config_dir`이 아닌 CWD 기준으로 해석)
   - `A-17` (`jira_act.cmd_promote`가 대상 `project` 키만 정규식 검사해 `[[OPS-1]]` 줄을 `KB`로 중복 승격)
   - `A-18` (`notes.find_keys`가 이미지 임베드 `![[raw/assets/KB-12.png]]` 안 키를 매칭해 `daily_prepare` 개인 할 일 이월 누락)
   - `A-19` (`confluence_push.prepare`가 코드 블록 보호 전에 HTML 주석을 제거해 코드 예시 내 주석 손실)
   - `A-20` (`wikimarkup` 중첩 자리표 `\x000\x00` NUL 바이트 잔존 및 `{quote}` 내 여러 줄 `{code}` 인용 누락)
   - `A-21` (`fts.py` 읽기 메서드가 SQLite 잠금 오류를 `FileBusy`(종료 6) 대신 raw `OperationalError`(종료 1)로 누수)
   - `A-22` (`config.py` `None` 섹션 + 환경변수 덮어쓰기 시 `ConfigError`(종료 4) 대신 `AttributeError`(종료 1) 충돌)
   - `A-23` (`ingest_images` 원장 잠금 밖 읽기 덮어쓰기, `lint._write`/`config.save_local` 무잠금 쓰기 등 동시성·잠금 결함)
   - `A-24` (`llm_detect` 기준선 `_post`의 429/5xx 재시도 누락 및 `lint._fit` `items[:2]` 예산 초과 추정)
2. **부분 누락 (4건)**: `A-09(3)`(`USER-GUIDE.md:175` 불필요한 `nvim/after` `rtp` 추가 안내), `A-16`(`shot.png`·`shot.jpg` stem 충돌 및 `ingest_images` 내 원시 `<!--` 미이스케이프), `A-25`(`init --yes` dead flag 등), `A-27`(`notes.page_template` `safe_name`, `parse_table` `strip("|")`, `read_view`, `status`/`lint` 유령 `gave_up`)

---

## 수정안 충돌 목록 (5건)

1. **`scripts/serve.py`, `nvim/lua/work-kb/server.lua`, `nvim/lua/work-kb/proc.lua` (`A-06` vs `C-01`, `C-05`)**:
   - `A-06`은 Jira 네트워크 호출이 있는 `act transitions`를 `SERVED`에서 빼고 비동기 단발 프로세스로 돌리자고 제안(`C-05` 문서 수정안도 `act transitions` 잔류 전제).
   - `C-01`은 `SERVED` 유지 + 하트비트 `{"id":n,"busy":true}` 또는 90초 타임아웃 연장을 제안(타임아웃 시 서버 종료/재실행 중단).
2. **`scripts/kb.py`, `scripts/serve.py` (`A-01` vs `C-33`)**:
   - `A-01`은 옵션 인자(`--root <값>` 등)를 건너뛰고 첫 위치 인자(하위 명령)를 추출하는 공통 파서를 `kb.py`·`serve.py`·`proc.lua`에 적용하자고 제안(`proposals --root V list` 포함 해결).
   - `C-33`은 `--root` 건너뛰기 외에 `act` 판정을 `jira_act.main`에 위임하는 대안도 제시했는데, 위임안은 `proposals --root V list`와 `serve` 판정을 해결하지 못함.
3. **`scripts/ingest_images.py` (`A-16(2)` vs `C-39`)**:
   - `A-16(2)`는 `extracted`·사이드카 출력에서 `&lt;!--` 엔티티 이스케이프를 제안.
   - `C-39`는 `compile._one_line`과 `ingest_images.clean` 모두에서 `<`·`>` 제거 또는 변화 없을 때까지 반복 제거를 제안.
4. **`scripts/ingest_images.py`, `scripts/status.py` (`A-16(1)` vs `C-41`)**:
   - `A-16(1)`은 다른 확장자 충돌(`shot.png`·`shot.jpg`) 방지를 위해 `sidecar_path`에 확장자 포함 폴백을 제안.
   - `C-41`은 사용자 노트(`inbox/회의.md`) 구분을 위해 front matter `source_image` 검사를 제안(둘을 함께 통합 설계해야 `collect`·`status`가 맞물림).
5. **`nvim/lua/work-kb/search.lua`, `docs/USER-GUIDE.md` (`A-09(2)` vs `C-47`)**:
   - `A-09(2)`는 현 구현(`fzf-lua`/`rg --fixed-strings` 우선)에 맞춰 `USER-GUIDE.md` 설명 수정을 제안.
   - `C-47`은 문서 수정 또는 `search.lua`의 `rg` 인자 구성을 AND 의미(단어별 필터)로 바꾸는 쪽도 함께 제안.

---

## 정정한 내 항목 목록 (9건, `REVIEW-agy.md` 반영)

1. **`A-01` (높음 → 중간)**: `KB_AGENT=1`에서 읽기 명령 과잉 거부만 발생하며 쓰기 우회는 없고 인자 순서로 복구 가능해 기준상 '중간'으로 조정(`C-33` 대조).
2. **`A-02` (높음 → 중간, 설명 보완)**: `server.lua:352-379` `available`/`ensure`가 다음 요청 시 `M.key` 차이로 서버를 재시작하므로 영구 불일치가 아니며, `setup()` 즉시 종료 누락과 `init.lua:422` 주석 불일치로 정정.
3. **`A-09` (낮음 → 중간)**: 1단계-2 기준("문서·요구사항 불일치 = 중간") 적용(`C-46` 대조).
4. **`A-10` (중간 → 낮음)**: 1단계-2 기준("품질·정리 = 낮음")에 따라 회귀 테스트 공백을 '낮음'으로 조정.
5. **`A-12` (높음 → 중간)**: 데이터 손실·전체 흐름 중단이 아닌 `topics` `mentions`/T3 요약 누락('잘못된 결과·D22 불일치')이므로 '중간'으로 조정.
6. **`A-14` (높음 → 중간)**: `kb.py wheels`는 이미 종료 7로 거부되며 직접 모듈 실행만 D57 요구사항 불일치이므로 '중간'으로 조정(`C-35` 대조).
7. **`A-25` (낮음 → 중간, 범위 보완)**: `init --yes` 사양/도움말 불일치 및 문서 불일치를 '중간'으로 조정하고 `DECISIONS.md` ch.6 우선순위 누락·`KB_SKIP_EMACS` 잔재를 보완(`C-50` 대조).
8. **`A-26` (중간 → 낮음)**: 1단계-2 기준("품질·정리 = 낮음")에 따라 테스트/커버리지 공백을 '낮음'으로 조정(`C-54`·`C-56` 대조).
9. **`A-27(2)` (주석 보완)**: `notes.section_span` 코드 펜스 결함은 `C-21` 재현으로 개인 할 일 이월 누락(`중간`) 영향이 확인됨을 명시(`A-27` 항목 전체 등급은 나머지 3건 정리성 개선 기준으로 '낮음' 유지).
