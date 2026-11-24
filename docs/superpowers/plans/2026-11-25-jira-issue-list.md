# Jira 일감 목록 화면 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Neovim에서 Jira 일감을 훑고, 거르고, 골라서 기존 동작(댓글·전환·담당·워크로그)을 거는 목록 버퍼를 만든다.

**Architecture:** Python 읽기 전용 명령 `kb.py issues --json`이 `jira/*.md` front matter를 JSON으로 낸다. Lua 모듈 `work-kb.issues`가 그 JSON을 받아 거르고 정렬해 스크래치 버퍼에 그린다. 줄의 동작은 `work-kb.jira`의 기존 함수에 키를 명시해 넘긴다.

**Tech Stack:** Python 3.14 (표준 라이브러리 + 기존 `lib.*`), Neovim 0.12 Lua, pytest, 헤드리스 Lua 러너(`tests/nvim/run.lua`).

**Spec:** `docs/superpowers/specs/2026-11-25-jira-issue-list-design.md` (실행자는 이 문서도 함께 읽는다)

## Global Constraints

- 목록을 열 때 네트워크에 접속하지 않는다. `issues` 명령은 파일만 읽고 아무것도 쓰지 않는다.
- `jira/` 파일을 고치지 않는다.
- Lua는 front matter를 해석하지 않는다. 해석은 `lib.frontmatter`만 한다.
- 새 플러그인 의존성을 만들지 않는다. `vim.ui.select`만 쓴다.
- 화면이 없으면(`ui._can_prompt()` 거짓) 고르기는 취소다. 멈추지 않는다.
- Neovim 0.12, Windows, Python 3.14. 명령줄은 ASCII, 인수는 `proc.kb_args`(`--argv-stdin`)로 넘긴다.
- `KB_AGENT=1`에서 `issues`는 허용한다(`agent_ok=True`).
- 폭 계산은 `vim.fn.strdisplaywidth`로 한다(한글 2칸).
- 사용자에게 보이는 글과 주석은 한국어, 짧은 문장. 사용자 오류는 `ui.fail`, 알림은 `ui.notify`.
- 기존 호출(인수 없는 `jira.comment()` 등)의 동작은 바뀌지 않는다. 기존 시험은 고치지 않고 통과해야 한다.
- 커밋 문구는 이 저장소 형식: `feat(issues): …`, `test(issues): …`, `docs: …` (한국어 본문).

## 시험 실행

- Python: `.venv\Scripts\python -m pytest tests/test_issues.py -q` (전체: `.venv\Scripts\python -m pytest -q`)
- Neovim: `$env:WORK_KB_TEST_FILTER = "issues"; nvim --headless -u tests/nvim/minimal_init.lua -l tests/nvim/run.lua` (마지막 줄 `Ran N tests, P passed, 0 failed, S skipped`). 필터를 지우면 전체.

## Review Focus

1. `kb.py issues`가 실패하거나 JSON이 아닌 글을 낸다(Python 없음, 설정 오류) → traceback 없이 "일감 목록을 읽지 못했습니다 (이유)" 알림, 버퍼는 이전 내용 유지. → Task 3
2. 창 폭이 열 합계보다 좁거나 제목에 넓은 글자·이모지·탭이 있다 → 오류 없이 표시 폭 기준으로 자르고 열이 어긋나지 않는다. → Task 3
3. 커서가 머리줄·구분선·도움말 줄에 있을 때 동작 키를 누른다 → "일감 줄이 아닙니다" 사용자 오류, 키를 묻지 않는다. → Task 4
4. 비동기 동작(전환·동기화)이 도는 동안 목록 버퍼를 닫거나 지운다 → 끝난 뒤 콜백이 오류를 내지 않는다. → Task 4
5. 다시 읽은 뒤 거르기 값(담당·프로젝트·뷰)이 목록에서 사라졌다 → 거르기는 머리줄에 남고, 빈 목록에 "x로 거르기를 지우세요"를 보인다. → Task 3

---

### Task 1: Python `kb.py issues`

