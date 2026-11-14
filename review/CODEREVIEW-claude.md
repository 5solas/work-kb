# 코드 리뷰: agy 구현 (claude → agy)

대상: `nvim-review-agy` HEAD `ffcfca6` (구현 `9732263..ffcfca6`, 46개 파일), 기준 `review/PLAN.md`(`afbd2c3`), `review/IMPL-agy.md`.
상대 워크트리는 읽기만 했다. 변이 확인과 합친 상태 확인은 내 저장소의 임시 detached 워크트리 둘(`ffcfca6` 사본, 내 `4372045`+`ffcfca6` 병합본)에서 했고 끝나고 지웠다.

## 판정: **수정 후 승인**

- 차단 0, 중요 2, 사소 3.
- 38항목 모두 PLAN의 수정 방법대로 들어갔다. 변이 37개 중 36개를 시험이 잡았다.
- 두 브랜치를 합친 상태에서 전체 시험이 통과했다(아래 4절). 계약 어긋남은 없다.

## 1. 지적

| ID | 파일:줄 | 등급 | 문제 | 근거 | 제안 수정 |
|---|---|---|---|---|---|
| R-claude-01 | `scripts/ingest_images.py:823` (`_write`의 `_merge_ledger`), `tests/test_ingest_images.py::test_ledger_merges_concurrent_update` | 중요 | P-56의 원장 병합은 `_skip`(실패)과 `_write`(성공) 두 경로에 들어갔는데 시험은 `_skip`만 본다. 성공 경로를 옛 코드(`self.ledger["images"][sha] = entry; self._save_ledger()`)로 되돌려도 시험이 통과한다. A-23의 원래 결함(동시 실행이 원장 항목을 덮어씀)은 성공 경로에서 더 흔하다 | 변이 P-56a: `self._merge_ledger(image=(sha, entry), unskip=sha)` → 옛 코드. `pytest tests/test_ingest_images.py::test_ledger_merges_concurrent_update` → `1 passed`(못 잡음). 시험 본문은 `run._skip(...)`만 부른다 | `_Run`을 만든 뒤 다른 실행이 원장에 항목을 저장하고, mock LLM으로 `_write`(또는 `run.run`) 성공 경로를 태운 뒤 두 항목이 다 남는지 보는 시험을 더한다 |
| R-claude-02 | `scripts/lib/llm_client.py:180-203` (`normalize_message`) | 중요 | P-66은 "전체가 JSON이면 자르지 않음"만 고쳤다. 앞에 생각 블록이 있고 JSON 안에 `</think>`가 들어 있으면 여전히 **마지막** `</think>`에서 잘라 JSON이 깨진다. C-37(리터럴 `</think>`로 JSON 절단)의 같은 원인이 남았다. PLAN은 "`<think>`로 시작할 때만 닫는 태그까지" 자르자고 했다 | `normalize_message({'content': '<think>r</think>{"t":"use </think> x"}'})` → 답 `'x"}'`, `parse_json_object` → `JSON을 읽지 못했습니다`. 같은 입력에서 `{"t":"use </think> x"}`만 주면 OK | JSON이 아니면 닫는 태그 위치를 앞에서부터 차례로 보며 그 뒤 나머지가 JSON으로 읽히는 첫 위치에서 자르고, 없으면 지금처럼 마지막 위치. 위 입력을 `tests/test_llm_client.py`에 더한다 |
| R-claude-03 | `review/IMPL-agy.md` 1절·3절, 커밋 메시지 `336955d`·`a7f2780` | 사소 | 기록의 P-ID와 설명이 PLAN·코드와 다르다. (1) IMPL의 P-53은 PLAN P-55(IP 차단), P-55는 PLAN P-56(잠금), PLAN P-53(빈 섹션 오류)은 P-48 줄에 섞임. 커밋 `fix(P-53): check blocklist for private/loopback IPs`도 PLAN P-55다. (2) P-64 "retry helper" — 실제는 재시도 없이 FileBusy로 바꿈. P-71 "Retry HTTP 429" — 실제는 `/models` GET의 429 재시도를 **끔**. P-73 "`#` comments" — 실제는 HTML 주석. (3) D-NV4 문안 중 P-58("이름·id가 맞으면"), P-70("상대/앵커 외"), P-74가 코드와 다름 | `git diff d168f6c..ffcfca6 -- scripts/lib/fts.py scripts/lib/llm_detect.py` (코드), 아래 2절 확인 | 코드는 맞다. IMPL 표의 ID·설명을 PLAN 기준으로 고친다. D-NV4는 내가 코드에 맞춰 고쳐 `docs/DECISIONS.md`에 기록했다(`124fbca`) |
| R-claude-04 | `scripts/init.py:822-824` (`_confirm`) | 사소 | `--yes`는 기본값이 "아니오"인 질문도 "예"로 한다. 이미 설정된 볼트에서 `init --yes`를 다시 돌리면 "LLM을 설정할까요?"(기본 n)·"Confluence를 설정할까요?"(기본 n)·"보조 프로필도?"(기본 n)가 예가 되어 그 구역 값을 다시 묻는다. PLAN 합의("확인 질문을 예로")대로이지만 "확인 없이 진행"을 기대한 사용자에게 놀랍다 | `init.py:744, 1102, 1110` 기본값, `_confirm`이 `wizard.yes`면 무조건 True | 도움말에 "기본값과 상관없이 예"를 적거나, "설정할까요?" 꼴 질문은 기본값을 따르게 한다. 바꾸면 D-NV4 P-74 줄도 맞춘다 |
| R-claude-05 | `scripts/confluence_push.py:470-506` (`_sanitize_element`) | 사소 | P-70은 PLAN 문구대로 `http`·`https`·`mailto` 밖을 모두 글자로 바꿔 상대 경로·`#앵커` 링크도 사라지고, 이미지는 내부 이미지까지 모두 alt 글자가 된다. 원래 Confluence에서 깨지던 링크라 손해는 작지만 `#앵커`는 Confluence에서도 동작했다 | `to_storage('[anchor](#sec) and [rel](other.md)')` → 수정 전 `<a href="#sec">`, 수정 후 `anchor and rel` | 그대로 둬도 된다(문서화함). 원하면 `#`로 시작하는 href만 허용 |

