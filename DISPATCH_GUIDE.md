# Japan Travel App — Developer Context for Claude Dispatch

Use this guide when making changes to the Japan travel app from Dispatch or any new Claude session.

## Quick Start

```bash
git clone https://<GITHUB_PAT>@github.com/Jwood1126-coder/japan-travel-app.git
cd japan-travel-app
# Make changes, then:
git add <files> && git commit -m "Description"
git push https://<GITHUB_PAT>@github.com/Jwood1126-coder/japan-travel-app.git main
# Railway auto-deploys on push. App URL: https://web-production-f84b27.up.railway.app/
```

> **Note:** Ask Jake for the GitHub PAT token. He'll provide it in the chat.


## Architecture

- **Flask 3.1 + SQLite + Flask-SocketIO** PWA deployed on Railway
- **PicoCSS 2.0**, Jinja2 templates, vanilla JS
- Boot-time migrations in `migrations/schema.py` (idempotent, sentinel pattern)
- Service worker cache versioning in `static/sw.js` and `static/js/app.js`

## How to Make Schedule Changes

**ALL data changes go through `migrations/schema.py`** using the sentinel pattern. Never edit the SQLite DB directly — it lives on Railway's persistent volume.

### Sentinel Migration Pattern (copy this template):

```python
def _migrate_your_change_name_v1(cursor, conn):
    """Description of what this migration does."""
    cursor.execute("SELECT notes FROM trip WHERE id = 1")
    row = cursor.fetchone()
    if row and row[0] and '__your_change_name_v1' in row[0]:
        return

    # Your SQL changes here
    cursor.execute("""
        UPDATE activity
        SET title = 'New Title', description = 'New description'
        WHERE id = 123
    """)

    # Set sentinel (MUST be last before commit)
    cursor.execute("""
        UPDATE trip SET notes = COALESCE(notes, '') || ' __your_change_name_v1'
        WHERE id = 1 AND (notes IS NULL OR notes NOT LIKE '%__your_change_name_v1%')
    """)
    conn.commit()
    print('  Description of what changed')
```

### Then register it in `run_schema_migrations()` (~line 82-110):
```python
    _migrate_your_change_name_v1(cursor, conn)
```

### After any change, bump the service worker cache:
- `static/sw.js`: Change `japan-trip-vNNN` to `japan-trip-v(NNN+1)` (currently v129)
- `static/js/app.js`: Change `sw.js?v=NNN` to `sw.js?v=(NNN+1)`

## Database Reference

### Day Mapping (CRITICAL — day_id ≠ day_number!)
| day_id | day_number | date       | city       |
|--------|-----------|------------|------------|
| 2      | 1         | 2026-04-05 | Travel     |
| 3      | 2         | 2026-04-06 | Tokyo      |
| 4      | 3         | 2026-04-07 | Tokyo      |
| 5      | 4         | 2026-04-08 | Hakone     |
| 6      | 5         | 2026-04-09 | Takayama   |
| 7      | 6         | 2026-04-10 | Takayama   |
| 8      | 7         | 2026-04-11 | Takayama   |
| 9      | 8         | 2026-04-12 | Kyoto      |
| 10     | 9         | 2026-04-13 | Kyoto      |
| 11     | 10        | 2026-04-14 | Kyoto      |
| 12     | 11        | 2026-04-15 | Hiroshima  |
| 13     | 12        | 2026-04-16 | Osaka      |
| 16     | 13        | 2026-04-17 | Osaka      |
| 14     | 14        | 2026-04-18 | Departure  |

### Activity Fields
- `day_id` — FK to day table (use day_id, NOT day_number)
- `title` — display name
- `time_slot` — morning, afternoon, evening, night
- `start_time` — e.g. "1:30 PM" (text, not time type)
- `description` — longer info text
- `address` — physical address
- `maps_url` — Google Maps Directions URL (format: `?api=1&origin=...&destination=...&travelmode=transit`)
- `url` — official website or ticket link
- `category` — temple, food, nightlife, shopping, nature, culture, transit, car-culture, logistics, entertainment
- `is_confirmed` — 1 if actually booked (overrides unbooked plans)
- `book_ahead` — 1 if needs advance reservation
- `book_ahead_note` — where/how to book
- `is_eliminated` — 1 if ruled out
- `is_completed` — 1 if done
- `sort_order` — display order within time slot
- `getting_there` — transit tips from previous activity
- `notes` — user notes
- `cost_per_person` — number
- `cost_note` — text like "¥500 entry"

### Accommodation IDs (selected stays)
- Tokyo: Sotetsu Fresa Inn Higashi-Shinjuku (Apr 6-9)
- Takayama: TAKANOYU (Apr 9-12)
- Kyoto Stay 1: Tsukiya-Mikazuki (Apr 12-14)
- Kyoto Stay 2: KumoMachiya KOSUGI (Apr 14-16)
- Osaka: Hotel The Leben Osaka (Apr 16-18)

### Google Maps Directions URL Format
Always use this format for maps_url:
```
https://www.google.com/maps/dir/?api=1&origin=ORIGIN+NAME&destination=DESTINATION+NAME&travelmode=transit
```
Replace spaces with `+`. Use descriptive names Google can geocode (e.g. "Sotetsu+Fresa+Inn+Higashi-Shinjuku,+Tokyo").

## Key Files

| Purpose | File |
|---------|------|
| Data migrations | `migrations/schema.py` |
| Home page route | `blueprints/itinerary.py` |
| Day view template | `templates/day.html` |
| Chat tools | `blueprints/chat/tools.py` |
| Chat executor | `blueprints/chat/executor.py` |
| Chat prompt | `blueprints/chat/prompt.py` |
| Gmail sync | `services/gmail_sync.py` |
| Service worker | `static/sw.js` |
| Main JS | `static/js/app.js` |

## Common Tasks

### Add a new activity
```python
cursor.execute("""
    INSERT INTO activity (day_id, title, time_slot, start_time, description,
        address, maps_url, url, category, is_confirmed, sort_order)
    VALUES (4, 'Activity Name', 'afternoon', '2:00 PM', 'Description',
        'Address', 'maps_url', 'website_url', 'food', 0, 20)
""")
```

### Update an existing activity
```python
cursor.execute("""
    UPDATE activity SET title = 'New Title', description = 'New desc'
    WHERE id = 123
""")
```

### Move an activity to a different day
```python
cursor.execute("UPDATE activity SET day_id = 4 WHERE id = 123")  -- day_id=4 is Day 3
```

### Download live DB for inspection
```bash
curl -s https://web-production-f84b27.up.railway.app/api/backup/download -o live.db
sqlite3 live.db "SELECT id, title, day_id FROM activity WHERE day_id = 4"
```

## IMPORTANT: Python string rules in SQL
Never use Python implicit string concatenation inside triple-quoted SQL strings.
```python
# BAD — causes SQLite syntax error and 502:
cursor.execute("""UPDATE activity SET notes = 'line one'
    'line two' WHERE id = 1""")

# GOOD — single continuous string:
cursor.execute("""UPDATE activity SET notes = 'line one line two' WHERE id = 1""")
```

## Trip Context
- Travelers: Jake (33) and wife Jessica, from Cleveland OH
- 14-day cherry blossom trip, April 5-18, 2026
- Route: Cleveland → Tokyo (4 nights) → Takayama (3 nights) → Kyoto (4 nights) → Osaka (2 nights) → Home
- 14-day JR Pass for all shinkansen/JR trains
- App URL: https://web-production-f84b27.up.railway.app/
