"""install.ps1 / uninstall.ps1 / nvim/capture.ahk 테스트.

install.ps1은 이제 venv, 사용자 환경 변수 WORK_KB_ROOT, 읽기 전용 점검(check)만 한다. 일반 Neovim 설정
(Neovim, Neovide, 글꼴, 플러그인, nvd 데몬)은 별도 저장소 nvim-config의 몫이다.

실제 사용자 프로필은 건드리지 않는다.
- 스크립트를 임시 저장소(공백 있는 경로)에 복사해서 돌린다. scripts/kb.py와 requirements.txt는 대역이다.
- 사용자 환경 변수는 가짜 저장소(JSON 파일)에 쓴다: -UserEnvFile 매개변수와 환경 변수
  WORK_KB_INSTALL_USER_ENV_FILE 둘 다로 준다. 모든 테스트 전후에 실제 HKCU\\Environment의 WORK_KB_ROOT가
  그대로인지 본다 (autouse 픽스처).
- LOCALAPPDATA·APPDATA·Startup은 임시 폴더를 매개변수와 환경 변수 둘 다로 준다.
- PATH는 가짜 bin 폴더(인수를 JSON 줄로 기록하는 .cmd 대역) + Windows 시스템 폴더뿐이다. nvim·uv·nvd는 대역이다.
- 하위 프로세스는 tests/nvim_proc.py로 돌린다: 시간 제한, 시간이 넘으면 프로세스 나무 전체를 끝낸다
  (subprocess.run(timeout=)은 손주가 파이프를 쥐면 Windows에서 영원히 기다린다).
- PowerShell 자체가 LOCALAPPDATA·APPDATA 아래 Microsoft\\ 폴더에 캐시를 쓸 수 있어 그 폴더는 비교에서 뺀다.
두 셸(Windows PowerShell 5.1, PowerShell 7)에서 돌린다. pwsh가 없으면 5.1만.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

import nvim_proc

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "install.ps1"
UNINSTALL = ROOT / "uninstall.ps1"
AHK = ROOT / "nvim" / "capture.ahk"
SYSROOT = Path(os.environ.get("SystemRoot", r"C:\Windows"))
PS51 = SYSROOT / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
PWSH = shutil.which("pwsh")
SHELLS = {"ps51": str(PS51)}
if PWSH:
    SHELLS["pwsh"] = PWSH
SHELL_IDS = list(SHELLS)
RUN_TIMEOUT = 180

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Windows 전용"),
    pytest.mark.skipif(not PS51.exists(), reason="powershell.exe 5.1 없음"),
]

LOGGER = """import json, os, shutil, sys
with open(LOG, "a", encoding="utf-8") as f:
    f.write(json.dumps({"name": sys.argv[1], "argv": sys.argv[2:]}, ensure_ascii=False) + "\\n")
if sys.argv[1] == "nvim" and sys.argv[2:] == ["--version"]:
    print("NVIM v" + os.environ.get("STUB_NVIM_VERSION", "0.12.6"))
    print("Build type: Release")
# uv venv 흉내: 작업 트리 .venv의 런처(python.exe)를 복사해 Python 3.14 가상환경처럼 보이게 한다 (설치는 없음)
if sys.argv[1] == "uv" and sys.argv[2:3] == ["venv"] and os.environ.get("STUB_PYTHON_LAUNCHER"):
    venv = sys.argv[-1]
    os.makedirs(os.path.join(venv, "Scripts"), exist_ok=True)
    shutil.copy(os.environ["STUB_PYTHON_LAUNCHER"], os.path.join(venv, "Scripts", "python.exe"))
    with open(os.environ["STUB_PYVENV_CFG"], encoding="utf-8") as src:
        cfg = src.read()
    import re
    cfg = re.sub(r"(?m)^(version(?:_info)?\\s*=\\s*)\\S+", lambda m: m.group(1) + "3.14.0", cfg)
    cfg = re.sub(r"(?m)^include-system-site-packages\\s*=.*$", "include-system-site-packages = false", cfg)
    with open(os.path.join(venv, "pyvenv.cfg"), "w", encoding="utf-8") as dst:
        dst.write(cfg)
"""
KB_STANDIN = b"import sys\nprint('stand-in kb.py ' + ' '.join(sys.argv[1:]))\n"


def _real_user_env() -> str | None:
    """실제 HKCU\\Environment의 WORK_KB_ROOT (읽기만)."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return winreg.QueryValueEx(key, "WORK_KB_ROOT")[0]
    except FileNotFoundError:
        return None


@pytest.fixture(autouse=True)
def real_profile_untouched():
    before = _real_user_env()
    yield
    assert _real_user_env() == before, "실제 사용자 환경 변수 WORK_KB_ROOT가 바뀜"


