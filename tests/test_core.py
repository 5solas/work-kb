import datetime as dt
import io
import json
import os
import subprocess
import sys
import threading
import time
import unicodedata
from pathlib import Path

import pytest

from lib import core

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_exit_codes_follow_spec():
    classes = (core.KbError, core.AuthError, core.NetworkError, core.ConfigError, core.PublicHostRefused, core.LockBusy, core.PolicyRefused)
    assert [c.exit_code for c in classes] == [1, 2, 3, 4, 5, 6, 7]


def test_run_main_maps_kberror_to_exit_code(capsys):
    def main(argv):
        raise core.AuthError("토큰이 만료되었습니다")

    with pytest.raises(SystemExit) as info:
        core.run_main(main, [])
    assert info.value.code == 2
    assert "토큰이 만료되었습니다" in capsys.readouterr().err


def test_run_main_redacts_registered_secrets(capsys):
    core.register_secret("s3cr3t-token-value")

    def main(argv):
        raise core.ConfigError("헤더에 s3cr3t-token-value 가 있음")

    with pytest.raises(SystemExit):
        core.run_main(main, [])
    err = capsys.readouterr().err
    assert "s3cr3t-token-value" not in err
    assert "***" in err


def _feed_stdin(monkeypatch, raw: bytes) -> None:
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8"))


@pytest.mark.parametrize(
    "payload",
    [
        ["link", "--note", "한글 😀 경로", "C:\\한글 폴더\\파일.md"],
        ["capture", "가나다라마바사 아자차카타파하", "뷁 똠 쀍"],  # cp949로 읽으면 깨지거나 실패
    ],
)
def test_argv_stdin_roundtrips_raw_utf8(monkeypatch, payload):
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")  # Emacs json-serialize와 같은 날것 UTF-8
    assert b"\\u" not in raw
    assert raw.decode("cp949", "replace") != raw.decode("utf-8")  # 전제: cp949 해독이면 다른 결과
    _feed_stdin(monkeypatch, raw + b" \r\n")
    seen = {}

    def main(argv):
        seen["argv"] = argv
        return 0

    with pytest.raises(SystemExit) as info:
        core.run_main(main, ["--argv-stdin", "--뒤"])
    assert info.value.code == 0
    assert seen["argv"] == payload + ["--뒤"]


def test_argv_stdin_rejects_invalid_utf8(monkeypatch, capsys):
    _feed_stdin(monkeypatch, '["한글"]'.encode("cp949"))
    with pytest.raises(SystemExit) as info:
        core.run_main(lambda argv: 0, ["--argv-stdin"])
    assert info.value.code == 4
    assert "--argv-stdin" in capsys.readouterr().err


def test_argv_stdin_rejects_non_list(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b'{"a": 1}'), encoding="utf-8"))
    with pytest.raises(SystemExit) as info:
        core.run_main(lambda argv: 0, ["--argv-stdin"])
    assert info.value.code == 4


def test_make_parser_usage_error_is_config_error():
    parser = core.make_parser("x", "설명")
    parser.add_argument("--n", type=int)
    with pytest.raises(core.ConfigError):
        parser.parse_args(["--n", "abc"])
    assert parser.parse_args(["--root", "C:/v"]).root == "C:/v"


def test_refuse_if_agent(monkeypatch):
    core.refuse_if_agent("댓글")
    monkeypatch.setenv("KB_AGENT", "1")
    with pytest.raises(core.PolicyRefused):
        core.refuse_if_agent("댓글")


def test_resolve_tz_uses_fixed_table_without_tzdata(monkeypatch):
    import zoneinfo

    def no_tzdata(name):
        raise zoneinfo.ZoneInfoNotFoundError(name)

    local = dt.timezone(dt.timedelta(hours=-5), "테스트 로컬")
    monkeypatch.setattr(zoneinfo, "ZoneInfo", no_tzdata)
    monkeypatch.setattr(core, "_os_local_tz", lambda: local)
    when = dt.datetime(2026, 11, 5)
    assert core.resolve_tz("Asia/Seoul").utcoffset(when) == dt.timedelta(hours=9)  # OS 로컬(-5)이 아님
    assert core.resolve_tz("UTC").utcoffset(when) == dt.timedelta(0)
    assert core.resolve_tz("Nowhere/Invalid") is local


