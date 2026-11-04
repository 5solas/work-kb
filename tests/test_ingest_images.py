"""ingest_images (T6) 테스트. 모의 LLM(canned·echo·vision)과 허구 이미지 픽스처만 쓴다."""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import shutil
from pathlib import Path

import pytest
import yaml
from PIL import Image
from PIL.Image import open as open_image

import ingest_images
import link
import mock_llm
import status
from lib import config, core, frontmatter, managed, net

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "images"
SAMPLE = ROOT / "examples" / "sample-vault"
NOW = "2026-11-05T14:30:00+09:00"

REPLY = {
    "kind": "photo",
    "text": "캐시 설계 회의 (허구)\nKB-12 updated는 UTC로 저장\n담당: @김철수 (리뷰)\nOPS-3 KB-999 언급",
    "summary": "캐시 설계 회의 화이트보드. updated를 UTC로 저장하기로 함.",
    "jira_keys": ["KB-12", "[[OPS-3]]", "ZZ-1", "kb-12", "엉뚱한 값"],
    "people": ["김철수", "@ 김철수", "박추정", "이름 없음!"],
}


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(net, "_sleep", lambda seconds: None)


@pytest.fixture
def llm():
    """`llm(**modes)` → 모의 LLM. 테스트가 끝나면 모두 멈춘다."""
    servers = []

    def _start(**modes):
        modes.setdefault("canned", {"image_ingest": json.dumps(REPLY, ensure_ascii=False)})
        server = mock_llm.start(**modes)
        servers.append(server)
        return server

    yield _start
    for server in servers:
        server.stop()


def detected(vision=True):
    return {"vision": vision, "json_mode": True, "reasoning_toggle": {"method": "none"}, "context_tokens": 32768}


def make_cfg(vault, url=None, *, vision=True, enabled=True, environment="home", profiles=None, order=("main",)):
    if profiles is None:
        profiles = {"main": {"base_url": f"{url}/v1", "model": "mock-비전", "detected": detected(vision)}}
    data = {
        "environment": environment,
        "jira": {"project_keys": ["KB", "OPS"]},
        "llm": {"enabled": enabled, "profiles": profiles, "fallback_order": list(order)},
    }
    config.save_local(vault, data)
    return config.load(vault)


def put(path: Path, fixture: str = "whiteboard.png") -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIX / fixture, path)
    return path.read_bytes()


def daily(vault: Path, day: str, *record: str) -> Path:
    path = vault / "daily" / f"{day}.md"
    text = f"---\ndate: {day}\n---\n## 오늘\n<!-- kb:today:start -->\n<!-- kb:today:end -->\n\n## 기록\n" + "".join(
        f"{line}\n" for line in record
    )
    path.write_bytes(text.encode("utf-8"))
    return path


def tree(root: Path, *, skip=(".state/logs",)) -> dict[str, bytes]:
    out = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_file() and not any(rel == s or rel.startswith(s + "/") for s in skip):
            out[rel] = path.read_bytes()
    return out


def chats(server) -> list[dict]:
    return [entry["json"] for entry in server.log if entry["path"].endswith("/chat/completions")]


def sent_image(body: dict) -> tuple[str, Image.Image]:
    parts = body["messages"][1]["content"]
    url = next(p["image_url"]["url"] for p in parts if p.get("type") == "image_url")
    head, data = url.split(",", 1)
    return head, open_image(io.BytesIO(base64.b64decode(data)))


def user_text(body: dict) -> str:
    return next(p["text"] for p in body["messages"][1]["content"] if p.get("type") == "text")


def sidecar_parts(path: Path) -> tuple[dict, str]:
    data = path.read_bytes()
    region = managed.read_region(data, "extracted")
    assert region is not None
    return frontmatter.parse(data), region.decode("utf-8")


def quiet(*_args):
    return None


# ---------------------------------------------------------------- inbox 드롭


