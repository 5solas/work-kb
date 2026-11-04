"""검색 (`kb.py search "질의"`).

Phase 1: daily·inbox·wiki(+ 있으면 jira 캐시)를 순수 Python으로 훑는다(LLM·색인 없음).
- 질의는 공백으로 나눈 낱말들의 AND. 줄 단위, 대소문자 무시(casefold), 부분 일치. 유니코드는 NFC로 맞춘다.
- 출력: `경로:줄: 본문`(볼트 기준 상대 경로, `/` 구분). `--json`이면 [{path, line, text}].
- 순서: daily(최신 날짜 먼저) → inbox(최신 파일 먼저) → wiki(경로순) → jira(경로순).
- 결과가 없어도 종료 0("결과 없음"). 빈 질의는 종료 4.
- `jira/.cache.sqlite`가 있으면 `fts_search`를 먼저 부른다. Phase 2가 FTS5로 채운다(D63).
"""
from __future__ import annotations

import json
import sys
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator

from lib import config, core, notes
from lib.config import Config

SCOPES = ("daily", "inbox", "wiki", "jira")
DEFAULT_LIMIT = 100
FTS_CACHE = ("jira", ".cache.sqlite")


@dataclass(frozen=True)
class Hit:
    path: str  # 볼트 기준 상대 경로, `/` 구분
    line: int  # 1부터
    text: str  # 줄 끝 문자 없음


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def parse_terms(query: str) -> list[str]:
    """질의를 AND 낱말 목록으로. 비교용으로 NFC·casefold 하고 중복을 뺀다."""
    terms: list[str] = []
    for word in _fold(query).split():
        if word not in terms:
            terms.append(word)
    return terms


def matches(text: str, terms: list[str]) -> bool:
    folded = _fold(text)
    return all(term in folded for term in terms)


# ---------------------------------------------------------------- 파일 고르기


def _readable(path: Path) -> bytes | None:
    """UTF-8로 읽히는 파일의 바이트. 아니면 None(건너뜀)."""
    if not core.is_scannable(path) or not path.is_file():
        return None
    try:
        data = path.read_bytes()
        notes.decode(data)
    except (OSError, UnicodeDecodeError):
        return None
    return data


def _hidden(path: Path, base: Path) -> bool:
    return any(part.startswith(".") for part in path.relative_to(base).parts)


def iter_files(
    cfg: Config, scope: str, *, skipped: list[tuple[str, str]] | None = None
) -> Iterator[tuple[Path, bytes]]:
    """한 범위(daily·inbox·wiki·jira)의 (경로, 바이트). 읽기 전용이라 로그도 남기지 않는다.

    UTF-8이 아니라 건너뛴 daily·inbox 파일은 skipped에 (경로, 이유)로 더한다.
    """
    if scope in ("daily", "inbox"):
        bad: list[tuple[str, str]] = []
        sources = notes.iter_sources(cfg, skipped=bad, log=False)
        found = [(p, d) for p, d in sources if notes.rel_path(cfg, p).split("/", 1)[0] == scope]
        if skipped is not None:
            skipped.extend(item for item in bad if item[0].split("/", 1)[0] == scope)
        # 이름이 날짜(daily)·날짜시각(inbox)으로 시작하므로 역순 = 최신 먼저
        yield from sorted(found, key=lambda item: item[0].name, reverse=True)
        return
    base = cfg.path(scope)
    if not base.is_dir():
        return
    paths = base.rglob("*.md") if scope == "wiki" else base.glob("*.md")
    for path in sorted(paths):
        if _hidden(path, base):
            continue
        data = _readable(path)
        if data is not None:
            yield path, data


def scan_file(rel: str, data: bytes, terms: list[str]) -> Iterator[Hit]:
    # 줄 번호는 편집기(Emacs grep 버퍼)와 맞게 `\n`만으로 센다. str.splitlines는 \x0c· 에서도 끊는다.
    for lineno, raw in enumerate(notes.decode(data).split("\n"), start=1):
        line = raw.rstrip("\r")
        if matches(line, terms):
            yield Hit(rel, lineno, line)


