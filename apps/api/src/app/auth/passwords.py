"""Password hashing with argon2id (argon2-cffi defaults, RFC 9106 low-memory profile).

Hashing is deliberately slow (tens of milliseconds of CPU). These functions are synchronous;
the service runs them in a worker thread so they never block the event loop.
"""

from functools import cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):  # VerifyMismatchError is a VerificationError
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the hash was made with older parameters and should be upgraded at login."""
    return _hasher.check_needs_rehash(password_hash)


def spend_verification_time(password: str) -> None:
    """Do the work of a real verification, so an unknown email takes as long as a known one."""
    verify_password(_dummy_hash(), password)


@cache
def _dummy_hash() -> str:
    return _hasher.hash("dummy password, used only to equalise login timing")