def test_inbox_image_moved_and_sidecar_written(vault, llm, fake_now):
    fake_now(NOW)
    server = llm()
    cfg = make_cfg(vault, server.base_url)
    original = put(vault / "inbox" / "회의실 화이트보드.png")
    lines: list[str] = []
    report = ingest_images.run(cfg, out=lines.append)

    assert report["status"] == "ok" and report["pending"] == 1
    assert not (vault / "inbox" / "회의실 화이트보드.png").exists()
    asset = vault / "raw" / "assets" / "2026-11-05-1430-회의실 화이트보드.png"
    assert asset.read_bytes() == original  # 원본 그대로 보존
    sidecar = vault / "inbox" / "2026-11-05-1430-회의실 화이트보드.md"
    fields, region = sidecar_parts(sidecar)
    assert fields == {
        "source_image": "raw/assets/2026-11-05-1430-회의실 화이트보드.png",
        "ingested_at": "2026-11-05T14:30:00+09:00",
        "model": "mock-비전",
        "kind": "photo",
    }
    text = sidecar.read_text(encoding="utf-8")
    assert "![[raw/assets/2026-11-05-1430-회의실 화이트보드.png]]\n\n## 추출\n<!-- kb:extracted:start -->\n" in text
    assert "- 종류: 사진" in region
    assert "- 키: [[KB-12]] · [[OPS-3]]\n" in region  # project_keys 밖(ZZ-1)·형식 오류·중복 제외
    assert "- 사람: @김철수\n" in region  # 글에 없는 이름(박추정)·형식 오류 제외
    assert "- KB-12 updated는 UTC로 저장" in region and "### 읽은 글" in region
    assert report["processed"] == [
        {
            "image": "inbox/회의실 화이트보드.png",
            "source_image": "raw/assets/2026-11-05-1430-회의실 화이트보드.png",
            "sidecar": "inbox/2026-11-05-1430-회의실 화이트보드.md",
            "profile": "main",
            "kind": "photo",
        }
    ]
    assert any(line.startswith("결과: 읽음 1, 중복 0") for line in lines)

    ledger = json.loads((vault / ".state" / "images.json").read_text(encoding="utf-8"))
    entry = ledger["images"][core.sha256_hex(original)]
    assert entry["sidecar"] == "inbox/2026-11-05-1430-회의실 화이트보드.md" and entry["profile"] == "main"
    assert status.unprocessed_images(cfg) == 0

    (body,) = chats(server)
    system = body["messages"][0]["content"]
    assert system.startswith("KB-TASK: image_ingest\n") and "## 작업 스키마" in system and "screenshot" in system
    head, image = sent_image(body)
    assert head == "data:image/png;base64" and image.size == (960, 640) and image.mode == "RGB"
    assert "파일명: 회의실 화이트보드.png" in user_text(body) and "KB, OPS" in user_text(body)
    assert body["response_format"] == {"type": "json_object"} and body["max_tokens"] == 2048

    # 두 번째 실행: 할 일 없음, LLM 호출 없음
    before = tree(vault)
    again = ingest_images.run(cfg, out=quiet)
    assert again["status"] == "empty" and len(chats(server)) == 1
    assert tree(vault) == before


def test_stamped_name_kept_and_collisions_numbered(vault, llm, fake_now):
    fake_now(NOW)
    server = llm()
    cfg = make_cfg(vault, server.base_url)
    put(vault / "inbox" / "2026-11-01-0900-이미 시각.png", "mail.png")
    put(vault / "inbox" / "보드.png", "whiteboard.png")
    (vault / "raw" / "assets" / "2026-11-05-1430-보드.png").write_bytes(b"other")  # 이미 있는 이름
    ingest_images.run(cfg, out=quiet)
    assert (vault / "raw" / "assets" / "2026-11-01-0900-이미 시각.png").is_file()
    assert (vault / "inbox" / "2026-11-01-0900-이미 시각.md").is_file()
    assert (vault / "raw" / "assets" / "2026-11-05-1430-보드-2.png").is_file()
    assert (vault / "inbox" / "2026-11-05-1430-보드-2.md").is_file()
    assert (vault / "raw" / "assets" / "2026-11-05-1430-보드.png").read_bytes() == b"other"


