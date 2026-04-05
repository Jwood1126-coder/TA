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
    _migrate_day_location_fixes(cursor, conn)
    _migrate_departure_day_data(cursor, conn)
    _migrate_itinerary_refinement(cursor, conn)
    _migrate_remove_daytrip_transport(cursor, conn)
    _migrate_checklist_simplify(cursor, conn)
    _migrate_production_ready(cursor, conn)
    _migrate_url_integrity(cursor, conn)
    _migrate_fix_eliminated_booking_status(cursor, conn)
    _migrate_cancel_kyotofish(cursor, conn)

    # --- Gmail sync tables ---
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gmail_sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            status TEXT DEFAULT 'running',
            emails_found INTEGER DEFAULT 0,
            changes_detected INTEGER DEFAULT 0,
            errors TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pending_gmail_change (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gmail_message_id TEXT NOT NULL,
            email_subject TEXT,
            email_from TEXT,
            email_date TEXT,
            change_type TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id INTEGER,
            description TEXT NOT NULL,
            proposed_data TEXT,
            current_data TEXT,
            status TEXT DEFAULT 'pending',
            detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TIMESTAMP,
            errors TEXT
        )
    """)

    # Add Opus analysis columns to pending_gmail_change
    for col, col_type in [
        ('consequence', 'TEXT'),
        ('confidence', 'TEXT'),
        ('opus_reasoning', 'TEXT'),
    ]:
        try:
            cursor.execute(f"ALTER TABLE pending_gmail_change ADD COLUMN {col} {col_type}")
        except Exception:
            pass  # already exists

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

    # Day 14: Tokyo-specific activities (sort_order 1-4: Tsukiji, shopping, Don Quijote, Uniqlo)
    # happen AFTER Osaka→Tokyo Shinkansen, so they must be afternoon.
    # Checkout + Keikyu (sort_order 5-6) stay as morning (pre-transport in Osaka).
    # Airport activities (sort_order >= 7) also afternoon.
    cursor.execute("""
        UPDATE activity SET time_slot = 'afternoon'
        WHERE day_id = (SELECT id FROM day WHERE day_number = 14)
          AND time_slot = 'morning'
          AND (sort_order <= 4 OR sort_order >= 7)
    """)


def _migrate_day_location_fixes(cursor, conn):
    """Fix day-location assignments that don't match the accommodation chain.

    Idempotent: only changes days that still have the wrong location.
    """
    # Day 12 (Apr 16) is Osaka check-in day, not Kyoto.
    # Activities on this day are Osaka Castle, Kuromon Market, Shinsekai.
    cursor.execute("""
        UPDATE day SET location_id = (SELECT id FROM location WHERE name = 'Osaka')
        WHERE day_number = 12
          AND location_id = (SELECT id FROM location WHERE name = 'Kyoto')
    """)

    # Day 12 buffer day activities had NULL time_slot — set to afternoon (post-checkin)
    cursor.execute("""
        UPDATE activity SET time_slot = 'afternoon'
        WHERE day_id = (SELECT id FROM day WHERE day_number = 12)
          AND (time_slot IS NULL OR time_slot = '')
    """)

    # Day 13 "Revisit a favorite Tokyo spot" is geographically impossible from Osaka
    cursor.execute("""
        UPDATE activity SET is_eliminated = 1
        WHERE title LIKE '%Revisit a favorite Tokyo spot%'
          AND is_eliminated = 0
          AND day_id = (SELECT id FROM day WHERE day_number = 13)
    """)

    # Byodo-in Temple is in Uji (Kyoto), not Osaka — move from Day 12 to Day 10 afternoon
    cursor.execute("""
        SELECT id FROM activity
        WHERE title LIKE '%Byodo-in%'
          AND day_id = (SELECT id FROM day WHERE day_number = 12)
    """)
    if cursor.fetchone():
        # Make room: bump Day 10 activities at sort_order >= 8
        cursor.execute("""
            UPDATE activity SET sort_order = sort_order + 1
            WHERE day_id = (SELECT id FROM day WHERE day_number = 10)
              AND sort_order >= 8
        """)
        cursor.execute("""
            UPDATE activity SET
                day_id = (SELECT id FROM day WHERE day_number = 10),
                time_slot = 'afternoon',
                sort_order = 8
            WHERE title LIKE '%Byodo-in%'
              AND day_id = (SELECT id FROM day WHERE day_number = 12)
        """)


def _migrate_departure_day_data(cursor, conn):
    """Add missing Osaka→Tokyo Shinkansen for departure day (Day 14).

    The return flight departs HND (Tokyo) but the last accommodation is Osaka.
    The traveler needs a Shinkansen from Shin-Osaka to Tokyo Station.

    Idempotent: checks if route already exists.
    """
    cursor.execute("""
        SELECT id FROM transport_route
        WHERE route_from = 'Shin-Osaka' AND route_to = 'Tokyo'
    """)
    if not cursor.fetchone():
        cursor.execute("""
            INSERT INTO transport_route (route_from, route_to, transport_type, train_name,
                duration, jr_pass_covered, cost_if_not_covered, notes, day_id, sort_order, maps_url)
            VALUES (
                'Shin-Osaka', 'Tokyo', 'Shinkansen', 'Nozomi / Hikari',
                '~2h 15min', 1, '~¥13,870',
                'Take early Shinkansen from Shin-Osaka to Tokyo Station. Hikari is JR Pass covered; Nozomi requires separate ticket but is 10 min faster. Get ekiben at station!',
                (SELECT id FROM day WHERE day_number = 14), 1,
                'https://www.google.com/maps/dir/Shin-Osaka+Station/Tokyo+Station'
            )
        """)

    # Ensure Shinagawa→Haneda sorts after the Shinkansen arrival
    cursor.execute("""
        UPDATE transport_route SET sort_order = 2
        WHERE route_from = 'Shinagawa' AND route_to LIKE '%Haneda%'
          AND sort_order != 2
    """)


def _migrate_itinerary_refinement(cursor, conn):
    """Travel-agent schedule refinement: move activities between days, fix anchors/optionals,
    fold description items into parents, fix TeamLab and Byodo-in placement.

    Idempotent: each operation checks current state before changing.
    Uses content-based lookups (NOT hardcoded IDs).
    """
    # --- 1. TeamLab: update to Biovortex Kyoto and move to Day 10 evening ---
    cursor.execute("""
        SELECT id, day_id FROM activity
        WHERE title LIKE '%TeamLab%Planets%'
          AND is_eliminated = 0
    """)
    tl = cursor.fetchone()
    if tl:
        day10_id = _day_id(cursor, 10)
        cursor.execute("""
            UPDATE activity SET
                title = 'teamLab Biovortex Kyoto',
                description = 'Japan''s largest teamLab museum — 10,000+ sqm across four floors with 50+ immersive artworks. Opened Oct 2025. Walk barefoot through water, light, and living ecosystems. 7-minute walk from Kyoto Station. Book timed entry in advance (¥3,600–4,200). Open until 9:30 PM in April.',
                notes = 'Book at teamlab.art/e/kyoto/ — timed entry, sells out. Allow 2.5–3 hours. Extended April hours: 9 AM–9:30 PM.',
                day_id = ?,
                time_slot = 'evening',
                sort_order = 8,
                book_ahead = 1,
                is_optional = 0
            WHERE id = ?
        """, (day10_id, tl[0]))

    # --- 2. Byodo-in: move from Day 10 to Day 9 morning (south Kyoto flow from Fushimi Inari) ---
    day9_id = _day_id(cursor, 9)
    day10_id = _day_id(cursor, 10)
    cursor.execute("""
        SELECT id FROM activity
        WHERE title LIKE '%Byodo-in%'
          AND day_id = ?
    """, (day10_id,))
    byodo = cursor.fetchone()
    if byodo:
        # Bump Day 9 sort_order >= 3 to make room
        cursor.execute("""
            UPDATE activity SET sort_order = sort_order + 1
            WHERE day_id = ? AND sort_order >= 3
        """, (day9_id,))
        cursor.execute("""
            UPDATE activity SET
                day_id = ?,
                time_slot = 'morning',
                sort_order = 3
            WHERE id = ?
        """, (day9_id, byodo[0]))

    # --- 3. Move Day 5 overflow to Day 6 (Sanmachi, sake, crafts, Jinya) ---
    day5_id = _day_id(cursor, 5)
    day6_id = _day_id(cursor, 6)

    _move_activity(cursor, day5_id, day6_id, '%Sanmachi Suji%', 'afternoon', 10, is_optional=0)
    _move_activity(cursor, day5_id, day6_id, '%Sake brewer%', 'afternoon', 11, is_optional=1)
    _move_activity(cursor, day5_id, day6_id, '%Craft shops%', 'afternoon', 12, is_optional=1)
    _move_activity(cursor, day5_id, day6_id, '%Jinya%', 'afternoon', 13, is_optional=1)

    # --- 4. Promote anchors to required ---
    _set_optional(cursor, '%Check into Sotetsu%', 2, False)
    _set_optional(cursor, '%Check out%Sotetsu%', 5, False)
    _set_optional(cursor, '%Check into TAKANOYU%', 5, False)
    _set_optional(cursor, '%Kinkaku-ji%', 10, False)
    _set_optional(cursor, '%Peace Memorial%', 11, False)
    _set_optional(cursor, '%Miyagawa%Morning%Market%', 6, False)
    _set_optional(cursor, '%Hida Folk%', 6, False)
    _set_optional(cursor, '%izakaya%', 6, False)
    _set_optional(cursor, '%Kiyomizu-dera%', 9, False)
    _set_optional(cursor, '%Hanamikoji%', 9, False)

    # --- 5. Demote to optional ---
    for pattern, day_num in [
        ('%Evening walk%explore%', 2),
        ('%Late-night ramen%', 2),
        ('%PRIORITY%SLEEP%', 2),
        ('%scenic train%', 5),
        ('%Hida beef%', 5),
        ('%Keihan Line%', 9),
        ('%Be respectful%', 9),
        ('%Kamo River%night%', 9),
        ('%Shinsekai%', 12),
        ('%Kimono rental%', 12),
        ('%Sit on%RIGHT%', 13),
        ('%Confirmed open%', 13),
        ('%Sushi omakase%', 13),
        ('%Tsukiji%', 14),
        ('%Don Quijote%', 14),
        ('%Uniqlo%', 14),
        ('%Tenryu-ji%', 10),
        ('%Togetsukyo%', 10),
    ]:
        _set_optional(cursor, pattern, day_num, True)

    # Demote Nishiki Market on Day 10
    _set_optional(cursor, '%Nishiki Market%', 10, True)

    # --- 6. Fold description items into parents then delete ---
    # Each tuple: (child_pattern, parent_pattern, day_num, text_to_append)
    _folds = [
        # Day 3
        ('%oldest temple%628%', '%Senso-ji%', 3, "Tokyo's oldest temple (founded 628 AD)."),
        ('%Nakamise-dori%250m%', '%Senso-ji%', 3, 'Nakamise-dori: 250m of traditional shops selling snacks, souvenirs, crafts.'),
        ('%Massive Shinto%', '%Meiji Shrine%', 3, 'Massive Shinto shrine hidden in a 170-acre forest in the middle of Tokyo.'),
        ('%Cover charge%300%', '%Golden Gai%', 3, 'Cover charge: ¥300–1,000. Drinks: ¥500–800. Just bar-hop and explore.'),
        ('%Grab a stool%', '%Omoide%', 3, 'Grab a stool elbow-to-elbow with salarymen.'),
        # Day 5
        ('%scenic train ride%', '%Hida%Express%', 5, "One of Japan's most scenic train rides — mountain gorges, rivers, tiny villages."),
        # Day 9
        ('%2 km path%cherry%', '%Philosopher%Path%', 9, '2 km path lined with cherry trees and small temples.'),
        # Day 10
        ('%Entry%500%best light%', '%Kinkaku-ji%', 10, 'Entry: ¥500. Get there ~9 AM for best light and fewer people.'),
        ('%dashimaki tamago%', '%Nishiki%', 10, 'Try: dashimaki tamago, fresh mochi, matcha soft serve, Kyoto pickles, grilled seafood.'),
        ('%souvenir shopping%tea%', '%Nishiki%', 10, 'Good for souvenir shopping — packaged tea, spices, ceramics.'),
        # Day 11
        ('%Deeply moving%museum%', '%Peace Memorial%', 11, 'Deeply moving — individual human stories, letters, belongings, shadows burned into stone.'),
        ('%Free to walk%park%museum%200%', '%Peace Memorial%', 11, 'Free to walk the park; museum entry ¥200.'),
        ('%Okonomimura%stalls%', '%okonomiyaki%', 11, 'Okonomimura building near Peace Park has dozens of stalls — pick any busy one.'),
        ('%high tide%float%low tide%', '%Torii Gate%', 11, 'At high tide it appears to float; at low tide you can walk to it.'),
        ('%deer roam%', '%Torii Gate%', 11, 'Friendly wild deer roam the island.'),
        ('%momiji manju%', '%Torii Gate%', 11, 'Try momiji manju — maple leaf-shaped cakes with various fillings.'),
        # Day 14
        ('%Tax-free%passport%', '%omiyage%terminal%', 14, 'Tax-free shopping available with passport.'),
    ]
    for child_pat, parent_pat, day_num, text in _folds:
        day_id = _day_id(cursor, day_num)
        cursor.execute("SELECT id FROM activity WHERE title LIKE ? AND day_id = ?",
                        (child_pat, day_id))
        child = cursor.fetchone()
        cursor.execute("SELECT id, description FROM activity WHERE title LIKE ? AND day_id = ?",
                        (parent_pat, day_id))
        parent = cursor.fetchone()
        if child and parent:
            existing = parent[1] or ''
            new_desc = (existing + ' ' + text).strip() if existing else text
            cursor.execute("UPDATE activity SET description = ? WHERE id = ?",
                            (new_desc, parent[0]))
            cursor.execute("DELETE FROM activity WHERE id = ?", (child[0],))

    # --- 7. Fold Day 2 dinner alternatives into one ---
    day2_id = _day_id(cursor, 2)
    cursor.execute("SELECT id, description FROM activity WHERE title LIKE '%Light dinner%' AND day_id = ?",
                    (day2_id,))
    dinner = cursor.fetchone()
    if dinner:
        cursor.execute("SELECT id FROM activity WHERE title LIKE '%Walk-up ramen%' AND day_id = ?",
                        (day2_id,))
        ramen = cursor.fetchone()
        cursor.execute("SELECT id FROM activity WHERE title LIKE '%onigiri%bento%' AND day_id = ?",
                        (day2_id,))
        konbini = cursor.fetchone()
        if ramen or konbini:
            existing = dinner[1] or ''
            new_desc = existing + ' Options: Walk-up ramen shop (¥800–1,200). Or grab onigiri + bento from 7-Eleven/FamilyMart (genuinely delicious, ¥500 total).'
            cursor.execute("UPDATE activity SET description = ? WHERE id = ?",
                            (new_desc.strip(), dinner[0]))
            if ramen:
                cursor.execute("DELETE FROM activity WHERE id = ?", (ramen[0],))
            if konbini:
                cursor.execute("DELETE FROM activity WHERE id = ?", (konbini[0],))

    # --- 8. Consolidate Day 14 flight items into one ---
    day14_id = _day_id(cursor, 14)
    # Check if already consolidated
    cursor.execute("SELECT id FROM activity WHERE title LIKE '%Flights home%' AND day_id = ?",
                    (day14_id,))
    if not cursor.fetchone():
        cursor.execute("SELECT id FROM activity WHERE title LIKE '%UA876%' AND day_id = ?",
                        (day14_id,))
        ua876 = cursor.fetchone()
        if ua876:
            cursor.execute("""
                UPDATE activity SET
                    title = 'Flights home: UA876 → SFO → UA1470 → CLE',
                    description = 'UA876 HND→SFO, layover, UA1470 SFO→CLE. Home by 10:13 PM.'
                WHERE id = ?
            """, (ua876[0],))
            for pat in ['%Layover%', '%UA1470%', '%HOME BY%']:
                cursor.execute("SELECT id FROM activity WHERE title LIKE ? AND day_id = ?",
                                (pat, day14_id))
                row = cursor.fetchone()
                if row:
                    cursor.execute("DELETE FROM activity WHERE id = ?", (row[0],))
        else:
            # Flight items already deleted but consolidated item missing — create it
            cursor.execute("""
                INSERT INTO activity (day_id, title, description, time_slot, sort_order,
                    is_optional, is_substitute, is_confirmed, is_eliminated, book_ahead)
                VALUES (?, 'Flights home: UA876 → SFO → UA1470 → CLE',
                    'UA876 HND→SFO, layover, UA1470 SFO→CLE. Home by 10:13 PM.',
                    'afternoon', 9, 0, 0, 0, 0, 0)
            """, (day14_id,))

    # --- 9. Delete pure vibe/status notes ---
    for pat, day_num in [
        ('%most memorable night%', 5),
        ('%Confirmed open%', 13),
    ]:
        day_id = _day_id(cursor, day_num)
        cursor.execute("DELETE FROM activity WHERE title LIKE ? AND day_id = ?",
                        (pat, day_id))

    # --- 10. Move Day 13 logistics to Day 12 (checkout/checkin match reservation dates) ---
    day12_id = _day_id(cursor, 12)
    day13_id = _day_id(cursor, 13)

    # Kyotofish checkout → Day 12 morning
    cursor.execute("""
        SELECT id FROM activity WHERE title LIKE '%Check out%Kyotofish%' AND day_id = ?
    """, (day13_id,))
    row = cursor.fetchone()
    if row:
        cursor.execute("""
            UPDATE activity SET day_id = ?, time_slot = 'morning', sort_order = 0, is_optional = 0
            WHERE id = ?
        """, (day12_id, row[0]))

    # Leben check-in → Day 12 morning
    cursor.execute("""
        SELECT id FROM activity WHERE title LIKE '%Check into Hotel%Leben%' AND day_id = ?
    """, (day13_id,))
    row = cursor.fetchone()
    if row:
        cursor.execute("""
            UPDATE activity SET day_id = ?, time_slot = 'morning', sort_order = 1, is_optional = 0
            WHERE id = ?
        """, (day12_id, row[0]))

    # Train tip + ekiben → Day 12
    cursor.execute("""
        SELECT id FROM activity WHERE title LIKE '%RIGHT side%train%' AND day_id = ?
    """, (day13_id,))
    row = cursor.fetchone()
    if row:
        cursor.execute("""
            UPDATE activity SET day_id = ?, time_slot = 'morning', sort_order = 2, is_optional = 1
            WHERE id = ?
        """, (day12_id, row[0]))

    cursor.execute("""
        SELECT id FROM activity WHERE title LIKE '%ekiben%' AND day_id = ?
    """, (day13_id,))
    row = cursor.fetchone()
    if row:
        cursor.execute("""
            UPDATE activity SET day_id = ?, time_slot = 'morning', sort_order = 3, is_optional = 1
            WHERE id = ?
        """, (day12_id, row[0]))

    # --- 11. Update day titles ---
    cursor.execute("""
        UPDATE day SET title = 'KYOTO → OSAKA BUFFER DAY'
        WHERE day_number = 12 AND title NOT LIKE '%BUFFER%'
    """)
    cursor.execute("""
        UPDATE day SET title = 'LAST FULL DAY — OSAKA'
        WHERE day_number = 13 AND title LIKE '%KYOTO%OSAKA%'
    """)

    # --- 12. Update Day 13 morning activity for Osaka context ---
    cursor.execute("""
        UPDATE activity SET
            title = 'Morning: Sleep in, explore Osaka at your own pace',
            description = 'Last full day — no agenda required. Revisit a favorite spot, wander Namba, or just rest.'
        WHERE title LIKE '%last Kyoto explorations%'
          AND day_id = ?
    """, (day13_id,))

    # --- 13. Add Dotonbori to Day 12 if not present ---
    cursor.execute("SELECT id FROM activity WHERE title LIKE '%Dotonbori%' AND day_id = ?",
                    (day12_id,))
    if not cursor.fetchone():
        cursor.execute("""
            INSERT INTO activity (day_id, title, description, time_slot, sort_order,
                is_optional, is_substitute, is_confirmed, is_eliminated, book_ahead)
            VALUES (?, 'Dotonbori at night',
                'Osaka''s signature nighttime experience — neon lights, street food, canal reflections. Try takoyaki and okonomiyaki. The Glico Running Man sign is here.',
                'evening', 20, 1, 0, 0, 0, 0)
        """, (day12_id,))

    # --- 14. Fold Hida beef + futon into parents on Day 5 ---
    cursor.execute("""
        SELECT id, description FROM activity WHERE title LIKE '%kaiseki dinner%' AND day_id = ?
    """, (day5_id,))
    kaiseki = cursor.fetchone()
    if kaiseki:
        cursor.execute("SELECT id FROM activity WHERE title LIKE '%Hida beef%' AND day_id = ? AND title NOT LIKE '%sushi%'",
                        (day5_id,))
        hida = cursor.fetchone()
        if hida:
            existing = kaiseki[1] or ''
            cursor.execute("UPDATE activity SET description = ? WHERE id = ?",
                            ((existing + " Hida beef (the region's famous wagyu) will be featured.").strip(), kaiseki[0]))
            cursor.execute("DELETE FROM activity WHERE id = ?", (hida[0],))

    cursor.execute("""
        SELECT id, description FROM activity WHERE title LIKE '%onsen%bath%TAKANOYU%' AND day_id = ?
    """, (day5_id,))
    onsen = cursor.fetchone()
    if onsen:
        cursor.execute("SELECT id FROM activity WHERE title LIKE '%futon%tatami%' AND day_id = ?",
                        (day5_id,))
        futon = cursor.fetchone()
        if futon:
            existing = onsen[1] or ''
            cursor.execute("UPDATE activity SET description = ? WHERE id = ?",
                            ((existing + ' Sleep on futon laid out on tatami mats by the staff while you were at dinner.').strip(), onsen[0]))
            cursor.execute("DELETE FROM activity WHERE id = ?", (futon[0],))

    # Fold Day 9 Gion geisha timing note
    cursor.execute("""
        SELECT id, description FROM activity WHERE title LIKE '%Hanamikoji%' AND day_id = ?
    """, (day9_id,))
    gion = cursor.fetchone()
    if gion:
        cursor.execute("SELECT id FROM activity WHERE title LIKE '%5:30%7:00%' AND day_id = ?",
                        (day9_id,))
        timing = cursor.fetchone()
        if timing:
            existing = gion[1] or ''
            cursor.execute("UPDATE activity SET description = ? WHERE id = ?",
                            ((existing + ' Best time: 5:30–7:00 PM as geiko and maiko walk to engagements.').strip(), gion[0]))
            cursor.execute("DELETE FROM activity WHERE id = ?", (timing[0],))

    # Day 14: checkout to required, morning slot
    cursor.execute("""
        UPDATE activity SET is_optional = 0, time_slot = 'morning', sort_order = 1
        WHERE title LIKE '%check out%grab bags%'
          AND day_id = ?
    """, (day14_id,))

    # Day 12: Kyotofish checkout + Leben check-in required
    cursor.execute("""
        UPDATE activity SET is_optional = 0
        WHERE title LIKE '%Check out%Kyotofish%' AND day_id = ?
    """, (day12_id,))
    cursor.execute("""
        UPDATE activity SET is_optional = 0
        WHERE title LIKE '%Check into Hotel%Leben%' AND day_id = ?
    """, (day12_id,))

    conn.commit()


def _day_id(cursor, day_number):
    """Get day table ID for a given day number."""
    cursor.execute("SELECT id FROM day WHERE day_number = ?", (day_number,))
    row = cursor.fetchone()
    return row[0] if row else None


def _move_activity(cursor, from_day_id, to_day_id, title_pattern, time_slot, sort_order, is_optional=None):
    """Move an activity between days by content match. Idempotent."""
    cursor.execute("SELECT id FROM activity WHERE title LIKE ? AND day_id = ?",
                    (title_pattern, from_day_id))
    row = cursor.fetchone()
    if row:
        updates = "day_id = ?, time_slot = ?, sort_order = ?"
        params = [to_day_id, time_slot, sort_order]
        if is_optional is not None:
            updates += ", is_optional = ?"
            params.append(is_optional)
        params.append(row[0])
        cursor.execute(f"UPDATE activity SET {updates} WHERE id = ?", params)


def _set_optional(cursor, title_pattern, day_number, optional):
    """Set is_optional on an activity by content match. Idempotent."""
    day_id = _day_id(cursor, day_number)
    if day_id:
        cursor.execute("""
            UPDATE activity SET is_optional = ?
            WHERE title LIKE ? AND day_id = ? AND is_optional != ?
        """, (1 if optional else 0, title_pattern, day_id, 1 if optional else 0))


def _migrate_remove_daytrip_transport(cursor, conn):
    """Remove day-trip transport routes (Hakone, Hiroshima, Miyajima).

    Inter-city day trips use Google Maps links + description notes instead of
    dedicated transport cards. Transport cards are reserved for reservation-to-
    reservation moves only.

    One-shot: uses sentinel to run only once. Previously ran unconditionally
    on every boot, which would delete user-added routes matching these patterns.
    """
    # Sentinel check
    cursor.execute("SELECT notes FROM trip WHERE id = 1")
    row = cursor.fetchone()
    if row and row[0] and '__daytrip_transport_removed_v1' in row[0]:
        return

    # --- 1. Delete Hakone day-trip route (Tokyo → Odawara) ---
    cursor.execute("""
        DELETE FROM transport_route
        WHERE route_from LIKE '%Tokyo%' AND route_to LIKE '%Odawara%'
    """)

    # --- 2. Delete Hiroshima day-trip routes (Kyoto → Hiroshima, Hiroshima → Miyajima) ---
    cursor.execute("""
        DELETE FROM transport_route
        WHERE route_from LIKE '%Kyoto%' AND route_to LIKE '%Hiroshima%'
    """)
    cursor.execute("""
        DELETE FROM transport_route
        WHERE route_from LIKE '%Hiroshima%' AND route_to LIKE '%Miyajima%'
    """)

    # --- 3. Fold transport info into Hakone Loop activity description ---
    day4_id = _day_id(cursor, 4)
    if day4_id:
        cursor.execute("""
            SELECT id, description FROM activity
            WHERE title LIKE '%Hakone Loop%' AND day_id = ?
        """, (day4_id,))
        row = cursor.fetchone()
        if row and 'Shinkansen' not in (row[1] or ''):
            cursor.execute("""
                UPDATE activity SET
                    description = 'Shinkansen from Tokyo Station to Odawara (~35 min, JR Pass covered), then Hakone Free Pass loop circuit:',
                    maps_url = 'https://www.google.com/maps/dir/Tokyo+Station/Odawara+Station'
                WHERE id = ?
            """, (row[0],))

    # --- 4. Fold transport info into Hiroshima activity description ---
    day11_id = _day_id(cursor, 11)
    if day11_id:
        cursor.execute("""
            SELECT id, description FROM activity
            WHERE title LIKE '%Hiroshima Peace Memorial%' AND day_id = ?
        """, (day11_id,))
        row = cursor.fetchone()
        if row and 'Shinkansen' not in (row[1] or ''):
            cursor.execute("""
                UPDATE activity SET
                    description = 'Shinkansen from Kyoto Station to Hiroshima (~1h 45min, JR Pass covered). ' || description,
                    maps_url = 'https://www.google.com/maps/dir/Kyoto+Station/Hiroshima+Station'
                WHERE id = ?
            """, (row[0],))

    # --- 5. Fold ferry info into Miyajima torii gate activity ---
        cursor.execute("""
            SELECT id, description FROM activity
            WHERE title LIKE '%Itsukushima Torii%' AND day_id = ?
        """, (day11_id,))
        row = cursor.fetchone()
        if row and 'JR Ferry' not in (row[1] or ''):
            cursor.execute("""
                UPDATE activity SET
                    description = 'JR Ferry from Miyajimaguchi to Miyajima (~10 min, JR Pass covered). ' || description,
                    maps_url = 'https://www.google.com/maps/dir/Miyajimaguchi+Station/Miyajima+Ferry+Terminal'
                WHERE id = ?
            """, (row[0],))

    # Set sentinel so this never runs again
    cursor.execute("""
        UPDATE trip SET notes = COALESCE(notes, '') || ' __daytrip_transport_removed_v1'
        WHERE id = 1 AND (notes IS NULL OR notes NOT LIKE '%__daytrip_transport_removed_v1%')
    """)

    conn.commit()


def _migrate_checklist_simplify(cursor, conn):
    """Simplify checklist: consolidate categories, remove stale items, add missing items.

    - Merge pre_departure_* categories into 'preparation'
    - Remove "Buy remaining miles" (stale — flights confirmed)
    - Convert Visit Japan Web from decision to task
    - Fix priority values (were set to category names)
    - Add missing practical items (cash, offline maps, confirmations)

    One-shot: uses sentinel to run only once.
    """
    # Sentinel check
    cursor.execute("SELECT notes FROM trip WHERE id = 1")
    row = cursor.fetchone()
    if row and row[0] and '__checklist_simplified_v1' in row[0]:
        return

    # --- 1. Delete stale "Buy remaining miles" ---
    cursor.execute("DELETE FROM checklist_item WHERE title LIKE '%remaining miles%'")

    # --- 2. Consolidate categories to 'preparation' ---
    cursor.execute("""
        UPDATE checklist_item SET category = 'preparation'
        WHERE category LIKE 'pre_departure_%'
    """)

    # --- 3. Convert Visit Japan Web from decision to task ---
    cursor.execute("SELECT id FROM checklist_item WHERE title LIKE '%Visit Japan Web%'")
    vjw = cursor.fetchone()
    if vjw:
        cursor.execute("""
            UPDATE checklist_item SET
                item_type = 'task',
                url = 'https://www.vjw.digital.go.jp/',
                description = 'Pre-fill customs & immigration forms online before landing. Skip paper forms — QR code at immigration.'
            WHERE id = ?
        """, (vjw[0],))
        cursor.execute("DELETE FROM checklist_option WHERE checklist_item_id = ?", (vjw[0],))

    # --- 4. Fix priority values ---
    cursor.execute("""
        UPDATE checklist_item SET priority = 'high'
        WHERE title LIKE '%Passport%' OR title LIKE '%JR Pass%'
           OR title LIKE '%eSIM%' OR title LIKE '%Visit Japan Web%'
    """)
    cursor.execute("""
        UPDATE checklist_item SET priority = 'medium'
        WHERE priority NOT IN ('high', 'medium', 'low')
    """)

    # --- 5. Add missing practical items ---
    new_items = [
        ('Get Japanese yen before departure (¥30-50k cash)',
         'Japan is still cash-heavy for small restaurants, shrines, vending machines, and rural areas.',
         'high'),
        ('Download offline Google Maps for Kansai + Chubu regions',
         'Essential backup when underground or in rural areas with spotty signal.',
         'medium'),
        ('Save/print all accommodation confirmation emails',
         'Have backup confirmations accessible offline in case of connectivity issues.',
         'medium'),
    ]
    for title, desc, pri in new_items:
        cursor.execute("SELECT id FROM checklist_item WHERE title LIKE ?",
                       ('%' + title[:30] + '%',))
        if not cursor.fetchone():
            cursor.execute("SELECT MAX(sort_order) FROM checklist_item WHERE category = 'preparation'")
            max_order = (cursor.fetchone()[0] or 0) + 1
            cursor.execute("""
                INSERT INTO checklist_item
                    (category, title, description, item_type, status, priority, sort_order, is_completed)
                VALUES ('preparation', ?, ?, 'task', 'pending', ?, ?, 0)
            """, (title, desc, pri, max_order))

    # --- 6. Add packing item ---
    cursor.execute("SELECT id FROM checklist_item WHERE title LIKE '%Cash belt%'")
    if not cursor.fetchone():
        cursor.execute("SELECT MAX(sort_order) FROM checklist_item WHERE category = 'packing_essential'")
        max_order = (cursor.fetchone()[0] or 0) + 1
        cursor.execute("""
            INSERT INTO checklist_item
                (category, title, description, item_type, status, priority, sort_order, is_completed)
            VALUES ('packing_essential', 'Cash belt or hidden pouch',
                    'For carrying yen safely — pickpocketing is rare but losing cash hurts.',
                    'task', 'pending', 'medium', ?, 0)
        """, (max_order,))

    # --- 7. Re-sort preparation: high priority first ---
    cursor.execute("""
        SELECT id FROM checklist_item
        WHERE category = 'preparation'
        ORDER BY
            CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
            sort_order
    """)
    rows = cursor.fetchall()
    for i, (item_id,) in enumerate(rows):
        cursor.execute("UPDATE checklist_item SET sort_order = ? WHERE id = ?", (i + 1, item_id))

    # Set sentinel so this never runs again
    cursor.execute("""
        UPDATE trip SET notes = COALESCE(notes, '') || ' __checklist_simplified_v1'
        WHERE id = 1 AND (notes IS NULL OR notes NOT LIKE '%__checklist_simplified_v1%')
    """)

    conn.commit()


def _migrate_production_ready(cursor, conn):
    """Production-ready itinerary cleanup: fix trust-breaking errors, eliminate stale
    alternates, clean up note-like activities, add categories and maps.

    Idempotent: all operations check current state before mutating.
    Uses content-based lookups (NOT hardcoded IDs).
    """

    # ---- PHASE 1: Trust-breaking data errors ----

    # 1a. Move Kyoto→Osaka transport from Day 13 to Day 12
    day12_id = _day_id(cursor, 12)
    day13_id = _day_id(cursor, 13)
    if day12_id and day13_id:
        cursor.execute("""
            UPDATE transport_route SET day_id = ?
            WHERE route_from LIKE '%Kyoto%' AND route_to LIKE '%Osaka%' AND day_id = ?
        """, (day12_id, day13_id))

    # 1b. Fix Leben check-in title (remove wrong night count)
    if day12_id:
        cursor.execute("""
            UPDATE activity SET title = 'Check into Hotel The Leben Osaka'
            WHERE title LIKE '%Check into Hotel%Leben%1 night%' AND day_id = ?
        """, (day12_id,))

    # 1c-d. Delete stale Shinkansen tips from Day 12 (Mt. Fuji tip + ekiben — wrong for 15-min Kyoto→Osaka)
    if day12_id:
        cursor.execute("DELETE FROM activity WHERE title LIKE '%Sit on the RIGHT side%' AND day_id = ?", (day12_id,))
        cursor.execute("DELETE FROM activity WHERE title LIKE '%Buy an ekiben%' AND day_id = ?", (day12_id,))

    # 1e. Fix Day 14 logistics flow
    day14_id = _day_id(cursor, 14)
    if day14_id:
        # Fix checkout title
        cursor.execute("""
            UPDATE activity SET
                title = 'Check out of Hotel The Leben Osaka',
                description = 'Early checkout. Store luggage at hotel or use station lockers if doing optional Tokyo time.',
                time_slot = 'morning', sort_order = 1
            WHERE title LIKE '%Return to hotel%check out%' AND day_id = ?
        """, (day14_id,))

        # Fix Keikyu with flight time
        cursor.execute("""
            UPDATE activity SET
                title = 'Keikyu Line Shinagawa → Haneda Terminal 3',
                description = '~15 min, ~¥500. Allow 2+ hours before departure (flight UA876 departs 3:50 PM).'
            WHERE title LIKE '%Keikyu Line%' AND day_id = ?
        """, (day14_id,))

        # Fix Haneda with arrival time
        cursor.execute("""
            UPDATE activity SET
                title = 'Haneda Airport — arrive by 1:50 PM',
                description = 'International terminal (Terminal 3). Check in, immigration, duty-free shopping.'
            WHERE title LIKE '%Haneda Airport%' AND day_id = ? AND title NOT LIKE '%omiyage%'
        """, (day14_id,))

        # Fix flights with real times
        cursor.execute("""
            UPDATE activity SET
                title = 'UA876 HND→SFO (departs 3:50 PM) → UA1470 SFO→CLE',
                description = 'Arrive SFO 9:35 AM same day. Layover. UA1470 departs 2:20 PM, arrive CLE 10:13 PM.'
            WHERE title LIKE '%Flights home%' AND day_id = ?
        """, (day14_id,))
        # Also match if already partially updated
        cursor.execute("""
            UPDATE activity SET
                description = 'Arrive SFO 9:35 AM same day. Layover. UA1470 departs 2:20 PM, arrive CLE 10:13 PM.'
            WHERE title LIKE '%UA876%' AND day_id = ? AND description LIKE '%UA876 HND%SFO%layover%'
        """, (day14_id,))

        # Fix Tsukiji conditional
        cursor.execute("""
            UPDATE activity SET
                description = 'Only if taking early Shinkansen from Osaka (arrive Tokyo ~9 AM). Farewell sushi/seafood.'
            WHERE title LIKE '%Tsukiji%' AND day_id = ?
        """, (day14_id,))

        # Fix omiyage
        cursor.execute("""
            UPDATE activity SET
                title = 'Airport omiyage shops',
                description = 'Haneda Terminal 3 has excellent souvenir shops — Tokyo Banana, Royce chocolate, Kit Kat flavors, wagashi. Tax-free with passport.'
            WHERE title LIKE '%omiyage%' AND day_id = ?
        """, (day14_id,))

    # 1f. Fix Senso-ji geography (not near hotel — hotel is in Higashi-Shinjuku)
    day3_id = _day_id(cursor, 3)
    if day3_id:
        cursor.execute("""
            UPDATE activity SET
                description = 'Tokyo''s oldest temple (founded 628 AD). Nakamise-dori: 250m of traditional shops leading to the temple. ~30 min by subway from Higashi-Shinjuku (Toei Oedo to Asakusa).'
            WHERE title LIKE '%Senso-ji%' AND day_id = ? AND description LIKE '%right near%'
        """, (day3_id,))

    # 1g. Fix Day 2 evening walk neighborhood
    day2_id = _day_id(cursor, 2)
    if day2_id:
        cursor.execute("""
            UPDATE activity SET
                title = 'Evening walk around Higashi-Shinjuku',
                description = 'Explore the neighborhood around the hotel — Kabukicho, Shinjuku Gyoen area.'
            WHERE title LIKE '%Evening walk%explore%' AND day_id = ?
        """, (day2_id,))

        # Fix Senso-ji night walk distance
        cursor.execute("""
            UPDATE activity SET
                title = 'Senso-ji Temple at night',
                description = 'Beautifully illuminated, almost empty, completely different atmosphere than daytime. ~30 min subway from hotel (Toei Oedo to Asakusa). Only if you have real energy left.'
            WHERE title LIKE '%Senso-ji%night%' AND day_id = ?
        """, (day2_id,))

        # Fix late-night ramen
        cursor.execute("""
            UPDATE activity SET
                description = 'Plenty of ramen shops near Shinjuku stay open late. Budget ~¥900-1,200.'
            WHERE title LIKE '%Late-night ramen%' AND day_id = ?
        """, (day2_id,))

    conn.commit()

    # ---- PHASE 2: Eliminate stale alternates ----

    day4_id = _day_id(cursor, 4)
    day7_id = _day_id(cursor, 7)
    day11_id = _day_id(cursor, 11)

    if day4_id:
        cursor.execute("UPDATE activity SET is_eliminated = 1 WHERE title LIKE '%NIKKO%' AND day_id = ? AND is_eliminated = 0", (day4_id,))
    if day11_id:
        cursor.execute("UPDATE activity SET is_eliminated = 1 WHERE title LIKE '%HIMEJI%NARA%' AND day_id = ? AND is_eliminated = 0", (day11_id,))
    if day7_id:
        cursor.execute("UPDATE activity SET is_eliminated = 1 WHERE title LIKE '%SKIP KANAZAWA%' AND day_id = ? AND is_eliminated = 0", (day7_id,))

    conn.commit()

    # ---- PHASE 3: Delete notes masquerading as activities ----

    if day2_id:
        cursor.execute("DELETE FROM activity WHERE title LIKE '%Keikyu Line%' AND day_id = ? AND title NOT LIKE '%Shinagawa%'", (day2_id,))
        cursor.execute("DELETE FROM activity WHERE title LIKE '%PRIORITY%SLEEP%' AND day_id = ?", (day2_id,))

    day8_id = _day_id(cursor, 8)
    if day8_id:
        cursor.execute("DELETE FROM activity WHERE title LIKE '%Hakutaka%' AND day_id = ?", (day8_id,))

    day9_id = _day_id(cursor, 9)
    if day9_id:
        # Fold Keihan Line info into Fushimi Inari
        cursor.execute("SELECT id, description FROM activity WHERE title LIKE '%Fushimi Inari%' AND day_id = ?", (day9_id,))
        row = cursor.fetchone()
        if row and 'Keihan' not in (row[1] or ''):
            cursor.execute("""
                UPDATE activity SET
                    description = 'Thousands of vermilion torii gates winding up the mountainside. Go early (~6:30 AM) to beat crowds. Getting there: Keihan Line from Sanjo to Fushimi-Inari Station (~6 min, ~¥220).'
                WHERE id = ?
            """, (row[0],))
        cursor.execute("DELETE FROM activity WHERE title LIKE '%Keihan Line%' AND day_id = ?", (day9_id,))

        # Fold respectful note into Hanamikoji
        cursor.execute("SELECT id, description FROM activity WHERE title LIKE '%Hanamikoji%' AND day_id = ?", (day9_id,))
        row = cursor.fetchone()
        if row and 'respectful' not in (row[1] or '').lower():
            cursor.execute("""
                UPDATE activity SET
                    description = 'Best time: 5:30-7:00 PM as geiko/maiko walk to engagements. Be respectful — do not chase, block, or photograph them up close. They are working professionals.'
                WHERE id = ?
            """, (row[0],))
        cursor.execute("DELETE FROM activity WHERE title LIKE '%Be respectful%' AND day_id = ?", (day9_id,))

    if day13_id:
        cursor.execute("DELETE FROM activity WHERE title LIKE '%quiet ramen shop%' AND day_id = ?", (day13_id,))

    day6_id = _day_id(cursor, 6)
    if day6_id:
        cursor.execute("DELETE FROM activity WHERE title LIKE '%Breakfast%Included at ryokan%' AND day_id = ?", (day6_id,))

    conn.commit()

    # ---- PHASE 4: Fold sub-notes into parent descriptions ----

    # Black eggs → Hakone Loop
    if day4_id:
        cursor.execute("SELECT id, description FROM activity WHERE title LIKE '%Hakone Loop%' AND day_id = ?", (day4_id,))
        row = cursor.fetchone()
        if row and 'black' not in (row[1] or '').lower():
            cursor.execute("""
                UPDATE activity SET description = description || ' Try the black sulfur eggs at Owakudani — cooked in volcanic steam, supposedly add 7 years to your life!'
                WHERE id = ?
            """, (row[0],))
        cursor.execute("DELETE FROM activity WHERE title LIKE '%black sulfur eggs%' AND day_id = ?", (day4_id,))

    # Apple butter → morning market
    if day6_id:
        cursor.execute("SELECT id, description FROM activity WHERE title LIKE '%Miyagawa Morning Market%' AND day_id = ?", (day6_id,))
        row = cursor.fetchone()
        if row and 'apple' not in (row[1] or '').lower():
            cursor.execute("""
                UPDATE activity SET description = 'Opens 6 AM, runs until noon. Try apple butter, mountain vegetable pickles, handmade crafts from local artisans.'
                WHERE id = ?
            """, (row[0],))
        cursor.execute("DELETE FROM activity WHERE title LIKE '%apple butter%' AND day_id = ?", (day6_id,))

    # Walk farmhouses + Wada House → Shirakawa-go
    if day7_id:
        cursor.execute("SELECT id, description FROM activity WHERE title LIKE '%Shirakawa-go%Village%' AND day_id = ?", (day7_id,))
        row = cursor.fetchone()
        if row and 'farmhouses' not in (row[1] or '').lower():
            cursor.execute("""
                UPDATE activity SET description = 'Explore 2-3 hours. Walk among gassho-zukuri farmhouses — steep thatched roofs built 250+ years ago. Visit Wada House (largest preserved farmhouse, ~¥300).'
                WHERE id = ?
            """, (row[0],))
        cursor.execute("DELETE FROM activity WHERE title LIKE '%Walk among gassho%' AND day_id = ?", (day7_id,))
        cursor.execute("DELETE FROM activity WHERE title LIKE '%Wada House%' AND day_id = ?", (day7_id,))

    # Teahouse note → Higashi Chaya
    if day7_id:
        cursor.execute("SELECT id, description FROM activity WHERE title LIKE '%Higashi Chaya%' AND day_id = ?", (day7_id,))
        row = cursor.fetchone()
        if row and 'open to visitors' not in (row[1] or '').lower():
            cursor.execute("""
                UPDATE activity SET description = 'Atmospheric wooden teahouses lit by warm lanterns at dusk. Some teahouses are open to visitors during the day; at night, just walk and absorb the atmosphere.'
                WHERE id = ?
            """, (row[0],))
        cursor.execute("DELETE FROM activity WHERE title LIKE '%teahouses are open%' AND day_id = ?", (day7_id,))

    conn.commit()

    # ---- PHASE 5: Fix incorrect data ----

    # Rename "Off the beaten path" to "D.T. Suzuki Museum"
    if day8_id:
        cursor.execute("""
            UPDATE activity SET
                title = 'D.T. Suzuki Museum',
                description = 'Serene, minimalist museum of Zen Buddhism with a stunning reflective water garden. Perfect for quiet contemplation. One of the most beautiful small museums in Japan.'
            WHERE title LIKE '%Off the beaten path%' AND day_id = ?
        """, (day8_id,))

    # Fix check-in/out not optional
    if day8_id:
        cursor.execute("UPDATE activity SET is_optional = 0 WHERE title LIKE '%Check into Tsukiya%' AND day_id = ? AND is_optional = 1", (day8_id,))
    if day7_id:
        cursor.execute("UPDATE activity SET is_optional = 0 WHERE title LIKE '%Check out of TAKANOYU%' AND day_id = ? AND is_optional = 1", (day7_id,))

    # Fix teamLab URL
    cursor.execute("""
        UPDATE activity SET
            url = 'https://www.teamlab.art/e/kyoto/',
            maps_url = 'https://www.google.com/maps/search/?api=1&query=teamLab+Biovortex+Kyoto',
            notes = 'Book at teamlab.art/e/kyoto/ — timed entry, sells out. Allow 2.5-3 hours. Extended April hours: open until 9:30 PM.'
        WHERE title LIKE '%teamLab%Biovortex%' AND (url LIKE '%planets%' OR url IS NULL)
    """)

    conn.commit()

    # ---- PHASE 6: Add categories to all activities ----
    category_rules = [
        ('logistics', ['%Arrive DTW%', '%Get up and walk%', '%Welcome Suica%', '%Check into%', '%Check out%',
                        '%Haneda Airport%', '%UA876%', '%K%s House%', '%Kaname Inn%']),
        ('food', ['%dinner%', '%ramen%', '%Lunch%', '%okonomiyaki%', '%kaisendon%', '%Omicho Market%dinner%',
                  '%kaiseki%', '%Hida beef sushi%', '%Golden Gai%', '%Omoide Yokocho%', '%Pontocho%',
                  '%Kyoto dinner%', '%Gion area restaurant%', '%Kuromon Market%', '%Dotonbori%',
                  '%Nishiki Market%', '%gold leaf ice cream%', '%Tsukiji%', '%omiyage%']),
        ('temple', ['%Senso-ji%', '%Meiji Shrine%', '%Fushimi Inari%', '%Kiyomizu-dera%', '%Byodo-in%',
                     '%Kinkaku-ji%', '%Tenryu-ji%', '%Itsukushima%', '%NIKKO%']),
        ('nature', ['%Hakone Loop%', '%onsen%', '%Tenzan Tohji%', '%Bamboo Grove%', '%Togetsukyo%',
                     '%Monkey Park%', '%Philosopher%Path%', '%Kamo River%', '%Sai River%',
                     '%observation deck%']),
        ('culture', ['%Shibuya Crossing%', '%Tokyo Skytree%', '%Hida Folk Village%', '%Sanmachi%',
                      '%Takayama Jinya%', '%old streets at night%', '%Shirakawa-go%Village%',
                      '%Kanazawa Castle%', '%Higashi Chaya%', '%21st Century Museum%', '%D.T. Suzuki%',
                      '%Orientation walk%', '%Hiroshima Peace%', '%Osaka Castle%', '%Shinsekai%',
                      '%Kimono%', '%Evening walk%', '%Hanamikoji%', '%Sleep in%', '%HIMEJI%',
                      '%explore%Shinjuku%']),
        ('shopping', ['%Harajuku%', '%Sannenzaka%', '%Craft shops%', '%Quick shopping%',
                       '%Don Quijote%', '%Uniqlo%', '%Miyagawa Morning Market%', '%Itsukushima Shrine%']),
        ('nightlife', ['%Golden Gai%']),
        ('transit', ['%Shinkansen%', '%Hida Limited Express%', '%Keikyu Line%Shinagawa%', '%SKIP KANAZAWA%']),
        ('entertainment', ['%teamLab%']),
    ]
    for cat, patterns in category_rules:
        for pat in patterns:
            cursor.execute("UPDATE activity SET category = ? WHERE title LIKE ? AND (category IS NULL OR category = '')", (cat, pat))

    conn.commit()

    # ---- PHASE 7: Add maps_url to key activities ----
    maps_rules = [
        ('%Senso-ji%', 'Senso-ji+Temple+Asakusa+Tokyo'),
        ('%Meiji Shrine%', 'Meiji+Shrine+Shibuya+Tokyo'),
        ('%Harajuku%', 'Takeshita+Street+Harajuku+Tokyo'),
        ('%Shibuya Crossing%', 'Shibuya+Crossing+Tokyo'),
        ('%Golden Gai%', 'Golden+Gai+Shinjuku+Tokyo'),
        ('%Omoide Yokocho%', 'Omoide+Yokocho+Shinjuku+Tokyo'),
        ('%Tenzan Tohji%', 'Tenzan+Tohji-kyo+Hakone'),
        ('%Miyagawa Morning Market%', 'Miyagawa+Morning+Market+Takayama'),
        ('%Hida Folk Village%', 'Hida+no+Sato+Takayama'),
        ('%Sanmachi Suji%', 'Sanmachi+Suji+Takayama'),
        ('%Takayama Jinya%', 'Takayama+Jinya'),
        ('%Shirakawa-go%Village%', 'Shirakawa-go+Village'),
        ('%observation deck%', 'Shirakawa-go+Observation+Deck'),
        ('%Higashi Chaya%', 'Higashi+Chaya+District+Kanazawa'),
        ('%21st Century Museum%', '21st+Century+Museum+Contemporary+Art+Kanazawa'),
        ('%D.T. Suzuki Museum%', 'D.T.+Suzuki+Museum+Kanazawa'),
        ('%Kamo River%', 'Kamo+River+Sanjo+Kyoto'),
        ('%Pontocho Alley%', 'Pontocho+Alley+Kyoto'),
        ('%Fushimi Inari%', 'Fushimi+Inari+Taisha+Kyoto'),
        ('%Byodo-in%', 'Byodo-in+Temple+Uji+Kyoto'),
        ('%Kiyomizu-dera%', 'Kiyomizu-dera+Temple+Kyoto'),
        ('%Hanamikoji%', 'Hanamikoji+Street+Gion+Kyoto'),
        ('%Kinkaku-ji%', 'Kinkaku-ji+Golden+Pavilion+Kyoto'),
        ('%Bamboo Grove%', 'Arashiyama+Bamboo+Grove+Kyoto'),
        ('%Tenryu-ji%', 'Tenryu-ji+Temple+Arashiyama+Kyoto'),
        ('%Nishiki Market%', 'Nishiki+Market+Kyoto'),
        ('%Hiroshima Peace%', 'Hiroshima+Peace+Memorial+Park'),
        ('%Itsukushima Torii%', 'Itsukushima+Shrine+Miyajima'),
        ('%Osaka Castle%', 'Osaka+Castle+Park'),
        ('%Kuromon Market%', 'Kuromon+Market+Osaka'),
        ('%Shinsekai%', 'Shinsekai+Osaka'),
        ('%Dotonbori%', 'Dotonbori+Osaka'),
    ]
    for title_pat, query in maps_rules:
        maps_url = f'https://www.google.com/maps/search/?api=1&query={query}'
        cursor.execute("UPDATE activity SET maps_url = ? WHERE title LIKE ? AND (maps_url IS NULL OR maps_url = '')",
                       (maps_url, title_pat))

    conn.commit()

    # ---- PHASE 8: Re-sort activities per day ----
    for day_num in range(1, 15):
        did = _day_id(cursor, day_num)
        if not did:
            continue
        cursor.execute("""
            SELECT id FROM activity WHERE day_id = ?
            ORDER BY
                CASE time_slot WHEN 'morning' THEN 1 WHEN 'afternoon' THEN 2
                               WHEN 'evening' THEN 3 WHEN 'night' THEN 4 ELSE 5 END,
                sort_order
        """, (did,))
        rows = cursor.fetchall()
        for i, (aid,) in enumerate(rows):
            cursor.execute("UPDATE activity SET sort_order = ? WHERE id = ?", (i + 1, aid))

    conn.commit()


def _migrate_url_integrity(cursor, conn):
    """Fix missing/generic URLs for selected accommodations and transport routes.

    Idempotent: only updates NULL or known-bad values (generic Agoda homepage).
    Uses content-based lookups (NOT hardcoded IDs).
    """

    # --- 1. Selected accommodation URLs ---
    _accom_urls = [
        # (name_pattern, booking_url, alt_booking_url, maps_url)
        ('%Sotetsu Fresa%Higashi%',
         'https://en.sotetsu-hotels.com/fresa-inn/higashishinjuku/',
         None,
         None),  # maps_url already set
        ('%TAKANOYU%',
         None,
         None,
         'https://www.google.com/maps/search/?api=1&query=TAKANOYU+Takayama+Japan'),
        ('%Tsukiya%Mikazuki%',
         'https://tsukiya-kyoto.com/english/',
         'https://tsukiya-kyoto.com/mikazuki.php',
         'https://www.google.com/maps/search/?api=1&query=Tsukiya+Mikazuki+Kyoto+Shimogyo'),
        ('%Kyotofish%Miyagawa%',
         'https://www.kyotofish.net/',
         'https://www.airbnb.com/rooms/47141239',
         'https://www.google.com/maps/search/?api=1&query=Kyotofish+Miyagawa+Higashiyama+Kyoto'),
        ('%Hotel%Leben%Osaka%',
         'https://leben-hotels.jp/en/',
         None,
         'https://www.google.com/maps/search/?api=1&query=Hotel+The+Leben+Osaka+Shinsaibashi'),
    ]
    GENERIC_AGODA = 'https://www.agoda.com/'

    for name_pat, booking, alt_booking, maps in _accom_urls:
        cursor.execute("""
            SELECT id, booking_url, alt_booking_url, maps_url FROM accommodation_option
            WHERE name LIKE ? AND is_selected = 1
        """, (name_pat,))
        row = cursor.fetchone()
        if not row:
            continue
        opt_id, cur_booking, cur_alt, cur_maps = row

        # Fix booking_url if missing or generic homepage
        if booking and (not cur_booking or cur_booking == GENERIC_AGODA):
            cursor.execute("UPDATE accommodation_option SET booking_url = ? WHERE id = ?",
                           (booking, opt_id))
        # Fix alt_booking_url if missing
        if alt_booking and not cur_alt:
            cursor.execute("UPDATE accommodation_option SET alt_booking_url = ? WHERE id = ?",
                           (alt_booking, opt_id))
        # Fix maps_url if missing
        if maps and not cur_maps:
            cursor.execute("UPDATE accommodation_option SET maps_url = ? WHERE id = ?",
                           (maps, opt_id))

    # --- 2. Transport route URLs (fill missing operator sites) ---
    _transport_urls = [
        # (route_from_pattern, route_to_pattern, url_to_set)
        ('%Kanazawa%', '%Tsuruga%', 'https://www.westjr.co.jp/global/en/'),
        ('%Tsuruga%', '%Kyoto%', 'https://www.westjr.co.jp/global/en/'),
        ('%Kyoto%', '%Osaka%', 'https://www.westjr.co.jp/global/en/'),
        ('%Kyoto%', '%Tokyo%', 'https://smart-ex.jp/en/'),
        ('%Shin-Osaka%', '%Tokyo%', 'https://smart-ex.jp/en/'),
        ('%Shinagawa%', '%Haneda%', 'https://www.keikyu.co.jp/en/'),
        ('%Takayama%', '%Shirakawa%', 'https://www.nouhibus.co.jp/english/'),
        ('%Shirakawa%', '%Kanazawa%', 'https://www.nouhibus.co.jp/english/'),
    ]
    for from_pat, to_pat, url in _transport_urls:
        cursor.execute("""
            UPDATE transport_route SET url = ?
            WHERE route_from LIKE ? AND route_to LIKE ?
              AND (url IS NULL OR url = '')
        """, (url, from_pat, to_pat))

    conn.commit()


def _migrate_fix_eliminated_booking_status(cursor, conn):
    """Downgrade eliminated accommodation options that still have active booking status.

    If an option is eliminated (is_eliminated=1) but still has booking_status
    'booked' or 'confirmed', it triggers false "multiple options booked" warnings.
    Downgrade to 'cancelled' since the option was ruled out.

    Idempotent: only affects eliminated options with active booking status.
    """
    cursor.execute("""
        UPDATE accommodation_option
        SET booking_status = 'cancelled'
        WHERE is_eliminated = 1
          AND booking_status IN ('booked', 'confirmed')
    """)
    affected = cursor.rowcount
    if affected:
        print(f'  Fixed {affected} eliminated option(s) with active booking status → cancelled')


def _migrate_cancel_kyotofish(cursor, conn):
    """Cancel Kyotofish Miyagawa booking for Kyoto Stay 2.

    Booking was cancelled by user. Unselect, clear document link, and
    re-open other options for consideration.

    One-shot: uses sentinel to ensure this only runs once.
    """
    # Sentinel check — use trip.notes (not quick_notes which this migration overwrites)
    cursor.execute("SELECT notes FROM trip WHERE id = 1")
    row = cursor.fetchone()
    if row and row[0] and '__kyotofish_cancelled_v1' in row[0]:
        return

    cursor.execute("""
        UPDATE accommodation_option
        SET booking_status = 'cancelled', is_selected = 0, document_id = NULL
        WHERE name LIKE '%Kyotofish%Miyagawa%'
          AND (is_selected = 1 OR booking_status IN ('booked', 'confirmed'))
    """)
    if cursor.rowcount:
        print(f'  Cancelled Kyotofish Miyagawa booking')
        # Re-open other options for this location
        cursor.execute("""
            UPDATE accommodation_option
            SET is_eliminated = 0
            WHERE location_id = (
                SELECT location_id FROM accommodation_option
                WHERE name LIKE '%Kyotofish%Miyagawa%'
            )
              AND name NOT LIKE '%Kyotofish%'
              AND is_eliminated = 1
        """)

    # Clean up quick_notes
    cursor.execute("""
        UPDATE accommodation_location
        SET quick_notes = 'Second half of Kyoto. Private teahouse in Miyagawacho geisha district.'
        WHERE location_name LIKE '%Kyoto%Stay 2%'
          AND quick_notes LIKE '%BOOKED%Kyotofish%'
    """)
    if cursor.rowcount:
        print(f'  Cleaned Kyoto Stay 2 quick_notes (removed cancelled booking reference)')

    # Set sentinel so this never runs again
    cursor.execute("""
        UPDATE trip SET notes = COALESCE(notes, '') || ' __kyotofish_cancelled_v1'
        WHERE id = 1 AND (notes IS NULL OR notes NOT LIKE '%__kyotofish_cancelled_v1%')
    """)
