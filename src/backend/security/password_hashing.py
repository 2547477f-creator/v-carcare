"""Password hashing helpers built on Argon2id.

Each encoded hash contains a unique random salt.  The application-wide pepper
comes from ``PASSWORD_PEPPER`` and must be kept outside the database and source
control.  Store the returned PHC string directly in the password_hash column.
"""

import base64
import hmac
import os
import secrets
from typing import Final

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError
from werkzeug.security import check_password_hash


SALT_BYTES: Final = 16
PEPPER_ENV_VAR: Final = "PASSWORD_PEPPER"

# OWASP-aligned interactive-login settings.  The encoded PHC string records
# these parameters, so ``check_needs_rehash`` can identify hashes to upgrade.
_PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=SALT_BYTES,
    type=Type.ID,
)


def generate_salt() -> str:
    """Return a cryptographically secure, URL-safe salt string.

    ``hash_password`` creates its own salt automatically; this helper is for
    cases that need an independently stored salt and should not normally be
    used for password hashes.
    """
    return base64.urlsafe_b64encode(secrets.token_bytes(SALT_BYTES)).decode("ascii")


def get_password_pepper() -> str:
    """Read the required application-wide password pepper from the environment."""
    pepper = os.environ.get(PEPPER_ENV_VAR)
    if not pepper:
        raise RuntimeError(
            f"{PEPPER_ENV_VAR} must be configured before hashing or verifying passwords"
        )
    return pepper


def hash_password(password: str, *, pepper: str | None = None) -> str:
    """Create an Argon2id PHC hash with a fresh random salt and a pepper."""
    return _PASSWORD_HASHER.hash(_peppered_password(password, pepper))


def verify_password(password: str, encoded_hash: str, *, pepper: str | None = None) -> bool:
    """Return whether *password* matches an Argon2id PHC hash.

    Invalid or malformed hashes are deliberately treated as non-matches rather
    than exposing parsing details to a login endpoint.
    """
    if not isinstance(encoded_hash, str) or not encoded_hash.startswith("$argon2id$"):
        return False
    try:
        return _PASSWORD_HASHER.verify(encoded_hash, _peppered_password(password, pepper))
    except (InvalidHashError, VerificationError):
        return False


def verify_legacy_werkzeug_password(password: str, encoded_hash: str) -> bool:
    """Verify a pre-Argon2id Werkzeug hash during a one-time login migration."""
    if not isinstance(encoded_hash, str) or encoded_hash.startswith("$argon2"):
        return False
    try:
        return check_password_hash(encoded_hash, password)
    except (ValueError, TypeError):
        return False


def needs_rehash(encoded_hash: str) -> bool:
    """Return whether a valid hash should be replaced with current parameters."""
    if not isinstance(encoded_hash, str) or not encoded_hash.startswith("$argon2id$"):
        return True
    try:
        return _PASSWORD_HASHER.check_needs_rehash(encoded_hash)
    except InvalidHashError:
        return True


def _peppered_password(password: str, pepper: str | None) -> str:
    if not isinstance(password, str):
        raise TypeError("password must be a string")
    active_pepper = get_password_pepper() if pepper is None else pepper
    if not isinstance(active_pepper, str) or not active_pepper:
        raise ValueError("pepper must be a non-empty string")

    # HMAC safely combines arbitrary-length password input with the secret
    # pepper before handing it to Argon2id.
    return hmac.new(
        active_pepper.encode("utf-8"), password.encode("utf-8"), "sha256"
    ).hexdigest()
