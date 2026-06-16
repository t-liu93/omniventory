"""SQLAlchemy models package.

Import all models here so that Alembic's ``env.py`` (which imports this
package) discovers every table when generating / running migrations.
"""

from app.models.app_config import AppConfig
from app.models.household import Household
from app.models.location import Location
from app.models.session import Session
from app.models.user import User

__all__ = ["AppConfig", "Household", "Location", "Session", "User"]
