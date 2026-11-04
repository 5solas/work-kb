"""kb.py search (Phase 1: 순수 Python 훑기)."""
import json
import shutil
from pathlib import Path

import pytest

import search
from lib import config

ROOT = Path(__file__).resolve().parents[1]
NOTES = ROOT / "tests" / "fixtures" / "notes"
SAMPLE = ROOT / "examples" / "sample-vault"

INBOX_NOTE = "inbox/20261105-0930-회의 메모.md"


@pytest.fixture
def notes_vault(vault):
    shutil.copytree(NOTES / "daily", vault / "daily", dirs_exist_ok=True)
    shutil.copytree(NOTES / "inbox", vault / "inbox", dirs_exist_ok=True)
    return vault


def _hits(vault, query, scope="all"):
    return [(h.path, h.line, h.text) for h in search.search(config.load(vault), query, scope=scope)]


def _tree(root: Path) -> dict[str, bytes]:
    files = {p.relative_to(root).as_posix(): p for p in sorted(root.rglob("*")) if p.is_file()}
    return {rel: p.read_bytes() for rel, p in files.items() if not rel.startswith(".state/")}


def test_fixture_eols_are_as_documented():
    assert b"\r\n" not in (NOTES / "daily" / "2026-11-04.md").read_bytes()
    crlf = (NOTES / "daily" / "2026-11-05.md").read_bytes()
    assert crlf.count(b"\r\n") == crlf.count(b"\n") > 0


def test_korean_hit_in_korean_filename_under_korean_path(notes_vault):
    assert "한글" in str(notes_vault)
    hits = _hits(notes_vault, "김철수")
    assert ("daily/2026-11-04.md", 13, "- 09:12 [[KB-12]] @김철수 와 front matter 논의. updated는 UTC로 저장하기로.") in hits
    assert (INBOX_NOTE, 8, "- @김철수 가 캐시 설계 검토") in hits


def test_crlf_file_line_numbers_and_text_without_cr(notes_vault):
    assert _hits(notes_vault, "막힘") == [("daily/2026-11-05.md", 13, "- 09:30 [[OPS-3]] 막힘, @이영희 확인 대기")]


def test_case_insensitive(notes_vault):
    assert _hits(notes_vault, "cache") == [("daily/2026-11-05.md", 16, "- 17:00 검색어 대소문자 Cache 테스트")]
    assert _hits(notes_vault, "kb-12") == _hits(notes_vault, "KB-12")
    assert ("daily/2026-11-05.md", 14, "- 10:15 KB-12 캐시 리뷰 반영") in _hits(notes_vault, "kb-12")


def test_and_terms_any_order(notes_vault):
    both = _hits(notes_vault, "OPS-3 배포")
    assert {(p, n) for p, n, _ in both} == {("daily/2026-11-04.md", 14), (INBOX_NOTE, 7)}
    assert _hits(notes_vault, "배포 OPS-3") == both
    assert _hits(notes_vault, "  배포   OPS-3  ") == both
    assert _hits(notes_vault, "OPS-3 없는낱말") == []


def test_substring_inside_words_and_code(notes_vault):
    # 검색은 코드 블록·URL·인라인 코드를 빼지 않는다(링크와 다름)
    assert [p for p, _, _ in _hits(notes_vault, "KB-999")] == ["daily/2026-11-04.md"]
    assert [p for p, _, _ in _hits(notes_vault, "browse/KB-404")] == ["daily/2026-11-04.md"]
    assert len(_hits(notes_vault, "정산")) == 1


def test_order_daily_newest_first_then_inbox_wiki_jira(notes_vault):
    (notes_vault / "wiki" / "projects" / "OPS.md").write_text("# OPS\n\n- OPS-3 위키 언급\n", encoding="utf-8")
    (notes_vault / "jira" / "OPS-3.md").write_text("---\nkey: OPS-3\n---\n# OPS-3 배포 체크리스트\n", encoding="utf-8")
    paths = [p for p, _, _ in _hits(notes_vault, "OPS-3")]
    assert paths == [
        "daily/2026-11-05.md",
        "daily/2026-11-04.md",
        INBOX_NOTE,
        "wiki/projects/OPS.md",
        "jira/OPS-3.md",
        "jira/OPS-3.md",
    ]


def test_scope_filters(notes_vault):
    (notes_vault / "wiki" / "topics" / "캐시.md").write_text("# 캐시\n김철수 의견\n", encoding="utf-8")
    (notes_vault / "jira" / "KB-12.md").write_text("# KB-12 캐시\n김철수 담당\n", encoding="utf-8")
    (notes_vault / "jira" / "_views" / "today.md").write_text("| [[KB-12]] | 김철수 |\n", encoding="utf-8")
    assert {p for p, _, _ in _hits(notes_vault, "김철수", "inbox")} == {INBOX_NOTE}
    assert {p for p, _, _ in _hits(notes_vault, "김철수", "daily")} == {"daily/2026-11-04.md"}
    assert {p for p, _, _ in _hits(notes_vault, "김철수", "wiki")} == {"wiki/topics/캐시.md"}
    assert {p for p, _, _ in _hits(notes_vault, "김철수", "jira")} == {"jira/KB-12.md"}  # _views 제외


