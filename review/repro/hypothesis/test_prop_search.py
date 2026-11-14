import sys
import unicodedata
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[3] / 'scripts'))

from hypothesis import given, settings, strategies as st, assume, HealthCheck

import search

SET = settings(max_examples=5000, deadline=None, suppress_health_check=list(HealthCheck))
ch = st.characters(codec="utf-8", exclude_categories=("Cs",))
korean = st.sampled_from(list("가각간한국어캐시배포김철수") + ["\u1100\u1161", "\u11a8", "\u0301", "e", "E", "ß", "İ", "ﬃ", " ", "\t", "Σ", "ς"])
text_st = st.one_of(st.text(alphabet=ch, max_size=12), st.lists(korean, max_size=10).map("".join))


@SET
@given(text_st)
def test_query_matches_its_own_text(text):
    terms = search.parse_terms(text)
    assert search.matches(text, terms)


@SET
@given(text_st, st.data())
def test_substring_of_nfc_text_matches(text, data):
    text = unicodedata.normalize("NFC", text)
    assume(text)
    i = data.draw(st.integers(0, len(text)))
    j = data.draw(st.integers(i, len(text)))
    q = text[i:j]
    terms = search.parse_terms(q)
    assert search.matches(text, terms), (text, q, terms)


@SET
@given(text_st)
def test_case_insensitive(text):
    terms = search.parse_terms(text.upper())
    assert search.matches(text.lower(), terms) or text.upper().lower() != text.lower()


@SET
@given(text_st, text_st)
def test_nfd_query_vs_nfc_text(a, b):
    line = a + " " + b
    q = unicodedata.normalize("NFD", b)
    assert search.matches(unicodedata.normalize("NFC", line), search.parse_terms(q))