class Env:
    """임시 저장소 + 임시 프로필 + 가짜 bin + 가짜 사용자 환경 변수 저장소."""

    def __init__(self, base: Path):
        self.base = base
        self.repo = base / "repo dir"
        self.local = base / "LocalAppData"
        self.roaming = base / "AppData"
        self.startup = base / "Startup"
        self.fakebin = base / "fakebin"
        self.log = base / "stub-log.jsonl"
        self.user_env = base / "user-env.json"
        self.manifest = self.local / "work-kb" / "install-manifest.json"
        self.config = self.local / "nvim"
        self._build()

    def _build(self) -> None:
        for d in (self.repo / "scripts", self.local, self.roaming, self.startup, self.fakebin):
            d.mkdir(parents=True, exist_ok=True)
        shutil.copy(INSTALL, self.repo / "install.ps1")
        shutil.copy(UNINSTALL, self.repo / "uninstall.ps1")
        (self.repo / "requirements.txt").write_bytes(b"pyyaml\n")
        (self.repo / "scripts" / "kb.py").write_bytes(KB_STANDIN)
        logger = self.base / "stub_logger.py"
        logger.write_text(f"LOG = {str(self.log)!r}\n" + LOGGER, encoding="utf-8")
        for name in ("nvim", "uv", "nvd"):
            self.stub(name)

    def stub(self, name: str) -> None:
        (self.fakebin / f"{name}.cmd").write_text(
            f'@echo off\r\n"{sys.executable}" -I "{self.base / "stub_logger.py"}" "%~n0" %*\r\nexit /b 0\r\n', encoding="ascii")

    def env(self) -> dict[str, str]:
        drop_prefix = ("KB_", "NVIM", "VIM", "XDG_", "NV_PIPE", "WORK_KB", "PSModulePath", "PSExecutionPolicyPreference")
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(tuple(p.upper() for p in drop_prefix))}
        env["PATH"] = os.pathsep.join([
            str(self.fakebin), str(SYSROOT / "System32"), str(SYSROOT),
            str(SYSROOT / "System32" / "Wbem"), str(PS51.parent),
        ])
        env["LOCALAPPDATA"] = str(self.local)
        env["APPDATA"] = str(self.roaming)
        env["WORK_KB_INSTALL_USER_ENV_FILE"] = str(self.user_env)
        return env

    def run(self, shell: str, script: str, *args: str, extra_env: dict[str, str] | None = None,
            profile: bool = True) -> tuple[int, str]:
        seams = ["-LocalAppData", str(self.local), "-UserEnvFile", str(self.user_env)]
        if script == "install.ps1":
            seams += ["-AppData", str(self.roaming), "-StartupDir", str(self.startup)]
        cmd = [SHELLS[shell], "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
               "-File", str(self.repo / script), *(seams if profile else []), *args]
        res = nvim_proc.run(cmd, timeout=RUN_TIMEOUT, env={**self.env(), **(extra_env or {})}, cwd=self.base)
        out = res.stdout + res.stderr
        assert not res.timed_out, f"{res.describe()}\n{out}"
        return res.returncode, out

    def stub_calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def user_vars(self) -> dict[str, str]:
        if not self.user_env.exists():
            return {}
        text = self.user_env.read_text(encoding="utf-8")
        return json.loads(text) if text.strip() else {}

    def set_user_vars(self, values: dict[str, str]) -> None:
        self.user_env.write_text(json.dumps(values), encoding="utf-8")

    def make_config(self, path: Path | None = None) -> Path:
        """nvim-config처럼 보이는 Neovim 설정 폴더 (work-kb spec 포함)."""
        config = path or self.config
        (config / "lua" / "plugins").mkdir(parents=True)
        (config / "lua" / "core").mkdir(parents=True)
        (config / "init.lua").write_bytes(b"require('core.daemon')\n")
        (config / "lua" / "core" / "daemon.lua").write_bytes(b"return {}\n")
        (config / "lua" / "plugins" / "work-kb.lua").write_bytes(b"return {}\n")
        return config

    def snapshot(self, *tops: Path) -> dict[str, object]:
        snap: dict[str, object] = {}
        for top in tops or (self.repo, self.local, self.roaming, self.startup):
            if top.exists():
                _snap(top, top.name, snap)
        return snap


def _snap(path: Path, rel: str, out: dict[str, object]) -> None:
    """링크(정션·심볼릭)를 따라가지 않고 트리를 기록한다. PowerShell 캐시 폴더(Microsoft)는 뺀다."""
    for entry in os.scandir(path):
        key = f"{rel}/{entry.name}"
        p = Path(entry.path)
        if p.is_junction() or entry.is_symlink():
            out[key] = ("link", os.readlink(entry.path))
        elif entry.is_dir():
            if entry.name == "Microsoft" and path.name in ("LocalAppData", "AppData"):
                continue
            out[key] = ("dir",)
            _snap(p, key, out)
        else:
            st = p.stat()
            out[key] = ("file", p.read_bytes(), st.st_mtime_ns)


