"""Phase 6 포장: `kb.py wheels`와 `scripts/deploy_check.py` (W6.3, D02·D34·D60).

- 표시 없는 테스트는 네트워크 없이 돈다(pip는 가짜로 바꾸고, 복사 점검은 임시 폴더).
- `packaging` 표시 테스트만 실제로 내려받는다(KB_ALLOW_NETWORK=1을 하위 프로세스에만 준다):
  cp314 wheel 묶음 → 한글 경로의 새 uv venv(3.14)에 `--no-index` 설치 → kb.py 사전 점검 통과.
  Python 3.15가 있으면 PyYAML cp315 wheel 빌드를 시도해 결과를 출력만 한다(실패해도 테스트는 통과).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import deploy_check
import wheels
from lib import core

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
REQUIRED_PACKAGES = ("requests", "pyyaml", "markdown", "keyring", "pillow")


def _child_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("KB_") and k not in ("PYTHONPATH", "PYTHONHOME")}
    env["PYTHONUTF8"] = "1"
    env.update(extra)
    return env


def _run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None, timeout: int = 600):
    return subprocess.run(
        [str(c) for c in command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd or ROOT),
        env=env or _child_env(),
        timeout=timeout,
    )


# ================================================================ wheels: 순수 함수


def test_parse_targets():
    assert wheels.parse_targets("3.13,3.14") == ["3.13", "3.14"]
    assert wheels.parse_targets(" 3.14 , 3.14,") == ["3.14"]
    for bad in ("", "3.12", "314", "3.x", "2.7", "3.14.1"):
        with pytest.raises(core.ConfigError):
            wheels.parse_targets(bad)


def test_download_command_has_the_d02_flags(tmp_path):
    req = tmp_path / "requirements.txt"
    command = wheels.download_command("py.exe", "3.14", req, tmp_path / "w")
    assert command[:4] == ["py.exe", "-m", "pip", "download"]
    joined = " ".join(command)
    for piece in (
        "--only-binary=:all:",
        "--platform win_amd64",
        "--implementation cp",
        "--python-version 3.14",
        "--abi cp314",
        f"-r {req}",
        f"-d {tmp_path / 'w'}",
    ):
        assert piece in joined
    sdist = wheels.sdist_command("py.exe", "pyyaml==6.0.3", tmp_path / "w" / "sdist")
    assert sdist[sdist.index("--no-binary") + 1] == "pyyaml"
    assert "--no-deps" in sdist and "pyyaml==6.0.3" in sdist


@pytest.mark.parametrize(
    ("filename", "target", "ok"),
    [
        ("pyyaml-6.0.3-cp314-cp314-win_amd64.whl", "3.14", True),
        ("pyyaml-6.0.3-cp314-cp314-win_amd64.whl", "3.13", False),
        ("PyYAML-6.0.1-cp313-cp313-win_amd64.whl", "3.13", True),
        ("pillow-13.0.0-cp314-cp314t-win_amd64.whl", "3.14", False),  # free-threaded
        ("pillow-13.0.0-cp314-cp314-win32.whl", "3.14", False),
        ("pillow-13.0.0-cp314-cp314-manylinux_2_28_x86_64.whl", "3.14", False),
        ("requests-2.34.2-py3-none-any.whl", "3.13", True),
        ("x-1.0-py2.py3-none-any.whl", "3.14", True),
        ("x-1.0-1-py3-none-any.whl", "3.14", True),  # build tag
        ("cryptography-46.0.0-cp311-abi3-win_amd64.whl", "3.13", True),
        ("x-1.0-cp315-abi3-win_amd64.whl", "3.14", False),
        ("x-1.0-py315-none-any.whl", "3.14", False),
        ("x-1.0-cp314-none-win_amd64.whl", "3.14", True),
    ],
)
def test_wheel_compatibility(filename, target, ok):
    wheel = wheels.parse_wheel(filename)
    assert wheel is not None
    assert wheels.compatible(wheel, target) is ok


def test_parse_wheel_names_are_normalized():
    assert wheels.parse_wheel("jaraco.classes-3.4.0-py3-none-any.whl").name == "jaraco-classes"
    assert wheels.parse_wheel("PyYAML-6.0.1-cp313-cp313-win_amd64.whl").name == "pyyaml"
    assert wheels.parse_wheel("pyyaml-6.0.3.tar.gz") is None
    assert wheels.parse_wheel("bad.whl") is None


def test_requirement_names_reads_repo_file(tmp_path):
    assert wheels.requirement_names(ROOT / "requirements.txt") == list(REQUIRED_PACKAGES)
    req = tmp_path / "r.txt"
    req.write_text("# 주석\n-r other.txt\nPyYAML>=6 ; python_version>='3.13'\n\nrequests[socks]==2.3  # x\n", encoding="utf-8")
    assert wheels.requirement_names(req) == ["pyyaml", "requests"]


# ================================================================ wheels: 가짜 pip로 main 실행

CP314_SET = [
    "pyyaml-6.0.3-cp314-cp314-win_amd64.whl",
    "pillow-13.0.0-cp314-cp314-win_amd64.whl",
    "requests-2.34.2-py3-none-any.whl",
    "markdown-3.11.2-py3-none-any.whl",
    "keyring-25.7.0-py3-none-any.whl",
]
CP313_SET = ["pyyaml-6.0.3-cp313-cp313-win_amd64.whl", "pillow-13.0.0-cp313-cp313-win_amd64.whl"]


class FakePip:
    def __init__(self, *, fail_sdist: bool = False, skip: tuple[str, ...] = ()):
        self.calls: list[list[str]] = []
        self.fail_sdist = fail_sdist
        self.skip = skip

    def __call__(self, command, cwd=None, **kwargs):
        self.calls.append(list(command))
        assert command[1:4] == ["-m", "pip", "download"]
        dest = Path(command[command.index("-d") + 1])
        dest.mkdir(parents=True, exist_ok=True)
        if "--no-binary" in command:
            if self.fail_sdist:
                return subprocess.CompletedProcess(command, 1)
            version = command[-4].split("==")[1] if "==" in command[-4] else "0"
            (dest / f"pyyaml-{version}.tar.gz").write_bytes(b"sdist")
            return subprocess.CompletedProcess(command, 0)
        target = command[command.index("--python-version") + 1]
        names = CP314_SET if target == "3.14" else CP313_SET + CP314_SET[2:]
        for name in names:
            if not name.startswith(self.skip):
                (dest / name).write_bytes(name.encode("utf-8"))
        return subprocess.CompletedProcess(command, 0)


def test_dry_run_prints_commands_and_downloads_nothing(tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise AssertionError("dry-run이 pip를 실행했다")

    monkeypatch.setattr(wheels.subprocess, "run", boom)
    dest = tmp_path / "한글 휠"
    assert wheels.main(["--dry-run", "--dir", str(dest)]) == 0
    out = capsys.readouterr().out
    assert out.count("pip download") == 3  # 3.13, 3.14, sdist
    assert "--abi cp313" in out and "--abi cp314" in out and "--no-binary pyyaml" in out
    assert not dest.exists()


def test_main_downloads_per_target_and_writes_manifest(tmp_path, monkeypatch, capsys):
    pip = FakePip()
    monkeypatch.setattr(wheels.subprocess, "run", pip)
    dest = tmp_path / "한글 경로 (테스트)" / "wheels"
    assert wheels.main(["--dir", str(dest)]) == 0
    assert [c[c.index("--abi") + 1] for c in pip.calls if "--abi" in c] == ["cp313", "cp314"]
    assert all(c[0] == sys.executable for c in pip.calls)
    assert "pyyaml==6.0.3" in pip.calls[-1]  # sdist는 wheel과 같은 판
    manifest = (dest / "MANIFEST.txt").read_text(encoding="utf-8")
    sections = _sections(manifest)
    assert "pyyaml-6.0.3-cp314-cp314-win_amd64.whl" in sections["cp314"]
    assert "pyyaml-6.0.3-cp314-cp314-win_amd64.whl" not in sections["cp313"]
    assert "pyyaml-6.0.3-cp313-cp313-win_amd64.whl" in sections["cp313"]
    assert "sdist/pyyaml-6.0.3.tar.gz" in sections["sdist"]
    assert "누락" not in manifest
    assert re.search(r"^[0-9a-f]{64}  \d+  sdist/pyyaml-6\.0\.3\.tar\.gz$", manifest, re.M)
    out = capsys.readouterr().out
    assert "[cp314]" in out and str(dest / "MANIFEST.txt") in out


def test_missing_package_or_sdist_failure_exits_1(tmp_path, monkeypatch):
    monkeypatch.setattr(wheels.subprocess, "run", FakePip(skip=("keyring",), fail_sdist=True))
    dest = tmp_path / "w"
    with pytest.raises(core.KbError) as info:
        wheels.main(["--targets", "3.14", "--dir", str(dest)])
    assert info.value.exit_code == 1
    assert "keyring" in str(info.value) and "sdist" in str(info.value)
    manifest = (dest / "MANIFEST.txt").read_text(encoding="utf-8")
    assert "누락: keyring" in manifest  # 실패해도 목록은 남긴다


def test_wheels_bad_target_is_config_error(tmp_path):
    with pytest.raises(core.ConfigError):
        wheels.main(["--targets", "3.12", "--dir", str(tmp_path)])


def test_wheels_refused_for_agent(run_kb):
    result = run_kb("wheels", "--dry-run", env={"KB_AGENT": "1"})
    assert result.returncode == 7


def test_wheels_cli_dry_run(run_kb):
    result = run_kb("wheels", "--dry-run", "--targets", "3.14", "--no-sdist")
    assert result.returncode == 0, result.stderr
    assert "--python-version 3.14" in result.stdout and "--no-binary" not in result.stdout


def _sections(manifest: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current = None
    for line in manifest.splitlines():
        match = re.match(r"^\[(\w+)\]", line)
        if match:
            current = match.group(1)
            sections[current] = ""
        elif current:
            sections[current] += line + "\n"
    return sections


# ================================================================ deploy_check

KEEP = [
    "scripts/kb.py",
    "scripts/lib/core.py",
    "scripts/lib/__init__.py",
    "config.yaml",
    "config.local.example.yaml",
    "requirements.txt",
    "AGENTS.md",
    "CLAUDE.md",
    ".gitignore",
    "emacs/work-kb.el",
    "obsidian/setup.md",
    "prompts/tag.md",
    "docs/DEPLOY.md",
    "daily/.gitkeep",
    "inbox/.gitkeep",
    "raw/assets/.gitkeep",
    "jira/_views/.gitkeep",
    "jira/_views/일감.base",
    "wiki/엔티티.base",
    "wiki/people/.gitkeep",
    "wheels/pyyaml-6.0.3-cp314-cp314-win_amd64.whl",
    "wheels/sdist/pyyaml-6.0.3.tar.gz",
    "wheels/MANIFEST.txt",
]
DROP = [
    "config.local.yaml",
    ".env",
    ".git/HEAD",
    ".venv/Scripts/python.exe",
    ".venv313/pyvenv.cfg",
    ".state/kb.lock",
    ".state/logs/kb.log",
    "_trash/old.md",
    "examples/sample-vault/daily/2026-11-01.md",
    "tests/test_x.py",
    "tests/fixtures/a.json",
    "scripts/__pycache__/kb.cpython-314.pyc",
    "scripts/lib/__pycache__/core.cpython-313.pyc",
    ".pytest_cache/v/cache",
    ".obsidian/workspace.json",
    "daily/2026-11-05.md",
    "daily/2026-11-05.md~",
    "inbox/20261102-1430-회의 메모.md",
    "inbox/화이트보드.png",
    "raw/assets/2026-11-05-1420-메일캡처.png",
    "raw/원문.md",
    "jira/KB-12.md",
    "jira/outbox.jsonl",
    "jira/.cache.sqlite",
    "jira/.cache.sqlite-wal",
    "jira/_views/내 일감.md",
    "wiki/index.md",
    "wiki/decisions.md",
    "wiki/_lint.md",
    "wiki/projects/KB.md",
    "wiki/people/김철수.md",
    "wiki/topics/캐시 설계.md",
    "docs/.README.md.1234.tmp",
]


@pytest.fixture
def fake_repo(tmp_path) -> Path:
    repo = tmp_path / "집 저장소" / "work-kb"
    for rel in KEEP + DROP:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"내용 {rel}\n".encode("utf-8"))
    return repo


@pytest.mark.parametrize("rel", DROP)
def test_excluded_rule_drops(rel):
    assert deploy_check.excluded(rel, False)


@pytest.mark.parametrize("rel", KEEP)
def test_excluded_rule_keeps(rel):
    assert not deploy_check.excluded(rel, False)


def test_excluded_dirs():
    for rel in (".git", ".venv", ".venv313", ".state", "_trash", "examples", "tests", "scripts/__pycache__", ".obsidian"):
        assert deploy_check.excluded(rel, True), rel
    for rel in ("daily", "inbox", "raw", "raw/assets", "jira", "jira/_views", "wiki", "wiki/people", "wheels", "wheels/sdist", "scripts/lib"):
        assert not deploy_check.excluded(rel, True), rel


def test_copy_into_hangul_path(fake_repo, tmp_path, capsys):
    dest = tmp_path / "한글 경로 (테스트)" / "work-kb"
    assert deploy_check.main([str(dest), "--source", str(fake_repo)]) == 0
    for rel in KEEP:
        assert (dest / rel).is_file(), rel
    for rel in DROP:
        assert not (dest / rel).exists(), rel
    for rel in ("daily", "inbox", "raw/assets", "jira/_views", "wiki/topics", "wiki/projects"):
        assert (dest / rel).is_dir(), rel  # 빈 폴더 구조는 남는다
    for rel in (".git", ".venv", ".state", "_trash", "examples", "tests", "scripts/__pycache__"):
        assert not (dest / rel).exists(), rel
    out = capsys.readouterr().out
    assert f"파일 {len(KEEP)}개" in out
    assert "✓ 제외 대상 없음" in out
    assert (dest / "jira/_views/일감.base").read_bytes() == (fake_repo / "jira/_views/일감.base").read_bytes()


def test_destination_must_be_new_or_empty_and_outside(fake_repo, tmp_path):
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "x.txt").write_bytes(b"x")
    for dest in (busy, fake_repo / "copy", fake_repo, fake_repo.parent):
        with pytest.raises(core.ConfigError):
            deploy_check.main([str(dest), "--source", str(fake_repo)])
    empty = tmp_path / "empty"
    empty.mkdir()
    assert deploy_check.main([str(empty), "--source", str(fake_repo)]) == 0
    with pytest.raises(core.ConfigError):
        deploy_check.main([str(tmp_path / "x"), "--source", str(tmp_path)])  # 저장소가 아님


def test_check_finds_forbidden_and_missing_independently(fake_repo, tmp_path):
    dest = tmp_path / "copy"
    deploy_check.copy_tree(fake_repo, dest)
    assert deploy_check.find_forbidden(dest) == []
    assert deploy_check.find_missing(dest) == []
    planted = ["config.local.yaml", "jira/.cache.sqlite", "daily/2026-11-05.md", "wiki/projects/KB.md", "scripts/__pycache__/a.pyc", ".venv/x", "raw/assets/a.png"]
    for rel in planted:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        (dest / rel).write_bytes(b"x")
    (dest / "AGENTS.md").unlink()
    found = deploy_check.find_forbidden(dest)
    for rel in ("config.local.yaml", "jira/.cache.sqlite", "daily/2026-11-05.md", "wiki/projects/KB.md", "scripts/__pycache__", ".venv", "raw/assets/a.png"):
        assert rel in found, (rel, found)
    assert "daily/.gitkeep" not in found and "jira/_views/일감.base" not in found
    assert deploy_check.find_missing(dest) == ["AGENTS.md"]


def test_cli_copies_real_repo_and_copy_runs(tmp_path):
    """실제 저장소를 한글·공백 경로로 복사 → 복사본의 kb.py가 돌고, demo는 집 전용이라 종료 4."""
    dest = tmp_path / "한글 경로 (테스트)" / "work-kb"
    result = _run([sys.executable, "-X", "utf8", SCRIPTS / "deploy_check.py", dest])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "✓ 제외 대상 없음" in result.stdout
    assert re.search(r"파일 \d+개, [\d.]+ (KB|MB)", result.stdout)
    for rel in ("tests", "examples", ".venv", ".state", "config.local.yaml", "_trash"):
        assert not (dest / rel).exists(), rel
    help_ = _run([sys.executable, "-X", "utf8", dest / "scripts" / "kb.py", "--help"], cwd=dest)
    assert help_.returncode == 0, help_.stderr
    assert "wheels" in help_.stdout
    demo = _run([sys.executable, "-X", "utf8", dest / "scripts" / "kb.py", "demo"], cwd=dest, env=_child_env(KB_ROOT=str(dest)))
    assert demo.returncode == 4, demo.stdout + demo.stderr
    assert "examples" in demo.stderr


# ================================================================ packaging: 실제 내려받기


@pytest.fixture(scope="module")
def downloaded(tmp_path_factory) -> Path:
    dest = tmp_path_factory.mktemp("wheels") / "한글 휠 (테스트)"
    result = _run(
        [sys.executable, "-X", "utf8", SCRIPTS / "kb.py", "wheels", "--targets", "3.14", "--dir", dest],
        env=_child_env(KB_ALLOW_NETWORK="1"),
        timeout=900,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    return dest


@pytest.mark.packaging
def test_manifest_lists_cp314_wheels(downloaded):
    manifest = (downloaded / "MANIFEST.txt").read_text(encoding="utf-8")
    section = _sections(manifest)["cp314"]
    names = {}
    for line in section.splitlines():
        parts = line.split()
        if len(parts) == 3:
            names[parts[0]] = parts[2]
    for package in REQUIRED_PACKAGES:
        assert package in names, (package, section)
        assert (downloaded / names[package]).is_file()
    assert names["pyyaml"].endswith("-cp314-cp314-win_amd64.whl")
    assert names["pillow"].endswith("-cp314-cp314-win_amd64.whl")
    assert list((downloaded / "sdist").glob("pyyaml-*.tar.gz"))
    assert "누락" not in manifest


@pytest.mark.packaging
def test_offline_install_into_fresh_venv_under_hangul_path(downloaded, tmp_path):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv가 없어 새 3.14 venv를 만들 수 없음")
    venv = tmp_path / "한글 경로 (테스트)" / "venv"
    made = _run([uv, "venv", "--seed", "--managed-python", "--python", "3.14", venv], env=_child_env(UV_PYTHON_NO_REGISTRY="1", KB_ALLOW_NETWORK="1"))
    assert made.returncode == 0, made.stderr
    python = venv / "Scripts" / "python.exe"
    # 설치는 네트워크 없이: tests/sitecustomize.py가 루프백 밖 접속을 막는다.
    offline = _child_env(PYTHONPATH=str(ROOT / "tests"))
    install = _run(
        [python, "-m", "pip", "install", "--no-index", "--find-links", downloaded, "-r", ROOT / "requirements.txt", "--disable-pip-version-check"],
        env=offline,
    )
    assert install.returncode == 0, install.stdout[-4000:] + install.stderr[-4000:]
    check = _run([python, "-c", "import sys, yaml, requests, keyring, markdown, PIL; print(sys.version_info[:2], yaml.__with_libyaml__)"], env=offline)
    assert check.returncode == 0, check.stderr
    assert "(3, 14)" in check.stdout
    preflight = _run([python, "-X", "utf8", SCRIPTS / "kb.py", "--help"], env=offline)
    assert preflight.returncode == 0, preflight.stderr


def _python315() -> str | None:
    candidates = [shutil.which(n) for n in ("python", "python3", "python3.15")]
    uv = shutil.which("uv")
    if uv:
        found = _run([uv, "python", "find", "3.15"], env=_child_env(UV_PYTHON_NO_REGISTRY="1"))
        if found.returncode == 0:
            candidates.append(found.stdout.strip())
    for candidate in candidates:
        if not candidate or "WindowsApps" in candidate:
            continue
        probe = _run([candidate, "-c", "import sys; print(sys.version_info[:2] == (3, 15))"])
        if probe.returncode == 0 and probe.stdout.strip() == "True":
            return candidate
    return None


@pytest.mark.packaging
def test_cp315_pyyaml_wheel_build_attempt(downloaded, tmp_path):
    """결과만 기록한다(D02). 빌드 실패는 테스트 실패가 아니다."""
    python = _python315()
    if python is None:
        pytest.skip("Python 3.15 없음")
    sdist = sorted((downloaded / "sdist").glob("pyyaml-*.tar.gz"))[-1]
    out = tmp_path / "cp315"
    built = _run(
        [python, "-m", "pip", "wheel", sdist, "--no-deps", "-w", out, "--disable-pip-version-check"],
        env=_child_env(KB_ALLOW_NETWORK="1"),
        timeout=900,
    )
    produced = sorted(p.name for p in out.glob("*.whl")) if out.is_dir() else []
    if built.returncode == 0 and produced:
        import zipfile

        with zipfile.ZipFile(out / produced[0]) as zf:
            native = [n for n in zf.namelist() if n.endswith(".pyd")]
        print(f"\n[cp315 PyYAML] 성공: {produced} (C 확장 {'있음' if native else '없음: 순수 Python'}) via {python}")
    else:
        tail = (built.stderr or built.stdout)[-1500:]
        print(f"\n[cp315 PyYAML] 실패 (pip 종료 {built.returncode}) via {python}:\n{tail}")
