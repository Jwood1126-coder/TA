# CLAUDE.md — Japan Travel App

## What This App Is

A Flask + SQLite PWA for planning and managing a Japan trip. Deployed on Railway with auto-deploy from `main`. Mobile-first, password-protected, with an AI chat agent built in.

**Tech stack:** Flask 3.1, Flask-SQLAlchemy, Flask-SocketIO, Gunicorn + gevent, SQLite, Vanilla JS, PicoCSS 2.0, Anthropic Claude API, service worker (network-first).

## Project Structure

```
app.py                  # App factory, auth, template filters
models.py               # SQLAlchemy models (Trip, Day, Activity, AccommodationLocation/Option, Flight, TransportRoute, etc.)
extensions.py           # Shared SocketIO instance
guardrails.py           # All input validation (status, category, transport type, document-first rule, date overlap)
config.py               # Flask config + env vars
wsgi.py / start.py      # Production entry points (Gunicorn + Railway bootstrap)
import_markdown.py      # Seed script: copies data/seed.db → data/japan_trip.db

services/               # Canonical mutation pipeline — ALL writes go through here
  accommodations.py      # select, eliminate, delete, update_status, add_option, reorder, update_location_dates
  activities.py          # toggle, add, update, eliminate, delete, confirm, update_notes, update_day_notes
  checklists.py          # toggle, update_status, create, delete
  transport.py           # add, update, delete
  flights.py             # update (with document-first validation)
  budget.py              # record_expense
  trip_audit.py          # Pre-export audit: 14 integrity checks → AuditResult (blockers, warnings, stale_refs)

blueprints/             # HTTP routes + UI rendering (delegate mutations to services/)
  itinerary.py           # Dashboard + day view
  accommodations.py      # Hotel picker CRUD + URL fetching
  activities.py          # Activity list + CRUD
  checklists.py          # Checklist view + CRUD
  documents.py           # Document upload, link/unlink, boot-time seeding
  calendar.py            # Month/week/day/list calendar views
  uploads.py             # Photo upload + EXIF + thumbnails
  backup.py / export.py  # DB backup/restore, PDF export (audit-gated)
  chat/                  # AI chat: prompt.py, tools.py, executor.py, context.py, routes.py

migrations/
  schema.py              # Idempotent DDL (ALTER TABLE, CREATE TABLE) + one-shot data migrations
  legacy.py              # Verifies legacy migrations applied (sentinel checks, read-only)
  validate.py            # Boot-time audit (read-only, delegates to trip_audit)

scripts/                # One-off repair scripts (all support --dry-run)
tests/                  # test_smoke.py, test_services.py, conftest.py (restores seed after runs)
static/css/             # 12 files: base, layout, components, dark (always), + per-page CSS
static/js/              # Per-page vanilla JS files
static/sw.js            # Service worker — bump cache version on EVERY frontend change
```

## Before Writing Any Code

### Read First, Always

1. **Read every file you will modify.** No exceptions. Do not edit a file you have not read in this session.
2. **Verify column and field names** against `models.py` before using them in queries, filters, or assignments. Column names are the #1 hallucination in code generation.
3. **Verify function signatures** before calling any function. Read the source. Do not assume parameter names or return types.
4. **Find all callers** of code you are changing. Grep for imports, function calls, template references, and Socket.IO event names.
5. **Query the database for current trip state.** Never rely on hardcoded facts — hotel names, dates, flight numbers, confirmation codes. The database is the source of truth for trip data. Documentation PDFs are the source of truth for booking details.

### Impact Analysis Checklist

- **Touching a model?** Check: `migrations/schema.py` (DDL), `to_dict()` methods, all templates rendering that model, all service functions mutating it, all chat tools referencing it.
- **Touching a service function?** Check: the blueprint route that calls it AND `blueprints/chat/executor.py`.
- **Touching a route?** Check: the template, JS file, and Socket.IO listeners that depend on it.
- **Touching CSS/JS/HTML?** You must bump the service worker cache version in `static/sw.js`.
- **Touching the schema?** Verify `create_app()` boots cleanly: `python -c "from app import create_app; create_app()"`.

### Match Existing Patterns

Before writing new code, find the closest existing example in the codebase and follow its conventions exactly — naming, error handling, response format, Socket.IO emit pattern. Consistency beats novelty.

## The Service Layer Is Non-Negotiable

Every data mutation flows through `services/`. The pipeline: **validate → normalize → DB write → cascade side effects → Socket.IO emit.**

