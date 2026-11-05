import importlib
import importlib.util
import json
import sys
import types

import pytest

import kb
from lib import core


def test_no_args_prints_usage_exit_4(run_kb):
    result = run_kb()
    assert result.returncode == 4
    assert "init" in result.stdout and "doctor" in result.stdout


def test_help_exit_0(run_kb):
    result = run_kb("--help")
    assert result.returncode == 0
    assert "사용법" in result.stdout


def test_unknown_command_exit_4(run_kb):
    result = run_kb("nosuch")
    assert result.returncode == 4
    assert "알 수 없는 명령: nosuch" in result.stderr


def test_argv_stdin_carries_korean(run_kb):
    # Emacs json-serialize처럼 \uXXXX 없이 날것의 UTF-8을 보낸다.
    raw = json.dumps(["없는명령😀", "한글 인수"], ensure_ascii=False)
    assert "\\u" not in raw
    result = run_kb("--argv-stdin", stdin=raw)
    assert result.returncode == 4
    assert "알 수 없는 명령: 없는명령😀" in result.stderr


def test_agent_session_refuses_init_before_import(run_kb):
    result = run_kb("init", env={"KB_AGENT": "1"})
    assert result.returncode == 7
    assert "KB_AGENT" in result.stderr


def test_init_help_mentions_dev(run_kb):
    # init.py는 W0.5에서 생기므로, 여기서는 명령 표만 확인한다.
    assert kb.COMMANDS["init"].agent_ok is False
    assert kb.COMMANDS["doctor"].agent_ok is True
    assert "--dev" in kb.COMMANDS["init"].help
    assert "--dev" in kb.usage()


# ---------------------------------------------------------------- 명령 표 계약(부록 A §2)


def test_command_positional_order_follows_contract():
    command = kb.Command("daily_prepare", "today_main")
    assert command.module == "daily_prepare"
    assert command.func == "today_main"
    assert command.agent_ok is True
    assert command.help == ""
    assert kb.Command("push", "main", False).agent_ok is False
    with pytest.raises(TypeError):
        kb.Command("m", "main", True, "설명")  # help는 키워드 전용


def _fake_module(monkeypatch, name: str, calls: list) -> None:
    module = types.ModuleType(name)

    def main(argv):
        calls.append(("main", argv))
        return 0

    def other(argv):
        calls.append(("other", argv))
        return 3

    module.main = main
    module.other = other
    monkeypatch.setitem(sys.modules, name, module)


def test_dispatch_calls_registered_func(monkeypatch):
    calls: list = []
    _fake_module(monkeypatch, "kb_fake_cmd", calls)
    monkeypatch.setattr(kb, "COMMANDS", {"fake": kb.Command("kb_fake_cmd", "other", help="가짜")})
    assert kb.main(["fake", "한글 인수"]) == 3
    assert calls == [("other", ["한글 인수"])]


def test_agent_gate_refuses_before_import(monkeypatch):
    calls: list = []
    imported: list[str] = []
    _fake_module(monkeypatch, "kb_fake_cmd", calls)
    real_import = importlib.import_module

    def spy(name, *args, **kwargs):
        imported.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(kb.importlib, "import_module", spy)
    monkeypatch.setattr(
        kb,
        "COMMANDS",
        {"push": kb.Command("kb_fake_cmd", agent_ok=False), "status": kb.Command("kb_fake_cmd")},
    )
    monkeypatch.setenv("KB_AGENT", "1")
    with pytest.raises(core.PolicyRefused):
        kb.main(["push"])
    assert imported == []
    assert calls == []
    # agent_ok=True인 명령은 KB_AGENT=1이어도 실행된다
    assert kb.main(["status", "x"]) == 0
    assert imported == ["kb_fake_cmd"]
    assert calls == [("main", ["x"])]


def test_agent_ok_sub_allows_only_listed_subcommand(monkeypatch):
    calls: list = []
    _fake_module(monkeypatch, "kb_fake_cmd", calls)
    monkeypatch.setattr(kb, "COMMANDS", {"act": kb.Command("kb_fake_cmd", agent_ok=False, agent_ok_sub=("list",))})
    monkeypatch.setenv("KB_AGENT", "1")
    assert kb.main(["act", "list", "--json"]) == 0
    assert calls == [("main", ["list", "--json"])]
    for argv in (["act"], ["act", "comment", "KB-1"], ["act", "flush"]):
        with pytest.raises(core.PolicyRefused):
            kb.main(argv)
    assert len(calls) == 1


def test_real_act_agent_ok_sub():
    assert kb.COMMANDS["act"].agent_ok_sub == ("list", "transitions")


# ---------------------------------------------------------------- _preflight (D02)


def _hide_modules(monkeypatch, *hidden: str) -> None:
    real = importlib.util.find_spec

    def fake(name, *args, **kwargs):
        return None if name in hidden else real(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake)


def test_preflight_passes_when_everything_present(capsys):
    assert kb._preflight() is None
    assert capsys.readouterr().err == ""


def test_preflight_missing_dependency_exit_4(monkeypatch, capsys):
    _hide_modules(monkeypatch, "yaml")
    monkeypatch.setattr(sys, "version_info", (3, 14, 0, "final", 0))
    with pytest.raises(SystemExit) as info:
        kb._preflight()
    assert info.value.code == 4
    err = capsys.readouterr().err
    assert "pyyaml" in err
    assert "pip install -r requirements.txt" in err
    assert "--no-index --find-links wheels" in err
    assert "3.15" not in err


