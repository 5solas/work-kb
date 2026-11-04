"""lib/fts.py: sqlite 캐시 + FTS5 (D63). 한국어 접두, 키 검색, LIKE 대안, 특수 입력."""
from __future__ import annotations

import sqlite3

import pytest

from lib import core, fts

ROWS = [
    {"key": "KB-12", "project": "KB", "summary": "캐시 front matter 확정", "status": "In Progress", "status_category": "indeterminate",
     "assignee": "홍길동", "updated": "2026-11-04T16:05:11+09:00", "due": "2026-11-06",
     "body": "jira 캐시를 정리한다. @김철수 와 논의. 설정 foo.bar 값"},
    {"key": "KB-120", "project": "KB", "summary": "다른 일감", "status": "To Do", "status_category": "new",
     "assignee": "김철수", "updated": "2026-11-01T09:00:00+09:00", "due": "", "body": "KB-12 후속 작업"},
    {"key": "OPS-2", "project": "OPS", "summary": "백업 스토리지 용량 경고", "status": "In Progress", "status_category": "indeterminate",
     "assignee": "홍길동", "updated": "2026-11-04T11:22:33+09:00", "due": "2026-11-05", "body": "디스크 AND 용량 \"따옴표\" 100% 확인"},
]  # fmt: skip


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "한글 폴더" / "jira" / ".cache.sqlite"
    with fts.Cache(path) as cache:
        assert cache.fts_enabled
        assert cache.upsert(ROWS) == 3
    return path


def keys(hits):
    return [h["key"] for h in hits]


def test_korean_prefix_finds_inflected_word(db):
    hits = fts.search(db, ["캐시"])
    assert keys(hits) == ["KB-12"]  # 본문의 '캐시를'도 접두로 찾는다
    assert hits[0]["via"] == "fts"
    assert hits[0]["path"] == "jira/KB-12.md"
    assert hits[0]["summary"] == "캐시 front matter 확정"
    assert keys(fts.search(db, ["캐시를"])) == ["KB-12"]


def test_key_query(db):
    hits = fts.search(db, ["KB-12"])
    assert "KB-12" in keys(hits) and hits[0]["via"] == "fts"
    assert set(keys(fts.search(db, ["kb-12"]))) == set(keys(hits))
    assert keys(fts.search(db, ["OPS-2"])) == ["OPS-2"]


def test_terms_are_anded(db):
    assert keys(fts.search(db, ["캐시", "백업"])) == []
    hits = fts.search(db, ["캐시", "홍길동"])  # 담당은 FTS 열이 아님 → 0건 → LIKE가 열을 가로질러 찾는다
    assert keys(hits) == ["KB-12"] and hits[0]["via"] == "like"
    assert keys(fts.search(db, ["백업", "용량"])) == ["OPS-2"]


def test_zero_fts_hits_fall_back_to_like(db):
    hits = fts.search(db, ["시를"])  # 낱말 가운데 → FTS 0건 → LIKE
    assert keys(hits) == ["KB-12"]
    assert hits[0]["via"] == "like"
    assert keys(fts.search(db, ["없는낱말"])) == []


def test_like_fallback_when_fts5_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(fts, "FTS_MODULE", "no_such_fts_module")
    path = tmp_path / "jira" / ".cache.sqlite"
    with fts.Cache(path) as cache:
        assert not cache.fts_enabled
        cache.upsert(ROWS)
        hits = cache.search(["캐시"])
    assert keys(hits) == ["KB-12"] and hits[0]["via"] == "like"
    assert keys(fts.search(path, ["홍길동"])) == ["KB-12", "OPS-2"]  # 갱신 내림차순

    # FTS5를 다시 쓸 수 있게 되면 처음 열 때 색인을 다시 만든다
    monkeypatch.setattr(fts, "FTS_MODULE", "fts5")
    with fts.Cache(path) as cache:
        assert cache.fts_enabled
        hits = cache.search(["캐시"])
    assert keys(hits) == ["KB-12"] and hits[0]["via"] == "fts"


def test_operational_error_falls_back_to_like(db):
    with fts.Cache(db) as cache:
        cache._conn.execute("DROP TABLE issues_fts")
        hits = cache.search(["캐시"])
    assert keys(hits) == ["KB-12"] and hits[0]["via"] == "like"