- **Services** own domain logic shared by UI routes and AI chat (validation, mutation, cascades, emit).
- **Blueprints** own HTTP concerns (request parsing, response formatting).
- **Chat executor** does fuzzy name matching → resolves to an ID → calls the same service function the UI uses.

**To add a new mutation:** create a service function in `services/<domain>.py`, then call it from both the blueprint route and `blueprints/chat/executor.py`. Never put business logic directly in a route handler or chat executor.

Key cascades the services handle:
- Accommodation status change → checklist sync, price recalculation
- Accommodation select/eliminate → mutual exclusion, checklist sync
- Checklist status change → linked accommodation status update
- Activity elimination → clears `is_confirmed`
- Document delete/unlink → downgrades `confirmed` → `booked`

## Document-First Architecture (The Iron Rule)

**A booking CANNOT have status `confirmed` without a linked Document record.** Enforced at three levels:

1. `guardrails.validate_document_status()` — rejects at API boundary
2. `blueprints/chat/executor.py` — validates before any status update
3. `services/trip_audit.py` — flags violations in audit

Status transitions: `not_booked → researching → booked → confirmed → completed` (and `→ cancelled` from any state). The `booked → confirmed` transition requires `document_id`. Unlinking a document auto-downgrades to `booked`.

Visual language: confirmed = solid green border + doc icon; planned = dashed yellow border + "Planned" badge. Never render planned items with confirmed styling.

## Scheduling Conflict Prevention

This is the app's #1 bug class. Before creating or moving any time-bound entity, run these checks:

### What Counts as a Conflict

- **Time slot collision:** Two non-substitute, non-eliminated activities in the same `time_slot` on the same day.
- **City mismatch:** An activity scheduled on a day when accommodations place the traveler in a different city. Query `AccommodationLocation` date ranges to determine which city a day falls in.
- **Transport overlap:** A transport route departure time that conflicts with a scheduled activity on the same day.
- **Accommodation overlap:** A selected accommodation date range that overlaps another selected accommodation. Same-day checkout/checkin is allowed (check_in == other.check_out); true overlap is not. Use `guardrails.check_accom_date_overlap()`.

### The Protocol

1. **Before adding/moving an activity:** Query all existing activities on the target day. Check for time_slot collisions and verify the day's city matches the activity's intended location.
2. **Before modifying accommodation dates:** Re-validate the entire affected date span using `check_accom_date_overlap()`. Changing a check-in date affects the previous accommodation's checkout. Changing a check-out date affects the next accommodation's check-in.
3. **Never silently resolve a conflict.** Surface it to the user with specifics: what conflicts with what, on which day, at what time. Let the user decide.
4. **Geographic plausibility:** If consecutive activities are in different cities (not neighborhoods), verify there is a transport route between them with sufficient travel time. Flag if not.

### Travel Logic

- **Days vs. nights:** Check-in Apr 6 to check-out Apr 9 = 3 nights. Use `AccommodationLocation.nights` property, not `.num_nights` field.
- **Transition days:** Checkout from city A and check-in to city B often share a date. The calendar handles this.
- **Transport cards are for inter-city moves only.** Day-trip transit goes in activity descriptions. Intra-city navigation uses Google Maps links per activity.
- **Overpacked days:** >10 active activities on one day is unrealistic. Flag it.

## Anti-Hallucination Rules