검토했지만 지적하지 않은 것(문제 없음):
- `wikimarkup._restore` 반복: 자리표 꼴(`\x00N\x00`)이 든 입력 3종을 넣어도 끝났다(15초 제한 안 걸림).
- `sidecar_for`와 중복 사이드카: 중복 사이드카도 `source_image`를 쓰므로 다음 실행에서 다시 미처리로 세지 않는다(`ingest_images.py:723-730`).
- `iter_lines` front matter: BOM+front matter, CRLF front matter에서 본문 첫 줄이 코드로 잡히지 않는다.
- P-48 목록형 환경 변수: `_LIST_ENV_KEYS` = `confluence.push_globs`, `jira.project_keys`, `llm.fallback_order`, `llm.public_hosts_blocklist`. 목록 덮어쓰기는 그대로 된다.
- 보안: 오프라인 전환 기록의 `last_error`는 `core.redact`를 거친다. 새 subprocess 호출 없음. `resolve_conn`은 설정 파일 기준 상대 경로만 붙인다(사용자 설정 값). Confluence 거르기는 `javascript:`·`data:`·제어 문자 섞인 스킴을 막는다.
- KB_AGENT: 오프라인 전환은 `jira_act.main`의 쓰기 거부를 먼저 지난다. `init --yes`는 `kb.py`·`init.main` 둘 다 거부.
- 데이터 영역: daily를 고치는 경로는 키 정규화뿐이고 P-41·P-52로 코드 블록·임베드·비키 위키링크 안을 더 정확히 건너뛴다. inbox 이미지는 다른 노트가 참조하면 옮기지 않는다(P-68).

## 2. PLAN 이행과 "달라진 점" 판단

- 38항목 모두 수정 방법대로 구현됐다. IMPL 2절의 10개 차이는 모두 정당하다(시험 픽스처를 새 동작에 맞춤, 잠금 파일을 트리 비교에서 뺌, P-72 시험을 "항상 500"으로 바꿈 등).
- P-51 지문에서 본문·`updated:`를 뺀 것은 PLAN("목록과 aliases 해시")과 같고 매 실행 캐시 무효화를 막는다. 맞다.
- P-53/P-55 IP: 사설 IP라도 차단 목록에 있으면 거부하게 넓힌 것은 PLAN보다 강하지만 회사 데이터 보호 방향이라 맞다.
- P-70 이미지 전부 글자화는 PLAN("외부 src만")보다 넓다. R-claude-05로 기록.

## 3. 변이 확인

`ffcfca6` 임시 사본에서 수정 부분 하나만 되돌리고 그 항목 시험만 돌렸다(`review` 밖 스크립트, 실행 뒤 `git checkout`으로 복구).

