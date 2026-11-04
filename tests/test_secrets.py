"""lib/secrets.py: 참조 해석, 회사 환경의 env 거부, 저장 크기 한도, 다시 읽기 확인, 안내 문구.

keyring은 conftest가 파일 기반 가짜(fake_keyring.FileKeyring)로 바꿔 둔다.
"""
from __future__ import annotations

from pathlib import Path

import keyring
import keyring.errors
import pytest
from keyring.backends import chainer, fail, null

import fake_keyring
from lib import config, core
from lib import secrets as kb_secrets

HOME = config.Config(root=Path("C:/kb"), data={}, environment="home")
COMPANY = config.Config(root=Path("C:/kb"), data={}, environment="company")


# ---------------------------------------------------------------- parse_ref


@pytest.mark.parametrize(
    "ref, expected",
    [
        ("keyring:work-kb/jira", kb_secrets.Ref("keyring", "work-kb", "jira")),
        ("keyring:work-kb/llm-fallback", kb_secrets.Ref("keyring", "work-kb", "llm-fallback")),
        ("keyring:work-kb/hong.gildong@example.invalid", kb_secrets.Ref("keyring", "work-kb", "hong.gildong@example.invalid")),
        ("env:DEEPINFRA_API_KEY", kb_secrets.Ref("env", "", "DEEPINFRA_API_KEY")),
        ("env:_X1", kb_secrets.Ref("env", "", "_X1")),
    ],
)
def test_parse_ref_valid(ref, expected):
    parsed = kb_secrets.parse_ref(ref)
    assert parsed == expected
    assert str(parsed) == ref


@pytest.mark.parametrize(
    "ref",
    [
        "",
        "keyring:",
        "keyring:work-kb",
        "keyring:/jira",
        "keyring:work-kb/",
        "keyring:work-kb/a/b",
        "keyring: work-kb/jira",
        "keyring:work-kb/jira ",
        "KEYRING:work-kb/jira",
        "env:",
        "env:1ABC",
        "env:A-B",
        "env:$X",
        "file:C:/secret.txt",
        None,
        123,
    ],
)
def test_parse_ref_invalid(ref):
    with pytest.raises(core.ConfigError) as info:
        kb_secrets.parse_ref(ref)  # type: ignore[arg-type]
    assert info.value.exit_code == 4


def test_parse_ref_does_not_echo_raw_values():
    with pytest.raises(core.ConfigError) as info:
        kb_secrets.parse_ref("plain-token-abc123")
    assert "plain-token-abc123" not in str(info.value)


# ---------------------------------------------------------------- resolve


def test_resolve_keyring_and_register_secret():
    keyring.set_password("work-kb", "jira", "tok-resolve-1234")
    assert kb_secrets.resolve("keyring:work-kb/jira", COMPANY) == "tok-resolve-1234"
    assert core.redact("헤더 tok-resolve-1234 끝") == "헤더 *** 끝"


def test_resolve_missing_and_none():
    assert kb_secrets.resolve(None, HOME) is None
    assert kb_secrets.resolve("", HOME) is None
    assert kb_secrets.resolve("keyring:work-kb/missing", HOME) is None


def test_resolve_env_at_home(monkeypatch):
    assert kb_secrets.resolve("env:DEEPINFRA_API_KEY", HOME) is None  # conftest가 지움
    monkeypatch.setenv("DEEPINFRA_API_KEY", "di-test-value-5678")
    assert kb_secrets.resolve("env:DEEPINFRA_API_KEY", HOME) == "di-test-value-5678"
    assert "di-test-value-5678" not in core.redact("x di-test-value-5678")


def test_resolve_env_refused_at_company(monkeypatch):
    monkeypatch.setenv("DEEPINFRA_API_KEY", "di-test-value-5678")
    with pytest.raises(core.ConfigError, match="회사"):
        kb_secrets.resolve("env:DEEPINFRA_API_KEY", COMPANY)


def test_resolve_env_other_names_refused_at_home(monkeypatch):
    monkeypatch.setenv("OTHER_API_KEY", "x-value-9999")
    with pytest.raises(core.ConfigError, match="DEEPINFRA_API_KEY"):
        kb_secrets.resolve("env:OTHER_API_KEY", HOME)


def test_resolve_with_loaded_config(vault):
    (vault / "config.local.yaml").write_text("environment: home\n", encoding="utf-8")
    cfg = config.load(vault, environ={})
    keyring.set_password("work-kb", "confluence", "conf-token-0001")
    assert kb_secrets.resolve("keyring:work-kb/confluence", cfg) == "conf-token-0001"
    (vault / "config.local.yaml").unlink()
    cfg = config.load(vault, environ={})  # 파일이 없으면 company
    with pytest.raises(core.ConfigError):
        kb_secrets.resolve("env:DEEPINFRA_API_KEY", cfg)


class _BrokenRead(fake_keyring.FileKeyring):
    def get_password(self, service, username):
        raise keyring.errors.KeyringError("backend down")


