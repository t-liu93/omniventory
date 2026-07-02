# Omniventory

> 🌐 **Languages:** English (this doc) · [中文](./README_zh.md)

**A self-hosted, three-in-one inventory system.** Omniventory unifies three normally-separate needs into a single data model:

1. **Best-before / expiry** — food, medicine, and consumables, with proactive "N days ahead" reminders.
2. **Durable-goods ledger** — serial number, warranty, value, a multi-level location hierarchy, photos, and full lifecycle tracking.
3. **Consumable stock** — an in/out movement ledger, minimum-stock thresholds, and low-stock alerts.

It is **single-tenant, multi-user** (one household or team per deployment), ships as a **single Docker image**, and keeps all of its data in one directory you can copy to back up. Personal-use-first, and open-source.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

> **Status:** early / pre-1.0 — the first tagged release is **0.1.0**. The core is feature-complete for daily use; AI-assisted features and external integrations are on the [roadmap](./docs/plan/roadmap.md).

## Features

### The three-in-one core

- **🥫 Best-before & expiry** — batch-level best-before dates, auto-computed on intake from per-item shelf-life defaults; "expiring soon" / "expired" listings and a dashboard tile.
- **📦 Durable-goods ledger** — item **definitions** vs. individual **lots/instances**; serial number, model, manufacturer, **warranty** (a first-class reminder source), and purchase price / value; a self-referential **location tree** where a container can itself be a tracked item; photos and lifecycle.
- **🧴 Consumable stock** — an append-only **movement ledger** (intake / consume / move / adjust / discard); quantity is always **derived from the ledger, never overwritten**; FEFO/FIFO consumption; minimum-stock thresholds with low-stock alerts and undo.

### Proactive reminders

- **One unified engine** across best-before, warranty, low-stock, and maintenance-due.
- **Configurable lead times** — global **+ per-item + per-user**.
- **Event-triggered + daily scheduled scan** (double safety), default-on.
- **Pluggable channels** — in-app inbox, email (SMTP digests), and outbound **HTTP webhooks** including a **Home Assistant** state endpoint. *(An MQTT/Home-Assistant bridge exists but is currently disabled.)*

### Daily-use ergonomics

- **Multi-user & roles** — admin / member / viewer, invitations, responsible-party routing, an audit log, and security hardening (auth rate-limiting, an SSRF guard that still allows your LAN, session management).
- **Cross-cutting** — attachments/photos, tags, notes, custom fields, and global search.
- **Barcode scanning** — client-side 1D + 2D decoding with product lookup on intake.
- **Shopping list** — auto-generated from low-stock plus manual items; checking an item off can intake it into stock.
- **Maintenance schedules** — recurring upkeep for durables, feeding the reminder engine.
- **Data portability** — CSV export; your data lives in a single bind-mounted directory (copy it to back up).

### Platform

- **Bilingual UI** — English + 简体中文, switchable at runtime and remembered per account.
- **Installable PWA** — desktop and mobile are equally first-class; offline-capable shell.
- **LLM foundation** — an OpenAI-compatible provider you can configure and connection-test; AI-assisted entry and semantic search are on the roadmap.

## Tech stack

- **Backend** — Python 3.13 · FastAPI · SQLAlchemy 2.0 (typed) · Alembic · SQLite · [uv](https://docs.astral.sh/uv/). Auth is opaque server-side session cookies; all business logic lives in the application/service layer.
- **Frontend** — React 19 + TypeScript · Vite · [Mantine](https://mantine.dev/) · react-i18next (ZH + EN) · PWA. pnpm.
- **Contract-first** — the FastAPI app exports `openapi.json`, from which the typed API client is generated; a no-drift CI gate keeps them in sync.
- **Deploy** — a single multi-stage Docker image that serves the embedded SPA; SQLite on a bind mount.

## Getting started (Docker)

Requirements: Docker + Docker Compose.

```bash
git clone git@github.com:omniventory/omniventory.git
cd omniventory

# Pulls the prebuilt multi-arch image from GHCR, runs the one-shot `migrate`
# service (alembic upgrade head), then starts `app` only after it succeeds
# (fail-closed).
docker compose up -d
```

Pin a specific release by setting `IMAGE_TAG=0.1.0` in `.env` (defaults to `latest`). To build from source instead of pulling, use `make docker-dev`.

Then open the app in your browser and complete the **first-run setup** (create the admin account) — there is no environment-seeded admin.

Data is stored under a bind-mounted `DATA_DIR` (mapped to `/app/data` in the container: the SQLite file + uploaded media). The container runs as **uid/gid 1000**. **To back up, stop the container and copy that directory.**

### Configuration

Zero-config by default. Override via an optional `.env` file next to `docker-compose.yml`:

| Variable | Purpose |
| --- | --- |
| `IMAGE_TAG` | Published image tag to run (`ghcr.io/omniventory/omniventory:<IMAGE_TAG>`); defaults to `latest`. |
| `APP_PORT` | Host port to expose the app on. |
| `DATA_DIR` | Host path bind-mounted to `/app/data` (SQLite + media). |
| `SECRET_KEY` | Session-signing key. Auto-generated and persisted on first run if left blank. |
| `DATABASE_URL` | Database connection string (defaults to the SQLite file under `/app/data`). |
| `ENVIRONMENT` | `production` / `development`. |

Running behind a reverse proxy (Nginx, Caddy, …) for TLS is recommended when exposing Omniventory beyond your LAN; the container exposes a single HTTP port.

## Development

Requirements: Python 3.13, [uv](https://docs.astral.sh/uv/), Node 22, pnpm, and Docker.

```bash
# Backend (in backend/)
uv sync
uv run uvicorn app.main:create_app --factory --reload

# Frontend (in frontend/)
pnpm install
pnpm dev
```

`Makefile` targets (run from the repo root) are the canonical entry points — humans and CI call the same ones:

- `make check` — all quality gates (lint + type-check + tests, both sides). This is the Definition-of-Done gate.
- `make lint` · `make test` — the halves of the above.
- `make codegen` — regenerate `openapi.json` + the frontend API types. Re-run and commit whenever the API changes (a CI job fails on drift).
- `make docker-build` — build a local image from source (`omniventory:latest`).
- `make docker-dev` — build from source + run the dev stack (tagged `omniventory:dev`, so it never shadows the published GHCR image).

## Project status & roadmap

Omniventory is built milestone by milestone. The core (unified model, stock ledger, expiry, the reminder engine, cross-cutting capabilities + barcode + export, multi-user & roles, shopping list + maintenance) is done; multi-unit conversion is paused, and the LLM applications + external integrations are planned. See **[`docs/plan/roadmap.md`](./docs/plan/roadmap.md)** for the milestone map and progress table.

Design docs under `docs/` are bilingual (an English canonical `<name>.md` and a Chinese mirror `<name>_zh.md`).

## Contributing

This started as a personal project and is open-sourced for others to self-host and build on. Issues and pull requests are welcome. Before submitting a PR, please run `make check` and keep the OpenAPI contract in sync with `make codegen`.

## License

[MIT](./LICENSE) © 2026 Omniventory
