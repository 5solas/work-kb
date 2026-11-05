"""`kb.py push` (scripts/confluence_push.py), lib/confluence_client.py, tests/mock_confluence.py (W5.2, D55·D56)."""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest
import requests

import confluence_push
import mock_confluence as mock_mod
from lib import config, core
from lib import secrets as kb_secrets
from lib.confluence_client import ConfluenceClient, NotFound, PersonalSpace, RedirectRefused

TOKEN = "mock-token"
SPACE = "~honggildong"
JIRA = "https://jira.example.invalid"

PROJECT = """---
type: project
key: KB
title: 지식 저장고
updated: 2026-11-05
---
# KB — 지식 저장고

## 개요
캐시 설계를 정리한다. 관련 [[OPS-3]] · [[topics/캐시]] · [[2026-11-04]] · [[people/김철수|김철수]]

## 현재 일감
<!-- kb:issues:start -->
| 키 | 제목 |
|---|---|
| [[KB-12]] | 캐시 front matter 확정 |
<!-- kb:issues:end -->

## 최근 요약
<!-- kb:summary:start -->
요약 문장입니다. <!-- 내부 메모 -->
<!-- kb:summary:end -->

## 언급
<!-- kb:mentions:start -->
- [[2026-11-04]] 09:12 @김철수 와 비밀스러운 daily 원문
<!-- kb:mentions:end -->
"""

TOPIC = """---
type: topic
---
# 캐시

[[projects/KB]]로 돌아가기. 코드 예시 `[[KB-99]]` 그대로.

```
[[projects/KB]] 코드 블록 안
```
"""

DECISIONS = """# 결정 기록

<!-- kb:decisions:start -->
## 2026-11-04 캐시 updated 시각은 UTC로 저장
- 상태: 확정
- 관련: [[KB-12]] · [[projects/KB]] · @김철수
- 내용: 확정된 내용 알파
- 출처: [[2026-11-04]] 09:12 <!-- src:0123456789abcdef -->

## 2026-11-05 미확정 제안
- 상태: 제안
- 내용: 제안된 내용 베타
- 출처: [[2026-11-05]] 10:00 <!-- src:fedcba9876543210 -->

## 2026-11-05 기각된 제안
- 상태: 기각
- 내용: 기각된 내용 감마

## 2026-11-06 두 번째 확정
- 상태: 확정   <!-- 사람이 바꿈 -->
- 내용: 확정된 내용 델타
<!-- kb:decisions:end -->
"""


# ---------------------------------------------------------------- 도우미


def _write(vault, rel: str, text: str) -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def _setup(vault, server, **conf) -> None:
    kb_secrets.store("work-kb", "confluence", TOKEN)
    confluence = {
        "enabled": True,
        "base_url": server.base_url if server is not None else "http://127.0.0.1:9",
        "space_key": SPACE,
        "parent_page_id": "",
        "pat_ref": "keyring:work-kb/confluence",
    }
    confluence.update(conf)
    config.save_local(vault, {"environment": "home", "jira": {"base_url": JIRA, "project_keys": ["KB", "OPS"]}, "confluence": confluence})


def _run(vault, *args):
    out = io.StringIO()
    argv = list(args)
    only = []
    dry = False
    while argv:
        arg = argv.pop(0)
        if arg == "--dry-run":
            dry = True
        elif arg == "--only":
            only.append(argv.pop(0))
    code = confluence_push.run(config.load(vault), only=only, dry_run=dry, out=out)
    return code, out.getvalue()


def _writes(server) -> list[dict]:
    return [e for e in server.log if e["method"] in ("POST", "PUT")]


def _state(vault) -> dict:
    path = vault / ".state" / "confluence.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _page(server, title: str) -> dict:
    page = server.page_by_title(SPACE, title)
    assert page is not None, [p["title"] for p in server.user_pages()]
    return page


def _xml(storage: str) -> ET.Element:
    return ET.fromstring(mock_mod.STORAGE_ROOT + storage + "</ac:root>")


@pytest.fixture
def vault_pages(vault):
    _write(vault, "wiki/projects/KB.md", PROJECT)
    _write(vault, "wiki/topics/캐시.md", TOPIC)
    _write(vault, "wiki/decisions.md", DECISIONS)
    _write(vault, "wiki/people/김철수.md", "# 김철수\n")
    _write(vault, "daily/2026-11-04.md", "## 기록\n- 09:12 비밀스러운 daily 원문\n")
    return vault


# ---------------------------------------------------------------- 생성·갱신·변경 없음


def test_create_then_update_bumps_version(vault_pages, mock_confluence, fake_now):
    fake_now("2026-11-05T18:00:00+09:00")
    _setup(vault_pages, mock_confluence)
    code, out = _run(vault_pages)
    assert code == 0, out
    titles = sorted(p["title"] for p in mock_confluence.user_pages())
    assert titles == ["[KB] 결정 기록", "[KB] 지식 저장고", "[KB] 캐시"]
    page = _page(mock_confluence, "[KB] 지식 저장고")
    assert page["version"] == 1
    state = _state(vault_pages)
    assert set(state) == {"wiki/decisions.md", "wiki/projects/KB.md", "wiki/topics/캐시.md"}
    entry = state["wiki/projects/KB.md"]
    assert entry["page_id"] == page["id"] and entry["version"] == 1
    assert entry["pushed_at"] == "2026-11-05T18:00:00+09:00"
    assert len(entry["sha256"]) == 64
    assert "요약: 생성 3 · 갱신 0" in out

    _write(vault_pages, "wiki/projects/KB.md", PROJECT.replace("캐시 설계를 정리한다.", "캐시 설계를 다시 정리한다."))
    mock_confluence.log.clear()
    code, out = _run(vault_pages)
    assert code == 0, out
    page = _page(mock_confluence, "[KB] 지식 저장고")
    assert page["version"] == 2 and "다시 정리한다" in page["body"]
    puts = [e for e in mock_confluence.log if e["method"] == "PUT"]
    assert len(puts) == 1 and puts[0]["json"]["version"] == {"number": 2}
    assert puts[0]["path"] == f"/rest/api/content/{page['id']}"
    assert not [e for e in mock_confluence.log if e["method"] == "POST"]
    assert _state(vault_pages)["wiki/projects/KB.md"]["version"] == 2
    assert "요약: 생성 0 · 갱신 1 · 변경 없음 2" in out
    # 조회는 언제나 expand=version
    gets = [e for e in mock_confluence.log if e["method"] == "GET" and e["path"].startswith("/rest/api/content")]
    assert gets and all("version" in e["query"].get("expand", "") for e in gets)


