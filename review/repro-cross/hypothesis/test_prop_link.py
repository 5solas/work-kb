import re
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[3] / 'scripts'))

from hypothesis import given, settings, strategies as st, assume, HealthCheck

import link
from lib import notes

SET = settings(max_examples=5000, deadline=None, suppress_health_check=list(HealthCheck))
KEYS = ("KB", "OPS", "AB")

TOK = [
    "KB-1", "KB-12", "OPS-3", "AB-7", "XKB-1", " ", "가", "a", "-", ".", "/", "[", "]", "[[", "]]", "|", "#",
    "`", "``", "https://x.com/", "http://j/browse/", "(", ")", "@", "=", "!", "<", ">", "'", '"', "_", "1",
    "jira/", ".md", ":", "\u3000", "KB-١", "KB-１",
]
line_st = st.lists(st.sampled_from(TOK), max_size=14).map("".join)


@SET
@given(line_st)
def test_normalize_idempotent(text):
    once, _ = link.normalize_text(text, KEYS)
    twice, n = link.normalize_text(once, KEYS)
    assert twice == once and n == 0, (text, once, twice)


def protected_spans(text):
    spans = []
    for pat in (notes.INLINE_CODE_RE, notes.URL_RE):
        spans += [(m.start(), m.end()) for m in pat.finditer(text)]
    return spans


@SET
@given(line_st)
def test_normalize_never_touches_code_or_url(text):
    out, n = link.normalize_text(text, KEYS)
    assume(n > 0)
    # removing inserted brackets must give back the original text
    # and every protected span of the original must still appear verbatim in output
    for s, e in protected_spans(text):
        assert text[s:e] in out, (text, out, text[s:e])
    # only change is insertion of [[ ]] around keys
    stripped = out
    for m in re.finditer(r"\[\[((?:KB|OPS|AB)-\d+)\]\]", out):
        pass
    assert out.replace("[[", "").replace("]]", "") == text.replace("[[", "").replace("]]", ""), (text, out)


@SET
@given(line_st)
def test_normalized_keys_are_ascii_digits(text):
    out, n = link.normalize_text(text, KEYS)
    for m in notes.find_keys(out, KEYS):
        if m.bracketed:
            assert re.fullmatch(r"[A-Z][A-Z0-9_]+-[0-9]+", m.key), (text, out, m.key)


people_tok = ["@", "김철수", "가", " ", "a", "x@y", "`", "https://a.com/@b", "\u1100\u1161", "@\u1100\u1175\u11b7", "."]
people_st = st.lists(st.sampled_from(people_tok), max_size=10).map("".join)


@SET
@given(people_st)
def test_people_nfc_nfd_equivalent(text):
    import unicodedata
    a = notes.find_people(unicodedata.normalize("NFC", text))
    b = notes.find_people(unicodedata.normalize("NFD", text))
    assert [unicodedata.normalize("NFC", x) for x in b] == a, (text, a, b)
