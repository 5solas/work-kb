# Jira 새 일감 양식·후보 제안 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 새 일감·담당·키·워크로그·JQL 입력에서 외워 치는 것을 없앤다.

**Architecture:** 읽기 명령 `kb.py meta`가 생성 정보와 사용자를 Jira에서 받고, `act create|promote`가 항목을 변환해 outbox로 보낸다. Neovim은 `ui.pick`과 두 창짜리 양식(`form.lua`)을 쓴다.

**Tech Stack:** Python 3.14, Neovim 0.12 Lua, pytest, `tests/nvim/run.lua`, `tests/mock_jira.py`

**Spec:** `docs/superpowers/specs/2026-11-25-jira-issue-form-design.md`

## Global Constraints

- 모든 Jira 쓰기는 확인(y) 뒤 outbox를 거친다. `meta`는 Jira에 쓰지 않는다.
- 후보를 받지 못해도 유형·요약·설명으로 만들 수 있다.
- 쓴 글을 잃지 않는다: 닫기·실패·불확실에서 내용을 기억한다.
- 담당자 검색 결과를 파일에 남기지 않는다.
- 명시한 키·daily 줄은 콜백이 끝날 때까지 바뀌지 않는다.
- 화면이 없으면 묻지 않고 취소로 본다.

## Review Focus

1. 같은 양식을 두 번 빨리 보냄(`C-c C-c` 연타) → 한 번만 보낸다. (Task 3)
2. 보내는 동안 양식 창을 닫음 → 결과를 알리고 내용은 기억한다. (Task 3)
3. 응답이 잘리거나 JSON이 없음 → 불확실로 다룬다. (Task 3)
4. 캐시가 다른 Jira 서버의 것이거나 깨짐 → 쓰지 않고 새로 받는다. (Task 1)
5. 양식이 열린 사이 daily 줄이 바뀜 → 승격을 거부하고 글을 둔다. (Task 1·3)

### Task 1: Python

Files: `scripts/jira_meta.py`(새), `scripts/jira_act.py`, `scripts/lib/jira_client.py`, `scripts/lib/outbox.py`, `scripts/issues.py`, `scripts/kb.py`, `tests/mock_jira.py`, `tests/test_jira_meta.py`(새), `tests/test_jira_act_fields.py`(새), `tests/test_issues.py`, `AGENTS.md`, `docs/README.md`

Produces:
- `kb.py meta create PROJECT --json [--refresh]` → `{project, fetched_at, stale, fallback, error, issuetypes: [{id, name, description: {available, required}, fields: [{id, name, required, has_default, kind, allowed}]}]}`
- `kb.py meta users (--project P | --issue KEY) [--query 글] --json` → `{me: {name, display}|null, users: [{name, display}]}` (`meta me`를 합침)
- `act create|promote … --json` 마지막 줄 `{"record", "state", "key", "linked", "message"}`; `--field ID=JSON`; `promote --summary --description --expect-line <줄의 sha256>`
- `act discard ID` (`failed` → `discarded`)
- `kb.py issues --json`에 `view_jql`

- [ ] 시험 작성 → 실패 확인 → 구현 → 통과 → 문서 일치 시험 → 커밋

### Task 2: 고르기

Files: `nvim/lua/work-kb/{ui,jira,proc,issues}.lua`, `tests/nvim/spec/jira_pick_spec.lua`(새), 기존 jira 시험의 바뀐 물음

Produces: `jira.resolve_key(opts, cb)`, `jira.pick_user({issue|project}, prompt, cb(user|nil))` (`user = {name, display}`), `ui.pick`은 콜백 오류를 문구로 보인다. 메뉴는 뜨는 창.

- [ ] 시험 작성 → 실패 확인 → 구현 → 통과 → 커밋

### Task 3: 양식

Files: `nvim/lua/work-kb/form.lua`(새), `nvim/lua/work-kb/{jira,issues}.lua`, `tests/nvim/spec/form_spec.lua`(새), `docs/USER-GUIDE.md`, `docs/DECISIONS.md`(D-NV11)

Produces: `form.open({ promote = {ref, line, text}? })`; `jira.create()`·`jira.promote()`가 연다.

- [ ] 시험 작성 → 실패 확인 → 구현 → 통과 → 실제 화면 확인 → 전체 시험 → 검토 → 머지·푸시
