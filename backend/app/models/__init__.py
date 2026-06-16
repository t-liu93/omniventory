"""SQLAlchemy models package.

Import all models here so that Alembic's ``env.py`` (which imports this
package) discovers every table when generating / running migrations.
"""

from app.models.app_config import AppConfig
from app.models.category import Category
from app.models.household import Household
from app.models.item_definition import ItemDefinition
from app.models.item_kind import ItemKind
from app.models.location import Location
from app.models.session import Session
from app.models.user import User

__all__ = [
    "AppConfig",
    "Category",
    "Household",
    "ItemDefinition",
    "ItemKind",
    "Location",
    "Session",
    "User",
]