def test_skips_non_utf8_and_editor_temp_files(notes_vault):
    (notes_vault / "inbox" / "20261101-0000-옛 메모.md").write_bytes("김철수 cp949".encode("cp949"))
    (notes_vault / "daily" / ".#2026-11-05.md").write_text("김철수 잠금", encoding="utf-8")
    (notes_vault / "daily" / "#2026-11-05.md#").write_text("김철수 자동 저장", encoding="utf-8")
    (notes_vault / "wiki" / "people" / "김철수.md~").write_text("김철수 백업", encoding="utf-8")
    (notes_vault / "wiki" / ".obsidian").mkdir()
    (notes_vault / "wiki" / ".obsidian" / "x.md").write_text("김철수 숨김", encoding="utf-8")
    assert {p for p, _, _ in _hits(notes_vault, "김철수")} == {"daily/2026-11-04.md", INBOX_NOTE}


def test_search_with_non_utf8_source_writes_nothing(tmp_path, run_kb):
    """검색은 읽기 전용: UTF-8이 아닌 파일이 있어도 `.state/logs`를 만들지 않는다."""
    root = tmp_path / "읽기 전용 볼트"
    (root / "daily").mkdir(parents=True)
    shutil.copy(ROOT / "config.yaml", root / "config.yaml")
    (root / "daily" / "2026-11-04.md").write_bytes("- 김철수 UTF-8\n".encode("utf-8"))
    (root / "daily" / "2026-11-05.md").write_bytes("- 김철수 cp949\n".encode("cp949"))
    before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert _hits(root, "김철수") == [("daily/2026-11-04.md", 1, "- 김철수 UTF-8")]
    result = run_kb("search", "김철수", root=root)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["daily/2026-11-04.md:1: - 김철수 UTF-8"]
    assert result.stderr.splitlines() == ["건너뜀: daily/2026-11-05.md: UTF-8이 아니라 건너뜀"]  # D11: 이름을 알린다
    after = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert after == before
    assert not (root / ".state").exists()


def test_bom_file_first_line(vault):
    (vault / "inbox" / "bom.md").write_bytes(b"\xef\xbb\xbf" + "첫 줄 검색어\n둘째 줄\n".encode("utf-8"))
    assert _hits(vault, "첫") == [("inbox/bom.md", 1, "첫 줄 검색어")]


def test_nfc_nfd_equivalent(vault):
    import unicodedata

    (vault / "inbox" / "nfd.md").write_text(unicodedata.normalize("NFD", "- 한글 분해형\n"), encoding="utf-8")
    assert [p for p, _, _ in _hits(vault, "한글")] == ["inbox/nfd.md"]


def test_empty_vault_and_missing_folders(tmp_path):
    root = tmp_path / "빈 볼트"
    root.mkdir()
    shutil.copy(ROOT / "config.yaml", root / "config.yaml")
    assert _hits(root, "아무거나") == []


def test_empty_query_is_an_error(vault):
    from lib import core

    with pytest.raises(core.ConfigError):
        search.search(config.load(vault), "   ")
    with pytest.raises(core.ConfigError):
        search.main(["--root", str(vault)])


def test_fts_search_uses_cache_and_falls_back_when_broken(notes_vault):
    from lib import fts

    (notes_vault / "jira" / "KB-12.md").write_text("# KB-12 캐시\n", encoding="utf-8")
    cfg = config.load(notes_vault)
    # 캐시 없음 → 파일 스캔
    assert ("jira/KB-12.md", 1, "# KB-12 캐시") in _hits(notes_vault, "캐시", "jira")
    # 캐시 있음 → FTS(한국어 접두: 캐시 → 캐시를), 결과는 파일 단위(줄 1)
    fts.upsert(
        cfg,
        [
            {"key": "KB-12", "project": "KB", "summary": "캐시를 확정", "status": "In Progress", "status_category": "indeterminate", "assignee": "홍길동", "updated": "2026-11-04", "due": "", "body": "설명"},
            {"key": "OPS-3", "project": "OPS", "summary": "배포", "status": "Blocked", "status_category": "indeterminate", "assignee": "홍길동", "updated": "2026-11-03", "due": "", "body": "체크리스트"},
        ],
    )
    hits = search.fts_search(cfg, ["캐시"])
    assert hits is not None and [(h.path, h.line) for h in hits] == [("jira/KB-12.md", 1)]
    assert "KB-12" in hits[0].text and "캐시를 확정" in hits[0].text
    assert [h.path for h in search.fts_search(cfg, ["kb-12"])] == ["jira/KB-12.md"]
    assert search.fts_search(cfg, ["없는낱말"]) == []
    assert _hits(notes_vault, "캐시", "jira") == [("jira/KB-12.md", 1, hits[0].text)]  # 캐시가 있으면 파일을 훑지 않음
    # 손상된 캐시 → None → 파일 스캔
    (notes_vault / "jira" / ".cache.sqlite").write_bytes(b"garbage")
    assert search.fts_search(cfg, ["캐시"]) is None
    assert ("jira/KB-12.md", 1, "# KB-12 캐시") in _hits(notes_vault, "캐시", "jira")


