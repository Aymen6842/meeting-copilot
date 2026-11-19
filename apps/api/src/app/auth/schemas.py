import datetime
import uuid
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

from app.auth.models import MAX_EMAIL_LENGTH, Role

# NIST SP 800-63B: length matters, composition rules do not. The maximum stops anyone from
# making the server hash megabytes of "password".
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128


def normalize_email(value: str) -> str:
    return value.strip().lower()


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Annotated[EmailStr, AfterValidator(normalize_email)]
    password: Annotated[str, Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)]


class LoginRequest(BaseModel):
    """Deliberately loose: a malformed email simply fails to log in, like a wrong one."""

    model_config = ConfigDict(extra="forbid")

    email: Annotated[
        str, Field(min_length=1, max_length=MAX_EMAIL_LENGTH), AfterValidator(normalize_email)
    ]
    password: Annotated[str, Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)]


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    created_at: datetime.datetime


class WorkspaceResponse(BaseModel):
    id: uuid.UUID
    name: str
    role: Role


class AccountResponse(BaseModel):
    user: UserResponse
    workspace: WorkspaceResponse