**Files:**
- Create: `scripts/issues.py`, `tests/test_issues.py`
- Modify: `scripts/kb.py:74-99` (COMMANDS), `scripts/serve.py:44-51` (SERVED), `AGENTS.md` (허용 명령 블록, 명령 표, `serve` 행), `docs/README.md` (명령 표, `kb.py serve` 행)

**Interfaces:**
- Consumes: `lib.frontmatter.parse(bytes) -> dict`, `lib.notes.read_view(cfg, name) -> list[dict[str, str]]`(한글 열 이름 `키·제목·상태·담당·마감·갱신`), `core.load_state(cfg.root, "sync")`, `jira_sync._disk_records`의 스텁 판정(`jira_sync.py:532-547`: `status_category` 없는 파일은 스텁).
- Produces:
  - `issues.FIELDS = ("key","project","summary","status","status_category","issuetype","priority","assignee","sprint","labels","components","updated","due","url")`
  - `issues.read_issues(cfg, *, include_out_of_scope: bool = False) -> tuple[list[dict], list[str]]` (행, 건너뛴 파일 설명)
  - `issues.view_names(cfg) -> list[str]` (`jira/_views/*.md`의 stem, 정렬)
  - `issues.collect(cfg, *, all_: bool = False, view: str | None = None) -> dict` → `{"synced_at": str|None, "views": [...], "issues": [...]}`
  - `issues.main(argv) -> int`
  - CLI: `kb.py issues [--root R] [--json] [--all] [--view NAME]`

행 규칙: `labels`·`components`는 문자열 목록, 나머지는 문자열(없으면 `""`). YAML이 날짜·숫자·null로 읽은 값도 문자열로 바꾼다(날짜는 `isoformat()`). 범위 밖 행에만 `"in_scope": false`를 붙인다. 뷰에서 파일이 없거나 스텁인 키는 뷰 표의 값(키·제목·상태·담당·마감·갱신)만 채우고 `"cached": false`를 붙인다. 기본 정렬은 키 순(프로젝트, 번호). 뷰는 뷰 표의 순서.

- [ ] **Step 1: 실패하는 시험을 쓴다** (`tests/test_issues.py`). `vault`·`run_kb` 픽스처를 쓰고, 일감 파일은 `jira_sync.render_doc`로 만든다(직접 front matter를 쓰지 않는다. 깨진 파일만 손으로 쓴다).

```python
def test_json_schema_and_fields(vault, run_kb):
    # KB-12(indeterminate), KB-2(done) 두 파일
    out = json.loads(run_kb("issues", "--json").stdout)
    assert set(out) == {"synced_at", "views", "issues"}
    assert [i["key"] for i in out["issues"]] == ["KB-2", "KB-12"]   # 번호 순, 완료 포함
    assert set(out["issues"][0]) == set(issues.FIELDS)
    assert isinstance(out["issues"][0]["labels"], list)

def test_skips_stub_views_and_out_of_scope(vault, run_kb): ...
    # 스텁(status_category 없음), jira/_views/today.md, in_scope: false 파일 → 기본 출력에 없음
def test_all_includes_out_of_scope_with_flag(vault, run_kb): ...
    # --all → 그 행에 "in_scope": False, 다른 행에는 in_scope 키 없음
def test_view_order_and_uncached_rows(vault, run_kb): ...
    # _views/today.md 표: KB-12, ZZ-9(파일 없음), KB-3(스텁) →
    # 키 순서 그대로, ZZ-9·KB-3은 "cached": False, summary·status는 표의 값, priority == ""
    # in_scope: false인 파일이 뷰에 있으면 온전한 행 + "in_scope": False
def test_unknown_view_exits_1_and_lists_names(vault, run_kb): ...
    # returncode == 1, stderr에 "today" 포함
def test_broken_front_matter_is_skipped(vault, run_kb): ...
    # returncode == 0, 그 키 없음, stderr에 파일 이름
def test_yaml_typed_values_become_strings(vault, run_kb): ...
    # due: 2026-11-28 (YAML date), summary: 123, assignee: null, labels: "a" →
    # "2026-11-28", "123", "", ["a"]
def test_synced_at_is_last_sync_or_null(vault, run_kb): ...
def test_empty_vault(vault, run_kb): ...
    # jira/ 없음 → {"synced_at": None, "views": [], "issues": []}, returncode 0
def test_plain_table_output(vault, run_kb): ...
    # --json 없이: 줄마다 키·상태·제목·담당·마감, 완료 포함
def test_allowed_under_kb_agent(vault, run_kb): ...
    # env={"KB_AGENT": "1"} → returncode 0
def test_writes_nothing(vault, run_kb): ...
    # 실행 전후 볼트 전체의 (경로, mtime, 크기)가 같다 (.state 포함)
def test_served(…): ...
    # serve.SERVED["issues"] is None, serve.run_argv(["issues", "--root", str(vault), "--json"])의 out이 같은 JSON
```

