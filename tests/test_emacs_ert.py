"""Emacs ERT(tests/emacs/test-work-kb.el)를 두 콘솔 로캘로 돌린다 (D65).

- 콘솔 상속(이 프로세스의 콘솔)과 CREATE_NO_WINDOW(콘솔 없음 → cp949 로캘) 두 번.
- 두 경우 모두 먼저 `(set-language-environment "Korean")`로 데몬과 같은 파일 인코딩 기본값을 만든다.
- 로캘 확인: CREATE_NO_WINDOW는 WORK_KB_TEST_EXPECT_LOCALE=cp949를 넘겨 ERT가 `locale-coding-system`을
  단정한다. 상속 모드는 콘솔이 cp65001일 때만 cp65001을 단정하고, 늘 로캘을 출력에 남기며,
  그때 두 실행의 로캘이 다른지도 본다(한 로캘만 시험되는 일을 막는다).
- stdin은 DEVNULL, 출력은 UTF-8로 해독, 제한 120초.
- work-kb-root는 이 저장소, 볼트는 tmp 아래 한글 경로(ERT가 WORK_KB_TEST_TMP 아래에 만든다).
- KB_SKIP_EMACS=1일 때만 건너뛴다. 그 밖에 emacs가 없으면 실패다.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ERT_FILE = ROOT / "tests" / "emacs" / "test-work-kb.el"
# conftest의 autouse 픽스처가 KB_* 환경변수를 지우므로 가져올 때 읽어 둔다.
SKIP_EMACS = os.environ.get("KB_SKIP_EMACS") == "1"
MIN_TESTS = 25  # ERT가 실제로 돌았는지 확인 (0개 통과를 성공으로 보지 않음)

pytestmark = pytest.mark.skipif(SKIP_EMACS, reason="KB_SKIP_EMACS=1")

MODES = {
    "inherited-console": 0,
    "create-no-window": getattr(subprocess, "CREATE_NO_WINDOW", 0),
}
# 콘솔이 없으면 Emacs는 시스템 코드 페이지(이 기계 cp949)를 로캘로 쓴다 (D65).
NO_WINDOW_LOCALE = "cp949"
LOCALE_RE = re.compile(r"WORK-KB-TEST-LOCALE: (\S+)")
_seen_locales: dict[str, str] = {}


def _console_cp() -> int:
    """이 프로세스가 물려줄 콘솔의 입력 코드 페이지 (콘솔이 없으면 0)."""
    try:
        import ctypes

        return int(ctypes.windll.kernel32.GetConsoleCP())
    except (AttributeError, OSError):
        return 0


def _expected_locale(mode: str) -> str | None:
    """ERT가 확인할 `locale-coding-system'. 상속 모드는 콘솔이 cp65001일 때만 단정한다."""
    if mode == "create-no-window":
        return NO_WINDOW_LOCALE
    return "cp65001" if _console_cp() == 65001 else None


def _emacs() -> str:
    found = shutil.which("emacs")
    if found is None:
        pytest.fail("emacs를 PATH에서 찾지 못했습니다 (건너뛰려면 KB_SKIP_EMACS=1)")
    return found


def _child_env(tmp_path: Path, expect_locale: str | None = None) -> dict[str, str]:
    drop = {"PYTHONPATH", "PYTHONHOME", "EMACSLOADPATH", "EMACSDATA", "WORK_KB_TEST_EXPECT_LOCALE"}
    env = {k: v for k, v in os.environ.items() if not k.startswith("KB_") and k not in drop}
    env["WORK_KB_TEST_TMP"] = str(tmp_path)
    env["WORK_KB_TEST_EXPECT_KOREAN"] = "1"
    if expect_locale:
        env["WORK_KB_TEST_EXPECT_LOCALE"] = expect_locale
    return env


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 콘솔 로캘 검사")
@pytest.mark.parametrize("mode", list(MODES))
def test_ert_suite(mode, tmp_path):
    assert ERT_FILE.is_file()
    assert str(tmp_path).isascii(), f"임시 경로가 ASCII가 아님: {tmp_path}"
    expected_locale = _expected_locale(mode)
    command = [
        _emacs(),
        "-Q",
        "--batch",
        "--eval",
        '(set-language-environment "Korean")',
        "-l",
        str(ERT_FILE.relative_to(ROOT)).replace("\\", "/"),
        "-f",
        "ert-run-tests-batch-and-exit",
    ]
    result = subprocess.run(
        command,
        cwd=str(ROOT),
        env=_child_env(tmp_path, expected_locale),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        creationflags=MODES[mode],
    )
    output = result.stdout + result.stderr
    tail = output[-6000:]
    assert result.returncode == 0, tail
    summary = re.search(r"Ran (\d+) tests, (\d+) results as expected, (\d+) unexpected", output)
    assert summary, tail
    ran, expected, unexpected = (int(x) for x in summary.groups())
    assert unexpected == 0, tail
    assert ran == expected >= MIN_TESTS, tail
    # 건너뛴 ERT가 있으면 이유를 보여 준다: 한국어 환경·한글 루트 검사는 Windows에서 반드시 돈다.
    skipped = re.search(r"(\d+) skipped", output)
    assert not skipped or int(skipped.group(1)) == 0, tail
    # 이 모드의 로캘을 기록하고(-rA·실패 출력에 보임), 두 모드가 같은 로캘로 돌지 않았는지 본다.
    found = LOCALE_RE.search(output)
    assert found, tail
    locale = found.group(1)
    print(f"{mode}: locale-coding-system={locale} (콘솔 코드 페이지 {_console_cp()})")
    if expected_locale:
        assert locale == expected_locale, tail
    _seen_locales[mode] = locale
    if len(_seen_locales) == len(MODES) and _console_cp() == 65001:
        assert _seen_locales["inherited-console"] != _seen_locales["create-no-window"], _seen_locales