def test_unchanged_is_skipped_without_network(vault_pages, mock_confluence):
    _setup(vault_pages, mock_confluence)
    assert _run(vault_pages)[0] == 0
    mock_confluence.log.clear()
    code, out = _run(vault_pages)
    assert code == 0
    assert mock_confluence.log == []
    assert "요약: 생성 0 · 갱신 0 · 변경 없음 3" in out


def test_title_prefix_change_pushes_again(vault_pages, mock_confluence):
    _setup(vault_pages, mock_confluence)
    assert _run(vault_pages, "--only", "wiki/projects/KB.md")[0] == 0
    page_id = _page(mock_confluence, "[KB] 지식 저장고")["id"]
    _setup(vault_pages, mock_confluence, title_prefix="[메모] ")
    code, out = _run(vault_pages, "--only", "wiki/projects/KB.md")
    assert code == 0, out
    # 새 제목으로는 없으므로 state의 page_id로 찾아 같은 페이지의 제목을 바꾼다
    page = mock_confluence.pages[page_id]
    assert page["title"] == "[메모] 지식 저장고" and page["version"] == 2
    assert len(mock_confluence.user_pages()) == 1


def test_existing_page_found_by_title_is_updated(vault_pages, mock_confluence):
    _setup(vault_pages, mock_confluence)
    client = ConfluenceClient(mock_confluence.base_url, TOKEN, space_key=SPACE)
    made = client.create_page(SPACE, "[KB] 캐시", "<p>옛 내용</p>")
    client.close()
    code, out = _run(vault_pages, "--only", "wiki/topics/*.md")
    assert code == 0, out
    page = mock_confluence.pages[made["id"]]
    assert page["version"] == 2 and "옛 내용" not in page["body"]
    assert _state(vault_pages)["wiki/topics/캐시.md"]["page_id"] == made["id"]


def test_version_conflict_rereads_once(vault_pages):
    with mock_mod.start(version_conflict_once=True) as server:
        _setup(vault_pages, server)
        client = ConfluenceClient(server.base_url, TOKEN, space_key=SPACE)
        made = client.create_page(SPACE, "[KB] 지식 저장고", "<p>옛</p>")
        client.close()
        code, out = _run(vault_pages, "--only", "wiki/projects/KB.md")
        assert code == 0, out
        puts = [e for e in server.log if e["method"] == "PUT"]
        assert [p["json"]["version"]["number"] for p in puts] == [2, 3]
        assert server.pages[made["id"]]["version"] == 3
        assert _state(vault_pages)["wiki/projects/KB.md"]["version"] == 3


# ---------------------------------------------------------------- 부모

def test_empty_parent_means_space_homepage(vault_pages, mock_confluence):
    _setup(vault_pages, mock_confluence)
    assert _run(vault_pages, "--only", "wiki/projects/KB.md")[0] == 0
    home = mock_confluence.spaces[SPACE]["homepage"]
    assert _page(mock_confluence, "[KB] 지식 저장고")["ancestors"] == [home]
    post = [e for e in mock_confluence.log if e["method"] == "POST"][0]
    assert post["json"]["ancestors"] == [{"id": home}]
    assert post["json"]["space"] == {"key": SPACE}
    assert post["json"]["body"]["storage"]["representation"] == "storage"


def test_configured_parent(vault_pages, mock_confluence):
    client = ConfluenceClient(mock_confluence.base_url, TOKEN, space_key=SPACE)
    parent = client.create_page(SPACE, "업무 위키", "<p>부모</p>")
    client.close()
    _setup(vault_pages, mock_confluence, parent_page_id=parent["id"])
    assert _run(vault_pages, "--only", "wiki/decisions.md")[0] == 0
    assert _page(mock_confluence, "[KB] 결정 기록")["ancestors"] == [parent["id"]]  # 부모는 스페이스 맨 위에 만들었다
    assert not any(e["path"].startswith("/rest/api/space/") for e in mock_confluence.log)


# ---------------------------------------------------------------- 내용 손질


def test_front_matter_markers_mentions_and_comments_removed(vault_pages, mock_confluence):
    _setup(vault_pages, mock_confluence)
    assert _run(vault_pages)[0] == 0
    body = _page(mock_confluence, "[KB] 지식 저장고")["body"]
    _xml(body)
    assert "비밀스러운" not in body and "daily 원문" not in body
    assert "kb:" not in body and "<!--" not in body and "내부 메모" not in body
    assert "type: project" not in body and "updated:" not in body
    assert "요약 문장입니다." in body and "<table>" in body
    assert "<h2>언급</h2>" in body  # 구역 밖 제목은 남는다
    assert "<h1>" not in body  # 첫 H1은 제목으로 쓰고 본문에서 뺀다


def test_only_confirmed_decisions(vault_pages, mock_confluence):
    _setup(vault_pages, mock_confluence)
    assert _run(vault_pages, "--only", "wiki/decisions.md")[0] == 0
    body = _page(mock_confluence, "[KB] 결정 기록")["body"]
    assert "확정된 내용 알파" in body and "확정된 내용 델타" in body
    assert "제안된 내용 베타" not in body and "기각된 내용 감마" not in body
    assert "src:" not in body and "사람이 바꿈" not in body and "<!--" not in body


def test_keep_confirmed_decisions_unit():
    text = "# 결정\n\n머리말\n\n## a\n- 상태: 제안\n\n## b\n* 상태 : **확정**\n```\n## 코드 안 제목\n```\n## c\n- 상태: 확정 <!-- src:1 -->\n"
    out = confluence_push.keep_confirmed_decisions(text)
    assert out.startswith("# 결정\n\n머리말\n\n")
    assert "## a" not in out and "## b" in out and "## 코드 안 제목" in out and "## c" in out