- [ ] **Step 2: 실패 확인.** Run: `.venv\Scripts\python -m pytest tests/test_issues.py -q` → `ModuleNotFoundError: issues` 또는 "알 수 없는 명령".
- [ ] **Step 3: `scripts/issues.py` 구현.** 위 Interfaces의 함수. 인수 파서는 `status.py`의 형식(`--root`, `allow_abbrev=False`)을 따른다. 깨진 파일은 `problems`에 `"jira/KB-9.md: <오류>"`로 넣고 `main`이 stderr에 한 줄씩 쓴다.
- [ ] **Step 4: 등록.** `kb.COMMANDS["issues"] = Command("issues", help="Jira 일감 캐시 목록 (--json, --all, --view)")` (`status` 줄 아래). `serve.SERVED["issues"] = None`.
- [ ] **Step 5: 문서 일치.** `AGENTS.md`: 허용 블록에 `python scripts/kb.py issues`, 명령 표에 행 하나, `serve` 행의 처리 목록에 `issues`. `docs/README.md`: 명령 표와 `kb.py serve` 행에 같은 내용.
- [ ] **Step 6: 통과 확인.** Run: `.venv\Scripts\python -m pytest tests/test_issues.py tests/test_docs.py tests/test_repo_tree.py tests/test_serve.py tests/test_kb_dispatch.py -q` → 모두 통과.
- [ ] **Step 7: 커밋.** `feat(issues): kb.py issues 읽기 명령 (캐시 목록 JSON, 뷰, serve)`

---

### Task 2: `jira.lua` 동작에 선택 인수

**Files:**
- Modify: `nvim/lua/work-kb/jira.lua` (`act_run` 398-408, `view` 449, `comment` 455, `transition` 473, `assign` 511, `worklog` 526, `sync` 619)
- Test: `tests/nvim/spec/jira_spec.lua` (끝에 추가)

**Interfaces:**
- Produces (모두 선택 인수, 없으면 지금과 같다):
  - `jira.view(opts)`, `jira.comment(opts)`, `jira.transition(opts)`, `jira.assign(opts)`, `jira.worklog(opts)`
  - `opts = { key = string?, initial = string?, on_done = fun(code: integer)? }`
  - `opts.key`가 있으면 `M.jira_key()`를 부르지 않는다. Jira 키 꼴이 아니면 기존 "Jira 키가 아닙니다" 오류.
  - `opts.initial`은 `comment`만 쓴다: 기본 본문을 현재 줄 대신 이 값으로. 이때 `--from-line`은 쓰지 않고 항상 `--body=`.
  - `opts.on_done(code)`는 `act` 프로세스가 끝난 뒤, 기존 알림 다음에 한 번 부른다. 사용자가 취소하면 부르지 않는다.
  - `jira.sync(opts)`: `opts.on_done(code)`를 기존 알림 다음에 부른다.
- `M.run(name)`과 `M.menu()`는 지금처럼 인수 없이 부른다.

- [ ] **Step 1: 실패하는 시험.** 기존 `record_runs`·`H.ui_stub`·`H.fake_spawn`을 쓴다.

