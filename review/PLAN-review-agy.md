# 개선 합의안 검토 보고 (agy, 1회차)

대상: `nvim-review-claude/review/PLAN.md` (커밋 `0a265e7`, 84행: `P-01`~`P-34`, `P-40`~`P-77`, `P-90`~`P-101`, `K1`~`K6`, 파일 소유표, 순서·완료 기준).
변경된 전제: 지원·검증 Python은 **3.14 단일**이며 `review/REVIEW-agy.md`의 3.13 관련 기술에 `(3.14 단일 대상으로 범위 밖)`을 병기했다.

## 요약

- **집계(84행)**: **동의 77, 수정 요청 7, 반대 0** (claude 34행 중 동의 32·수정 요청 2, agy 38행 중 동의 33·수정 요청 5, 제외·보류 12행 전부 동의).
- **원 항목·파일 점검**: `A-01`~`A-27`(27건)과 `C-01`~`C-57`(57건) 모두 누락 없이 포함됨을 확인했다. `★` 표시 심각도 조정(14건)은 1절 심각도 기준과 일관되어 수용한다. 파일 소유표는 claude 45개·agy 45개로 **교집합 0개**이며 코드 내 `3.13` 잔여 위치도 `P-20` 목록에 빠짐없다.
- **주요 수정 요청(합의 차단/구현 보완 사항)**:
  1. **`P-52` (`notes.find_keys`)**: PLAN 문구대로 `!?[[...]]`를 일괄 마스킹하면 `[[KB-12]]`·`[[jira/KB-12]]` 같은 정상 키 링크(`bracketed=True`)까지 가려져 기존 `link`·`test_notes`가 깨진다. `bracketed=True`(비임베드)는 유지하고 `![[...]]`와 비-키 위키링크 안 `bracketed=False`만 제외해야 한다.
  2. **`P-51` (`link` 주제 연결)**: `scripts/link.py:132-134` `_Ctx.fingerprint`에 `wiki/topics/*.md` 목록·`aliases` 해시를 포함해야 새 주제 추가/병합 때 `links_cache.json`이 무효화된다.
  3. **`P-57` (`jira_sync` 403)**: `in_scope: false` 처리는 `fetch_out_of_scope`(`739-743`)에만 두고 `--key` 경로(`902-907`)에는 넣지 말며, `/myself` 확인은 매 `sync` 시작이 아니라 403 발생 시 1회 지연 확인으로 바꾸는 편이 불필요한 왕복과 모의 시험 영향을 줄인다.
  4. **`P-67` (`defang_comments`) & `K2`**: `tests/test_compile.py:714-715`(`test_one_line_strips_comments_and_newlines`)가 현재 주석 제거를 단언하므로 K2(`&lt;!--`/`--&gt;` 이스케이프)에 맞게 기존 테스트 기대값 갱신이 필요하다.
  5. **`P-69` (`sidecar_for`) & `K5`**: `scripts/ingest_images.py:496`(`asset_destination`)의 `sidecar_path` 호출도 `sidecar_for`로 통일하고, `source_image` 대조가 `inbox/` 고정 이미지와 `raw/assets/` 이동 이미지를 모두 처리해야 한다.
  6. **`P-01` (`fake_serve.py`)**: `tests/nvim/fake_serve.py:25`에도 `SERVED_SUB`에 `"transitions"`가 남아 있어 `P-01` 수정 파일에 추가해야 한다.
  7. **`P-04` (`jira.lua` 옵션)**: `create`/`worklog`의 자유 문자열 옵션(`--type=`·`--started=` 등)도 `=` 꼴 또는 `--` 적용 대상을 일괄 점검해야 한다.

## 84개 항목 판정 표