def test_link_rewriting(vault_pages, mock_confluence):
    _setup(vault_pages, mock_confluence)
    assert _run(vault_pages)[0] == 0
    body = _page(mock_confluence, "[KB] 지식 저장고")["body"]
    root = _xml(body)
    anchors = {a.text: a.get("href") for a in root.iter("a")}
    assert anchors["OPS-3"] == f"{JIRA}/browse/OPS-3"
    assert anchors["KB-12"] == f"{JIRA}/browse/KB-12"
    assert '<ac:link><ri:page ri:content-title="[KB] 캐시"/>' in body
    # daily·사람 페이지(push_globs 밖)는 글자만
    assert "2026-11-04" in body and "김철수" in body
    assert "[[" not in body and "people/" not in body

    topic = _page(mock_confluence, "[KB] 캐시")["body"]
    _xml(topic)
    assert '<ri:page ri:content-title="[KB] 지식 저장고"/>' in topic
    assert "<code>[[KB-99]]</code>" in topic  # 코드 안 위키링크는 그대로
    assert "[[projects/KB]] 코드 블록 안" in topic

    decisions = _page(mock_confluence, "[KB] 결정 기록")["body"]
    assert f'<a href="{JIRA}/browse/KB-12">KB-12</a>' in decisions
    assert '<ri:page ri:content-title="[KB] 지식 저장고"/>' in decisions


def test_to_storage_is_well_formed_for_tricky_input():
    resolver = confluence_push.LinkResolver({"wiki/topics/A&B.md": "[KB] A&B \"따옴표\""}, None)
    body = (
        "줄 <br> 바꿈 &nbsp; &bogus; & < > \x0c\n\n<div onclick='x'>블록</div>\n\n"
        "[[topics/A&B|별칭 ]]> 끝]] · [[KB-1]] · ![[raw/assets/x.png]]\n\n| a | b |\n|---|---|\n| [[topics/A&B\\|표]] | 2 |\n"
    )
    storage = confluence_push.to_storage(body, resolver)
    root = _xml(storage)
    assert "&lt;br&gt;" in storage and "&lt;div" in storage and "&#160;" in storage and "&amp;bogus;" in storage
    links = list(root.iter("{http://atlassian.com/content}link"))
    assert len(links) == 2
    page = links[0].find("{http://atlassian.com/resource/identifier}page")
    assert page.get("{http://atlassian.com/resource/identifier}content-title") == '[KB] A&B "따옴표"'
    assert "KB-1" in storage and "<a " not in storage  # Jira 주소가 없으면 글자
    assert "raw/assets/x.png" in storage and "<img" not in storage


def test_title_sources():
    assert confluence_push.prepare("wiki/topics/x.md", "---\ntitle: 앞\n---\n# 제목\n본문\n".encode(), "[P] ").title == "[P] 앞"
    doc = confluence_push.prepare("wiki/topics/x.md", "\n# 제목 #\n본문\n".encode(), "[P] ")
    assert doc.title == "[P] 제목" and "제목" not in doc.body
    assert confluence_push.prepare("wiki/topics/이름.md", "본문만\r\n".encode(), "").title == "이름"


def test_broken_markers_fail_that_file_only(vault_pages, mock_confluence):
    _setup(vault_pages, mock_confluence)
    _write(vault_pages, "wiki/topics/깨짐.md", "# 깨짐\n<!-- kb:summary:start -->\n끝 없음\n")
    code, out = _run(vault_pages)
    assert code == 1
    assert "wiki/topics/깨짐.md" in out and "관리 구역" in out
    assert len(mock_confluence.user_pages()) == 3
    assert "wiki/topics/깨짐.md" not in _state(vault_pages)


# ---------------------------------------------------------------- 금지 폴더


@pytest.mark.parametrize(
    "globs",
    [
        ["wiki/projects/*.md", "daily/*.md"],
        ["**/*.md"],
        ["*/2026-11-04.md"],
        ["jira/*.md"],
        ["raw/**"],
        [".state/*.json"],
        ["../outside/*.md"],
        ["wiki/../daily/*.md"],
        # Windows가 같은 폴더로 여는 다른 철자(끝의 점·공백, 스트림)
        ["daily./*.md"],
        ["DAILY./*.md"],
        ["daily /*.md"],
        ["daily::$INDEX_ALLOCATION/*.md"],
        ["daily:$I30:$INDEX_ALLOCATION/*.md"],
        ["wiki/projects/*.md", "Daily. /2026-11-04.md"],
    ],
)
def test_forbidden_globs_refused(vault_pages, mock_confluence, globs):
    _setup(vault_pages, mock_confluence, push_globs=globs)
    with pytest.raises(core.PolicyRefused) as exc:
        _run(vault_pages)
    assert exc.value.exit_code == 7
    assert mock_confluence.log == []
    assert not (vault_pages / ".state" / "confluence.json").exists()


@pytest.mark.parametrize("only", ["daily/2026-11-04.md", "inbox/*", "JIRA/KB-1.md", r"raw\assets\x.png"])
def test_only_forbidden_refused(vault_pages, mock_confluence, only):
    _setup(vault_pages, mock_confluence)
    with pytest.raises(core.PolicyRefused):
        _run(vault_pages, "--only", only)
    assert mock_confluence.log == []


def test_only_outside_push_globs_is_config_error(vault_pages, mock_confluence):
    _setup(vault_pages, mock_confluence)
    with pytest.raises(core.ConfigError):
        _run(vault_pages, "--only", "wiki/people/김철수.md")
    assert mock_confluence.log == []


def test_forbidden_refused_via_cli(run_kb, vault_pages, mock_confluence):
    _setup(vault_pages, mock_confluence, push_globs=["wiki/projects/*.md", "daily/*.md"])
    result = run_kb("push")
    assert result.returncode == 7, result.stdout + result.stderr
    assert "daily" in result.stderr and "금지" in result.stderr
    assert mock_confluence.log == []


def test_agent_refused(run_kb, vault_pages, mock_confluence):
    _setup(vault_pages, mock_confluence)
    result = run_kb("push", env={"KB_AGENT": "1"})
    assert result.returncode == 7
    assert mock_confluence.log == []


# ---------------------------------------------------------------- 시험 실행·설정


def test_dry_run_writes_nothing_and_sends_nothing(vault_pages, mock_confluence):
    _setup(vault_pages, mock_confluence)
    before = {p: p.read_bytes() for p in vault_pages.rglob("*") if p.is_file()}
    code, out = _run(vault_pages, "--dry-run")
    assert code == 0, out
    assert mock_confluence.log == []
    after = {p: p.read_bytes() for p in vault_pages.rglob("*") if p.is_file()}
    assert after == before
    assert "시험 실행" in out and "wiki/projects/KB.md" in out and "[KB] 지식 저장고" in out
    assert "요약: 보낼 것 3" in out