def _steps(out: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in re.finditer(r"(?m)^STEP (\S+) (\S+)", out)}


def _real_venv_launcher() -> tuple[Path, Path]:
    src = ROOT / ".venv"
    launcher, cfg = src / "Scripts" / "python.exe", src / "pyvenv.cfg"
    if not (launcher.is_file() and cfg.is_file()):
        pytest.skip("작업 트리에 .venv가 없음 (런처를 빌려 올 수 없음)")
    return launcher, cfg


def _fake_venv(env: Env, version: str, packages: bool = True) -> None:
    """작업 트리 .venv의 런처(python.exe)와 pyvenv.cfg를 복사해 만든 가짜 .venv (설치하지 않는다).

    packages=True면 .pth 한 줄로 작업 트리 .venv의 site-packages를 보이게 한다: pip과 requirements.txt의
    패키지가 "이미 설치된" 상태가 된다 (install.ps1의 의존성 확인이 통과한다). False면 빈 venv다.
    """
    launcher, cfg_path = _real_venv_launcher()
    dst = env.repo / ".venv" / "Scripts"
    dst.mkdir(parents=True)
    shutil.copy(launcher, dst / "python.exe")
    cfg = cfg_path.read_text(encoding="utf-8")
    cfg = re.sub(r"(?m)^(version(?:_info)?\s*=\s*)\S+", lambda m: m.group(1) + version, cfg)
    (env.repo / ".venv" / "pyvenv.cfg").write_text(cfg, encoding="utf-8")
    if packages:
        site = env.repo / ".venv" / "Lib" / "site-packages"
        site.mkdir(parents=True)
        (site / "borrowed.pth").write_text(str(ROOT / ".venv" / "Lib" / "site-packages") + "\n", encoding="utf-8")


@pytest.fixture
def env(tmp_path) -> Env:
    return Env(tmp_path)


# ── 정적 검사 ───────────────────────────────────────────────

SCRIPTS = [INSTALL, UNINSTALL]


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_scripts_are_ascii_without_bom(path):
    data = path.read_bytes()
    data.decode("ascii")
    assert not data.startswith(b"\xef\xbb\xbf")


@pytest.mark.parametrize("shell", SHELL_IDS)
@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_scripts_parse_without_errors(shell, path):
    command = (
        "$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
        f"'{path}', [ref]$null, [ref]$e); 'ERRORS=' + @($e).Count; $e | ForEach-Object {{ $_.Message }}"
    )
    res = nvim_proc.run([SHELLS[shell], "-NoProfile", "-NonInteractive", "-Command", command], timeout=60)
    assert not res.timed_out, res.describe()
    assert "ERRORS=0" in res.stdout, res.stdout + res.stderr


def test_scripts_avoid_ps7_only_syntax():
    """5.1에서도 돌아야 한다: ??, ?., && / || 파이프라인 연결이 없어야 한다(문자열 밖)."""
    for path in SCRIPTS:
        code = re.sub(r"'[^'\n]*'|\"[^\"\n]*\"", "''", path.read_text(encoding="ascii"))
        code = "\n".join(line.split("#", 1)[0] for line in code.splitlines())
        for token in ("??", "?.", "&&", "||"):
            assert token not in code, (path.name, token)


def test_scripts_never_target_the_neovim_or_neovide_profile():
    """설치·제거 스크립트는 일반 Neovim 설정 영역에 쓰지 않는다: 정션, scoop, 글꼴, 시작 프로그램, PATH 없음."""
    for path in SCRIPTS:
        text = path.read_text(encoding="ascii")
        for bad in ("New-Item -ItemType Junction", "mklink", "scoop install", "MasonInstall", "Lazy",
                    "CreateShortcut", "'Path', ", "Fonts", "git clone https", "nvim-data"):
            code_lines = [ln for ln in text.splitlines() if bad in ln and not ln.lstrip().startswith("#")
                          and "Show-Cmd" not in ln and "$NvimConfigUrl = " not in ln]
            assert not code_lines, (path.name, bad, code_lines)


# ── -WhatIf, -Only ──────────────────────────────────────────

@pytest.mark.parametrize("shell", SHELL_IDS)
def test_whatif_changes_nothing_and_prints_the_plan(shell, env):
    env.make_config()
    before = env.snapshot()
    code, out = env.run(shell, "install.ps1", "-WhatIf")
    assert code == 0, out
    assert env.snapshot() == before, out
    assert env.stub_calls() == [], out  # 외부 프로그램을 하나도 띄우지 않는다
    assert not env.user_env.exists(), out
    assert _steps(out) == {"venv": "whatif", "env": "whatif", "check": "whatif"}, out
    for text in (
        f"WHATIF venv {env.fakebin / 'uv.cmd'} venv --seed --python 3.14 \"{env.repo / '.venv'}\"",
        f"WHATIF env set user environment variable WORK_KB_ROOT = {env.repo} in fake user environment {env.user_env}",
        f"WHATIF check {env.fakebin / 'nvim.cmd'} --version",
        f"nvim config: {env.config} (lua\\plugins\\work-kb.lua found)",
    ):
        assert text in out, (text, out)
    # 다음 단계 안내 (한국어, 런타임에 \\uXXXX를 푼다)
    assert "다음 단계" in out and "nvd --restart" in out and "kb.py init" in out, out