def test_fts_hook_is_called_only_when_cache_exists(notes_vault, monkeypatch):
    (notes_vault / "jira" / "KB-12.md").write_text("# KB-12 캐시\n", encoding="utf-8")
    (notes_vault / "jira" / ".cache.sqlite").write_bytes(b"")

    calls = []

    def fake_fts(cfg, terms):
        calls.append(terms)
        return [search.Hit("jira/KB-12.md", 99, "FTS 결과")]

    monkeypatch.setattr(search, "fts_search", fake_fts)
    assert _hits(notes_vault, "캐시", "jira") == [("jira/KB-12.md", 99, "FTS 결과")]
    assert calls == [["캐시"]]
    (notes_vault / "jira" / ".cache.sqlite").unlink()
    assert _hits(notes_vault, "캐시", "jira") == [("jira/KB-12.md", 1, "# KB-12 캐시")]  # 캐시 없으면 안 부름
    assert len(calls) == 1


def test_main_text_output_and_limit(notes_vault, capsys):
    assert search.main(["--root", str(notes_vault), "OPS-3"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "daily/2026-11-05.md:13: - 09:30 [[OPS-3]] 막힘, @이영희 확인 대기"
    assert out[1:] == ["daily/2026-11-04.md:14: - 11:40 OPS-3 배포 체크리스트 검토", f"{INBOX_NOTE}:7: - OPS-3 배포 순서 합의"]

    assert search.main(["--root", str(notes_vault), "--limit", "1", "OPS-3"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("daily/2026-11-05.md:13: ")
    assert out[1] == "… 2개 더 있음 (전체 3개, --limit 0이면 전부)"
    assert len(out) == 2

    assert search.main(["--root", str(notes_vault), "--limit", "0", "OPS-3"]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 3


def test_bad_options_exit_4(vault, run_kb):
    for args in (["search", "--limit", "-1", "x"], ["search", "--in", "nowhere", "x"], ["search", "--limit", "abc", "x"], ["search"]):
        result = run_kb(*args)
        assert result.returncode == 4, (args, result.stderr)
        assert result.stderr.startswith("오류: ")


def test_cli_json(notes_vault, run_kb):
    result = run_kb("search", "김철수", "--json")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data[0] == {"path": "daily/2026-11-04.md", "line": 13, "text": "- 09:12 [[KB-12]] @김철수 와 front matter 논의. updated는 UTC로 저장하기로."}
    assert {"path": INBOX_NOTE, "line": 8, "text": "- @김철수 가 캐시 설계 검토"} in data
    assert all(set(item) == {"path", "line", "text"} for item in data)


def test_cli_no_hits_exit_0(notes_vault, run_kb):
    result = run_kb("search", "존재하지않는낱말")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "결과 없음"
    result = run_kb("search", "존재하지않는낱말", "--json")
    assert result.returncode == 0
    assert json.loads(result.stdout) == []


def test_cli_argv_stdin_and_cp949_console(notes_vault, run_kb):
    """Emacs 경로(--argv-stdin)와 UTF-8 모드 없는 cp949 콘솔에서도 한글이 깨지지 않는다."""
    result = run_kb("--argv-stdin", stdin=json.dumps(["search", "이영희 막힘"], ensure_ascii=False))
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["daily/2026-11-05.md:13: - 09:30 [[OPS-3]] 막힘, @이영희 확인 대기"]

    raw = run_kb("search", "회의", utf8=False)
    assert raw.returncode == 0, raw.stderr
    text = raw.stdout.decode("utf-8")  # 엄격한 해독
    assert f"{INBOX_NOTE}:3: title: 회의 메모" in text.splitlines()


def test_sample_vault(tmp_path):
    root = tmp_path / "샘플 볼트"
    shutil.copytree(SAMPLE, root)
    before = _tree(root)
    hits = _hits(root, "@김철수")
    days = {p for p, _, _ in hits}
    assert {"daily/2026-11-02.md", "daily/2026-11-03.md", "daily/2026-11-04.md", "daily/2026-11-05.md"} <= days
    assert "inbox/20261104-0910-캐시 설계 아이디어.md" in days
    # CRLF 파일
    assert ("daily/2026-11-03.md", 13, "- 09:30 스탠드업. [[OPS-3]] 막힘, @이영희 확인 대기") in _hits(root, "스탠드업")
    assert _tree(root) == before  # 검색은 볼트를 바꾸지 않는다(로그 폴더 제외)