def test_resolve_tz_prefers_zoneinfo(monkeypatch):
    import zoneinfo

    sentinel = dt.timezone(dt.timedelta(hours=3), "zoneinfo")
    monkeypatch.setattr(zoneinfo, "ZoneInfo", lambda name: sentinel)
    assert core.resolve_tz("Asia/Seoul") is sentinel


def test_os_local_tz_is_a_tzinfo():
    assert isinstance(core._os_local_tz(), dt.tzinfo)


def test_now_honours_fake_now(monkeypatch):
    tz = core.resolve_tz("Asia/Seoul")
    monkeypatch.setenv("KB_FAKE_NOW", "2026-11-05T09:12:00")
    assert core.now(tz).isoformat() == "2026-11-05T09:12:00+09:00"
    monkeypatch.setenv("KB_FAKE_NOW", "2026-11-05T00:12:00+00:00")
    assert core.now(tz).hour == 9


def test_line_hash_normalises_whitespace_and_unicode():
    h = core.line_hash("  KB-12   캐시  ")
    assert h == core.line_hash("KB-12 캐시")
    assert len(h) == 16
    assert h != core.line_hash("KB-13 캐시")
    assert core.line_hash(unicodedata.normalize("NFD", "한글")) == core.line_hash("한글")


def test_atomic_write_creates_and_guards_concurrent_edit(tmp_path):
    path = tmp_path / "한글 폴더" / "노트.md"
    core.atomic_write(path, "내용\r\n".encode("utf-8"))
    assert path.read_bytes() == "내용\r\n".encode("utf-8")
    assert not list(path.parent.glob(".*.tmp"))
    digest = core.sha256_hex(path.read_bytes())
    core.atomic_write(path, b"v2", expect_sha256=digest)
    with pytest.raises(core.ConcurrentEdit):
        core.atomic_write(path, b"v3", expect_sha256=digest)
    assert path.read_bytes() == b"v2"
    assert not list(path.parent.glob(".*.tmp"))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 공유 위반만 해당")
def test_atomic_write_retries_while_handle_is_held(tmp_path):
    path = tmp_path / "held.md"
    path.write_bytes(b"old")
    other = tmp_path / "other.tmp"
    other.write_bytes(b"x")
    with open(path, "rb"):
        with pytest.raises(PermissionError):  # 전제: 열린 파일은 바로 교체되지 않는다
            os.replace(other, path)

    opened = threading.Event()

    def hold():
        with open(path, "rb"):
            opened.set()
            time.sleep(0.6)

    thread = threading.Thread(target=hold)
    thread.start()
    assert opened.wait(10), "도우미 스레드가 파일을 열지 못함"
    started = time.monotonic()
    core.atomic_write(path, b"new")
    assert time.monotonic() - started >= 0.3
    thread.join(10)
    assert path.read_bytes() == b"new"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 공유 위반만 해당")
def test_atomic_write_rechecks_hash_before_every_replace(tmp_path):
    """편집기가 저장하느라 파일을 잡고 있던 사이에 바뀐 내용을, 기다린 뒤에 덮지 않는다."""
    path = tmp_path / "한글 폴더" / "2026-11-04.md"
    path.parent.mkdir()
    original = "## 기록\n- 09:00 KB-1 a\n".encode("utf-8")
    path.write_bytes(original)
    human = original + "- 09:05 사람이 저장 중이던 줄\n".encode("utf-8")
    opened = threading.Event()

    def editor_save():
        with open(path, "r+b") as fh:  # 저장 중인 편집기처럼 쓰기 핸들을 잡는다
            opened.set()
            time.sleep(0.3)
            fh.seek(0)
            fh.write(human)
            fh.truncate()
            fh.flush()

    thread = threading.Thread(target=editor_save)
    thread.start()
    assert opened.wait(10), "도우미 스레드가 파일을 열지 못함"
    with pytest.raises(core.ConcurrentEdit):
        core.atomic_write(path, "## 기록\n- 09:00 [[KB-1]] a\n".encode("utf-8"), expect_sha256=core.sha256_hex(original))
    thread.join(10)
    assert path.read_bytes() == human
    assert not list(path.parent.glob(".*.tmp"))