| P-ID | 판정 | 요청 내용 또는 근거 |
|---|---|---|
| P-01 | 수정 요청 | 방향·K1 동의. 단 `tests/nvim/fake_serve.py:25`의 `SERVED_SUB`에도 `transitions`가 있으므로 `수정 파일`에 `tests/nvim/fake_serve.py`를 추가하고, 쓰기 타임아웃(`code=nil`) 시 호출부 오류 알림이 깨지지 않는지 함께 확인 요청. |
| P-02 | 동의 | `X-A02_lazy_restart.lua` 직접 재현(exit 0)으로 `init.lua:422` 주석 정정·`A-03` 60초 제한 해소·`★낮음` 동의. |
| P-03 | 동의 | `K4` 공용 `kb.subcommand(argv)`와 `serve`/`proc.served()` 일치 방식 동의. |
| P-04 | 수정 요청 | `--body=`·`--comment=`·`--summary=`·`--description=`·`--jql=` 및 `--` 패턴 동의. `jira.lua`의 `create`(`--type=`)·`worklog`(`--started=`·`--time=`) 등 나머지 값 옵션도 `-` 입력에 안전하도록 `=` 꼴 일괄 적용 확인 요청. |
| P-05 | 동의 | 임시 파일 + `fsync` + `os.replace` 재시도 + 디스크 변경 검사(사람 편집 보호) 적절. |
| P-06 | 동의 | `scripts/` mtime 비교 후 응답 뒤 정상 종료(0)·지연 재기동 연계 적절. |
| P-07 | 동의 | 조각 테이블 + `table.concat` 선형 버퍼링 적절(시험 예외 명시 동의). |
| P-08 | 동의 | `repo` 기록 + `hadPrevious`/`previous` 복원 연계 적절. |
| P-09 | 동의 | `★낮음` 근거(`install.ps1:398-409` 다음 후보 폴백)와 `--offline --no-python-downloads` 추가 동의. |
| P-10 | 동의 | 이미지 확장자 외 `.md` 후보 병행 탐색 적절. |
| P-11 | 동의 | `check_done`에도 `--date <time.day()>` 전달 동의. |
| P-12 | 동의 | `core.safe_name` 유니코드 공백 목록과 Lua 표 동기화 적절. |
| P-13 | 동의 | `notes._PERSON_CHARS` 코드 포인트 범위와 `open.lua` 일치 동의. |
| P-14 | 동의 | `open_at_point`에서 `M.link_key` 우선 분기 적절. |
| P-15 | 동의 | `config.lua` `M.vault_dir()`·`M.in_dir()` 공용 헬퍼 통합 적절. |
| P-16 | 동의 | `lhsraw` 기반 해제 적절. |
| P-17 | 동의 | `stopped` 상태를 `health.info`로 완화 적절. |
| P-18 | 동의 | `ui.lua` 프롬프트 동기 동작 근거 확인, `★낮음` 및 `ref` 선고정·수정 버퍼 경고 동의. |
| P-19 | 동의 | `wheels.main` 앞 `core.refuse_if_agent("wheels")` 추가 적절. |
| P-20 | 동의 | 3.14 단일 전제 반영. 저장소 전체 `3.13` 검색 시 누락 파일 없음(`scripts/deploy_check.py`·`schedule.py`·`demo.py`·`docs/README.md` 해당 없음) 확인. `tests/test_docs.py:182` 목록 수정 포함 동의. |
| P-21 | 동의 | Neovim 최소 0.12 통일 및 관련 파일·시험 추가 동의. |
| P-22 | 동의 | `P-01` 반영 후 `AGENTS.md`·`serve.py`·`docs/README.md` 문구 정정 및 `tests/test_docs.py` 검증 동의. |
| P-23 | 동의 | `K6`에 따라 `USER-GUIDE.md` 검색 순서를 실제 구현에 맞춤 동의. |
| P-24 | 동의 | `opts.vault` 안내 추가 동의. |
| P-25 | 동의 | 접두 키 교체 두 방법 문서화 동의. |
| P-26 | 동의 | `DECISIONS.md` Neovim 전환·대체 목록 정리 동의. |
| P-27 | 동의 | `DEPLOY.md` §9 안내 문구 정정 동의. |
| P-28 | 동의 | `README.md`·`USER-GUIDE.md` `serve` 및 Neovim 점검 차이 명시 동의. |
| P-29 | 동의 | 캡처 버퍼·`G` LLM 호출·경로·`AGENTS.md` `status` 설명 정정 동의. |
| P-30 | 동의 | `docs/README.md`·`AGENTS.md` 관리 구역 표(`proposals`·`lint`)·`DEPLOY.md` 제외 범위 정정 동의. |
| P-31 | 동의 | `test_docs.py`·`test_repo_tree.py` 점검 범위 확장 및 키 표 대조 동의. |
| P-32 | 동의 | pip 없는 venv 환경 처리(`pytest.skip` 사유 명시 및 mock 분리) 동의. |
| P-33 | 동의 | `doctor.check_editors` Neovim 버전 파싱 단위 시험 추가 동의. |
| P-34 | 동의 | Lua 미사용 변수 정리(`server.lua:188` 유지 포함) 동의. |
| P-40 | 동의 | `★높음` 동의(매 실행 `daily/` 손상). `scripts/lib/frontmatter.py:27` `_locate` 활용 가능 확인. |
| P-41 | 동의 | CommonMark 울타리 닫힘 규칙 적용 동의. |
| P-42 | 동의 | `section_span`이 `iter_lines`의 `in_code` 및 코드 펜스 상태를 따르도록 수정 동의(`P-40`·`P-41` 이후 순서). |
| P-43 | 동의 | `notes.py:211`, `link.py:42`, `daily_prepare.py:45-46`의 `\d` → `[0-9]` 교체 동의. |
| P-44 | 동의 | `find_people` 입력 NFC 정규화 동의. |
| P-45 | 동의 | `managed._marker_lines`·`notes._LINE_BREAK`의 단독 `\r` 처리 동의. |
| P-46 | 동의 | `frontmatter._FENCE` `\r` 허용 동의. |
| P-47 | 동의 | `★중간` 동의(사람 `daily/` 중복 제목). 제목 비교 양쪽 NFC 및 `section_span` 정규화 일관성 반영. |
| P-48 | 동의 | `★중간` 동의(기본 `"[KB] "` 깨짐). 목록형 키만 YAML 목록 파싱 적용. |
| P-49 | 동의 | NEL·U+2028·U+2029 포함 값 큰따옴표 덤프 동의. |
| P-50 | 동의 | 예약 이름 `max_len-1` 절단 후 `_` 접두 동의. |
| P-51 | 수정 요청 | D22 구현 동의. 단 `scripts/link.py:132-134` `_Ctx.fingerprint`에 기존 `wiki/topics/*.md` 목록과 `aliases` 해시를 포함해야 새 주제가 생기거나 `merge_with`로 별칭이 늘었을 때 기존 원천이 `links_cache.json`을 재사용하지 않고 다시 매칭된다. 또한 없는 주제 파일은 자동 생성하지 않음을 명시 요청. |
| P-52 | 수정 요청 | D20 수정 필요성에 동의하나 PLAN 문구("위키링크·임베드 범위를 가린 뒤 찾는다")가 모호하다. `_masked`에서 `!?[[...]]`를 통째로 가리면 `[[KB-12]]`·`[[jira/KB-12]]`(`bracketed=True`)까지 사라져 `link`와 `test_notes.py:83-105`가 깨진다. `bracketed=True`(앞 `!` 없는 키/경로 링크)는 유지하고 `![[...]]` 임베드 및 비-키 위키링크 내부 `bracketed=False`만 제외하도록 수정 방법 보완 요청. |
| P-53 | 동의 | `★낮음` 동의. `Mapping` 검사 추가 동의. |
| P-54 | 동의 | `link_safe_name`·양끝 `|` 1개 제거·`UnicodeDecodeError` 경고 처리 동의. |
| P-55 | 동의 | `ipaddress.ip_address(h).is_global` 기반 공개 IP 차단 동의(완료 후 D-NV4 문안 claude 전달). |
| P-56 | 동의 | `ingest_images` 잠금 내 원장 재읽기·병합 및 `lint._write`·`config.save_local` 잠금 적용 동의. |
| P-57 | 수정 요청 | 403 `in_scope: false` 전환 방향 동의. 단 (1) `jira_sync.py:902-907`은 `run_keys`(`sync --key`) 오류 처리 구간이므로 `in_scope: false` 적용은 `fetch_out_of_scope`(`739-743`)로 한정하고(`sync --key` 403은 토큰 살아 있으면 종료 4 유지), (2) 매 실행 시작 시 무조건 `/myself`를 부르지 말고 403 발생 시 1회 지연 확인(`client.myself()` 캐시)으로 죽은 토큰(종료 2)과 단건 권한 없음을 가르게 수정 요청. |
| P-58 | 동의 | 숫자 인수 전환 오프라인 pending 큐(종료 3) 동의(완료 후 D-NV4 문안 전달). |
| P-59 | 동의 | `★중간` 동의. `outbox.py`로 `state_of`·`stale_after` 이동 및 `status` "불확실 N개" 표시 동의. |
| P-60 | 동의 | `cmd_promote` 전체 `project_keys`·`linked:false` 중복 차단 및 `_after_done` `try/except core.KbError` 경고 처리 동의. |
| P-61 | 동의 | `★중간` 수용(중복 쓰기엔 수동 `--retry-failed` 등 추가 조건 필요). `_keep`에서 `UNCERTAIN` 유지 동의. |
| P-62 | 동의 | `net.resolve_conn(cfg, conn)` 공용화 동의. |
| P-63 | 동의 | `_restore` 반복 및 `{quote}` 안 코드 블록 `> ` 접두 처리 동의. |
| P-64 | 동의 | `fts` 읽기 메서드 `_is_busy`→`FileBusy` 처리 및 부재/잠금 분리 동의. |
| P-65 | 동의 | `status.collect` `"jira": {"project_keys": cfg.project_keys}` 추가 동의(`nvim/lua/work-kb/jira.lua:333-374` `find_project_keys` 호환 확인). |
| P-66 | 동의 | 전체 JSON 파싱 선시도 + `<think(ing)?>` 시작 시에만 닫는 태그 절단 동의. |
| P-67 | 수정 요청 | `K2` 및 `notes.defang_comments` 공용화 동의. 단 현재 `scripts/compile.py:487`과 `tests/test_compile.py:714-715`(`test_one_line_strips_comments_and_newlines`)가 `<!--`·`-->` **제거**를 단언하고 있으므로 K2 이스케이프(`&lt;!--`/`--&gt;`)에 맞춰 기존 테스트 기대값을 갱신함을 계획에 명시 요청. |
| P-68 | 동의 | 참조 검사 범위를 `inbox/*.md`·`wiki/**/*.md`까지 확장 및 `pinned` 처리 동의. |
| P-69 | 수정 요청 | `★중간` 및 `K5` `sidecar_for` 통합 동의. 단 `scripts/ingest_images.py:496`(`asset_destination`)의 `sidecar_path` 호출도 `sidecar_for`로 맞춰야 하며, `status.unprocessed_images`(`status.py:176,181`)가 `inbox/` 고정 이미지(`inbox/<name>`)와 이동/임베드 이미지(`raw/assets/<name>`)의 `source_image`를 모두 매칭하게 설계 명시 요청(순환 import 없이 배치). |
| P-70 | 동의 | `★중간` 동의. `http`·`https`·`mailto` 외 `href` 및 외부 이미지 평문 전환 동의. |
| P-71 | 동의 | `★중간` 동의. `detect_context`에도 `_no_429_retry()` 적용 동의. |
| P-72 | 동의 | `★중간` 동의. 기준선 호출 `_post_settled` 통일 동의. |
| P-73 | 동의 | `prepare()` 코드 스팬/펜스 보호 후 HTML 주석 제거 동의. |
| P-74 | 동의 | `init --yes` 확인 질문 자동 수락 구현 동의(완료 후 D-NV4 문안 전달). |
| P-75 | 동의 | `prompts/image_ingest.md` 코드 블록 예시 제거 및 단위 시험 추가 동의. |
| P-76 | 동의 | 4개 핵심 분기 시험 추가 동의(`jira_act.py:559-565` 예외 후 미재전송·`atomic_write` `FileBusy`·`_retry_after` HTTP 날짜·`outbox.counts`). |
| P-77 | 동의 | `wikimarkup.py:146` `list_widths`·`lint.py:419-420` 미사용 바인딩·`lint.py:1059` `SECTION_ORDER` 정리 동의(`schedule.py:89` 유지 확인). |
| P-90 | 동의 | 본 워크트리에서 `X-A02_lazy_restart.lua` 실행 결과 exit 0(`before: via=server pid=45140 idle_sec=60`, `after: first via=process, second via=server pid=53380 idle_sec=10 starts=2`, `second status shows new vault: true`) 확인. 제외 및 `P-02` 주석 정정 동의. |
| P-91 | 동의 | `nvd-rpc.lua` 5초 자체 종료 및 `nvd.cmd` 존재 근거, AHK 실행 환경 부재 고려 시 제외 동의. |
| P-92 | 동의 | `nvim --headless --clean --cmd "set rtp+=nvim"` 직접 실행 결과 `after_in_rtp=false` 확인. `K3` 제외 동의. |
| P-93 | 동의 | 한글 볼트 경로 회피 설계를 고려해 제외 동의. |
| P-94 | 동의 | `scripts/lib/core.py:262, 338-340` `_RETRY_WINERRORS = {5, 32, 33}` 재확인, 직렬 실행 전제 포함 제외 동의. |
| P-95 | 동의 | 각 `P-*` 검증 항목이 시험을 추가하므로 중복 제외 동의. |
| P-96 | 동의 | `P-03`·`P-15` 통합으로 중복 제외 동의. |
| P-97 | 동의 | 재현 없음·경미 영향으로 보류 동의. |
| P-98 | 동의 | 실제 Jira DC 페이징 검증 필요로 보류 동의. |
| P-99 | 동의 | `-m live` 금지 및 프롬프트 변경 위험으로 보류 동의. |
| P-100 | 동의 | 추정·재현 없음으로 보류 동의. |
| P-101 | 동의 | `DECISIONS` 설계 결정 필요로 보류 동의. |

