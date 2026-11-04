"""clip_image.ps1 테스트. 클립보드는 절대 수정하지 않는다.

- 저장 경로(PNG 쓰기, 폴더 만들기, 상대 -Out을 $PWD 기준으로 풀기)는 테스트 전용 이음매
  `-FromFile <이미지>`로 검사한다. 이 경로는 클립보드를 열지 않으므로 어느 세션에서나 돈다.
- 실제 클립보드 검사(-Probe, 저장)는 클립보드를 열 수 없는 세션(샌드박스·비대화형 데스크톱)에서
  건너뛴다. 사용자가 일반 데스크톱 세션에서 확인할 때는 `KB_REQUIRE_CLIPBOARD=1`을 켜고 돌린다:
  그러면 "클립보드를 열 수 없음"은 건너뛰기가 아니라 실패다 (조용히 건너뛴 확인을 막는다).
  예) PowerShell: `$env:KB_REQUIRE_CLIPBOARD='1'; .venv\\Scripts\\python.exe -m pytest -q tests/test_clip_image.py -rs`
"""
from __future__ import annotations

import os
import struct
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "clip_image.ps1"
PS = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
# conftest의 autouse 픽스처가 KB_* 환경변수를 지우므로 가져올 때 읽어 둔다.
REQUIRE_CLIPBOARD = os.environ.get("KB_REQUIRE_CLIPBOARD") == "1"

pytestmark = pytest.mark.skipif(not PS.exists(), reason="powershell.exe 5.1 없음")

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _ps_base(sta: bool = True) -> list[str]:
    cmd = [str(PS), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
    # powershell.exe 5.1 defaults to STA, so the non-STA case needs an explicit -MTA.
    cmd.append("-STA" if sta else "-MTA")
    return cmd


def _ps_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in ("PSExecutionPolicyPreference", "PSModulePath")}


def run_ps(*args: str, sta: bool = True, cwd: Path | None = None):
    cmd = _ps_base(sta) + ["-File", str(SCRIPT), *args]
    proc = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, env=_ps_env(), timeout=120, cwd=cwd)
    return proc.returncode, proc.stdout.decode("oem", "replace"), proc.stderr.decode("oem", "replace")


CLIPBOARD_DENIED = ("Clipboard operation did not succeed", "클립보드 작업을 수행하지 못했습니다")


def _skip_if_clipboard_inaccessible(code: int, err: str) -> None:
    """빌드용 셸(샌드박스·비대화형 데스크톱)에서는 클립보드 자체가 열리지 않는다.

    스크립트 결함이 아니라 환경 문제이므로 건너뛰고, 사용자가 일반 데스크톱 세션에서
    KB_REQUIRE_CLIPBOARD=1로 확인한다(DECISIONS Phase 1 관문). 그때는 실패로 바꾼다.
    """
    if code == 3 and any(marker in err for marker in CLIPBOARD_DENIED):
        reason = "이 세션에서는 클립보드를 열 수 없습니다. 일반 데스크톱 세션에서 다시 실행하세요: " + err.strip().splitlines()[-1]
        if REQUIRE_CLIPBOARD:
            pytest.fail("KB_REQUIRE_CLIPBOARD=1: " + reason)
        pytest.skip(reason)


def _bmp(width: int = 3, height: int = 2) -> bytes:
    """작은 24비트 BMP (클립보드 대신 -FromFile로 넣을 그림)."""
    row = b"\x00\x00\xff" * width
    row += b"\x00" * ((4 - len(row) % 4) % 4)
    pixels = row * height
    dib = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, len(pixels), 2835, 2835, 0, 0)
    header = struct.pack("<2sIHHI", b"BM", 14 + 40 + len(pixels), 0, 0, 54)
    return header + dib + pixels


def _png_size(data: bytes) -> tuple[int, int]:
    assert data.startswith(PNG_MAGIC)
    assert data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


def test_script_is_ascii():
    SCRIPT.read_bytes().decode("ascii")


def test_probe_writes_nothing(tmp_path):
    code, _, err = run_ps("-Probe", cwd=tmp_path)
    _skip_if_clipboard_inaccessible(code, err)
    assert code in (0, 2), err
    assert list(tmp_path.iterdir()) == []


def test_missing_out_fails():
    code, _, err = run_ps()
    assert code == 3
    assert "-Out" in err


def test_requires_sta(tmp_path):
    code, _, err = run_ps("-Out", str(tmp_path / "x.png"), sta=False)
    assert code == 3
    assert "STA" in err
    assert not (tmp_path / "x.png").exists()