1. **Column/field rule:** Before referencing any column in code — verify it exists in `models.py`. Check foreign key names (it's `location_id`, not `accommodation_location_id`; `rank` is NOT NULL).
2. **Function rule:** Before calling any function — read its source. Verify parameters and return type.
3. **Data rule:** Never generate placeholder data (fake URLs, addresses, prices, phone numbers). If the information isn't in the database or provided by the user, say so.
4. **Feature rule:** Before describing what the app "can do" — search the codebase to verify. Do not describe aspirational functionality as existing.
5. **External reference rule:** Never fabricate URLs, map links, business hours, transit schedules, or prices. Use data from the database or the user.
6. **Stale context rule:** This file describes the system architecture. The database describes current trip state. When they conflict, the database wins.

## Database Discipline

### Seed & Migration Architecture

- `data/seed.db` is the canonical seed committed to git. `import_markdown.py` copies it to the working DB.
- The migration system is **closed** — no new migration functions. Schema changes (new columns/tables) go in `migrations/schema.py` as idempotent DDL.
- Boot-time is **read-only** (except DDL and document sync). All data repairs go in `scripts/` with `--dry-run` support.
- Never replace the production DB — it contains live bookings, chat history, photos, completions.

### Read Before Write

- Before creating a record, check if it exists. Before updating, verify current state. Before deleting, check FK dependencies.
- Use `guardrails.py` validators at API boundaries. Internal service-to-service calls trust validated data.
- Never use bare `try/except` that swallows errors. Validate inputs before the operation. Errors must be visible — return them to the user.

### Schema Changes

1. Add the column to the model in `models.py`
2. Add an ALTER TABLE entry in `migrations/schema.py` using the `(table, column, type)` tuple pattern
3. New columns must be nullable or have defaults. Never rename or drop columns without explicit user approval.
4. Update `to_dict()` if the field should appear in API responses

## Guardrails & Validation

All input validation lives in `guardrails.py`. Read it for the canonical set of valid values (statuses, categories, time slots, transport types). Do not duplicate these lists elsewhere — import from `guardrails`.

The trip audit service (`services/trip_audit.py`) runs 14 integrity checks. It powers:
- Boot-time warnings via `migrations/validate.py` (read-only, advisory)
- Export gating via `blueprints/export.py` (blockers prevent export, `?force=1` overrides)
- API via `GET /api/trip/audit`

## CSS Architecture

Always loaded (in `base.html`): `base.css` → `layout.css` → `components.css` → `dark.css`. Per-page CSS loaded via `{% block page_css %}`. Dark mode overrides go in `dark.css` (loads last for specificity). When adding styles, add to the appropriate per-page file or `components.css` for shared styles.

## After Every Code Change

1. **Run tests:** `python -m pytest tests/ -v` — all must pass.
2. **Verify boot:** `python -c "from app import create_app; create_app()"` — check for warnings.
3. **Bump service worker** in `static/sw.js` if you changed any CSS, JS, or HTML.
4. **Check both themes** — verify affected pages in light and dark mode.
5. **If you changed a service function** — verify both the UI route and the chat executor still work.
6. **If you changed seed data** — run `python scripts/export_seed.py` and commit `data/seed.db`.

## How to Make Common Changes

### Add a new page/feature
1. Create blueprint in `blueprints/`, template in `templates/` (extends `base.html`), CSS in `static/css/`
2. Register blueprint in `create_app()` in `app.py`
3. Add nav link in `base.html`, JS file in `static/js/` if needed
4. Bump service worker cache version

### Add a new API endpoint
Add the route to the appropriate blueprint. Return `jsonify()`, emit Socket.IO events for UI updates. Follow existing patterns in the same file.

### Add a new chat tool
1. Define the tool schema in `blueprints/chat/tools.py` (import enums from `guardrails`)
2. Add the handler in `blueprints/chat/executor.py` (use fuzzy matching, delegate to services)
3. Add a service function if the mutation doesn't exist yet

## Deployment

- **Git push = production deploy.** Every push to `main` auto-deploys to Railway. No staging environment.
- **Boot sequence:** `start.py` (backup DB, seed if first deploy) → `wsgi.py` → `create_app()` → schema DDL → legacy verification → read-only audit → document sync → serve.
- **Env vars:** `SECRET_KEY`, `TRIP_PASSWORD`, `ANTHROPIC_API_KEY`, `RAILWAY_VOLUME_MOUNT_PATH`. Production refuses default values.
- **The production DB is irreplaceable** — it contains live data that does not exist elsewhere.
- **Secrets are env vars.** Never hardcode passwords or API keys. Never commit `.env` files.
- Never force-push or reset `main`.

## Engineering Discipline

- **One concern per commit.** Do not refactor while fixing a bug. Do not add features while refactoring.
- **No dead code.** Do not comment things out, add unused imports, or create helpers "for later."
- **No speculative abstractions.** Do not create config systems or plugin architectures for one-time operations. Write the simplest code that solves the actual problem.
- **Validate at boundaries, trust internally.** User input and API requests get validated via `guardrails.py`. Internal service calls do not need redundant re-validation.
- **Test what you ship.** New route → add smoke test. New service function → add service test.
- **Degrade gracefully.** Every page must work with zero items, one item, and many items.
- **Templates are composable.** Use `{% extends %}`, `{% block %}`, `{% include %}`. Do not duplicate HTML.

## Running Locally

```bash
pip install -r requirements.txt
python import_markdown.py          # first time: copies seed.db → japan_trip.db
python app.py                      # http://localhost:5000
python -m pytest tests/ -v         # run all tests
```

Default password: `changeme` (override with `TRIP_PASSWORD` env var).
