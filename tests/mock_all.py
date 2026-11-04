"""세 모의 서버(Jira 8081, Confluence 8082, LLM 8083)를 한꺼번에 띄운다.

`python tests/mock_all.py` → 고정 포트로 띄우고 base URL 세 줄을 출력한 뒤 Ctrl+C까지 기다린다.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

_TESTS = str(Path(__file__).resolve().parent)
if _TESTS not in sys.path:
    sys.path.insert(0, _TESTS)

import mock_confluence  # noqa: E402
import mock_jira  # noqa: E402
import mock_llm  # noqa: E402


def start_all(jira_port: int = mock_jira.FIXED_PORT, confluence_port: int = mock_confluence.FIXED_PORT, llm_port: int = mock_llm.FIXED_PORT):
    """(jira, confluence, llm) RunningMock 세 개를 돌려준다. 하나라도 실패하면 이미 띄운 것을 닫는다."""
    started = []
    try:
        started.append(mock_jira.start(jira_port))
        started.append(mock_confluence.start(confluence_port))
        started.append(mock_llm.start(llm_port))
    except BaseException:
        for mock in started:
            mock.stop()
        raise
    return tuple(started)


def stop_all(mocks) -> None:
    for mock in mocks:
        mock.stop()


def main() -> None:
    mocks = start_all()
    for mock in mocks:
        print(mock.base_url, flush=True)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop_all(mocks)


if __name__ == "__main__":
    main()