```lua
it("transition with opts.key does not read the cursor", function()
  -- 버퍼 현재 줄: "KB-99 후속 작업", opts.key = "KB-12"
  -- 기대: 첫 run 인수에 "transitions", "KB-12" 가 있고 "KB-99"는 없다
end)
it("comment with opts.initial starts empty and never uses --from-line", function()
  -- daily 파일의 줄에서 jira.comment({ key = "KB-12", initial = "" }), 답 { "input", "확인함" }
  -- 기대: 인수에 "--body=확인함", "--from-line" 없음. 답이 ":initial"이면 "빈 댓글은 보내지 않습니다"
end)
it("on_done gets the exit code after the notification", function()
  -- assign({ key = "KB-12", on_done = f }) 종료 0 → f(0) 한 번, 종료 3 → f(3)
end)
it("on_done is not called when the user cancels", function() ... end)
it("opts.key that is not a key fails", function()
  -- H.fails_with(function() jira.view({ key = "nope" }) end, "Jira 키가 아닙니다")
end)
it("sync on_done", function() ... end)
```

- [ ] **Step 2: 실패 확인.** Run: `$env:WORK_KB_TEST_FILTER = "jira"; nvim --headless -u tests/nvim/minimal_init.lua -l tests/nvim/run.lua` → 새 시험 실패.
- [ ] **Step 3: 구현.** 지역 함수 `resolve_key(opts)` 하나를 두고 다섯 동작이 쓴다. `act_run`의 셋째 인수 `after`에 `opts.on_done`을 잇는다.
- [ ] **Step 4: 통과 확인.** 같은 명령 → 새 시험과 기존 jira 시험 모두 통과.
- [ ] **Step 5: 커밋.** `feat(jira): 동작 함수에 선택 인수 (key, initial, on_done)`

---

### Task 3: `issues.lua` 자료와 그리기 (창·키 없음)

**Files:**
- Create: `nvim/lua/work-kb/issues.lua`, `tests/nvim/spec/issues_spec.lua`

**Interfaces:**
- Consumes: Task 1의 CLI 출력, `proc.kb_args`, `proc.run_output(args, {buffer=})`(`jira.read_project`와 같은 방식), `jira.parse_json_output(text)`, `require("work-kb.time").day()`.
- Produces:
  - `M.BUFFER = "*work-kb-issues*"`, `M.JSON_BUFFER = "*work-kb-issues-json*"`
  - `M.state = { status = "open", project = nil, assignee = nil, view = nil, data = nil }` — `status`는 `"open" | "all" | "done"`
  - `M.fetch(view: string?) -> data|nil, err: string?` — `kb.py issues --json [--view NAME]`. 실패·JSON 아님이면 `nil, 이유`
  - `M.filter(issues, state) -> issues` — open: `status_category ~= "done"`, done: `== "done"`. `project`·`assignee`는 값이 같을 때만. `state.view`가 있으면 status는 무시한다(전체)
  - `M.sort(issues, by_view: boolean) -> issues` — `by_view`면 그대로. 아니면 범주 순(`indeterminate` → `new` → 그 밖·빈 값 → `done`), 같은 범주는 `updated` 내림차순, 같으면 키 순
  - `M.values(issues, field) -> string[]` — 그 항목의 서로 다른 비어 있지 않은 값, 정렬(고르기 후보)
  - `M.render(data, state, width: integer, today: string) -> { lines = string[], rows = { [lnum] = issue }, marks = { {lnum, col_start, col_end, hl} } }`
  - 강조 그룹: `WorkKbIssueProgress`, `WorkKbIssueTodo`, `WorkKbIssueDone`, `WorkKbIssueOverdue`, `WorkKbIssueUncached`, `WorkKbIssueHeader` (기본 연결은 구현자가 정한다)

