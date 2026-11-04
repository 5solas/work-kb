"""jira_sync (W2.4): 전체·증분, mentions 바이트 보존, --key, --jql(--cache), --dry-run, 뷰, 프로젝트 표, 종료 코드."""
from __future__ import annotations

import datetime as dt
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

import jira_sync
import mock_jira
from lib import config, core, fts, frontmatter, managed, notes
from lib import secrets as kb_secrets

TOKEN = "sync-test-token-1234"
NOW = "2026-11-04T16:20:03+09:00"  # 모의 Jira의 '지금'(KB-12 updated 16:05:11) 뒤
ALL_KEYS = {"KB-3", "KB-5", "KB-7", "KB-9", "KB-10", "KB-11", "KB-12", "KB-14", "KB-15", "OPS-1", "OPS-2", "OPS-4", "OPS-7", "OPS-8", "OPS-10"}
SCOPE_KEYS = ALL_KEYS - {"KB-15", "OPS-1", "KB-11"}  # 30일보다 오래됨, 내 일감 아님(KB-11)


# ---------------------------------------------------------------- 준비


@pytest.fixture
def start_jira():
    started = []

    def _start(**modes):
        server = mock_jira.start(**modes)
        started.append(server)
        return server

    yield _start
    for server in started:
        server.stop()


def _configure(vault: Path, base_url: str, **jira) -> config.Config:
    kb_secrets.store("work-kb", "jira", TOKEN)
    section = {"base_url": base_url, "project_keys": ["KB", "OPS"], "pat_ref": "keyring:work-kb/jira", "trust_env": False}
    section.update(jira)
    config.save_local(vault, {"environment": "home", "jira": section})
    return config.load(vault)


@pytest.fixture
def env(vault, start_jira, fake_now):
    fake_now(NOW)
    server = start_jira()
    cfg = _configure(vault, server.base_url)
    return SimpleNamespace(vault=vault, jira=server, cfg=cfg)


def run(capsys, vault: Path, *args: str) -> tuple[int, str, str]:
    """core.run_main처럼 KbError를 종료 코드로 바꾼다."""
    capsys.readouterr()
    try:
        code = jira_sync.main([*args, "--root", str(vault)])
    except core.KbError as exc:
        print(f"오류: {exc}", file=__import__("sys").stderr)
        code = exc.exit_code
    out, err = capsys.readouterr()
    return code, out, err


def jira_files(vault: Path) -> dict[str, bytes]:
    return {p.stem: p.read_bytes() for p in sorted((vault / "jira").glob("*.md"))}


def tree(vault: Path) -> dict[str, bytes]:
    """볼트 전체 파일(로그 제외) → 바이트."""
    out = {}
    for path in sorted(vault.rglob("*")):
        if path.is_file() and "logs" not in path.relative_to(vault).parts and path.name != "kb.lock":
            out[path.relative_to(vault).as_posix()] = path.read_bytes()
    return out


def searches(server) -> list[str]:
    return [entry["query"].get("jql", "") for entry in server.log if entry["path"] == "/rest/api/2/search"]


def state(vault: Path) -> dict:
    return json.loads((vault / ".state" / "sync.json").read_text(encoding="utf-8"))


def set_now(monkeypatch, value: str) -> None:
    monkeypatch.setenv("KB_FAKE_NOW", value)


# ---------------------------------------------------------------- 전체