## K1~K6 검토

| K | 판정 | 의견 |
|---|---|---|
| K1 | 동의 | `act transitions` 제외·90초 타임아웃·쓰기 재실행 금지·`last_start=nil` 조합이 가장 안전하다. `P-01`에서 `tests/nvim/fake_serve.py`만 누락 없이 맞추면 된다. |
| K2 | 동의 | `defang_comments`를 `lib/notes.py`에 두고 4곳이 공유하는 방향에 동의한다. 기존 `test_compile.py:714-715` 기대값 갱신만 반영하면 된다. |
| K3 | 동의 | `after_in_rtp=false`를 직접 재확인했으므로 안내 유지가 맞다. |
| K4 | 동의 | `kb.subcommand(argv)` 공용 함수가 `kb.py`·`serve.py`·`proc.lua` 전체를 깔끔히 해결한다. |
| K5 | 동의 | `sidecar_for` 공용 함수로 `C-41`과 `A-16(1)`을 한 번에 해소하는 데 동의한다. `asset_destination`(`ingest_images.py:496`)도 같은 함수를 쓰게 맞춘다. |
| K6 | 동의 | `USER-GUIDE.md` 설명을 실제 백엔드 순서에 맞추고 검색 성능을 유지하는 데 동의한다. |

## agy 담당 38항목(`P-40`~`P-77`) 구현성·소유·순서 점검

