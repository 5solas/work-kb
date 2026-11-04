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


def test_last_sync_is_run_start_time(env, capsys, monkeypatch):
    # 실행 중에 시간이 흘러도 last_sync는 검색 전(실행 시작) 시각이다(D50: 겹침 구간의 기준)
    start = dt.datetime.fromisoformat(NOW)
    later = start + dt.timedelta(minutes=7)

    def fake_now(tz):
        searched = any(e["path"] == "/rest/api/2/search" for e in env.jira.log)
        return (later if searched else start).astimezone(tz)

    monkeypatch.setattr(jira_sync.core, "now", fake_now)
    assert run(capsys, env.vault)[0] == 0
    saved = state(env.vault)
    assert saved["last_sync"] == saved["last_full"] == saved["last_run"] == NOW


def test_missing_cache_means_full(env, capsys, monkeypatch):
    assert run(capsys, env.vault)[0] == 0
    fts.cache_path(env.cfg).unlink()
    set_now(monkeypatch, "2026-11-04T16:30:03+09:00")
    code, out, _ = run(capsys, env.vault)
    assert code == 0 and "sync 전체" in out
    assert fts.search(env.cfg, ["캐시"])


def test_plan_scope_minutes(env):
    start = dt.datetime.fromisoformat("2026-11-04T16:30:30+09:00")
    good = {"last_sync": NOW, "last_full": NOW, "scope": jira_sync.scope_hash(env.cfg)}
    kind, jql = jira_sync.plan_scope(env.cfg, good, start, full=False)
    assert kind == "full"  # 캐시가 아직 없다
    fts.open_cache(env.cfg).close()
    kind, jql = jira_sync.plan_scope(env.cfg, good, start, full=False)
    assert kind == "incremental" and jql.endswith("updated >= '-16m'")  # 10분 27초 → 11 + 5
    kind, _ = jira_sync.plan_scope(env.cfg, good, start, full=True)
    assert kind == "full"
    kind, _ = jira_sync.plan_scope(env.cfg, {**good, "last_sync": "망가진 값"}, start, full=False)
    assert kind == "full"


def test_plan_scope_full_triggers(env, vault):
    fts.open_cache(env.cfg).close()
    start = dt.datetime.fromisoformat("2026-11-04T16:30:30+09:00")
    good = {"last_sync": NOW, "last_full": NOW, "scope": jira_sync.scope_hash(env.cfg)}

    def kind(state, cfg=env.cfg, at=start):
        return jira_sync.plan_scope(cfg, state, at, full=False)[0]

    assert kind(good) == "incremental"
    # 시계가 뒤로 감: last_sync가 지금보다 미래 → 증분 구간을 믿을 수 없다
    assert kind({**good, "last_sync": "2026-11-04T16:40:00+09:00"}) == "full"
    assert kind({**good, "last_full": "2027-01-01T00:00:00+09:00"}) == "full"
    # 범위 해시가 없거나 다름
    assert kind({k: v for k, v in good.items() if k != "scope"}) == "full"
    assert kind({**good, "scope": "0" * 64}) == "full"
    # 주기적 전체: 마지막 전체가 24시간(기본)보다 오래
    assert kind({k: v for k, v in good.items() if k != "last_full"}) == "full"
    day_later = dt.datetime.fromisoformat("2026-11-05T16:20:04+09:00")
    assert kind({**good, "last_sync": "2026-11-05T16:00:00+09:00"}, at=day_later) == "full"
    assert kind({**good, "last_sync": "2026-11-05T16:00:00+09:00"}, at=day_later - dt.timedelta(seconds=2)) == "incremental"
    # 설정으로 바꾼다
    hourly = _configure(vault, env.jira.base_url, full_every_hours=1)
    hourly_good = {**good, "scope": jira_sync.scope_hash(hourly)}
    assert kind(hourly_good, cfg=hourly) == "incremental"
    assert kind(hourly_good, cfg=hourly, at=start + dt.timedelta(hours=1)) == "full"
    # 범위 해시: 키 순서는 상관없고, jql_mine·project_keys가 바뀌면 다르다
    same = _configure(vault, env.jira.base_url, project_keys=["OPS", "KB"])
    assert jira_sync.scope_hash(same) == jira_sync.scope_hash(env.cfg)
    assert jira_sync.scope_hash(_configure(vault, env.jira.base_url, project_keys=["KB"])) != jira_sync.scope_hash(env.cfg)
    assert jira_sync.scope_hash(_configure(vault, env.jira.base_url, jql_mine="assignee = currentUser()")) != jira_sync.scope_hash(env.cfg)
    for bad in ("매일", 0, -1, True):
        with pytest.raises(core.ConfigError):
            jira_sync.plan_scope(_configure(vault, env.jira.base_url, full_every_hours=bad), good, start, full=False)


