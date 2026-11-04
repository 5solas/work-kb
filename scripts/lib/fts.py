"""Jira 캐시 검색 색인 (`jira/.cache.sqlite`, Phase 2, D63).

- 표 `issues(key, project, summary, status, status_category, assignee, updated, due, body)`가 원본이다.
- FTS5(`unicode61`)를 쓸 수 있으면 가상 표 `issues_fts(key, summary, body)`를 같이 유지한다.
  못 쓰면(모듈 없음) `issues`만 두고 검색은 LIKE로 한다.
- 검색어는 낱말마다 `"낱말"*`로 인용한다(접두 검색: `캐시`가 `캐시를`을 찾는다). FTS 질의가
  OperationalError를 내거나 0건이면 LIKE(부분 일치)로 다시 찾는다.
- 다시 만들 수 있는 캐시다. 지워도 `kb.py sync --full`이 다시 채운다.

쓰기(upsert)는 호출자가 `core.kb_lock` 안에서 부른다. 읽기(search)는 잠금이 필요 없다.
"""
from __future__ import annotations

import sqlite3
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping

from lib import core

CACHE_PARTS = ("jira", ".cache.sqlite")
SCHEMA_VERSION = "1"
COLUMNS: tuple[str, ...] = ("key", "project", "summary", "status", "status_category", "assignee", "updated", "due", "body")
FTS_COLUMNS: tuple[str, ...] = ("key", "summary", "body")
LIKE_COLUMNS: tuple[str, ...] = ("key", "summary", "status", "assignee", "body")
FTS_MODULE = "fts5"  # 테스트가 없는 모듈 이름으로 바꿔 LIKE 경로를 확인한다
FTS_TOKENIZE = "unicode61"
BUSY_TIMEOUT = 10.0  # 초. 다른 연결이 잡고 있으면 이만큼 기다린 뒤 core.FileBusy(종료 6)