| 항목 | 되돌린 부분 | 테스트 실패 여부 |
|---|---|---|
| P-40 | `iter_lines`의 `front_end`를 "첫 줄 `---`면 파일 끝까지"로 | 실패(잡음) |
| P-41 | `_is_closing_fence`를 3글자 비교로 | 실패(잡음) |
| P-42 | `section_span`이 `in_code`를 무시 | 실패(잡음) |
| P-43 | 키 정규식 `[0-9]` → `\d` | 실패(잡음) |
| P-44 | `find_people` NFC 정규화 제거 | 실패(잡음) |
| P-46 | `_FENCE`를 `---[ \t]*\r?`로 | 실패(잡음) |
| P-48 | 목록 허용 검사 제거 | 실패(잡음) |
| P-50 | 예약 이름 접두 뒤 자르기 제거 | 실패(잡음) |
| P-51a | `_find_topics` 결과를 빈 목록으로 | 실패(잡음) |
| P-51b | 지문에서 `topics_hash` 제거 | 실패(잡음) |
| P-52 | 임베드·비키 위키링크 안 건너뛰기 제거 | 실패(잡음) |
| P-53 | 빈 섹션 Mapping 검사 제거 | 실패(잡음) |
| P-55 | 공개 IP(`is_global`) 차단 끔 | 실패(잡음) |
| P-56a | `_write` 성공 경로의 `_merge_ledger`를 옛 저장으로 | **통과(못 잡음)** → R-claude-01 |
| P-56b | `lint._write`의 `kb_lock` 제거 | 실패(잡음) |
| P-56c | `save_local`의 `kb_lock` 제거 | 실패(잡음) |
| P-57a | 403을 범위 밖으로 보지 않음 | 실패(잡음) |
| P-57b | 403 때 `/myself` 확인 제거 | 실패(잡음) |
| P-58 | 숫자 id 오프라인 큐 끔 | 실패(잡음) |
| P-59 | status가 `state_of` 대신 원래 status 사용 | 실패(잡음) |
| P-60a | 줄 끝 키 검사를 대상 프로젝트만으로 | 실패(잡음) |
| P-60b | done·`linked` 아님 승격 검사 제거 | 실패(잡음) |
| P-60c | `_after_done`의 KbError 처리 제거 | 실패(잡음) |
| P-61 | uncertain 유지 분기 제거 | 실패(잡음) |
| P-62 | `JiraClient.from_config`의 `resolve_conn` 제거 | 실패(잡음) |
| P-63 | `_restore` 반복 제거 | 실패(잡음) |
| P-64 | `Cache._read`의 FileBusy 변환 우회 | 실패(잡음) |
| P-65 | `status --json`의 `jira.project_keys` 제거 | 실패(잡음) |
| P-66 | "전체 JSON이면 자르지 않음" 끔 | 실패(잡음) |
| P-67 | `compile._one_line`을 옛 제거 방식으로 | 실패(잡음) |
| P-68 | inbox·wiki 참조 수집 제거 | 실패(잡음) |
| P-69 | `sidecar_for`의 `source_image` 대조 제거 | 실패(잡음) |
| P-70 | `_SafeLinksImages` 등록 제거 | 실패(잡음) |
| P-71 | `/models`의 `_no_429_retry` 제거 | 실패(잡음) |
| P-72 | 기준선 `_post_settled` → `_post` | 실패(잡음) |
| P-73 | 코드 밖 주석만 지우기 → 전부 지우기 | 실패(잡음) |
| P-74 | `wizard.yes` 분기 끔 | 실패(잡음) |

## 4. 두 브랜치 사이 계약 (합친 상태 확인)

임시 detached 워크트리에서 내 `4372045`에 `ffcfca6`을 병합(충돌 없음)한 뒤 내 `.venv`(3.14)로 돌렸다.