def test_future_last_sync_means_full(env, capsys, monkeypatch):
    assert run(capsys, env.vault)[0] == 0
    saved = state(env.vault)
    saved["last_sync"] = "2027-06-01T00:00:00+09:00"  # 다른 기계의 빠른 시계·시계를 뒤로 돌림
    (env.vault / ".state" / "sync.json").write_text(json.dumps(saved), encoding="utf-8")
    env.jira.log.clear()
    set_now(monkeypatch, "2026-11-04T16:30:03+09:00")
    code, out, _ = run(capsys, env.vault)
    assert code == 0 and "sync 전체" in out
    assert searches(env.jira)[0] == jira_sync.scope_jql(env.cfg) + " ORDER BY key ASC"
    assert state(env.vault)["last_sync"] == "2026-11-04T16:30:03+09:00"


def test_periodic_full_after_a_day(env, capsys, monkeypatch):
    assert run(capsys, env.vault)[0] == 0
    set_now(monkeypatch, "2026-11-05T09:00:00+09:00")
    code, out, _ = run(capsys, env.vault)
    assert code == 0 and "sync 증분" in out  # 17시간 뒤: 아직 증분
    set_now(monkeypatch, "2026-11-05T16:30:00+09:00")  # 첫 전체 뒤 24시간 넘음(증분은 7.5시간 전)
    env.jira.log.clear()
    code, out, _ = run(capsys, env.vault)
    assert code == 0 and "sync 전체" in out
    assert searches(env.jira)[0] == jira_sync.scope_jql(env.cfg) + " ORDER BY key ASC"
    assert state(env.vault)["last_full"] == "2026-11-05T16:30:00+09:00"


def test_added_project_is_backfilled_on_next_plain_sync(vault, start_jira, capsys, fake_now, monkeypatch):
    fake_now(NOW)
    server = start_jira()
    _configure(vault, server.base_url, project_keys=["KB"])
    assert run(capsys, vault)[0] == 0
    assert {k for k in jira_files(vault) if k.startswith("OPS-")} == set()
    _configure(vault, server.base_url, project_keys=["KB", "OPS"])  # init으로 프로젝트를 더함
    set_now(monkeypatch, "2026-11-04T16:35:03+09:00")
    code, out, err = run(capsys, vault)
    assert code == 0, err
    assert "sync 전체" in out
    assert set(jira_files(vault)) == SCOPE_KEYS  # 30일 안의 OPS 일감이 모두 왔다
    assert state(vault)["scope"] == jira_sync.scope_hash(config.load(vault))
    set_now(monkeypatch, "2026-11-04T16:50:03+09:00")
    assert "sync 증분" in run(capsys, vault)[1]  # 범위가 그대로면 다시 증분


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


def test_bom_file_keeps_bom_and_mentions(env, capsys, monkeypatch):
    assert run(capsys, env.vault)[0] == 0
    path = env.vault / "jira" / "KB-7.md"
    data = managed.replace_region(path.read_bytes(), "mentions", "- [[2026-11-02]] 09:00 막힘 확인")
    path.write_bytes(b"\xef\xbb\xbf" + data)  # 옛 메모장은 UTF-8에 BOM을 붙인다
    region = managed.read_region(path.read_bytes(), "mentions")
    env.jira.touch("KB-7", summary="증분 동기화 시각 계산 (BOM)")
    set_now(monkeypatch, "2026-11-04T16:40:00+09:00")
    assert run(capsys, env.vault)[0] == 0
    data = path.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf---\n") and not data.startswith(b"\xef\xbb\xbf\xef\xbb\xbf")
    assert managed.read_region(data, "mentions") == region
    assert frontmatter.parse(data)["summary"] == "증분 동기화 시각 계산 (BOM)"
    assert frontmatter.split(data)[1].startswith("# KB-7 증분 동기화 시각 계산 (BOM)\n".encode("utf-8"))  # front matter 블록은 하나
    # 바뀐 게 없으면 BOM 파일도 다시 쓰지 않는다
    before = path.read_bytes()
    set_now(monkeypatch, "2026-11-04T16:50:00+09:00")
    assert run(capsys, env.vault, "--full")[0] == 0
    assert path.read_bytes() == before


