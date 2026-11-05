"""install.ps1 / uninstall.ps1 / nvim/bin 런처 / nvim/capture.ahk 테스트 (설계 문서 I절, 리드 결정 A3).

실제 사용자 프로필은 건드리지 않는다.
- 스크립트를 임시 저장소(공백 있는 경로)에 복사하고, nvim/ 아래 설정 파일은 대역(stand-in)을 쓴다
  (N4a·N4b가 쓰는 init.lua·lazy-lock.json·tools.lock.json·local.example.lua·config.toml.in의 설계 문서 이름).
- LOCALAPPDATA·APPDATA·Startup 폴더는 임시 폴더를 매개변수(-LocalAppData 등)와 환경변수 둘 다로 준다.
- PATH는 가짜 bin 폴더(인수를 JSON 줄로 기록하는 .cmd 대역) + Windows 시스템 폴더뿐이다.
  그래서 scoop·nvim·uv는 대역이 불리고 아무것도 설치되지 않는다. SCOOP도 임시 폴더다.
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
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "install.ps1"
UNINSTALL = ROOT / "uninstall.ps1"
BIN = ROOT / "nvim" / "bin"
AHK = ROOT / "nvim" / "capture.ahk"
SYSROOT = Path(os.environ.get("SystemRoot", r"C:\Windows"))
PS51 = SYSROOT / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
PWSH = shutil.which("pwsh")
SHELLS = {"ps51": str(PS51)}
if PWSH:
    SHELLS["pwsh"] = PWSH

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Windows 전용"),
    pytest.mark.skipif(not PS51.exists(), reason="powershell.exe 5.1 없음"),
]

LOCK_PLUGINS = {
    "lazy.nvim": "306a05526ada86a7b30af95c5cc81ffba93fef97",
    "tokyonight.nvim": "0123456789abcdef0123456789abcdef01234567",
}
TOOLS = {"lua-language-server": "3.19.2", "stylua": "v2.3.0"}
LOCAL_EXAMPLE = """return {
  vault = nil,          -- [[D:\\업무\\vault]]; nil = repo root
  python = nil,
  colorscheme = 'tokyonight-night',
  ime = { auto = true, langmap = false },
  background = { enabled = true, server = true, prewarm = true, deferred_shada = false },
  neovide = { scale = 1.0, scroll_animation = 0.08 },
  keep_builtins = {},   -- e.g. { 'matchit' }
}
"""
TOML_TEMPLATE = """neovim-bin = '@NVIM_EXE@'     # real exe, not the scoop shim
fork = true
frame = "full"
idle = true
maximized = false
no-multigrid = false
srgb = true
tabs = false
title-hidden = false
vsync = true
wsl = false

[font]
normal = ["JetBrainsMono NF", "Sarasa Fixed K"]
size = 12.0
hinting = "full"
edging = "antialias"
"""
LOGGER = """import json, os, sys
keys = ("XDG_CONFIG_HOME", "LOCALAPPDATA", "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0")
with open(LOG, "a", encoding="utf-8") as f:
    f.write(json.dumps({"name": sys.argv[1], "argv": sys.argv[2:], **{k: os.environ.get(k) for k in keys}},
                       ensure_ascii=False) + "\\n")
# lazy.nvim 흉내: 설치 뒤 잠금 파일을 다시 쓴다 (설치 스크립트가 원래 내용으로 되돌려야 한다)
if sys.argv[1] == "nvim" and os.environ.get("STUB_CLOBBER_LOCK") and os.environ.get("XDG_CONFIG_HOME"):
    with open(os.path.join(os.environ["XDG_CONFIG_HOME"], "nvim", "lazy-lock.json"), "w", encoding="utf-8") as f:
        f.write("{}\\n")