@pytest.mark.parametrize("shell", SHELL_IDS)
def test_only_and_skip_select_steps(shell, env):
    code, out = env.run(shell, "install.ps1", "-Only", "env", "-WhatIf")
    assert code == 0 and _steps(out) == {"env": "whatif"}, out
    code, out = env.run(shell, "install.ps1", "-Skip", "venv,check", "-WhatIf")
    assert code == 0 and _steps(out) == {"env": "whatif"}, out
    code, out = env.run(shell, "install.ps1", "-Only", "env,check")
    assert code == 0 and set(_steps(out)) == {"env", "check"}, out
    assert "venv" not in _steps(out) and not (env.repo / ".venv").exists()
    assert env.user_vars() == {"WORK_KB_ROOT": str(env.repo)}


def test_unknown_step_is_rejected(env):
    code, out = env.run("ps51", "install.ps1", "-Only", "env,link")
    assert code == 2, out
    assert "unknown step(s): link" in out
    code, out = env.run("ps51", "uninstall.ps1", "-Only", "nope")
    assert code == 2 and "unknown step(s): nope" in out, out


# ── env: WORK_KB_ROOT ───────────────────────────────────────

@pytest.mark.parametrize("shell", SHELL_IDS)
def test_env_set_rerun_and_uninstall_removes(shell, env):
    code, out = env.run(shell, "install.ps1", "-Only", "env")
    assert code == 0 and _steps(out) == {"env": "changed"}, out
    assert env.user_vars() == {"WORK_KB_ROOT": str(env.repo)}
    manifest = json.loads(env.manifest.read_text(encoding="utf-8"))
    assert manifest["env"]["WORK_KB_ROOT"] == {"value": str(env.repo), "hadPrevious": False, "previous": None}
    assert not env.manifest.read_bytes().startswith(b"\xef\xbb\xbf")
    assert "WORK_KB_ROOT" in out and str(env.manifest) in out  # 요약에 놓은 것

    before = env.snapshot()
    code, out = env.run(shell, "install.ps1", "-Only", "env")
    assert code == 0 and _steps(out) == {"env": "ok"}, out
    assert env.snapshot() == before

    code, out = env.run(shell, "uninstall.ps1")
    assert code == 0, out
    assert _steps(out) == {"env": "changed", "venv": "ok"}, out
    assert "WORK_KB_ROOT" not in env.user_vars()
    assert not env.manifest.exists() and not env.manifest.parent.exists()


@pytest.mark.parametrize("shell", SHELL_IDS)
def test_env_replaces_other_value_only_with_yes_and_uninstall_restores(shell, env):
    other = r"D:\다른 곳\work-kb"
    env.set_user_vars({"WORK_KB_ROOT": other, "UNRELATED": "x"})
    code, out = env.run(shell, "install.ps1", "-Only", "env")  # -NonInteractive: 질문에 답할 수 없음
    assert code == 1 and _steps(out) == {"env": "failed"}, out
    assert env.user_vars() == {"WORK_KB_ROOT": other, "UNRELATED": "x"} and not env.manifest.exists()

    code, out = env.run(shell, "install.ps1", "-Only", "env", "-Yes")
    assert code == 0 and _steps(out) == {"env": "changed"}, out
    assert env.user_vars() == {"WORK_KB_ROOT": str(env.repo), "UNRELATED": "x"}
    entry = json.loads(env.manifest.read_text(encoding="utf-8"))["env"]["WORK_KB_ROOT"]
    assert entry == {"value": str(env.repo), "hadPrevious": True, "previous": other}

    code, out = env.run(shell, "uninstall.ps1", "-WhatIf")
    assert code == 0 and _steps(out)["env"] == "whatif", out
    assert env.user_vars()["WORK_KB_ROOT"] == str(env.repo) and env.manifest.exists()

    code, out = env.run(shell, "uninstall.ps1")
    assert code == 0 and _steps(out)["env"] == "changed", out
    assert env.user_vars() == {"WORK_KB_ROOT": other, "UNRELATED": "x"}
    assert not env.manifest.exists()


