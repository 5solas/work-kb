-- 파일명 규칙 (ERT work-kb-test-safe-name, work-kb-test-link-safe-asset-and-inbox 앞부분).
local H = require("helpers")

local function names()
  return require("work-kb.names")
end

describe("names", function()
  it("safe_name 표 (core.safe_name과 같은 규칙)", function()
    local n = names()
    H.eq(n.safe_name('a<b>:c"d/e\\f|g?h*i'), "abcdefghi")
    H.eq(n.safe_name("  여러   공백 . "), "여러 공백")
    H.eq(n.safe_name(""), "_")
    H.eq(n.safe_name(nil), "_")
    H.eq(n.safe_name("..."), "_")
    H.eq(n.safe_name("CON"), "_CON")
    H.eq(n.safe_name("con.txt"), "_con.txt")
    H.eq(n.safe_name("COM¹"), "_COM¹")
    H.eq(n.safe_name("lpt³.md"), "_lpt³.md")
    H.eq(n.safe_name("CON .txt"), "_CON .txt")
    H.eq(n.safe_name("CONIN$"), "_CONIN$")
    H.eq(n.safe_name("CONSOLE"), "CONSOLE")
    H.eq(n.safe_name("COM0"), "COM0")
    H.eq(n.safe_name("탭\t과\n줄"), "탭과줄", "제어 문자는 지운다 (공백으로 바꾸지 않음)")
  end)

  it("folds unicode spaces like core.safe_name", function()
    -- Python 3.14 core.safe_name 결과 (`\s`는 유니코드 공백: U+0085 U+00A0 U+1680 U+2000–200A U+2028/9 U+202F U+205F U+3000)
    local n = names()
    local cases = {
      { "회의 메모", "회의 메모" },
      { "회의\227\128\128메모", "회의 메모" }, -- U+3000
      { "\227\128\128앞 공백", "앞 공백" },
      { "끝 공백\194\160", "끝 공백" }, -- U+00A0
      { "a\194\133b", "a b" }, -- U+0085
      { "a\226\128\168b\226\128\169c", "a b c" }, -- U+2028 U+2029
      { "x\225\154\128y\226\128\128z\226\128\138", "x y z" }, -- U+1680 U+2000 U+200A
      { "p\226\128\175q\226\129\159r", "p q r" }, -- U+202F U+205F
      { "\194\160 \227\128\128 ", "_" },
      { "a\226\128\139b", "a\226\128\139b" }, -- U+200B는 공백이 아니다
      { "가\226\128\130\226\128\131나", "가 나" }, -- U+2002 U+2003 이어진 것은 하나로
    }
    for _, c in ipairs(cases) do
      H.eq(n.safe_name(c[1]), c[2], vim.inspect(c[1]))
    end
  end)

  it("80글자 제한은 바이트가 아니라 글자", function()
    local n = names()
    local long = string.rep("가", 100)
    local cut = n.safe_name(long)
    H.eq(n.length(cut), 80)
    H.eq(#cut, 240, "한글 80자 = 240바이트")
    H.eq(n.safe_name(string.rep("a", 79) .. " .b"), string.rep("a", 79), "자른 뒤 끝의 공백·점은 지운다")
    H.eq(n.length(n.safe_name(string.rep("😀", 90))), 80)
    H.eq(n.safe_name("가나다", 2), "가나")
  end)

  it("link_safe_name은 [ ] # ^ |를 -로", function()
    local n = names()
    H.eq(n.link_safe_name("회의 [초안] #2"), "회의 -초안- -2")
    H.eq(n.link_safe_name("a^b|c]"), "a-bc-")
    H.eq(n.link_safe_name("CON"), "_CON")
    H.eq(n.link_safe_name(" . "), "_")
    H.eq(n.safe_name("이슈 #12 [a]"), "이슈 #12 [a]", "safe_name은 그대로")
  end)

  it("nfc: 조합형 한글 자모를 완성형으로, 나머지는 그대로", function()
    local n = names()
    -- ᄒ ᅡ ᆫ ᄀ ᅳ ᆯ (U+1112 U+1161 U+11AB U+1100 U+1173 U+11AF) → 한글
    local decomposed = "\225\132\146\225\133\161\225\134\171\225\132\128\225\133\179\225\134\175"
    H.eq(n.nfc(decomposed), "한글")
    H.eq(n.nfc("ᄀ" .. "ᅡ"), "가", "L+V")
    H.eq(n.nfc("가" .. "\225\134\168"), "각", "LV+T")
    H.eq(n.nfc("각" .. "\225\134\168"), "각\225\134\168", "LVT 뒤의 T는 붙이지 않는다")
    local latin = "e\204\129" -- e + U+0301: 한글이 아니므로 그대로 둔다 (Python NFC와 다른 점)
    H.eq(n.nfc(latin), latin)
    H.eq(n.nfc("이미 완성형 😀"), "이미 완성형 😀")
    H.eq(n.safe_name(decomposed .. ".md"), "한글.md")
    local raw = "a\255b"
    H.eq(n.nfc(raw), raw, "날 바이트는 그대로")
  end)

  it("unique_path는 덮어쓰지 않고 -2, -3", function()
    local n = names()
    local dir = H.make_vault("이름")
    local path = dir .. "/inbox/메모.md"
    H.eq(n.unique_path(path), path)
    H.write_bytes(path, "x")
    H.eq(n.unique_path(path), dir .. "/inbox/메모-2.md")
    H.write_bytes(dir .. "/inbox/메모-2.md", "x")
    H.eq(n.unique_path(path), dir .. "/inbox/메모-3.md")
    local tar = dir .. "/raw/a.tar.gz"
    H.write_bytes(tar, "x")
    H.eq(n.unique_path(tar), dir .. "/raw/a.tar-2.gz")
    local dot = dir .. "/raw/.hidden"
    H.write_bytes(dot, "x")
    H.eq(n.unique_path(dot), dir .. "/raw/.hidden-2")
  end)
end)
