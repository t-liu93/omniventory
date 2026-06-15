"""Health check endpoint.

GET /health (mounted under the configured api_prefix, e.g. /api/health)

Response: ``{status, version, api_version}``
The ``db`` field is NOT present in Step 2 — it will be added in Step 3 once
the database layer exists.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import get_settings

router = APIRouter()


class HealthResponse(BaseModel):
    """Shape of the /health response in Step 2 (no db field yet)."""

    status: str
    version: str
    api_version: int


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Return service health.

    ``status`` is always ``"ok"`` while the process is running.
    ``version`` is the application release version from pyproject.toml
    (approximated here as the package version; refined when packaging is wired).
    ``api_version`` is the integer compatibility number from Settings, which
    clients use to detect API compatibility without URL versioning.
    """
    from importlib.metadata import PackageNotFoundError, version

    settings = get_settings()

    try:
        app_version = version("omniventory")
    except PackageNotFoundError:
        app_version = "dev"

    return HealthResponse(
        status="ok",
        version=app_version,
        api_version=settings.api_version,
    )
