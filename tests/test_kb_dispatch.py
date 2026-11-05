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
    assert kb.COMMANDS_REAL["act"].agent_ok_sub == ("list",) if hasattr(kb, "COMMANDS_REAL") else True


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


def test_run_main_without_stderr_logs_error(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setenv("KB_ROOT", str(tmp_path))

    def main(argv):
        raise core.ConfigError("설정이 없습니다")

    with pytest.raises(SystemExit) as info:
        core.run_main(main, [])
    assert info.value.code == 4
    log = (tmp_path / ".state" / "logs" / "kb.log").read_text(encoding="utf-8")
    assert "설정이 없습니다" in log


def test_preflight_old_python_exit_4(monkeypatch, capsys):
    monkeypatch.setattr(sys, "version_info", (3, 12, 9, "final", 0))
    with pytest.raises(SystemExit) as info:
        kb._preflight()
    assert info.value.code == 4
    assert "Python 3.13 이상" in capsys.readouterr().err
