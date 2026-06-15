# Omniventory · Makefile
# Thin aliases so humans and CI call the same commands.
# All targets delegate to the toolchain inside backend/ or frontend/.

.PHONY: check lint test codegen

# Run all quality gates (lint + type-check + tests on both sides)
check: lint test

# Lint + type-check both sides
lint:
	@echo "==> Backend: ruff check"
	cd backend && uv run ruff check .
	@echo "==> Backend: ruff format --check"
	cd backend && uv run ruff format --check .
	@echo "==> Backend: mypy"
	cd backend && uv run mypy app
	@echo "==> Frontend: eslint"
	cd frontend && pnpm lint
	@echo "==> Frontend: tsc"
	cd frontend && pnpm typecheck

# Run tests on both sides
test:
	@echo "==> Backend: pytest"
	cd backend && uv run pytest
	@echo "==> Frontend: vitest"
	cd frontend && pnpm test

# Contract-first codegen: OpenAPI snapshot → TS types
# Step 1: export the FastAPI OpenAPI document to repo-root openapi.json
# Step 2: generate TypeScript type declarations from openapi.json
# Both artifacts are committed and gate-checked by the CI contract job.
codegen:
	@echo "==> Codegen step 1: export OpenAPI → openapi.json"
	cd backend && uv run python scripts/export_openapi.py
	@echo "==> Codegen step 2: openapi-typescript → frontend/src/api/schema.d.ts"
	cd frontend && pnpm exec openapi-typescript ../openapi.json -o src/api/schema.d.ts