def test_full_sync_writes_issue_files(env, capsys):
    code, out, err = run(capsys, env.vault)
    assert code == 0, err
    assert "sync 전체" in out
    files = jira_files(env.vault)
    assert set(files) == SCOPE_KEYS
    # 모의 서버의 해석기와 같은 범위
    scope = jira_sync.scope_jql(env.cfg)
    assert scope.startswith('project in ("KB","OPS") AND (')
    assert {i["key"] for i in mock_jira.evaluate_jql(env.jira, scope)} == SCOPE_KEYS
    assert searches(env.jira)[0] == scope + " ORDER BY key ASC"

    data = files["KB-12"]
    meta = frontmatter.parse(data)
    assert meta["key"] == "KB-12"
    assert meta["summary"] == "캐시 front matter 확정"
    assert meta["status"] == "In Progress"
    assert meta["status_category"] == "indeterminate"
    assert meta["issuetype"] == "Task"
    assert meta["priority"] == "Medium"
    assert meta["assignee"] == "홍길동"
    assert meta["reporter"] == "김철수"
    assert meta["project"] == "KB"
    assert meta["sprint"] == "KB 스프린트 7"
    assert meta["labels"] == ["cache", "schema"]
    assert meta["components"] == []
    assert meta["created"] == "2026-10-28T09:00:00+09:00"
    assert meta["updated"] == "2026-11-04T16:05:11+09:00"
    assert meta["due"] == "2026-11-06"
    assert meta["url"] == f"{env.jira.base_url}/browse/KB-12"
    assert meta["synced_at"] == NOW
    assert "stub" not in meta
    text = data.decode("utf-8")
    assert "\n# KB-12 캐시 front matter 확정\n" in text
    assert "> 동기화: 2026-11-04 16:20. 이 파일은 자동 생성되며" in text
    # 설명: wiki markup → 마크다운
    # 설명의 제목은 두 단계 내린다(파일의 ## 설명·## 댓글과 섞이지 않게)
    assert "\n#### 배경\n" in text and "**front matter**" in text
    assert [line for line in text.splitlines() if line.startswith("## ")] == ["## 설명", "## 댓글", "## 노트에서 언급"]
    # 댓글: 검색 응답에는 3개뿐 → total 5까지 따로 받는다
    comments = notes.section_span(data, "댓글")
    body = data[comments[0]:comments[1]].decode("utf-8")
    lines = [line for line in body.splitlines() if line.startswith("- ")]
    assert len(lines) == 5
    assert lines[0] == "- 2026-11-01 10:02 김철수: 필드는 최소로 가자"
    assert lines[1] == "- 2026-11-01 11:15 홍길동: 좋습니다. sprint는 이름만 넣겠습니다."
    assert lines[2] == "- 2026-11-02 09:40 김철수: **due**가 없으면 빈 값으로 두세요"  # 첫 줄도 마크다운으로
    assert "{code}" not in lines[3] and not lines[3].endswith(": ```")
    assert managed.read_region(data, "mentions") == b""
    # Done·없는 값
    kb3 = frontmatter.parse(files["KB-3"])
    assert kb3["status_category"] == "done"

    saved = state(env.vault)
    assert saved["last_sync"] == NOW and saved["last_full"] == NOW
    assert saved["counts"]["issues"] == len(SCOPE_KEYS)
    assert saved["counts"]["views"] == {name: len(notes.read_view(env.cfg, name)) for name in ("today", "blocked", "review")}


def test_sync_never_touches_daily_inbox_raw(env, capsys):
    (env.vault / "daily" / "2026-11-04.md").write_bytes("## 기록\n- 09:12 KB-12 메모\n".encode("utf-8"))
    (env.vault / "inbox" / "20261104-0910-메모.md").write_bytes("---\ntitle: 메모\n---\nKB-5\n".encode("utf-8"))
    (env.vault / "raw" / "assets" / "a.png").write_bytes(b"\x89PNG")
    before = {k: v for k, v in tree(env.vault).items() if k.split("/")[0] in ("daily", "inbox", "raw")}
    assert run(capsys, env.vault)[0] == 0
    after = {k: v for k, v in tree(env.vault).items() if k.split("/")[0] in ("daily", "inbox", "raw")}
    assert after == before


def test_no_churn_when_unchanged(env, capsys, monkeypatch):
    assert run(capsys, env.vault, "--full")[0] == 0
    before = tree(env.vault)
    set_now(monkeypatch, "2026-11-04T17:00:00+09:00")
    code, out, _ = run(capsys, env.vault, "--full")
    assert code == 0
    after = tree(env.vault)
    changed = {k for k in after if after[k] != before.get(k)}
    assert changed <= {".state/sync.json", "jira/.cache.sqlite"}, changed  # 캐시는 받은 행을 늘 다시 넣는다
    assert ".state/sync.json" in changed
    assert "새 파일 0 · 바뀜 0" in out


