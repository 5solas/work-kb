import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[3] / 'scripts'))

from hypothesis import given, settings, strategies as st, assume, HealthCheck

from lib import frontmatter, core

SET = settings(max_examples=2000, deadline=None, suppress_health_check=list(HealthCheck))

# realistic keys (as used by tools) plus some odd ones
key_st = st.one_of(
    st.sampled_from(["date", "updated", "stub", "status", "summary", "title", "name", "aliases", "상태", "key"]),
    st.from_regex(r"\A[a-z\uac00-\ud7a3][a-z0-9_\uac00-\ud7a3-]{0,6}\Z"),
)
val_char = st.characters(codec="utf-8", exclude_categories=("Cs",), exclude_characters="\x85")
val_st = st.one_of(
    st.text(alphabet=val_char, max_size=20),
    st.sampled_from(["on", "NO", "2026-11-05", "", "---", "a: b", "# c", "- x", "'q'", '"d"', "line1\nline2", "\n---\n", "x\r\ny"]),
    st.lists(st.text(alphabet=val_char, max_size=6), max_size=3),
)

base_st = st.sampled_from([
    b"",
    b"body only\n",
    b"---\ndate: 2026-11-05\n---\n# t\n",
    b"---\r\ndate: 2026-11-05\r\ntitle: x\r\n---\r\nbody\r\n",
    b"\xef\xbb\xbf---\ntitle: '\xea\xb0\x80'\n# comment\nlist:\n  - a\n  - b\n---\nbody\n",
    b"---\n---\nbody\n",
    b"---\nkey: KB-1\nproject: KB\nstub: true\nsummary: \"\"\nstatus: \"\"\n---\n# KB-1\n",
])


def expected(v):
    if isinstance(v, list):
        return [str(x) for x in v]
    return v


@SET
@given(base_st, key_st, val_st)
def test_set_then_parse_roundtrip_and_idempotent(data, key, value):
    try:
        before = frontmatter.parse(data)
    except core.ConfigError:
        return
    out = frontmatter.set_fields(data, {key: value})
    parsed = frontmatter.parse(out)
    assert parsed.get(key) == expected(value), (out, parsed)
    for k, v in before.items():
        if k != key:
            assert parsed.get(k) == v
    again = frontmatter.set_fields(out, {key: value})
    assert again == out, (out, again)
    # body preserved
    assert frontmatter.split(out)[1] == frontmatter.split(data)[1] or frontmatter.split(data)[0] == b""


@SET
@given(base_st, key_st)
def test_delete_key(data, key):
    try:
        frontmatter.parse(data)
    except core.ConfigError:
        return
    out = frontmatter.set_fields(data, {key: "v"})
    out2 = frontmatter.set_fields(out, {key: None})
    assert key not in frontmatter.parse(out2)


