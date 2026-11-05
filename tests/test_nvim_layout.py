"""Neovim 설정 폴더(nvim/)의 모양 (설계 C·J절). nvim을 돌리지 않는 정적 검사.

- 필요한 파일이 있다. lazy-lock.json·tools.lock.json은 올바른 JSON, Neovide 틀은 표지를 바꾸면 TOML로 읽힌다.
- nvim/ 아래(work-kb 플러그인 포함) 텍스트 파일은 UTF-8, BOM 없음, LF만 (.gitattributes `* -text`).
- lua/local.lua는 .gitignore가 덮고 예시는 덮지 않는다.
- 설정은 <leader>k* 키를 직접 정하지 않는다 (work-kb 플러그인 몫, 설계 A1).
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NVIM = ROOT / "nvim"

REQUIRED = [
    "init.lua",
    "lazy-lock.json",
    "tools.lock.json",
    "ftplugin/markdown.lua",
    "after/ftplugin/markdown.lua",
    "lua/local.example.lua",
    "neovide/config.toml.in",
    *[f"lua/config/{name}.lua" for name in (
        "options", "lazy", "autocmds", "keymaps", "settings", "background", "util", "health", "neovide", "ime",
    )],
    *[f"lua/plugins/{name}.lua" for name in (
        "colorscheme", "editor", "picker", "git", "lsp", "completion", "format", "markdown", "treesitter", "work-kb",
    )],
    "dev/work-kb/plugin/work-kb.lua",
    "dev/work-kb/lua/work-kb/init.lua",
]
TEXT_SUFFIXES = {".lua", ".json", ".in", ".toml", ".ps1", ".ahk", ".md", ".vim"}


@pytest.mark.parametrize("rel", REQUIRED)
def test_required_file_exists(rel):
    path = NVIM / rel
    assert path.is_file(), rel
    assert path.stat().st_size > 0, rel


def test_lazy_lock_is_valid_json_with_commits():
    lock = json.loads((NVIM / "lazy-lock.json").read_bytes().decode("utf-8"))
    assert lock and "work-kb" not in lock
    for name, entry in lock.items():
        assert re.fullmatch(r"[0-9a-f]{40}", entry["commit"]), name
        assert entry["branch"], name
    assert "lazy.nvim" in lock and "tokyonight.nvim" in lock


def test_tools_lock_is_valid_json():
    tools = json.loads((NVIM / "tools.lock.json").read_bytes().decode("utf-8"))["tools"]
    assert set(tools) == {"lua-language-server", "pyright", "ruff", "stylua"}
    assert all(isinstance(v, str) and re.match(r"v?\d+\.\d+", v) for v in tools.values()), tools


def test_neovide_template_parses_as_toml():
    text = (NVIM / "neovide" / "config.toml.in").read_bytes().decode("utf-8")
    assert "@NVIM_EXE@" in text
    data = tomllib.loads(text.replace("@NVIM_EXE@", r"C:\Users\x\scoop\apps\neovim\current\bin\nvim.exe"))
    assert data["neovim-bin"].endswith("nvim.exe")
    assert data["font"]["normal"]


def _text_files():
    for path in sorted(NVIM.rglob("*")):
        if path.is_file() and path.suffix in TEXT_SUFFIXES and path.name != "local.lua":
            yield path


@pytest.mark.parametrize("path", list(_text_files()), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_text_files_are_utf8_lf_without_bom(path):
    data = path.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf"), "BOM"
    assert b"\r" not in data, "CR"
    data.decode("utf-8")


def test_local_lua_is_ignored_and_example_is_not():
    lines = [ln.strip() for ln in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
    assert "/nvim/lua/local.lua" in lines
    assert not any(ln.endswith("local.example.lua") for ln in lines)


def test_config_defines_no_work_kb_keys():
    offenders = []
    for path in list((NVIM / "lua" / "config").glob("*.lua")) + list((NVIM / "lua" / "plugins").glob("*.lua")):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"""['"]<leader>k[^'"]*['"]""", text):
            offenders.append((path.name, m.group(0)))
    # 허용: work-kb.lua의 접두 자리 표시·prefix 값, which-key 그룹 이름
    allowed = {("work-kb.lua", "'<leader>k'"), ("editor.lua", "'<leader>k'")}
    assert set(offenders) <= allowed, offenders