그리기 규칙 (spec 5.2의 그림과 같다):
- 1줄: ` Jira 일감  [미완료|전체|완료]  프로젝트: <값|전체>  담당: <값|전체>  뷰: <이름|—>   <보인 수>/<전체 수>건   동기화 <HH:MM|없음>`
- 2줄: 구분선(`─` 반복, 창 폭). 마지막 두 줄: 구분선과 도움말 `<CR> 열기  p 미리 보기  s 상태  P 프로젝트  A 담당  V 뷰  x 거르기 지움  g? 도움말`
- 일감 줄: ` KEY  상태  우선순위  제목  담당  마감`. 줄은 공백 하나 뒤 **맨 키**로 시작한다. 키·상태·우선순위·담당 열 폭은 보이는 행의 최대 표시 폭(상태·담당은 최대 12칸에서 자름). 마감은 `MM-DD`, 없으면 `—`. 빈 우선순위는 `—`. 제목이 남는 폭을 쓴다(최소 10칸). 자른 글 끝은 `…`.
- 폭이 모자라면 우선순위 → 담당 → 마감 순으로 열을 숨긴다.
- 제목의 탭·줄바꿈은 공백 하나로 바꾼다.
- 마감이 `today`보다 이르고 범주가 `done`이 아니면 마감 칸에 `WorkKbIssueOverdue`. `cached == false`인 줄은 줄 전체 `WorkKbIssueUncached`.
- 일감이 0건: 캐시가 비었으면(`#data.issues == 0`) ` 캐시에 일감이 없습니다. S로 동기화하세요`, 거르기 때문이면 ` 조건에 맞는 일감이 없습니다. x로 거르기를 지우세요`.

- [ ] **Step 1: 실패하는 시험.** `M.fetch`는 `H.fake_spawn`으로, 나머지는 순수 함수로 시험한다.

```lua
it("fetch parses issues --json and passes --view", ...)
it("fetch returns nil and a reason on non-zero exit and on non-JSON output", ...)   -- Review Focus 1
it("filter: open hides done, done shows only done, view ignores status", ...)
it("filter: project and assignee match exactly", ...)
it("sort: indeterminate, new, unknown, done; then updated desc; then key", ...)
it("sort keeps view order", ...)
it("values are distinct, non-empty and sorted", ...)
it("render: header shows filters, counts and sync time", ...)
  -- state { status="open", assignee="김철수" }, 3건 중 1건 →
  -- lines[1]에 "[미완료]", "담당: 김철수", "1/3건", "동기화 09:10"
it("render: every issue line starts with a bare key and rows maps the line", ...)
  -- lines[3]:match("^ ([A-Z][A-Z0-9_]+%-%d+) ") == rows[3].key
it("render: columns align by display width with Korean text", ...)
  -- 모든 일감 줄에서 제목 열의 시작 표시 폭(strdisplaywidth(줄:sub(1, 시작-1)))이 같다
it("render: narrow width hides priority, then assignee, then due, never errors", ...)  -- Review Focus 2
  -- width 80, 50, 30, 10 모두 오류 없음. 모든 줄의 strdisplaywidth <= math.max(width, 최소 폭)
it("render: wide characters, emoji and tabs in the summary are cut by display width", ...)  -- Review Focus 2
it("render: overdue mark only when due < today and not done", ...)
it("render: uncached rows get WorkKbIssueUncached", ...)
it("render: empty cache and empty-by-filter hints differ", ...)   -- Review Focus 5
  -- assignee = "없는 사람" → 머리줄에 "담당: 없는 사람", 본문에 "x로 거르기를 지우세요"
it("render: 5000 issues under 500 ms", ...)   -- vim.uv.hrtime으로 잰다
```

- [ ] **Step 2: 실패 확인.** Run: 필터 `issues`로 Neovim 러너 → `module 'work-kb.issues' not found`.
- [ ] **Step 3: 구현.** 위 Interfaces와 그리기 규칙대로.
- [ ] **Step 4: 통과 확인.** 같은 명령 → 모두 통과.
- [ ] **Step 5: 커밋.** `feat(issues): 일감 목록 자료·거르기·정렬·그리기`

---

### Task 4: 목록 버퍼, 키, 진입점, 사용자 문서

**Files:**
- Modify: `nvim/lua/work-kb/issues.lua`, `nvim/lua/work-kb/ui.lua` (새 함수 `pick`), `nvim/lua/work-kb/init.lua:24-54` (keys), `nvim/lua/work-kb/jira.lua:659-686` (MENU), `tests/nvim/helpers.lua` (`H.ui_stub`에 `pick`), `tests/nvim/spec/issues_spec.lua`, `docs/USER-GUIDE.md` (6장 키 표, 6.4절 메뉴 표, 새 절 "일감 목록"), `docs/DECISIONS.md` (`D-NV10`), `tests/test_docs.py:247-249` (필수 키 집합에 `J`와 `l` 추가)