def test_preflight_python_315_adds_pyyaml_hint(monkeypatch, capsys):
    _hide_modules(monkeypatch, "yaml")
    monkeypatch.setattr(sys, "version_info", (3, 15, 0, "final", 0))
    with pytest.raises(SystemExit) as info:
        kb._preflight()
    assert info.value.code == 4
    err = capsys.readouterr().err
    assert "pip install -r requirements.txt" in err
    assert "Python 3.15에는 PyYAML wheel이 아직 없습니다" in err


def test_preflight_without_stderr_writes_log(monkeypatch, tmp_path):
    # pythonw.exe(스케줄 작업)에서는 sys.stderr가 None이다. 메시지는 .state/logs/kb.log로 간다.
    _hide_modules(monkeypatch, "yaml")
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(kb, "HERE", tmp_path / "scripts")
    with pytest.raises(SystemExit) as info:
        kb._preflight()
    assert info.value.code == 4
    log = (tmp_path / ".state" / "logs" / "kb.log").read_text(encoding="utf-8")
    assert "pip install -r requirements.txt" in log


def _scheduled_task_dirs(monkeypatch, tmp_path):
    """스케줄 작업처럼: KB_ROOT 없음, 작업 폴더는 저장소, 볼트는 --root로만 넘어온다."""
    repo = tmp_path / "저장소"
    vault = tmp_path / "내 볼트 (KB)"
    repo.mkdir()
    vault.mkdir()
    monkeypatch.delenv("KB_ROOT", raising=False)
    monkeypatch.chdir(repo)
    monkeypatch.setattr(sys, "stderr", None)
    return repo, vault


def test_preflight_without_stderr_logs_to_root_vault(monkeypatch, tmp_path):
    _hide_modules(monkeypatch, "yaml")
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(kb, "HERE", tmp_path / "repo" / "scripts")
    vault = tmp_path / "한글 볼트"
    vault.mkdir()
    monkeypatch.setattr(sys, "argv", ["kb.py", "sync", "--root", str(vault)])
    with pytest.raises(SystemExit):
        kb._preflight()
    assert (vault / ".state" / "logs" / "kb.log").exists()
    assert not (tmp_path / "repo" / ".state").exists()


def test_run_main_without_stderr_logs_error(monkeypatch, tmp_path):
    repo, vault = _scheduled_task_dirs(monkeypatch, tmp_path)

    def main(argv):
        raise core.ConfigError("설정이 없습니다")

    with pytest.raises(SystemExit) as info:
        core.run_main(main, ["sync", "--root", str(vault)])
    assert info.value.code == 4
    log = (vault / ".state" / "logs" / "kb.log").read_text(encoding="utf-8")
    assert "설정이 없습니다" in log
    assert not (repo / ".state").exists()


def test_run_main_without_stderr_root_equals_form(monkeypatch, tmp_path):
    repo, vault = _scheduled_task_dirs(monkeypatch, tmp_path)

    def main(argv):
        raise core.ConfigError("설정이 없습니다")

    with pytest.raises(SystemExit):
        core.run_main(main, ["sync", f"--root={vault}"])
    assert "설정이 없습니다" in (vault / ".state" / "logs" / "kb.log").read_text(encoding="utf-8")
    assert not (repo / ".state").exists()


def test_run_main_without_stderr_missing_root_falls_back(monkeypatch, tmp_path):
    # --root 폴더가 없으면 그 폴더를 만들지 않고 KB_ROOT/현재 폴더에 남긴다.
    repo, vault = _scheduled_task_dirs(monkeypatch, tmp_path)
    missing = tmp_path / "없는 볼트"

    def main(argv):
        raise core.ConfigError("볼트 루트 폴더가 없습니다")

    with pytest.raises(SystemExit):
        core.run_main(main, ["sync", "--root", str(missing)])
    assert not missing.exists()
    assert "볼트 루트 폴더가 없습니다" in (repo / ".state" / "logs" / "kb.log").read_text(encoding="utf-8")


def test_run_main_without_stderr_logs_unexpected_traceback(monkeypatch, tmp_path):
    repo, vault = _scheduled_task_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(core, "_SECRETS", set())
    core.register_secret("s3cr3t-토큰-값")

    def main(argv):
        raise RuntimeError("unexpected bug s3cr3t-토큰-값")

    with pytest.raises(SystemExit) as info:
        core.run_main(main, ["sync", "--root", str(vault)])
    assert info.value.code == 1
    log = (vault / ".state" / "logs" / "kb.log").read_text(encoding="utf-8")
    assert "Traceback" in log and "RuntimeError: unexpected bug" in log
    assert "s3cr3t-토큰-값" not in log
    assert not (repo / ".state").exists()


def test_run_main_with_stderr_propagates_unexpected(monkeypatch, tmp_path):
    def main(argv):
        raise RuntimeError("unexpected bug")

    with pytest.raises(RuntimeError, match="unexpected bug"):
        core.run_main(main, ["--root", str(tmp_path)])
    assert not (tmp_path / ".state").exists()


def test_preflight_old_python_exit_4(monkeypatch, capsys):
    monkeypatch.setattr(sys, "version_info", (3, 12, 9, "final", 0))
    with pytest.raises(SystemExit) as info:
        kb._preflight()
    assert info.value.code == 4
    assert "Python 3.13 이상" in capsys.readouterr().err