def test_env_moved_clone_keeps_the_original_previous_value(env):
    """같은 변수를 install.ps1이 두 번 바꾸면(저장소를 옮김) 처음 값을 기억한다."""
    env.set_user_vars({"WORK_KB_ROOT": r"C:\원래 값"})
    code, out = env.run("ps51", "install.ps1", "-Only", "env", "-Yes")
    assert code == 0, out
    moved = env.base / "moved repo"
    shutil.copytree(env.repo, moved)
    code, out = nvim_run_script(env, moved / "install.ps1", "-Only", "env")  # 우리 값이므로 묻지 않는다
    assert code == 0 and _steps(out) == {"env": "changed"}, out
    assert env.user_vars()["WORK_KB_ROOT"] == str(moved)
    entry = json.loads(env.manifest.read_text(encoding="utf-8"))["env"]["WORK_KB_ROOT"]
    assert entry["previous"] == r"C:\원래 값" and entry["hadPrevious"] is True
    code, out = nvim_run_script(env, moved / "uninstall.ps1")
    assert code == 0, out
    assert env.user_vars() == {"WORK_KB_ROOT": r"C:\원래 값"}


def nvim_run_script(env: Env, script: Path, *args: str) -> tuple[int, str]:
    seams = ["-LocalAppData", str(env.local), "-UserEnvFile", str(env.user_env)]
    res = nvim_proc.run([str(PS51), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
                         *seams, *args], timeout=RUN_TIMEOUT, env=env.env(), cwd=env.base)
    assert not res.timed_out, res.describe()
    return res.returncode, res.stdout + res.stderr


def test_env_already_set_by_hand_is_not_recorded(env):
    env.set_user_vars({"WORK_KB_ROOT": str(env.repo)})
    code, out = env.run("ps51", "install.ps1", "-Only", "env")
    assert code == 0 and _steps(out) == {"env": "ok"} and "not by install.ps1" in out, out
    assert not env.manifest.exists()
    code, out = env.run("ps51", "uninstall.ps1")
    assert code == 0 and _steps(out)["env"] == "ok", out
    assert env.user_vars() == {"WORK_KB_ROOT": str(env.repo)}


def test_uninstall_leaves_a_value_changed_since_install(env):
    code, out = env.run("ps51", "install.ps1", "-Only", "env")
    assert code == 0, out
    env.set_user_vars({"WORK_KB_ROOT": r"E:\사용자가 바꿈"})
    code, out = env.run("ps51", "uninstall.ps1")
    assert code == 0 and _steps(out)["env"] == "skipped", out
    assert env.user_vars() == {"WORK_KB_ROOT": r"E:\사용자가 바꿈"}
    assert not env.manifest.exists()


def test_uninstall_without_manifest_changes_nothing(env):
    env.set_user_vars({"WORK_KB_ROOT": str(env.repo)})
    _fake_venv(env, "3.14.0")
    before = env.snapshot()
    code, out = env.run("ps51", "uninstall.ps1", "-RemoveVenv")
    assert code == 0 and _steps(out) == {"env": "ok", "venv": "ok"}, out
    assert env.snapshot() == before
    assert env.user_vars() == {"WORK_KB_ROOT": str(env.repo)}


# ── check (읽기 전용) ───────────────────────────────────────

@pytest.mark.parametrize("shell", SHELL_IDS)
def test_full_install_with_nvim_config_present_never_touches_it(shell, env):
    """nvim-config가 이미 있는 PC: 설정 폴더와 Neovide 폴더는 바이트·시각까지 그대로, check는 ok, doctor를 돈다."""
    config = env.make_config()
    neovide = env.roaming / "neovide"
    neovide.mkdir()
    (neovide / "config.toml").write_bytes(b"# nvim-config's own\n")
    _fake_venv(env, "3.14.0")
    guarded = (env.config, neovide)
    before = env.snapshot(*guarded)
    code, out = env.run(shell, "install.ps1")
    assert code == 0, out
    assert _steps(out) == {"venv": "ok", "env": "changed", "check": "ok"}, out
    assert env.snapshot(*guarded) == before
    assert f"nvim config: {config} (lua\\plugins\\work-kb.lua found)" in out
    assert f"nvd: {env.fakebin / 'nvd.cmd'}" in out and "nvim: " in out and "v0.12.6" in out
    assert "stand-in kb.py doctor" in out, out
    names = [(c["name"], c["argv"]) for c in env.stub_calls()]
    assert names == [("nvim", ["--version"])], names  # nvd·uv는 실행하지 않는다
    code, out = env.run(shell, "uninstall.ps1", "-RemoveVenv")
    assert code == 0, out
    assert env.snapshot(*guarded) == before
    assert (env.repo / ".venv").is_dir()  # install.ps1이 만든 것이 아니므로 남는다


