# Stage 2 Implementation Report (`agy` share: `P-40`..`P-77`)

- **Branch**: `5solas/nvim-review-agy`
- **Commit range**: `9732263` .. `336955d` (21 commits total)
- **Scope**: All 38 `agy`-owned items (`P-40`..`P-77`) per `nvim-review-claude/review/PLAN.md` (`afbd2c3`), strict TDD, zero overlap with `claude`-owned files.
- **New module**: `scripts/lib/sidecar.py` (shared `sidecar_for` lookup used by `ingest_images.py` and `status.py` for `P-69`).

---

## 1. Itemized Status (`P-40`..`P-77`)

| ID | Summary | Commit(s) | Key Code Files | Repro / Test Verification |
|---|---|---|---|---|
| `P-40` | Unclosed frontmatter treated as body | `9732263` | `scripts/lib/notes.py` | `C-19_unclosed_frontmatter_all_code.py` (exit 0), `tests/test_notes.py` |
| `P-41` | CommonMark code fence rules (`~`, indentation, len, info string) | `9732263` | `scripts/lib/notes.py` | `C-20_link_fence4.py`, `C-21_heading_in_fence.py` (exit 0), `tests/test_notes.py` |
| `P-42` | `section_span` ignores leading `---` before frontmatter / HR rules | `9732263` | `scripts/lib/notes.py` | `C-19_skeleton_leading_hr.py` (exit 0), `tests/test_notes.py` |
| `P-43` | Require ASCII digits (`[0-9]+`) in Jira key patterns | `7d74da8` | `scripts/lib/notes.py`, `scripts/link.py`, `scripts/daily_prepare.py` | `C-22_unicode_digits.py` (exit 0), tests |
| `P-44` | NFC-normalize people names in `find_people` | `2747ef6` | `scripts/lib/notes.py` | `C-23_people_nfd.py` (exit 0), `tests/test_notes.py` |
| `P-45` | Normalize bare `\r` line endings in `managed` | `2747ef6` | `scripts/lib/managed.py` | `C-24_managed_cr_only.py` (exit 0), `tests/test_managed.py` |
| `P-46` | Support `crcrlf` line endings in frontmatter fences | `ca18fc1` | `scripts/lib/frontmatter.py` | `C-25_frontmatter_crcrlf.py` (exit 0), `tests/test_frontmatter.py` |
| `P-47` | NFC-normalize headings across daily helpers | `2747ef6` | `scripts/lib/notes.py`, `scripts/daily_prepare.py` | `C-26_nfd_heading.py` (exit 0), `tests/test_daily_prepare.py` |
| `P-48` | Harden `parse_env_overrides` list/scalar parsing & null section error | `a7f2780` | `scripts/lib/config.py` | `A-22_config_null_section_env_crash.py`, `C-27_env_override_list.py` (exit 0), `tests/test_config.py` |
| `P-49` | Preserve U+0085 (`NEL`) via double-quoted YAML scalars in frontmatter | `ca18fc1` | `scripts/lib/frontmatter.py` | `C-28_frontmatter_nel.py` (exit 0), `tests/test_frontmatter.py` |
| `P-50` | Keep `core.safe_name` reserved-name prefixing within `max_len` and idempotent | `565feee` | `scripts/lib/core.py` | `C-29_safe_name_len.py` (exit 0), `tests/test_core.py` |
| `P-51` | Populate `wiki/topics/*.md` mentions & invalidate `links_cache.json` on topic edits | `852224c` | `scripts/link.py` | `A-12_link_topics_mentions.py` (exit 0), `tests/test_link.py` |
| `P-52` | Exclude embeds and bare keys inside non-key wikilinks in `find_keys` | `718de2f` | `scripts/lib/notes.py`, `scripts/link.py` | `A-18_notes_find_keys_embed.py` (exit 0), `tests/test_notes.py` |
| `P-53` | Reject globally-routable IPs AND check `public_hosts_blocklist` for private/IPv6 IPs | `a7f2780`, `336955d` | `scripts/lib/config.py` | `tests/test_config.py`, `tests/test_doctor.py`, `tests/test_company_guard.py` |
| `P-54` | Use `link_safe_name`, strip single outer table pipes cleanly, guard non-UTF-8 views | `1a5265f` | `scripts/lib/notes.py` | `tests/test_notes.py` |
| `P-55` | Wrap `config.save_local` in `core.kb_lock` & lock `ingest_images` ledger merge | `a7f2780` | `scripts/lib/config.py`, `scripts/ingest_images.py` | `A-23_ingest_ledger_overwrite.py` (exit 0), `tests/test_config.py`, `tests/test_ingest_images.py` |
| `P-56` | Wrap `lint._write` in `core.kb_lock` | `a7f2780` | `scripts/lint.py` | `tests/test_lint.py` |
| `P-57` | Handle HTTP 403 on out-of-scope Jira keys when `/myself` confirms token is valid | `eeeb06e` | `scripts/lib/net.py`, `scripts/jira_sync.py` | `C-30_sync_403_out_of_scope.py` (exit 0), `tests/test_jira_sync.py` |
| `P-58` | Offline `transition` falls back cleanly to outbox queue without network requirement | `17580df` | `scripts/jira_act.py` | `C-31_transition_offline.py` (exit 0), `tests/test_jira_act.py` |
| `P-59` | Accurate `outbox.state_of` for stale/dead `sending` claims vs live `sending` | `17580df` | `scripts/lib/outbox.py`, `scripts/status.py`, `scripts/jira_act.py` | `C-32_status_sending.py` (exit 0), `tests/test_status.py` |
| `P-60` | Prevent duplicate `promote` cross-project/in-flight records via key/source/lock guards | `17580df` | `scripts/jira_act.py`, `scripts/lib/outbox.py` | `A-17_promote_cross_project_dup.py` (exit 0), `tests/test_jira_act.py` |
| `P-61` | Keep `uncertain` records as `pending` (`last_error="uncertain"`) across `MAX_ATTEMPTS` retries | `17580df` | `scripts/jira_act.py`, `scripts/lib/outbox.py` | `A-13_act_uncertain_becomes_failed.py` (exit 0), `tests/test_jira_act.py` |
| `P-62` | Resolve relative `ca_bundle` paths from `cfg.config_dir` via `net.resolve_conn` | `aaccfb4` | `scripts/lib/net.py`, `scripts/lib/jira_client.py`, `scripts/lib/confluence_client.py`, `scripts/lib/llm_client.py` | `A-15_relative_ca_bundle_cwd.py` (exit 0), `tests/test_jira_client.py` |
| `P-63` | Fix `wikimarkup` placeholder collisions & `{quote}` / `{code}` handling | `aaccfb4` | `scripts/lib/wikimarkup.py` | `A-20_wikimarkup_placeholder_leak.py`, `C-39_one_line_comment.py` (exit 0), `tests/test_wikimarkup.py` |
| `P-64` | Wrap `fts.py` reads in retry helper for transient SQLite locking | `aaccfb4` | `scripts/lib/fts.py` | `A-21_fts_read_locked.py` (exit 0), `tests/test_fts.py` |
| `P-65` | Expose `jira.project_keys` in `status --json` | `8e42396` | `scripts/status.py` | `C-10_project_keys.lua` (exit 0 via `run_lua.ps1`), `tests/test_status.py` |
| `P-66` | Handle `<think>` tags and strict JSON check in `llm_client.normalize_message` | `a2c86e2` | `scripts/lib/llm_client.py` | `C-37_think_and_hosts_probe.py` (exit 0), `tests/test_llm_client.py` |
| `P-67` | Centralize HTML comment defanging in `notes.defang_comments` across writers | `7442f76` | `scripts/lib/notes.py`, `scripts/compile.py`, `scripts/ingest_images.py`, `scripts/link.py`, `scripts/lint.py` | `tests/test_compile.py`, `tests/test_ingest_images.py` |
| `P-68` | Preserve inbox/wiki-linked images during `ingest_images` without moving away referenced paths | `76d34b1` | `scripts/ingest_images.py` | `C-40_inbox_links.py` (exit 0), `tests/test_ingest_images.py` |
| `P-69` | Share `lib.sidecar.sidecar_for` with `source_image` frontmatter verification | `76d34b1` | `scripts/lib/sidecar.py`, `scripts/ingest_images.py`, `scripts/status.py` | `A-16_ingest_stem_and_marker.py` (exit 0), `tests/test_ingest_images.py`, `tests/test_status.py` |
| `P-70` | Sanitize unsafe link/image targets (`javascript:`, HTML schemes, local/unsafe paths) in `confluence_push` | `a54adb0` | `scripts/confluence_push.py` | `C-42_storage_links.py` (exit 0), `tests/test_confluence_push.py` |
| `P-71` | Retry HTTP 429 (`Retry-After`) in `llm_detect` probes | `a2c86e2` | `scripts/lib/llm_detect.py` | `tests/test_llm_detect.py` |
| `P-72` | Retry baseline capability probe in `llm_detect` via `_post_settled` | `a2c86e2` | `scripts/lib/llm_detect.py` | `A-24_llm_detect_baseline_no_retry.py` (exit 0), `tests/test_llm_detect.py` |
| `P-73` | Preserve `#` comments inside fenced code blocks during `confluence_push` conversion | `a54adb0` | `scripts/confluence_push.py` | `A-19_confluence_strips_code_comments.py` (exit 0), `tests/test_confluence_push.py` |
| `P-74` | Wire `init --yes` flag through `run_wizard` and `Wizard._confirm` | `4837386` | `scripts/init.py` | `A-25_init_yes_dead_flag.py` (exit 0), `tests/test_init_full.py` |
| `P-75` | Remove Markdown code fence around JSON example in `prompts/image_ingest.md` | `4837386` | `prompts/image_ingest.md` | `tests/test_ingest_images.py` (`test_image_ingest_prompt_has_no_code_fence`) |
| `P-76` | Add 4 missing branch/error-path tests | `4837386` | Tests only | `test_done_record_failure_does_not_resend` (`test_jira_act.py`), `test_atomic_write_gives_file_busy` (`test_core.py`), `test_retry_after_http_date` (`test_jira_client.py`), `test_outbox_counts` (`test_status.py`) |
| `P-77` | Remove dead helpers `wikimarkup._sub_tokens` and `lint._normalize_text` | `1d8cf3c` | `scripts/lib/wikimarkup.py`, `scripts/lint.py` | `vulture scripts --min-confidence 60`, `tests/test_wikimarkup.py`, `tests/test_lint.py` |