# ---------------------------------------------------------------- 증분


def test_incremental_after_full(env, capsys, monkeypatch):
    assert run(capsys, env.vault)[0] == 0
    before = jira_files(env.vault)
    new_updated = env.jira.touch("KB-5", summary="마크업 변환기 2차")  # 모의 지금 + 1분 = 16:06:11
    set_now(monkeypatch, "2026-11-04T16:30:03+09:00")  # 마지막 동기화 10분 뒤
    env.jira.log.clear()
    code, out, err = run(capsys, env.vault)
    assert code == 0, err
    assert "sync 증분" in out
    jql = searches(env.jira)[0]
    assert jql == jira_sync.scope_jql(env.cfg) + " AND updated >= '-15m' ORDER BY key ASC"
    after = jira_files(env.vault)
    assert frontmatter.parse(after["KB-5"])["summary"] == "마크업 변환기 2차"
    assert frontmatter.parse(after["KB-5"])["updated"] == jira_sync._iso(new_updated)
    # 구간 안이지만 바뀌지 않은 KB-12는 다시 쓰지 않는다
    assert after["KB-12"] == before["KB-12"]
    assert {k for k in after if after[k] != before[k]} == {"KB-5"}
    saved = state(env.vault)
    assert saved["last_sync"] == "2026-11-04T16:30:03+09:00"
    assert saved["last_full"] == NOW
    assert saved["counts"]["issues"] == 2  # KB-5, KB-12 (모의 지금 16:06:11 - 15분)


def test_old_last_sync_means_full(env, capsys, monkeypatch):
    assert run(capsys, env.vault)[0] == 0
    saved = state(env.vault)
    saved["last_sync"] = "2026-10-09T16:00:00+09:00"  # 26일 전
    (env.vault / ".state" / "sync.json").write_text(json.dumps(saved), encoding="utf-8")
    env.jira.log.clear()
    code, out, _ = run(capsys, env.vault)
    assert code == 0 and "sync 전체" in out
    assert "updated >= '-" not in searches(env.jira)[0].replace("updated >= '-30d'", "")
    assert state(env.vault)["last_full"] == NOW


def test_missing_cache_means_full(env, capsys, monkeypatch):
    assert run(capsys, env.vault)[0] == 0
    fts.cache_path(env.cfg).unlink()
    set_now(monkeypatch, "2026-11-04T16:30:03+09:00")
    code, out, _ = run(capsys, env.vault)
    assert code == 0 and "sync 전체" in out
    assert fts.search(env.cfg, ["캐시"])


def test_plan_scope_minutes(env):
    start = dt.datetime.fromisoformat("2026-11-04T16:30:30+09:00")
    kind, jql = jira_sync.plan_scope(env.cfg, {"last_sync": NOW}, start, full=False)
    assert kind == "full"  # 캐시가 아직 없다
    fts.open_cache(env.cfg).close()
    kind, jql = jira_sync.plan_scope(env.cfg, {"last_sync": NOW}, start, full=False)
    assert kind == "incremental" and jql.endswith("updated >= '-16m'")  # 10분 27초 → 11 + 5
    kind, _ = jira_sync.plan_scope(env.cfg, {"last_sync": NOW}, start, full=True)
    assert kind == "full"
    kind, _ = jira_sync.plan_scope(env.cfg, {"last_sync": "망가진 값"}, start, full=False)
    assert kind == "full"


# ---------------------------------------------------------------- mentions 보존


