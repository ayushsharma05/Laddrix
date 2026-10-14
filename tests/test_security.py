"""Security building blocks: passwords, TOTP with replay guard, tokens, sealed secrets."""

import pytest

from mocktrade.server import security
from mocktrade.server.security import SecretBox

NOW = 1_760_400_000.0  # a fixed moment (seconds since 1970)


def test_passwords_hash_and_verify():
    stored = security.hash_password("correct horse battery")
    assert stored.startswith("$argon2id$")
    assert security.verify_password(stored, "correct horse battery")
    assert not security.verify_password(stored, "correct horse batterY")
    assert not security.verify_password(None, "anything at all")  # unknown user
    assert not security.verify_password("not a hash", "correct horse battery")
    assert not security.needs_rehash(stored)


@pytest.mark.parametrize("weak", ["short", " leading space here", "trailing space here "])
def test_weak_passwords_are_refused(weak):
    with pytest.raises(ValueError):
        security.hash_password(weak)


def test_totp_accepts_this_step_and_its_neighbours_once():
    secret = security.new_totp_secret()
    step = int(NOW // 30)
    now_code = security.totp_code(secret, NOW)
    assert security.match_totp(secret, now_code, NOW, last_used_step=0) == step
    assert security.match_totp(secret, now_code, NOW, last_used_step=step) is None  # replay
    late = security.totp_code(secret, NOW - 30)  # phone clock 30 s behind
    assert security.match_totp(secret, late, NOW, last_used_step=0) == step - 1
    too_old = security.totp_code(secret, NOW - 90)
    assert security.match_totp(secret, too_old, NOW, last_used_step=0) is None


@pytest.mark.parametrize("bad", ["", "12345", "1234567", "abcdef", "12 456"])
def test_malformed_codes_are_refused(bad):
    assert security.match_totp(security.new_totp_secret(), bad, NOW, 0) is None


def test_totp_uri_is_for_authenticator_apps():
    uri = security.totp_uri("JBSWY3DPEHPK3PXP", "ayush")
    assert uri.startswith("otpauth://totp/MockTrade:ayush?") and "issuer=MockTrade" in uri


def test_session_tokens_are_random_and_stored_only_as_digests():
    a, b = security.new_session_token(), security.new_session_token()
    assert a != b and len(a) >= 43
    assert security.token_digest(a) != a and len(security.token_digest(a)) == 64


def test_secret_box_round_trip_and_wrong_key():
    box = SecretBox(SecretBox.new_key())
    sealed = box.seal("JBSWY3DPEHPK3PXP")
    assert "JBSWY3DP" not in sealed and box.open(sealed) == "JBSWY3DPEHPK3PXP"
    with pytest.raises(ValueError, match="wrong MOCKTRADE_SECRET_KEY"):
        SecretBox(SecretBox.new_key()).open(sealed)
    with pytest.raises(ValueError, match="not a valid key"):
        SecretBox("short")


def test_old_weaker_hashes_are_flagged_for_upgrade():
    from argon2 import PasswordHasher

    weak = security.hash_password("correct horse battery")  # made with the light test hasher
    previous = security.use_hasher(PasswordHasher())  # the production default
    try:
        assert security.needs_rehash(weak)
        assert security.verify_password(weak, "correct horse battery")
    finally:
        security.use_hasher(previous)


def test_production_hasher_is_argon2id_with_strong_defaults():
    from argon2 import PasswordHasher, Type

    default = PasswordHasher()
    assert default.type is Type.ID and default.memory_cost >= 19 * 1024 and default.time_cost >= 2