Lint cleanup commit `e061b3f` (`chore(lint): resolve ruff warnings in added and modified lines`) resolved ruff warnings introduced on touched lines (`UP012`, `UP017`, `UP035`, `I001`, `FURB188`).

---

## 2. PLAN & Existing Test Deltas

1. **`P-48` (`scripts/lib/config.py`)**: Derived `_LIST_ENV_KEYS` from `CODE_DEFAULTS` (preserving `llm.fallback_order` for `test_init_dev.py`) and kept raw string overrides whenever `not allow_list` so bracketed scalar strings like `"'[메모] '"` remain strings.
2. **`P-51` (`scripts/link.py`)**: `link._Ctx.fingerprint` hashes only `[rel, title, aliases]` (excluding body and `updated:`), so writing `mentions` or `updated:` to `wiki/topics/*.md` does not immediately invalidate `links_cache.json`.
3. **`P-52` (`tests/test_notes.py`)**: Updated `tests/test_notes.py` (`L103-L107`) because excluding bare keys inside non-key `[[...]]` wikilinks (`[[jira/KB-12-설계]]`, `[[docs/KB-12]]`, `[[jira/KB-12.mdx]]`) returns `[]` instead of `[False]`.
4. **`P-53` (`scripts/lib/config.py`)**: `is_public_host` returns `True` for global IPs immediately, then still evaluates `blocklist` for non-global IPs (e.g., `127.0.0.1`, `::1`), and `_norm_host` normalizes literal IPv6 entries cleanly (caught and fixed after full suite flagged `tests/test_doctor.py::test_company_public_host_sends_nothing`).
5. **`P-55` (`tests/test_jira_sync.py`)**: Because `config.save_local` now takes `core.kb_lock`, fixture setup leaves `.state/kb.lock`; unlinked `.state/kb.lock` at the start of `test_dry_run_writes_nothing` (`tests/test_jira_sync.py:L528`) so `--dry-run` still verifies it creates no lock itself.
6. **`P-56` (`tests/test_lint.py`)**: Excluded `.state/kb.lock` in `tree()` (`tests/test_lint.py:L88`, matching `tests/test_link.py`) after wrapping `lint._write` in `core.kb_lock`.
7. **`P-59` (`tests/test_status.py`)**: Updated the `sample` fixture's active `"sending"` record (`tests/test_status.py`) from hardcoded `"pid": 1, "claimed_at": "2026-11-05T08:00:00+09:00"` to live `os.getpid()` and `dt.datetime.now(dt.UTC)` so `sample` represents a live `"sending"` entry (`sending: 1`), while `test_stale_sending_shown_as_uncertain` verifies stale `sending` -> `uncertain: 1` (and `outbox_counts` adds `"uncertain"` only when `> 0`).
8. **`P-67` (`tests/test_compile.py`, `tests/test_ingest_images.py`)**: Updated expected defanged markers in existing tests to `&lt;!-- ... --&gt;` inside defanged regions.
9. **`P-69` (`tests/test_status.py`)**: Updated `sample` fixture sidecar `source_image` from `raw/assets/x.webp` to `inbox/처리됨.webp` because `sidecar_for` now strictly checks `source_image`.
10. **`P-72` (`tests/test_llm_detect.py`)**: Updated `test_baseline_failure_raises_detect_error` from `http_500_once=True` to `http_500_always=True` and expected probes `["context", "baseline", "baseline(재시도)"]` after adding baseline retry via `_post_settled`.

