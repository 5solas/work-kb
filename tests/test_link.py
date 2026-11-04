"""link.py (W1.1): 언급 구역, stub, 키 정규화, 원장 캐시, 동시 편집."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

import link
from lib import config, core, frontmatter, managed

NOW = "2026-11-05T09:30:00"


def _setup(vault: Path, keys=("KB", "OPS"), **extra) -> config.Config:
    data = {"environment": "home", "jira": {"project_keys": list(keys)}}
    data.update(extra)
    config.save_local(vault, data)
    return config.load(vault)


def _write(vault: Path, rel: str, text: str, *, crlf: bool = False) -> Path:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    if crlf:
        data = data.replace(b"\n", b"\r\n")
    path.write_bytes(data)
    return path


def _snapshot(vault: Path) -> dict[str, tuple[bytes, int]]:
    out = {}
    for path in sorted(vault.rglob("*")):
        rel = path.relative_to(vault).as_posix()
        if not path.is_file() or rel.startswith(".state/logs/") or rel == ".state/kb.lock":
            continue
        out[rel] = (path.read_bytes(), path.stat().st_mtime_ns)
    return out


def _region(path: Path, name: str = "mentions") -> list[str]:
    body = managed.read_region(path.read_bytes(), name)
    assert body is not None, f"{path}: 구역 {name} 없음"
    return body.decode("utf-8").splitlines()


DAY4 = """---
date: 2026-11-04
---
## 기록
- 16:05 KB-12 댓글 올림
- 09:12 [[KB-12]] @김철수 와 front matter 논의
"""

DAY5 = """---
date: 2026-11-05
---
## 오늘
<!-- kb:today:start -->
- [ ] [[KB-55]] 오늘 구역의 키 · 진행중
<!-- kb:today:end -->

## 이월
<!-- kb:carryover:start -->
- [ ] 개인: 경비 정산 @박이월 (← 2026-11-04)
<!-- kb:carryover:end -->

