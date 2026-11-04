"""kb.py status: 파일만 읽는 상태 요약."""
import json
import shutil
from pathlib import Path

import pytest
import yaml

import status
from lib import config, core

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "sample-vault"

JSON_KEYS = {
    "environment",
    "local_config",
    "vault",
    "agent",
    "today",
    "last_sync",
    "outbox",
    "proposals",
    "images",
    "llm",
    "lock",
    "problems",
}


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _write_local(root: Path, data: dict) -> None:
    (root / "config.local.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


@pytest.fixture
def sample(tmp_path):
    """샘플 볼트 복사본 + 상태 파일·outbox·제안·이미지·LLM 프로필."""
    root = tmp_path / "샘플 볼트" / "work-kb"
    shutil.copytree(SAMPLE, root)
    local = yaml.safe_load((root / "config.local.example.yaml").read_text(encoding="utf-8"))
    local["llm"] = {
        "enabled": True,
        "fallback_order": ["main", "fallback"],
        "profiles": {
            "main": {
                "base_url": "http://127.0.0.1:9/v1",
                "model": "가짜-비전-모델",
                "api_key_ref": "env:SAMPLE_KEY",
                "detected": {
                    "vision": True,
                    "json_mode": False,
                    "reasoning_toggle": {"method": "param", "param_name": "reasoning_effort"},
                    "context_tokens": 262144,
                },
            },
            "fallback": {"base_url": "https://llm.example.invalid/v1", "model": "작은-모델"},
        },
    }
    _write_local(root, local)
    (root / ".state").mkdir()
    (root / ".state" / "sync.json").write_text(json.dumps({"last_sync": "2026-11-05T08:45:00+09:00"}), encoding="utf-8")
    (root / "jira").mkdir()
    outbox = [
        {"id": "1", "action": "comment", "key": "KB-12", "status": "pending"},
        {"id": "2", "action": "comment", "key": "KB-7", "status": "pending"},
        {"id": "3", "action": "transition", "key": "OPS-3", "status": "failed"},
        {"id": "4", "action": "comment", "key": "KB-12", "status": "done"},
        {"id": "5", "action": "worklog", "key": "KB-5", "status": "sending", "pid": 1, "claimed_at": "2026-11-05T08:00:00+09:00"},
    ]
    lines = [json.dumps(item, ensure_ascii=False) for item in outbox] + ["{깨진 줄", ""]
    (root / "jira" / "outbox.jsonl").write_bytes("\n".join(lines).encode("utf-8"))
    (root / "wiki").mkdir()
    (root / "wiki" / "_proposals.md").write_text(
        "# 주제 제안\n\n- 구역 밖 항목은 세지 않음\n<!-- kb:proposals:start -->\n1. 캐시 설계\n   - 근거 줄\n2. 배포 절차\n<!-- kb:proposals:end -->\n",
        encoding="utf-8",
    )
    inbox = root / "inbox"
    (inbox / "화이트보드.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (inbox / "메일 캡처.JPG").write_bytes(b"\xff\xd8\xff")
    (inbox / "처리됨.webp").write_bytes(b"RIFF")
    (inbox / "처리됨.md").write_text("---\nsource_image: raw/assets/x.webp\n---\n", encoding="utf-8")
    (inbox / "메모.txt").write_text("이미지 아님", encoding="utf-8")
    return root


def test_empty_vault(vault, fake_now, capsys):
    fake_now("2026-11-05T09:00:00+09:00")
    shutil.rmtree(vault / ".state")
    before = _tree(vault)
    assert status.main(["--root", str(vault)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "환경: company (config.local.yaml이 없어 회사로 봅니다)",
        "config.local.yaml: 없음 → kb.py init",
        f"볼트: {vault.resolve()}",
        "오늘 daily: daily/2026-11-05.md 없음 → kb.py today",
        "마지막 동기화: 없음",
        "outbox: 대기 0개, 전송 중 0개, 실패 0개",
        "미처리 제안: 0개",
        "미처리 이미지: 0개",
        "LLM: 꺼짐",
        "LLM 프로필: 없음",
        "잠금: 없음",
    ]
    assert _tree(vault) == before
    assert not (vault / ".state").exists()  # 상태 폴더·로그를 만들지 않는다


def test_empty_vault_json(vault, fake_now, capsys):
    fake_now("2026-11-05T09:00:00+09:00")
    assert status.main(["--root", str(vault), "--json"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert set(info) == JSON_KEYS
    assert info["environment"] == "company" and info["local_config"] is False and info["agent"] is False
    assert info["today"] == {"date": "2026-11-05", "path": "daily/2026-11-05.md", "exists": False}
    assert info["last_sync"] is None and info["lock"] is None
    assert info["outbox"] == {"pending": 0, "sending": 0, "failed": 0}
    assert info["proposals"] == 0 and info["images"] == 0
    assert info["llm"] == {"enabled": False, "fallback_order": ["main", "fallback"], "profiles": {}}
    assert info["problems"] == []


def test_sample_vault_text(sample, fake_now, run_kb):
    fake_now("2026-11-05T09:00:00+09:00")
    before = _tree(sample)
    result = run_kb("status", root=sample)
    assert result.returncode == 0, result.stderr
    out = result.stdout.splitlines()
    assert out[:9] == [
        "환경: home",
        "config.local.yaml: 있음",
        f"볼트: {sample.resolve()}",
        "오늘 daily: daily/2026-11-05.md 있음",
        "마지막 동기화: 2026-11-05T08:45:00+09:00",
        "outbox: 대기 2개, 전송 중 1개, 실패 1개",
        "미처리 제안: 2개",
        "미처리 이미지: 2개",
        "LLM: 켜짐 (순서: main → fallback)",
    ]
    assert "LLM main: 가짜-비전-모델 @ 127.0.0.1 · 탐지: 비전 예, JSON 모드 아니오, 추론 토글 param, 컨텍스트 262,144" in out
    assert "LLM fallback: 작은-모델 @ llm.example.invalid · 탐지: 탐지 안 됨" in out
    assert "잠금: 없음" in out
    assert "주의: jira/outbox.jsonl에 읽지 못한 줄 1개" in out
    assert not any(line.startswith("에이전트 모드") for line in out)
    assert _tree(sample) == before  # 아무것도 쓰지 않는다


def test_sample_vault_json_keys(sample, fake_now, run_kb):
    fake_now("2026-11-04T23:59:00+09:00")
    result = run_kb("status", "--json", root=sample)
    assert result.returncode == 0, result.stderr
    info = json.loads(result.stdout)
    assert set(info) == JSON_KEYS
    assert info["today"]["exists"] is True and info["today"]["date"] == "2026-11-04"
    assert info["outbox"] == {"pending": 2, "sending": 1, "failed": 1}
    assert info["images"] == 2 and info["proposals"] == 2
    main = info["llm"]["profiles"]["main"]
    assert set(main) == {"model", "host", "public_host", "detected", "detected_summary"}
    assert main["detected"]["context_tokens"] == 262144
    assert info["llm"]["profiles"]["fallback"]["detected"] is None


def test_kb_agent_allowed(sample, run_kb):
    result = run_kb("status", root=sample, env={"KB_AGENT": "1"})
    assert result.returncode == 0, result.stderr
    assert "에이전트 모드: KB_AGENT=1 (외부 쓰기·설정 변경 거부)" in result.stdout.splitlines()
    result = run_kb("status", "--json", root=sample, env={"KB_AGENT": "1"})
    assert json.loads(result.stdout)["agent"] is True


def test_lock_holder_other_process_and_same_process(sample, run_kb):
    assert status.lock_holder(sample) is None  # 잠금 파일도 없음
    with core.kb_lock(sample, "link"):
        holder = status.lock_holder(sample)
        assert holder is not None and holder.startswith("link (pid ")
        result = run_kb("status", root=sample)
        assert result.returncode == 0, result.stderr
        line = next(x for x in result.stdout.splitlines() if x.startswith("잠금: "))
        assert line.startswith("잠금: link (pid ")
    assert status.lock_holder(sample) is None  # 풀린 뒤(파일은 남아 있음)
    with core.kb_lock(sample, "다음 작업"):  # 확인이 잠금을 망가뜨리지 않는다
        pass


def test_company_public_host_warning(sample, capsys):
    local = yaml.safe_load((sample / "config.local.yaml").read_text(encoding="utf-8"))
    local["environment"] = "company"
    local["llm"]["profiles"]["fallback"]["base_url"] = "https://api.deepinfra.com/v1/openai"
    _write_local(sample, local)
    assert status.main(["--root", str(sample)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "LLM fallback: 작은-모델 @ api.deepinfra.com · 탐지: 탐지 안 됨 · 공개 호스트(회사 환경에서는 거부)" in out
    assert any(line.startswith("LLM main: ") and "공개 호스트" not in line for line in out)


def test_broken_state_files_are_reported_not_fatal(vault, capsys):
    _write_local(vault, {"environment": "home"})
    (vault / ".state" / "sync.json").write_text("{깨짐", encoding="utf-8")
    (vault / "wiki" / "_proposals.md").write_text("<!-- kb:proposals:start -->\n- a\n", encoding="utf-8")
    assert status.main(["--root", str(vault)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "마지막 동기화: 없음" in out
    assert "미처리 제안: 0개" in out
    problems = [line for line in out if line.startswith("주의: ")]
    assert len(problems) == 2
    assert problems[0].startswith("주의: .state/sync.json을 읽지 못했습니다")
    assert problems[1].startswith("주의: wiki/_proposals.md를 읽지 못했습니다")


def test_unprocessed_images_include_embedded_raw_assets_without_sidecar(vault):
    """캡처 C·F는 이미지를 raw/assets에 두고 daily `## 기록`에 임베드한다(§6.5 T6). 사이드카가 없으면 미처리."""
    _write_local(vault, {"environment": "home"})
    assets = vault / "raw" / "assets"
    for name in ("2026-11-05-1420-화이트보드.png", "2026-11-05-1500-처리됨.jpg", "2026-11-05-1600-코드.png", "이름만.webp"):
        (assets / name).write_bytes(b"\x89PNG")
    (vault / "inbox" / "2026-11-05-1500-처리됨.md").write_text("---\nsource_image: raw/assets/2026-11-05-1500-처리됨.jpg\n---\n", encoding="utf-8")
    (vault / "daily" / "2026-11-05.md").write_text(
        "## 오늘\n- ![[raw/assets/2026-11-05-1600-코드.png]] 기록 절 밖\n"
        "## 기록\n"
        "- 14:20 ![[raw/assets/2026-11-05-1420-화이트보드.png]] 회의\n"
        "- 14:21 ![[raw/assets/2026-11-05-1420-화이트보드.png|300]] 같은 그림 다시\n"
        "- 15:00 ![[raw/assets/2026-11-05-1500-처리됨.jpg]] 사이드카 있음\n"
        "- 15:10 ![[raw/assets/없는 그림.png]] 파일 없음\n"
        "- 15:20 ![[이름만.webp]] 이름만 쓴 임베드(D64)\n"
        "```\n![[raw/assets/2026-11-05-1600-코드.png]]\n```\n",
        encoding="utf-8",
    )
    assert status.unprocessed_images(config.load(vault)) == 2  # 화이트보드, 이름만
    (vault / "inbox" / "화이트보드.png").write_bytes(b"\x89PNG")  # inbox 드롭도 그대로 센다
    assert status.unprocessed_images(config.load(vault)) == 3


def test_summarize_detected():
    assert status.summarize_detected(None) == "탐지 안 됨"
    assert status.summarize_detected({}) == "탐지 안 됨"
    assert status.summarize_detected({"vision": False, "reasoning_toggle": {"method": "none"}}) == "비전 아니오, 추론 토글 none"
    assert status.summarize_detected({"evidence": "x"}) == "기록됨"


def test_never_touches_network(sample, monkeypatch):
    import socket

    def refuse(*args, **kwargs):
        raise AssertionError("status가 네트워크를 썼습니다")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    info = status.collect(config.load(sample))
    assert info["llm"]["profiles"]["main"]["host"] == "127.0.0.1"