def test_dry_run_after_push_shows_unchanged(vault_pages, mock_confluence):
    _setup(vault_pages, mock_confluence)
    assert _run(vault_pages)[0] == 0
    _write(vault_pages, "wiki/topics/캐시.md", TOPIC + "\n추가\n")
    mock_confluence.log.clear()
    code, out = _run(vault_pages, "--dry-run")
    assert code == 0 and mock_confluence.log == []
    assert "갱신 예정" in out and "변경 없음" in out and "요약: 보낼 것 1 · 변경 없음 2" in out


def test_disabled_does_nothing(vault_pages, mock_confluence):
    _setup(vault_pages, mock_confluence, enabled=False)
    code, out = _run(vault_pages)
    assert code == 0 and "꺼져" in out
    assert mock_confluence.log == []


def test_missing_space_key_is_config_error(vault_pages, mock_confluence):
    _setup(vault_pages, mock_confluence, space_key="")
    with pytest.raises(core.ConfigError):
        _run(vault_pages)


def test_401_exits_2(run_kb, vault_pages):
    with mock_mod.start(require_token="other-token") as server:
        _setup(vault_pages, server)
        result = run_kb("push")
        assert result.returncode == 2, result.stdout + result.stderr
        assert "401" in result.stderr
        assert server.user_pages() == []
    assert not (vault_pages / ".state" / "confluence.json").exists()


def test_missing_pat_exits_2(run_kb, vault_pages, mock_confluence):
    _setup(vault_pages, mock_confluence, pat_ref="keyring:work-kb/nothing")
    result = run_kb("push")
    assert result.returncode == 2
    assert mock_confluence.log == []


def test_connection_refused_exits_3(run_kb, vault_pages):
    server = mock_mod.start()
    server.stop()
    _setup(vault_pages, server)
    result = run_kb("push")
    assert result.returncode == 3, result.stdout + result.stderr


def test_forbid_create_is_auth_error(vault_pages):
    with mock_mod.start(forbid_create=True) as server:
        _setup(vault_pages, server)
        with pytest.raises(core.AuthError):
            _run(vault_pages)
        assert server.user_pages() == []
    assert _state(vault_pages) == {}


def test_success_cli_output(run_kb, vault_pages, mock_confluence):
    _setup(vault_pages, mock_confluence)
    result = run_kb("push", "--only", "wiki/topics/*")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "+ wiki/topics/캐시.md: 생성 → [KB] 캐시" in result.stdout
    assert [p["title"] for p in mock_confluence.user_pages()] == ["[KB] 캐시"]


# ---------------------------------------------------------------- 클라이언트


def test_personal_space_own_first(mock_confluence):
    client = ConfluenceClient(mock_confluence.base_url, TOKEN)
    found = client.personal_space()
    assert isinstance(found, PersonalSpace)
    assert found.username == "honggildong" and found.own["key"] == SPACE and found.candidates == [found.own]
    assert [(e["path"], e["query"]) for e in mock_confluence.log] == [
        ("/rest/api/user/current", {}),
        ("/rest/api/space", {"spaceKey": SPACE}),
    ]


def test_personal_space_falls_back_to_list():
    spaces = [{"key": "~kimcheolsu", "name": "김철수"}, {"key": "~leeyounghee", "name": "이영희"}]
    with mock_mod.start(personal_spaces=spaces) as server:
        client = ConfluenceClient(server.base_url, TOKEN)
        found = client.personal_space()
    assert found.own is None
    assert [s["key"] for s in found.candidates] == ["~kimcheolsu", "~leeyounghee"]
    # D55: ~username → ~userKey → type=personal 목록
    assert [e["path"] for e in server.log] == ["/rest/api/user/current"] + ["/rest/api/space"] * 3
    assert server.log[1]["query"] == {"spaceKey": SPACE}
    assert server.log[2]["query"] == {"spaceKey": "~" + mock_mod.USER["userKey"]}
    assert server.log[3]["query"] == {"type": "personal", "start": "0", "limit": "25"}


def test_personal_space_user_key_step():
    user_key_space = "~" + mock_mod.USER["userKey"]
    spaces = [{"key": "~kimcheolsu", "name": "김철수"}, {"key": user_key_space, "name": "홍길동"}]
    with mock_mod.start(personal_spaces=spaces) as server:
        client = ConfluenceClient(server.base_url, TOKEN)
        found = client.personal_space()
    assert found.own is not None and found.own["key"] == user_key_space
    assert found.candidates == [found.own]
    assert [e["query"] for e in server.log[1:]] == [{"spaceKey": SPACE}, {"spaceKey": user_key_space}]


def test_personal_space_key_match_ignores_case():
    with mock_mod.start(personal_spaces=[{"key": "~HongGilDong", "name": "홍길동"}]) as server:
        client = ConfluenceClient(server.base_url, TOKEN)
        found = client.personal_space()
    assert found.own is not None and found.own["key"] == "~HongGilDong"
    assert [e["query"] for e in server.log[1:]] == [{"spaceKey": SPACE}]


def test_client_honours_timeout(vault, mock_confluence, monkeypatch):
    from lib import net

    _setup(vault, mock_confluence, timeout=7)
    seen: list[float] = []
    original = net.request

    def spy(session, method, url, **kw):
        seen.append(kw.get("timeout"))
        return original(session, method, url, **kw)

    monkeypatch.setattr(net, "request", spy)
    with ConfluenceClient.from_config(config.load(vault)) as client:
        assert client.timeout == 7.0
        client.personal_space()
    assert seen and all(t == 7.0 for t in seen)


def test_client_5xx_retries_reads_once_but_not_writes(mock_confluence, monkeypatch):
    from lib import net

    sleeps: list[float] = []
    monkeypatch.setattr(net, "_sleep", sleeps.append)
    client = ConfluenceClient(mock_confluence.base_url, TOKEN, space_key=SPACE)
    mock_confluence.modes["server_error"] = 1
    assert client.find_page(SPACE, "없는 제목") is None
    gets = [e for e in mock_confluence.log if e["path"] == "/rest/api/content"]
    assert len(gets) == 2 and sleeps == [1.0]
    mock_confluence.modes["server_error"] = 2
    with pytest.raises(core.NetworkError) as exc:
        client.get_page("999999")
    assert exc.value.after_send is False
    mock_confluence.log.clear()
    mock_confluence.modes["server_error"] = 1
    with pytest.raises(core.NetworkError) as exc:
        client.create_page(SPACE, "제목", "<p>a</p>")
    assert exc.value.after_send is True
    assert [e["method"] for e in mock_confluence.log] == ["POST"]  # 쓰기는 다시 보내지 않는다
    client.close()


