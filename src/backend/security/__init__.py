"""Security controls shared by the backend."""

from .password_hashing import (
    generate_salt,
    get_password_pepper,
    hash_password,
    needs_rehash,
    verify_legacy_werkzeug_password,
    verify_password,
)

__all__ = [
    "generate_salt",
    "get_password_pepper",
    "hash_password",
    "needs_rehash",
    "verify_legacy_werkzeug_password",
    "verify_password",
]