def test_check_without_config_prints_commands_and_creates_nothing(env):
    env.set_user_vars({"WORK_KB_ROOT": str(env.repo)})
    (env.fakebin / "nvd.cmd").unlink()
    before = env.snapshot()
    code, out = env.run("ps51", "install.ps1", "-Only", "check")
    assert code == 0, out  # warn은 실패가 아니다
    assert _steps(out) == {"check": "warn"}, out
    assert env.snapshot() == before
    assert not env.config.exists()
    clone = env.base / "nvim-config"
    assert f'git clone https://github.com/5solas/nvim-config "{clone}"' in out
    assert f'pwsh -File "{clone}\\scripts\\install.ps1"' in out
    assert "nvim-config missing" in out and "nvd missing" in out and "no .venv" in out
    assert "nvd: not found on PATH" in out
    assert [c["name"] for c in env.stub_calls()] == ["nvim"]  # git·nvd·pwsh는 띄우지 않는다


def test_check_old_nvim_and_config_without_spec(env):
    env.config.mkdir()
    (env.config / "init.lua").write_bytes(b"-- some other config\n")
    env.set_user_vars({"WORK_KB_ROOT": r"D:\elsewhere"})
    code, out = env.run("ps51", "install.ps1", "-Only", "check", extra_env={"STUB_NVIM_VERSION": "0.10.4"})
    assert code == 0 and _steps(out) == {"check": "warn"}, out
    assert "nvim 0.10.4 < 0.11" in out
    assert "config does not load work-kb" in out and f'git -C "{env.config}" pull' in out
    assert "WORK_KB_ROOT not this repository" in out
    assert (env.config / "init.lua").read_bytes() == b"-- some other config\n"


def test_check_reports_old_installer_leftovers_without_touching_them(env):
    link = env.config
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(env.repo / "nvim")], check=True, capture_output=True,
                   timeout=30)
    (env.repo / "nvim").mkdir()
    toml = env.roaming / "neovide" / "config.toml"
    toml.parent.mkdir()
    toml.write_bytes(b"# work-kb: generated by install.ps1 from nvim/neovide/config.toml.in\nfork = true\n")
    (env.startup / "work-kb-nvim-server.lnk").write_bytes(b"not really a shortcut")
    before = env.snapshot()
    code, out = env.run("ps51", "install.ps1", "-Only", "check")
    assert code == 0 and _steps(out) == {"check": "warn"}, out
    assert env.snapshot() == before
    assert f'cmd /c rmdir "{link}"' in out and "old junction" in out
    assert f'Remove-Item -LiteralPath "{toml}"' in out
    assert f'Remove-Item -LiteralPath "{env.startup / "work-kb-nvim-server.lnk"}"' in out


def test_check_follows_xdg_config_home_and_nvim_appname(env):
    xdg = env.base / "xdg"
    config = env.make_config(xdg / "kbtest")
    env.set_user_vars({"WORK_KB_ROOT": str(env.repo)})
    _fake_venv(env, "3.14.0")
    code, out = env.run("ps51", "install.ps1", "-Only", "check",
                        extra_env={"XDG_CONFIG_HOME": str(xdg), "NVIM_APPNAME": "kbtest"})
    assert code == 0 and _steps(out) == {"check": "ok"}, out
    assert f"nvim config: {config} (lua\\plugins\\work-kb.lua found)" in out


# ── venv ────────────────────────────────────────────────────

def test_venv_whatif_plan(env):
    code, out = env.run("ps51", "install.ps1", "-Only", "venv", "-WhatIf", "-Offline", "-WheelsDir", str(env.base / "wheels"))
    assert code == 0, out
    assert f'venv --seed --python 3.14 "{env.repo / ".venv"}"' in out  # 공백 있는 경로는 따옴표로
    assert f"--no-index --find-links {env.base / 'wheels'}" in out
    assert f'-X utf8 "{env.repo / "scripts" / "kb.py"}" --help' in out


def test_venv_whatif_plan_online_first(env):
    code, out = env.run("ps51", "install.ps1", "-Only", "venv", "-WhatIf")
    assert code == 0, out
    assert re.search(r"(?m)^WHATIF venv .*python\.exe\" -X utf8 -m pip install -r .*requirements\.txt\"\r?$", out), out
    assert "WHATIF venv (only if the online install fails)" in out and "--no-index --find-links" in out


def test_venv_existing_good_is_kept(env):
    _fake_venv(env, "3.14.0")
    code, out = env.run("ps51", "install.ps1", "-Only", "venv")
    assert code == 0, out
    assert _steps(out) == {"venv": "ok"} and "kb.py --help works, requirements satisfied" in out, out
    assert env.stub_calls() == []
    assert not env.manifest.exists()


def test_venv_existing_but_missing_packages_is_not_reported_ok(env):
    """kb.py --help는 표준 라이브러리만으로 돈다. 패키지가 빠진 venv를 'ok'로 넘기면 안 된다 (git pull 뒤 재실행)."""
    _fake_venv(env, "3.14.0", packages=False)
    wheels = env.base / "empty wheels"
    wheels.mkdir()
    code, out = env.run("ps51", "install.ps1", "-Only", "venv", "-Offline", "-WheelsDir", str(wheels))
    assert code == 1 and _steps(out) == {"venv": "failed"}, out
    assert "--no-index --find-links" in out, out  # 설치를 시도했다


