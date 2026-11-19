"""The only place that imports every module's models.

Alembic (`migrations/env.py`) and the schema-drift test need all tables registered on
`Base.metadata`. Modules themselves never import each other's models. Add each new module's
`models` here.
"""

from sqlalchemy import MetaData

import app.auth.models  # registers the auth tables
import app.meetings.models  # noqa: F401 - registers the meeting tables
from app.core.db import Base


def all_models_metadata() -> MetaData:
    return Base.metadata