**Interfaces:**
- Consumes: Task 2의 `jira.<동작>({ key, initial, on_done })`, `jira.sync({ on_done })`, `jira.create()`; Task 3의 `fetch`·`filter`·`sort`·`values`·`render`·`state`; `open.open_key(key)`, `open.jira_file(key)`; `proc.kb_args`, `proc.run`.
- Produces:
  - `ui.pick(prompt: string, items: string[], cb: fun(choice: string|nil))` — `vim.ui.select`를 감싼다. 화면이 없으면 묻지 않고 `cb(nil)`. `H.ui_stub`의 답 종류는 `{ "pick", "값" }`
  - `issues.open()` — 버퍼를 만들고(이미 보이면 그 창으로) 읽어 그린다. `init.keys`의 `{ key = "J", name = "issues", module = "work-kb.issues", fn = "open" }`
  - `issues.refresh(opts)` — `opts.keep_data`가 아니면 `fetch` 후 다시 그린다. 커서는 전에 있던 키의 줄로(없으면 첫 일감 줄). 버퍼가 유효하지 않으면 아무것도 하지 않는다
  - `issues.key_at_cursor() -> string` — 일감 줄이 아니면 `ui.fail("일감 줄이 아닙니다")`
  - `jira.MENU` "조회·동기화" 무리에 `{ key = "l", fn = "list", label = "일감 목록" }`, `jira.list = function() return require("work-kb.issues").open() end`

버퍼: `buftype=nofile`, `bufhidden=hide`, `swapfile=false`, `modifiable=false`, `filetype=work-kb-issues`, `cursorline`(창), `wrap=false`(창). `WinResized`·`VimResized`에서 `refresh({ keep_data = true })`.

키 (모두 버퍼 지역, `nowait`, `desc`는 `work-kb: `로 시작):

| 키 | 동작 |
|---|---|
| `<CR>`, `o` | `open.open_key(key)` |
| `p` | 미리 보기 켜고 끄기. 켜면 `jira/KEY.md`를 아래 분할 창(초점은 목록)에 보이고 `CursorMoved`에서 따라간다. 파일이 없으면 미리 보기 창을 비우고 알림 없이 둔다 |
| `s` | `state.status`: open → all → done → open. `refresh({ keep_data = true })` |
| `P`, `A` | `ui.pick`에 `{ "전체", unpack(values(data.issues, "project"|"assignee")) }`. "전체"면 nil. 취소면 그대로 |
| `V` | `ui.pick`에 `{ "—", unpack(data.views) }`. 고르면 `state.view = 이름`, `fetch(이름)` 후 그림. "—"면 nil |
| `x` | status = "open", project·assignee·view = nil, 다시 읽어 그림 |
| `c` | `jira.comment({ key = key, initial = "" })` |
| `t`, `a` | `jira.transition`·`jira.assign`에 `{ key, on_done }`. `on_done(0)`이면 `proc.run(proc.kb_args("sync", "--key", key), …)` 후 `refresh()`. 0이 아니면 아무것도 하지 않는다 |
| `w` | `jira.worklog({ key = key })` |
| `+` | `jira.create()` |
| `S` | `jira.sync({ on_done = function(code) if code == 0 then refresh() end end })` |
| `r` | `refresh()` |
| `y` | 키를 `"` 레지스터와 `+` 레지스터에 넣고 "KB-12 복사" 알림 |
| `g?` | 키 표를 `ui.notify`로 |
| `q` | `results.quit()`과 같은 규칙으로 닫는다. 미리 보기 창도 닫는다 |

`n`·`N`·`/`는 붙이지 않는다(Vim 검색).