def test_personal_space_anonymous_stops():
    with mock_mod.start(anonymous=True) as server:
        client = ConfluenceClient(server.base_url, TOKEN)
        with pytest.raises(core.AuthError):
            client.personal_space()
    assert [e["path"] for e in server.log] == ["/rest/api/user/current"]


def test_personal_spaces_paging():
    spaces = [{"key": f"~user{i:02d}", "name": f"사용자{i}"} for i in range(30)]
    with mock_mod.start(personal_spaces=spaces) as server:
        client = ConfluenceClient(server.base_url, TOKEN)
        listed = client.personal_spaces()
    assert len(listed) == 30
    assert [e["query"].get("start") for e in server.log] == ["0", "25"]


def test_client_from_config_and_errors(vault, mock_confluence):
    _setup(vault, mock_confluence)
    with ConfluenceClient.from_config(config.load(vault)) as client:
        assert client.space_key == SPACE and client.parent_page_id is None
        assert client.current_user()["username"] == "honggildong"
        assert client.get_page("999999") is None
        assert client.find_page(SPACE, "없는 제목") is None
        assert client.homepage_id() == mock_confluence.spaces[SPACE]["homepage"]
        with pytest.raises(core.ConfigError):
            client.homepage_id("~nobody")
        made = client.create_page(SPACE, "제목", "<p>a</p>")
        assert made["version"] == {"number": 1}
        with pytest.raises(core.KbError) as exc:
            client.create_page(SPACE, "제목", "<p>b</p>")
        assert exc.value.status == 400 and "already exists" in str(exc.value)
        with pytest.raises(NotFound):
            client._get("/content/999999")
        assert client.update_page(made["id"], "제목", "<p>c</p>", 2)["version"] == {"number": 2}


def test_client_bad_token_is_auth_error():
    with mock_mod.start(bad_token="expired") as server:
        client = ConfluenceClient(server.base_url, "expired")
        with pytest.raises(core.AuthError):
            client.current_user()


# ---------------------------------------------------------------- 모의 서버


def _call(server, method, path, body=None, params=None):
    resp = requests.request(method, server.base_url + path, json=body, params=params, headers={"Authorization": "Bearer t"}, timeout=5)
    return resp.status_code, resp.json()


def _page_body(title, value, *, space=SPACE, version=None, ancestors=None):
    body = {"type": "page", "title": title, "space": {"key": space}, "body": {"storage": {"value": value, "representation": "storage"}}}
    if version is not None:
        body["version"] = {"number": version}
    if ancestors is not None:
        body["ancestors"] = ancestors
    return body


def test_mock_content_rules(mock_confluence):
    status, page = _call(mock_confluence, "POST", "/rest/api/content", _page_body("가", "<p>ok</p>"))
    assert status == 200 and page["version"] == {"number": 1}
    pid = page["id"]
    # 잘 짜이지 않은 XML
    status, err = _call(mock_confluence, "POST", "/rest/api/content", _page_body("나", "<p>열림"))
    assert status == 400 and "xhtml" in err["message"]
    status, _ = _call(mock_confluence, "POST", "/rest/api/content", _page_body("나", "a &nbsp; b"))
    assert status == 400
    # 같은 스페이스 같은 제목
    status, err = _call(mock_confluence, "POST", "/rest/api/content", _page_body("가", "<p/>"))
    assert status == 400 and "already exists" in err["message"]
    # 없는 부모, 없는 스페이스
    assert _call(mock_confluence, "POST", "/rest/api/content", _page_body("다", "<p/>", ancestors=[{"id": "1"}]))[0] == 400
    assert _call(mock_confluence, "POST", "/rest/api/content", _page_body("다", "<p/>", space="~nobody"))[0] == 400
    # 갱신 버전 규칙
    assert _call(mock_confluence, "PUT", f"/rest/api/content/{pid}", _page_body("가", "<p/>", version=1))[0] == 409
    assert _call(mock_confluence, "PUT", f"/rest/api/content/{pid}", _page_body("가", "<p/>", version=3))[0] == 409
    status, page = _call(mock_confluence, "PUT", f"/rest/api/content/{pid}", _page_body("가", "<p>둘</p>", version=2))
    assert status == 200 and page["version"] == {"number": 2}
    assert _call(mock_confluence, "PUT", "/rest/api/content/424242", _page_body("가", "<p/>", version=2))[0] == 404
    # 조회
    status, found = _call(mock_confluence, "GET", "/rest/api/content", params={"spaceKey": SPACE, "title": "가", "expand": "version,body.storage"})
    assert status == 200 and found["size"] == 1
    assert found["results"][0]["version"] == {"number": 2}
    assert found["results"][0]["body"]["storage"]["value"] == "<p>둘</p>"
    status, one = _call(mock_confluence, "GET", f"/rest/api/content/{pid}")
    assert status == 200 and "version" not in one
    assert one["space"]["key"] == SPACE  # expand가 없으면 DC 기본값처럼 space가 있다
    status, one = _call(mock_confluence, "GET", f"/rest/api/content/{pid}", params={"expand": "version"})
    assert status == 200 and "space" not in one  # expand를 주면 그것만
    status, one = _call(mock_confluence, "GET", f"/rest/api/content/{pid}", params={"expand": "version,space"})
    assert one["space"]["key"] == SPACE and one["version"] == {"number": 2}
    status, space = _call(mock_confluence, "GET", f"/rest/api/space/{SPACE}", params={"expand": "homepage"})
    assert status == 200 and space["homepage"]["id"] == mock_confluence.spaces[SPACE]["homepage"]


def test_mock_modes():
    assert {"version_conflict_once", "forbid_create", "anonymous", "personal_spaces"} <= mock_mod.KNOWN_MODES
    with mock_mod.start(forbid_create=True) as server:
        assert _call(server, "POST", "/rest/api/content", _page_body("가", "<p/>"))[0] == 403
    with mock_mod.start(version_conflict_once=True) as server:
        status, page = _call(server, "POST", "/rest/api/content", _page_body("가", "<p/>"))
        pid = page["id"]
        assert _call(server, "PUT", f"/rest/api/content/{pid}", _page_body("가", "<p/>", version=2))[0] == 409
        assert server.pages[pid]["version"] == 2
        assert _call(server, "PUT", f"/rest/api/content/{pid}", _page_body("가", "<p/>", version=3))[0] == 200
    with mock_mod.start(reject_title="나쁜 제목") as server:
        status, err = _call(server, "POST", "/rest/api/content", _page_body("나쁜 제목", "<p/>"))
        assert status == 400 and "모의 거부" in err["message"]
    with mock_mod.start(redirect_to="http://127.0.0.1:9", redirect_methods=["PUT"]) as server:
        assert _call(server, "GET", "/rest/api/content", params={"spaceKey": SPACE})[0] == 200
        resp = requests.put(server.base_url + "/rest/api/content/1", json={}, headers={"Authorization": "Bearer t"}, allow_redirects=False, timeout=5)
        assert resp.status_code == 307 and resp.headers["Location"] == "http://127.0.0.1:9/rest/api/content/1"