def test_link_unsafe_characters_in_name(vault, llm, fake_now):
    fake_now(NOW)
    server = llm()
    cfg = make_cfg(vault, server.base_url)
    put(vault / "inbox" / "회의 [초안] #1.png")
    ingest_images.run(cfg, out=quiet)
    assert (vault / "raw" / "assets" / "2026-11-05-1430-회의 -초안- -1.png").is_file()
    assert (vault / "inbox" / "2026-11-05-1430-회의 -초안- -1.md").is_file()


# ---------------------------------------------------------------- daily 임베드


def test_daily_embeds_get_sidecars_and_daily_raw_unchanged(vault, llm, fake_now):
    fake_now(NOW)
    server = llm()
    cfg = make_cfg(vault, server.base_url)
    put(vault / "raw" / "assets" / "2026-11-05-1420-메일.png", "mail.png")
    put(vault / "raw" / "assets" / "2026-11-05-1500-문서.jpg", "document.png")  # 확장자만 jpg(내용은 PNG)
    put(vault / "raw" / "assets" / "안붙임.png", "whiteboard.png")  # 임베드 안 됨 → 대상 아님
    note = daily(
        vault,
        "2026-11-05",
        "- 14:20 ![[raw/assets/2026-11-05-1420-메일.png]] 메일 캡처 @김철수",
        "- 15:00 ![[2026-11-05-1500-문서.jpg|300]] 문서 이름만",
        "```",
        "- 16:00 ![[raw/assets/안붙임.png]] 코드 블록 안",
        "```",
    )
    raw_before = {k: v for k, v in tree(vault).items() if k.startswith(("daily/", "raw/"))}
    report = ingest_images.run(cfg, out=quiet)
    assert [p["image"] for p in report["processed"]] == [
        "raw/assets/2026-11-05-1420-메일.png",
        "raw/assets/2026-11-05-1500-문서.jpg",
    ]
    assert {k: v for k, v in tree(vault).items() if k.startswith(("daily/", "raw/"))} == raw_before
    fields, _ = sidecar_parts(vault / "inbox" / "2026-11-05-1420-메일.md")
    assert fields["source_image"] == "raw/assets/2026-11-05-1420-메일.png"
    assert (vault / "inbox" / "2026-11-05-1500-문서.md").is_file()
    assert not (vault / "inbox" / "안붙임.md").exists()
    first, second = chats(server)
    assert "메모 줄: - 14:20 ![[raw/assets/2026-11-05-1420-메일.png]] 메일 캡처 @김철수" in user_text(first)
    assert sent_image(second)[0] == "data:image/jpeg;base64"
    assert status.unprocessed_images(cfg) == 0
    assert note.read_bytes() == raw_before["daily/2026-11-05.md"]


def test_inbox_image_referenced_by_daily_is_not_moved(vault, llm, fake_now):
    fake_now(NOW)
    server = llm()
    cfg = make_cfg(vault, server.base_url)
    original = put(vault / "inbox" / "붙인 그림.png")
    daily(vault, "2026-11-05", "- 10:00 ![[inbox/붙인 그림.png]] 링크가 깨지면 안 됨")
    ingest_images.run(cfg, out=quiet)
    assert (vault / "inbox" / "붙인 그림.png").read_bytes() == original
    fields, _ = sidecar_parts(vault / "inbox" / "붙인 그림.md")
    assert fields["source_image"] == "inbox/붙인 그림.png"
    assert not any((vault / "raw" / "assets").iterdir())
    assert status.unprocessed_images(cfg) == 0
    assert ingest_images.run(cfg, out=quiet)["status"] == "empty"


