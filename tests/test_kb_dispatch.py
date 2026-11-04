import json


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
    result = run_kb("--argv-stdin", stdin=json.dumps(["없는명령", "한글 인수"]))
    assert result.returncode == 4
    assert "없는명령" in result.stderr


def test_agent_session_refuses_init_before_import(run_kb):
    result = run_kb("init", env={"KB_AGENT": "1"})
    assert result.returncode == 7
    assert "KB_AGENT" in result.stderr


def test_init_help_mentions_dev(run_kb):
    # init.py는 W0.5에서 생기므로, 여기서는 명령 표만 확인한다.
    import kb

    assert kb.COMMANDS["init"].agent_ok is False
    assert kb.COMMANDS["doctor"].agent_ok is True