# ---------------------------------------------------------------- FTS (Phase 2)


def fts_search(cfg: Config, terms: list[str]) -> list[Hit] | None:
    """`jira/.cache.sqlite`의 FTS5 검색(D63: 낱말마다 `"term"*`, OperationalError·0건이면 LIKE).

    캐시를 열지 못하면 None을 돌려주고, 호출자가 jira/*.md를 직접 훑는다.
    결과 줄 번호는 1(파일 단위 결과)이고 본문은 `키 · 상태 · 제목`이다.
    """
    from lib import fts

    try:
        rows = fts.search(cfg, terms)
    except Exception:  # 캐시 손상·스키마 불일치 등 → 파일 스캔으로
        return None
    hits: list[Hit] = []
    for row in rows:
        summary = " · ".join(str(row.get(name) or "") for name in ("key", "status", "summary") if row.get(name))
        hits.append(Hit(str(row.get("path") or f"jira/{row.get('key')}.md"), 1, summary))
    return hits


# ---------------------------------------------------------------- 검색


def search(
    cfg: Config, query: str, *, scope: str = "all", skipped: list[tuple[str, str]] | None = None
) -> list[Hit]:
    """모든 결과(개수 제한 없음). 빈 질의는 ConfigError. 건너뛴 파일은 skipped에 더한다."""
    terms = parse_terms(query)
    if not terms:
        raise core.ConfigError('검색어를 입력하세요. 예: kb.py search "캐시 KB-12"')
    if scope != "all" and scope not in SCOPES:
        raise core.ConfigError(f"--in: {scope} (가능: {', '.join(SCOPES)}, all)")
    scopes = SCOPES if scope == "all" else (scope,)
    hits: list[Hit] = []
    for name in scopes:
        if name == "jira" and cfg.path(*FTS_CACHE).exists():
            fts = fts_search(cfg, terms)
            if fts is not None:
                hits.extend(fts)
                continue
        for path, data in iter_files(cfg, name, skipped=skipped):
            hits.extend(scan_file(notes.rel_path(cfg, path), data, terms))
    return hits


def main(argv: list[str]) -> int:
    parser = core.make_parser("kb.py search", "daily·inbox·wiki·jira 캐시에서 낱말을 찾는다 (공백은 AND, 대소문자 무시)")
    parser.add_argument("query", nargs="*", help="검색어. 공백으로 나눈 낱말이 한 줄에 모두 있어야 한다")
    parser.add_argument("--json", action="store_true", help="[{path, line, text}] JSON으로 출력")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"최대 결과 수 (기본 {DEFAULT_LIMIT}, 0이면 전부)")
    parser.add_argument("--in", dest="scope", choices=(*SCOPES, "all"), default="all", help="찾을 곳 (기본 all)")
    args = parser.parse_args(argv)
    if args.limit < 0:
        raise core.ConfigError("--limit은 0 이상이어야 합니다 (0이면 전부)")
    query = " ".join(args.query)
    if not parse_terms(query):
        raise core.ConfigError('검색어를 입력하세요. 예: kb.py search "캐시 KB-12"')
    cfg = config.load(args.root)
    skipped: list[tuple[str, str]] = []
    hits = search(cfg, query, scope=args.scope, skipped=skipped)
    for rel, reason in skipped:
        print(f"건너뜀: {rel}: {reason}", file=sys.stderr)
    shown = hits[: args.limit] if args.limit else hits
    if args.json:
        print(json.dumps([asdict(h) for h in shown], ensure_ascii=False, indent=1))
        return 0
    if not hits:
        print("결과 없음")
        return 0
    for hit in shown:
        print(f"{hit.path}:{hit.line}: {hit.text}")
    if len(shown) < len(hits):
        print(f"… {len(hits) - len(shown)}개 더 있음 (전체 {len(hits)}개, --limit 0이면 전부)")
    return 0


if __name__ == "__main__":
    core.run_main(main)
