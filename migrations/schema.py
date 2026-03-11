"""Schema migrations — adds missing columns/tables to the database.

Idempotent and safe to run on every boot. Uses raw SQLite DDL
so it works even if models have changed.
"""

import os
import sqlite3


def run_schema_migrations(app):
    """Add new columns/tables if they don't exist."""
    db_path = app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
    if not os.path.exists(db_path):
        return
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # --- Create Document table (Phase 6: document-first architecture) ---
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS document (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            original_name TEXT,
            file_type TEXT,
            file_size INTEGER,
            doc_type TEXT NOT NULL,
            extracted_data TEXT,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes TEXT
        )
    """)

    # --- Column additions (idempotent via try/except) ---
    migrations = [
        ('activity', 'address', 'TEXT'),
        ('accommodation_option', 'address', 'TEXT'),
        ('accommodation_option', 'is_eliminated', 'BOOLEAN DEFAULT 0'),
        ('location', 'address', 'TEXT'),
        ('flight', 'confirmation_number', 'TEXT'),
        ('chat_message', 'image_filename', 'TEXT'),
        ('checklist_item', 'url', 'TEXT'),
        ('location', 'guide_url', 'TEXT'),
        ('checklist_item', 'item_type', "TEXT DEFAULT 'task'"),
        ('checklist_item', 'status', "TEXT DEFAULT 'pending'"),
        ('checklist_item', 'accommodation_location_id', 'INTEGER'),
        ('activity', 'url', 'TEXT'),
        ('location', 'latitude', 'REAL'),
        ('location', 'longitude', 'REAL'),
        ('accommodation_option', 'booking_image', 'TEXT'),
        ('accommodation_option', 'maps_url', 'TEXT'),
        ('activity', 'maps_url', 'TEXT'),
        ('accommodation_option', 'check_in_info', 'TEXT'),
        ('accommodation_option', 'check_out_info', 'TEXT'),
        ('activity', 'is_eliminated', 'BOOLEAN DEFAULT 0'),
        ('chat_message', 'context_summary', 'TEXT'),
        ('activity', 'category', 'TEXT'),
        ('activity', 'why', 'TEXT'),
        ('activity', 'book_ahead', 'BOOLEAN DEFAULT 0'),
        ('activity', 'book_ahead_note', 'TEXT'),
        ('activity', 'getting_there', 'TEXT'),
        ('activity', 'is_confirmed', 'BOOLEAN DEFAULT 0'),
        ('accommodation_option', 'phone', 'TEXT'),
        # Phase 6: document-first FK columns
        ('accommodation_option', 'document_id', 'INTEGER REFERENCES document(id)'),
        ('flight', 'document_id', 'INTEGER REFERENCES document(id)'),
        # Transport route enrichment
        ('transport_route', 'maps_url', 'TEXT'),
        ('transport_route', 'url', 'TEXT'),
        # Checklist option location link
        ('checklist_option', 'maps_url', 'TEXT'),
        # Transport movement grouping
        ('transport_route', 'route_group', 'TEXT'),
    ]
    for table, column, col_type in migrations:
        try:
            cursor.execute(f'ALTER TABLE {table} ADD COLUMN {column} {col_type}')
        except sqlite3.OperationalError:
            pass  # Column already exists
    conn.commit()

    # --- One-shot data migrations (idempotent, safe to re-run) ---
    _migrate_transport_data(cursor, conn)
    _migrate_route_groups(cursor, conn)
    _migrate_activity_time_slots(cursor, conn)

    conn.commit()
    conn.close()


def _migrate_transport_data(cursor, conn):
    """Split Haneda combined route into two cards + enrich all routes with maps_url.

    Idempotent: checks current state before each change.
    Uses content-based lookups (NOT hardcoded IDs — production IDs differ from local).
    """
    # Find the Haneda combined route by content (any ID)
    cursor.execute("""
        SELECT id FROM transport_route
        WHERE route_from = 'Haneda Airport' AND transport_type LIKE '%OR%'
    """)
    haneda_combined = cursor.fetchone()
    if haneda_combined:
        cursor.execute("""
            UPDATE transport_route SET
                transport_type = 'Keikyu Line + subway',
                train_name = 'Keikyu Airport Express → Toei Oedo Line',
                duration = '~75 min',
                cost_if_not_covered = '~¥800',
                notes = 'Keikyu Line to Shinagawa, transfer to Toei Oedo Line to Higashi-Shinjuku. Use IC card (Suica/Pasmo).',
                maps_url = 'https://www.google.com/maps/dir/Haneda+Airport+Terminal+3,+Tokyo/Higashi-Shinjuku+Station',
                url = 'https://www.keikyu.co.jp/en/',
                sort_order = 1
            WHERE id = ?
        """, (haneda_combined[0],))

    # Insert Limousine Bus route if it doesn't exist yet
    cursor.execute("SELECT id FROM transport_route WHERE transport_type = 'Limousine Bus'")
    if not cursor.fetchone():
        cursor.execute("""
            INSERT INTO transport_route (route_from, route_to, transport_type, train_name,
                duration, jr_pass_covered, cost_if_not_covered, notes, day_id, sort_order, maps_url, url)
            VALUES (
                'Haneda Airport', 'Shinjuku', 'Limousine Bus', 'Airport Limousine Bus',
                '~60-85 min', 0, '~¥1,300',
                'Direct bus from Haneda to Shinjuku Bus Terminal. No transfers. Runs every 20-30 min. Then 10 min walk to hotel.',
                (SELECT id FROM day WHERE day_number = 2), 2,
                'https://www.google.com/maps/dir/Haneda+Airport+Terminal+3,+Tokyo/Shinjuku+Expressway+Bus+Terminal',
                'https://www.limousinebus.co.jp/en/'
            )
        """)

    # Enrich all routes with maps_url where missing — lookup by route_from/route_to (not ID)
    _route_data = [
        ('Tokyo', 'Odawara', 'https://www.google.com/maps/dir/Tokyo+Station/Odawara+Station', 'https://www.jreast.co.jp/multi/en/'),
        ('Tokyo', 'Nagoya', 'https://www.google.com/maps/dir/Tokyo+Station/Nagoya+Station', 'https://www.jreast.co.jp/multi/en/'),
        ('Nagoya', 'Takayama', 'https://www.google.com/maps/dir/Nagoya+Station/Takayama+Station', 'https://touristpass.jp/en/'),
        ('Takayama', 'Shirakawa-go', 'https://www.google.com/maps/dir/Takayama+Nohi+Bus+Center/Shirakawa-go+Bus+Terminal', None),
        ('Shirakawa-go', 'Kanazawa', 'https://www.google.com/maps/dir/Shirakawa-go+Bus+Terminal/Kanazawa+Station', None),
        ('Kanazawa', 'Tsuruga', 'https://www.google.com/maps/dir/Kanazawa+Station/Tsuruga+Station', None),
        ('Tsuruga', 'Kyoto', 'https://www.google.com/maps/dir/Tsuruga+Station/Kyoto+Station', None),
        ('Kyoto', 'Hiroshima', 'https://www.google.com/maps/dir/Kyoto+Station/Hiroshima+Station', None),
        ('Hiroshima', 'Miyajima', 'https://www.google.com/maps/dir/Miyajimaguchi+Station/Miyajima+Ferry+Terminal', 'https://www.jr-miyajimaferry.co.jp/en/'),
        ('Kyoto', 'Tokyo', 'https://www.google.com/maps/dir/Kyoto+Station/Tokyo+Station', None),
        ('Kyoto', 'Osaka', 'https://www.google.com/maps/dir/Kyoto+Station/Shin-Osaka+Station', None),
        ('Shinagawa', 'Haneda Airport', 'https://www.google.com/maps/dir/Shinagawa+Station/Haneda+Airport+Terminal+3', None),
    ]
    for route_from, route_to, maps, url in _route_data:
        cursor.execute("""
            SELECT id, maps_url FROM transport_route
            WHERE route_from LIKE ? AND route_to LIKE ?
        """, (f'%{route_from}%', f'%{route_to}%'))
        row = cursor.fetchone()
        if row and not row[1]:
            if url:
                cursor.execute("UPDATE transport_route SET maps_url = ?, url = ? WHERE id = ?",
                               (maps, url, row[0]))
            else:
                cursor.execute("UPDATE transport_route SET maps_url = ? WHERE id = ?",
                               (maps, row[0]))


def _migrate_route_groups(cursor, conn):
    """Set route_group on alternative routes for the same movement.

    Idempotent: only sets route_group where it's currently NULL.
    """
    # Day 2: Haneda Airport arrival — two alternative routes to Shinjuku area
    cursor.execute("""
        UPDATE transport_route SET route_group = 'haneda-to-shinjuku'
        WHERE route_from = 'Haneda Airport'
          AND route_group IS NULL
          AND day_id = (SELECT id FROM day WHERE day_number = 2)
    """)


def _migrate_activity_time_slots(cursor, conn):
    """Fix activity time_slots so they appear in the correct position relative to transport.

    Template flow: Checkout → Morning → Flights → Transport → Check-in → Afternoon → Evening
    Activities BEFORE transport need time_slot='morning'.
    Activities AFTER transport need time_slot='afternoon' or later.

    Idempotent: only changes activities that still have the wrong slot.
    """
    # Day 2: "Pick up Welcome Suica IC card" happens at airport BEFORE transport
    cursor.execute("""
        UPDATE activity SET time_slot = 'morning'
        WHERE title LIKE '%Welcome Suica%'
          AND time_slot = 'afternoon'
          AND day_id = (SELECT id FROM day WHERE day_number = 2)
    """)

    # Day 5: Post-arrival Takayama activities were incorrectly slotted as morning.
    # sort_order >= 4 are activities after the train ride (scenic description, check-in,
    # exploration, sake, crafts, Jinya). They should appear AFTER the transport section.
    cursor.execute("""
        UPDATE activity SET time_slot = 'afternoon'
        WHERE day_id = (SELECT id FROM day WHERE day_number = 5)
          AND time_slot = 'morning'
          AND sort_order >= 4 AND sort_order <= 9
    """)

    # Day 7: Shirakawa-go activities + Kanazawa Castle happen after bus legs.
    # Only checkout (sort_order 1) is truly pre-transport.
    cursor.execute("""
        UPDATE activity SET time_slot = 'afternoon'
        WHERE day_id = (SELECT id FROM day WHERE day_number = 7)
          AND time_slot = 'morning'
          AND sort_order >= 2
    """)

    # Day 14: Airport activities (sort_order >= 7) happen after transport to Haneda.
    # Morning activities (shopping, checkout, Keikyu) are pre-transport.
    cursor.execute("""
        UPDATE activity SET time_slot = 'afternoon'
        WHERE day_id = (SELECT id FROM day WHERE day_number = 14)
          AND time_slot = 'morning'
          AND sort_order >= 7
    """)
