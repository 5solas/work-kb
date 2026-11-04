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


def test_argv_stdin_roundtrips_korean_and_emoji(monkeypatch):
    payload = ["link", "--note", "한글 😀 경로", "C:\\한글 폴더\\파일.md"]
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode("utf-8"))))
    seen = {}

    def main(argv):
        seen["argv"] = argv
        return 0

    with pytest.raises(SystemExit) as info:
        core.run_main(main, ["--argv-stdin"])
    assert info.value.code == 0
    assert seen["argv"] == payload


def test_argv_stdin_rejects_non_list(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b'{"a": 1}')))
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


def test_resolve_tz_seoul_without_tzdata():
    tz = core.resolve_tz("Asia/Seoul")
    assert tz.utcoffset(dt.datetime(2026, 11, 5)) == dt.timedelta(hours=9)
    assert core.resolve_tz("Nowhere/Invalid") is not None


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

    release = threading.Event()

    def hold():
        with open(path, "rb"):
            release.wait(0.6)

    thread = threading.Thread(target=hold)
    thread.start()
    time.sleep(0.05)
    started = time.monotonic()
    core.atomic_write(path, b"new")
    assert time.monotonic() - started >= 0.3
    thread.join()
    assert path.read_bytes() == b"new"


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
    ],
)
def test_safe_name(raw, expected):
    assert core.safe_name(raw) == expected


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