def test_atomic_write_must_not_exist(tmp_path):
    path = tmp_path / "새 파일.md"
    core.atomic_write(path, b"first", expect_sha256=core.MUST_NOT_EXIST)
    assert path.read_bytes() == b"first"
    with pytest.raises(core.ConcurrentEdit):
        core.atomic_write(path, b"second", expect_sha256=core.MUST_NOT_EXIST)
    assert path.read_bytes() == b"first"
    assert not list(tmp_path.glob(".*.tmp"))


def test_atomic_write_must_not_exist_checks_at_the_last_moment(tmp_path, monkeypatch):
    """임시 파일을 쓰는 사이에 누가 만들면(편집기 저장) 덮지 않는다. 하드 링크가 안 되면 교체 직전 확인."""
    for use_link in (True, False):
        path = tmp_path / f"오늘-{use_link}.md"
        real_fsync = os.fsync

        def fsync_then_human_creates(fd, path=path):
            real_fsync(fd)
            if not path.exists():
                path.write_bytes("사람이 먼저 만듦".encode("utf-8"))

        monkeypatch.setattr(core.os, "fsync", fsync_then_human_creates)
        if not use_link:
            def no_link(src, dst):
                raise OSError("하드 링크를 지원하지 않음")

            monkeypatch.setattr(core.os, "link", no_link)
        with pytest.raises(core.ConcurrentEdit):
            core.atomic_write(path, b"tool", expect_sha256=core.MUST_NOT_EXIST)
        assert path.read_bytes() == "사람이 먼저 만듦".encode("utf-8")
        monkeypatch.undo()
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("회의 [초안] #1 ^블록", "회의 -초안- -1 -블록"),
        ("a|b", "ab"),  # |는 safe_name이 이미 지운다
        ("보통 이름", "보통 이름"),
        ("CON", "_CON"),
        ("", "_"),
        ("[[KB-1]]", "--KB-1--"),
    ],
)
def test_link_safe_name(raw, expected):
    assert core.link_safe_name(raw) == expected
    assert not any(ch in core.link_safe_name(raw) for ch in "[]#^|")


def test_get_logger_creates_nothing_until_first_record(tmp_path):
    root = tmp_path / "로그 볼트"
    root.mkdir()
    logger = core.get_logger(root)
    assert not (root / ".state").exists()  # 얻기만 해서는 폴더도 파일도 없다
    logger.warning("첫 기록 한글")
    for handler in logger.handlers:
        handler.flush()
    assert "첫 기록 한글" in (root / ".state" / "logs" / "kb.log").read_text(encoding="utf-8")


def test_kb_lock_timeout_override(tmp_path, monkeypatch):
    child = _child(tmp_path, hold=3, timeout=1)
    try:
        assert child.stdout.readline().strip() == "locked"
        monkeypatch.setenv("KB_LOCK_TIMEOUT", "0.2")
        started = time.monotonic()
        with pytest.raises(core.LockBusy):
            with core.kb_lock(tmp_path, "test"):  # 기본 60초 대신 0.2초
                pass
        assert time.monotonic() - started < 2
    finally:
        child.kill()
        child.wait(timeout=5)


CHILD = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
from lib import core
try:
    with core.kb_lock(sys.argv[2], "child", timeout=float(sys.argv[4])):
        print("locked", flush=True)
        time.sleep(float(sys.argv[3]))
    print("released", flush=True)
except core.LockBusy:
    print("busy", flush=True)