# ---------------------------------------------------------------- 검토 반영: 금지 경로(철자·링크·자동 생성 페이지)


def _junction(link, target) -> None:
    """`mklink /J link target`. 만들지 못하면 그 테스트를 건너뛴다."""
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    if result.returncode != 0 or not os.path.isjunction(link):
        pytest.skip("mklink /J를 쓸 수 없습니다")


@pytest.fixture
def junctions():
    made: list = []

    def make(link, target) -> None:
        _junction(link, target)
        made.append(link)

    yield make
    for link in made:
        try:
            os.rmdir(link)  # 정션만 지운다(대상은 그대로)
        except OSError:
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="정션은 Windows 전용")
def test_junction_to_daily_is_refused(vault_pages, mock_confluence, junctions):
    shutil.rmtree(vault_pages / "wiki" / "topics")
    junctions(vault_pages / "wiki" / "topics", vault_pages / "daily")  # mklink /J wiki\topics daily
    _setup(vault_pages, mock_confluence)  # 기본 push_globs(wiki/topics/*.md 포함)
    with pytest.raises(core.PolicyRefused) as exc:
        _run(vault_pages)
    assert exc.value.exit_code == 7
    assert "wiki/topics/2026-11-04.md" in str(exc.value) and "외부 게시 금지 폴더" in str(exc.value)
    assert mock_confluence.log == []
    assert not (vault_pages / ".state" / "confluence.json").exists()
    with pytest.raises(core.PolicyRefused):
        _run(vault_pages, "--dry-run")


@pytest.mark.skipif(sys.platform != "win32", reason="정션은 Windows 전용")
def test_junction_inside_wiki_is_refused(vault_pages, mock_confluence, junctions):
    junctions(vault_pages / "wiki" / "alias", vault_pages / "wiki" / "projects")
    _setup(vault_pages, mock_confluence, push_globs=["wiki/alias/*.md"])
    with pytest.raises(core.PolicyRefused) as exc:
        _run(vault_pages)
    assert exc.value.exit_code == 7 and "심볼릭 링크·정션" in str(exc.value)
    assert mock_confluence.log == []