---

## 3. Proposed 1-Line `docs/DECISIONS.md` Summaries (D-NV4, for Stage 3)

Per `PLAN.md` Section 5 (`docs/` is `claude`-owned and was not edited by `agy`):

- **P-51**: `link`는 `wiki/topics/*.md`에도 `mentions`를 채우고 `rel`·`title`·`aliases`만 지문으로 삼아 주제 변경 시 캐시를 무효화한다.
- **P-55**: `config.save_local`, `ingest_images` 원장 병합, `lint._write` 쓰기를 `core.kb_lock`으로 보호한다.
- **P-58**: `jira_act transition` 오프라인 실행은 transition 이름·id가 맞으면 캐시 없이도 outbox에 대기 기록으로 남긴다.
- **P-61**: `jira_act`는 `uncertain` 항목을 `MAX_ATTEMPTS` 재시도 뒤에도 `failed`가 아닌 `pending`(`last_error="uncertain"`)으로 유지한다.
- **P-67**: 모든 HTML 주석 무력화(`&lt;!-- ... --&gt;`)를 `notes.defang_comments` 단일 헬퍼로 통일한다.
- **P-69**: `ingest_images`와 `status`가 `lib.sidecar.sidecar_for`를 공유해 `source_image` 메타 일치를 함께 검사한다.
- **P-70**: `confluence_push`는 `http://`, `https://`, 문서 내부 상대/앵커 외 위험 스킴·로컬 경로 링크와 이미지를 제거·무력화한다.
- **P-74**: `init --yes`는 확인(`_confirm`) 프롬프트를 기본 승인(`True`)으로 건너뛰되 일반 설정 값·비밀값 입력은 그대로 묻는다.