# ---------------------------------------------------------------- 중복


def test_duplicate_inbox_drop_goes_to_trash_without_llm(vault, llm, fake_now):
    fake_now(NOW)
    server = llm()
    cfg = make_cfg(vault, server.base_url)
    put(vault / "inbox" / "a.png")
    put(vault / "inbox" / "b 같은 그림.png")  # 같은 실행 안의 중복
    report = ingest_images.run(cfg, out=quiet)
    assert len(chats(server)) == 1
    assert len(report["processed"]) == 1
    assert report["duplicates"] == [
        {"image": "inbox/b 같은 그림.png", "moved_to": "_trash/b 같은 그림.png", "same_as": "inbox/2026-11-05-1430-a.md"}
    ]
    assert (vault / "_trash" / "b 같은 그림.png").is_file()

    put(vault / "inbox" / "b 같은 그림.png")  # 다음 실행에 또 드롭: 이름이 겹치면 -2
    report = ingest_images.run(cfg, out=quiet)
    assert len(chats(server)) == 1
    assert report["duplicates"][0]["moved_to"] == "_trash/b 같은 그림-2.png"
    assert not list((vault / "inbox").glob("*.png"))


def test_duplicate_embed_gets_pointer_sidecar(vault, llm, fake_now):
    fake_now(NOW)
    server = llm()
    cfg = make_cfg(vault, server.base_url)
    put(vault / "inbox" / "a.png")
    ingest_images.run(cfg, out=quiet)
    put(vault / "raw" / "assets" / "사본.png")
    daily(vault, "2026-11-05", "- 15:00 ![[raw/assets/사본.png]] KB-12 같은 그림")
    report = ingest_images.run(cfg, out=quiet)
    assert len(chats(server)) == 1
    assert report["duplicates"] == [{"image": "raw/assets/사본.png", "sidecar": "inbox/사본.md", "same_as": "inbox/2026-11-05-1430-a.md"}]
    fields, region = sidecar_parts(vault / "inbox" / "사본.md")
    assert fields["duplicate_of"] == "inbox/2026-11-05-1430-a.md" and fields["kind"] == "photo"
    assert region == "- 같은 이미지를 이미 읽었습니다: [[inbox/2026-11-05-1430-a]]\n"
    assert (vault / "raw" / "assets" / "사본.png").is_file()
    assert status.unprocessed_images(cfg) == 0


def test_ledger_rebuilt_from_sidecars(vault, llm, fake_now):
    fake_now(NOW)
    server = llm()
    cfg = make_cfg(vault, server.base_url)
    put(vault / "inbox" / "a.png")
    ingest_images.run(cfg, out=quiet)
    (vault / ".state" / "images.json").unlink()
    put(vault / "inbox" / "다시.png")
    report = ingest_images.run(cfg, out=quiet)
    assert len(chats(server)) == 1  # 원장 없이도 사이드카의 source_image로 중복을 안다
    assert report["duplicates"][0]["moved_to"] == "_trash/다시.png"
    ledger = ingest_images.load_ledger(cfg)
    (entry,) = ledger["images"].values()
    assert entry["sidecar"] == "inbox/2026-11-05-1430-a.md" and entry["rebuilt"] is True and entry["kind"] == "photo"


# ---------------------------------------------------------------- LLM 없음·실패


def test_no_vision_profile_exit_0_and_nothing_written(vault, llm, fake_now, capsys):
    fake_now(NOW)
    server = llm()
    make_cfg(vault, server.base_url, vision=False)
    put(vault / "inbox" / "a.png")
    put(vault / "raw" / "assets" / "b.png", "mail.png")
    daily(vault, "2026-11-05", "- 09:00 ![[raw/assets/b.png]]")
    before = tree(vault)
    assert ingest_images.main(["--root", str(vault)]) == 0
    out = capsys.readouterr().out
    assert "비전 프로필이 없어 이미지 읽기를 건너뜁니다 (미처리 이미지 2개)" in out
    assert tree(vault) == before
    assert server.counts == {"models": 0, "chat": 0}