def test_resolve_wraps_backend_errors(tmp_path):
    keyring.set_keyring(_BrokenRead(tmp_path / "k.json"))
    with pytest.raises(core.KbError) as info:
        kb_secrets.resolve("keyring:work-kb/jira", HOME)
    assert "자격 증명" in str(info.value)
    assert not isinstance(info.value, core.ConfigError)


# ---------------------------------------------------------------- store


def test_store_round_trip():
    kb_secrets.store("work-kb", "jira", "mock-token")
    assert keyring.get_password("work-kb", "jira") == "mock-token"
    assert kb_secrets.resolve("keyring:work-kb/jira", HOME) == "mock-token"


@pytest.mark.parametrize("value", ["", "   ", None])
def test_store_rejects_empty(value):
    with pytest.raises(core.ConfigError):
        kb_secrets.store("work-kb", "jira", value)  # type: ignore[arg-type]
    assert keyring.get_password("work-kb", "jira") is None


@pytest.mark.parametrize(
    "value, ok",
    [
        ("a" * 1280, True),
        ("a" * 1281, False),
        ("가" * 1280, True),
        ("가" * 1281, False),
        ("\U0001F600" * 640, True),  # 서로게이트 쌍 = 4바이트
        ("\U0001F600" * 641, False),
    ],
)
def test_store_size_limit(value, ok):
    if ok:
        kb_secrets.store("work-kb", "llm", value)
        assert keyring.get_password("work-kb", "llm") == value
    else:
        with pytest.raises(core.ConfigError, match="2560"):
            kb_secrets.store("work-kb", "llm", value)
        assert keyring.get_password("work-kb", "llm") is None


def test_store_requires_service_and_name():
    with pytest.raises(core.ConfigError):
        kb_secrets.store("", "jira", "x-value-1")
    with pytest.raises(core.ConfigError):
        kb_secrets.store("work-kb", "", "x-value-1")


class _Mangling(fake_keyring.FileKeyring):
    def set_password(self, service, username, password):
        super().set_password(service, username, password[:-1])


class _BrokenWrite(fake_keyring.FileKeyring):
    def set_password(self, service, username, password):
        raise keyring.errors.PasswordSetError("write failed for " + password)


def test_store_read_back_mismatch(tmp_path):
    keyring.set_keyring(_Mangling(tmp_path / "k.json"))
    with pytest.raises(core.KbError, match="다시 읽었더니"):
        kb_secrets.store("work-kb", "jira", "token-abcdef")


def test_store_wraps_backend_errors_without_leaking(tmp_path):
    keyring.set_keyring(_BrokenWrite(tmp_path / "k.json"))
    with pytest.raises(core.KbError) as info:
        kb_secrets.store("work-kb", "jira", "token-leak-check")
    message = str(info.value)
    assert "자격 증명" in message and "PasswordSetError" in message
    assert "token-leak-check" not in message


class _EmptyChainer(chainer.ChainerBackend):
    backends = []  # type: ignore[assignment]


@pytest.mark.parametrize("backend", [fail.Keyring(), null.Keyring(), _EmptyChainer()])
def test_store_rejects_unusable_backends(backend):
    keyring.set_keyring(backend)
    with pytest.raises(core.KbError) as info:
        kb_secrets.store("work-kb", "jira", "token-abcdef")
    assert not isinstance(info.value, core.ConfigError)
    assert "자격 증명" in str(info.value)


# ---------------------------------------------------------------- backend_name


def test_backend_name_is_fake():
    assert kb_secrets.backend_name() == "fake_keyring.FileKeyring"


def test_backend_name_chainer():
    keyring.set_keyring(_EmptyChainer())
    assert kb_secrets.backend_name().endswith("_EmptyChainer()")


# ---------------------------------------------------------------- describe_missing


@pytest.mark.parametrize(
    "ref, needles",
    [
        ("env:DEEPINFRA_API_KEY", ["환경변수", "DEEPINFRA_API_KEY", "SetEnvironmentVariable"]),
        ("keyring:work-kb/jira", ["work-kb/jira", "kb.py init --section jira"]),
        ("keyring:work-kb/confluence", ["work-kb/confluence", "kb.py init --section confluence"]),
        ("keyring:work-kb/llm", ["work-kb/llm", "kb.py init --section llm"]),
        ("keyring:work-kb/llm-fallback", ["kb.py init --section llm"]),
        (None, ["kb.py init"]),
        ("", ["kb.py init"]),
        ("keyring:work-kb", ["형식"]),
    ],
)
def test_describe_missing(ref, needles):
    text = kb_secrets.describe_missing(ref)
    for needle in needles:
        assert needle in text
    assert text.endswith(".")


def test_describe_missing_other_service_has_no_section():
    text = kb_secrets.describe_missing("keyring:other/thing")
    assert "`python scripts/kb.py init`" in text
    assert "--section" not in text


def test_describe_missing_does_not_echo_raw_value():
    assert "raw-token-0000" not in kb_secrets.describe_missing("raw-token-0000")