---

## 4. Final Gates Verification (Post-`336955d`)

| Gate Command | Result |
|---|---|
| `.venv\Scripts\python.exe -m pytest -q` | `2231 passed, 4 skipped, 14 deselected in 573.77s (0:09:33)` |
| `.venv\Scripts\python.exe -m pytest -q -n auto` | `2231 passed, 4 skipped in 85.08s (0:01:25)` |
| `.venv\Scripts\python.exe -m pytest -q -m perf` | `1 passed, 2248 deselected in 9.35s` |
| `.venv\Scripts\python.exe -m pytest -q -m demo` | `5 passed, 2244 deselected in 16.08s` |
| `.venv\Scripts\python.exe -m pytest -q -m packaging` | `3 passed, 2246 deselected in 31.41s` |
| `.venv\Scripts\python.exe -m pytest -q tests/test_nvim.py` | `2 passed in 34.55s` |
| `C:\Users\kite7\scoop\shims\ruff.exe check . --statistics` | `Found 488 errors.` (baseline: 493; 0 new in diff) |
| `C:\Users\kite7\scoop\shims\ruff.exe check scripts --select F841` | `All checks passed!` |
| `.venv\Scripts\python.exe -m vulture scripts --min-confidence 60` | `wikimarkup._sub_tokens` & `lint._normalize_text` removed; remaining hits are expected interface/dynamic attributes |
| `agy`-owned repros (`A-12, A-13, A-15..A-25`, `C-10`, `C-19..C-32`, `C-37, C-39, C-40, C-42`) | 33 / 33 exited 0 (`TOTAL_REPRO_FAILURES=0`) |
