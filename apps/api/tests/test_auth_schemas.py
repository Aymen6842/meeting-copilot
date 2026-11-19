import pytest
from pydantic import ValidationError

from app.auth.schemas import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    LoginRequest,
    RegisterRequest,
)
from app.auth.service import hash_token


def test_register_normalises_email() -> None:
    request = RegisterRequest(email="  Ada@Example.COM ", password="x" * PASSWORD_MIN_LENGTH)

    assert request.email == "ada@example.com"


@pytest.mark.parametrize("email", ["not-an-email", "ada@", "@example.com", ""])
def test_register_rejects_invalid_email(email: str) -> None:
    with pytest.raises(ValidationError, match="email"):
        RegisterRequest(email=email, password="x" * PASSWORD_MIN_LENGTH)


@pytest.mark.parametrize(
    ("length", "valid"),
    [
        (PASSWORD_MIN_LENGTH - 1, False),
        (PASSWORD_MIN_LENGTH, True),
        (PASSWORD_MAX_LENGTH, True),
        (PASSWORD_MAX_LENGTH + 1, False),
    ],
)
def test_register_password_length_limits(length: int, valid: bool) -> None:
    def build() -> RegisterRequest:
        return RegisterRequest(email="ada@example.com", password="x" * length)

    if valid:
        build()
    else:
        with pytest.raises(ValidationError, match="password"):
            build()


def test_register_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="role"):
        RegisterRequest.model_validate(
            {"email": "ada@example.com", "password": "x" * PASSWORD_MIN_LENGTH, "role": "owner"}
        )


def test_login_normalises_email_without_validating_its_format() -> None:
    request = LoginRequest(email=" Not-An-Email ", password="anything")

    assert request.email == "not-an-email"


def test_token_hash_is_stable_hex_and_hides_the_token() -> None:
    token = "example-session-token"

    assert hash_token(token) == hash_token(token)
    assert len(hash_token(token)) == 64
    assert token not in hash_token(token)
