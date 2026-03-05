# Japan Travel App - Project Notes

## Overview
A Flask web app for planning and managing a Japan trip (April 2026). Deployed on Railway with auto-deploy from GitHub. Uses SQLite with a persistent volume at `/data`.

**GitHub:** https://github.com/Jwood1126-coder/japan-travel-app.git
**Railway project:** supportive-purpose

## Tech Stack
- **Backend:** Flask + SQLAlchemy + SQLite
- **Real-time:** Flask-SocketIO (eventlet)
- **CSS:** PicoCSS + custom `app.css` with dark mode (`[data-theme="dark"]`)
- **Deployment:** Railway (auto-deploy from GitHub main branch)
- **DB persistence:** Railway volume at `/data`

## Key Architecture Patterns

### Startup Functions (app.py)
Railway's DB persists across deploys, so schema/data changes use idempotent startup functions:
1. `_run_migrations()` - ALTER TABLE ADD COLUMN (skips if exists)
2. `_seed_checklist_decisions()` - Upgrades checklist items to decision type
3. `_fix_booking_urls()` - Fixes generic/wrong accommodation URLs
4. `_seed_guide_urls()` - Adds japan-guide.com travel guide URLs to locations

**Pattern:** Check if already done, skip if so. Always safe to re-run.

### Data Seeding (import_markdown.py)
Used for fresh DB creation only. Contains all hardcoded trip data:
- Locations, days, activities, accommodations, flights, transport, budget, checklists, reference content
- Run with: `python import_markdown.py`

### Checklist-Itinerary Integration
- `AccommodationOption.is_selected` drives what shows in the day view
- If no option selected, all non-eliminated options shown as "pending choices"
- `price_tier` property: `$` (<$60), `$$` ($60-120), `$$$` (>$120)

## File Structure
```
japan-travel-app/
  app.py                    # App factory, migrations, startup fixers
  models.py                 # All SQLAlchemy models
  config.py                 # Config (DB path, secret key, etc.)
  import_markdown.py        # Data seeding for fresh DB
  wsgi.py                   # Production entry point
  blueprints/
    itinerary.py            # Day view, index, activity toggle
    accommodations.py       # Accommodation CRUD
    checklists.py           # Checklist view + API
    uploads.py              # Photo uploads
    chat.py                 # AI chat feature
    reference.py            # Reference content pages
  templates/
    base.html               # Layout with nav, dark mode, safe areas
    index.html              # Trip overview/dashboard
    day.html                # Individual day itinerary view
    checklists.html         # Checklist management
    accommodations.html     # Accommodation picker
    reference.html          # Reference info pages
  static/
    css/app.css             # All custom styles
    js/itinerary.js         # Day view JS (toggle, notes, etc.)
    js/checklists.js        # Checklist interactions
    js/accommodations.js    # Accommodation picker JS
```

## Models (models.py)
- **Trip** - Single trip record
- **Location** - Cities/regions (Tokyo, Hakone, etc.) with `guide_url` for travel guides
- **Day** - Individual days linked to locations
- **Activity** - Things to do, with time slots, costs, JR pass coverage
- **AccommodationLocation** - Where to stay (by city)
- **AccommodationOption** - Individual hotel/ryokan options with pricing, booking URLs
- **Flight** - Flight legs
- **TransportRoute** - Train/bus routes between cities
- **BudgetItem** - Budget line items
- **ChecklistItem** - Pre-trip/packing tasks and decisions
- **ChecklistOption** - Options within decision-type checklist items
- **Photo** - Trip photos
- **ChatMessage** - AI chat history
- **ReferenceContent** - Static reference info

## Recent Changes

### Travel Guide Integration (latest)
- Added `guide_url` field to Location model
- Each location links to its japan-guide.com comprehensive guide page
- Guide link appears in the day view header next to location badge
- URLs: Tokyo, Hakone, Takayama, Shirakawa-go, Kanazawa, Kyoto, Osaka

### URL Fixes (commit 681a71d)
- Fixed 9 accommodation URLs that pointed to generic search pages
- Fixed 3 property names that were wrong (Sotetsu Fresa, Dormy Inn, Hotel Mets)
- All 38+ accommodations verified through web searches

### Checklist-Itinerary Integration (commit fa86d4d)
- $ pricing symbols on accommodations (replaces exact prices)
- Links on all checklist items
- Pending accommodation options shown in day view when none selected
- Fixed index page stats

## Locations & Guide URLs
| Location | Guide URL |
|----------|-----------|
| Tokyo | https://www.japan-guide.com/e/e2164.html |
| Hakone | https://www.japan-guide.com/e/e5200.html |
| Takayama | https://www.japan-guide.com/e/e5900.html |
| Shirakawa-go | https://www.japan-guide.com/e/e5950.html |
| Kanazawa | https://www.japan-guide.com/e/e2167.html |
| Kyoto | https://www.japan-guide.com/e/e2158.html |
| Osaka | https://www.japan-guide.com/e/e2157.html |

## Known Issues / Future Work
From the full app review:
- Toast notifications for user feedback (instead of silent saves)
- Session security flags (httponly, secure cookies)
- ARIA labels for accessibility
- ScrollIntoView on expand for better UX
- Enum validation on status fields
- Pull-to-refresh could use visual indicator improvement

## Deployment Notes
- Push to `main` branch triggers auto-deploy on Railway
- SQLite DB lives on Railway volume - schema changes need migration functions in `_run_migrations()`
- Data fixes for existing records need startup fixer functions (like `_fix_booking_urls()`)
- `import_markdown.py` only runs for fresh DB creation
