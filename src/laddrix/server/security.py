"""Security building blocks: passwords, 2FA codes, session tokens, secrets at rest.

- Passwords: Argon2id (the current OWASP recommendation; memory-hard, so GPUs can't
  guess quickly). Only the hash is stored.
- 2FA: TOTP, the 6-digit codes from an authenticator app (RFC 6238, 30 s steps).
  A code is accepted for the current step or one either side (phone clocks drift),
  and each step can be used only once, so a code seen over a shoulder is useless.
- Sessions: 256 random bits in the cookie; the server stores only their SHA-256,
  so a leaked database can't be used to log in.
- Secrets at rest (TOTP secrets): encrypted with a key from the environment
  (LADDRIX_SECRET_KEY), never in code or git.
"""

import hashlib
import hmac
import secrets

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken

MIN_PASSWORD_LENGTH = 12
TOTP_STEP_S = 30
ISSUER = "Laddrix"

_hasher = PasswordHasher()  # argon2id with the library's current safe defaults
# Checked against when a username doesn't exist, so that answer takes as long as a
# real check: the response time never reveals which usernames exist.
_DUMMY_HASH = _hasher.hash("not-a-real-password-" + secrets.token_hex(8))


def use_hasher(hasher: PasswordHasher) -> PasswordHasher:
    """Swap the password hasher; returns the previous one. Tests use a light one so they
    run fast; production never calls this and keeps the strong defaults. Hashes made
    with weaker settings are upgraded at the next login (see needs_rehash)."""
    global _hasher, _DUMMY_HASH
    previous, _hasher = _hasher, hasher
    _DUMMY_HASH = _hasher.hash("not-a-real-password-" + secrets.token_hex(8))
    return previous


def check_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if password.strip() != password:
        raise ValueError("password must not start or end with spaces")


def hash_password(password: str) -> str:
    check_password_strength(password)
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    """True if the password matches. With no stored hash, spends the same time and
    returns False."""
    try:
        return _hasher.verify(stored_hash or _DUMMY_HASH, password) and stored_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """True when the hash uses older settings: re-hash at the next successful login."""
    return _hasher.check_needs_rehash(stored_hash)


def new_temporary_password() -> str:
    """A one-time password for an admin's reset: 16 characters, easy to read out (no 0/O,
    1/l/I), well above the minimum length."""
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(16))


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, username: str) -> str:
    """The otpauth:// link an authenticator app reads (usually shown as a QR code)."""
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=ISSUER)


def totp_code(secret: str, at_s: float) -> str:
    """The code an authenticator shows at a moment (for tests and tools)."""
    return pyotp.TOTP(secret).at(int(at_s))


def match_totp(secret: str, code: str, now_s: float, last_used_step: int) -> int | None:
    """The 30-second step this code belongs to, if valid and not used before; else None.

    Accepts the current step and one either side. A step at or before
    `last_used_step` is refused, so each code works once."""
    code = code.strip()
    if len(code) != 6 or not code.isdigit():
        return None
    totp = pyotp.TOTP(secret)
    current = int(now_s // TOTP_STEP_S)
    for step in (current - 1, current, current + 1):
        if step <= last_used_step:
            continue
        if hmac.compare_digest(totp.at(step * TOTP_STEP_S), code):
            return step
    return None


def new_session_token() -> str:
    return secrets.token_urlsafe(32)  # 256 bits


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_state() -> str:
    """A one-time value that ties a broker login redirect to the session that started it."""
    return secrets.token_urlsafe(24)


class SecretBox:
    """Encrypts small secrets (TOTP secrets, later broker tokens) with one key.

    Make a key once with `SecretBox.new_key()` and keep it in the environment."""

    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise ValueError("LADDRIX_SECRET_KEY is not a valid key") from exc

    @staticmethod
    def new_key() -> str:
        return Fernet.generate_key().decode()

    def seal(self, plain: str) -> str:
        return self._fernet.encrypt(plain.encode()).decode()

    def open(self, sealed: str) -> str:
        try:
            return self._fernet.decrypt(sealed.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("secret can't be decrypted: wrong LADDRIX_SECRET_KEY?") from exc