@pytest.mark.parametrize(
    "terms, expected",
    [
        (["@김철수"], ["KB-12"]),
        (["foo.bar"], ["KB-12"]),
        (["AND"], ["OPS-2"]),
        (['"따옴표"'], ["OPS-2"]),
        (['"'], ["OPS-2"]),
        (["'"], []),
        (["100%"], ["OPS-2"]),
        (["_"], []),
        (["NOT", "OR"], []),
        (["*"], []),
        ([], []),
        (["", "  "], []),
    ],
)
def test_special_terms_do_not_raise(db, terms, expected):
    assert keys(fts.search(db, terms)) == expected


def test_fts_query_quotes_each_term():
    assert fts.fts_query(['a"b', "KB-12", " ", "캐시"]) == '"a""b"* "KB-12"* "캐시"*'


def test_upsert_replaces_row(db):
    with fts.Cache(db) as cache:
        cache.upsert([{**ROWS[0], "summary": "새 제목", "body": "새 본문"}])
        assert cache.count() == 3
        assert cache.get("KB-12")["summary"] == "새 제목"
        assert keys(cache.search(["캐시를"])) == []
        assert keys(cache.search(["새"])) == ["KB-12"]
        assert cache.upsert([{"key": ""}, {"summary": "키 없음"}]) == 0


def test_delete_removes_row_and_index(db):
    with fts.Cache(db) as cache:
        assert cache.delete(["KB-12", "없음-1", ""]) == 1
        assert cache.get("KB-12") is None and cache.count() == 2
        assert keys(cache.search(["캐시"])) == []  # FTS 색인에서도 빠졌다(LIKE로도 없음)
        assert cache.delete([]) == 0


def test_search_missing_file_creates_nothing(tmp_path):
    path = tmp_path / "jira" / ".cache.sqlite"
    assert fts.search(path, ["캐시"]) == []
    assert not path.exists()


def test_corrupt_file_is_config_error(tmp_path):
    path = tmp_path / ".cache.sqlite"
    path.write_bytes(b"this is not a sqlite database at all" * 100)
    with pytest.raises(core.ConfigError) as exc:
        fts.Cache(path)
    assert exc.value.exit_code == 4
    assert "--full" in str(exc.value)


def test_locked_cache_is_file_busy_not_corruption(db, monkeypatch):
    monkeypatch.setattr(fts, "BUSY_TIMEOUT", 0.2)
    other = sqlite3.connect(str(db), timeout=0, isolation_level=None)
    try:
        other.execute("BEGIN EXCLUSIVE")  # 다른 프로그램이 쓰기 트랜잭션을 잡고 있다
        with pytest.raises(core.FileBusy) as exc:
            fts.Cache(db)
        assert exc.value.exit_code == 6
        assert "캐시를 다른 프로그램이 쓰고 있습니다" in str(exc.value)
        assert "손상" not in str(exc.value) and "_trash" not in str(exc.value)
        other.execute("ROLLBACK")
        # 열린 뒤 쓰기(upsert)에서 잠겨도 같은 오류
        with fts.Cache(db) as cache:
            other.execute("BEGIN EXCLUSIVE")
            with pytest.raises(core.FileBusy):
                cache.upsert(ROWS[:1])
            other.execute("ROLLBACK")
            assert cache.get("KB-12")["summary"] == ROWS[0]["summary"]
    finally:
        other.close()


def test_opening_existing_cache_does_not_write(db):
    before = (db.read_bytes(), db.stat().st_mtime_ns)
    for _ in range(2):
        with fts.Cache(db) as cache:
            assert cache.fts_enabled and cache.count() == 3
    assert keys(fts.search(db, ["캐시"])) == ["KB-12"]
    assert (db.read_bytes(), db.stat().st_mtime_ns) == before


def test_schema_change_recreates(db):
    with sqlite3.connect(str(db)) as conn:
        conn.execute("UPDATE meta SET value = '0' WHERE name = 'schema'")
    conn.close()
    with fts.Cache(db) as cache:
        assert cache.count() == 0
        cache.upsert(ROWS[:1])
        assert keys(cache.search(["캐시"])) == ["KB-12"]
