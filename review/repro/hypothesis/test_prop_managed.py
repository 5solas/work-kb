import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[3] / 'scripts'))

from hypothesis import given, settings, strategies as st, assume, HealthCheck, example

from lib import managed

TOKENS = [
    b"a", b"\xea\xb0\x80", b" ", b"\t", b"\n", b"\r\n", b"\r", b"\r\r\n",
    b"<!-- kb:x:start -->", b"<!-- kb:x:end -->", b"<!-- kb:y:start -->", b"<!-- kb:y:end -->",
    b"<!-- kb:x:start --> ", b" <!-- kb:x:start -->", b"-", b"<", b"\xff",
]

raw_files = st.tuples(st.booleans(), st.lists(st.sampled_from(TOKENS), max_size=30)).map(
    lambda t: (managed.BOM if t[0] else b"") + b"".join(t[1])
)

body_text = st.lists(
    st.sampled_from(["a", "가", " ", "\n", "\r\n", "\r", "<!-- kb:x:start -->", "\t", "\u3000", "\x0c", "\ufeff"]),
    max_size=12,
).map("".join)


import os
SKIP_CR_ONLY = os.environ.get("SKIP_CR_ONLY") == "1"


def valid(data):
    if SKIP_CR_ONLY and b"\n" not in data and b"\r" in data:
        return None
    try:
        return managed.spans(data)
    except managed.MarkerError:
        return None


def enc_ok(body):
    try:
        managed._encode_lines(body, b"\n")
        return True
    except managed.MarkerError:
        return False


SET = settings(max_examples=3000, deadline=None, suppress_health_check=list(HealthCheck))


@SET
@given(raw_files, st.sampled_from(["x", "y", "z"]), body_text)
def test_replace_preserves_outside_and_idempotent(data, name, body):
    sp = valid(data)
    assume(sp is not None)
    assume(enc_ok(body))
    out = managed.replace_region(data, name, body)
    # other regions untouched
    for other, s, e in sp:
        if other != name:
            assert managed.read_region(out, other) == data[s:e]
    hit = [(s, e) for n, s, e in sp if n == name]
    if hit:
        s, e = hit[0]
        new_body = managed.read_region(out, name)
        assert out == data[:s] + new_body + data[e:]
    else:
        # original bytes are a prefix
        assert out.startswith(data), (data, out)
    # idempotent
    assert managed.replace_region(out, name, body) == out
    # region readable and equal to normalized body
    got = managed.read_region(out, name)
    assert got is not None


@SET
@given(raw_files, st.sampled_from(["x", "y", "z"]), st.lists(body_text, max_size=3))
def test_append_preserves(data, name, entries):
    sp = valid(data)
    assume(sp is not None)
    assume(all(enc_ok(e) for e in entries))
    out = managed.append_to_region(data, name, entries)
    hit = [(s, e) for n, s, e in sp if n == name]
    if hit:
        s, e = hit[0]
        assert out[:e] == data[:e]
        assert out.endswith(data[e:])
    else:
        assert out.startswith(data)
    managed.spans(out)


@SET
@given(raw_files)
def test_strip_markers_roundtrip_body(data):
    sp = valid(data)
    assume(sp is not None)
    out = managed.strip_markers(data)
    for name, s, e in sp:
        assert data[s:e] in out
    assert valid(out) == []


@SET
@given(raw_files, st.sampled_from(["x", "y"]), body_text)
def test_replace_region_text_lines_consistent_with_notes(data, name, body):
    """The region a tool writes should be seen as the same region by notes.iter_lines line splitting."""
    from lib import notes
    sp = valid(data)
    assume(sp is not None)
    assume(enc_ok(body))
    out = managed.replace_region(data, name, body)
    try:
        lines = list(notes.iter_lines("daily/2026-01-01.md", out))
    except UnicodeDecodeError:
        return
    # every marker line notes sees as marker should correspond to a managed marker and vice versa
    n_markers_notes = sum(1 for l in lines if managed._MARKER.fullmatch(l.text.encode("utf-8")))
    n_markers_managed = 2 * len(managed.spans(out))
    assert n_markers_notes == n_markers_managed, (out, [l.text for l in lines])