def test_mentions_preserved_byte_for_byte(env, capsys, monkeypatch):
    import link

    (env.vault / "daily" / "2026-11-04.md").write_bytes(
        "## 기록\n- 09:12 [[KB-12]] @김철수 와 front matter 논의\n- 10:00 KB-12 두 번째 메모\n".encode("utf-8")
    )
    link.run(env.cfg)  # 동기화 전: stub + 언급
    stub = (env.vault / "jira" / "KB-12.md").read_bytes()
    assert frontmatter.parse(stub)["stub"] == "true"
    region = managed.read_region(stub, "mentions")
    assert region and "@김철수".encode("utf-8") in region

    assert run(capsys, env.vault)[0] == 0
    first = (env.vault / "jira" / "KB-12.md").read_bytes()
    assert "stub" not in frontmatter.parse(first)
    assert managed.read_region(first, "mentions") == region

    # 사람이 구역 안을 이상하게 고쳐도(공백, CR, 한글) 바이트 그대로
    odd = region + "  - 손으로 쓴 줄 \t끝 공백  \r\n- 두 번째\r\n".encode("utf-8")
    edited = first.replace(region, odd)
    (env.vault / "jira" / "KB-12.md").write_bytes(edited)
    env.jira.touch("KB-12", summary="캐시 front matter 최종 확정")
    set_now(monkeypatch, "2026-11-04T16:40:00+09:00")
    assert run(capsys, env.vault, "--full")[0] == 0
    second = (env.vault / "jira" / "KB-12.md").read_bytes()
    assert frontmatter.parse(second)["summary"] == "캐시 front matter 최종 확정"
    assert managed.read_region(second, "mentions") == odd
    start = second.index(b"<!-- kb:mentions:start -->")
    assert second[start:].startswith(b"<!-- kb:mentions:start -->\n" + odd + b"<!-- kb:mentions:end -->")

    # link를 다시 돌려도 diff 없음(같은 언급)
    link.run(env.cfg)
    assert managed.read_region((env.vault / "jira" / "KB-12.md").read_bytes(), "mentions") == region


def test_crlf_file_keeps_eol_and_mentions(env, capsys, monkeypatch):
    assert run(capsys, env.vault)[0] == 0
    path = env.vault / "jira" / "KB-7.md"
    crlf = path.read_bytes().replace(b"\n", b"\r\n")
    crlf = managed.replace_region(crlf, "mentions", "- [[2026-11-02]] 09:00 막힘 확인")
    path.write_bytes(crlf)
    region = managed.read_region(crlf, "mentions")
    env.jira.touch("KB-7", summary="증분 동기화 시각 계산 (수정)")
    set_now(monkeypatch, "2026-11-04T16:40:00+09:00")
    assert run(capsys, env.vault)[0] == 0
    data = path.read_bytes()
    assert managed.read_region(data, "mentions") == region == "- [[2026-11-02]] 09:00 막힘 확인\r\n".encode("utf-8")
    assert data.count(b"\n") == data.count(b"\r\n")
    assert frontmatter.parse(data)["summary"] == "증분 동기화 시각 계산 (수정)"


def test_marker_lines_in_jira_text_are_neutralized(env, capsys, monkeypatch):
    env.jira.touch("KB-5", description="h2. 주의\n<!-- kb:mentions:start -->\n가짜 구역\n<!-- kb:mentions:end -->\n끝")
    assert run(capsys, env.vault)[0] == 0
    data = (env.vault / "jira" / "KB-5.md").read_bytes()
    assert [name for name, _s, _e in managed.spans(data)] == ["mentions"]
    assert managed.read_region(data, "mentions") == b""
    assert b"\n <!-- kb:mentions:start -->\n" in data


def test_bad_markers_skip_file_and_keep_last_sync(env, capsys, monkeypatch):
    assert run(capsys, env.vault)[0] == 0
    path = env.vault / "jira" / "KB-12.md"
    broken = path.read_bytes().replace(b"<!-- kb:mentions:end -->", b"")
    path.write_bytes(broken)
    env.jira.touch("KB-12", summary="바뀐 제목")
    set_now(monkeypatch, "2026-11-04T16:40:00+09:00")
    code, _out, err = run(capsys, env.vault)
    assert code == 0
    assert "jira/KB-12.md" in err and "관리 구역" in err
    assert path.read_bytes() == broken
    assert state(env.vault)["last_sync"] == NOW  # 다음 증분이 같은 구간을 다시 받는다