def test_venv_wrong_version_without_yes_is_left_alone(env):
    _fake_venv(env, "3.15.2")
    before = env.snapshot()
    code, out = env.run("ps51", "install.ps1", "-Only", "venv")
    assert code == 1 and _steps(out) == {"venv": "failed"} and "3.15" in out, out
    assert env.snapshot() == before
    assert env.stub_calls() == []


def test_created_venv_is_recorded_and_removed_only_with_remove_venv(env):
    """uv 대역이 가짜 venv를 만든다. pip는 그 venv에 없으므로 설치는 실패하지만(네트워크 없음) 만든 사실은 기록된다."""
    launcher, cfg = _real_venv_launcher()
    wheels = env.base / "empty wheels"
    wheels.mkdir()
    code, out = env.run("ps51", "install.ps1", "-Only", "venv", "-Offline", "-WheelsDir", str(wheels),
                        extra_env={"STUB_PYTHON_LAUNCHER": str(launcher), "STUB_PYVENV_CFG": str(cfg)})
    venv = env.repo / ".venv"
    assert (venv / "Scripts" / "python.exe").is_file(), out
    assert [c["argv"][:4] for c in env.stub_calls()] == [["venv", "--seed", "--python", "3.14"]], out
    assert json.loads(env.manifest.read_text(encoding="utf-8"))["venvs"] == [str(venv)], out
    assert code == 1 and _steps(out) == {"venv": "failed"}, out  # pip 없음

    code, out = env.run("ps51", "uninstall.ps1")
    assert code == 0 and _steps(out)["venv"] == "skipped" and "-RemoveVenv" in out, out
    assert venv.is_dir() and env.manifest.exists()

    outside = env.base / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_bytes(b"keep")
    subprocess.run(["cmd", "/c", "mklink", "/J", str(venv / "j"), str(outside)], check=True, capture_output=True, timeout=30)
    code, out = env.run("ps51", "uninstall.ps1", "-RemoveVenv")
    assert code == 0 and _steps(out)["venv"] == "changed", out
    assert not venv.exists()
    assert (outside / "keep.txt").read_bytes() == b"keep"  # 정션 안을 따라 지우지 않는다
    assert not env.manifest.exists()


# ── nvim/capture.ahk (AutoHotkey v2; 이 PC에는 AutoHotkey가 없어 실행은 확인하지 않는다) ──

def _ahk_text() -> str:
    return AHK.read_bytes().decode("utf-8")


def _ahk_string(name: str) -> str:
    m = re.search(rf'(?m)^{name} := "([^"\r\n]*)"\s*$', _ahk_text())
    assert m, name
    return m.group(1)


def test_capture_ahk_shape():
    raw = AHK.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw
    text = _ahk_text()
    lines = text.splitlines()
    assert lines[0].strip() == "#Requires AutoHotkey v2.0"
    assert "UNVERIFIED" in text and "미확인" in text
    assert re.search(r'(?m)^\^!\+c::Capture\("WorkKb capture"\)$', text)
    assert re.search(r'(?m)^\^!\+v::Capture\("WorkKb capture-clipboard-image"\)$', text)
    assert not re.search(r"(?m)^[\^!+]*#[\^!+#]*\w+::", text)  # Win 조합 없음(D13)
    assert not re.search(r"(?m)^\s*(Run|MsgBox|Send|WinActivate|RunWait),", text)  # v1 명령 문법 없음
    code = "\n".join(ln for ln in lines if not ln.lstrip().startswith(";"))
    # 옛 자체 서버 방식이 남아 있지 않다
    for old in ("work-kb-nvim", "neovide-attach", "nvim-server", "--remote-send"):
        assert old not in code, old
    # nvim-config 데몬: NV_PIPE를 따르고 기본은 nvd.ps1과 같은 이름, nvd와 nvd-rpc.lua를 쓴다
    assert 'EnvGet("NV_PIPE")' in code and '"\\\\.\\pipe\\nvim-daemon-" A_UserName' in code
    assert '"nvd.cmd"' in code and '"nvd-rpc.lua"' in code
    # 기다림은 모두 상한이 있다
    assert "RunWait(" not in code
    assert re.findall(r"ProcessWaitClose\(pid, \w+Seconds\)", code) and "ProcessWaitClose(pid)" not in code
    assert all(re.search(r"WinWait\([^)]*, \w+\)", ln) for ln in code.splitlines() if "WinWait(" in ln)
    assert "A_TickCount > deadline" in code
    settings = [i for i, ln in enumerate(lines) if re.match(r"^\w+ := ", ln)]
    hotkeys = [i for i, ln in enumerate(lines) if "::" in ln and not ln.lstrip().startswith(";")]
    assert settings and max(settings) < min(hotkeys)


