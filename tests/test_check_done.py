"""`kb.py today --check-done`: `[x]` 일감의 완료 전환 제안(읽기 전용, Jira 호출 없음, 자동 전환 없음)."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

import daily_prepare
from lib import config, frontmatter

D05 = dt.date(2026, 11, 5)
CMD = 'python scripts/kb.py act transition'

DAILY = """---
date: 2026-11-05
---
## 오늘
<!-- kb:today:start -->
- [x] [[KB-12]] 캐시 front matter 확정 · In Progress · 마감 11-06
- [ ] [[KB-13]] 열린 일감 · To Do
- [x] [[KB-1]] 끝난 일감 · Done
- [X] [[OPS-3]] 배포 체크리스트 · 막힘
- [x] [[KB-99]] 캐시 없는 일감 · To Do
- [x] [[KB-12]] 같은 키 둘째 줄
<!-- kb:today:end -->
- [x] [[OPS-4]] 사람이 제목 아래에 쓴 줄

## 이월
<!-- kb:carryover:start -->
- [x] [[KB-50]] 이월 구역은 보지 않는다
<!-- kb:carryover:end -->

## 기록
- [x] [[KB-51]] 기록 절은 보지 않는다
```
- [x] [[KB-52]] 코드 블록
```
"""


def _issue(vault: Path, key: str, **fields) -> Path:
    meta = {"key": key, "summary": f"{key} 요약", "status": "In Progress", "status_category": "indeterminate"}
    meta.update(fields)
    meta = {k: v for k, v in meta.items() if v is not None}
    path = vault / "jira" / f"{key}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(frontmatter.render(meta) + f"# {key}\n".encode("utf-8"))
    return path


def _daily(vault: Path, text: str = DAILY, day: str = "2026-11-05") -> Path:
    path = vault / "daily" / f"{day}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def _snapshot(vault: Path) -> dict[str, bytes]:
    return {p.relative_to(vault).as_posix(): p.read_bytes() for p in sorted(vault.rglob("*")) if p.is_file()}


@pytest.fixture
def cfg(vault):
    config.save_local(vault, {"environment": "home", "jira": {"project_keys": ["KB", "OPS"]}})
    return config.load(vault)


@pytest.fixture
def issues(vault):
    _issue(vault, "KB-12", summary="캐시 front matter 확정", status="In Progress")
    _issue(vault, "KB-13", summary="열린 일감", status="To Do", status_category="new")
    _issue(vault, "KB-1", summary="끝난 일감", status="Done", status_category="done")
    _issue(vault, "OPS-3", summary="배포 체크리스트", status="Blocked")
    _issue(vault, "OPS-4", summary="사람 줄 일감", status="검토 중")
    _issue(vault, "KB-50", status="To Do", status_category="new")
    _issue(vault, "KB-51", status="To Do", status_category="new")
    _issue(vault, "KB-52", status="To Do", status_category="new")


def _by_key(result) -> dict[str, daily_prepare.DoneItem]:
    return {item.key: item for item in result.items}


def test_proposals_only_for_checked_not_done(cfg, vault, issues):
    _daily(vault)
    result = daily_prepare.check_done(cfg, D05)
    items = _by_key(result)
    assert list(items) == ["KB-12", "OPS-3", "KB-99", "OPS-4"]  # 줄 순서, 키별 한 번
    assert result.done == 1  # KB-1
    kb12 = items["KB-12"]
    assert kb12.kind == "proposal" and kb12.line == 6
    assert kb12.title == "캐시 front matter 확정" and kb12.status == "In Progress"
    assert kb12.transition == "Done"
    assert kb12.command == f'{CMD} KB-12 "Done"'
    assert "act transitions KB-12" in kb12.hint  # 캐시에 전환 목록이 없으므로 후보 확인 안내
    assert items["OPS-3"].command == f'{CMD} OPS-3 "Done"'  # [X] 대문자도
    assert items["OPS-4"].status == "검토 중"  # 구역 밖이지만 `## 오늘` 아래 사람 줄
    for key in ("KB-13", "KB-1", "KB-50", "KB-51", "KB-52"):
        assert key not in items


def test_missing_cache_hints_sync(cfg, vault, issues):
    _daily(vault)
    items = _by_key(daily_prepare.check_done(cfg, D05))
    kb99 = items["KB-99"]
    assert kb99.kind == "no_cache" and not kb99.command and not kb99.transition
    assert kb99.title == "캐시 없는 일감"  # 줄에서 제목
    assert "python scripts/kb.py sync --key KB-99" in kb99.hint
    # 링크가 만든 스텁도 캐시 없음으로 본다
    _issue(vault, "KB-99", status=None, status_category=None, summary=None, stub=True)
    kb99 = _by_key(daily_prepare.check_done(cfg, D05))["KB-99"]
    assert kb99.kind == "no_cache" and "sync --key KB-99" in kb99.hint
    # 깨진 front matter도 마찬가지
    (vault / "jira" / "KB-99.md").write_bytes(b"---\nkey: [\n---\n")
    assert _by_key(daily_prepare.check_done(cfg, D05))["KB-99"].kind == "no_cache"


def test_text_output_format(cfg, vault, issues, capsys):
    _daily(vault)
    assert daily_prepare.today_main(["--root", str(vault), "--date", "2026-11-05", "--check-done"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f'KB-12 캐시 front matter 확정 · 현재 In Progress → 완료 전환 제안: {CMD} KB-12 "Done"'
    assert out[1].startswith("  (") and "python scripts/kb.py act transitions KB-12" in out[1]
    assert f'OPS-3 배포 체크리스트 · 현재 Blocked → 완료 전환 제안: {CMD} OPS-3 "Done"' in out
    assert "KB-99 캐시 없는 일감 · 캐시 없음 → 먼저 동기화: python scripts/kb.py sync --key KB-99" in out
    assert out[-1].startswith("전환 제안 3건, 확인 필요 1건, 이미 완료 1건")
    assert not any("KB-13" in line or "KB-1 " in line for line in out)


def test_never_writes_or_calls_jira(cfg, vault, issues, monkeypatch, capsys):
    _daily(vault)
    before = _snapshot(vault)

    def boom(*_a, **_k):
        raise AssertionError("쓰기·잠금·네트워크 금지")

    from lib import core

    monkeypatch.setattr(core, "atomic_write", boom)
    monkeypatch.setattr(core, "kb_lock", boom)
    monkeypatch.setattr(core, "save_state", boom)
    import requests

    monkeypatch.setattr(requests.Session, "request", boom)
    assert daily_prepare.today_main(["--root", str(vault), "--date", "2026-11-05", "--check-done"]) == 0
    assert daily_prepare.today_main(["--root", str(vault), "--date", "2026-11-05", "--check-done", "--json"]) == 0
    capsys.readouterr()
    assert _snapshot(vault) == before


def test_missing_daily_is_not_created(cfg, vault, capsys):
    assert daily_prepare.today_main(["--root", str(vault), "--date", "2026-11-05", "--check-done"]) == 0
    assert "daily/2026-11-05.md" in capsys.readouterr().out
    assert not (vault / "daily" / "2026-11-05.md").exists()
    assert daily_prepare.today_main(["--root", str(vault), "--date", "2026-11-05", "--check-done", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert not (vault / "daily" / "2026-11-05.md").exists()


def test_nothing_to_propose(cfg, vault, issues, capsys):
    _daily(vault, "## 오늘\n<!-- kb:today:start -->\n- [x] [[KB-1]] 끝 · Done\n- [ ] [[KB-12]] 열림\n<!-- kb:today:end -->\n")
    assert daily_prepare.today_main(["--root", str(vault), "--date", "2026-11-05", "--check-done"]) == 0
    assert capsys.readouterr().out.strip() == "완료 전환 제안 없음 (daily/2026-11-05.md, 이미 완료 1건)"


def test_json_list(cfg, vault, issues, capsys):
    _daily(vault)
    assert daily_prepare.today_main(["--root", str(vault), "--date", "2026-11-05", "--check-done", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list) and [row["key"] for row in data] == ["KB-12", "OPS-3", "KB-99", "OPS-4"]
    first = data[0]
    assert first == {
        "key": "KB-12",
        "line": 6,
        "title": "캐시 front matter 확정",
        "status": "In Progress",
        "status_category": "indeterminate",
        "kind": "proposal",
        "transition": "Done",
        "command": f'{CMD} KB-12 "Done"',
        "hint": first["hint"],
        "moved_from": "",
    }
    assert data[2]["kind"] == "no_cache" and data[2]["command"] == ""


def test_json_requires_check_done(cfg, vault):
    from lib import core

    with pytest.raises(core.ConfigError):
        daily_prepare.today_main(["--root", str(vault), "--json"])


def test_config_transition_names(vault, issues):
    _daily(vault)
    config.save_local(vault, {"environment": "home", "jira": {"done_transition_names": ["완료", "Done"]}})
    items = _by_key(daily_prepare.check_done(config.load(vault), D05))
    assert items["KB-12"].transition == "완료" and items["KB-12"].command == f'{CMD} KB-12 "완료"'

    config.save_local(vault, {"environment": "home", "jira": {"done_transition_names": "Resolve Issue, Done"}})
    assert daily_prepare.done_transition_names(config.load(vault)) == ["Resolve Issue", "Done"]

    config.save_local(vault, {"environment": "home", "jira": {"done_transition_names": []}})
    assert daily_prepare.done_transition_names(config.load(vault)) == ["Done", "완료"]

    config.save_local(vault, {"environment": "home", "jira": {"done_transition_names": [True]}})
    from lib import core

    with pytest.raises(core.ConfigError):
        daily_prepare.done_transition_names(config.load(vault))


def test_default_names_without_config(vault):
    """설정 어디에도 done_transition_names가 없으면 기본값. 볼트로 복사된 저장소 config.yaml에서 그 키를 뺀다."""
    shared = vault / "config.yaml"
    if shared.exists():
        text = shared.read_text(encoding="utf-8")
        kept = "".join(line for line in text.splitlines(keepends=True) if "done_transition_names" not in line)
        shared.write_bytes(kept.encode("utf-8"))
    config.save_local(vault, {"environment": "home"})
    cfg = config.load(vault)
    assert cfg.get("jira.done_transition_names") is None
    assert daily_prepare.done_transition_names(cfg) == ["Done", "완료"]


def test_cached_transitions_pick_actual_name(cfg, vault, issues):
    """캐시에 전환 목록이 있으면 그 안에서 설정 이름을 찾는다(대소문자 무시, 실제 이름 사용)."""
    _daily(vault)
    _issue(vault, "KB-12", summary="캐시 front matter 확정", transitions=["In Review", "DONE"])
    _issue(vault, "OPS-3", summary="배포 체크리스트", status="Blocked", transitions=["Unblock"])
    items = _by_key(daily_prepare.check_done(cfg, D05))
    assert items["KB-12"].command == f'{CMD} KB-12 "DONE"' and items["KB-12"].hint == ""
    ops3 = items["OPS-3"]
    assert ops3.kind == "proposal" and ops3.command == "" and ops3.transition == ""
    assert "act transitions OPS-3" in ops3.hint


def test_moved_issue_follows_new_key(cfg, vault, issues):
    _daily(vault, "## 오늘\n<!-- kb:today:start -->\n- [x] [[KB-12]] 옛 키\n- [x] [[KB-13]] 옮겨졌는데 새 캐시 없음\n- [x] [[KB-14]] 사라짐\n<!-- kb:today:end -->\n")
    _issue(vault, "KB-12", in_scope=False, moved_to="OPS-40")
    _issue(vault, "OPS-40", summary="옮겨진 일감", status="In Progress")
    _issue(vault, "KB-13", in_scope=False, moved_to="OPS-41")
    _issue(vault, "KB-14", in_scope=False)
    items = _by_key(daily_prepare.check_done(cfg, D05))
    assert items["OPS-40"].moved_from == "KB-12" and items["OPS-40"].command == f'{CMD} OPS-40 "Done"'
    assert items["OPS-41"].kind == "no_cache" and "sync --key OPS-41" in items["OPS-41"].hint
    assert items["KB-14"].kind == "out_of_scope" and not items["KB-14"].command
    lines = daily_prepare.format_done_item(items["OPS-40"])
    assert lines[0].startswith("OPS-40 옮겨진 일감 (← KB-12) · 현재 In Progress → 완료 전환 제안:")


def test_moved_cycle_terminates(cfg, vault):
    _daily(vault, "## 오늘\n<!-- kb:today:start -->\n- [x] [[KB-12]] 고리\n<!-- kb:today:end -->\n")
    _issue(vault, "KB-12", in_scope=False, moved_to="KB-13")
    _issue(vault, "KB-13", in_scope=False, moved_to="KB-12")
    result = daily_prepare.check_done(cfg, D05)
    assert len(result.items) == 1 and result.items[0].kind == "out_of_scope"


def test_crlf_and_bom_daily(cfg, vault, issues):
    _daily(vault)
    path = vault / "daily" / "2026-11-05.md"
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes().replace(b"\n", b"\r\n"))
    items = _by_key(daily_prepare.check_done(cfg, D05))
    assert list(items) == ["KB-12", "OPS-3", "KB-99", "OPS-4"]


def test_cli_agent_allowed_and_json(cfg, vault, issues, run_kb):
    _daily(vault)
    before = _snapshot(vault)
    proc = run_kb("today", "--check-done", "--date", "2026-11-05", env={"KB_AGENT": "1"})
    assert proc.returncode == 0, proc.stderr
    assert f'{CMD} KB-12 "Done"' in proc.stdout
    proc = run_kb("today", "--check-done", "--date", "2026-11-05", "--json", env={"KB_AGENT": "1"})
    assert proc.returncode == 0, proc.stderr
    assert [row["key"] for row in json.loads(proc.stdout)] == ["KB-12", "OPS-3", "KB-99", "OPS-4"]
    after = _snapshot(vault)
    after.pop(".state/kb.log", None)  # 로그만 허용
    before.pop(".state/kb.log", None)
    assert after == before


def test_cli_today_uses_fake_now(cfg, vault, issues, run_kb, fake_now):
    _daily(vault)
    fake_now("2026-11-05T09:00:00")
    proc = run_kb("today", "--check-done")
    assert proc.returncode == 0, proc.stderr
    assert f'{CMD} OPS-3 "Done"' in proc.stdout
    assert not (vault / "daily" / "2026-11-06.md").exists()