# ---------------------------------------------------------------- --key, --jql


def test_key_option(env, capsys):
    code, out, err = run(capsys, env.vault, "--key", "KB-15", "--key", "ops-4", "--key", "KB-11")
    assert code == 0, err
    assert set(jira_files(env.vault)) == {"KB-15", "OPS-4", "KB-11"}
    kb11 = frontmatter.parse((env.vault / "jira" / "KB-11.md").read_bytes())
    assert kb11["assignee"] == "" and kb11["due"] == ""
    assert frontmatter.parse((env.vault / "jira" / "KB-15.md").read_bytes())["status_category"] == "done"
    assert not (env.vault / ".state" / "sync.json").exists()
    assert all("/issue/" in e["path"] for e in env.jira.log if e["path"] != "/rest/api/2/field")
    # 프로젝트 표: OPS-4는 열린 일감, KB-15는 done이라 빠짐
    ops = (env.vault / "wiki" / "projects" / "OPS.md").read_bytes()
    assert b"[[OPS-4]]" in managed.read_region(ops, "issues")
    kb = (env.vault / "wiki" / "projects" / "KB.md").read_bytes()
    assert b"[[KB-15]]" not in managed.read_region(kb, "issues")


def test_key_unknown_exits_4(env, capsys):
    code, _out, err = run(capsys, env.vault, "--key", "KB-999", "--key", "KB-12")
    assert code == 4
    assert "KB-999" in err
    assert set(jira_files(env.vault)) == {"KB-12"}
    assert run(capsys, env.vault, "--key", "not a key")[0] == 4


def test_sync_key_contract(env):
    with jira_sync.JiraClient.from_config(env.cfg) as client:
        path = jira_sync.sync_key(env.cfg, client, "KB-12")
    assert path == env.cfg.path("jira", "KB-12.md")
    assert frontmatter.parse(path.read_bytes())["key"] == "KB-12"
    assert fts.search(env.cfg, ["KB-12"])[0]["key"] == "KB-12"


def test_jql_print_only(env, capsys):
    code, out, _ = run(capsys, env.vault, "--jql", "key = KB-3")
    assert code == 0
    assert "KB-3 · Done · 홍길동 · 캐시 폴더 구조 정리" in out
    assert "1건 (저장하지 않음" in out
    assert jira_files(env.vault) == {}
    assert not fts.cache_path(env.cfg).exists()
    assert not (env.vault / ".state" / "sync.json").exists()
    assert not (env.vault / "wiki" / "projects" / "KB.md").exists()
    # 범위로 감싸지 않는다(D49)
    assert searches(env.jira)[-1] == "key = KB-3 ORDER BY key ASC"


def test_jql_cache_writes(env, capsys):
    code, out, _ = run(capsys, env.vault, "--jql", "project = OPS ORDER BY updated DESC", "--cache")
    assert code == 0
    assert set(jira_files(env.vault)) == {k for k in ALL_KEYS if k.startswith("OPS-")}
    assert searches(env.jira)[-1] == "project = OPS ORDER BY updated DESC"  # 사용자 정렬 유지
    with fts.open_cache(env.cfg) as cache:
        assert cache.get("OPS-1")["status_category"] == "done"
    assert not (env.vault / ".state" / "sync.json").exists()
    assert (env.vault / "wiki" / "projects" / "OPS.md").exists()
    assert not (env.vault / "wiki" / "projects" / "KB.md").exists()


def test_jql_400_exits_4(env, capsys):
    code, _out, err = run(capsys, env.vault, "--jql", "foo = bar")
    assert code == 4
    assert "Field 'foo' does not exist" in err


@pytest.mark.parametrize(
    "args",
    [["--cache"], ["--jql", "key = KB-3", "--key", "KB-3"], ["--full", "--key", "KB-3"], ["--jql", "  "], ["--bogus"]],
)
def test_bad_arguments_exit_4(env, capsys, args):
    assert run(capsys, env.vault, *args)[0] == 4
    assert env.jira.log == []


