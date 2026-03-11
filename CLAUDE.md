# CLAUDE.md — Japan Travel App

## What This App Is

A Flask + SQLite PWA for planning and managing a Japan trip. Deployed on Railway with auto-deploy from the `main` branch. Mobile-first, password-protected, with an AI chat agent built in.

## Source of Truth for Bookings

**`Documentation/flights/` contains the PDF booking confirmations. These are the AUTHORITATIVE source for all accommodation and flight data.** Never override booking details from PDFs with inferences, guesses, or "schedule audits."

### Confirmed accommodation chain (ALL BOOKED):
1. **Sotetsu Fresa Inn** — Tokyo, Apr 6–9 (3n) — Agoda #976558450
2. **TAKANOYU** — Takayama, Apr 9–12 (3n) — Airbnb #HMDDRX4NFX (host Hiroto)
3. **Tsukiya-Mikazuki** — Kyoto, Apr 12–14 (2n) — Airbnb #HMXTP9H2Z9
4. **Kyotofish Miyagawa** — Kyoto, Apr 14–16 (2n) — Airbnb (host Karen)
5. **Hotel The Leben Osaka** — Osaka, Apr 16–18 (2n) — Agoda #976698966

### NO KANAZAWA OVERNIGHT. Day 8 is transit only: Takayama → Shirakawa-go → Kanazawa → Kyoto.

### Flights:
- **Outbound**: DL5392 + DL275, Apr 5, CLE→DTW→HND. Confirmation HBPF75.
- **Return**: UA876 + UA1470, Apr 18, HND→SFO→CLE. Confirmation I91ZHJ.

## Tech Stack

- **Backend:** Flask 3.1, Flask-SQLAlchemy, Flask-SocketIO, Gunicorn + gevent
- **Database:** SQLite on a Railway persistent volume (`/data/japan_trip.db`)
- **Frontend:** Vanilla JS, PicoCSS 2.0, custom CSS with cherry blossom theme + dark mode
- **AI:** Anthropic Claude API (chat blueprint with tool use + image processing)
- **PWA:** Service worker with network-first caching

## Project Structure

```
app.py                  # App factory, auth routes, template filters (~190 lines)
models.py               # 15 SQLAlchemy models (includes Document model)
extensions.py           # Shared Flask extensions (socketio) — importable without circular deps
guardrails.py           # Runtime validation: booking status, document-first rule, date overlaps
config.py               # Flask config, env vars, production validation
wsgi.py                 # Gunicorn entry point (imports create_app)
start.py                # Railway bootstrap: dir setup, DB backup, seed, launch gunicorn
import_markdown.py      # Seed script: copies data/seed.db → data/japan_trip.db

data/seed.db            # Canonical seed database (committed, matches confirmed bookings)

services/               # Domain service layer — canonical mutation pipeline
  __init__.py            # Package docstring
  accommodations.py      # select, eliminate, delete, update_status, add_option, reorder
  activities.py          # toggle, add, update, eliminate, delete, update_notes, update_day_notes
  checklists.py          # toggle, update_status, create, delete

scripts/
  export_seed.py        # Export current DB as new seed.db (strips chat/photos)
  fix_local_db.py       # One-time data fixes (applied to create current seed.db)

tests/
  test_smoke.py         # 29 smoke tests: seed integrity, routes, export quality

migrations/
  schema.py             # Schema migrations: ALTER TABLE column additions (idempotent)
  legacy.py             # Verifies all legacy migrations were applied (sentinel checks)
  validate.py           # Boot-time schedule validation (date gaps, overpacked days, etc.)
  archive/              # Frozen reference of all 39 original migration functions (NEVER imported)

blueprints/
  itinerary.py          # Dashboard (index) + day view routes
  accommodations.py     # Hotel picker CRUD + reorder + batch operations
  checklists.py         # Checklist view + toggle/add/update/delete APIs
  activities.py         # Activity list + toggle/update/add/delete APIs
  uploads.py            # Photo upload with EXIF extraction + thumbnail generation
  documents.py          # Document-first: upload, link/unlink, auto-match, seed on boot
  calendar.py           # Month calendar view
  backup.py             # DB backup/restore via API
  export.py             # PDF export
  bookahead.py          # Ticketed activities page
  reference.py          # Travel reference content page
  chat/                 # AI chat package
    __init__.py          # Exports chat_bp
    prompt.py            # SYSTEM_PROMPT constant (~140 lines)
    tools.py             # TOOLS + SERVER_TOOLS definitions (16 tools)
    executor.py          # execute_tool() — all tool handlers (~300 lines)
    context.py           # build_context() — dynamic trip state for AI
    routes.py            # Flask routes: chat view, send message (SSE), history

templates/              # Jinja2 templates (base.html is the layout)
static/css/             # 12 organized CSS files (see CSS Architecture below)
static/js/              # Per-page JS files (vanilla, no framework)
static/sw.js            # Service worker (bump cache version on every deploy)
source_data/            # Original markdown plans (historical reference, no longer used for seeding)
Documentation/flights/  # PDF booking confirmations (authoritative source)
```

