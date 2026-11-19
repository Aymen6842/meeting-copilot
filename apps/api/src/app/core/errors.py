"""Domain exceptions raised by services.

Services raise these; they never build HTTP responses. The mapping to HTTP status codes lives
in one place, `app.core.error_handlers`.
"""


class DomainError(Exception):
    """Base class. `code` is a stable, machine-readable identifier sent to clients."""

    code = "domain_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(DomainError):
    code = "not_found"


class ConflictError(DomainError):
    code = "conflict"


class PermissionDeniedError(DomainError):
    code = "permission_denied"


class DomainValidationError(DomainError):
    code = "validation_failed"