"""


def _child(root: Path, hold: float, timeout: float) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-X", "utf8", "-c", CHILD, str(SCRIPTS), str(root), str(hold), str(timeout)],
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )


def test_lock_blocks_other_process_then_recovers(tmp_path):
    root = tmp_path / "한글 볼트"
    child = _child(root, hold=1.5, timeout=1)
    assert child.stdout.readline().strip() == "locked"
    started = time.monotonic()
    with pytest.raises(core.LockBusy) as info:
        with core.kb_lock(root, "test", timeout=0.3):
            pass
    assert 0.25 <= time.monotonic() - started < 1.4
    assert "child" in str(info.value)
    with core.kb_lock(root, "test", timeout=5):
        assert time.monotonic() - started >= 1.0
    assert child.wait(timeout=5) == 0


def test_lock_is_reentrant_in_process_and_held_until_outer_exit(tmp_path):
    with core.kb_lock(tmp_path, "a", timeout=0.2):
        with core.kb_lock(tmp_path, "a", timeout=0.2):
            pass
        child = _child(tmp_path, hold=0, timeout=0.3)
        assert child.stdout.readline().strip() == "busy"
        child.wait(timeout=5)
    child = _child(tmp_path, hold=0, timeout=0.3)
    assert child.stdout.readline().strip() == "locked"
    child.wait(timeout=5)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ('회의: "결정"/정리?', "회의 결정정리"),
        ("  .숨김.  ", "숨김"),
        ("", "_"),
        ("CON", "_CON"),
        ("con.md", "_con.md"),
        ("가" * 100, "가" * 80),
        ("a\x00b", "ab"),
        ("CON .txt", "_CON .txt"),
        ("nul .md", "_nul .md"),
        ("COM¹", "_COM¹"),
        ("LPT².png", "_LPT².png"),
        ("CONIN$", "_CONIN$"),
        ("CONOUT$", "_CONOUT$"),
        ("conout$.md", "_conout$.md"),
        ("COM0", "COM0"),
        ("CONSOLE.md", "CONSOLE.md"),
        ("회의 CON", "회의 CON"),
    ],
)
def test_safe_name(raw, expected):
    assert core.safe_name(raw) == expected


@pytest.mark.skipif(not hasattr(os.path, "isreserved"), reason="os.path.isreserved는 Windows 전용")
@pytest.mark.parametrize("raw", ["CON .txt", "nul .md", "COM¹", "LPT².png", "CONIN$", "CONOUT$", "aux", "PRN.tar.gz"])
def test_safe_name_result_is_never_reserved(raw):
    assert os.path.isreserved(raw)  # 전제
    assert not os.path.isreserved(core.safe_name(raw))


def test_safe_name_uses_os_isreserved(monkeypatch):
    monkeypatch.setattr(os.path, "isreserved", lambda p: p == "새 예약 이름", raising=False)
    assert core.safe_name("새 예약 이름") == "_새 예약 이름"
    assert core.safe_name("보통 이름") == "보통 이름"


def test_unique_path_never_overwrites(tmp_path):
    path = tmp_path / "메모.md"
    assert core.unique_path(path) == path
    path.write_bytes(b"")
    assert core.unique_path(path) == tmp_path / "메모-2.md"
    (tmp_path / "메모-2.md").write_bytes(b"")
    assert core.unique_path(path) == tmp_path / "메모-3.md"


@pytest.mark.parametrize("name, ok", [("2026-11-05.md", True), (".#2026-11-05.md", False), ("#2026-11-05.md#", False), ("x.md~", False), (".x.md.123.tmp", False), (".gitkeep", True)])
def test_is_scannable(name, ok):
    assert core.is_scannable(Path("daily") / name) is ok


def test_state_roundtrip_korean(tmp_path):
    assert core.load_state(tmp_path, "links") == {}
    core.save_state(tmp_path, "links", {"daily/2026-11-05.md": {"줄": "한글 😀"}})
    assert core.load_state(tmp_path, "links") == {"daily/2026-11-05.md": {"줄": "한글 😀"}}
    (tmp_path / ".state" / "links.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(core.ConfigError):
        core.load_state(tmp_path, "links")


def test_logger_redacts_secrets(tmp_path):
    core.register_secret("tok-abcdef")
    logger = core.get_logger(tmp_path)
    logger.info("Authorization: Bearer %s", "tok-abcdef")
    for handler in logger.handlers:
        handler.flush()
    text = (tmp_path / ".state" / "logs" / "kb.log").read_text(encoding="utf-8")
    assert "tok-abcdef" not in text
    assert "Bearer ***" in text