## CSS Architecture

CSS is split into 12 files loaded via `{% block page_css %}` in templates:

- **Always loaded** (in base.html): `base.css` → `layout.css` → `components.css` → `dark.css`
- **Per-page**: `dashboard.css`, `day.css`, `calendar.css`, `accommodations.css`, `checklists.css`, `activities.css`, `chat.css`, `documents.css`
- **dark.css** loads last for specificity (all `[data-theme="dark"]` selectors)
When adding styles:
- Add to the appropriate per-page CSS file, or `components.css` for shared styles
- Dark mode overrides go in `dark.css`
- Bump the service worker cache version after any CSS change

## How the Database Works

### Seed Architecture
- `data/seed.db` is the **canonical seed** committed to git — matches confirmed booking state
- `import_markdown.py` copies `seed.db` → `japan_trip.db` (use `--force` to overwrite)
- `source_data/*.md` are historical reference only — no longer used for seeding
- To update the seed after production changes: download backup, run `python scripts/export_seed.py /path/to/backup.db`

### Production (Railway)
- SQLite lives on a **persistent volume** at `$RAILWAY_VOLUME_MOUNT_PATH/data/japan_trip.db`
- The DB in the git repo (`data/japan_trip.db`) is gitignored and NOT used on Railway
- `start.py` auto-backs up the DB before every deploy (keeps last 20)
- On first deploy: copies `data/seed.db` to the volume

### Local Development
- DB lives at `./data/japan_trip.db`
- Run `python import_markdown.py` to initialize from `data/seed.db`

### The Migration System

The live production DB was mutated by 39 migration functions that originally lived in `app.py`. These have been **archived** to `migrations/archive/` (frozen, never imported). On every boot, `create_app()` runs:

1. **`migrations/schema.py`** — Adds missing columns/tables via ALTER TABLE + CREATE TABLE (idempotent)
2. **`migrations/legacy.py`** — Verifies all 39 legacy migrations were applied (sentinel checks, does NOT re-run them)
3. **`migrations/validate.py`** — Schedule validation: date gaps, overpacked days, departure conflicts, document integrity
4. **Document seeding** — `seed_document_records()` syncs files on disk → DB, `auto_link_documents()` matches to bookings

**Rules for data changes:**
- The migration system is **closed** — no new migration functions should be added
- Use API endpoints, the AI chat, or one-time scripts in `scripts/` for data changes
- Never replace the live DB with a fresh import — it contains live booking data, chat history, photos
- Test boot: `python -c "from app import create_app; create_app()"`

### Schema Changes
- New columns: add to `migrations/schema.py` using the `(table, column, type)` tuple pattern
- Also add the column to the model in `models.py`
- The ALTER TABLE is wrapped in try/except so it's safe to re-run

## Document-First Architecture

### The Iron Rule
**If there is no uploaded document (PDF, email screenshot, booking confirmation) linked to a booking, it CANNOT have status `confirmed` in the database.**

This is enforced at three levels:
1. **API layer** — `guardrails.validate_document_status()` rejects confirmed without `document_id`
2. **AI chat** — `executor.py` calls `validate_document_status()` before setting booking_status
3. **Boot-time** — `migrations/validate.py` Check 6 warns about confirmed items missing documents