# ---------------------------------------------------------------- --dry-run


def test_dry_run_writes_nothing(env, capsys, monkeypatch):
    before = tree(env.vault)
    code, out, _ = run(capsys, env.vault, "--dry-run")
    assert code == 0
    assert "시험 실행" in out and "만들 예정: jira/KB-12.md" in out
    assert tree(env.vault) == before
    assert not (env.vault / ".state" / "logs").exists()
    assert not (env.vault / ".state" / "kb.lock").exists()

    assert run(capsys, env.vault)[0] == 0
    before = tree(env.vault)
    env.jira.touch("KB-5", summary="바뀜")
    set_now(monkeypatch, "2026-11-04T16:40:00+09:00")
    code, out, _ = run(capsys, env.vault, "--dry-run")
    assert code == 0 and "바뀔 예정: jira/KB-5.md" in out
    assert tree(env.vault) == before
    assert state(env.vault)["last_sync"] == NOW
    for args in (["--key", "KB-5", "--dry-run"], ["--jql", "key = KB-5", "--cache", "--dry-run"]):
        code, out, _ = run(capsys, env.vault, *args)
        assert code == 0 and "바뀔 예정" in out
        assert tree(env.vault) == before


# ---------------------------------------------------------------- 뷰


def test_views_round_trip_through_read_view(env, capsys):
    assert run(capsys, env.vault)[0] == 0
    for name, jql in env.cfg.get("jira.views").items():
        rows = notes.read_view(env.cfg, name)
        expected = [i["key"] for i in mock_jira.evaluate_jql(env.jira, jql + " ORDER BY key ASC")]
        assert [r["key"] for r in rows] == expected, name
        assert expected
        data = (env.vault / "jira" / "_views" / f"{name}.md").read_bytes()
        meta = frontmatter.parse(data)
        assert meta == {"view": name, "jql": jql, "synced_at": NOW}
        assert f"# {name}\n\n| 키 | 제목 | 상태 | 담당 | 마감 | 갱신 |\n|---|---|---|---|---|---|\n" in data.decode("utf-8")
    rows = {r["key"]: r for r in notes.read_view(env.cfg, "today")}
    assert rows["KB-12"] == {"키": "[[KB-12]]", "제목": "캐시 front matter 확정", "상태": "In Progress", "담당": "홍길동", "마감": "2026-11-06", "갱신": "2026-11-04", "key": "KB-12"}
    assert "KB-7" in {r["key"] for r in notes.read_view(env.cfg, "blocked")}
    assert "KB-3" not in rows  # Done은 today에 없다


def test_view_cell_escaping_round_trips(env, capsys):
    env.jira.touch("KB-12", summary="파이프 | 와 \\| 역슬래시")
    assert run(capsys, env.vault)[0] == 0
    rows = {r["key"]: r for r in notes.read_view(env.cfg, "today")}
    assert rows["KB-12"]["제목"] == "파이프 | 와 \\| 역슬래시"


def test_view_400_keeps_old_file_and_exits_4(env, capsys, vault):
    cfg = _configure(vault, env.jira.base_url, views={"bad": "foo = bar", "깨진뷰": "status = Nope"})
    views_dir = vault / "jira" / "_views"
    old = "---\nview: bad\n---\n# bad\n\n이전 내용\n".encode("utf-8")
    (views_dir / "bad.md").write_bytes(old)
    code, out, err = run(capsys, vault)
    assert code == 4
    assert "뷰 'bad' 건너뜀(이전 파일 유지): Field 'foo' does not exist" in err
    assert "뷰 '깨진뷰' 건너뜀" in err and "Nope" in err
    assert (views_dir / "bad.md").read_bytes() == old
    assert not (views_dir / "깨진뷰.md").exists()
    for name in ("today", "blocked", "review"):  # 다른 뷰와 이슈는 끝까지 처리
        assert (views_dir / f"{name}.md").exists()
    assert set(jira_files(vault)) == SCOPE_KEYS
    saved = state(vault)
    assert saved["last_sync"] == NOW
    assert saved["counts"]["views"]["bad"] == "error"
    log = (vault / ".state" / "logs" / "kb.log").read_text(encoding="utf-8")
    assert "Field 'foo' does not exist" in log