- [ ] **Step 1: 실패하는 시험** (`issues_spec.lua`에 추가). `M.fetch`는 `H.patch`로 고정 자료를 돌려주게 한다. `jira`·`open` 모듈은 `jira_spec.lua`의 `stub_open` 방식으로 기록한다.

```lua
it("open draws the buffer read-only and puts the cursor on the first issue", ...)
it("open twice reuses the buffer and the window", ...)
it("open with a failing fetch notifies and keeps the old lines", ...)          -- Review Focus 1
it("<CR> opens the key of the cursor line", ...)
it("action on header, separator or help line fails with '일감 줄이 아닙니다' and asks nothing", ...)  -- Review Focus 3
it("t passes the row key even when the cursor is on another key in the summary", ...)
  -- 줄 "KB-12 … KB-99 후속", 커서를 "KB-99" 위에 → jira.transition이 받은 opts.key == "KB-12"
it("c starts from an empty body", ...)   -- opts.initial == ""
it("after a successful transition it syncs that key and redraws", ...)
  -- on_done(0) → proc.run 인수에 "sync", "--key", "KB-12" → 끝난 뒤 fetch 한 번 더
it("a failed transition does not sync or redraw", ...)   -- on_done(3)
it("on_done after the buffer was wiped does not error", ...)   -- Review Focus 4
  -- on_done을 잡아 두고 nvim_buf_delete(force) 뒤에 부른다. sync의 on_exit도 같다
it("s cycles open, all, done", ...)
it("A offers 전체 plus the assignees present and filters", ...)   -- stub.last_items 확인
it("P/A/V cancelled leave the state unchanged", ...)
it("V refetches with --view and x clears every filter", ...)
it("refresh keeps the cursor on the same key, or the first row when it is gone", ...)
it("a filter value that disappeared stays in the header with the clear hint", ...)   -- Review Focus 5
it("no UI: pick is cancelled, drawing still works", ...)   -- ui._can_prompt = false
it("p shows jira/KEY.md below without moving focus and follows the cursor", ...)
it("q closes the list and the preview window", ...)
it("n and N are not mapped in the buffer", ...)
  -- vim.fn.maparg("n", "n", false, true)가 버퍼 지역 매핑이 아님
it(":WorkKb issues, <prefix>J and jira menu l all call issues.open", ...)
```

- [ ] **Step 2: 실패 확인.** Neovim 러너(필터 `issues`) → 새 시험 실패.
- [ ] **Step 3: `ui.pick`과 `H.ui_stub`의 `pick`.** `ui.select`(동기)는 그대로 둔다.
- [ ] **Step 4: `issues.lua`에 버퍼·키·미리 보기·`open`·`refresh`·`key_at_cursor` 구현.**
- [ ] **Step 5: 진입점.** `init.keys`에 `J`, `jira.MENU`에 `l`과 `jira.list`.
- [ ] **Step 6: 문서.** `docs/USER-GUIDE.md`: 키 표에 `` `J` | `issues` `` 행, 6.4절 표에 `` `l` | `list` `` 행, 새 절에 화면 그림과 위 키 표. `docs/DECISIONS.md` 6장에 `D-NV10`(일감 목록: 접근 A 선택 이유, `issues` 명령, `jira.lua` 선택 인수, 2단계 예고)을 기존 `D-NV9` 형식으로. `tests/test_docs.py`의 필수 키 집합에 `"J"`와 `"l"` 추가.
- [ ] **Step 7: 통과 확인.** Neovim 러너 전체(필터 없음) → `0 failed`. `.venv\Scripts\python -m pytest -q` → 모두 통과.
- [ ] **Step 8: 실제 화면 확인.** `examples/sample-vault`를 볼트로 Neovim을 띄워 `<leader>kJ`를 눌러 본다. 캐시가 비면 `jira_sync.render_doc`로 일감 파일 몇 개를 임시 볼트에 만들어 확인한다. 본 것(정렬·색·미리 보기·좁은 창)을 보고에 적는다.
- [ ] **Step 9: 커밋.** `feat(issues): 일감 목록 버퍼·키·미리 보기, <leader>kJ와 Jira 메뉴 l` / `docs: 일감 목록(D-NV10) 사용자 안내`