def test_from_file_relative_out_under_cwd(tmp_path):
    """Emacs처럼 루트(한글·공백 경로)를 cwd로 두고 상대 -Out을 주면 그 아래에 PNG를 만든다."""
    root = tmp_path / "한글 루트"
    root.mkdir()
    (root / "src.bmp").write_bytes(_bmp(3, 2))
    rel = ".state/tmp/clip-20261105-091200.png"
    code, out, err = run_ps("-FromFile", "src.bmp", "-Out", rel, cwd=root)
    assert code == 0, err
    assert out.strip() == "" and err.strip() == ""
    saved = root / ".state" / "tmp" / "clip-20261105-091200.png"
    assert saved.is_file()  # 폴더(.state/tmp)도 스크립트가 만들었다
    assert _png_size(saved.read_bytes()) == (3, 2)
    assert (root / "src.bmp").read_bytes() == _bmp(3, 2)
    assert not (ROOT / rel).exists()
    assert sorted(p.name for p in root.iterdir()) == [".state", "src.bmp"]


def test_from_file_absolute_out_creates_dirs(tmp_path):
    src = tmp_path / "in.bmp"
    src.write_bytes(_bmp(1, 1))
    out = tmp_path / "a" / "b" / "c.png"
    code, _, err = run_ps("-FromFile", str(src), "-Out", str(out), cwd=tmp_path)
    assert code == 0, err
    assert _png_size(out.read_bytes()) == (1, 1)


def test_from_file_missing_or_bad(tmp_path):
    code, _, err = run_ps("-FromFile", "none.bmp", "-Out", "x.png", cwd=tmp_path)
    assert code == 3
    assert "-FromFile" in err
    (tmp_path / "bad.bmp").write_bytes(b"not an image")
    code, _, err = run_ps("-FromFile", "bad.bmp", "-Out", "x.png", cwd=tmp_path)
    assert code == 3
    assert "clip_image:" in err
    assert not (tmp_path / "x.png").exists()


def _forms_loaded_after(tmp_path: Path, *args: str) -> tuple[int, bool, str]:
    """스크립트를 한 세션 안에서 돌린 뒤 System.Windows.Forms(클립보드 API)가 올라왔는지 본다."""
    # 매개변수 이름(-FromFile)은 그대로, 값만 작은따옴표로 감싼다 (감싸면 위치 인수가 된다).
    quoted = " ".join(a if a.startswith("-") else "'" + a.replace("'", "''") + "'" for a in args)
    command = (
        f"& '{SCRIPT}' {quoted}; $c = $LASTEXITCODE; "
        "$f = @([AppDomain]::CurrentDomain.GetAssemblies() | Where-Object { $_.GetName().Name -eq 'System.Windows.Forms' }).Count; "
        "[Console]::Out.WriteLine('exit=' + $c + ' forms=' + $f)"
    )
    proc = subprocess.run(
        _ps_base() + ["-Command", command],
        capture_output=True, stdin=subprocess.DEVNULL, env=_ps_env(), timeout=120, cwd=tmp_path,
    )
    out = proc.stdout.decode("oem", "replace")
    line = [x for x in out.splitlines() if x.startswith("exit=")]
    assert line, out + proc.stderr.decode("oem", "replace")
    code_part, forms_part = line[-1].split()
    return int(code_part.split("=")[1]), forms_part != "forms=0", proc.stderr.decode("oem", "replace")


def test_from_file_never_touches_clipboard(tmp_path):
    """-FromFile은 클립보드 API(System.Windows.Forms)를 불러오지도 않는다. 대조: -Probe는 불러온다."""
    (tmp_path / "src.bmp").write_bytes(_bmp())
    code, forms, err = _forms_loaded_after(tmp_path, "-FromFile", "src.bmp", "-Out", "o.png")
    assert code == 0, err
    assert not forms
    assert (tmp_path / "o.png").read_bytes().startswith(PNG_MAGIC)
    _, forms, _ = _forms_loaded_after(tmp_path, "-Probe")
    assert forms  # 측정이 실제로 Forms 적재를 잡는지 확인


def test_save_matches_probe(tmp_path):
    pcode, _, perr = run_ps("-Probe")
    _skip_if_clipboard_inaccessible(pcode, perr)
    assert pcode in (0, 2), perr
    out = tmp_path / "sub" / "clip.png"
    code, _, err = run_ps("-Out", str(out))
    if pcode == 2:
        assert code == 2, err
        assert not out.exists()
        assert not out.parent.exists()
    else:
        assert code == 0, err
        data = out.read_bytes()
        assert data.startswith(PNG_MAGIC)
        out.unlink()
