"""Application factory for Omniventory.

``create_app()`` is the sole public entry point.  It builds and returns the
FastAPI application instance.  No app object or Settings are instantiated at
module-import time — all side-effectful work happens inside the factory
function, which is called explicitly (e.g. by the ASGI server or by tests).
"""

import secrets
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Path to the directory where the Vite build output lands inside the container.
# When running in dev / tests with no built frontend this directory won't exist
# and static serving is silently skipped (the condition is checked at startup).
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _resolve_secret_key(app: FastAPI) -> None:
    """Resolve the effective secret key and stash it on ``app.state.secret_key``.

    Resolution order:
    1. ``settings.secret_key`` is set and non-empty  → use it as-is (do NOT
       persist it; the env value may rotate).
    2. ``app_config['secret_key']`` exists in the DB  → use the persisted key.
    3. Neither present                                → generate
       ``secrets.token_hex(32)``, persist to ``app_config``, use it.

    The function is best-effort: if the ``app_config`` table does not yet
    exist (schema not yet migrated) it skips the DB read/write and uses the
    env value (or raises a clear error if there is none).
    """
    import logging

    from sqlalchemy import inspect as sa_inspect

    from app.config import get_settings
    from app.db.base import get_engine, get_session_factory
    from app.repositories.app_config import AppConfigRepository

    logger = logging.getLogger(__name__)
    settings = get_settings()

    # If the caller provided an explicit env key, use it directly.
    if settings.secret_key:
        app.state.secret_key = settings.secret_key
        return

    engine = get_engine()
    table_ready = sa_inspect(engine).has_table("app_config")

    if not table_ready:
        # Schema not migrated yet — generate an ephemeral key for this boot.
        # On a real deployment alembic upgrade head runs before uvicorn starts,
        # so this branch is only hit in tests or bare-python runs pre-migration.
        logger.warning(
            "app_config table not found; using an ephemeral secret_key for this boot. "
            "Run 'alembic upgrade head' to persist the key across restarts."
        )
        app.state.secret_key = secrets.token_hex(32)
        return

    factory = get_session_factory()
    db = factory()
    try:
        repo = AppConfigRepository(db)
        persisted = repo.get("secret_key")

        if persisted:
            app.state.secret_key = persisted
            return

        # Generate, persist, and use a new key.
        new_key = secrets.token_hex(32)
        repo.set("secret_key", new_key)
        db.commit()
        logger.info("Generated and persisted a new secret_key in app_config.")
        app.state.secret_key = new_key
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _purge_expired_sessions() -> None:
    """Delete expired session rows on application startup.

    This is the actual cleanup mechanism for expired sessions.  ``verify``
    is a pure read (it rejects but does not delete expired rows), so this
    startup sweep keeps the table tidy without relying on per-request
    side-effects that could be silently rolled back by error handlers.

    The sweep is best-effort: if the ``sessions`` table does not yet exist
    (e.g. on a fresh DB before ``alembic upgrade head`` has been run) the
    function skips silently rather than crashing the app.  The table-existence
    check uses ``sqlalchemy.inspect`` so no raw SQL hits the DB when the
    schema isn't present.

    For a long-running deployment a proper periodic job (cron / APScheduler)
    should be added later; the startup sweep is sufficient for M0's single-
    user, self-hosted use-case.
    """
    from sqlalchemy import inspect as sa_inspect

    from app.auth.sessions import purge_expired
    from app.db.base import get_engine, get_session_factory

    engine = get_engine()
    if not sa_inspect(engine).has_table("sessions"):
        return  # Schema not yet migrated — skip silently.

    factory = get_session_factory()
    db = factory()
    try:
        count = purge_expired(db)
        db.commit()
        if count:
            import logging

            logging.getLogger(__name__).info("Purged %d expired session(s) on startup.", count)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """FastAPI lifespan: resolve secret key and purge expired sessions on startup."""
    _resolve_secret_key(app)
    _purge_expired_sessions()
    yield


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
        lifespan=_lifespan,
        # Disable the default /docs and /redoc under root; they will be
        # accessible under the api_prefix once routers are mounted.
        docs_url=f"{settings.api_prefix}/docs",
        redoc_url=f"{settings.api_prefix}/redoc",
        openapi_url=f"{settings.api_prefix}/openapi.json",
    )

    # ------------------------------------------------------------------ #
    # Root API router — all routes live under settings.api_prefix          #
    # ------------------------------------------------------------------ #
    from app.api.routes import auth, health
    from app.api.routes.categories import router as categories_router
    from app.api.routes.definitions import router as definitions_router
    from app.api.routes.instances import router as instances_router
    from app.api.routes.kinds import router as kinds_router
    from app.api.routes.locations import router as locations_router

    root_router = APIRouter()
    root_router.include_router(health.router)
    root_router.include_router(auth.router)
    root_router.include_router(locations_router)
    root_router.include_router(categories_router)
    root_router.include_router(kinds_router)
    root_router.include_router(definitions_router)
    root_router.include_router(instances_router)

    app.include_router(root_router, prefix=settings.api_prefix)

    # ------------------------------------------------------------------ #
    # Static SPA serving (Step 7)                                          #
    # Mounted ONLY when the built frontend directory exists, so dev / tests #
    # / make codegen runs without a frontend build are unaffected.         #
    #                                                                      #
    # Mount order matters: the API router is registered above, so          #
    # /api/* routes take precedence over the static mount.                 #
    #                                                                      #
    # The catch-all SPA route is marked include_in_schema=False so it      #
    # never appears in the OpenAPI spec and `make codegen` stays a no-op.  #
    # ------------------------------------------------------------------ #
    if _STATIC_DIR.is_dir():
        # Mount named static assets (hashed filenames from Vite build).
        # "html=False" so that 404s fall through to our catch-all below.
        app.mount(
            "/assets",
            StaticFiles(directory=str(_STATIC_DIR / "assets")),
            name="static-assets",
        )

        # Serve well-known top-level files from the static root
        # (manifest.webmanifest, icons, sw.js, …) via a secondary mount.
        # We mount it at /static-root internally but expose it at root via
        # the catch-all below for everything that isn't /api or /assets.
        _index_html = _STATIC_DIR / "index.html"

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str) -> FileResponse:
            """Serve the SPA index.html for all non-API routes (history fallback).

            The ``include_in_schema=False`` flag keeps this route invisible to
            the OpenAPI document generator, ensuring ``make codegen`` is a
            true no-op even when the static directory exists.

            Unregistered ``/api/*`` paths are short-circuited to 404 so that
            client typos and not-yet-implemented endpoints return a JSON 404
            instead of SPA HTML.  The prefix is derived from
            ``settings.api_prefix`` (default ``/api``) with the leading slash
            stripped, so the check stays correct if the prefix is reconfigured.
            Registered API routes (``/api/health``, ``/api/auth/*``, docs)
            continue to be matched before this catch-all ever fires.
            """
            # Short-circuit unregistered /api/* paths → 404 JSON.
            # full_path has no leading slash (Starlette strips it from the
            # path parameter), so we compare against the prefix without "/".
            api_prefix = settings.api_prefix.lstrip("/")  # e.g. "api"
            if full_path == api_prefix or full_path.startswith(api_prefix + "/"):
                raise HTTPException(status_code=404)

            # Try the exact path first (e.g. /icon-192.png, /manifest.webmanifest)
            candidate = _STATIC_DIR / full_path
            if candidate.is_file():
                return FileResponse(str(candidate))
            # Fall back to index.html for all SPA client-side routes
            return FileResponse(str(_index_html))

    return app
