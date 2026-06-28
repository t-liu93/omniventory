"""SQLAlchemy models package.

Import all models here so that Alembic's ``env.py`` (which imports this
package) discovers every table when generating / running migrations.
"""

from app.models.app_config import AppConfig
from app.models.attachment import Attachment
from app.models.audit_log import AuditLog
from app.models.barcode import Barcode
from app.models.category import Category
from app.models.household import Household
from app.models.item_definition import ItemDefinition
from app.models.item_kind import ItemKind
from app.models.location import Location
from app.models.maintenance_schedule import MaintenanceSchedule
from app.models.media_file import MediaFile
from app.models.note import Note
from app.models.notification import Notification
from app.models.notification_delivery import NotificationDelivery
from app.models.session import Session
from app.models.setting import Setting
from app.models.shopping_list_item import ShoppingListItem
from app.models.stock_instance import StockInstance
from app.models.stock_movement import StockMovement
from app.models.tag import Tag, TagLink
from app.models.user import User
from app.models.user_token import UserToken

__all__ = [
    "AppConfig",
    "Attachment",
    "AuditLog",
    "Barcode",
    "Category",
    "Household",
    "ItemDefinition",
    "ItemKind",
    "Location",
    "MaintenanceSchedule",
    "MediaFile",
    "Note",
    "Notification",
    "NotificationDelivery",
    "Session",
    "Setting",
    "ShoppingListItem",
    "StockInstance",
    "StockMovement",
    "Tag",
    "TagLink",
    "User",
    "UserToken",
]
