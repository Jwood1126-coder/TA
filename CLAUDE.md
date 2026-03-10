# CLAUDE.md — Japan Travel App

## What This App Is

A Flask + SQLite PWA for planning and managing a Japan trip. Deployed on Railway with auto-deploy from the `main` branch. Mobile-first, password-protected, with an AI chat agent built in.

## Tech Stack

- **Backend:** Flask 3.1, Flask-SQLAlchemy, Flask-SocketIO, Gunicorn + gevent
- **Database:** SQLite on a Railway persistent volume (`/data/japan_trip.db`)
- **Frontend:** Vanilla JS, PicoCSS 2.0, custom CSS with cherry blossom theme + dark mode
- **AI:** Anthropic Claude API (chat blueprint with tool use + image processing)
- **PWA:** Service worker with network-first caching

## Project Structure

```
app.py                  # App factory + 26 idempotent startup migrations (3,065 lines)
models.py               # 13 SQLAlchemy models (327 lines)
config.py               # Flask config, env vars, production validation
wsgi.py                 # Gunicorn entry point (imports create_app)
start.py                # Railway bootstrap: dir setup, DB backup, seed, launch gunicorn
import_markdown.py      # Seed script: builds fresh DB from source_data/ markdown files

blueprints/
  itinerary.py          # Dashboard (index) + day view routes
  accommodations.py     # Hotel picker CRUD + reorder + batch operations
  checklists.py         # Checklist view + toggle/add/update/delete APIs
  chat.py               # AI chat: system prompt, tool defs, SSE streaming, image upload
  activities.py         # Activity list + toggle/update/add/delete APIs
  uploads.py            # Photo upload with EXIF extraction + thumbnail generation
  documents.py          # PDF upload + list view
  calendar.py           # Month calendar view
  backup.py             # DB backup/restore via API
  export.py             # PDF export
  bookahead.py          # Ticketed activities page
  reference.py          # Travel reference content page

templates/              # Jinja2 templates (base.html is the layout)
static/css/app.css      # All styles (4,200+ lines), includes dark mode
static/js/              # Per-page JS files (vanilla, no framework)
static/sw.js            # Service worker
source_data/            # Original markdown plans (seed data for import_markdown.py)
docs/PROJECT_NOTES.md   # Historical project notes (partially outdated)
```

## How the Database Works

### Production (Railway)
- SQLite lives on a **persistent volume** at `$RAILWAY_VOLUME_MOUNT_PATH/data/japan_trip.db`
- The DB in the git repo (`data/japan_trip.db`) is gitignored and NOT used on Railway
- `start.py` auto-backs up the DB before every deploy (keeps last 20)
- On first deploy: runs `import_markdown.py` to build DB from `source_data/*.md`

### Local Development
- DB lives at `./data/japan_trip.db`
- Run `python import_markdown.py` to create a fresh local DB from markdown source

### The Migration System

**This is critical to understand.** The live production DB has been mutated by 26 startup migration functions in `app.py`. These run on every app boot inside `create_app()`:

1. Each migration function is **idempotent** — it checks if already applied and skips if so
2. They are **order-dependent** — do not reorder them
3. They contain **hardcoded data** (activity titles, dates, URLs, coordinates, booking details)
4. The `source_data/` markdown files are the *original* seed, but the live DB has drifted far from them via these migrations

**Rules for migrations:**
- New data changes go in a NEW function at the end of the migration list in `create_app()`
- Always add an idempotency guard at the top (check if already applied, return early if so)
- Never modify or reorder existing migration functions
- Never replace the live DB with the repo DB — it would lose all runtime data (bookings, completions, photos, chat history)
- Test locally first: `python -c "from app import create_app; create_app()"`

### Schema Changes
- New columns: add to `_run_migrations()` at the top of `app.py` using the `(table, column, type)` tuple pattern
- Also add the column to the model in `models.py`
- The ALTER TABLE is wrapped in try/except so it's safe to re-run

## Key Architecture Patterns

### Authentication
- Password-based login (`TRIP_PASSWORD` env var)
- Session cookies (24hr lifetime, HTTPOnly, SameSite=Lax, Secure in production)
- Rate limiting: 5 login attempts per 5 minutes per IP (in-memory)
- `@app.before_request` redirects unauthenticated users to `/login`

### Real-time Updates
- Flask-SocketIO emits events (`accommodation_updated`, `activity_updated`)
- Client-side Socket.IO listeners refresh UI without page reload
- Gevent worker model supports WebSocket connections

### AI Chat (blueprints/chat.py)
- 100+ line system prompt with trip context, personality, and tool instructions
- 12+ tools for modifying DB records (accommodations, activities, flights, checklists, budget)
- Image processing: extracts booking confirmations, flight receipts via Claude vision
- SSE streaming for incremental response display
- Context window includes full trip state (all accommodations, activities, flights, transport)

### Template Filters (defined in create_app)
- `maps_link(address)` — Google Maps search URL
- `translate_link(url)` — Google Translate wrapper for Japanese pages
- `linkify_stations(text)` — Auto-links station names to Google Maps

### Accommodation Selection Logic
- Each city has an `AccommodationLocation` with multiple `AccommodationOption` records
- `is_selected=True` marks the chosen hotel (only one per location)
- `is_eliminated=True` removes from consideration without deleting
- Unselected locations show "pending choices" count in the day view
- `booking_status`: not_booked / booked / confirmed
- `price_tier` property: `$` (<$60/night), `$$` ($60-120), `$$$` (>$120)

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
6. Add styles to `static/css/app.css`

### Add a new model field
1. Add the column to the model class in `models.py`
2. Add an ALTER TABLE entry in `_run_migrations()` in `app.py`
3. Update any `to_dict()` methods if the field should be in API responses

### Modify existing trip data in production
1. Write a new idempotent migration function in `app.py`
2. Add it to the end of the migration call list in `create_app()`
3. Push to `main` — it runs on next deploy

### Add a new API endpoint
- Add the route to the appropriate blueprint
- Follow existing patterns: return `jsonify()`, emit Socket.IO events for UI updates
- All routes are auth-gated by the `before_request` hook

## Deployment

- **Platform:** Railway (auto-deploy from GitHub `main` branch)
- **Entry:** `Procfile` → `start.py` → Gunicorn with gevent workers
- **Boot sequence:** `start.py` (backup DB, seed if needed) → `wsgi.py` (create_app) → migrations run → app serves
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

## Things to Be Careful About

- **app.py is 3,065 lines** — most of it is migration functions. Don't refactor these without tests.
- **No test suite exists.** Changes should be verified manually or by running the app locally.
- **The CSS is one 4,200-line file.** Changes need care to avoid breaking other pages. Dark mode uses `[data-theme="dark"]` selectors.
- **source_data/ markdown files are outdated** relative to the live DB. They reflect the original trip plan before 26 migrations of changes.
- **Booking-Action-Guide.md** references an older 16-day trip structure — the trip is now 14 days.
- **Never force-push or reset main** — Railway auto-deploys from it.
- **Never replace the production DB** with a fresh import — it contains live booking data, chat history, photos, and completed activities.

## Running Locally

```bash
pip install -r requirements.txt
python import_markdown.py    # first time only, creates data/japan_trip.db
python app.py                # runs on http://localhost:5000
```

Default password: `changeme` (override with `TRIP_PASSWORD` env var)