def test_llm_disabled_does_nothing(vault, llm, fake_now, capsys):
    fake_now(NOW)
    server = llm()
    make_cfg(vault, server.base_url, enabled=False)
    put(vault / "inbox" / "a.png")
    before = tree(vault)
    assert ingest_images.main(["--root", str(vault)]) == 0
    assert "LLM이 꺼져 있어 이미지 읽기를 건너뜁니다 (미처리 이미지 1개)" in capsys.readouterr().out
    assert tree(vault) == before and server.counts == {"models": 0, "chat": 0}


def test_unreachable_vision_profile_exit_0(vault, fake_now, capsys):
    fake_now(NOW)
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    make_cfg(vault, f"http://127.0.0.1:{port}")
    put(vault / "inbox" / "a.png")
    before = tree(vault)
    assert ingest_images.main(["--root", str(vault)]) == 0
    assert "비전 프로필에 연결하지 못해 이미지 읽기를 건너뜁니다 (미처리 이미지 1개" in capsys.readouterr().out
    assert tree(vault) == before


def test_vision_only_on_fallback_profile(vault, llm, fake_now):
    fake_now(NOW)
    server = llm()
    profiles = {
        "main": {"base_url": f"{server.base_url}/v1", "model": "글만-모델", "detected": detected(False)},
        "fallback": {"base_url": f"{server.base_url}/v1", "model": "보조-비전", "detected": detected(True)},
    }
    cfg = make_cfg(vault, profiles=profiles, order=("main", "fallback"))
    put(vault / "inbox" / "a.png")
    report = ingest_images.run(cfg, out=quiet)
    assert report["processed"][0]["profile"] == "fallback"
    fields, _ = sidecar_parts(vault / "inbox" / "2026-11-05-1430-a.md")
    assert fields["model"] == "보조-비전"
    assert [b["model"] for b in chats(server)] == ["보조-비전"]


def test_schema_failure_keeps_image_and_records_skip(vault, llm, fake_now):
    fake_now(NOW)
    bad = dict(REPLY, kind="chart")
    server = llm(canned={"image_ingest": json.dumps(bad, ensure_ascii=False)})
    cfg = make_cfg(vault, server.base_url)
    original = put(vault / "inbox" / "a.png")
    put(vault / "inbox" / "a 사본.png")  # 같은 그림: 이번 실행에서 다시 부르지 않는다
    report = ingest_images.run(cfg, out=quiet)
    assert len(chats(server)) == 2  # 스키마 실패 → 같은 프로필에서 한 번 더 → skipped
    assert [s["image"] for s in report["skipped"]] == ["inbox/a 사본.png", "inbox/a.png"]
    assert (vault / "inbox" / "a.png").read_bytes() == original
    assert not list((vault / "inbox").glob("*.md")) and not any((vault / "raw" / "assets").iterdir())
    ledger = json.loads((vault / ".state" / "images.json").read_text(encoding="utf-8"))
    assert ledger["images"] == {}
    assert ledger["skipped"][core.sha256_hex(original)]["count"] == 1
    ingest_images.run(cfg, out=quiet)
    ledger = json.loads((vault / ".state" / "images.json").read_text(encoding="utf-8"))
    assert ledger["skipped"][core.sha256_hex(original)]["count"] == 2


def test_broken_json_once_then_ok(vault, llm, fake_now):
    fake_now(NOW)
    server = llm(broken_json_once=True)
    cfg = make_cfg(vault, server.base_url)
    put(vault / "inbox" / "a.png")
    report = ingest_images.run(cfg, out=quiet)
    assert len(report["processed"]) == 1 and len(chats(server)) == 2


