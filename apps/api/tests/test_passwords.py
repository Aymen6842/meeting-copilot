from argon2 import PasswordHasher

from app.auth.passwords import (
    hash_password,
    needs_rehash,
    spend_verification_time,
    verify_password,
)

PASSWORD = "correct horse battery staple"


def test_hash_is_argon2id_and_not_the_password() -> None:
    password_hash = hash_password(PASSWORD)

    assert password_hash.startswith("$argon2id$")
    assert PASSWORD not in password_hash


def test_same_password_hashes_differently_each_time() -> None:
    assert hash_password(PASSWORD) != hash_password(PASSWORD)


def test_correct_password_verifies() -> None:
    assert verify_password(hash_password(PASSWORD), PASSWORD)


def test_wrong_password_does_not_verify() -> None:
    assert not verify_password(hash_password(PASSWORD), PASSWORD + "!")


def test_malformed_hash_does_not_verify_or_raise() -> None:
    assert not verify_password("not-a-hash", PASSWORD)


def test_fresh_hash_needs_no_rehash() -> None:
    assert not needs_rehash(hash_password(PASSWORD))


def test_hash_with_weaker_parameters_needs_rehash() -> None:
    weak_hash = PasswordHasher(time_cost=1, memory_cost=8 * 1024).hash(PASSWORD)

    assert needs_rehash(weak_hash)


def test_spending_verification_time_never_raises() -> None:
    spend_verification_time(PASSWORD)