- **구현 가능성**: 38개 항목 모두 `scripts/lib/**`, `scripts/*.py`, `prompts/image_ingest.md`, `tests/test_*.py` 안에서 외부 API 호출 없이 구현·모의 시험이 가능하다.
- **소유권 경계**:
  - agy 45개 파일과 claude 45개 파일 사이에 **겹치는 파일 0개**.
  - 교차 읽기 의존: claude `P-04` 새 시험(`tests/test_dash_args_claude.py`)이 `jira_act.build_parser()`를 import하므로 agy(`P-58`~`P-61`, `P-76`)는 `jira_act.build_parser()` 기존 CLI 옵션 계약을 유지한다.
  - 문서 반영 의존: 동작이 바뀌는 8개 항목(`P-51`, `P-55`, `P-58`, `P-61`, `P-67`, `P-69`, `P-70`, `P-74`) 구현 완료 뒤 1줄 요약과 `D-NV4` 문안을 claude에게 넘긴다.
- **작업 순서 점검**:
  - `notes.py` 연쇄(`PLAN.md` 5절 보완): `P-40` → `P-41` → `P-42` → `P-43`·`P-44`·`P-45` → `P-47` → `P-52` → `P-54` → `P-51`(stabilized `notes.find_keys` 뒤 `link`) → `P-67`(`defang_comments` 추가 후 `compile`·`ingest_images`·`link`·`lint`). 5절 1번에 `P-45`·`P-54`도 `notes.py` 묶음에 명시해 두면 순차 적용이 명확하다.
  - 동일 파일 묶음(순차 권장): `config.py` (`P-48`·`P-53`·`P-55`·`P-56`), `ingest_images.py` (`P-56`·`P-67`·`P-68`·`P-69`), `jira_act.py` (`P-58`·`P-59`·`P-60`·`P-61`), `status.py` (`P-59`·`P-65`·`P-69`), `llm_detect.py` (`P-71`·`P-72`), `confluence_push.py` (`P-70`·`P-73`).

## 누락·추가 제안

- **항목·파일 누락 없음**: `A-01`~`A-27`·`C-01`~`C-57` 84개 소스 ID 전부 반영됐고, `3.14` 단일화(`P-20`) 대상 외 코드·테스트에 남은 `3.13` 필수 참조도 없다.
- **새 `P-ID` 신설 불필요**: 위 7건의 `수정 요청`(`P-01`, `P-04`, `P-51`, `P-52`, `P-57`, `P-67`, `P-69`) 모두 기존 항목 범위 안에서 파일 1개(`tests/nvim/fake_serve.py`) 또는 구현 제약 1줄씩만 보완하면 된다.