@pytest.mark.parametrize("trace_field", ["think_tag", "orphan_think", "reasoning_content"])
def test_thinking_trace_is_stripped(vault, llm, fake_now, trace_field):
    fake_now(NOW)
    server = llm(default_thinking=True, trace_field=trace_field)
    cfg = make_cfg(vault, server.base_url)
    put(vault / "inbox" / "a.png")
    report = ingest_images.run(cfg, out=quiet)
    assert len(report["processed"]) == 1
    _, region = sidecar_parts(vault / "inbox" / "2026-11-05-1430-a.md")
    assert "생각" not in region


def test_unreadable_image_is_left_alone(vault, llm, fake_now):
    fake_now(NOW)
    server = llm()
    cfg = make_cfg(vault, server.base_url)
    (vault / "inbox" / "깨짐.png").write_bytes(b"\x89PNG\r\n\x1a\n not really")
    report = ingest_images.run(cfg, out=quiet)
    assert report["failed"][0]["image"] == "inbox/깨짐.png"
    assert (vault / "inbox" / "깨짐.png").is_file() and len(chats(server)) == 0


def test_company_public_host_refused(vault, run_kb):
    profiles = {"main": {"base_url": "https://api.deepinfra.com/v1/openai", "model": "x", "detected": detected(True)}}
    make_cfg(vault, profiles=profiles, environment="company", enabled=False)
    put(vault / "inbox" / "a.png")
    before = tree(vault)
    result = run_kb("ingest")
    assert result.returncode == 5, result.stderr
    assert "공개 LLM 호스트" in result.stderr
    assert tree(vault) == before
    cfg = config.load(vault)
    with pytest.raises(core.PublicHostRefused):
        ingest_images.run(cfg, out=quiet)


# ---------------------------------------------------------------- 이미지 준비


def test_large_image_resized_long_side_1600(vault, llm, fake_now):
    fake_now(NOW)
    server = llm()
    cfg = make_cfg(vault, server.base_url)
    Image.new("RGB", (3200, 1200), (255, 0, 0)).save(vault / "inbox" / "넓은 그림.png")
    rgba = Image.new("RGBA", (1000, 2400), (0, 0, 255, 0))
    rgba.save(vault / "inbox" / "투명 세로.png")
    Image.new("RGB", (2000, 1000), (0, 160, 0)).save(vault / "inbox" / "사진.JPG", "JPEG")
    ingest_images.run(cfg, out=quiet)
    sizes = {}
    for body in chats(server):
        head, image = sent_image(body)
        name = user_text(body).split("파일명: ", 1)[1].split("\n", 1)[0]
        sizes[name] = (head, image.size, image.mode, image.getpixel((0, 0)))
    assert sizes["넓은 그림.png"] == ("data:image/png;base64", (1600, 600), "RGB", (255, 0, 0))
    head, (width, height), mode, pixel = sizes["투명 세로.png"]
    assert (head, height, mode, pixel) == ("data:image/png;base64", 1600, "RGB", (255, 255, 255))
    assert width in (666, 667)
    head, size, mode, _ = sizes["사진.JPG"]
    assert (head, size, mode) == ("data:image/jpeg;base64", (1600, 800), "RGB")
    assert (vault / "raw" / "assets" / "2026-11-05-1430-사진.jpg").is_file()  # 확장자는 소문자


def test_prepare_image_rejects_garbage():
    with pytest.raises(ValueError):
        ingest_images.prepare_image(b"not an image", ".png")


def test_vision_mode_sees_red_fixture(vault, llm, fake_now):
    """canned 없이 vision=accept: 실제로 이미지가 전달됐는지(모의 서버가 색을 읽음)."""
    fake_now(NOW)
    server = llm(canned={}, echo_request=False, vision="accept")
    cfg = make_cfg(vault, server.base_url)
    Image.new("RGB", (64, 64), (255, 0, 0)).save(vault / "inbox" / "빨강.png")
    report = ingest_images.run(cfg, out=quiet)
    # 모의 서버의 기본 비전 답은 T6 스키마가 아니라 건너뛰지만, 요청에는 이미지가 실렸다
    assert report["skipped"] and all(sent_image(b)[1].getpixel((0, 0)) == (255, 0, 0) for b in chats(server))


