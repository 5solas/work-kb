"""clip_image.ps1 테스트. 클립보드는 절대 수정하지 않는다."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "clip_image.ps1"
PS = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"

pytestmark = pytest.mark.skipif(not PS.exists(), reason="powershell.exe 5.1 없음")

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def run_ps(*args: str, sta: bool = True, cwd: Path | None = None):
    env = {k: v for k, v in os.environ.items() if k != "PSExecutionPolicyPreference"}
    cmd = [str(PS), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
    # powershell.exe 5.1 defaults to STA, so the non-STA case needs an explicit -MTA.
    cmd.append("-STA" if sta else "-MTA")
    cmd += ["-File", str(SCRIPT), *args]
    proc = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, env=env, timeout=120, cwd=cwd)
    return proc.returncode, proc.stdout.decode("oem", "replace"), proc.stderr.decode("oem", "replace")


def test_script_is_ascii():
    SCRIPT.read_bytes().decode("ascii")


def test_probe_writes_nothing(tmp_path):
    code, _, err = run_ps("-Probe", cwd=tmp_path)
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


def test_save_matches_probe(tmp_path):
    pcode, _, perr = run_ps("-Probe")
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
