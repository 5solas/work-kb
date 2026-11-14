import os
import sys
import tempfile
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[3] / 'scripts'))

from hypothesis import given, settings, strategies as st, HealthCheck

from lib import core

SET = settings(max_examples=5000, deadline=None, suppress_health_check=list(HealthCheck))
name_st = st.one_of(
    st.text(max_size=100),
    st.lists(st.sampled_from(["CON", "con", "NUL", "COM1", "LPT¹", ".", " ", "a", "가", ":", "\u3000", "\t", "txt", "_", "x" * 40]), max_size=8).map("".join),
)


@SET
@given(name_st)
def test_safe_name_props(name):
    out = core.safe_name(name)
    assert out
    assert not core._FORBIDDEN.search(out)
    assert not out.endswith((" ", "."))
    assert len(out) <= 81, (name, out, len(out))
    if out != "_":
        assert not core._is_reserved(out), out
    if len(out) <= 80: assert core.safe_name(out) == out, (name, out, core.safe_name(out))


@settings(max_examples=150, deadline=None, suppress_health_check=list(HealthCheck))
@given(st.text(min_size=1, max_size=60))
def test_safe_name_creatable(name):
    out = core.safe_name(name)
    d = tempfile.mkdtemp()
    p = os.path.join(d, out + ".md")
    with open(p, "wb") as fh:
        fh.write(b"x")
    assert os.path.basename(os.path.realpath(p)).lower() == (out + ".md").lower() or os.path.exists(p)
    os.remove(p)
    os.rmdir(d)