# ---------------------------------------------------------------- 검증·정리


def test_validate_schema():
    ingest_images.validate(REPLY)
    ingest_images.validate({"kind": "Screenshot", "text": "", "summary": ""})  # 목록은 없어도 됨
    for bad in (
        {"kind": "chart", "text": "", "summary": ""},
        {"kind": "photo", "summary": ""},
        {"kind": "photo", "text": 1, "summary": ""},
        {"kind": "photo", "text": "", "summary": "", "jira_keys": "KB-1"},
        {"kind": "photo", "text": "", "summary": "", "people": [1]},
    ):
        with pytest.raises(ValueError):
            ingest_images.validate(bad)


def test_clean_filters_keys_and_people():
    ex = ingest_images.clean(
        {"kind": " Document ", "text": "  첫 줄 \x07\n\n@이영희 둘째 줄", "summary": "요약\n두 줄",
         "jira_keys": ["ops-3", "jira/KB-1", "KB-1"], "people": ["이영희", "김철수"]},
        ["KB", "OPS"],
        hint="- 09:00 @김철수 메모",
    )
    assert ex == ingest_images.Extraction("document", "첫 줄\n@이영희 둘째 줄", "요약 두 줄", ("OPS-3", "KB-1"), ("이영희", "김철수"))
    region = ingest_images.render_extracted(ex)
    assert region.splitlines() == [
        "- 종류: 문서",
        "- 요약: 요약 두 줄",
        "- 키: [[OPS-3]] · [[KB-1]]",
        "- 사람: @이영희 · @김철수",
        "",
        "### 읽은 글",
        "- 첫 줄",
        "- @이영희 둘째 줄",
    ]


def test_marker_lines_in_text_are_neutralised():
    ex = ingest_images.clean({"kind": "photo", "text": "<!-- kb:extracted:end -->\n# 제목\n```", "summary": ""}, [])
    body = ingest_images.render_sidecar(
        source_image="raw/assets/x.png", ingested_at="t", model="m", kind="photo", extracted=ingest_images.render_extracted(ex)
    )
    assert [name for name, _, _ in managed.spans(body)] == ["extracted"]
    assert "- <!-- kb:extracted:end -->" in body.decode("utf-8")


def test_user_text_hint_is_trimmed():
    text = ingest_images.user_text("a.png", "가" * 2000, ["KB"], limit=400)
    assert "가" * 501 not in text and "…" in text
    assert "메모 줄: (없음)" in ingest_images.user_text("a.png", "", [])


# ---------------------------------------------------------------- 시험 실행