def _is_busy(exc: BaseException) -> bool:
    """sqlite가 잠김(다른 연결의 트랜잭션) 때문에 낸 오류인가."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    text = str(exc).lower()
    return "locked" in text or "busy" in text


def _busy_error(path: Path, exc: BaseException) -> core.FileBusy:
    return core.FileBusy(
        f"{path}: 캐시를 다른 프로그램이 쓰고 있습니다 ({exc}). 그 프로그램(DB 도구 등)을 닫고 다시 실행하세요"
    )


def cache_path(cfg: Any) -> Path:
    """설정(Config)의 볼트 기준 `jira/.cache.sqlite`."""
    return cfg.path(*CACHE_PARTS)


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def fts_query(terms: Iterable[str]) -> str:
    """낱말마다 `"낱말"*`(안의 큰따옴표는 두 번). 빈 낱말은 뺀다."""
    parts = []
    for term in terms:
        text = _fold(str(term)).strip()
        if text:
            parts.append('"' + text.replace('"', '""') + '"*')
    return " ".join(parts)


def _like_pattern(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class Cache:
    """sqlite 캐시 하나. `with Cache(path) as cache:`로 쓴다(Windows에서 파일을 잡고 있지 않게)."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = sqlite3.connect(str(self.path), timeout=BUSY_TIMEOUT)
        except sqlite3.Error as exc:
            if _is_busy(exc):
                raise _busy_error(self.path, exc) from exc
            raise core.ConfigError(f"{self.path}: 검색 캐시를 열지 못했습니다 ({exc})") from exc
        self._conn.row_factory = sqlite3.Row
        self.fts_enabled = False
        try:
            self._setup()
        except sqlite3.DatabaseError as exc:
            self._conn.close()
            if _is_busy(exc):
                raise _busy_error(self.path, exc) from exc
            raise core.ConfigError(
                f"{self.path}: 검색 캐시가 손상되었습니다 ({exc}). 이 파일을 _trash/로 옮기고 `kb.py sync --full`을 실행하세요"
            ) from exc

    # ------------------------------------------------------------ 수명

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Cache":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------ 준비

    def _meta(self, name: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE name = ?", (name,)).fetchone()
        return None if row is None else str(row[0])

    def _set_meta(self, name: str, value: str) -> None:
        self._conn.execute("INSERT OR REPLACE INTO meta(name, value) VALUES (?, ?)", (name, value))

    def _tables(self) -> set[str]:
        return {str(row[0]) for row in self._conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

    def _setup(self) -> None:
        """먼저 읽고, 표·형식이 이미 맞으면 아무것도 쓰지 않는다(검색만 하는 실행이 파일을 바꾸지 않게)."""
        conn = self._conn
        tables = self._tables()
        schema = self._meta("schema") if "meta" in tables else None
        if "meta" not in tables or "issues" not in tables or schema != SCHEMA_VERSION:
            with conn:
                conn.execute("CREATE TABLE IF NOT EXISTS meta(name TEXT PRIMARY KEY, value TEXT)")
                if schema not in (None, SCHEMA_VERSION):
                    # 다시 만들 수 있는 캐시라 형식이 바뀌면 비우고 새로 만든다
                    conn.execute("DROP TABLE IF EXISTS issues_fts")
                    conn.execute("DROP TABLE IF EXISTS issues")
                columns = ", ".join(f"{name} TEXT" if name != "key" else "key TEXT PRIMARY KEY" for name in COLUMNS)
                conn.execute(f"CREATE TABLE IF NOT EXISTS issues({columns})")
                self._set_meta("schema", SCHEMA_VERSION)
            tables = self._tables()
        try:
            if "issues_fts" not in tables:
                with conn:
                    conn.execute(
                        f"CREATE VIRTUAL TABLE IF NOT EXISTS issues_fts USING {FTS_MODULE}"
                        f"({', '.join(FTS_COLUMNS)}, tokenize='{FTS_TOKENIZE}')"
                    )
            conn.execute("SELECT count(*) FROM issues_fts").fetchone()
            self.fts_enabled = True
        except sqlite3.OperationalError as exc:
            if _is_busy(exc):
                raise
            self.fts_enabled = False  # FTS5 없음 → LIKE만
            return
        if self._meta("fts_synced") != "1":
            self._rebuild_fts()

    def _rebuild_fts(self) -> None:
        """FTS 없이 쓰인 적이 있으면 issues에서 색인을 다시 만든다."""
        cols = ", ".join(FTS_COLUMNS)
        with self._conn:
            self._conn.execute("DELETE FROM issues_fts")
            self._conn.execute(f"INSERT INTO issues_fts({cols}) SELECT {cols} FROM issues")
            self._set_meta("fts_synced", "1")

    # ------------------------------------------------------------ 쓰기

    def upsert(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """행을 넣거나 바꾼다(키 기준). 넣은 행 수."""
        items = []
        for row in rows:
            key = str(row.get("key") or "").strip()
            if not key:
                continue
            items.append(tuple(key if name == "key" else _fold(str(row.get(name) or "")) for name in COLUMNS))
        if not items:
            return 0
        placeholders = ", ".join("?" for _ in COLUMNS)
        fts_index = [COLUMNS.index(name) for name in FTS_COLUMNS]
        conn = self._conn
        try:
            with conn:
                conn.executemany(f"INSERT OR REPLACE INTO issues({', '.join(COLUMNS)}) VALUES ({placeholders})", items)
                if self.fts_enabled:
                    try:
                        conn.executemany("DELETE FROM issues_fts WHERE key = ?", [(item[0],) for item in items])
                        conn.executemany(
                            f"INSERT INTO issues_fts({', '.join(FTS_COLUMNS)}) VALUES ({', '.join('?' for _ in FTS_COLUMNS)})",
                            [tuple(item[i] for i in fts_index) for item in items],
                        )
                        self._set_meta("fts_synced", "1")
                    except sqlite3.OperationalError as exc:
                        if _is_busy(exc):
                            raise
                        self.fts_enabled = False
                if not self.fts_enabled:
                    self._set_meta("fts_synced", "0")  # FTS를 다시 쓸 수 있게 되면 처음 열 때 다시 만든다
        except sqlite3.OperationalError as exc:
            if _is_busy(exc):
                raise _busy_error(self.path, exc) from exc
            raise
        return len(items)

    def delete(self, keys: Iterable[str]) -> int:
        """키의 행을 지운다(범위 밖으로 나간 일감). 지운 행 수."""
        items = [(str(key).strip(),) for key in keys if str(key or "").strip()]
        if not items:
            return 0
        conn = self._conn
        try:
            with conn:
                before = conn.total_changes
                conn.executemany("DELETE FROM issues WHERE key = ?", items)
                removed = conn.total_changes - before
                if self.fts_enabled:
                    try:
                        conn.executemany("DELETE FROM issues_fts WHERE key = ?", items)
                    except sqlite3.OperationalError as exc:
                        if _is_busy(exc):
                            raise
                        self.fts_enabled = False
                        self._set_meta("fts_synced", "0")
        except sqlite3.OperationalError as exc:
            if _is_busy(exc):
                raise _busy_error(self.path, exc) from exc
            raise
        return removed

    # ------------------------------------------------------------ 읽기

    def count(self) -> int:
        return int(self._conn.execute("SELECT count(*) FROM issues").fetchone()[0])

    def get(self, key: str) -> dict | None:
        row = self._conn.execute(f"SELECT {', '.join(COLUMNS)} FROM issues WHERE key = ?", (key,)).fetchone()
        return None if row is None else dict(row)

    def _rows(self, cursor: sqlite3.Cursor, via: str) -> list[dict]:
        out = []
        for row in cursor.fetchall():
            item = {name: row[name] for name in COLUMNS}
            item["path"] = f"jira/{item['key']}.md"
            item["via"] = via
            out.append(item)
        return out

    def search_fts(self, terms: Iterable[str]) -> list[dict]:
        """FTS5만으로 찾는다. FTS가 없으면 OperationalError."""
        query = fts_query(terms)
        if not self.fts_enabled:
            raise sqlite3.OperationalError("no such module: fts5")
        cols = ", ".join(f"i.{name} AS {name}" for name in COLUMNS)
        cursor = self._conn.execute(
            f"SELECT {cols} FROM issues_fts JOIN issues AS i ON i.key = issues_fts.key "
            "WHERE issues_fts MATCH ? ORDER BY bm25(issues_fts), i.key",
            (query,),
        )
        return self._rows(cursor, "fts")

    def search_like(self, terms: Iterable[str]) -> list[dict]:
        """모든 낱말이 key·summary·status·assignee·body 중 어딘가에 들어 있는 행(ASCII는 대소문자 무시)."""
        clauses: list[str] = []
        params: list[str] = []
        for term in terms:
            text = _fold(str(term)).strip()
            if not text:
                continue
            pattern = _like_pattern(text)
            clauses.append("(" + " OR ".join(f"{name} LIKE ? ESCAPE '\\'" for name in LIKE_COLUMNS) + ")")
            params.extend(pattern for _ in LIKE_COLUMNS)
        if not clauses:
            return []
        cursor = self._conn.execute(
            f"SELECT {', '.join(COLUMNS)} FROM issues WHERE {' AND '.join(clauses)} ORDER BY updated DESC, key",
            params,
        )
        return self._rows(cursor, "like")

    def search(self, terms: Iterable[str]) -> list[dict]:
        """낱말 AND 검색. FTS5 → (OperationalError·0건이면) LIKE. 각 행에 path·via('fts'|'like')가 붙는다."""
        terms = [t for t in (_fold(str(x)).strip() for x in terms) if t]
        if not terms:
            return []
        if self.fts_enabled:
            try:
                hits = self.search_fts(terms)
            except sqlite3.OperationalError:
                hits = []
            if hits:
                return hits
        return self.search_like(terms)


# ---------------------------------------------------------------- 모듈 함수


def open_cache(target: Any) -> Cache:
    """경로 또는 Config로 캐시를 연다(없으면 만든다)."""
    path = target if isinstance(target, (str, Path)) else cache_path(target)
    return Cache(path)


def upsert(target: Any, rows: Iterable[Mapping[str, Any]]) -> int:
    with open_cache(target) as cache:
        return cache.upsert(rows)


def search(target: Any, terms: Iterable[str]) -> list[dict]:
    """경로·Config의 캐시에서 찾는다. 캐시 파일이 없으면 [](만들지 않는다)."""
    path = Path(target) if isinstance(target, (str, Path)) else cache_path(target)
    if not path.exists():
        return []
    with Cache(path) as cache:
        return cache.search(terms)