def test_sync_then_today_renders_view_issues(env, run_kb):
    result = run_kb("sync")
    assert result.returncode == 0, result.stderr
    result = run_kb("today")
    assert result.returncode == 0, result.stderr
    daily = (env.vault / "daily" / "2026-11-04.md").read_bytes()
    region = managed.read_region(daily, "today").decode("utf-8")
    today = [r["key"] for r in notes.read_view(env.cfg, "today")]
    blocked = [r["key"] for r in notes.read_view(env.cfg, "blocked")]
    assert today and blocked
    for key in set(today) | set(blocked):
        assert f"[[{key}]]" in region
    assert "- [ ] [[KB-12]] 캐시 front matter 확정 · In Progress · 마감 11-06" in region
    assert "[[KB-7]] 증분 동기화 시각 계산 · 막힘" in region


# ---------------------------------------------------------------- 프로젝트 페이지


def test_project_issues_table(env, capsys, monkeypatch):
    page = env.vault / "wiki" / "projects" / "KB.md"
    human = (
        "---\ntype: project\nkey: KB\ntitle: 지식 저장고\nupdated: 2026-10-01\n---\n# KB — 지식 저장고\n\n"
        "## 개요\n사람이 쓴 개요. 기계가 건드리지 않음.\n\n## 현재 일감\n<!-- kb:issues:start -->\n<!-- kb:issues:end -->\n"
        "\n## 언급\n<!-- kb:mentions:start -->\n- 사람 언급\n<!-- kb:mentions:end -->\n"
    ).encode("utf-8")
    page.write_bytes(human)
    assert run(capsys, env.vault)[0] == 0
    data = page.read_bytes()
    region = managed.read_region(data, "issues").decode("utf-8")
    rows = notes.parse_table(region)
    keys = [notes.wikilinks(r["키"])[0] for r in rows]
    # 열린 KB 일감만, 갱신 내림차순
    assert keys == ["KB-12", "KB-9", "KB-5", "KB-7", "KB-14", "KB-10"]
    assert "KB-3" not in keys
    assert rows[0]["갱신"] == "2026-11-04" and rows[0]["담당"] == "홍길동"
    # 구역 밖은 그대로, updated만 오늘로
    assert "사람이 쓴 개요. 기계가 건드리지 않음." in data.decode("utf-8")
    assert managed.read_region(data, "mentions") == "- 사람 언급\n".encode("utf-8")
    assert frontmatter.parse(data)["updated"] == "2026-11-04"

    def without_volatile(raw: bytes) -> list[bytes]:
        return [line for line in managed.replace_region(raw, "issues", "").split(b"\n") if not line.startswith(b"updated:")]

    assert without_volatile(data) == without_volatile(human)

    # OPS 페이지는 새로 만들어짐(Jira의 프로젝트 이름)
    ops = (env.vault / "wiki" / "projects" / "OPS.md").read_bytes()
    assert frontmatter.parse(ops)["title"] == "운영"
    assert b"[[OPS-1]]" not in ops

    # 바뀌지 않으면 updated도 그대로(다음 날 다시 동기화)
    set_now(monkeypatch, "2026-11-05T09:00:00+09:00")
    assert run(capsys, env.vault, "--full")[0] == 0
    assert page.read_bytes() == data

    # Done으로 바뀌면 표에서 빠진다
    env.jira.touch("KB-12", status={"id": "5", "name": "Done", "statusCategory": {"id": 3, "key": "done", "name": "Done"}})
    assert run(capsys, env.vault)[0] == 0
    data = page.read_bytes()
    assert b"[[KB-12]]" not in managed.read_region(data, "issues")
    assert frontmatter.parse(data)["updated"] == "2026-11-05"