### Status Transitions
```
not_booked → researching → booked → confirmed → completed
                                ↑                    ↓
                                └── cancelled ←──────┘
```
- `not_booked → booked`: Set dates and details, no document needed
- `booked → confirmed`: **REQUIRES document_id** — the API rejects without it
- `confirmed → cancelled`: Can happen anytime, preserves the record
- Deleting/unlinking a document auto-downgrades `confirmed → booked`
- NEVER skip states (e.g., `not_booked → confirmed` without a document)

### Document Model
- `Document` records in the DB represent uploaded files (PDFs, images)
- `AccommodationOption.document_id` and `Flight.document_id` link bookings to their proof
- On boot, `seed_document_records()` syncs files on disk → DB records
- On boot, `auto_link_documents()` matches unlinked documents to bookings by confirmation number, name keywords, and date patterns

### Planned vs Confirmed Visual Language
- **Confirmed** (doc-backed): solid green left border, 📄 icon, document link shown
- **Planned** (no document): dashed yellow left border, "Planned" badge, "No document linked" hint
- NEVER render planned items with the same styling as confirmed items

### Accommodation Chain
The confirmed accommodation chain defines the trip structure:
1. Sotetsu Fresa Inn → Tokyo, Apr 6–9
2. TAKANOYU → Takayama, Apr 9–12
3. Tsukiya-Mikazuki → Kyoto, Apr 12–14
4. Kyotofish Miyagawa → Kyoto, Apr 14–16
5. Hotel The Leben Osaka → Osaka, Apr 16–18

Days get their city from the accommodation covering that date. Transport routes connect consecutive stays.

## Guardrails & Validation

### Runtime Enforcement (`guardrails.py`)
- `validate_booking_status()` — rejects invalid status strings
- `validate_document_status()` — enforces the iron rule (confirmed requires document)
- `validate_time_slot()` — rejects invalid time slots
- `validate_non_negative()` — rejects negative prices/costs
- `check_accom_date_overlap()` — detects overlapping accommodation dates

