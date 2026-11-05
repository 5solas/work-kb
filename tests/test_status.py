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
    "confluence",
    "outbox",
    "proposals",
    "images",
    "images_gave_up",
    "compile_gave_up",
    "llm",
    "lock",
    "problems",
}


# 편찬 원장: gave_up 2개(tag 1, decisions 1). `_proposals`의 항목은 작업 단위가 아니라 세지 않는다
COMPILE_LEDGER = {
    "tag": {
        "daily/2026-11-04.md#aaaa": {"status": "ok", "tag": "결정"},
        "daily/2026-11-04.md#bbbb": {"status": "gave_up", "skips": 3},
        "daily/2026-11-04.md#cccc": {"status": "skipped", "skips": 1},
    },
    "decisions": {"daily/2026-11-03.md": {"status": "gave_up", "skips": 3}},
    "entity_summary": {"wiki/projects/KB.md": {"status": "ok"}},
    "_proposals": {"next_id": 2, "items": {"1": {"name": "캐시", "status": "gave_up"}}},
}

# Confluence 게시 원장(kb.py push가 쓴다): 볼트 상대 경로 → {page_id, version, sha256, pushed_at}.
# 가장 최근은 시각으로 고른다(+00:00 항목이 문자열로는 앞서도 실제로는 더 늦다)
CONFLUENCE_LEDGER = {
    "wiki/projects/KB.md": {"page_id": "1001", "version": 3, "sha256": "a" * 64, "pushed_at": "2026-11-04T18:10:00+09:00"},
    "wiki/decisions/2026-11.md": {"page_id": "1002", "version": 1, "sha256": "b" * 64, "pushed_at": "2026-11-04T09:30:00+00:00"},
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
    (root / ".state" / "compile.json").write_text(json.dumps(COMPILE_LEDGER, ensure_ascii=False), encoding="utf-8")
    (root / ".state" / "confluence.json").write_text(json.dumps(CONFLUENCE_LEDGER, ensure_ascii=False), encoding="utf-8")
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
        "마지막 Confluence 게시: 없음",
        "outbox: 대기 0개, 전송 중 0개, 실패 0개",
        "미처리 제안: 0개",
        "미처리 이미지: 0개",
        "포기한 편찬 단위: 0개",
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
    assert info["confluence"] == {"last_push": None, "pages": 0}
    assert info["outbox"] == {"pending": 0, "sending": 0, "failed": 0}
    assert info["proposals"] == 0 and info["images"] == 0 and info["compile_gave_up"] == 0
    assert info["llm"] == {"enabled": False, "fallback_order": ["main", "fallback"], "profiles": {}}
    assert info["problems"] == []


def test_sample_vault_text(sample, fake_now, run_kb):
    fake_now("2026-11-05T09:00:00+09:00")
    before = _tree(sample)
    result = run_kb("status", root=sample)
    assert result.returncode == 0, result.stderr
    out = result.stdout.splitlines()
    assert out[:11] == [
        "환경: home",
        "config.local.yaml: 있음",
        f"볼트: {sample.resolve()}",
        "오늘 daily: daily/2026-11-05.md 있음",
        "마지막 동기화: 2026-11-05T08:45:00+09:00",
        "마지막 Confluence 게시: 2026-11-04T09:30:00+00:00 (2쪽)",
        "outbox: 대기 2개, 전송 중 1개, 실패 1개",
        "미처리 제안: 2개",
        "미처리 이미지: 4개",  # 픽스처 2개 + 샘플 볼트의 inbox 드롭 1개·daily 임베드 raw/assets 1개
        "포기한 편찬 단위: 2개 → 입력이 바뀌면 다시 시도, 지금 다시 하려면 kb.py compile --force",
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
    assert info["images"] == 4 and info["proposals"] == 2
    assert info["compile_gave_up"] == 2
    assert info["confluence"] == {"last_push": "2026-11-04T09:30:00+00:00", "pages": 2}
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


def test_detected_pending_shows_waiting(sample, capsys):
    """init --dev가 키 없이 미뤄 둔 `detected: pending`은 '탐지 대기'(W4.6)."""
    local = yaml.safe_load((sample / "config.local.yaml").read_text(encoding="utf-8"))
    local["llm"]["profiles"]["main"]["detected"] = "pending"
    _write_local(sample, local)
    assert status.main(["--root", str(sample)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "LLM main: 가짜-비전-모델 @ 127.0.0.1 · 탐지: 탐지 대기" in out
    assert "LLM fallback: 작은-모델 @ llm.example.invalid · 탐지: 탐지 안 됨" in out
    info = status.collect(config.load(sample))
    assert info["llm"]["profiles"]["main"]["detected"] == "pending"
    assert info["llm"]["profiles"]["fallback"]["detected"] is None


def test_compile_gave_up_counts_and_broken_ledger(vault, capsys):
    _write_local(vault, {"environment": "home"})
    cfg = config.load(vault)
    assert status.compile_gave_up(cfg, []) == 0  # 원장 없음
    (vault / ".state" / "compile.json").write_text(json.dumps(COMPILE_LEDGER, ensure_ascii=False), encoding="utf-8")
    assert status.compile_gave_up(cfg, []) == 2
    (vault / ".state" / "compile.json").write_text("{깨짐", encoding="utf-8")
    problems: list[str] = []
    assert status.compile_gave_up(cfg, problems) == 0
    assert len(problems) == 1 and problems[0].startswith(".state/compile.json을 읽지 못했습니다")
    assert status.main(["--root", str(vault)]) == 0  # 깨진 원장도 치명적이지 않다
    assert "포기한 편찬 단위: 0개" in capsys.readouterr().out.splitlines()


def test_summarize_detected():
    assert status.summarize_detected(None) == "탐지 안 됨"
    assert status.summarize_detected({}) == "탐지 안 됨"
    assert status.summarize_detected("pending") == "탐지 대기"
    assert status.summarize_detected("이상한 값") == "탐지 안 됨"
    assert status.summarize_detected({"vision": False, "reasoning_toggle": {"method": "none"}}) == "비전 아니오, 추론 토글 none"
    assert status.summarize_detected({"evidence": "x"}) == "기록됨"


def test_summarize_detected_shows_output_floor_d41():
    """D41: method none + 기준선 생각 켜짐이면 출력 하한 2048을 status에 보인다."""
    from lib import llm_client

    assert status.NONE_REASONING_FLOOR == llm_client.NONE_REASONING_FLOOR
    floor = {"vision": True, "reasoning_toggle": {"method": "none"}, "baseline_reasoning": True, "context_tokens": 32768}
    assert status.summarize_detected(floor) == "비전 예, 추론 토글 none, 컨텍스트 32,768, 출력 하한 2048(D41)"
    assert "출력 하한" not in status.summarize_detected({**floor, "baseline_reasoning": False})
    assert "출력 하한" not in status.summarize_detected({**floor, "reasoning_toggle": {"method": "param"}})
    assert "출력 하한" not in status.summarize_detected({"reasoning_toggle": {"method": "none"}})


def test_images_gave_up_from_images_ledger(vault, fake_now, capsys):
    """ingest가 포기한 이미지(skipped[sha].count >= 3)를 센다. 모양이 다르거나 깨져도 치명적이지 않다."""
    fake_now("2026-11-05T09:00:00+09:00")
    _write_local(vault, {"environment": "home"})
    cfg = config.load(vault)
    assert status.images_gave_up(cfg, []) == 0  # 원장 없음
    ledger = {
        "images": {"s0": {"sidecar": "inbox/a.md"}},
        "skipped": {
            "s1": {"count": 3, "error": "x", "image": "inbox/a.png"},
            "s2": {"count": 5, "error": "x", "image": "inbox/b.png"},
            "s3": {"count": 2, "error": "x", "image": "inbox/c.png"},
            "s4": {"count": "3"},
            "s5": "이상한 값",
            "s6": {"count": True},
        },
    }
    state = vault / ".state" / "images.json"
    state.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    assert status.images_gave_up(cfg, []) == 2
    assert status.main(["--root", str(vault)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "미처리 이미지: 0개 (포기한 이미지 2개, 이유는 .state/images.json)" in out
    assert json.loads(_json_out(vault, capsys))["images_gave_up"] == 2
    for broken in ('{"skipped": []}', '["x"]', "{깨짐"):
        state.write_text(broken, encoding="utf-8")
        problems: list[str] = []
        assert status.images_gave_up(cfg, problems) == 0
        assert len(problems) == (1 if broken == "{깨짐" else 0)
    assert status.main(["--root", str(vault)]) == 0
    assert "미처리 이미지: 0개" in capsys.readouterr().out.splitlines()


def test_confluence_push_ledger_empty_broken_and_odd(vault, fake_now, capsys):
    """`.state/confluence.json`: 비면 '없음', 깨지면 주의 한 줄(치명적이지 않음), 모양이 다른 항목은 세지 않는다."""
    fake_now("2026-11-05T09:00:00+09:00")
    _write_local(vault, {"environment": "home"})
    cfg = config.load(vault)
    state = vault / ".state" / "confluence.json"
    assert status.confluence_pushes(cfg, []) == {"last_push": None, "pages": 0}  # 파일 없음
    state.write_text("{}", encoding="utf-8")
    assert status.main(["--root", str(vault)]) == 0
    assert "마지막 Confluence 게시: 없음" in capsys.readouterr().out.splitlines()
    odd = {**CONFLUENCE_LEDGER, "wiki/x.md": "이상한 값", "wiki/y.md": {"page_id": "9", "pushed_at": "언제"}}
    state.write_text(json.dumps(odd, ensure_ascii=False), encoding="utf-8")
    assert status.confluence_pushes(cfg, []) == {"last_push": "2026-11-04T09:30:00+00:00", "pages": 3}
    state.write_text("{깨짐", encoding="utf-8")
    problems: list[str] = []
    assert status.confluence_pushes(cfg, problems) == {"last_push": None, "pages": 0}
    assert len(problems) == 1 and problems[0].startswith(".state/confluence.json을 읽지 못했습니다")
    assert status.main(["--root", str(vault)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "마지막 Confluence 게시: 없음" in out
    assert any(line.startswith("주의: .state/confluence.json을 읽지 못했습니다") for line in out)
    assert json.loads(_json_out(vault, capsys))["confluence"] == {"last_push": None, "pages": 0}


def _json_out(vault: Path, capsys) -> str:
    assert status.main(["--root", str(vault), "--json"]) == 0
    return capsys.readouterr().out


def test_never_touches_network(sample, monkeypatch):
    import socket

    def refuse(*args, **kwargs):
        raise AssertionError("status가 네트워크를 썼습니다")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    info = status.collect(config.load(sample))
    assert info["llm"]["profiles"]["main"]["host"] == "127.0.0.1"