def test_symlinked_file_is_refused(vault_pages, mock_confluence, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("# 밖\n볼트 밖 비밀\n", encoding="utf-8")
    link = vault_pages / "wiki" / "topics" / "밖.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("심볼릭 링크를 만들 권한이 없습니다")
    _setup(vault_pages, mock_confluence)
    with pytest.raises(core.PolicyRefused) as exc:
        _run(vault_pages)
    assert "볼트 밖" in str(exc.value)
    assert mock_confluence.log == []


def test_collect_checks_resolved_path_not_only_spelling(vault_pages, monkeypatch):
    """패턴 검사를 빼도 파일마다 실제 위치로 다시 막는다."""
    monkeypatch.setattr(confluence_push, "_glob_reason", lambda pattern: None)
    _setup(vault_pages, None, push_globs=["daily./*.md", "daily::$INDEX_ALLOCATION/*.md"])
    selection = confluence_push.collect(config.load(vault_pages))
    assert selection.files == []
    assert selection.refused and all("금지 폴더" in reason for _, reason in selection.refused)


@pytest.mark.parametrize(
    "rel",
    ["daily./a.md", "DAILY /a.md", "daily::$INDEX_ALLOCATION/a.md", "inbox.:$DATA/a.md", "wiki/_lint.md", "WIKI/_Proposals.md",
     "wiki/index.md", "wiki/Actions.md", "wiki/index.md.", "wiki/actions.md::$DATA", "wiki/_*.md", "wiki/.../x.md"],
)
def test_forbidden_reason_spellings(rel):
    assert confluence_push.forbidden_reason(rel)


@pytest.mark.parametrize("rel", ["wiki/topics/index.md", "wiki/projects/_draft.md", "wiki/decisions.md", "wiki/topics/*.md"])
def test_forbidden_reason_allows_wiki_pages(rel):
    assert confluence_push.forbidden_reason(rel) is None


@pytest.mark.parametrize(
    "globs",
    [["wiki/**/*.md"], ["wiki/*.md"], ["wiki/_lint.md"], ["wiki/_*.md"], ["wiki/index.md"], ["wiki/actions.md"], ["wiki/[_]proposals.md"]],
)
def test_generated_wiki_pages_refused(vault_pages, mock_confluence, globs):
    _write(vault_pages, "wiki/_lint.md", "# 점검\n- 2026-10-01 비공개 제안 PROPOSED_SECRET_TITLE\n")
    _write(vault_pages, "wiki/_proposals.md", "# 제안\n- 미확정\n")
    _write(vault_pages, "wiki/index.md", "# 색인\n")
    _write(vault_pages, "wiki/actions.md", "# 할 일\n- [ ] 미확정 후보\n")
    _setup(vault_pages, mock_confluence, push_globs=globs)
    with pytest.raises(core.PolicyRefused) as exc:
        _run(vault_pages)
    assert exc.value.exit_code == 7 and "게시 금지" in str(exc.value)
    assert mock_confluence.log == []
    assert not (vault_pages / ".state" / "confluence.json").exists()


@pytest.mark.parametrize("only", ["wiki/_lint.md", "wiki/index.md", "wiki/actions.md", "daily./2026-11-04.md"])
def test_only_generated_or_spelled_refused(vault_pages, mock_confluence, only):
    _setup(vault_pages, mock_confluence)
    with pytest.raises(core.PolicyRefused):
        _run(vault_pages, "--only", only)
    assert mock_confluence.log == []


# ---------------------------------------------------------------- 검토 반영: 스페이스·결정·주석


TEAM_SPACES = [{"key": SPACE, "name": "홍길동"}, {"key": "TEAM", "name": "팀", "type": "global"}]


@pytest.mark.parametrize("edit", [False, True])
def test_space_change_creates_in_new_space(vault_pages, edit):
    with mock_mod.start(personal_spaces=TEAM_SPACES) as server:
        _setup(vault_pages, server)
        assert _run(vault_pages, "--only", "wiki/topics/캐시.md")[0] == 0
        old = server.page_by_title(SPACE, "[KB] 캐시")
        _setup(vault_pages, server, space_key="TEAM")
        if edit:
            _write(vault_pages, "wiki/topics/캐시.md", TOPIC + "\n추가 문장\n")
        server.log.clear()
        code, out = _run(vault_pages, "--only", "wiki/topics/캐시.md")
        assert code == 0, out
        new = server.page_by_title("TEAM", "[KB] 캐시")
        assert new is not None and new["id"] != old["id"] and new["version"] == 1
        assert not [e for e in server.log if e["method"] == "PUT"]
        assert server.pages[old["id"]]["version"] == 1 and server.pages[old["id"]]["space"] == SPACE
        lookup = [e for e in server.log if e["path"] == f"/rest/api/content/{old['id']}"]
        assert lookup and "space" in lookup[0]["query"]["expand"].split(",")
        assert _state(vault_pages)["wiki/topics/캐시.md"]["page_id"] == new["id"]


@pytest.mark.parametrize("candidate_space", [None, {}, {"key": ""}, {"key": "~other"}, "~honggildong"])
def test_push_one_needs_matching_space_on_state_page(candidate_space):
    """state의 page_id 페이지에 space.key가 없거나 다르면 갱신하지 않고 새로 만든다(안전 쪽)."""
    calls: list[str] = []

    class FakeClient:
        def find_page(self, space_key, title):
            return None

        def get_page(self, page_id):
            page = {"id": page_id, "version": {"number": 4}}
            if candidate_space is not None:
                page["space"] = candidate_space
            return page

        def create_page(self, space_key, title, storage, parent_id=None):
            calls.append("create")
            return {"id": "9", "version": {"number": 1}}

        def update_page(self, *args, **kwargs):
            calls.append("update")
            return {"id": "5", "version": {"number": 5}}

    item = confluence_push.Item(rel="wiki/a.md", title="t", storage="<p/>", sha256="x", previous={"page_id": "5"})
    kind, _ = confluence_push.push_one(FakeClient(), SPACE, item, lambda: None, forget=lambda: calls.append("forget"))
    assert kind == "created" and calls == ["forget", "create"]


def test_push_one_accepts_state_page_in_same_space_ignoring_case():
    class FakeClient:
        def find_page(self, space_key, title):
            return None

        def get_page(self, page_id):
            return {"id": page_id, "version": {"number": 4}, "space": {"key": "~HongGilDong"}}

        def update_page(self, page_id, title, storage, version, *, space_key=None):
            return {"id": page_id, "version": {"number": version}}

    item = confluence_push.Item(rel="wiki/a.md", title="t", storage="<p/>", sha256="x", previous={"page_id": "5"})
    kind, page = confluence_push.push_one(FakeClient(), SPACE, item, lambda: None)
    assert kind == "updated" and page["version"] == {"number": 5}


def test_stale_state_entry_dropped_when_create_fails(vault_pages):
    with mock_mod.start(personal_spaces=TEAM_SPACES) as server:
        _setup(vault_pages, server)
        assert _run(vault_pages, "--only", "wiki/topics/캐시.md")[0] == 0
        server.modes["reject_title"] = "[KB] 캐시"
        _setup(vault_pages, server, space_key="TEAM")
        code, out = _run(vault_pages, "--only", "wiki/topics/캐시.md")
        assert code == 1 and "모의 거부" in out
        assert "wiki/topics/캐시.md" not in _state(vault_pages)  # 다른 스페이스의 page_id는 지웠다
        assert not [e for e in server.log if e["method"] == "PUT"]


@pytest.mark.parametrize(
    "text, leaked",
    [
        # 항목 안의 `# ` 줄도 그 항목에 속한다
        ("# 결정 로그\n\n## 2026-11-05 미확정\n- 상태: 제안\n- 내용: 공개 전 검토 필요\n# 메모\n- 미확정 세부: 비공개 일정 알파\n", "비공개 일정 알파"),
        # 코드 블록 안의 상태 줄은 보지 않는다
        ("## a\n- 상태: 제안\n```\n- 상태: 확정\n```\n- 내용: 펜스 비밀\n", "펜스 비밀"),
        ("## a\n~~~\n- 상태: 확정\n~~~\n- 내용: 펜스만 비밀\n", "펜스만 비밀"),
        # 상태 줄이 둘 이상이면 확정이 아니다
        ("## a\n- 상태: 확정\n- 상태: 기각\n- 내용: 이중 상태 비밀\n", "이중 상태 비밀"),
        ("## a\n- 상태: 확정\n### 세부\n- 상태: 제안\n- 내용: 하위 절 비밀\n", "하위 절 비밀"),
        # HTML 주석 안의 상태 줄은 보지 않는다
        ("## a\n<!--\n- 상태: 확정\n-->\n- 내용: 주석 비밀\n", "주석 비밀"),
        # 상태 줄이 없으면 게시하지 않는다
        ("## a\n- 내용: 상태 없음 비밀\n", "상태 없음 비밀"),
        # 탭으로 띄운 `##`도 새 항목이다(앞 확정 항목에 붙지 않는다)
        ("## a\n- 상태: 확정\n- 내용: 공개\n##\t탭 제목\n- 상태: 제안\n- 내용: 탭 비밀\n", "탭 비밀"),
        ("## a\n- 상태: 확정\n- 내용: 공개\n  ## 들여쓴 제목\n- 상태: 제안\n- 내용: 들여쓰기 비밀\n", "들여쓰기 비밀"),
        ("## a\n- 상태: 확정이 아님\n- 내용: 비슷한 글자 비밀\n", "비슷한 글자 비밀"),
        ("## a\n- **상태**: 제안\n- 상태: 확정\n- 내용: 굵은 상태 비밀\n", "굵은 상태 비밀"),
    ],
)
def test_keep_confirmed_decisions_fail_closed(text, leaked):
    out = confluence_push.keep_confirmed_decisions(text)
    assert leaked not in out, out


def test_keep_confirmed_decisions_keeps_subsections_of_confirmed():
    text = "# 결정\n\n## a\n- 상태: 확정 <!-- src:1 -->\n### 세부\n- 메모: 공개 세부\n```\n## 코드\n```\n\n## b\n- 상태: 제안\n"
    out = confluence_push.keep_confirmed_decisions(text)
    assert out.startswith("# 결정\n\n## a\n") and "공개 세부" in out and "## 코드" in out and "## b" not in out


def test_unclosed_comment_fails_that_file(vault_pages, mock_confluence):
    _setup(vault_pages, mock_confluence)
    _write(vault_pages, "wiki/topics/u.md", "# u\nbefore\n<!-- src:abc unclosed UNCLOSED_SECRET\nafter\n")
    code, out = _run(vault_pages)
    assert code == 1
    assert "wiki/topics/u.md" in out and "HTML 주석이 닫히지 않음" in out
    assert len(mock_confluence.user_pages()) == 3
    assert "wiki/topics/u.md" not in _state(vault_pages)
    for page in mock_confluence.user_pages():
        assert "<!--" not in page["body"] and "&lt;!--" not in page["body"] and "src:" not in page["body"]
        assert "UNCLOSED_SECRET" not in page["body"]


def test_comment_markers_inside_code_are_fine():
    doc = confluence_push.prepare("wiki/topics/c.md", "# c\n`<!--` 예시\n```\n<!-- 코드 안\n```\n".encode(), "")
    assert "`<!--` 예시" in doc.body
    with pytest.raises(core.KbError):
        confluence_push.prepare("wiki/topics/c.md", "# c\n`a` <!-- 열림\n".encode(), "")


# ---------------------------------------------------------------- 검토 반영: 리다이렉트·파일별 실패·링크·해시


@pytest.mark.parametrize("method", ["GET", "POST", "PUT"])
def test_redirect_is_refused(vault_pages, method):
    with mock_mod.start() as target, mock_mod.start() as server:
        _setup(vault_pages, server)
        if method == "PUT":
            assert _run(vault_pages, "--only", "wiki/topics/캐시.md")[0] == 0
            _write(vault_pages, "wiki/topics/캐시.md", TOPIC + "\n바뀐 문장\n")
        before = _state(vault_pages)
        server.modes.update(redirect_to=target.base_url, redirect_methods=[method])
        server.log.clear()
        with pytest.raises(RedirectRefused) as exc:
            _run(vault_pages, "--only", "wiki/topics/캐시.md")
        assert exc.value.exit_code == 4
        assert [e["method"] for e in server.log if e["path"].startswith("/rest/api/content")][-1] == method
        assert target.log == []  # 리다이렉트를 따라가지 않았다
        assert _state(vault_pages) == before
        if method != "PUT":
            assert not (vault_pages / ".state" / "confluence.json").exists()


def test_redirect_exits_4_via_cli(run_kb, vault_pages):
    with mock_mod.start() as target, mock_mod.start(redirect_to="") as server:
        server.modes["redirect_to"] = target.base_url
        _setup(vault_pages, server)
        result = run_kb("push")
        assert result.returncode == 4, result.stdout + result.stderr
        assert "리다이렉트" in result.stderr
        assert target.log == []


def test_duplicate_title_fails_second_file_only(vault, mock_confluence):
    _write(vault, "wiki/topics/a.md", "---\ntitle: 같은 제목\n---\n가 본문\n")
    _write(vault, "wiki/topics/b.md", "---\ntitle: 같은 제목\n---\n나 본문\n")
    _write(vault, "wiki/topics/c.md", "# 다른 제목\n다 본문\n")
    _setup(vault, mock_confluence, push_globs=["wiki/topics/*.md"])
    code, out = _run(vault)
    assert code == 1
    assert sorted(p["title"] for p in mock_confluence.user_pages()) == ["[KB] 같은 제목", "[KB] 다른 제목"]
    assert "가 본문" in _page(mock_confluence, "[KB] 같은 제목")["body"]
    assert "  ! wiki/topics/b.md: " in out and "같은 제목" in out
    assert set(_state(vault)) == {"wiki/topics/a.md", "wiki/topics/c.md"}


def test_rejected_page_fails_others_pushed(vault_pages):
    with mock_mod.start(reject_title="[KB] 캐시") as server:
        _setup(vault_pages, server)
        code, out = _run(vault_pages)
        assert code == 1
        titles = sorted(p["title"] for p in server.user_pages())
        assert titles == ["[KB] 결정 기록", "[KB] 지식 저장고"]
        assert "  ! wiki/topics/캐시.md: " in out and "모의 거부" in out and "400" in out
        assert set(_state(vault_pages)) == {"wiki/decisions.md", "wiki/projects/KB.md"}
        assert "실패 1" in out


def test_only_still_links_other_push_globs_pages(vault_pages, mock_confluence):
    _setup(vault_pages, mock_confluence)
    assert _run(vault_pages, "--only", "wiki/topics/캐시.md")[0] == 0
    assert [p["title"] for p in mock_confluence.user_pages()] == ["[KB] 캐시"]
    assert '<ri:page ri:content-title="[KB] 지식 저장고"/>' in _page(mock_confluence, "[KB] 캐시")["body"]


def test_front_matter_title_only_change_is_pushed(vault, mock_confluence):
    _write(vault, "wiki/topics/plain.md", "---\ntitle: 첫 제목\n---\n링크 없는 본문\n")
    _setup(vault, mock_confluence, push_globs=["wiki/topics/*.md"])
    assert _run(vault)[0] == 0
    page_id = _page(mock_confluence, "[KB] 첫 제목")["id"]
    _write(vault, "wiki/topics/plain.md", "---\ntitle: 둘째 제목\n---\n링크 없는 본문\n")
    mock_confluence.log.clear()
    code, out = _run(vault)
    assert code == 0, out
    puts = [e for e in mock_confluence.log if e["method"] == "PUT"]
    assert len(puts) == 1 and puts[0]["path"] == f"/rest/api/content/{page_id}"
    assert mock_confluence.pages[page_id]["title"] == "[KB] 둘째 제목"


def test_dry_run_needs_no_pat(vault_pages, mock_confluence):
    _setup(vault_pages, mock_confluence, pat_ref="keyring:work-kb/nothing")
    code, out = _run(vault_pages, "--dry-run")
    assert code == 0, out
    assert mock_confluence.log == []


def test_main_refuses_agent(vault_pages, mock_confluence, monkeypatch):
    _setup(vault_pages, mock_confluence)
    monkeypatch.setenv("KB_AGENT", "1")
    with pytest.raises(core.PolicyRefused):
        confluence_push.main(["--root", str(vault_pages)])
    assert mock_confluence.log == []