"""


class Env:
    """임시 저장소 + 임시 프로필 + 가짜 bin."""

    def __init__(self, base: Path):
        self.base = base
        self.repo = base / "repo dir"
        self.local = base / "LocalAppData"
        self.roaming = base / "AppData"
        self.startup = base / "Startup"
        self.scoop = base / "scoop"
        self.fakebin = base / "fakebin"
        self.log = base / "stub-log.jsonl"
        self.vault = base / "업무 볼트" / "vault"
        self.real_nvim = self.scoop / "apps" / "neovim" / "current" / "bin" / "nvim.exe"
        self._build()

    def _build(self) -> None:
        nvim = self.repo / "nvim"
        for d in (nvim / "lua", nvim / "neovide", nvim / "bin", self.repo / "scripts", self.local, self.roaming,
                  self.startup, self.fakebin, self.vault, self.real_nvim.parent):
            d.mkdir(parents=True, exist_ok=True)
        shutil.copy(INSTALL, self.repo / "install.ps1")
        shutil.copy(UNINSTALL, self.repo / "uninstall.ps1")
        for f in BIN.glob("*.ps1"):
            shutil.copy(f, nvim / "bin" / f.name)
        (nvim / "init.lua").write_bytes(b"-- stand-in for the N4a init.lua\n")
        lock = {name: {"branch": "main", "commit": commit} for name, commit in LOCK_PLUGINS.items()}
        (nvim / "lazy-lock.json").write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
        (nvim / "tools.lock.json").write_text(json.dumps(TOOLS, indent=2) + "\n", encoding="utf-8")
        (nvim / "lua" / "local.example.lua").write_bytes(LOCAL_EXAMPLE.encode("utf-8"))
        (nvim / "neovide" / "config.toml.in").write_bytes(TOML_TEMPLATE.encode("utf-8"))
        (self.repo / "requirements.txt").write_bytes(b"pyyaml\n")
        (self.repo / "scripts" / "kb.py").write_bytes(b"print('stand-in')\n")
        self.real_nvim.write_bytes(b"")  # neovim-bin이 가리킬 '실제 exe' 자리
        logger = self.base / "stub_logger.py"
        logger.write_text(f"LOG = {str(self.log)!r}\n" + LOGGER, encoding="utf-8")
        for name in ("scoop", "nvim", "uv"):
            (self.fakebin / f"{name}.cmd").write_text(
                f'@echo off\r\n"{sys.executable}" -I "{logger}" "%~n0" %*\r\nexit /b 0\r\n', encoding="ascii")
        # scoop 심 모양: nvim.cmd 옆 nvim.shim이 실제 exe를 가리킨다 (설치 스크립트가 따라간다)
        (self.fakebin / "nvim.shim").write_text(f'path = "{self.real_nvim}"\r\n', encoding="utf-8")

    def env(self) -> dict[str, str]:
        drop_prefix = ("KB_", "NVIM", "VIM", "XDG_", "PSModulePath", "PSExecutionPolicyPreference")
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(tuple(p.upper() for p in drop_prefix))}
        env["PATH"] = os.pathsep.join([
            str(self.fakebin), str(SYSROOT / "System32"), str(SYSROOT),
            str(SYSROOT / "System32" / "Wbem"), str(PS51.parent),
        ])
        env["LOCALAPPDATA"] = str(self.local)
        env["APPDATA"] = str(self.roaming)
        env["SCOOP"] = str(self.scoop)
        return env

    def profile_args(self) -> list[str]:
        return ["-LocalAppData", str(self.local), "-AppData", str(self.roaming), "-StartupDir", str(self.startup)]

    def run(self, shell: str, script: str, *args: str, timeout: int = 180,
            extra_env: dict[str, str] | None = None) -> tuple[int, str]:
        cmd = [SHELLS[shell], "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
               "-File", str(self.repo / script), *self.profile_args(), *args]
        proc = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, env={**self.env(), **(extra_env or {})},
                              cwd=str(self.base), timeout=timeout)
        out = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
        return proc.returncode, out

    def stub_calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def snapshot(self) -> dict[str, object]:
        snap: dict[str, object] = {}
        for top in (self.repo, self.local, self.roaming, self.startup, self.scoop, self.vault.parent):
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
            out[key] = ("file", p.read_bytes())


def _steps(out: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in re.finditer(r"(?m)^STEP (\S+) (\S+)", out)}


def _same_path(a: Path | str, b: Path | str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


@pytest.fixture
def env(tmp_path) -> Env:
    return Env(tmp_path)


SHELL_IDS = list(SHELLS)


# ── 정적 검사 ───────────────────────────────────────────────

SCRIPTS = [INSTALL, UNINSTALL, BIN / "nvim-server.ps1", BIN / "neovide-attach.ps1"]


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
    proc = subprocess.run([SHELLS[shell], "-NoProfile", "-NonInteractive", "-Command", command],
                          capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
    out = proc.stdout.decode("utf-8", "replace")
    assert "ERRORS=0" in out, out + proc.stderr.decode("utf-8", "replace")


def test_scripts_avoid_ps7_only_syntax():
    """5.1에서도 돌아야 한다: ??, ?., 삼항 연산자, && / || 파이프라인 연결이 없어야 한다(문자열 밖)."""
    for path in SCRIPTS:
        code = re.sub(r"'[^'\n]*'|\"[^\"\n]*\"", "''", path.read_text(encoding="ascii"))
        code = "\n".join(line.split("#", 1)[0] for line in code.splitlines())
        for token in ("??", "?.", "&&", "||"):
            assert token not in code, (path.name, token)


# ── -WhatIf ─────────────────────────────────────────────────

@pytest.mark.parametrize("shell", SHELL_IDS)
def test_whatif_changes_nothing_and_prints_the_plan(shell, env):
    before = env.snapshot()
    code, out = env.run(shell, "install.ps1", "-WhatIf", "-Yes", "-VaultPath", str(env.vault), "-PreStart")
    assert code == 0, out
    assert env.snapshot() == before, out
    assert env.stub_calls() == [], out
    steps = _steps(out)
    for step in ("scoop", "link", "local", "neovide", "venv", "plugins", "tools", "doctor", "prestart"):
        assert steps.get(step) == "whatif", (step, out)
    junction = env.local / "nvim"
    expected = [
        "WHATIF scoop scoop install git",
        "WHATIF scoop scoop install ripgrep",
        "WHATIF scoop scoop bucket add extras",
        "WHATIF scoop scoop install extras/neovide",
        f"WHATIF link junction {junction} -> {env.repo / 'nvim'}",
        f"WHATIF local write {env.repo / 'nvim' / 'lua' / 'local.lua'} (vault = {env.vault})",
        f"WHATIF neovide write {env.roaming / 'neovide' / 'config.toml'} (neovim-bin = {env.real_nvim})",
        "venv --seed --python 3.14",
        "+lua local ok, r = pcall(function() return require('config.lazy').install() end)",
        "not at the lockfile commit: ",
        "require('config.background').warm_all({ sync = true })",
        "+MasonInstall lua-language-server@3.19.2 stylua@v2.3.0",
        "+lua require('config.health').cli()",
        f"WHATIF prestart shortcut {env.startup / 'work-kb-nvim-server.lnk'}",
    ]
    for text in expected:
        assert text in out, (text, out)
    assert "-WindowStyle Hidden -File" in out and "nvim-server.ps1" in out


@pytest.mark.parametrize("shell", SHELL_IDS)
def test_uninstall_whatif_changes_nothing(shell, env):
    code, out = env.run(shell, "install.ps1", "-Only", "link,local,neovide", "-Yes")
    assert code == 0, out
    before = env.snapshot()
    code, out = env.run(shell, "uninstall.ps1", "-WhatIf", "-Purge")
    assert code == 0, out
    assert env.snapshot() == before, out
    assert f"WHATIF link remove junction {env.local / 'nvim'}" in out
    assert f"WHATIF neovide remove {env.roaming / 'neovide' / 'config.toml'}" in out


def test_unknown_step_is_rejected(env):
    code, out = env.run("ps51", "install.ps1", "-Only", "link,nope")
    assert code == 2, out
    assert "unknown step(s): nope" in out


# ── 실제 실행: link, local, neovide ─────────────────────────

@pytest.mark.parametrize("shell", SHELL_IDS)
def test_link_local_neovide_then_rerun_is_noop(shell, env):
    code, out = env.run(shell, "install.ps1", "-Only", "link,local,neovide", "-Yes", "-VaultPath", str(env.vault))
    assert code == 0, out
    assert _steps(out) == {"link": "changed", "local": "changed", "neovide": "changed"}, out
    assert env.stub_calls() == []

    junction = env.local / "nvim"
    assert junction.is_junction()
    assert _same_path(junction, env.repo / "nvim")
    assert (junction / "init.lua").read_bytes() == (env.repo / "nvim" / "init.lua").read_bytes()

    local = (env.repo / "nvim" / "lua" / "local.lua").read_bytes()
    assert not local.startswith(b"\xef\xbb\xbf")
    text = local.decode("utf-8")
    assert f"vault = [[{env.vault}]]," in text
    assert text.replace(f"vault = [[{env.vault}]],", "vault = nil,") == LOCAL_EXAMPLE  # 나머지는 그대로(LF 포함)

    toml_path = env.roaming / "neovide" / "config.toml"
    raw = toml_path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert raw.startswith(b"# work-kb: generated by install.ps1")
    data = tomllib.loads(raw.decode("utf-8"))
    assert data["neovim-bin"] == str(env.real_nvim)
    assert Path(data["neovim-bin"]).is_file()
    assert data["font"]["size"] == 12.0

    before = env.snapshot()
    code, out = env.run(shell, "install.ps1", "-Only", "link,local,neovide", "-Yes", "-VaultPath", str(env.vault))
    assert code == 0, out
    assert _steps(out) == {"link": "ok", "local": "ok", "neovide": "ok"}, out
    assert env.snapshot() == before


def test_local_lua_round_trips_hangul_vault_through_nvim(env):
    nvim = shutil.which("nvim")
    assert nvim, "nvim이 PATH에 없음"
    tricky = env.base / "업무 볼트" / "a]]b ]=] c"  # 긴 괄호 수준을 올려야 하는 경로
    tricky.mkdir(parents=True)
    code, out = env.run("ps51", "install.ps1", "-Only", "local", "-Yes", "-VaultPath", str(tricky))
    assert code == 0, out
    local = env.repo / "nvim" / "lua" / "local.lua"
    check = env.base / "check.lua"
    check.write_text(
        "local t = dofile(_G.arg[1]); io.stdout:write(t.vault); io.stdout:write('|' .. tostring(t.python))\n",
        encoding="utf-8",
    )
    proc = subprocess.run([nvim, "--clean", "--headless", "-l", str(check), str(local)],
                          capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert proc.stdout.decode("utf-8") == f"{tricky}|nil"


def test_local_without_vault_keeps_example(env):
    code, out = env.run("ps51", "install.ps1", "-Only", "local", "-Yes")
    assert code == 0, out
    assert (env.repo / "nvim" / "lua" / "local.lua").read_bytes() == LOCAL_EXAMPLE.encode("utf-8")


def test_local_never_overwrites(env):
    local = env.repo / "nvim" / "lua" / "local.lua"
    local.write_bytes(b"return { vault = 'mine' }\n")
    code, out = env.run("ps51", "install.ps1", "-Only", "local", "-Yes", "-VaultPath", str(env.vault))
    assert code == 0, out
    assert _steps(out) == {"local": "ok"}
    assert local.read_bytes() == b"return { vault = 'mine' }\n"


def test_font_size_and_foreign_config_backup(env):
    toml_path = env.roaming / "neovide" / "config.toml"
    toml_path.parent.mkdir(parents=True)
    toml_path.write_bytes(b"# my own config\nframe = 'none'\n")
    code, out = env.run("ps51", "install.ps1", "-Only", "neovide", "-FontSize", "13.5")
    assert code == 0, out
    assert tomllib.loads(toml_path.read_text(encoding="utf-8"))["font"]["size"] == 13.5
    backups = list(toml_path.parent.glob("config.toml.bak-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == b"# my own config\nframe = 'none'\n"
    # 설치 스크립트가 만든 파일은 다시 만들 때 백업하지 않는다
    code, out = env.run("ps51", "install.ps1", "-Only", "neovide")
    assert code == 0, out
    assert tomllib.loads(toml_path.read_text(encoding="utf-8"))["font"]["size"] == 12.0
    assert len(list(toml_path.parent.glob("config.toml.bak-*"))) == 1


# ── 기존 %LOCALAPPDATA%\nvim 보호 ───────────────────────────

@pytest.mark.parametrize("shell", SHELL_IDS)
def test_existing_real_folder_is_backed_up_not_deleted(shell, env):
    old = env.local / "nvim"
    (old / "lua").mkdir(parents=True)
    (old / "init.lua").write_bytes(b"-- old config\n")
    (old / "lua" / "x.lua").write_bytes(b"return 1\n")
    code, out = env.run(shell, "install.ps1", "-Only", "link", "-Yes")
    assert code == 0, out
    assert _steps(out) == {"link": "changed"}
    backups = list(env.local.glob("nvim.bak-*"))
    assert len(backups) == 1, out
    assert re.fullmatch(r"nvim\.bak-\d{8}-\d{6}", backups[0].name)
    assert (backups[0] / "init.lua").read_bytes() == b"-- old config\n"
    assert (backups[0] / "lua" / "x.lua").read_bytes() == b"return 1\n"
    assert old.is_junction() and _same_path(old, env.repo / "nvim")


@pytest.mark.parametrize("shell", SHELL_IDS)
def test_foreign_junction_is_renamed_and_its_target_kept(shell, env):
    other = env.base / "other-config"
    other.mkdir()
    (other / "init.lua").write_bytes(b"-- someone else's config\n")
    link = env.local / "nvim"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(other)], check=True, capture_output=True)
    code, out = env.run(shell, "install.ps1", "-Only", "link", "-Yes")
    assert code == 0, out
    backups = list(env.local.glob("nvim.bak-*"))
    assert len(backups) == 1 and backups[0].is_junction() and _same_path(backups[0], other), out
    assert (other / "init.lua").read_bytes() == b"-- someone else's config\n"
    assert _same_path(link, env.repo / "nvim")


def test_existing_folder_without_yes_is_left_alone(env):
    old = env.local / "nvim"
    old.mkdir()
    (old / "init.lua").write_bytes(b"-- old\n")
    code, out = env.run("ps51", "install.ps1", "-Only", "link")  # -NonInteractive: 질문에 답할 수 없음
    assert code == 1, out
    assert _steps(out) == {"link": "failed"}
    assert not old.is_junction() and (old / "init.lua").read_bytes() == b"-- old\n"
    assert not list(env.local.glob("nvim.bak-*"))


# ── uninstall ───────────────────────────────────────────────

@pytest.mark.parametrize("shell", SHELL_IDS)
def test_uninstall_removes_link_and_config_and_restores_backups(shell, env):
    old = env.local / "nvim"
    old.mkdir()
    (old / "init.lua").write_bytes(b"-- old\n")
    toml_path = env.roaming / "neovide" / "config.toml"
    toml_path.parent.mkdir(parents=True)
    toml_path.write_bytes(b"# mine\n")
    code, out = env.run(shell, "install.ps1", "-Only", "link,local,neovide", "-Yes", "-PreStart")
    assert code == 0, out
    repo_before = {k: v for k, v in env.snapshot().items() if k.startswith("repo dir/")}

    code, out = env.run(shell, "uninstall.ps1")
    assert code == 0, out
    assert _steps(out) == {"link": "changed", "neovide": "changed", "prestart": "ok", "purge": "skipped"}, out
    assert not old.is_junction() and (old / "init.lua").read_bytes() == b"-- old\n"
    assert not list(env.local.glob("nvim.bak-*"))
    assert toml_path.read_bytes() == b"# mine\n"
    assert not list(toml_path.parent.glob("config.toml.bak-*"))
    repo_after = {k: v for k, v in env.snapshot().items() if k.startswith("repo dir/")}
    assert repo_after == repo_before  # 정션 대상(저장소)과 local.lua는 그대로


def test_uninstall_leaves_foreign_things_alone(env):
    other = env.base / "other-config"
    other.mkdir()
    link = env.local / "nvim"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(other)], check=True, capture_output=True)
    toml_path = env.roaming / "neovide" / "config.toml"
    toml_path.parent.mkdir(parents=True)
    toml_path.write_bytes(b"# mine\n")
    before = env.snapshot()
    code, out = env.run("ps51", "uninstall.ps1", "-Purge")
    assert code == 0, out
    assert _steps(out) == {"link": "skipped", "neovide": "skipped", "prestart": "ok", "purge": "ok"}, out
    assert env.snapshot() == before


def test_uninstall_purge_deletes_only_locked_names(env):
    lazy = env.local / "nvim-data" / "lazy"
    for name in ("tokyonight.nvim", "unrelated.nvim"):
        (lazy / name / ".git").mkdir(parents=True)
        f = lazy / name / ".git" / "HEAD"
        f.write_bytes(b"0" * 40)
        os.chmod(f, 0o444)  # git 객체처럼 읽기 전용
    mason = env.local / "nvim-data" / "mason"
    (mason / "packages" / "stylua").mkdir(parents=True)
    (mason / "packages" / "stylua" / "mason-receipt.json").write_text(
        json.dumps({"links": {"bin": {"stylua.exe": "stylua.exe"}}}), encoding="utf-8")
    (mason / "packages" / "rust-analyzer").mkdir(parents=True)
    (mason / "bin").mkdir()
    (mason / "bin" / "stylua.exe").write_bytes(b"")
    (mason / "bin" / "rust-analyzer.exe").write_bytes(b"")
    # 정션이 들어 있어도 그 대상은 지우지 않는다
    outside = env.base / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_bytes(b"keep")
    subprocess.run(["cmd", "/c", "mklink", "/J", str(lazy / "tokyonight.nvim" / "j"), str(outside)],
                   check=True, capture_output=True)
    code, out = env.run("ps51", "uninstall.ps1", "-Purge")
    assert code == 0, out
    assert not (lazy / "tokyonight.nvim").exists()
    assert (lazy / "unrelated.nvim").exists()
    assert not (mason / "packages" / "stylua").exists() and not (mason / "bin" / "stylua.exe").exists()
    assert (mason / "packages" / "rust-analyzer").exists() and (mason / "bin" / "rust-analyzer.exe").exists()
    assert (outside / "keep.txt").read_bytes() == b"keep"


# ── 대역으로 확인하는 외부 명령 단계 ─────────────────────────

def test_plugins_tools_doctor_call_nvim_with_repo_config(env):
    code, out = env.run("ps51", "install.ps1", "-Only", "plugins,tools,doctor", "-Yes")
    calls = env.stub_calls()
    assert [c["name"] for c in calls] == ["nvim", "nvim", "nvim", "nvim"], out
    install, warm, mason, doctor = (c["argv"] for c in calls)
    assert install[0] == "--headless" and install[-1] == "+qa"
    assert "require('config.lazy').install()" in install[1] and "cquit 1" in install[1]
    assert "warm_all({ sync = true })" in warm[1]
    assert mason == ["--headless", "+MasonInstall lua-language-server@3.19.2 stylua@v2.3.0", "+qa"]
    assert doctor == ["--headless", "+lua require('config.health').cli()", "+qa"]
    for c in calls:
        assert c["XDG_CONFIG_HOME"] == str(env.repo)
        assert c["LOCALAPPDATA"] == str(env.local)
        # 깊은 경로에서 mini.nvim·fzf-lua 클론이 "Filename too long"으로 깨지지 않게
        assert (c["GIT_CONFIG_COUNT"], c["GIT_CONFIG_KEY_0"], c["GIT_CONFIG_VALUE_0"]) == ("1", "core.longpaths", "true")
    # 대역은 아무것도 설치하지 않으므로 단계가 그것을 알아챈다
    assert code == 1, out
    steps = _steps(out)
    assert steps["plugins"] == "failed" and "still not at the lockfile commit" in out
    assert steps["tools"] == "failed" and steps["doctor"] == "ok"


def test_plugins_step_puts_back_a_rewritten_lockfile(env):
    lock = env.repo / "nvim" / "lazy-lock.json"
    original = lock.read_bytes()
    code, out = env.run("ps51", "install.ps1", "-Only", "plugins", "-Yes", extra_env={"STUB_CLOBBER_LOCK": "1"})
    assert [c["name"] for c in env.stub_calls()] == ["nvim", "nvim"], out
    assert lock.read_bytes() == original, out
    assert "lazy rewrote lazy-lock.json; the committed version was put back" in out
    assert code == 1 and "lazy-lock.json restored" in out  # 대역은 설치하지 않으므로 여전히 실패


def test_plugins_failed_clone_marker_is_not_current(env):
    data = env.local / "nvim-data" / "lazy"
    for name, commit in LOCK_PLUGINS.items():
        (data / name / ".git").mkdir(parents=True)
        (data / name / ".git" / "HEAD").write_bytes(commit.encode() + b"\n")
    (data / "tokyonight.nvim.cloning").write_bytes(b"")  # lazy가 실패한 클론에 남기는 표지
    code, out = env.run("ps51", "install.ps1", "-Only", "plugins", "-WhatIf")
    assert code == 0, out
    assert "not at the lockfile commit: tokyonight.nvim" in out


def test_plugins_and_tools_already_current_run_nothing(env):
    data = env.local / "nvim-data"
    for name, commit in LOCK_PLUGINS.items():
        git = data / "lazy" / name / ".git"
        git.mkdir(parents=True)
        if name == "lazy.nvim":
            (git / "HEAD").write_bytes(commit.encode() + b"\n")
        else:  # 브랜치를 가리키는 HEAD + packed-refs
            (git / "HEAD").write_bytes(b"ref: refs/heads/main\n")
            (git / "packed-refs").write_bytes(b"# pack-refs\n" + commit.encode() + b" refs/heads/main\n")
    for name, version in TOOLS.items():
        pkg = data / "mason" / "packages" / name
        pkg.mkdir(parents=True)
        (pkg / "mason-receipt.json").write_text(json.dumps({"source": {"id": f"pkg:github/x/{name}@{version}"}}), encoding="utf-8")
    code, out = env.run("ps51", "install.ps1", "-Only", "plugins,tools")
    assert code == 0, out
    assert _steps(out) == {"plugins": "ok", "tools": "ok"}
    assert env.stub_calls() == []


@pytest.mark.parametrize("lock", [
    ["lua-language-server@3.19.2", "stylua@v2.3.0"],
    {"tools": {"lua-language-server": "3.19.2", "stylua": "v2.3.0"}},
    {"lua-language-server": {"version": "3.19.2"}, "stylua": {"version": "v2.3.0"}, "_comment": "x"},
], ids=["list", "nested", "objects"])
def test_tools_lock_formats(env, lock):
    (env.repo / "nvim" / "tools.lock.json").write_text(json.dumps(lock), encoding="utf-8")
    code, out = env.run("ps51", "install.ps1", "-Only", "tools", "-WhatIf")
    assert code == 0, out
    assert "+MasonInstall lua-language-server@3.19.2 stylua@v2.3.0" in out


def test_scoop_installs_only_missing_packages(env):
    code, out = env.run("ps51", "install.ps1", "-Only", "scoop", "-Yes")
    assert code == 0, out
    argv = [c["argv"] for c in env.stub_calls() if c["name"] == "scoop"]
    assert ["bucket", "add", "extras"] in argv
    assert ["install", "git"] in argv and ["install", "ripgrep"] in argv and ["install", "extras/neovide"] in argv
    assert ["install", "uv"] not in argv  # uv 대역이 PATH에 있다
    assert ["install", "neovim"] not in argv  # SCOOP\apps\neovim\current가 있다


def test_scoop_missing_fails_with_hint(env):
    (env.fakebin / "scoop.cmd").unlink()
    code, out = env.run("ps51", "install.ps1", "-Only", "scoop", "-Yes")
    assert code == 1
    assert "scoop is not installed" in out
    assert env.stub_calls() == []


def test_venv_whatif_plan(env):
    code, out = env.run("ps51", "install.ps1", "-Only", "venv", "-WhatIf", "-Offline", "-WheelsDir", str(env.base / "wheels"))
    assert code == 0, out
    assert f'venv --seed --python 3.14 "{env.repo / ".venv"}"' in out  # 공백 있는 경로는 따옴표로
    assert f"--no-index --find-links {env.base / 'wheels'}" in out
    assert f'-X utf8 "{env.repo / "scripts" / "kb.py"}" --help' in out


def test_venv_whatif_plan_online_first(env):
    code, out = env.run("ps51", "install.ps1", "-Only", "venv", "-WhatIf")
    assert code == 0, out
    assert re.search(r"(?m)^WHATIF venv .*python\.exe\" -m pip install -r .*requirements\.txt\"\r?$", out), out
    assert "WHATIF venv (only if the online install fails)" in out and "--no-index --find-links" in out


def _fake_venv(env: Env, version: str) -> None:
    """작업 트리 .venv의 런처(python.exe)와 pyvenv.cfg를 복사해 만든 가짜 .venv (설치하지 않는다)."""
    src = ROOT / ".venv"
    dst = env.repo / ".venv" / "Scripts"
    dst.mkdir(parents=True)
    shutil.copy(src / "Scripts" / "python.exe", dst / "python.exe")
    cfg = (src / "pyvenv.cfg").read_text(encoding="utf-8")
    cfg = re.sub(r"(?m)^(version(?:_info)?\s*=\s*)\S+", lambda m: m.group(1) + version, cfg)
    (env.repo / ".venv" / "pyvenv.cfg").write_text(cfg, encoding="utf-8")


def test_venv_existing_good_is_kept(env):
    _fake_venv(env, "3.14.0")
    code, out = env.run("ps51", "install.ps1", "-Only", "venv")
    assert code == 0, out
    assert _steps(out) == {"venv": "ok"} and "kb.py --help works" in out, out
    assert env.stub_calls() == []


def test_venv_wrong_version_without_yes_is_left_alone(env):
    _fake_venv(env, "3.15.2")
    before = env.snapshot()
    code, out = env.run("ps51", "install.ps1", "-Only", "venv")
    assert code == 1 and _steps(out) == {"venv": "failed"} and "3.15" in out, out
    assert env.snapshot() == before
    assert env.stub_calls() == []


# ── 로그온 사전 시작 바로 가기 ─────────────────────────────────

def _shortcut(path: Path) -> tuple[str, str, int]:
    command = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{path}'); "
               "[Console]::Out.WriteLine($s.TargetPath); [Console]::Out.WriteLine($s.Arguments); "
               "[Console]::Out.WriteLine($s.WindowStyle)")
    proc = subprocess.run([str(PS51), "-NoProfile", "-NonInteractive", "-Command", command],
                          capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
    target, arguments, style = proc.stdout.decode("utf-8", "replace").splitlines()[:3]
    return target, arguments, int(style)


@pytest.mark.parametrize("shell", SHELL_IDS)
def test_prestart_shortcut_create_rerun_remove(shell, env):
    lnk = env.startup / "work-kb-nvim-server.lnk"
    code, out = env.run(shell, "install.ps1", "-Only", "prestart")
    assert code == 0 and _steps(out) == {"prestart": "skipped"} and not lnk.exists(), out
    code, out = env.run(shell, "install.ps1", "-Only", "prestart", "-PreStart")
    assert code == 0 and _steps(out) == {"prestart": "changed"}, out
    target, arguments, style = _shortcut(lnk)
    assert target.lower() == str(PS51).lower()
    assert arguments == f'-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "{env.repo / "nvim" / "bin" / "nvim-server.ps1"}"'
    assert style == 7
    code, out = env.run(shell, "install.ps1", "-Only", "prestart", "-PreStart")
    assert code == 0 and _steps(out) == {"prestart": "ok"}, out
    code, out = env.run(shell, "install.ps1", "-Only", "prestart", "-NoPreStart")
    assert code == 0 and _steps(out) == {"prestart": "changed"} and not lnk.exists(), out


def test_uninstall_removes_prestart_shortcut(env):
    code, out = env.run("ps51", "install.ps1", "-Only", "prestart", "-PreStart")
    assert code == 0, out
    code, out = env.run("ps51", "uninstall.ps1")
    assert code == 0 and _steps(out)["prestart"] == "changed", out
    assert not (env.startup / "work-kb-nvim-server.lnk").exists()


# ── 실제 N4a·N4b 파일이 있으면 그것으로도 ─────────────────────

REAL_TEMPLATE = ROOT / "nvim" / "neovide" / "config.toml.in"
REAL_EXAMPLE = ROOT / "nvim" / "lua" / "local.example.lua"


@pytest.mark.parametrize("which", [w for w, p in (("template", REAL_TEMPLATE), ("example", REAL_EXAMPLE)) if p.exists()])
def test_real_config_files_work_with_installer(env, which):
    if which == "template":
        shutil.copy(REAL_TEMPLATE, env.repo / "nvim" / "neovide" / "config.toml.in")
        code, out = env.run("ps51", "install.ps1", "-Only", "neovide", "-FontSize", "14")
        assert code == 0, out
        data = tomllib.loads((env.roaming / "neovide" / "config.toml").read_text(encoding="utf-8"))
        assert data["neovim-bin"] == str(env.real_nvim)
        assert data["font"]["size"] == 14.0
    else:
        shutil.copy(REAL_EXAMPLE, env.repo / "nvim" / "lua" / "local.example.lua")
        code, out = env.run("ps51", "install.ps1", "-Only", "local", "-Yes", "-VaultPath", str(env.vault))
        assert code == 0, out
        assert f"vault = [[{env.vault}]]" in (env.repo / "nvim" / "lua" / "local.lua").read_text(encoding="utf-8")


# ── nvim/capture.ahk (AutoHotkey v2; 이 PC에는 AutoHotkey가 없어 실행은 확인하지 않는다) ──

def test_capture_ahk_shape():
    text = AHK.read_bytes().decode("utf-8")
    lines = text.splitlines()
    assert lines[0].strip() == "#Requires AutoHotkey v2.0"
    assert "실행해 보지 못했다" in text and "미확인" in text
    assert re.search(r'(?m)^\^!\+c::Capture\("WorkKb capture"\)$', text)
    assert re.search(r'(?m)^\^!\+v::Capture\("WorkKb capture-clipboard-image"\)$', text)
    assert not re.search(r"(?m)^[\^!+]*#[\^!+#]*\w+::", text)  # Win 조합 없음(D13)
    assert not re.search(r"(?m)^\s*(Run|MsgBox|Send|WinActivate|RunWait),", text)  # v1 명령 문법 없음
    assert 'Pipe := "\\\\.\\pipe\\work-kb-nvim"' in text
    assert "--headless --server" in text and "--remote-send" in text and "<C-\\><C-N>:" in text
    assert 'A_ScriptDir "\\bin\\neovide-attach.ps1"' in text
    code = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith(";")]
    setting = next(i for i, ln in enumerate(code) if ln.startswith("Nvim := "))
    hotkeys = [i for i, ln in enumerate(code) if "::" in ln]
    assert setting < min(hotkeys)


def test_capture_ahk_pipe_matches_launchers_and_installer():
    for path in (BIN / "nvim-server.ps1", BIN / "neovide-attach.ps1", INSTALL):
        assert "'\\\\.\\pipe\\work-kb-nvim'" in path.read_text(encoding="ascii"), path.name


def test_capture_ahk_commands_exist_in_plugin():
    init = (ROOT / "nvim" / "dev" / "work-kb" / "lua" / "work-kb" / "init.lua").read_text(encoding="utf-8")
    names = set(re.findall(r'name = "([a-z-]+)"', init))
    for command in re.findall(r'Capture\("WorkKb ([a-z-]+)"\)', AHK.read_text(encoding="utf-8")):
        assert command in names, command