### Boot-time Validation (`migrations/validate.py`)
Runs on every boot, prints warnings (does not block startup):
1. Accommodation date chain gaps/overlaps
2. Accommodation num_nights consistency
3. Departure day activity conflicts
4. Overpacked days (>10 activities)
5. Multiple selected options per location
6. Document integrity (confirmed without document, conf# without document)

## Service Layer (`services/`)

All mutations to accommodations, activities, and checklists flow through the service layer. This ensures UI routes and AI chat tools share identical validation, cascades, and Socket.IO emits.

### Pipeline per operation
Each service function owns: **input validation → DB write → cascade side effects → Socket.IO emit**

### What lives in services vs. blueprints
- **Services**: Domain logic shared by both UI and chat (validation, DB mutation, cascades, emit)
- **Blueprints**: HTTP concerns (request parsing, response formatting) + chat-specific fuzzy matching
- **Chat executor**: Fuzzy name/location matching → resolves to an ID → calls the same service function as the UI route

### Key cascades handled by services
- `accommodations.update_status()`: booking_status validation, document-first rule, price recalculation, checklist sync, Socket.IO emit
- `accommodations.select()` / `eliminate()`: mutual exclusion enforcement, checklist status sync
- `checklists.update_status()`: cascades status to linked AccommodationOption
- `activities.toggle()`: sets `completed_at` timestamp, emits update

### Adding a new mutation
1. Add the service function in `services/<domain>.py`
2. Call it from both the blueprint route and `blueprints/chat/executor.py`
3. Never put shared mutation logic directly in a route handler or chat tool

## Key Architecture Patterns

### Authentication
- Password-based login (`TRIP_PASSWORD` env var)
- Session cookies (24hr lifetime, HTTPOnly, SameSite=Lax, Secure in production)
- Rate limiting: 5 login attempts per 5 minutes per IP (in-memory)
- `@app.before_request` redirects unauthenticated users to `/login` (currently disabled for pre-trip sharing)

### Real-time Updates
- Flask-SocketIO emits events (`accommodation_updated`, `activity_updated`, `document_updated`)
- Client-side Socket.IO listeners refresh UI without page reload
- Gevent worker model supports WebSocket connections

### AI Chat (blueprints/chat/)
- 140-line system prompt with trip context, personality, and tool instructions (`prompt.py`)
- 16 tools for modifying DB records: accommodations, activities, flights, checklists, budget (`tools.py` + `executor.py`)
- Image processing: extracts booking confirmations, flight receipts via Claude vision
- SSE streaming for incremental response display (`routes.py`)
- Dynamic context includes full trip state: all accommodations, activities, flights, transport (`context.py`)
- Server-side web search tool (Anthropic-managed)
- **Document enforcement**: chat tools validate document status before setting booking_status to confirmed

### Template Filters (defined in create_app)
- `maps_link(address)` — Google Maps search URL
- `translate_link(url)` — Google Translate wrapper for Japanese pages
- `linkify_stations(text)` — Auto-links station names to Google Maps

### Accommodation Selection Logic
- Each city has an `AccommodationLocation` with multiple `AccommodationOption` records
- `is_selected=True` marks the chosen hotel (only one per location)
- `is_eliminated=True` removes from consideration without deleting
- `booking_status`: not_booked / researching / booked / confirmed / cancelled
- `document_id`: FK to Document — required for `confirmed` status
- `price_tier` property: `$` (<$60/night), `$$` ($60-120), `$$$` (>$120)
- Foreign key is `location_id` (NOT `accommodation_location_id`), `rank` is NOT NULL

### Activity System
- Activities belong to Days, grouped by `time_slot` (morning/afternoon/evening/night)
- `is_substitute=True` — alternative option, shown collapsed
- `is_optional=True` — skip-able, visually marked
- `is_eliminated=True` — ruled out
- `completion_pct()` on Day model counts only non-substitute activities
- `sort_order` controls display order within a day

## How to Make Common Changes

### Add a new page/feature
1. Create a blueprint in `blueprints/new_feature.py`
2. Create a template in `templates/new_feature.html` (extends `base.html`)
3. Register the blueprint in `create_app()` in `app.py`
4. Add navigation link in `base.html` (bottom nav or "More" menu)
5. Add JS file in `static/js/` if needed
6. Add page-specific CSS file in `static/css/`, load via `{% block page_css %}`
7. Bump service worker cache version in `static/sw.js`

### Add a new model field
1. Add the column to the model class in `models.py`
2. Add an ALTER TABLE entry in `migrations/schema.py` using the `(table, column, type)` tuple pattern
3. Update any `to_dict()` methods if the field should be in API responses

### Add a new API endpoint
- Add the route to the appropriate blueprint
- Follow existing patterns: return `jsonify()`, emit Socket.IO events for UI updates
- All routes are auth-gated by the `before_request` hook (when enabled)

## Deployment

- **Platform:** Railway (auto-deploy from GitHub `main` branch)
- **Entry:** `Procfile` → `start.py` → Gunicorn with gevent workers
- **Boot sequence:** `start.py` (backup DB, seed if needed) → `wsgi.py` (create_app) → schema migrations → legacy verification → schedule validation → document seeding/auto-linking → app serves
- **Environment variables:** `SECRET_KEY`, `TRIP_PASSWORD`, `ANTHROPIC_API_KEY`, `RAILWAY_VOLUME_MOUNT_PATH`
- Production refuses to start if `SECRET_KEY` or `TRIP_PASSWORD` are default values

## Travel Agent Mindset — Cross-Cutting Concerns

When making ANY change to the trip data (activities, accommodations, transport, checklists), think like a travel agent — not just a database operator:

- **Conflict detection:** Check for schedule overlaps, impossible timelines (activity in Kyoto at 2 PM + Osaka at 2:30 PM), and overpacked days. A day with 10+ activities is unrealistic.
- **Transportation consistency:** Every activity needs a way to get there. If `getting_there` is empty on a sightseeing activity, fill it. When activities span different neighborhoods, account for transit time (20-30 min within Kyoto, 45-60 min across Tokyo, 2+ hours between cities).
- **Ripple effects:** Changing one thing affects others. Moving a hotel check-in date affects the previous hotel's checkout. Moving an activity to a different day may invalidate its `getting_there` directions. Booking a restaurant should replace the generic "dinner out" placeholder.
- **Schedule gaps:** If a day has morning and evening activities but nothing in the afternoon, that's either intentional rest or a gap to flag.
- **Data consistency across views:** The dashboard, calendar, day view, and checklist all pull from the same data. Changes must be consistent everywhere — don't update an accommodation name in one place without ensuring the calendar and dashboard reflect it. The `_build_location_groups()` function aggregates across multiple AccommodationLocation records per city.
- **Transit awareness:** Know which transit is JR Pass covered vs. not. Hakone Free Pass (separate purchase), private railways in Kyoto (Eizan, Keifuku — not JR), local subways/buses (need Suica/cash). The `jr_pass_covered` field on TransportRoute and Activity matters.
- **Days vs. nights:** A stay from Apr 6 check-in to Apr 9 check-out is 3 nights (not 4). Nights = checkout date minus check-in date. This is a common source of errors.
- **Transition days:** Checkout from one city and check-in to the next often happen on the same date (e.g., Apr 9 = Tokyo checkout + Takayama check-in). The calendar view merges these overlap dates.

## Behavioral Rules for AI Agents

### 1. Documents Are the Source of Truth
- If there is no uploaded document confirming a booking, it CANNOT be `confirmed`
- The accommodation chain defines the trip structure — days get their city from accommodations
- NEVER create a confirmed accommodation or flight without a `document_id`
- NEVER override PDF booking data with inferences, guesses, or "schedule audits"

### 2. No More Migration Functions
- The migration pattern from v1 is closed — do not create migration functions
- Data changes happen through: UI actions, API endpoints, or AI chat tools
- Schema changes (new columns/tables) go in `migrations/schema.py` as idempotent DDL
- For one-time data fixes, write a script in `scripts/`, run it, verify, delete it
- NEVER add data manipulation code that runs on every boot

### 3. Always Check DB State Before Writing
- Before creating a record, query whether it already exists
- Before updating a record, verify the current state matches your assumptions
- NEVER blindly set fields — read first, then decide what to change
- Use `guardrails.py` validation functions to check constraints before committing

### 4. Status Transitions Are Enforced
- `booked → confirmed`: REQUIRES `document_id` — the API rejects without it
- Deleting/unlinking a document auto-downgrades `confirmed → booked`
- NEVER skip states (e.g., `not_booked → confirmed` without a document)
- All status changes must go through `validate_booking_status()` and `validate_document_status()`

### 5. Cross-Cutting Changes Must Cascade
When an accommodation is confirmed/modified/cancelled:
- Update the checklist item for that city
- Verify transport routes still connect correctly
- Verify day assignments still match the date range

When a document is deleted/unlinked:
- All linked bookings downgrade from `confirmed` to `booked`
- Boot-time validation will flag any remaining inconsistencies

### 6. Error Handling
- NEVER wrap database operations in bare `try/except` that swallows errors
- Validate inputs BEFORE attempting the operation (use `guardrails.py`)
- If something unexpected happens, return an error to the user — don't silently continue

### 7. Testing After Changes
- **Always run smoke tests:** `python -m pytest tests/test_smoke.py -v` (29 tests, validates seed, routes, export)
- After any data model change: verify dashboard, calendar, day view, accommodations, documents, checklists
- After any CSS change: verify every page in both light and dark mode on mobile (375px)
- After any chat tool change: send a message that triggers the tool, check the DB result
- Before deploying: run `python -c "from app import create_app; create_app()"` and check for warnings
- Bump the service worker cache version in `static/sw.js`
- After updating seed data: run `python scripts/export_seed.py` and commit `data/seed.db`

## Things to Be Careful About

- **Smoke tests exist.** Run `python -m pytest tests/test_smoke.py -v` to validate seed integrity, routes, and export quality.
- **source_data/ markdown files are historical reference only.** Seeding uses `data/seed.db` instead. Do not use markdown files as a data source.
- **Never force-push or reset main** — Railway auto-deploys from it.
- **Never replace the production DB** with a fresh import — it contains live booking data, chat history, photos, and completed activities.
- **Service worker cache** must be bumped on every CSS/HTML/JS change (`static/sw.js`).
- **Document-first rule is enforced** — attempting to set `confirmed` without a `document_id` will be rejected at the API, chat, and UI levels.

## Running Locally

```bash
pip install -r requirements.txt
python import_markdown.py          # first time only, copies seed.db → japan_trip.db
python app.py                      # runs on http://localhost:5000
python -m pytest tests/ -v         # run smoke tests
```

Default password: `changeme` (override with `TRIP_PASSWORD` env var)