# ---------------------------------------------------------------- 검색 캐시


def test_cache_filled_by_sync(env, capsys):
    assert run(capsys, env.vault)[0] == 0
    with fts.open_cache(env.cfg) as cache:
        assert cache.count() == len(SCOPE_KEYS)
        row = cache.get("KB-12")
        assert row["status_category"] == "indeterminate" and row["assignee"] == "홍길동"
        assert "필드는 최소로 가자" in row["body"]  # 댓글도 색인
        hits = cache.search(["KB-12"])
        assert hits[0]["key"] == "KB-12" and hits[0]["via"] == "fts"
        assert {h["key"] for h in cache.search(["캐시"])} >= {"KB-12", "KB-3"}


# ---------------------------------------------------------------- 종료 코드


def test_missing_pat_exits_2(vault, start_jira, capsys, fake_now):
    fake_now(NOW)
    server = start_jira()
    config.save_local(vault, {"environment": "home", "jira": {"base_url": server.base_url, "project_keys": ["KB"], "pat_ref": "keyring:work-kb/jira"}})
    assert run(capsys, vault)[0] == 2
    assert server.log == []


def test_bad_token_exits_2(vault, start_jira, capsys, fake_now):
    fake_now(NOW)
    server = start_jira(bad_token=TOKEN)
    _configure(vault, server.base_url)
    code, _out, err = run(capsys, vault)
    assert code == 2
    assert jira_files(vault) == {} and not (vault / ".state" / "sync.json").exists()


def test_refused_connection_exits_3(vault, capsys, fake_now):
    fake_now(NOW)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    _configure(vault, f"http://127.0.0.1:{port}", timeout=5)
    assert run(capsys, vault)[0] == 3
    assert jira_files(vault) == {}


def test_missing_project_keys_exits_4(vault, start_jira, capsys, fake_now):
    fake_now(NOW)
    server = start_jira()
    _configure(vault, server.base_url, project_keys=[])
    code, _out, err = run(capsys, vault)
    assert code == 4 and "project_keys" in err


def test_exit_codes_via_kb_py(env, run_kb):
    assert run_kb("jira_sync", "--jql", "foo = bar").returncode == 4
    result = run_kb("sync", "--key", "KB-12")
    assert result.returncode == 0, result.stderr
    assert "jira/KB-12.md" in result.stdout


def test_lock_busy_exits_6(env, run_kb):
    with core.kb_lock(env.vault, "test"):
        result = run_kb("sync", env={"KB_LOCK_TIMEOUT": "0.3"})
    assert result.returncode == 6, result.stderr
    assert jira_files(env.vault) == {}


def test_rate_limit_and_500_are_survived(vault, start_jira, capsys, fake_now, monkeypatch):
    from lib import net

    monkeypatch.setattr(net, "_sleep", lambda seconds: None)
    fake_now(NOW)
    server = start_jira(rate_limit=2, search_500_once=True, page_size_cap=4)
    _configure(vault, server.base_url)
    code, _out, err = run(capsys, vault)
    assert code == 0, err
    assert set(jira_files(vault)) == SCOPE_KEYS


# ---------------------------------------------------------------- 작은 단위


def test_demote_headings_skips_code():
    md = "# 하나\n```\n# 코드 안\n```\n#### 넷\n###### 여섯\n#해시태그"
    assert jira_sync.demote_headings(md) == "### 하나\n```\n# 코드 안\n```\n###### 넷\n###### 여섯\n#해시태그"


def test_comment_line_truncates_to_200_chars():
    tz = core.resolve_tz("Asia/Seoul")
    comment = {"author": {"displayName": "김철수"}, "created": "2026-11-01T01:02:00.000+0000", "body": "\n\n" + "가" * 300 + "\n둘째 줄"}
    line = jira_sync._comment_line(comment, tz)
    head, _, body = line.partition("김철수: ")
    assert head == "- 2026-11-01 10:02 "  # 설정 시간대로
    assert len(body) == 200 and body.endswith("…")
