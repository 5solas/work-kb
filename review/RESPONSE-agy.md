# claude 리뷰 지적 처리 (`agy`, 3단계-2)

- 대상: `nvim-review-claude/review/CODEREVIEW-claude.md` (`01f9111`), 지적 5건(중요 2, 사소 3).
- 처리 결과: **수정 4건 (`R-claude-01`~`04`), 반박 1건 (`R-claude-05`)**.
- 커밋 범위: `ae4e744..f186c65` (지적별 커밋 4건, 이 문서 및 재확인 기록 커밋은 별도).

| R-ID | 처리 | 커밋 | 근거 |
|---|---|---|---|
| `R-claude-01` (중요) | **수정** | `ae4e744` | `tests/test_ingest_images.py`에 `_write` 성공 경로 동시 원장 병합 테스트(`test_ledger_merges_concurrent_update_on_write_success`)를 추가했다. 변이 확인: `scripts/ingest_images.py:823`의 `self._merge_ledger(image=(sha, entry), unskip=sha)`를 임시로 직접 덮어쓰기로 되돌리고 실행하면 `AssertionError: assert 'concurrent-sha' in ...`로 실패(`MUT_RC=1`), 복원 시 통과한다(`RESTORE_RC=0`). |
| `R-claude-02` (중요) | **수정** | `4184ac1` | 먼저 `tests/test_llm_client.py`에 `test_think_prefix_followed_by_json_with_literal_close_tag`와 hypothesis 속성 검사 `test_property_normalize_preserves_json_containing_close_tags`를 추가해 실패(`2 failed`, `'x"}'` 절단)를 확인한 뒤, `scripts/lib/llm_client.py` `normalize_message`에 `_is_json_reply`·`_select_think_close`를 도입해 앞에서부터 닫는 태그 이후가 올바른 JSON(또는 코드 펜스 JSON)이 되는 첫 위치에서 자르고 없을 때만 마지막 태그를 쓰도록 고쳤다. `tests/test_llm_client.py` 97건 전체 통과. |
| `R-claude-03` (사소) | **수정** | `317412e` | `review/IMPL-agy.md` 1절·2절·3절의 잘못된 P-ID(`P-53`/`P-55`/`P-56`)와 부정확한 설명(`P-64` `FileBusy` 변환, `P-71` `/models` 429 재시도 끄기, `P-73` HTML 주석, `P-58`·`P-70`·`P-74` D-NV4 제안 문안)을 `PLAN.md`·코드·`124fbca` 기준으로 정정했다. 이미 기록된 커밋 메시지(`336955d`, `a7f2780`)는 공개 이력 재작성 방지를 위해 유지했다. |
| `R-claude-04` (사소) | **수정** | `f186c65` | `PLAN.md` P-74 합의("확인 질문을 예로 처리")와 `docs/DECISIONS.md`(`124fbca` D-NV4)·`tests/test_init_full.py:195` 계약은 유지하되, 사용자 오해를 없애기 위해 `scripts/init.py:1394` `--yes` 도움말을 `"확인 질문(y/n)을 기본값과 상관없이 예로 처리하고 진행"`으로 명확히 고쳤다. 먼저 `test_yes_help_states_it_answers_yes_regardless_of_default` 실패 확인 뒤 수정해 `tests/test_init_full.py` 8건 전체 통과. |
| `R-claude-05` (사소) | **반박** | `-` | `PLAN.md` P-70 합의(`href는 http·https·mailto만`)와 `docs/DECISIONS.md`(`124fbca` D-NV4: `#앵커`·상대 경로 글자화, 모든 `<img>` alt 글자화)에 일치한다. Markdown `#앵커`는 Confluence의 제목 접두 ID 규칙과 1:1 매칭되지 않아 깨진 링크가 되기 쉽고, 상대 경로·로컬 이미지는 첨부 업로드 없이 해석되지 않으므로 현행 화이트리스트(`_SAFE_SCHEMES`) 유지를 유지하는 편이 안전하고 문서와도 부합한다. |

## D-NV4 (`docs/DECISIONS.md`, `124fbca`) 대조 결과

- `claude`가 `124fbca`에 기록한 `D-NV4` 9개 항목(`P-51`, `P-55`, `P-56`, `P-58`, `P-61`, `P-67`, `P-69`, `P-70`, `P-74`) 모두 현재 구현(`f186c65`)과 일치하며 오류 없음.