def test_line_break_characters_in_front_matter_values(env, capsys):
    status = {"id": "3", "name": "검토\x85대기", "statusCategory": {"id": 4, "key": "indeterminate", "name": "In Progress"}}
    assignee = {"name": "honggildong", "key": "honggildong", "displayName": "홍 길동"}
    env.jira.touch("KB-12", status=status, assignee=assignee, priority={"id": "3", "name": "Me\rdium"}, labels=["a b", "c"])
    assert run(capsys, env.vault)[0] == 0
    meta = frontmatter.parse((env.vault / "jira" / "KB-12.md").read_bytes())
    assert meta["status"] == "검토 대기"
    assert meta["assignee"] == "홍 길동"
    assert meta["priority"] == "Me dium"
    assert meta["labels"] == ["a b", "c"]
    text = (env.vault / "jira" / "KB-12.md").read_bytes().decode("utf-8")
    head = text.split("\n---\n", 1)[0]
    assert not any(ch in head for ch in "\x85  \r")


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
    # 쓰지 못한 일감은 캐시도 앞서 나가지 않는다(페이지와 같은 옛 값)
    with fts.open_cache(env.cfg) as cache:
        assert cache.get("KB-12")["summary"] == "캐시 front matter 확정"
        assert cache.get("KB-5")["summary"] == frontmatter.parse((env.vault / "jira" / "KB-5.md").read_bytes())["summary"]


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


def test_views_are_not_wrapped_in_scope(env, capsys):
    # 막힌 지 30일 넘은 내 일감: 범위(jql_mine의 -30d) 밖이지만 blocked 뷰에는 나와야 한다(D49)
    env.jira.touch("KB-7", updated="2026-09-01T10:00:00.000+0900")
    assert run(capsys, env.vault)[0] == 0
    sent = searches(env.jira)
    for name, jql in env.cfg.get("jira.views").items():
        assert jql + " ORDER BY key ASC" in sent, name  # 뷰 JQL 그대로(감싸지 않음)
    assert not any(q.startswith(jira_sync.scope_jql(env.cfg)) and "status = Blocked" in q for q in sent)
    assert "KB-7" not in jira_files(env.vault)  # 범위 밖이라 이슈 파일은 없다
    assert [r["key"] for r in notes.read_view(env.cfg, "blocked")] == ["KB-7"]


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
    stub = notes.ensure_page(env.cfg, "jira", "KB-99").read_bytes()  # 동기화되지 않은 stub(o 명령이 만든 것)
    assert run(capsys, env.vault)[0] == 0
    # stub은 동기화된 일감이 아니라 범위 밖 정리 대상도 아니다(표시도, 다시 묻기도 없음)
    assert (env.vault / "jira" / "KB-99.md").read_bytes() == stub
    assert not any(q.startswith("key in") for q in searches(env.jira))
    data = page.read_bytes()
    region = managed.read_region(data, "issues").decode("utf-8")
    rows = notes.parse_table(region)
    keys = [notes.wikilinks(r["키"])[0] for r in rows]
    # 열린 KB 일감만, 갱신 내림차순. stub은 빠진다
    assert keys == ["KB-12", "KB-9", "KB-5", "KB-7", "KB-14", "KB-10"]
    assert "KB-3" not in keys and "[[KB-99]]" not in region
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


# ---------------------------------------------------------------- 범위 밖으로 나간 일감

KIMCS = {"name": "kimcs", "key": "kimcs", "displayName": "김철수"}
HONG = {"name": "honggildong", "key": "honggildong", "displayName": "홍길동"}
NOT_WATCHING = {"watchCount": 1, "isWatching": False}


def table_keys(vault: Path, project: str) -> list[str]:
    region = managed.read_region((vault / "wiki" / "projects" / f"{project}.md").read_bytes(), "issues").decode("utf-8")
    return [notes.wikilinks(r["키"])[0] for r in notes.parse_table(region)]


def cached(cfg, key: str) -> dict | None:
    with fts.open_cache(cfg) as cache:
        return cache.get(key)


