"""허구 이미지 픽스처 3장을 pillow로 그린다(실제 화면 캡처·사진 없음).

    python tests/fixtures/make_images.py            # tests/fixtures/images/에 씀
    python tests/fixtures/make_images.py --out DIR  # 다른 폴더에

- whiteboard.png: 회의 화이트보드 사진 흉내. 한국어 글씨와 키 KB-12.
- mail.png: 메일 프로그램 화면 흉내. `@김철수` 언급, 주소는 example.invalid.
- document.png: 문서 캡처 흉내. 배포 절차 초안, 키 OPS-3.

이름·주소·회사는 모두 지어낸 것이다. 한글 글꼴(맑은 고딕 등)이 없으면 기본 글꼴로 그리고 경고한다
(한글이 네모로 나온다). 저장소에 커밋된 PNG는 Windows 11의 맑은 고딕으로 만든 것이다.
각 파일은 200 KB 미만이어야 한다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / "images"
NAMES = ("whiteboard.png", "mail.png", "document.png")
MAX_BYTES = 200 * 1024

_FONT_CANDIDATES = (
    "C:/Windows/Fonts/malgun.ttf",
    "C:/Windows/Fonts/gulim.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
)
_BOLD_CANDIDATES = ("C:/Windows/Fonts/malgunbd.ttf",)


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for candidate in (*(_BOLD_CANDIDATES if bold else ()), *_FONT_CANDIDATES):
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    print("경고: 한글 글꼴을 찾지 못해 기본 글꼴로 그립니다", file=sys.stderr)
    try:
        return ImageFont.load_default(size)
    except TypeError:  # 옛 pillow
        return ImageFont.load_default()


def whiteboard() -> Image.Image:
    """화이트보드 사진: 회색 테두리, 흰 판, 색 마커 글씨, 손으로 그은 화살표·밑줄."""
    img = Image.new("RGB", (960, 640), (92, 96, 104))  # 벽
    draw = ImageDraw.Draw(img)
    draw.rectangle((40, 36, 920, 580), fill=(214, 216, 220))  # 알루미늄 테두리
    draw.rectangle((54, 50, 906, 566), fill=(246, 247, 244))  # 판
    draw.rectangle((300, 580, 660, 600), fill=(170, 172, 176))  # 마커 받침
    blue, red, black, green = (28, 66, 168), (196, 40, 36), (34, 34, 38), (30, 128, 62)
    title = _font(40, bold=True)
    body = _font(32)
    draw.text((90, 78), "캐시 설계 회의 (허구)", font=title, fill=black)
    draw.line((90, 132, 520, 128), fill=black, width=3)
    draw.text((90, 170), "KB-12  updated는 UTC로 저장", font=body, fill=blue)
    draw.text((90, 230), "만료 시각 계산 → 서버에서 한 번만", font=body, fill=blue)
    draw.text((90, 290), "front matter 필드는 최소로", font=body, fill=green)
    draw.text((90, 360), "담당: @김철수 (리뷰)", font=body, fill=red)
    draw.text((90, 420), "다음: 체크리스트 정리 · 금요일", font=body, fill=black)
    # 손으로 그린 상자와 화살표
    draw.rectangle((640, 170, 870, 330), outline=red, width=4)
    draw.text((664, 200), "캐시", font=body, fill=red)
    draw.text((664, 250), "TTL 1h", font=body, fill=red)
    draw.line((608, 250, 630, 250), fill=red, width=4)
    draw.polygon(((630, 238), (648, 250), (630, 262)), fill=red)
    draw.ellipse((86, 470, 106, 490), fill=green)
    draw.text((120, 462), "결정: UTC 저장", font=body, fill=green)
    return img


def mail() -> Image.Image:
    """메일 프로그램 화면: 제목 표시줄, 머리글 칸, 본문."""
    img = Image.new("RGB", (900, 560), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, 900, 40), fill=(36, 74, 140))
    small = _font(18)
    body = _font(22)
    bold = _font(24, bold=True)
    draw.text((16, 9), "받은편지함 - 가상메일 (허구)", font=small, fill=(255, 255, 255))
    for i, x in enumerate((840, 866)):
        draw.rectangle((x, 12, x + 16, 28), outline=(255, 255, 255), width=2 if i else 1)
    draw.rectangle((0, 40, 200, 560), fill=(240, 243, 248))
    for i, label in enumerate(("받은편지함 (3)", "보낸편지함", "임시보관함", "중요")):
        draw.text((20, 64 + i * 40), label, font=small, fill=(40, 40, 40))
    draw.rectangle((0, 56, 200, 92), outline=(36, 74, 140), width=2)
    x0 = 224
    draw.text((x0, 60), "[KB-7] 캐시 만료 오류 확인 요청", font=bold, fill=(20, 20, 20))
    draw.text((x0, 104), "보낸 사람: 김철수 <kim@example.invalid>", font=small, fill=(80, 80, 80))
    draw.text((x0, 132), "받는 사람: 홍길동 <hong@example.invalid>", font=small, fill=(80, 80, 80))
    draw.text((x0, 160), "날짜: 2026-11-05 (목) 10:42", font=small, fill=(80, 80, 80))
    draw.line((x0, 196, 876, 196), fill=(210, 210, 210), width=1)
    lines = (
        "@김철수 님이 회원님을 언급했습니다.",
        "",
        "만료 시각이 9시간 어긋나는 문제를 확인했습니다.",
        "KB-7 에 재현 절차를 적어 두었습니다.",
        "내일 오전까지 검토 부탁드립니다.",
        "",
        "- 김철수 드림",
    )
    for i, line in enumerate(lines):
        draw.text((x0, 216 + i * 36), line, font=body, fill=(30, 30, 30))
    draw.rectangle((x0, 486, x0 + 96, 522), fill=(36, 74, 140))
    draw.text((x0 + 26, 492), "답장", font=small, fill=(255, 255, 255))
    return img


def document() -> Image.Image:
    """문서 캡처: 흰 페이지, 제목, 번호 목록, 간단한 표."""
    img = Image.new("RGB", (800, 1000), (232, 232, 232))
    draw = ImageDraw.Draw(img)
    draw.rectangle((40, 30, 760, 970), fill=(255, 255, 255), outline=(200, 200, 200))
    title = _font(36, bold=True)
    body = _font(24)
    small = _font(18)
    draw.text((80, 70), "배포 절차 (초안, 허구)", font=title, fill=(20, 20, 20))
    draw.text((80, 126), "문서 번호 DOC-0001 · 관련 일감 OPS-3", font=small, fill=(110, 110, 110))
    draw.line((80, 160, 720, 160), fill=(60, 60, 60), width=2)
    steps = (
        "1. 스테이징에 배포하고 상태 확인",
        "2. 체크리스트 서명 (담당 @이영희)",
        "3. 운영 배포는 화요일 오후에만",
        "4. 문제가 있으면 바로 롤백",
    )
    for i, line in enumerate(steps):
        draw.text((80, 190 + i * 48), line, font=body, fill=(30, 30, 30))
    top = 420
    rows = (("단계", "담당", "예상 시간"), ("스테이징", "홍길동", "30분"), ("운영", "이영희", "1시간"), ("롤백", "김철수", "15분"))
    cols = (80, 300, 520, 720)
    for r, row in enumerate(rows):
        y = top + r * 52
        if r == 0:
            draw.rectangle((cols[0], y, cols[-1], y + 52), fill=(238, 241, 246))
        for c, cell in enumerate(row):
            draw.text((cols[c] + 14, y + 12), cell, font=body, fill=(30, 30, 30))
    for r in range(len(rows) + 1):
        draw.line((cols[0], top + r * 52, cols[-1], top + r * 52), fill=(150, 150, 150), width=1)
    for x in cols:
        draw.line((x, top, x, top + len(rows) * 52), fill=(150, 150, 150), width=1)
    draw.text((80, 680), "비고: 이 문서는 테스트용으로 지어낸 것이다.", font=small, fill=(110, 110, 110))
    draw.text((370, 930), "- 1 -", font=small, fill=(140, 140, 140))
    return img


BUILDERS = {"whiteboard.png": whiteboard, "mail.png": mail, "document.png": document}


def make_all(out: Path = OUT) -> list[Path]:
    """세 장을 만들어 저장하고 경로를 돌려준다. 200 KB를 넘으면 ValueError."""
    out.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for name in NAMES:
        path = out / name
        BUILDERS[name]().save(path, "PNG", optimize=True)
        size = path.stat().st_size
        if size >= MAX_BYTES:
            raise ValueError(f"{path}: {size} 바이트 (200 KB 이상)")
        paths.append(path)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="허구 이미지 픽스처 3장 만들기")
    parser.add_argument("--out", type=Path, default=OUT, help=f"저장 폴더 (기본: {OUT})")
    args = parser.parse_args(argv)
    for path in make_all(args.out):
        print(f"{path} ({path.stat().st_size:,} 바이트)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
