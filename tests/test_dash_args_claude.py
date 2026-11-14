"""Neovim(jira.lua·search.lua)이 만드는 인수 꼴을 Python 쪽이 그대로 받는지 본다 (P-04, C-02).

자유 글 값은 `--옵션=값` 한 인수로, 자유 글 위치 인수 앞에는 `--`를 넣는다. 그러면 `-`로 시작하는 글
(`-h…`, `->완료`, `--리뷰`)도 argparse가 옵션으로 읽지 않는다. 여기의 인수 목록은
`tests/nvim/spec/jira_spec.lua`의 "passes every free-text option value with ="와
`search_spec.lua`의 "puts -- before the query"가 단언하는 꼴과 같다.
"""
from __future__ import annotations

import jira_act
import pytest
from lib import core

DASHED = ["-h", "-hong", "->완료", "--리뷰", "-긴급 요약", "-"]


def _act(*argv: str):
    return jira_act.build_parser().parse_args(list(argv))


@pytest.mark.parametrize("text", DASHED)
def test_neovim_arg_forms_accept_dash_values(text, vault, run_kb):
    root = str(vault)
    # jira.lua M.comment: act comment KEY --root V --body=BODY
    ns = _act("comment", "KB-12", "--root", root, f"--body={text}")
    assert (ns.sub, ns.key, ns.body) == ("comment", "KB-12", text)
    # M.assign: act assign --root V -- KEY USER
    ns = _act("assign", "--root", root, "--", "KB-12", text)
    assert (ns.sub, ns.key, ns.username) == ("assign", "KB-12", text)
    # M.worklog: act worklog KEY 1h --root V --comment=TEXT
    ns = _act("worklog", "KB-12", "1h", "--root", root, f"--comment={text}")
    assert (ns.key, ns.duration, ns.comment) == ("KB-12", "1h", text)
    # M.create: act create PROJECT --root V --summary=S --type=T --description=D --yes
    ns = _act("create", "KB", "--root", root, f"--summary={text}", f"--type={text}", f"--description={text}", "--yes")
    assert (ns.project, ns.summary, ns.type, ns.description, ns.yes) == ("KB", text, text, text, True)

    # search.lua kb_search: search --root V -- QUERY (실제 kb.py, 임시 볼트)
    (vault / "daily" / "2026-11-05.md").write_text(f"## 기록\n- 09:12 {text} 표시 줄\n", encoding="utf-8")
    p = run_kb("search", "--root", root, "--", f"{text} 표시")
    assert p.returncode == 0, p.stderr
    assert "usage:" not in p.stdout
    assert "daily/2026-11-05.md:2:" in p.stdout


@pytest.mark.parametrize("text", DASHED)
def test_sync_jql_equals_form_accepts_dash_values(text, vault, monkeypatch):
    """R-agy-05: jira.lua M.query가 만드는 `sync --root V --jql=JQL`(jira_spec.lua "passes every free-text option
    value with =")을 jira_sync의 argparse가 그대로 받는지 본다. 네트워크 없이: 파싱 뒤의 설정·Jira·실행을 바꿔 끼운다."""
    import contextlib

    import jira_sync

    seen: list[str] = []
    monkeypatch.setattr(jira_sync.config, "load", lambda root: object())
    monkeypatch.setattr(jira_sync.JiraClient, "from_config", classmethod(lambda cls, cfg: contextlib.nullcontext(None)))

    def fake_run_jql(cfg, client, jql, **kwargs):
        seen.append(jql)
        return 0

    monkeypatch.setattr(jira_sync, "run_jql", fake_run_jql)
    assert jira_sync.main(["--root", str(vault), f"--jql={text}"]) == 0
    assert seen == [text]
    # 값이 따로 오는 옛 꼴은 argparse가 거부하거나 도움말로 샌다
    with pytest.raises((core.ConfigError, SystemExit)):
        jira_sync.main(["--root", str(vault), "--jql", "-hong"])


def test_old_separate_value_forms_are_what_broke(vault, run_kb):
    """수정 전 꼴(값이 따로 오는 인수, `--` 없는 위치 인수)은 argparse가 거부하거나 도움말로 샌다."""
    root = str(vault)
    with pytest.raises((core.ConfigError, SystemExit)):
        _act("comment", "KB-12", "--root", root, "--body", "-foo")
    with pytest.raises((core.ConfigError, SystemExit)):
        _act("assign", "KB-12", "--root", root, "-hong")
    p = run_kb("search", "--root", root, "-hello")
    assert p.returncode != 0 or "usage:" in p.stdout
