"""The only place that imports every module's models.

Alembic (`migrations/env.py`) and the schema-drift test need all tables registered on
`Base.metadata`. Modules themselves never import each other's models. Add each new module's
`models` here.
"""

from sqlalchemy import MetaData

import app.auth.models  # noqa: F401 - registers the auth tables
from app.core.db import Base


def all_models_metadata() -> MetaData:
    return Base.metadata
