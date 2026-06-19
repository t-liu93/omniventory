# Omniventory · Roadmap

> 🌐 **Languages:** English (this doc) · [中文](./roadmap_zh.md)

> **Round-1 planning output.** This is the *map*: locked decisions, global constraints, the domain-model spine, and the milestone ladder. Per-milestone design docs (`docs/plan/milestones/M<x>.md`, self-contained, with blind-review + 🟢 deploy self-test points) are produced in later planning rounds, one milestone at a time.
>
> **Progress lives only in the table below** (§4). The active milestone is the one marked 🟡. Don't track progress anywhere else (not in `AGENTS.md`, not in prose).
>
> Inspiration / "why we exist" / cross-project data-model insight: `docs/inspiration/investigation.md` (self-contained). This roadmap turns that investigation's distilled takeaways into a buildable plan.

---

## 1. Locked decisions

These were settled in round-1 planning discussion. Changing one is a "foundations" change (also update `AGENTS.md`'s "Tech stack & commands" once M0 ratifies the stack).

### 1.1 Tech stack
- **Backend**: Python · **FastAPI** · **SQLAlchemy** (ORM) · **Alembic** (migrations) · **SQLite** (lightweight, default).
  - Business/domain logic stays in the **application/service layer** — *no* SQLite-specific SQL, views, or triggers — so the DB stays swappable (Postgres later if ever needed). This also sidesteps the investigation's "logic sunk into SQL binds you to one DB" pitfall (Grocy).
- **Frontend**: **React + TypeScript** · **Mantine** (component lib) · **Feather Icons** via `react-feather` (official React build) · Vite. A **responsive** web app where **desktop and mobile are equally first-class**, that is **also a PWA** (installable, offline-capable). No native app in v1.
  - Rationale for React over the author's Vue experience: the responsive PWA covers both desktop and mobile in v1, and a future **React Native** mobile client reuses React skills/ecosystem. In the agentic-coding era, hand-writing familiarity is not the deciding factor.
- **Deploy**: single Docker container (app + embedded frontend + SQLite volume).

### 1.2 Tenancy & users — **single-tenant, multi-user**
- One deployment = exactly **one** "household / company" (a named **singleton `Household/Workspace`** entity: name, currency, timezone, settings).
- **Multiple user accounts** share all data within that one household; differentiation is by **account + role**, not by tenant.
- Business tables carry **no per-row tenant scoping** (there is only one tenant) — keeps the model simple.
- **Future-multi-tenant hedges (cheap insurance, do them from day one)**:
  1. The singleton `Household/Workspace` entity exists now (single-tenant = one row).
  2. All DB access goes through a **repository/service layer + a single "current context" abstraction** — never scattered raw queries. Going multi-tenant later then becomes: add `household_id` + Alembic backfill to the one existing row + flip on one centralized scope filter + make user↔household a membership + per-tenant uniqueness. Contained, not a rewrite.

### 1.3 Scope decisions
- **All four "optional" capabilities are in scope** as committed milestones: barcode scan + product recognition, multi-unit conversion, shopping list, durable maintenance schedules.
- **CSV import/export + backup/restore** is core (M5), not optional.
- **Integrations** (later milestone): **SMTP** (folded into the M4 reminder channels), **MQTT** (Home Assistant / IoT two-way), and an **OpenAI-compatible LLM backend** (scan product/receipt → auto-categorize; receipt → batch product creation). The LLM backend is a **reserved abstraction/hook now; real features later**.
- Explicitly **not** building InvenTree-style BOM / build orders / purchase-sales orders / part variants — over-designed for a household.

---

## 2. Global constraints (the project's red lines)

Hold on every milestone. Most are the investigation §3.3 takeaways, made binding.

1. **Definition / instance split is the spine.** "What kind of thing" (Item **Definition**: name, category, unit, defaults, thresholds) is separate from "this specific lot/unit" (Stock **Lot/Instance**: quantity, location, serial, batch, dates, value). Never collapse them into one table (Homebox's trap → can't hold same-item multiple batches / expiries).
2. **One instance table for both "per-unit" and "in-bulk", unified by a constraint:** `serial ⇒ quantity = 1` (enforced in DB *and* app). Serialized durables (②) and bulk consumables (③) live in the same table.
3. **Quantity is always derived from a movement ledger, never overwritten.** Every in/out/move/adjust is a typed transaction; current quantity = aggregation. Supports consumption-rate, audit, and undo. (Homebox's overwrite-float is the anti-pattern.)
4. **Expiry / warranty / value / location live on the instance/lot; defaults live on the definition** (`default_best_before_days`, `min_stock`, default location). Batch-level best-before (Grocy precision) beats item-level.
5. **Locations are a self-referential tree** (arbitrary depth). A **container can itself be a tracked item** (the toolbox is both a location and a valuable asset — Homebox's good insight). Never flat locations (Grocy's trap → no hierarchical durable tracking).
6. **One unified reminder engine, many sources.** It scans `best_before`, `warranty_expires`, `min_stock`/low-stock, and (later) maintenance-due. **Configurable "N days ahead"** per item and per user; **event-triggered + daily scheduled fallback** (double safety); **default-on**; notifies the **responsible party**; channels are **pluggable** (in-app, SMTP, MQTT, webhook). Avoids all three projects' reminder failures (hardcoded / default-off / subscriber-only / pull-only).
7. **Warranty is a first-class field** and a reminder source (all three reference projects lack proper warranty handling).
8. **Cross-cutting capabilities are generic/reusable, not duplicated per table:** photos/attachments (generic `model_type + model_id`), tags, notes, custom fields, barcodes.
9. **Money and quantity are `Decimal`;** quantity supports fractions (wire by the metre, liquids by the litre). Rounding rules pinned where money is involved.
10. **Single-tenant now, multi-tenant-ready** (see §1.2 hedges): singleton `Household/Workspace` entity + centralized data-access/context layer.
11. **Logic in the app layer, not the database.** No business logic in SQL views/triggers (portability + testability).
12. **An OpenAI-compatible LLM backend is a reserved abstraction.** Design seams for it (vision categorization, receipt→batch) even before implementing.

---

## 3. Domain model spine (overview)

Detailed schema lands in the per-milestone design docs; this is the shared skeleton everything hangs off.

- **Household/Workspace** — singleton config (name, currency, timezone, defaults).
- **User** — account + role; auth. (Multi-user fully fleshed out in M6.)
- **Location** — self-referential tree; a location may also *be* an item (container-as-item).
- **Category** — tree.
- **Item Definition** — name, category, kind (durable / consumable / perishable hints), unit(s), default location, `default_best_before_days`, `min_stock`/reorder point, `trackable` (serial-able), custom fields, barcode(s), photo.
- **Stock Lot / Instance** — FK→Definition, FK→Location, `quantity` (Decimal), `serial` (nullable; `serial ⇒ qty=1`), `batch`, `best_before_date`, state (opened/frozen…), `warranty_expires`, status, `purchase_price`/`date`/`source`, `received_at`, responsible user.
- **Stock Movement (ledger)** — type (intake / consume / move / adjust / discard / correction), FK→Lot, qty delta, from/to location, timestamp, user, note, reversal link. Quantity is derived from these.
- **Reminder / Notification** — trigger sources (best_before, warranty, low_stock, maintenance-due), per-item & per-user lead times, channel adapters, delivery log.
- **Cross-cutting** — Attachment (generic), Tag (nestable), Note, CustomField, Barcode.

---

## 4. Milestone map & progress

Legend: ⬜ planned · 🟡 active · 🟢 done. **Active milestone = the single 🟡 row.** Update *only* this table as work progresses.

| # | Milestone | Delivers | Status |
|---|---|---|---|
| **M0** | Foundations & scaffolding | running skeleton | 🟢 |
| **M1** | Unified core model & durable-goods registry | ② (registry) | 🟢 |
| **M1.5** | Internationalization (i18n) foundation | all (ZH + EN) | 🟢 |
| **M2** | Stock ledger & consumables | ③ (in/out + low-stock) | 🟢 |
| **M3** | Best-before / expiry & perishables | ① (data + listings) | 🟡 |
| **M4** | Unified reminder & notification engine | ①②③ proactive alerts | ⬜ |
| **M5** | Cross-cutting + barcode + data I/O | all | ⬜ |
| **M6** | Multi-user & roles | all | ⬜ |
| **M7** | Shopping list & maintenance schedules | ③ / ② | ⬜ |
| **M8** | Multi-unit conversion | ③ | ⬜ |
| **M9** | Integrations & extensions (MQTT / API / LLM) | all | ⬜ |

**Suggested 1.0 cut: through M6** (all three needs fully covered with proactive reminders, data I/O, and multi-user). M7–M9 are 1.x / post-1.0. Adjustable.

---

## 5. Milestones (detail)

> Each milestone is independently deployable and demoable. The 🟢 points are the deploy self-test checkpoints that the milestone's `M<x>.md` design doc will expand and the milestone report will stitch into a manual walkthrough.

### M0 — Foundations & scaffolding
**Goal:** an empty-but-running, CI-green app you can log into.
- Repo layout (`backend/`, `frontend/`); package managers; lint/format/type-check/test harness (backend: ruff + mypy + pytest; frontend: eslint + tsc + vitest); CI.
- FastAPI app + config/settings + health check; SQLAlchemy + Alembic wired; first migration.
- Single Docker container; frontend (React + Vite + Mantine + react-feather) embedded/served; **responsive** layout + PWA shell.
- **Auth skeleton** (single admin user to start; session/JWT) + **singleton `Household/Workspace`** entity + **centralized data-access/context layer** (the multi-tenant hedge).
- **Contract-first**: OpenAPI → TS types codegen, with a no-drift CI gate.
- 🟢 App boots; `/api/health` green; login works; one Alembic migration applies cleanly; CI green; `docker build` runs the whole thing.

### M1 — Unified core model & durable-goods registry (②)
**Goal:** register and browse durable goods in a real location hierarchy.
- Location tree CRUD (incl. container-as-item); Category tree.
- Item Definition CRUD; Stock Lot/Instance CRUD with `serial ⇒ qty=1` enforced.
- Durable fields on the instance: serial / model / manufacturer, `warranty_expires` (stored; reminders come in M4), value/purchase.
- List / detail / tree-browse / search UI.
- 🟢 Create a nested location tree; register a serialized durable into it; edit; search; the `serial ⇒ qty=1` constraint rejects bad input.

### M1.5 — Internationalization (i18n) foundation
**Goal:** make the UI bilingual (ZH + EN) *before* more surfaces accrue — a foundational, cross-cutting capability done early so every later milestone authors strings through i18n instead of hardcoding English.
- Frontend: **react-i18next** scaffolding; extract all existing M0–M1 UI strings into a translation catalog (ZH + EN); a language switcher (also on the pre-login / setup screens).
- **Layered language resolution:** logged-in → the account's stored `preferred_language` (authoritative, follows the user across devices); pre-login → explicit pick on the login/setup screen (remembered client-side) → browser auto-detect → fallback **EN**.
- Backend: per-user `preferred_language` field + a read/update endpoint. Because API messages use **stable error codes**, the backend does *no* localization itself — it returns codes and the frontend maps them to localized text (decided here so the wire/display split is in place from the start).
- 🟢 Switch language at runtime (ZH ⇄ EN) and the whole UI follows; the choice persists with the account across sessions/devices; the login screen renders in the resolved language before auth; a backend error shows up localized via its code.

> Locked: **react-i18next**, ZH + EN, backend error-codes. The detailed design doc (`docs/plan/milestones/M1.5.md`) is now written; it finalizes the language-persistence specifics (account-bound `preferred_language`, nullable = inherit client resolution; pre-login fallback localStorage → browser → **EN**), folds the backend error-codes into a **full uniform error-envelope refactor** (flat `{code, message, params}`), and scopes in locale-aware date/number formatting.

### M2 — Stock ledger & consumables (③)
**Goal:** track consumable stock by in/out flows with low-stock detection.
- Movement/ledger table (intake / consume / move / adjust / discard / correction); **quantity derived** from it; **FIFO** consumption order (by best_before then received); **undo/reversal**.
- `min_stock`/reorder point on Definition; low-stock computed endpoint + dashboard tile.
- Intake / consume / move flows in the UI.
- 🟢 Intake 10, consume 3 (FIFO), see derived qty = 7; undo restores; set `min_stock`, drop below it, low-stock surfaces.

### M3 — Best-before / expiry & perishables (①)
**Goal:** track best-before dates and surface what's expiring.
- `best_before_date` on the lot; `default_best_before_days` on the Definition (auto-compute on intake).
- Expiring / expired filters + dashboard; FIFO already honours best_before.
- *(Optional, may defer to a refinement:* opened/frozen/thawed "+N days" adjustments — Grocy-style.*)*
- 🟢 Intake a perishable with a default shelf life → best_before auto-computed; list items expiring within N days; expired items flagged.

> Locked: the detailed design doc (`docs/plan/milestones/M3.md`) is now written. It puts **`best_before_date` on the lot** (batch-level, mode-independent) and **`default_best_before_days` on the definition**, **auto-computes** best-before on intake (explicit-wins → definition-default → NULL), turns the M2 FIFO walk into **FEFO** (nearest-expiry-first — the M2 §12 promise, only the `ORDER BY` changes), and adds a **computed `GET /expiring`** read (per-lot, expired ∪ expiring-within-N) surfaced as a dashboard tile + a `/expiring` page. Reminders stay **M4**; opened/frozen "+N days" is **deferred**; **no new error codes** (Pydantic `ge=0`).

### M4 — Unified reminder & notification engine (①②③ proactive)
**Goal:** the differentiator — one engine, proactive "N days ahead" alerts across all sources.
- Consolidate trigger sources: best_before, `warranty_expires`, low-stock (maintenance-due added in M7).
- Per-item **and** per-user configurable lead times; **event-triggered + daily scheduled scan** (double safety); **default-on**; assign/notify the responsible user; digesting.
- **Pluggable channels**: in-app + **Email SMTP** (MQTT channel arrives in M9).
- 🟢 Configure lead times; run the daily scan; receive a consolidated reminder in-app and by email covering an expiring item, an expiring warranty, and a low-stock item.

### M5 — Cross-cutting capabilities + barcode + data I/O
**Goal:** the daily-use ergonomics and data portability of a real self-hosted app.
- Generic attachments/photos, tags, notes, custom fields, global search.
- **Barcode scanning + product lookup** on intake/identify.
- **CSV import/export**; **backup/restore** (ZIP of DB + media).
- 🟢 Attach a photo; scan a barcode to intake; import a CSV; export a backup and restore it into a clean instance.

### M6 — Multi-user & roles
**Goal:** real multi-user within the single household + hardening.
- Multiple user accounts; invitations; roles/permissions (admin / member / viewer); responsible-party assignment surfaced in reminders; basic audit.
- Security hardening: input sanitization wherever content is rendered, SSRF guard on outbound notifiers/webhooks, rate limiting.
- 🟢 Invite a second user; role enforcement blocks/permits correctly; assignment-based reminders reach the right person.

### M7 — Shopping list & maintenance schedules
**Goal:** close the consumable loop and the durable-care loop.
- Shopping list auto-generated from low-stock (+ manual items; check-off → optional intake).
- Maintenance schedules for durables (recurring; feed the M4 reminder engine as the maintenance-due source).
- 🟢 Low stock auto-populates the shopping list; checking an item off can intake it; a scheduled maintenance fires a reminder.

### M8 — Multi-unit conversion
**Goal:** "buy by the case, use by the piece" (Grocy-style), opt-in.
- Purchase / stock / consume / price units + conversion factors on the Definition; intake/consume honour conversions; pricing follows.
- 🟢 Define units (case = 24 pieces); intake 2 cases; consume 5 pieces; stock and value are correct.

### M9 — Integrations & extensions (post-1.0)
**Goal:** connect to the smart-home and unlock AI-assisted entry.
- **MQTT** bridge (Home Assistant / IoT): publish stock / expiry / low-stock events, accept commands; MQTT also usable as a reminder channel.
- **Public REST API + webhooks + OpenAPI client.**
- **LLM (OpenAI-compatible)** features on the reserved hook: scan product/receipt → auto-categorize; receipt → batch product creation.
- 🟢 An expiry event reaches Home Assistant over MQTT; an uploaded receipt drafts a batch of products via the LLM.

---

## 6. Parking lot (deferred / "later, maybe")

Not scheduled; revisit when the core is stable.
- Opened/frozen/thawed "+N days" perishable refinement (if not folded into M3).
- OIDC / SSO; 2FA.
- **Asset tagging & label printing** (complements M5 barcode scanning): for durables without a manufacturer serial, auto-assign an **internal asset tag** — a short human-readable code (e.g. `OMNI-000123`), *not* a raw UUID, kept **distinct from the manufacturer `serial`** field. Generate a printer-friendly **QR/barcode label as SVG** (QR + the human-readable code), stick it on the box/tool, then later **scan to locate / file** it (the scan side lands in M5). Closes the loop: auto-tag → print → stick → scan.
- **"Promote an instance to a container location"** one-click UX: create the mirror location node + the `item_instance_id` container-as-item link in a single step (removes the manual two-step when a tracked asset is also used as a place — see the container-as-item bridge, M1 §3.1).
- React Native native mobile client (the responsive PWA covers v1).
- Postgres + RLS (only if true multi-tenant SaaS is ever wanted).

## 7. Open questions / to revisit
- Exact role set & permission granularity for M6 (admin/member/viewer is the current assumption).
- Whether multi-unit conversion (M8) earns its complexity for the household use case, or stays a thin opt-in.
- **Stock-tracking modes `level` / `none` ergonomics:** in practice (M2) the author finds the current `level` (qualitative high/medium/low) and `none` (presence-only) modes not fully satisfying, but no concrete better design has emerged yet — revisit once real usage clarifies what's missing (relates to M2 §12's `level` granularity question).
- Author noted "probably more capabilities not yet thought of" — additions get slotted here, then into the table.
