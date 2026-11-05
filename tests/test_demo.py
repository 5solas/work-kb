"""scripts/demo.py (W5.4) 테스트. `demo` 마커: 기본 실행에서 빠지고 `pytest -m demo`로 돈다.

전체 흐름(모의 서버 3개 → 임시 설정 → sync → link → ingest → compile → lint → push)을 프로세스 안에서
돌리고 다음을 단언한다(D46): 모의 LLM이 채팅·이미지 요청을 1건 이상 받음, 모의 Confluence에 페이지가 있음,
저장소 파일이 하나도 바뀌지 않음(해시 비교), --keep이 없으면 임시 폴더가 지워짐.
"""
from __future__ import annotations

import hashlib
import io
import os
import socket
from pathlib import Path

import keyring
import pytest

import demo
import mock_jira
from lib import core
from lib import secrets as kb_secrets

pytestmark = pytest.mark.demo

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".venv", ".venv313", "__pycache__", ".pytest_cache", ".git"}


def repo_hashes() -> dict[str, str]:
    """저장소의 모든 파일 해시(가상환경·바이트코드 캐시 제외)."""
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            path = Path(dirpath) / name
            out[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def chat_bodies(llm) -> list[dict]:
    return [e["json"] for e in llm.log if e["path"].endswith("/chat/completions") and isinstance(e.get("json"), dict)]


def with_image(body: dict) -> bool:
    return any(
        isinstance(part, dict) and part.get("type") == "image_url"
        for message in body["messages"]
        if isinstance(message.get("content"), list)
        for part in message["content"]
    )


@pytest.fixture
def refs(monkeypatch):
    """secrets.resolve로 푼 참조를 모은다(실제 키·DEEPINFRA_API_KEY 참조가 없는지 보려고)."""
    seen: list[str] = []
    real = kb_secrets.resolve

    def spy(ref, cfg):
        seen.append(ref)
        return real(ref, cfg)

    monkeypatch.setattr(kb_secrets, "resolve", spy)
    return seen


def run_demo(**kw) -> tuple[dict, str]:
    buf = io.StringIO()
    result = demo.run(out=buf, **kw)
    return result, buf.getvalue()


# ---------------------------------------------------------------- 전체 흐름


def test_full_flow_hits_mocks_and_leaves_repo_untouched(refs, monkeypatch):
    monkeypatch.setenv("DEEPINFRA_API_KEY", "di-표식-키-읽히면-안됨")
    before = repo_hashes()
    test_keyring = keyring.get_keyring()

    result, output = run_demo()

    assert result["exit_code"] == 0, output
    assert all(step.ok for step in result["steps"]), output
    llm = result["mocks"].llm
    chats = chat_bodies(llm)
    assert len(chats) >= 1
    assert sum(1 for body in chats if with_image(body)) >= 1
    assert result["llm"]["vision"] >= 1
    assert not any(e["has_auth"] for e in llm.log)  # LLM 키를 보내지 않음
    pages = result["mocks"].confluence.user_pages()
    assert pages and {p["title"] for p in pages} == set(result["pages"])
    assert result["mocks"].jira.count("search") >= 1

    # 저장소·임시 폴더
    assert repo_hashes() == before
    assert not result["work_dir"].exists() and result["removed"]
    assert not result["kept"]
    assert result["vault"].is_relative_to(result["work_dir"])
    assert any("가" <= ch <= "힣" for ch in str(result["vault"])) and " " in str(result["vault"])

    # 비밀값: 데모 전용 keyring 참조만, keyring·시계는 원래대로
    assert refs and all(r.startswith(f"keyring:{demo.SERVICE}/") for r in refs)
    assert keyring.get_keyring() is test_keyring
    assert "KB_FAKE_NOW" not in os.environ

    # §7 결과 트리(상대 경로)와 요약
    tree = result["tree"]
    for rel in ("config.local.yaml", "wiki/_lint.md", "wiki/decisions.md", "jira/KB-12.md", "raw/assets/2026-11-05-1420-메일캡처.png"):
        assert rel in tree
    assert any(rel.startswith("inbox/") and rel.endswith("회의실 화이트보드.md") for rel in tree)  # 사이드카
    assert not any(rel.startswith(".state/logs") for rel in tree)
    head, _, tail = output.partition("결과 트리")
    assert tail and str(result["vault"]) not in tail.split("요약:")[0]
    assert "  wiki/" in tail and "    _lint.md" in tail
    assert "요약: 단계 9/9 성공" in output and "종료 코드 0" in output
    for name in ("sync", "link", "ingest", "compile", "lint", "push"):
        assert f"] {name}" in head


def test_keep_leaves_vault_outside_temp(tmp_path):
    keep = tmp_path / "남길 볼트 (데모)"
    result, output = run_demo(keep=keep)
    assert result["exit_code"] == 0, output
    assert result["kept"] and result["vault"] == keep.resolve()
    assert (keep / "wiki" / "_lint.md").is_file()
    assert (keep / "config.local.yaml").is_file()
    assert "keyring:work-kb-demo/" in (keep / "config.local.yaml").read_text(encoding="utf-8")
    assert not result["work_dir"].exists()  # 가짜 keyring 파일이 든 작업 폴더는 지운다
    assert not (keep / "keyring.json").exists()
    assert "남겨 둔 볼트" in output


def test_failed_step_gives_nonzero_exit_and_flow_continues(monkeypatch):
    import confluence_push

    def broken(cfg, **kw):
        raise core.AuthError("모의 인증 실패")

    monkeypatch.setattr(confluence_push, "run", broken)
    result, output = run_demo()
    assert result["exit_code"] == 2
    failed = [s.name for s in result["steps"] if not s.ok]
    assert "push" in failed and "검증" in failed  # 페이지가 없어 검증도 실패
    assert "lint" not in failed
    assert "모의 인증 실패" in output and "종료 코드 2" in output
    assert not result["work_dir"].exists()


# ---------------------------------------------------------------- 거부·사전 점검


@pytest.mark.parametrize(
    ("text", "company"),
    [
        ("environment: company\n", True),
        ('environment: "company"  # 회사\n', True),
        ("﻿environment: Company\njira: {}\n", True),
        ("environment: home\n", False),
        ("jira:\n  environment: company\n", False),  # 맨 위 키만 본다
        ("", False),
        ("environment:\n", False),  # 비었으면 글자 그대로 company가 아니다(D46)
    ],
)
def test_says_company_reads_only_environment(tmp_path, text, company):
    path = tmp_path / "config.local.yaml"
    path.write_bytes(text.encode("utf-8"))
    assert demo.says_company(path) is company


def test_refuses_company_before_touching_anything(tmp_path, monkeypatch):
    company = tmp_path / "회사 볼트"
    company.mkdir()
    (company / "config.local.yaml").write_text("environment: company\n", encoding="utf-8")
    monkeypatch.setattr(demo.tempfile, "mkdtemp", lambda **kw: pytest.fail("임시 폴더를 만들면 안 된다"))
    with pytest.raises(core.PolicyRefused) as info:
        demo.run(out=io.StringIO(), extra_roots=(company,))
    assert info.value.exit_code == 7


def test_missing_examples_or_mocks_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(demo, "SAMPLE", tmp_path / "없음")
    with pytest.raises(core.ConfigError) as info:
        demo.check_allowed()
    assert info.value.exit_code == 4 and "examples" in str(info.value)
    monkeypatch.setattr(demo, "SAMPLE", ROOT / "examples" / "sample-vault")
    monkeypatch.setattr(demo, "TESTS", tmp_path)
    with pytest.raises(core.ConfigError, match="tests/mock_jira.py"):
        demo.check_allowed()


def test_keep_inside_repo_or_nonempty_refused(tmp_path):
    with pytest.raises(core.ConfigError, match="저장소 안"):
        demo.run(out=io.StringIO(), keep=ROOT / "데모-남김")
    full = tmp_path / "차 있음"
    full.mkdir()
    (full / "a.txt").write_bytes(b"x")
    with pytest.raises(core.ConfigError, match="비어 있지 않"):
        demo.run(out=io.StringIO(), keep=full)


def test_fixed_port_taken_is_clear_error_and_cleans_up(monkeypatch):
    holder = mock_jira.start()  # 임시 포트를 잡아 둔다(실제 8081은 쓰지 않음, D65)
    try:
        monkeypatch.setitem(demo.FIXED_PORTS, "jira", holder.port)
        made: list[Path] = []
        real = demo.tempfile.mkdtemp

        def spy(**kw):
            path = real(**kw)
            made.append(Path(path))
            return path

        monkeypatch.setattr(demo.tempfile, "mkdtemp", spy)
        with pytest.raises(core.KbError, match=f"포트 {holder.port}"):
            demo.run(out=io.StringIO(), fixed_ports=True)
        assert made and not made[0].exists()
    finally:
        holder.stop()


def test_ports_are_ephemeral_by_default():
    result, output = run_demo()
    assert result["exit_code"] == 0, output
    ports = {result["mocks"].jira.port, result["mocks"].confluence.port, result["mocks"].llm.port}
    assert not ports & set(demo.FIXED_PORTS.values())
    for port in ports:  # 멈춘 뒤에는 연결이 거부된다
        with socket.socket() as sock:
            sock.settimeout(1)
            assert sock.connect_ex(("127.0.0.1", port)) != 0


# ---------------------------------------------------------------- kb.py 경유


def test_kb_demo_subprocess(run_kb):
    proc = run_kb("demo", timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "결과 트리" in proc.stdout and "종료 코드 0" in proc.stdout


def test_kb_demo_refused_for_agent(run_kb):
    proc = run_kb("demo", env={"KB_AGENT": "1"})
    assert proc.returncode == 7
    assert "결과 트리" not in proc.stdout