def test_capture_ahk_commands_exist_in_plugin():
    init = next((ROOT / "nvim").rglob("lua/work-kb/init.lua")).read_text(encoding="utf-8")
    names = set(re.findall(r'name = "([a-z-]+)"', init))
    commands = re.findall(r'Capture\("WorkKb ([a-z-]+)"\)', _ahk_text())
    assert commands
    for command in commands:
        assert command in names, command


def test_capture_ahk_lua_snippets_survive_cmd_quoting():
    """Lua 조각은 cmd /c 명령줄의 따옴표 안에 들어간다: " 와 % 와 < > 가 없어야 한다."""
    for name in ("LuaCwd", "LuaUis", "LuaSend"):
        code = _ahk_string(name)
        for ch in '"%<>^':
            assert ch not in code, (name, ch)


def _pipe_exists(name: str) -> bool:
    """capture.ahk와 같은 판정 (연결하지 않는다): WaitNamedPipeW가 성공하거나, 있지만 바쁘면(121) 있는 것이다."""
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitNamedPipeW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    if kernel32.WaitNamedPipeW(name, 1):
        return True
    return ctypes.get_last_error() == 121


def _nvd_rpc() -> Path | None:
    for base in filter(None, (os.environ.get("NVIM_CONFIG_DIR"), str(ROOT.parent / "nvim-config"))):
        rpc = Path(base) / "bin" / "nvd-rpc.lua"
        if rpc.is_file():
            return rpc
    return None


def test_capture_ahk_lua_drives_a_headless_daemon_through_nvd_rpc(tmp_path):
    """AutoHotkey 없이 같은 경로를 시험한다: capture.ahk의 Lua 조각을 nvim-config의 nvd-rpc.lua로
    전용 파이프의 헤드리스 nvim(--clean, 가짜 :WorkKb)에 보낸다. 사용자의 데몬 파이프는 쓰지 않는다."""
    nvim = nvim_proc.resolve_exe("nvim")
    rpc = _nvd_rpc()
    if nvim is None or rpc is None:
        pytest.skip("nvim 또는 nvim-config의 bin/nvd-rpc.lua 없음")
    pipe = rf"\\.\pipe\work-kb-ahk-test-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    child_env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("NVIM", "VIM", "XDG_", "NV_PIPE"))}
    child_env.update(XDG_STATE_HOME=str(tmp_path / "state"), XDG_DATA_HOME=str(tmp_path / "data"))
    server = subprocess.Popen([nvim, "--clean", "--headless", "-n", "-i", "NONE", "--listen", pipe],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              env=child_env, creationflags=nvim_proc.CREATE_NO_WINDOW)

    def call(code: str, value: str = "") -> str:
        res = nvim_proc.run([nvim, "-l", str(rpc), pipe, code, value], timeout=20, env=child_env)
        assert not res.timed_out and res.returncode == 0, res.describe() + res.stderr
        return res.stdout.strip()

    try:
        deadline = time.monotonic() + 15
        while not _pipe_exists(pipe):
            assert time.monotonic() < deadline and server.poll() is None, "서버 파이프가 생기지 않음"
            time.sleep(0.05)
        assert call(_ahk_string("LuaUis")) == "0"
        assert call(_ahk_string("LuaSend"), "WorkKb capture") == "no-work-kb"  # work-kb가 없으면 아무것도 하지 않는다
        assert call("return vim.fn.mode()") == "n"
        assert os.path.normcase(call(_ahk_string("LuaCwd"))) == os.path.normcase(call("return vim.uv.cwd()"))
        call("vim.api.nvim_create_user_command('WorkKb', function(o) vim.g.kb_last = o.args end, { nargs = 1 }) return 1")
        call("vim.api.nvim_feedkeys('i', 'n', false) return 1")  # 삽입 모드에 있어도 <C-\\><C-N>으로 빠져나온다
        assert call(_ahk_string("LuaSend"), "WorkKb capture-clipboard-image") == "ok"
        deadline = time.monotonic() + 10
        while call("return vim.g.kb_last or ''") != "capture-clipboard-image":
            assert time.monotonic() < deadline, "WorkKb가 불리지 않음"
            time.sleep(0.05)
        assert call("return vim.fn.mode()") == "n"
    finally:
        try:
            nvim_proc.run([nvim, "-l", str(rpc), pipe, "vim.defer_fn(function() vim.cmd('qa!') end, 10) return 1", ""],
                          timeout=10, env=child_env)
            server.wait(10)
        except subprocess.TimeoutExpired:
            pass
        finally:
            if server.poll() is None:
                server.kill()  # 이 테스트가 띄운 nvim만
                server.wait(10)
