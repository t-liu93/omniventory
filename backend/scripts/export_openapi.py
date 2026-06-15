"""Export the FastAPI OpenAPI document to repo-root ``openapi.json``.

Usage (from the repo root, via ``make codegen``):
    uv run python backend/scripts/export_openapi.py

Design decisions:
- Sets a fixed dummy SECRET_KEY in-process before importing the app so the
  script is runnable without a real secret (and is deterministic — the
  OpenAPI content does not depend on the secret value).
- Uses ``json.dump(..., sort_keys=True, indent=2)`` + a trailing newline so
  key ordering is always stable and diffs are deterministic.
- Writes to repo-root ``openapi.json`` (resolved relative to this script's
  position in the tree: ``../../openapi.json`` from ``backend/scripts/``).
"""

import json
import os
import sys
from pathlib import Path

# Ensure the backend/ directory (parent of this script's ``scripts/`` dir) is
# on sys.path so that ``import app`` resolves correctly regardless of cwd.
# When Python runs a script it adds the *script's* directory to sys.path[0],
# not the working directory — so we add backend/ explicitly here.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


def main() -> None:
    # ------------------------------------------------------------------ #
    # Inject required env vars before importing the app.                   #
    # The secret_key value does not affect OpenAPI output; we use a fixed  #
    # dummy so the script is runnable without a real secret and the output  #
    # is always deterministic.                                              #
    # ------------------------------------------------------------------ #
    os.environ.setdefault("SECRET_KEY", "codegen-dummy-secret-key-not-used-in-production")

    # Also ensure we use a throwaway in-memory SQLite so the export does not
    # create a stray DB file when the lifespan would otherwise touch the DB.
    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

    # Import the factory *after* setting env vars so pydantic-settings picks
    # them up correctly.  Clear the settings cache first in case it was
    # already populated (e.g. during testing).
    from app.config import get_settings

    get_settings.cache_clear()

    from app.main import create_app

    app = create_app()

    # Retrieve the OpenAPI document from FastAPI.
    openapi_schema = app.openapi()

    # ------------------------------------------------------------------ #
    # Determine the output path.                                           #
    # This script lives at backend/scripts/export_openapi.py; the repo    #
    # root is two levels up.                                               #
    # ------------------------------------------------------------------ #
    repo_root = Path(__file__).resolve().parent.parent.parent
    output_path = repo_root / "openapi.json"

    # Write with stable key ordering and a trailing newline so diffs are
    # deterministic regardless of Python dict insertion order.
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(openapi_schema, fh, sort_keys=True, indent=2)
        fh.write("\n")

    print(f"openapi.json written to {output_path}")


if __name__ == "__main__":
    main()