def test_reassigned_issue_leaves_project_table_after_full(env, capsys, monkeypatch):
    assert run(capsys, env.vault)[0] == 0
    assert "KB-12" in table_keys(env.vault, "KB")
    # 다른 사람에게 넘어가고 지켜보기도 끔 → 범위(jql_mine) 밖. 상태는 열린 그대로
    env.jira.touch("KB-12", assignee=KIMCS, watches=NOT_WATCHING, summary="넘겨준 일감")
    set_now(monkeypatch, "2026-11-04T16:30:03+09:00")
    code, out, _ = run(capsys, env.vault)
    assert code == 0 and "sync 증분" in out
    assert "KB-12" in table_keys(env.vault, "KB")  # 증분은 범위 밖을 모른다

    set_now(monkeypatch, "2026-11-04T16:40:03+09:00")
    env.jira.log.clear()
    before = jira_files(env.vault)
    code, out, err = run(capsys, env.vault, "--full")
    assert code == 0, err
    assert "범위 밖 정리 1" in out
    # 범위로 감싸지 않은 key in (…)로 다시 받았다
    assert "key in (KB-12) ORDER BY key ASC" in searches(env.jira)
    meta = frontmatter.parse((env.vault / "jira" / "KB-12.md").read_bytes())
    assert meta["in_scope"] == "false" and meta["assignee"] == "김철수" and meta["summary"] == "넘겨준 일감"
    assert "moved_to" not in meta
    assert "KB-12" not in table_keys(env.vault, "KB")
    assert cached(env.cfg, "KB-12") is None
    assert not any(h["key"] == "KB-12" for h in fts.search(env.cfg, ["넘겨준"]))
    after = jira_files(env.vault)
    assert {k for k in after if after[k] != before[k]} == {"KB-12"}
    assert state(env.vault)["counts"]["out_of_scope"] == 1

    # 이미 표시한 일감은 다음 전체 동기화에서 다시 묻지 않는다
    set_now(monkeypatch, "2026-11-04T16:50:03+09:00")
    env.jira.log.clear()
    assert run(capsys, env.vault, "--full")[0] == 0
    assert not any(q.startswith("key in") for q in searches(env.jira))
    assert (env.vault / "jira" / "KB-12.md").read_bytes() == after["KB-12"]

    # 다시 내 일감이 되면 표시가 사라지고 표·캐시로 돌아온다
    env.jira.touch("KB-12", assignee=HONG)
    set_now(monkeypatch, "2026-11-04T17:00:03+09:00")
    assert run(capsys, env.vault)[0] == 0
    assert "in_scope" not in frontmatter.parse((env.vault / "jira" / "KB-12.md").read_bytes())
    assert "KB-12" in table_keys(env.vault, "KB") and cached(env.cfg, "KB-12") is not None


def test_deleted_issue_is_marked_via_issue_404(env, capsys, monkeypatch):
    assert run(capsys, env.vault)[0] == 0
    env.jira.remove("KB-5")  # Jira에서 지워짐 → key in (KB-12, KB-5)는 400
    env.jira.touch("KB-12", assignee=KIMCS, watches=NOT_WATCHING)
    old_kb5 = (env.vault / "jira" / "KB-5.md").read_bytes()
    set_now(monkeypatch, "2026-11-04T16:40:03+09:00")
    env.jira.log.clear()
    code, out, err = run(capsys, env.vault, "--full")
    assert code == 0, err
    issue_calls = [e["path"] for e in env.jira.log if e["path"].startswith("/rest/api/2/issue/")]
    assert "/rest/api/2/issue/KB-5" in issue_calls and "/rest/api/2/issue/KB-12" in issue_calls
    kb5 = (env.vault / "jira" / "KB-5.md").read_bytes()
    meta = frontmatter.parse(kb5)
    assert meta["in_scope"] == "false" and "moved_to" not in meta
    assert meta["summary"] == frontmatter.parse(old_kb5)["summary"]  # 내용은 그대로, 표시만
    assert frontmatter.split(kb5)[1] == frontmatter.split(old_kb5)[1]
    assert frontmatter.parse((env.vault / "jira" / "KB-12.md").read_bytes())["in_scope"] == "false"
    assert {"KB-5", "KB-12"}.isdisjoint(table_keys(env.vault, "KB"))
    assert cached(env.cfg, "KB-5") is None and cached(env.cfg, "KB-12") is None


