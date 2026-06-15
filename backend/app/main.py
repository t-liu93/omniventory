"""Application factory for Omniventory.

``create_app()`` is the sole public entry point.  It builds and returns the
FastAPI application instance.  No app object or Settings are instantiated at
module-import time — all side-effectful work happens inside the factory
function, which is called explicitly (e.g. by the ASGI server or by tests).
"""

from fastapi import APIRouter, FastAPI

from app.api.routes import health


def create_app() -> FastAPI:
    """Build and return the configured FastAPI application.

    Deliberately avoids import-time side effects:
    - ``get_settings()`` is called *inside* this function, not at module level.
    - No module-level ``app = FastAPI()`` — callers invoke ``create_app()``.

    This makes the factory safe to import in tests and scripts without
    triggering env reads or network I/O.
    """
    # Import here (inside the factory) so that Settings are not read at module
    # import time.  Tests can call ``get_settings.cache_clear()`` before
    # ``create_app()`` to inject test-specific env vars.
    from app.config import get_settings

    settings = get_settings()

    app = FastAPI(
        title="Omniventory",
        description="Self-hosted three-in-one inventory system.",
        version="0.1.0",
        # Disable the default /docs and /redoc under root; they will be
        # accessible under the api_prefix once routers are mounted.
        docs_url=f"{settings.api_prefix}/docs",
        redoc_url=f"{settings.api_prefix}/redoc",
        openapi_url=f"{settings.api_prefix}/openapi.json",
    )

    # ------------------------------------------------------------------ #
    # Root API router — all routes live under settings.api_prefix          #
    # ------------------------------------------------------------------ #
    root_router = APIRouter()
    root_router.include_router(health.router)

    app.include_router(root_router, prefix=settings.api_prefix)

    return app