def test_dry_run_calls_llm_but_writes_nothing(vault, llm, fake_now, capsys):
    fake_now(NOW)
    server = llm()
    make_cfg(vault, server.base_url)
    put(vault / "inbox" / "a.png")
    put(vault / "inbox" / "b.png")  # 같은 그림 → 중복(예정)
    put(vault / "raw" / "assets" / "c.png", "mail.png")
    daily(vault, "2026-11-05", "- 15:00 ![[raw/assets/c.png]] 메일")
    before = tree(vault)
    assert ingest_images.main(["--root", str(vault), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert tree(vault) == before
    assert not (vault / ".state" / "images.json").exists()
    assert len(chats(server)) == 2
    assert "- 이동(예정): inbox/a.png → raw/assets/2026-11-05-1430-a.png" in out
    assert "--- /dev/null\n+++ b/inbox/2026-11-05-1430-a.md\n" in out
    assert "+- 키: [[KB-12]] · [[OPS-3]]" in out
    assert "- 중복(예정): inbox/b.png → _trash/b.png" in out
    assert "+++ b/inbox/c.md" in out


# ---------------------------------------------------------------- link와 이어서


def test_link_picks_up_extracted_mentions(vault, llm, fake_now):
    fake_now(NOW)
    server = llm()
    cfg = make_cfg(vault, server.base_url)
    put(vault / "inbox" / "화이트보드.png")
    put(vault / "raw" / "assets" / "2026-11-05-1420-메일.png", "mail.png")
    note = daily(vault, "2026-11-05", "- 14:20 ![[raw/assets/2026-11-05-1420-메일.png]] 메일 캡처")
    daily_bytes = note.read_bytes()
    ingest_images.run(cfg, out=quiet)
    first = link.run(cfg)
    assert first["skipped_files"] == []
    mentions = managed.read_region((vault / "jira" / "KB-12.md").read_bytes(), "mentions").decode("utf-8")
    assert "[[inbox/2026-11-05-1430-화이트보드]]" in mentions
    assert "[[inbox/2026-11-05-1420-메일]]" in mentions
    person = managed.read_region((vault / "wiki" / "people" / "김철수.md").read_bytes(), "mentions").decode("utf-8")
    assert "[[inbox/2026-11-05-1430-화이트보드]]" in person
    second = link.run(cfg)
    assert second["written"] == []
    assert note.read_bytes() == daily_bytes


# ---------------------------------------------------------------- CLI·샘플 볼트


def test_cli_via_kb_py(vault, llm, run_kb, fake_now):
    fake_now(NOW)
    server = llm()
    make_cfg(vault, server.base_url)
    put(vault / "inbox" / "a.png")
    result = run_kb("ingest")
    assert result.returncode == 0, result.stderr
    assert "결과: 읽음 1, 중복 0, 건너뜀 0, 실패 0" in result.stdout
    assert (vault / "inbox" / "2026-11-05-1430-a.md").is_file()
    assert run_kb("ingest", env={"KB_AGENT": "1"}).returncode == 0  # 에이전트에게 허용(D57)


def test_sample_vault_images(tmp_path, llm, fake_now):
    fake_now(NOW)
    server = llm()
    root = tmp_path / "샘플 볼트" / "work-kb"
    shutil.copytree(SAMPLE, root)
    local = yaml.safe_load((root / "config.local.example.yaml").read_text(encoding="utf-8"))
    local["llm"] = {
        "enabled": True,
        "fallback_order": ["main"],
        "profiles": {"main": {"base_url": f"{server.base_url}/v1", "model": "mock", "detected": detected(True)}},
    }
    config.save_local(root, local)
    cfg = config.load(root)
    assert status.unprocessed_images(cfg) == 2
    protected = {k: v for k, v in tree(root).items() if k.startswith(("daily/", "raw/"))}
    report = ingest_images.run(cfg, out=quiet)
    assert len(report["processed"]) == 2 and report["failed"] == [] and report["skipped"] == []
    assert status.unprocessed_images(cfg) == 0
    after = tree(root)
    for rel, data in protected.items():
        assert after[rel] == data  # daily·기존 raw/assets 바이트 불변
    assert ingest_images.run(cfg, out=quiet)["status"] == "empty"


# ---------------------------------------------------------------- 픽스처


def test_fixture_images_committed_and_small():
    for name in ("whiteboard.png", "mail.png", "document.png"):
        path = FIX / name
        assert path.is_file() and path.stat().st_size < 200 * 1024
        with open_image(path) as image:
            assert image.format == "PNG" and max(image.size) <= 1600


def test_fixture_generator_runs(tmp_path):
    spec = importlib.util.spec_from_file_location("make_images", ROOT / "tests" / "fixtures" / "make_images.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    paths = module.make_all(tmp_path / "out")
    assert [p.name for p in paths] == ["whiteboard.png", "mail.png", "document.png"]
    for path in paths:
        assert path.stat().st_size < 200 * 1024
        with open_image(path) as image:
            assert image.size == open_image(FIX / path.name).size