## 기록
- [ ] KB-12 확인하기
- 09:12 [[KB-12]] @김철수 와 front matter 논의
- 11:40 OPS-3 배포 @이영희 kim@example.com 메일
"""

INBOX = """---
created: 2026-11-03T10:00:00+09:00
title: 메모
---
KB-12 메모 내용
"""


@pytest.fixture
def basic(vault, fake_now):
    fake_now(NOW)
    cfg = _setup(vault)
    _write(vault, "daily/2026-11-04.md", DAY4)
    _write(vault, "daily/2026-11-05.md", DAY5)
    _write(vault, "inbox/20261103-1000-회의 메모.md", INBOX)
    return cfg


# ---------------------------------------------------------------- 기본 흐름


def test_mentions_sorted_deduplicated_and_normalised(basic, vault):
    summary = link.run(basic)
    assert summary["files_scanned"] == 3
    assert summary["skipped_files"] == []
    assert _region(vault / "jira" / "KB-12.md") == [
        "- [[inbox/20261103-1000-회의 메모]] KB-12 메모 내용",
        "- [[2026-11-04]] 09:12 [[KB-12]] @김철수 와 front matter 논의",
        "- [[2026-11-04]] 16:05 [[KB-12]] 댓글 올림",
        "- [[2026-11-05]] [[KB-12]] 확인하기",
    ]  # 11-05의 같은 09:12 줄은 줄 해시로 중복 제거, 시각 없는 줄은 시각 생략
    assert _region(vault / "wiki" / "projects" / "OPS.md") == [
        "- [[2026-11-05]] 11:40 [[OPS-3]] 배포 @이영희 kim@example.com 메일"
    ]
    kb_project = _region(vault / "wiki" / "projects" / "KB.md")
    assert len(kb_project) == 4
    assert _region(vault / "wiki" / "people" / "김철수.md") == [
        "- [[2026-11-04]] 09:12 [[KB-12]] @김철수 와 front matter 논의"
    ]
    # daily `## 기록`만 정규화, inbox는 그대로
    day4 = (vault / "daily" / "2026-11-04.md").read_text(encoding="utf-8")
    assert "- 16:05 [[KB-12]] 댓글 올림" in day4
    assert (vault / "inbox" / "20261103-1000-회의 메모.md").read_text(encoding="utf-8") == INBOX
    assert summary["normalized_lines"] == 3  # 11-04 한 줄, 11-05 두 줄


def test_today_and_carryover_regions_are_not_sources(basic, vault):
    link.run(basic)
    assert not (vault / "jira" / "KB-55.md").exists()
    assert not (vault / "wiki" / "people" / "박이월.md").exists()
    day5 = (vault / "daily" / "2026-11-05.md").read_text(encoding="utf-8")
    assert "- [ ] [[KB-55]] 오늘 구역의 키 · 진행중" in day5


def test_second_run_is_noop_byte_identical(basic, vault):
    first = link.run(basic)
    assert first["written"]
    before = _snapshot(vault)
    second = link.run(basic)
    assert second["written"] == []
    assert second["pages_created"] == 0 and second["normalized_lines"] == 0 and second["pages_updated"] == 0
    assert second["files_parsed"] == 0  # sha256이 같아 모두 캐시에서
    assert _snapshot(vault) == before  # 바이트와 수정 시각까지 같다


def test_stub_and_entity_front_matter(basic, vault):
    link.run(basic)
    stub = frontmatter.parse((vault / "jira" / "KB-12.md").read_bytes())
    assert stub["key"] == "KB-12" and stub["project"] == "KB" and stub["stub"] == "true"
    project = frontmatter.parse((vault / "wiki" / "projects" / "KB.md").read_bytes())
    assert project["type"] == "project" and project["key"] == "KB" and project["updated"] == "2026-11-05"
    person = frontmatter.parse((vault / "wiki" / "people" / "김철수.md").read_bytes())
    assert person["type"] == "person" and person["name"] == "김철수"
    # 다른 구역(issues, summary)은 템플릿 그대로 비어 있다
    data = (vault / "wiki" / "projects" / "KB.md").read_bytes()
    assert managed.read_region(data, "issues") == b"" and managed.read_region(data, "summary") == b""


def test_person_pages_only_for_at_names(vault, fake_now):
    fake_now(NOW)
    cfg = _setup(vault)
    _write(vault, "daily/2026-11-05.md", "## 기록\n- 09:00 김철수와 회의 @이영희 참석, 메일 kim@example.com\n- 09:10 `@코드` 와 https://x.invalid/@링크\n")
    link.run(cfg)
    people = sorted(p.name for p in (vault / "wiki" / "people").glob("*.md"))
    assert people == ["이영희.md"]


def test_code_url_front_matter_and_wikilink_interiors_are_not_mentions(vault, fake_now):
    fake_now(NOW)
    cfg = _setup(vault)
    text = (
        "---\ndate: 2026-11-05\ntitle: KB-90 front matter\n---\n"
        "## 기록\n"
        "```\nKB-99 코드 블록\n```\n"
        "- 09:00 인라인 `KB-88` 과 https://jira.example.invalid/browse/KB-77 링크\n"
        "- 09:10 ![[raw/assets/KB-66-shot.png]] 그림과 [[KB-65-설계]] 문서\n"
        "- 09:20 진짜 언급 KB-1\n"
    )
    _write(vault, "daily/2026-11-05.md", text)
    link.run(cfg)
    assert sorted(p.name for p in (vault / "jira").glob("*.md")) == ["KB-1.md"]


def test_index_region_counts_and_date_range(basic, vault):
    link.run(basic)
    lines = _region(vault / "wiki" / "index.md", "index")
    assert lines[0] == "- 기간: 2026-11-04 ~ 2026-11-05 · daily 2개 · inbox 1개"
    assert "- [[projects/KB|KB]] · 일감 1 · 언급 4" in lines
    assert "- [[projects/OPS|OPS]] · 일감 1 · 언급 1" in lines
    assert "- [[people/김철수|김철수]] · 언급 1" in lines
    assert "- [[people/이영희|이영희]] · 언급 1" in lines


def test_entry_text_truncation_embed_and_missing_time(vault, fake_now):
    fake_now(NOW)
    cfg = _setup(vault)
    long_line = "- 09:00 KB-5 " + "가" * 100 + " [[아주 긴 문서 이름]] 끝"
    _write(vault, "daily/2026-11-05.md", f"## 기록\n{long_line}\n- ![[raw/assets/x.png]] KB-5 그림\n")
    link.run(cfg)
    lines = _region(vault / "jira" / "KB-5.md")
    first = lines[0]
    assert first.startswith("- [[2026-11-05]] 09:00 [[KB-5]] 가가")
    assert first.endswith("…") and "[[아주" not in first  # 열린 [[ 앞에서 잘림
    body = first[len("- [[2026-11-05]] 09:00 "):]
    assert len(body) <= link.MAX_TEXT + 1
    # 시각 없는 줄은 시각을 생략하고(정렬은 앞 줄 시각 뒤), 임베드의 !를 뗀다
    assert lines[1] == "- [[2026-11-05]] [[raw/assets/x.png]] [[KB-5]] 그림"


# ---------------------------------------------------------------- 정규화


NORM_CASES = [
    ("- 09:00 맨 글자 KB-12 확인", "- 09:00 맨 글자 [[KB-12]] 확인"),
    ("- 09:00 이미 [[KB-12]] 링크", "- 09:00 이미 [[KB-12]] 링크"),
    ("- 09:00 별칭 [[KB-12|캐시]] 링크", "- 09:00 별칭 [[KB-12|캐시]] 링크"),
    ("- 09:00 인라인 `KB-12` 코드", "- 09:00 인라인 `KB-12` 코드"),
    ("- 09:00 주소 https://jira.example.invalid/browse/KB-12 참고", "- 09:00 주소 https://jira.example.invalid/browse/KB-12 참고"),
    ("- 09:00 위키링크 [[KB-12-설계]] 내부", "- 09:00 위키링크 [[KB-12-설계]] 내부"),
    ("- 09:00 파일 KB-12.md 과 경로 docs/KB-12 와 태그 #KB-12", "- 09:00 파일 KB-12.md 과 경로 docs/KB-12 와 태그 #KB-12"),
    ("- 09:00 조사 KB-12에서 OPS-3, 끝.", "- 09:00 조사 [[KB-12]]에서 [[OPS-3]], 끝."),
    ("- 09:00 모르는 키 ABC-1 과 XKB-1", "- 09:00 모르는 키 ABC-1 과 XKB-1"),
    ("- 09:00 두 번 KB-1 KB-1", "- 09:00 두 번 [[KB-1]] [[KB-1]]"),
]


@pytest.mark.parametrize(("before", "after"), NORM_CASES)
def test_normalize_text_cases(before, after):
    assert link.normalize_text(before, ["KB", "OPS"])[0] == after
    # 멱등
    assert link.normalize_text(after, ["KB", "OPS"]) == (after, 0)


def test_normalisation_only_in_record_section_bytes(vault, fake_now):
    fake_now(NOW)
    cfg = _setup(vault)
    text = (
        "---\ndate: 2026-11-05\nref: KB-1\n---\n"
        "## 메모\n- KB-2 기록 밖\n\n"
        "## 기록\n"
        "- 09:00 KB-3 안\n"
        "```\nKB-4 코드\n```\n"
        "<!-- kb:extracted:start -->\n- KB-6 구역 안\n<!-- kb:extracted:end -->\n"
        "- 10:00 끝 KB-5\n"
    )
    path = _write(vault, "daily/2026-11-05.md", text)
    summary = link.run(cfg)
    expected = text.replace("- 09:00 KB-3 안", "- 09:00 [[KB-3]] 안").replace("- 10:00 끝 KB-5", "- 10:00 끝 [[KB-5]]")
    assert path.read_bytes() == expected.encode("utf-8")
    assert summary["normalized_lines"] == 2


def test_normalisation_preserves_crlf_and_bom(vault, fake_now):
    fake_now(NOW)
    cfg = _setup(vault)
    text = "---\ndate: 2026-11-05\n---\n## 기록\n- 09:00 KB-12 한글 @김철수\n- 09:10 그냥 줄\n"
    path = vault / "daily" / "2026-11-05.md"
    path.write_bytes(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))
    link.run(cfg)
    data = path.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")
    assert data.count(b"\r\n") == data.count(b"\n") == 6
    assert "- 09:00 [[KB-12]] 한글 @김철수\r\n".encode("utf-8") in data
    assert _region(vault / "jira" / "KB-12.md") == ["- [[2026-11-05]] 09:00 [[KB-12]] 한글 @김철수"]
    assert b"\r\n" not in (vault / "jira" / "KB-12.md").read_bytes()  # 새 페이지는 LF
    assert link.run(cfg)["written"] == []


def test_normalize_keys_false_leaves_daily_untouched(vault, fake_now):
    fake_now(NOW)
    cfg = _setup(vault, normalize_keys=False)
    path = _write(vault, "daily/2026-11-05.md", "## 기록\n- 09:00 KB-12 그대로\n")
    before = path.read_bytes()
    link.run(cfg)
    assert path.read_bytes() == before
    assert _region(vault / "jira" / "KB-12.md") == ["- [[2026-11-05]] 09:00 KB-12 그대로"]


def test_human_edit_mid_run_is_skipped_and_reported(basic, vault, monkeypatch):
    target = vault / "daily" / "2026-11-05.md"
    human = (DAY5 + "- 12:00 사람이 방금 쓴 줄 KB-40\n").encode("utf-8")
    original = core.atomic_write

    def racing(path, data, **kwargs):
        if Path(path).resolve() == target.resolve() and kwargs.get("expect_sha256"):
            target.write_bytes(human)  # 읽은 뒤, 쓰기 직전에 사람이 저장
        return original(path, data, **kwargs)

    monkeypatch.setattr(core, "atomic_write", racing)
    summary = link.run(basic)
    assert summary["skipped_files"] == ["daily/2026-11-05.md"]
    assert any("daily/2026-11-05.md" in w for w in summary["warnings"])
    assert target.read_bytes() == human  # 사람 편집이 이긴다
    assert (vault / "jira" / "KB-12.md").exists()  # 나머지는 진행
    assert (vault / "daily" / "2026-11-04.md").read_text(encoding="utf-8").count("[[KB-12]]") == 2

    monkeypatch.setattr(core, "atomic_write", original)
    again = link.run(basic)
    assert again["skipped_files"] == []
    assert "- 12:00 사람이 방금 쓴 줄 [[KB-40]]" in target.read_text(encoding="utf-8")
    assert (vault / "jira" / "KB-40.md").exists()
    assert link.run(basic)["written"] == []


# ---------------------------------------------------------------- 사이드카·구역


def test_extracted_region_in_inbox_sidecar_is_matched_not_normalised(vault, fake_now):
    fake_now(NOW)
    cfg = _setup(vault)
    sidecar = (
        "---\nsource_image: raw/assets/2026-11-05-1420-whiteboard.png\ncreated: 2026-11-05T14:20:00+09:00\n---\n"
        "# whiteboard\n\n"
        "<!-- kb:extracted:start -->\n"
        "- [[KB-12]] 화이트보드 @김철수 결정\n"
        "- KB-13 맨 글자\n"
        "<!-- kb:extracted:end -->\n"
        "<!-- kb:summary:start -->\n- KB-14 다른 구역\n<!-- kb:summary:end -->\n"
    )
    path = _write(vault, "inbox/whiteboard.md", sidecar)
    link.run(cfg)
    assert path.read_text(encoding="utf-8") == sidecar
    assert _region(vault / "jira" / "KB-12.md") == ["- [[inbox/whiteboard]] [[KB-12]] 화이트보드 @김철수 결정"]
    assert _region(vault / "jira" / "KB-13.md") == ["- [[inbox/whiteboard]] KB-13 맨 글자"]
    assert not (vault / "jira" / "KB-14.md").exists()
    assert (vault / "wiki" / "people" / "김철수.md").exists()
    assert link.run(cfg)["written"] == []


def test_region_is_pure_function_of_sources(basic, vault):
    link.run(basic)
    (vault / "daily" / "2026-11-05.md").unlink()
    link.run(basic)
    assert _region(vault / "wiki" / "projects" / "OPS.md") == []
    assert _region(vault / "jira" / "OPS-3.md") == []  # 페이지는 남고 구역만 빈다
    # 사람이 구역 밖에 쓴 글은 지켜진다
    page = vault / "jira" / "KB-12.md"
    page.write_bytes(page.read_bytes() + "\n사람 메모\n".encode("utf-8"))
    _write(vault, "daily/2026-11-06.md", "## 기록\n- 08:00 KB-12 새 줄\n")
    link.run(basic)
    data = page.read_text(encoding="utf-8")
    assert data.endswith("\n사람 메모\n")
    assert "- [[2026-11-06]] 08:00 [[KB-12]] 새 줄" in _region(page)


def test_corrupt_or_stale_cache_does_not_change_result(basic, vault):
    link.run(basic)
    reference = _snapshot(vault)
    (vault / ".state" / "links_cache.json").write_text("{not json", encoding="utf-8")
    summary = link.run(basic)
    assert summary["files_parsed"] == 3
    after = _snapshot(vault)
    assert {k: v[0] for k, v in after.items()} == {k: v[0] for k, v in reference.items()}


def test_malformed_page_markers_are_skipped_and_reported(basic, vault):
    _write(vault, "jira/KB-12.md", "# KB-12\n<!-- kb:mentions:start -->\n- 손상\n")
    before = (vault / "jira" / "KB-12.md").read_bytes()
    summary = link.run(basic)
    assert "jira/KB-12.md" in summary["skipped_files"]
    assert (vault / "jira" / "KB-12.md").read_bytes() == before
    assert (vault / "wiki" / "projects" / "KB.md").exists()


def test_ledger_shape(basic, vault):
    link.run(basic)
    ledger = core.load_state(vault, "links")
    day5 = ledger["daily/2026-11-05.md"]
    assert all(isinstance(v, list) for v in day5.values())
    line_hash = core.line_hash("- 11:40 [[OPS-3]] 배포 @이영희 kim@example.com 메일")
    assert day5[line_hash] == ["jira/OPS-3.md", "wiki/projects/OPS.md", "wiki/people/이영희.md"]


# ---------------------------------------------------------------- 옵션


def test_dry_run_writes_nothing(basic, vault):
    before = _snapshot(vault)
    summary = link.run(basic, dry_run=True)
    assert summary["dry_run"] and "jira/KB-12.md" in summary["written"] and "daily/2026-11-04.md" in summary["written"]
    assert _snapshot(vault) == before


def test_rebuild_equals_fresh_run(basic, vault, tmp_path):
    fresh = tmp_path / "fresh"
    shutil.copytree(vault, fresh)
    link.run(basic)
    # 원천을 바꾸고 캐시가 있는 상태에서 --rebuild
    _write(vault, "daily/2026-11-06.md", "## 기록\n- 08:00 OPS-9 추가\n")
    _write(fresh, "daily/2026-11-06.md", "## 기록\n- 08:00 OPS-9 추가\n")
    link.run(basic, rebuild=True)
    link.run(config.load(fresh))
    cache_rel = ".state/links_cache.json"
    a = {k: v[0] for k, v in _snapshot(vault).items() if k != cache_rel}
    b = {k: v[0] for k, v in _snapshot(fresh).items() if k != cache_rel}
    assert a == b  # 페이지·daily·원장(.state/links.json)이 바이트 단위로 같다

    def _without_stat(root: Path) -> dict:
        cache = json.loads((root / cache_rel).read_text(encoding="utf-8"))
        for item in cache["files"].values():
            item.pop("stat")
        cache["pages"] = sorted(cache["pages"])
        return cache

    assert _without_stat(vault) == _without_stat(fresh)  # 캐시는 수정 시각만 다르다


def test_cache_fast_path_touch_and_same_size_edit(basic, vault):
    link.run(basic)
    path = vault / "daily" / "2026-11-04.md"
    # 내용은 같고 수정 시각만 바뀜 → 다시 읽되 sha256이 같아 분석은 건너뜀, 페이지 쓰기 없음
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    touched = link.run(basic)
    assert touched["files_parsed"] == 0
    assert touched["written"] == [".state/links_cache.json"]
    # 같은 크기로 고치고 수정 시각을 되돌린 경우는 --rebuild가 잡는다(크기·시각 비교의 한계)
    st = path.stat()
    path.write_bytes(path.read_bytes().replace("댓글".encode("utf-8"), "답글".encode("utf-8")))
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    link.run(basic, rebuild=True)
    assert "- [[2026-11-04]] 16:05 [[KB-12]] 답글 올림" in _region(vault / "jira" / "KB-12.md")


def test_stub_only(vault, fake_now):
    fake_now(NOW)
    cfg = _setup(vault)
    _write(vault, "daily/2026-11-05.md", "## 기록\n- 09:00 KB-1 줄\n")
    summary = link.run(cfg, stub_keys=["kb-99"])
    assert summary["stubs_created"] == ["jira/KB-99.md"]
    assert frontmatter.parse((vault / "jira" / "KB-99.md").read_bytes())["stub"] == "true"
    assert not (vault / "jira" / "KB-1.md").exists()  # 원천은 훑지 않는다
    assert "KB-1 줄" in (vault / "daily" / "2026-11-05.md").read_text(encoding="utf-8")
    again = link.run(cfg, stub_keys=["KB-99"])
    assert again["stubs_created"] == [] and again["stubs_existing"] == ["jira/KB-99.md"]
    with pytest.raises(core.ConfigError):
        link.run(cfg, stub_keys=["ZZ-1"])
    with pytest.raises(core.ConfigError):
        link.run(cfg, stub_keys=["KB12"])


def test_no_project_keys_is_config_error(vault, fake_now):
    fake_now(NOW)
    config.save_local(vault, {"environment": "home"})
    with pytest.raises(core.ConfigError, match="project_keys"):
        link.run(config.load(vault))


# ---------------------------------------------------------------- CLI


def test_cli_link_dry_run_and_stub(basic, vault, run_kb):
    result = run_kb("link", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "시험 실행" in result.stdout and "jira/KB-12.md" in result.stdout
    assert not (vault / "jira" / "KB-12.md").exists()

    result = run_kb("link")
    assert result.returncode == 0, result.stderr
    assert "언급" in result.stdout and (vault / "jira" / "KB-12.md").exists()

    result = run_kb("--argv-stdin", stdin='["link", "--stub", "OPS-77"]')
    assert result.returncode == 0, result.stderr
    assert "jira/OPS-77.md" in result.stdout

    result = run_kb("link", "--stub", "NOPE-1")
    assert result.returncode == 4
    assert "프로젝트 키" in result.stderr


def test_cli_missing_project_keys_exit_4(vault, run_kb):
    result = run_kb("link")
    assert result.returncode == 4
    assert "project_keys" in result.stderr