def test_moved_issue_marks_old_key_and_writes_new(env, capsys, monkeypatch):
    assert run(capsys, env.vault)[0] == 0
    env.jira.move("KB-12", "OPS-40")  # 범위 안 그대로(내 일감) → 전체 검색이 OPS-40을 준다
    # KB-14는 남의 일감이 된 뒤 OPS-41로 옮겨짐 → 새 키도 범위 밖
    env.jira.touch("KB-14", assignee=KIMCS, reporter=KIMCS, watches=NOT_WATCHING)
    env.jira.move("KB-14", "OPS-41")
    set_now(monkeypatch, "2026-11-04T16:40:03+09:00")
    # 시험 실행도 표시할 옛 키를 표에서 빼고 보고한다(아무것도 쓰지 않음)
    before = tree(env.vault)
    code, out, err = run(capsys, env.vault, "--full", "--dry-run")
    assert code == 0, err
    for rel in ("jira/KB-12.md", "jira/KB-14.md", "wiki/projects/KB.md"):
        assert f"바뀔 예정: {rel}" in out
    assert "만들 예정: jira/OPS-41.md" in out
    assert tree(env.vault) == before
    code, out, err = run(capsys, env.vault, "--full")
    assert code == 0, err
    old = frontmatter.parse((env.vault / "jira" / "KB-12.md").read_bytes())
    assert old["in_scope"] == "false" and old["moved_to"] == "OPS-40"
    new = frontmatter.parse((env.vault / "jira" / "OPS-40.md").read_bytes())
    assert new["key"] == "OPS-40" and new["project"] == "OPS" and "in_scope" not in new
    assert frontmatter.parse((env.vault / "jira" / "KB-14.md").read_bytes())["moved_to"] == "OPS-41"
    ops41 = frontmatter.parse((env.vault / "jira" / "OPS-41.md").read_bytes())
    assert ops41["in_scope"] == "false" and ops41["assignee"] == "김철수"
    assert {"KB-12", "KB-14"}.isdisjoint(table_keys(env.vault, "KB"))
    assert "OPS-40" in table_keys(env.vault, "OPS") and "OPS-41" not in table_keys(env.vault, "OPS")
    assert cached(env.cfg, "KB-12") is None and cached(env.cfg, "OPS-40") is not None and cached(env.cfg, "OPS-41") is None


def test_key_option_on_moved_issue(env, capsys):
    notes.ensure_page(env.cfg, "jira", "KB-12")  # o 명령이 만든 stub
    env.jira.move("KB-12", "OPS-40")
    code, out, err = run(capsys, env.vault, "--key", "KB-12")
    assert code == 0, err
    assert "jira/OPS-40.md: 동기화함" in out and "OPS-40로 옮겨진" in out
    assert frontmatter.parse((env.vault / "jira" / "OPS-40.md").read_bytes())["key"] == "OPS-40"
    old = frontmatter.parse((env.vault / "jira" / "KB-12.md").read_bytes())
    assert old["moved_to"] == "OPS-40" and old["in_scope"] == "false" and old["stub"] == "true"
    assert "OPS-40" in table_keys(env.vault, "OPS")
    with jira_sync.JiraClient.from_config(env.cfg) as client:  # 계약: 새 키 파일 경로
        assert jira_sync.sync_key(env.cfg, client, "KB-12") == env.cfg.path("jira", "OPS-40.md")


def test_locked_cache_exits_6(env, capsys, monkeypatch):
    import sqlite3

    assert run(capsys, env.vault)[0] == 0
    monkeypatch.setattr(fts, "BUSY_TIMEOUT", 0.2)
    other = sqlite3.connect(str(fts.cache_path(env.cfg)), timeout=0, isolation_level=None)
    try:
        other.execute("BEGIN EXCLUSIVE")
        set_now(monkeypatch, "2026-11-04T16:40:03+09:00")
        code, _out, err = run(capsys, env.vault, "--full")
    finally:
        other.close()
    assert code == 6
    assert "캐시를 다른 프로그램이 쓰고 있습니다" in err and "손상" not in err
    assert state(env.vault)["last_sync"] == NOW  # 다음 실행이 다시 받는다


def test_repeated_issue_across_pages_written_once(vault, start_jira, capsys, fake_now):
    fake_now(NOW)
    server = start_jira(repeat_page_boundary=True, page_size_cap=4)
    _configure(vault, server.base_url)
    code, out, err = run(capsys, vault)
    assert code == 0, err
    n = len(SCOPE_KEYS)
    # 이슈 n + 뷰 3이 새 파일, 프로젝트 페이지 2는 만든 뒤 표를 채움. 두 번 쓰인(그대로) 파일 없음
    assert f"받은 일감 {n}건 · 새 파일 {n + 3} · 바뀜 2 · 그대로 0" in out
    assert set(jira_files(vault)) == SCOPE_KEYS
    assert state(vault)["counts"]["issues"] == n


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