| 확인 | 결과 |
|---|---|
| 전체 `pytest -q` | 2272 passed, 4 skipped, 실패 0 |
| `-n auto` | 1 failed, 2271 passed — 실패는 **내** `tests/test_serve.py::test_exits_after_code_change`(agy R-agy-02와 같은 경쟁 상태). agy 코드와 무관, 내가 고친다 |
| `-m perf` / `demo` / `packaging` | 1 / 5 / 3 passed |
| C-10 `status --json`의 `jira.project_keys` → Lua `jira.read_project` | 합친 상태에서 통과(후보 `KB`·`OPS`, 기본값 `KB`) |
| C-12·C-13 이름 대조 | `@이름`의 NFD 입력 1건 DIFF — agy P-44(NFC)에 내 Lua가 아직 안 맞춘 것. agy R-agy-01과 같고 내 쪽에서 고친다 |
| `jira_act.build_parser()`(내 `tests/test_dash_args_claude.py`가 import) | agy가 parser를 바꾸지 않았다. 시험 통과 |
| `kb.subcommand`·serve SERVED·`fake_serve.py` | agy가 건드리지 않았다. 내 시험 통과 |
| `scripts/lib/sidecar.py` | agy 소유, 내 코드는 쓰지 않는다. 문서(D-NV4)에 이름 규칙 기록 |
| `status --json` `outbox.uncertain` 새 키 | Lua는 outbox 수를 읽지 않는다. 영향 없음 |
| 오프라인 전환(P-58) | Neovim `t`는 전환 목록을 먼저 받으므로 오프라인 큐는 명령줄 전용. D-NV4에 적었다 |

## 5. 재확인 (3단계-3)

대상: agy 수정 `ae4e744..60434cd`, 기록 `review/RESPONSE-agy.md`(agy 워크트리, 읽기만 함).
방법: 내 저장소에 임시 브랜치 `tmp/recheck-claude`(내 `b72f46d` + agy `60434cd` 병합, 충돌 없음)를 임시 워크트리로 만들어 그 위에서 확인했다. 끝나고 브랜치와 워크트리를 지웠다. 작업 브랜치에는 병합하지 않았다.

| ID | agy 처리 | 판정 | 근거 |
|---|---|---|---|
| R-claude-01 | 수정(`ae4e744`) | **해소** | 새 시험 `tests/test_ingest_images.py::test_ledger_merges_concurrent_update_on_write_success`. 직접 변이: `scripts/ingest_images.py:823`의 `self._merge_ledger(image=(sha, entry), unskip=sha)`를 옛 `self.ledger["images"][sha] = entry; self._save_ledger()`로 바꾸면 `1 failed`, 되돌리면 `1 passed` |
| R-claude-02 | 수정(`4184ac1`) | **해소** | 내 반례 `<think>r</think>{"t":"use </think> x"}` → 답 `{"t":"use </think> x"}`, `parse_json_object` OK, 생각 `r`. 추가로 본 경우도 맞다: 생각 블록 둘 뒤 JSON 안 `</think>` → OK, 생각 안에 `{…}`가 있어도 뒤 JSON만 답, 코드 울타리 JSON 안 `</think>` → OK, 대문자 태그·여는 태그 없는 닫는 태그 → OK, JSON이 아닌 답·닫히지 않은 `<think>`는 지금처럼 파싱 실패(재시도로 감) |
| R-claude-03 | 수정(`317412e`) | **해소** | `IMPL-agy.md` 표의 P-53(빈 섹션 ConfigError)·P-55(IP 차단)·P-56(잠금)·P-64(FileBusy)·P-70(앵커·상대·이미지 글자화)·P-71(`/models` 429 재시도 끔)·P-73(HTML 주석) 설명이 코드와 맞다. 이미 올린 커밋 메시지(`336955d`, `a7f2780`)를 고치지 않은 것은 이력 재작성을 피하려는 것이라 받아들인다 |
| R-claude-04 | 부분 수정(`f186c65`, 도움말) | **수용** | `kb.py init --help`: `--yes  확인 질문(y/n)을 기본값과 상관없이 예로 처리하고 진행`. 동작은 PLAN P-74 합의와 D-NV4대로이고, 예로 처리되는 질문은 다시 설정할지 묻는 것뿐이라 데이터 위험이 없다. 사소한 차이라 합의 원칙대로 받아들인다 |
| R-claude-05 | 반박 | **수용** | PLAN P-70("href는 http·https·mailto만, 그 밖은 글자로")과 내가 기록한 D-NV4(`124fbca`)대로다. 링크 글은 남고 주소만 빠지므로 데이터 손실·요구사항 위반이 아니다. 반박을 받아들인다 |

합친 상태 확인(`b72f46d` + `60434cd`): `pytest -q -n auto` 2284 passed, 4 skipped, 실패 0. `-m perf` 1 / `-m demo` 5 / `-m packaging` 3 passed. agy의 새 시험(`test_llm_client.py` hypothesis 포함, `test_init_full.py`, `test_ingest_images.py`)도 이 안에서 통과했다.

### 최종 판정: **승인**

차단·미해소 항목 없음. 중요 2건(R-claude-01, 02)은 해소, 사소 3건은 해소 1·수용 2.
