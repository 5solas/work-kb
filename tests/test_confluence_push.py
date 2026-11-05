"""`kb.py push` (scripts/confluence_push.py), lib/confluence_client.py, tests/mock_confluence.py (W5.2, D55·D56)."""
from __future__ import annotations

import io
import json
import xml.etree.ElementTree as ET

import pytest
import requests

import confluence_push
import mock_confluence as mock_mod
from lib import config, core
from lib import secrets as kb_secrets
from lib.confluence_client import ConfluenceClient, NotFound, PersonalSpace

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
    assert [e["path"] for e in server.log] == ["/rest/api/user/current", "/rest/api/space", "/rest/api/space"]
    assert server.log[1]["query"] == {"spaceKey": SPACE}
    assert server.log[2]["query"] == {"type": "personal", "start": "0", "limit": "25"}


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
