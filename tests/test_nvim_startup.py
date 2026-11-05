"""Neovim 설정(nvim/)의 시작 비용과 시작 때 불러오는 것 (설계 F절).

- 환경: XDG_CONFIG_HOME=<저장소>(nvim/이 설정, 정션 없이), XDG_STATE_HOME=<tmp>. 플러그인은 실제
  데이터 폴더(%LOCALAPPDATA%\\nvim-data\\lazy)의 것을 쓴다. NVIM*·VIM*·KB_* 환경변수는 지운다.
- 구조 검사(결정적): 시작 직후(+명령이 도는 때, 첫 화면 전) 불러온 플러그인은 lazy.nvim과 색 테마뿐이다.
  markdown 파일을 열어도 같다 (render-markdown은 vim.schedule로 첫 화면 뒤에 불러온다 — 설계 K절의
  대안; 불러온 뒤 붙는지는 tests/nvim/config_spec/markdown_spec.lua가 본다). markdown 버퍼는 treesitter로
  칠하고 b:current_syntax가 없다. fzf-lua·blink.cmp·which-key·work-kb 모듈은 없다.
  --startuptime 기록에 끈 내장 플러그인, syntax/markdown.vim, syntax/html.vim이 없다.
- 상대 검사(기본): 설정 시작 시간의 중앙값 ≤ --clean 중앙값 + 20ms (`+q`, markdown 파일 각각).
  준비 2회는 버리고 9회를 --clean과 번갈아 잰다. 넘으면 18회로 한 번 다시 재고 그래도 넘으면 실패.
  모든 표본을 실패 메시지에 넣는다. 시간은 --startuptime의 `--- NVIM STARTED ---` 줄의 시각.
- 절대 검사(@pytest.mark.perf, 기본 실행에서 빠짐): `+q` ≤ 140ms, markdown ≤ 150ms.
- 플러그인이 설치되지 않았으면(lazy.nvim 없음) 실패한다. KB_SKIP_NVIM=1일 때만 건너뛴다.
- nvim은 PATH에서 찾고, scoop shim이면 .shim 파일이 가리키는 실제 nvim.exe를 쓴다 (shim 비용 ~18ms 제외).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import statistics
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "nvim"
MD_FILE = ROOT / "tests" / "fixtures" / "notes" / "daily" / "2026-11-04.md"
SKIP_NVIM = os.environ.get("KB_SKIP_NVIM") == "1"
LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")

pytestmark = pytest.mark.skipif(SKIP_NVIM, reason="KB_SKIP_NVIM=1")

MARGIN_MS = 20.0
WARMUP = 2
RUNS = 9
RERUNS = 18
ABS_Q_MS = 140.0
ABS_MD_MS = 150.0

# lazy.lua의 disabled_plugins (runtime/plugin/<이름>.vim|lua)
DISABLED = ["gzip", "tarPlugin", "zipPlugin", "netrwPlugin", "tutor", "rplugin", "man",
            "spellfile", "shada", "osc52", "matchit"]
FORBIDDEN_MODULES = ["fzf-lua", "blink.cmp", "which-key", "work-kb"]

STARTED_RE = re.compile(r"^(\d+\.\d+)\s+\d+\.\d+: --- NVIM STARTED ---$", re.MULTILINE)

PROBE = r"""
local plugins = {}
for name, p in pairs(require('lazy.core.config').plugins) do
  if p._.loaded then plugins[#plugins + 1] = name end
end
table.sort(plugins)
local mods = {}
for name in pairs(package.loaded) do mods[#mods + 1] = name end
table.sort(mods)
io.stdout:write('PROBE ' .. vim.json.encode({
  plugins = plugins, modules = mods, ft = vim.bo.filetype,
  ts = vim.treesitter.highlighter.active[vim.api.nvim_get_current_buf()] ~= nil,
  syntax = vim.b.current_syntax or vim.NIL,
}) .. '\n')
"""


def _real_nvim() -> str:
    found = shutil.which("nvim")
    if found is None:
        pytest.fail("nvim을 PATH에서 찾지 못했습니다 (건너뛰려면 KB_SKIP_NVIM=1)")
    shim = Path(found).with_suffix(".shim")
    if shim.is_file():
        m = re.search(r'path\s*=\s*"([^"]+)"', shim.read_text(encoding="utf-8", errors="replace"))
        if m and Path(m.group(1)).is_file():
            return m.group(1)
    return found


def _env(tmp_path: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("NVIM", "VIM", "KB_", "MYVIMRC", "XDG_"))}
    env["XDG_CONFIG_HOME"] = str(ROOT)
    env["XDG_STATE_HOME"] = str(tmp_path / "state")
    return env


@pytest.fixture(scope="module")
def setup(tmp_path_factory):
    lazy_dir = Path(LOCALAPPDATA) / "nvim-data" / "lazy" / "lazy.nvim"
    if not lazy_dir.is_dir():
        pytest.fail(f"플러그인이 설치되지 않았습니다: {lazy_dir} (install.ps1 -Only plugins)")
    tmp = tmp_path_factory.mktemp("nvim-startup")
    return {"nvim": _real_nvim(), "env": _env(tmp), "tmp": tmp}


def _run(setup, args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [setup["nvim"], "--headless", *args],
        cwd=str(ROOT), env=setup["env"], stdin=subprocess.DEVNULL, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )


def _startup_ms(setup, clean: bool, md: bool, log: Path) -> float:
    if log.exists():
        log.unlink()
    args = (["--clean"] if clean else []) + ["--startuptime", str(log)]
    if md:
        args.append(str(MD_FILE))
    result = _run(setup, args + ["+q"])
    assert result.returncode == 0, result.stdout + result.stderr
    text = log.read_text(encoding="utf-8", errors="replace")
    found = STARTED_RE.findall(text)
    assert found, text[-2000:]
    return float(found[0])


def _sample(setup, md: bool, runs: int) -> tuple[list[float], list[float]]:
    log = setup["tmp"] / "startuptime.log"
    for _ in range(WARMUP):
        _startup_ms(setup, True, md, log)
        _startup_ms(setup, False, md, log)
    clean, config = [], []
    for _ in range(runs):
        clean.append(_startup_ms(setup, True, md, log))
        config.append(_startup_ms(setup, False, md, log))
    return clean, config


def _probe(setup, md: bool) -> dict:
    script = setup["tmp"] / "probe.lua"
    script.write_text(PROBE, encoding="utf-8")
    args = [str(MD_FILE)] if md else []
    result = _run(setup, args + [f"+luafile {script.as_posix()}", "+qa!"])
    out = result.stdout + result.stderr
    m = re.search(r"^PROBE (\{.*\})$", out, re.MULTILINE)
    assert result.returncode == 0 and m, out
    return json.loads(m.group(1))


def _fmt(values: list[float]) -> str:
    return "[" + ", ".join(f"{v:.1f}" for v in values) + "]"


@pytest.mark.skipif(os.name != "nt", reason="Windows 설정")
def test_only_lazy_and_colorscheme_before_first_paint(setup):
    state = _probe(setup, md=False)
    assert state["plugins"] == ["lazy.nvim", "tokyonight.nvim"], state["plugins"]
    for mod in FORBIDDEN_MODULES:
        assert not any(m == mod or m.startswith(mod + ".") for m in state["modules"]), mod
    print(f"+q: {state['plugins']}")


@pytest.mark.skipif(os.name != "nt", reason="Windows 설정")
def test_markdown_starts_treesitter_and_defers_render_markdown(setup):
    # render-markdown은 첫 화면 뒤(vim.schedule)에 불러온다: +명령이 도는 때에는 아직 없다.
    state = _probe(setup, md=True)
    assert state["plugins"] == ["lazy.nvim", "tokyonight.nvim"], state["plugins"]
    for mod in FORBIDDEN_MODULES + ["render-markdown"]:
        assert not any(m == mod or m.startswith(mod + ".") for m in state["modules"]), mod
    assert state["ft"] == "markdown"
    assert state["ts"] is True
    assert state["syntax"] is None, state["syntax"]
    print(f"md: {state['plugins']}")


@pytest.mark.skipif(os.name != "nt", reason="Windows 설정")
def test_startuptime_log_has_no_disabled_builtins_or_regex_markdown(setup):
    log = setup["tmp"] / "structure.log"
    _startup_ms(setup, False, True, log)
    text = log.read_text(encoding="utf-8", errors="replace").replace("\\", "/")
    for name in DISABLED:
        assert not re.search(rf"/plugin/{re.escape(name)}\.(vim|lua)\b", text), name
    assert not re.search(r"/syntax/markdown\.vim", text), "syntax/markdown.vim을 읽음"
    assert "/syntax/html.vim" not in text


def _check_relative(setup, md: bool):
    label = "markdown" if md else "+q"
    clean, config = _sample(setup, md, RUNS)
    c, k = statistics.median(clean), statistics.median(config)
    if k > c + MARGIN_MS:
        clean, config = _sample(setup, md, RERUNS)
        c, k = statistics.median(clean), statistics.median(config)
    message = f"{label}: clean 중앙값 {c:.1f}ms {_fmt(clean)}; 설정 중앙값 {k:.1f}ms {_fmt(config)}; 차이 {k - c:+.1f}ms"
    print(message)
    assert k <= c + MARGIN_MS, message


@pytest.mark.skipif(os.name != "nt", reason="Windows 설정")
def test_startup_q_within_clean_plus_margin(setup):
    _check_relative(setup, md=False)


@pytest.mark.skipif(os.name != "nt", reason="Windows 설정")
def test_startup_markdown_within_clean_plus_margin(setup):
    _check_relative(setup, md=True)


@pytest.mark.perf
@pytest.mark.skipif(os.name != "nt", reason="Windows 설정")
@pytest.mark.parametrize(("md", "budget"), [(False, ABS_Q_MS), (True, ABS_MD_MS)], ids=["q", "markdown"])
def test_startup_absolute_budget(setup, md, budget):
    _, config = _sample(setup, md, RUNS)
    k = statistics.median(config)
    message = f"{'markdown' if md else '+q'}: 설정 중앙값 {k:.1f}ms {_fmt(config)} (예산 {budget:.0f}ms)"
    print(message)
    assert k <= budget, message
