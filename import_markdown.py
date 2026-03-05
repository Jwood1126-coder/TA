"""
Import markdown travel plan files into SQLite database.
Run once: python import_markdown.py
Re-run safe: drops and recreates all tables.
"""
import re
import sys
import os
from datetime import date, datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

from app import create_app
from models import db, Trip, Location, Day, Activity, AccommodationLocation, \
    AccommodationOption, Flight, TransportRoute, BudgetItem, ChecklistItem, \
    ChecklistOption, ReferenceContent

# Look in source_data/ first (repo), then fall back to ../Japan/ (local dev)
_base = os.path.dirname(__file__)
_source = os.path.join(_base, 'source_data')
_japan = os.path.join(_base, '..', 'Japan')

if os.path.isdir(_source):
    MASTER_PLAN = os.path.join(_source, 'Japan-Master-Travel-Plan.md')
    ACCOMMODATION_PICKER = os.path.join(_source, 'Japan-Accommodation-Picker.md')
else:
    MASTER_PLAN = os.path.join(_japan, 'Japan-Master-Travel-Plan.md')
    ACCOMMODATION_PICKER = os.path.join(_japan, 'Japan-Accommodation-Picker.md')


def main():
    app = create_app()
    with app.app_context():
        print("Dropping and recreating all tables...")
        db.drop_all()
        db.create_all()

        print(f"Reading master plan: {MASTER_PLAN}")
        with open(MASTER_PLAN, 'r', encoding='utf-8') as f:
            master = f.read()

        print(f"Reading accommodation picker: {ACCOMMODATION_PICKER}")
        with open(ACCOMMODATION_PICKER, 'r', encoding='utf-8') as f:
            picker = f.read()

        # 1. Trip record
        import_trip()

        # 2. Locations
        import_locations(master)

        # 3. Days and activities
        import_days(master)

        # 4. Flights
        import_flights()

        # 5. Transport routes
        import_transport(master)

        # 6. Accommodations from picker file
        import_accommodations(picker)

        # 7. Budget
        import_budget(master)

        # 8. Checklists
        import_checklists(master)

        # 9. Reference content
        import_reference(master)

        db.session.commit()
        print("\nImport complete!")
        print_stats()


def import_trip():
    print("  Importing trip...")
    trip = Trip(
        name='Japan 2026 — Cherry Blossoms',
        start_date=date(2026, 4, 4),
        end_date=date(2026, 4, 18),
        num_people=2,
        budget_target_low=5316,
        budget_target_high=6256,
        notes='15-day cherry blossom trip. Cleveland → Minneapolis → Tokyo → '
              'Alps → Kyoto → Home'
    )
    db.session.add(trip)


def import_locations(master):
    print("  Importing locations...")
    locations = [
        ('Minneapolis', 'Midwest US', 'Quick overnight — explore a fun city',
         'Overnight layover between CLE and HND', '2026-04-04', '2026-04-05', None),
        ('Tokyo', 'Kanto', 'Electric, endless, overwhelming (in the best way)',
         'Fly in/out here. World\'s largest metro area.', '2026-04-06', '2026-04-08',
         'https://www.japan-guide.com/e/e2164.html'),
        ('Hakone', 'Kanto', 'Mountain escape, natural beauty',
         'Day trip from Tokyo. Mt. Fuji views, volcanic valleys.', '2026-04-08', '2026-04-08',
         'https://www.japan-guide.com/e/e5200.html'),
        ('Takayama', 'Chubu', 'Quiet, historic, sake and wagyu beef',
         'Japanese Alps. Preserved Edo-era streets. Peak cherry blossom.',
         '2026-04-09', '2026-04-10',
         'https://www.japan-guide.com/e/e5900.html'),
        ('Shirakawa-go', 'Chubu', 'Storybook rural Japan',
         'UNESCO village of 250-year-old thatched-roof farmhouses.',
         '2026-04-11', '2026-04-11',
         'https://www.japan-guide.com/e/e5950.html'),
        ('Kanazawa', 'Chubu', 'Elegant, artistic, refined',
         'Underrated coastal city. Top-3 garden, geisha district.',
         '2026-04-11', '2026-04-11',
         'https://www.japan-guide.com/e/e2167.html'),
        ('Kyoto', 'Kansai', 'Timeless, romantic, spiritual',
         'Cultural heart of Japan. Former imperial capital for 1,000 years.',
         '2026-04-12', '2026-04-16',
         'https://www.japan-guide.com/e/e2158.html'),
        ('Osaka', 'Kansai', 'Rowdy, delicious, the anti-Kyoto',
         'Optional buffer day. Japan\'s street food capital.',
         '2026-04-16', '2026-04-16',
         'https://www.japan-guide.com/e/e2157.html'),
    ]
    for i, (name, region, vibe, why, arr, dep, guide) in enumerate(locations, 1):
        loc = Location(
            name=name, region=region, vibe=vibe, why=why,
            guide_url=guide,
            arrival_date=date.fromisoformat(arr),
            departure_date=date.fromisoformat(dep),
            sort_order=i
        )
        db.session.add(loc)
    db.session.flush()


def import_days(master):
    print("  Importing days and activities...")

    # Split by day headers
    day_pattern = re.compile(
        r'### DAY (\d+) — April (\d+) \((\w+)\): (.+?)(?=\n)',
        re.IGNORECASE
    )

    # Map location names to IDs
    locations = {loc.name: loc.id for loc in Location.query.all()}

    # Day-to-location mapping
    day_locations = {
        1: 'Minneapolis', 2: 'Minneapolis', 3: 'Tokyo', 4: 'Tokyo',
        5: 'Hakone', 6: 'Takayama', 7: 'Takayama', 8: 'Kanazawa',
        9: 'Kyoto', 10: 'Kyoto', 11: 'Kyoto', 12: 'Kyoto',
        13: 'Kyoto', 14: 'Tokyo', 15: 'Tokyo'
    }

    # Day themes
    day_themes = {
        1: 'Travel Day', 2: 'Travel Day', 3: 'Arrival Day',
        4: 'Full Day', 5: 'Day Trip', 6: 'Travel + Explore',
        7: 'Full Day', 8: 'Travel Day', 9: 'Travel + Explore',
        10: 'Full Day', 11: 'Full Day', 12: 'Day Trip',
        13: 'Buffer Day', 14: 'Travel + Last Evening', 15: 'Departure'
    }

    # Find all day sections
    day_matches = list(day_pattern.finditer(master))

    for idx, match in enumerate(day_matches):
        day_num = int(match.group(1))
        april_date = int(match.group(2))
        title = match.group(4).strip()

        # Extract the content for this day (until next day or section)
        start = match.end()
        if idx + 1 < len(day_matches):
            end = day_matches[idx + 1].start()
        else:
            # Last day - find next ## section
            next_section = master.find('\n## ', start)
            end = next_section if next_section != -1 else len(master)

        day_content = master[start:end]

        loc_name = day_locations.get(day_num)
        loc_id = locations.get(loc_name) if loc_name else None

        day = Day(
            day_number=day_num,
            date=date(2026, 4, april_date),
            title=title,
            location_id=loc_id,
            theme=day_themes.get(day_num, 'Full Day'),
            is_buffer_day=(day_num == 13),
        )
        db.session.add(day)
        db.session.flush()

        # Parse activities from day content
        parse_activities(day, day_content)

    print(f"    {Day.query.count()} days, {Activity.query.count()} activities")


def parse_activities(day, content):
    """Parse activities from a day's markdown content."""
    lines = content.split('\n')
    current_slot = None
    sort_order = 0
    in_substitute = False

    slot_map = {
        'morning': 'morning', 'early morning': 'morning',
        'daytime': 'morning', 'late afternoon': 'afternoon',
        'afternoon': 'afternoon', 'mid-morning': 'morning',
        'late morning': 'morning',
        'evening': 'evening', 'night': 'night',
        'evening/night': 'evening',
    }

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Detect time slot changes
        bold_match = re.match(r'\*\*(?:🌙\s*)?(.+?)(?:\s*—.*)?(?::)?\*\*', stripped)
        if bold_match:
            slot_text = bold_match.group(1).strip().lower()
            # Remove emoji and clean up
            slot_text = re.sub(r'[^\w\s/]', '', slot_text).strip()
            for key, val in slot_map.items():
                if key in slot_text:
                    current_slot = val
                    break
            if 'night' in slot_text or '🌙' in stripped:
                current_slot = 'evening'
            continue

        # Detect substitutes
        if stripped.startswith('> **🔄 SUBSTITUTE') or stripped.startswith('> **🔄'):
            in_substitute = True
            sub_match = re.search(r'SUBSTITUTE[:\s]*(.+?)(?:\*\*|$)', stripped)
            sub_title = sub_match.group(1).strip() if sub_match else 'Alternative'
            sort_order += 1
            activity = Activity(
                day_id=day.id,
                title=sub_title,
                time_slot=current_slot,
                is_substitute=True,
                sort_order=sort_order,
            )
            # Collect description from following > lines
            desc_lines = []
            continue

        if in_substitute and stripped.startswith('>'):
            text = stripped.lstrip('>').strip()
            if text:
                # Check for sub-bullets
                if text.startswith('-') or text.startswith('*'):
                    text = text.lstrip('-*').strip()
                activity.description = (activity.description or '') + text + '\n'
            continue
        elif in_substitute and not stripped.startswith('>'):
            in_substitute = False
            db.session.add(activity)

        # Detect main activities (bullet points)
        bullet_match = re.match(r'^-\s+\*\*(.+?)\*\*(.*)$', stripped)
        if bullet_match:
            title = bullet_match.group(1).strip()
            rest = bullet_match.group(2).strip().lstrip('—–-').strip()

            # Skip non-activity lines
            skip_keywords = ['IMPORTANT', 'PRIORITY', 'NOTE', 'ACTIVATE',
                             'This is', 'WARNING', 'Confirmed']
            if any(kw in title.upper() for kw in ['IMPORTANT', 'ACTIVATE']):
                continue

            # Parse cost
            cost_note = None
            cost_match = re.search(r'[¥~][\d,]+(?:/person)?|~?\$[\d,]+', rest)
            if cost_match:
                cost_note = cost_match.group(0)

            # Check JR Pass
            jr_covered = 'JR Pass' in rest and '✓' in rest

            sort_order += 1
            activity = Activity(
                day_id=day.id,
                title=clean_title(title),
                description=rest if rest else None,
                time_slot=current_slot,
                cost_note=cost_note,
                jr_pass_covered=jr_covered,
                is_optional=False,
                is_substitute=False,
                sort_order=sort_order,
            )
            db.session.add(activity)
            continue

        # Simple bullet points as activities (less prominent)
        simple_bullet = re.match(r'^-\s+(.+)$', stripped)
        if simple_bullet and current_slot and not stripped.startswith('- *'):
            text = simple_bullet.group(1).strip()
            # Skip very short lines, metadata lines, or formatting
            if (len(text) < 15 or text.startswith('(') or
                    text.startswith('Note:') or text.startswith('Option')):
                continue

            # Check if it looks like an activity
            if any(kw in text.lower() for kw in
                   ['walk', 'visit', 'explore', 'try', 'grab', 'stop',
                    'check', 'dinner', 'lunch', 'breakfast', 'temple',
                    'shrine', 'market', 'museum', 'park', 'shop']):
                sort_order += 1
                activity = Activity(
                    day_id=day.id,
                    title=clean_title(text[:200]),
                    time_slot=current_slot,
                    is_optional=True,
                    sort_order=sort_order,
                )
                db.session.add(activity)


def clean_title(title):
    """Clean up activity title."""
    # Remove markdown formatting
    title = re.sub(r'\*\*(.+?)\*\*', r'\1', title)
    title = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', title)
    # Remove trailing dashes and whitespace
    title = title.strip(' —–-:')
    return title[:300]


def import_flights():
    print("  Importing flights...")
    flights = [
        Flight(direction='outbound', leg_number=1, flight_number='DL 5132',
               airline='Delta', route_from='CLE', route_to='MSP',
               depart_date=date(2026, 4, 4), depart_time='Per ticketed itinerary',
               duration='~2h 30min', aircraft='Regional jet',
               cost_type='cash', cost_amount='$638/person ($1,276 total)'),
        Flight(direction='outbound', leg_number=2, flight_number='DL 121',
               airline='Delta', route_from='MSP', route_to='HND',
               depart_date=date(2026, 4, 5), depart_time='Per ticketed itinerary',
               arrive_date=date(2026, 4, 6), arrive_time='~3-5 PM JST',
               duration='~12-13h nonstop', aircraft='Boeing 767 or A330',
               cost_type='cash', cost_amount='Included in outbound'),
        Flight(direction='return', leg_number=1, flight_number='UA 33',
               airline='United', route_from='NRT', route_to='LAX',
               depart_date=date(2026, 4, 18), depart_time='4:45 PM JST',
               arrive_date=date(2026, 4, 18), arrive_time='10:45 AM Pacific',
               duration='10h 0min', aircraft='Boeing 787-9 Dreamliner',
               cost_type='miles', cost_amount='~76,500 miles/person'),
        Flight(direction='return', leg_number=2, flight_number='UA 1896',
               airline='United', route_from='LAX', route_to='CLE',
               depart_date=date(2026, 4, 18), depart_time='1:05 PM Pacific',
               arrive_date=date(2026, 4, 18), arrive_time='8:45 PM Eastern',
               duration='4h 40min', aircraft='Boeing 737-900',
               cost_type='miles', cost_amount='Included in return'),
    ]
    for f in flights:
        db.session.add(f)


def import_transport(master):
    print("  Importing transport routes...")
    routes = [
        ('Tokyo', 'Odawara', 'Shinkansen', 'Hikari/Kodama', '~35 min', True, None),
        ('Tokyo', 'Nagoya', 'Shinkansen', 'Hikari', '~1h 40min', True, '¥11,300'),
        ('Nagoya', 'Takayama', 'JR Express', 'Hida Limited Express', '~2h 20min', True, '¥6,140'),
        ('Takayama', 'Shirakawa-go', 'Nohi Bus', None, '~50 min', False, '¥2,800/person'),
        ('Shirakawa-go', 'Kanazawa', 'Nohi Bus', None, '~1h 15min', False, '¥2,800/person'),
        ('Kanazawa', 'Tsuruga', 'Shinkansen', 'Hokuriku Shinkansen', '~45 min', True, '¥3,800'),
        ('Tsuruga', 'Kyoto', 'Limited Express', 'Thunderbird', '~80 min', True, '¥3,750'),
        ('Kyoto', 'Hiroshima', 'Shinkansen', 'Hikari', '~1h 45min', True, '¥11,420'),
        ('Hiroshima', 'Miyajima', 'JR Ferry', None, '~10 min', True, '¥360 RT'),
        ('Kyoto', 'Tokyo', 'Shinkansen', 'Hikari', '~2h 15min', True, '¥14,170'),
        ('Shinagawa', 'Narita Airport', 'Narita Express', 'N\'EX', '~50-60 min', True, '¥3,250'),
    ]
    for i, (fr, to, ttype, name, dur, jr, cost) in enumerate(routes, 1):
        route = TransportRoute(
            route_from=fr, route_to=to, transport_type=ttype,
            train_name=name, duration=dur, jr_pass_covered=jr,
            cost_if_not_covered=cost, sort_order=i,
        )
        db.session.add(route)


def import_accommodations(picker):
    print("  Importing accommodations from picker...")

    # Parse sections by ## headings
    sections = re.split(r'\n## \d+\.\s+', picker)

    # Location data (manually mapped from the picker file structure)
    location_data = [
        {'name': 'Minneapolis', 'check_in': '2026-04-04',
         'check_out': '2026-04-05', 'nights': 1, 'sort': 0,
         'notes': 'Book through united.com hotel portal. Apply $100 credit.'},
        {'name': 'Tokyo (Asakusa area)', 'check_in': '2026-04-06',
         'check_out': '2026-04-09', 'nights': 3, 'sort': 1,
         'notes': 'Tokyo accommodation tax: ~¥100-200/person/night'},
        {'name': 'Takayama Ryokan', 'check_in': '2026-04-09',
         'check_out': '2026-04-10', 'nights': 1, 'sort': 2,
         'notes': 'BOOK IMMEDIATELY — peak cherry blossom. Price includes kaiseki dinner + breakfast.'},
        {'name': 'Takayama Budget', 'check_in': '2026-04-10',
         'check_out': '2026-04-11', 'nights': 1, 'sort': 3,
         'notes': "K's House Takayama is CLOSED. Use alternatives."},
        {'name': 'Kanazawa', 'check_in': '2026-04-11',
         'check_out': '2026-04-12', 'nights': 1, 'sort': 4,
         'notes': 'Kanazawa accommodation tax: ¥200/person/night'},
        {'name': 'Kyoto (3 nights)', 'check_in': '2026-04-12',
         'check_out': '2026-04-15', 'nights': 3, 'sort': 5,
         'notes': 'Kyoto April = hardest booking in Japan. Private rooms sell out months ahead.'},
        {'name': 'Kyoto Machiya', 'check_in': '2026-04-15',
         'check_out': '2026-04-17', 'nights': 2, 'sort': 6,
         'notes': 'Traditional townhouse. Most romantic accommodation of the trip.'},
        {'name': 'Tokyo Final Night', 'check_in': '2026-04-17',
         'check_out': '2026-04-18', 'nights': 1, 'sort': 7,
         'notes': 'Near Shinagawa for Narita Express. May be eliminated if flight changes.'},
    ]

    for loc_info in location_data:
        loc = AccommodationLocation(
            location_name=loc_info['name'],
            check_in_date=date.fromisoformat(loc_info['check_in']),
            check_out_date=date.fromisoformat(loc_info['check_out']),
            num_nights=loc_info['nights'],
            quick_notes=loc_info['notes'],
            sort_order=loc_info['sort'],
        )
        db.session.add(loc)
        db.session.flush()

    # Now parse the actual accommodation options from the master plan
    # (The picker file has tables, but the master plan has the detailed 5-option tables)
    import_accommodation_options()


def import_accommodation_options():
    """Import the 5 ranked options per location from the master plan data."""
    locations = AccommodationLocation.query.order_by(
        AccommodationLocation.sort_order).all()

    # Hardcoded from the master plan tables (parsed from the actual data)
    options_data = {
        'Minneapolis': [],  # Book via united.com, no fixed options
        'Tokyo (Asakusa area)': [
            {'rank': 1, 'name': 'Nui. Hostel & Bar Lounge', 'type': 'Design hostel',
             'price_low': 65, 'price_high': 85,
             'standout': 'Trendy Kuramae warehouse, great bar. No private bath.',
             'url': 'https://backpackersjapan.co.jp/nuihostel/'},
            {'rank': 2, 'name': 'Dormy Inn Asakusa', 'type': 'Business hotel',
             'price_low': 100, 'price_high': 130,
             'standout': 'Rooftop onsen + free late-night ramen. Best all-around.',
             'url': 'https://www.hotespa.net/hotels/asakusa/',
             'alt_url': 'https://www.agoda.com/dormy-inn-asakusa/hotel/tokyo-jp.html',
             'has_onsen': True, 'breakfast': True},
            {'rank': 3, 'name': 'Airbnb apartment', 'type': 'Apartment',
             'price_low': 80, 'price_high': 110,
             'standout': 'Most space, kitchen, washer. Self check-in.',
             'url': 'https://www.airbnb.com/s/Asakusa--Tokyo/homes?adults=2&checkin=2026-04-06&checkout=2026-04-09'},
            {'rank': 4, 'name': 'CITAN Hostel', 'type': 'Design hostel',
             'price_low': 85, 'price_high': 120,
             'standout': 'Same company as Nui., design-forward, private bath.',
             'url': 'https://backpackersjapan.co.jp/citan/'},
            {'rank': 5, 'name': 'THE GATE HOTEL Kaminarimon', 'type': 'Boutique hotel',
             'price_low': 180, 'price_high': 250,
             'standout': 'Skytree views from rooftop terrace. The splurge.',
             'url': 'https://www.gate-hotel.jp/asakusa-kaminarimon/en/'},
        ],
        'Takayama Ryokan': [
            {'rank': 1, 'name': 'Tanabe Ryokan', 'type': 'Traditional ryokan',
             'price_low': 100, 'price_high': 130,
             'standout': 'Cheapest authentic, family-run, central. Hida beef hoba miso.',
             'url': 'https://tanabe-ryokan.jp/english.html',
             'breakfast': True, 'has_onsen': True},
            {'rank': 2, 'name': 'Sumiyoshi Ryokan', 'type': 'Traditional ryokan',
             'price_low': 120, 'price_high': 160,
             'standout': 'River-view rooms, near morning market.',
             'url': 'http://www.sumiyoshi-ryokan.com/',
             'breakfast': True, 'has_onsen': True},
            {'rank': 3, 'name': 'Ryokan Asunaro', 'type': 'Traditional ryokan',
             'price_low': 140, 'price_high': 180,
             'standout': 'Hinoki cypress onsen baths. Generous Hida beef.',
             'url': 'https://www.yado-asunaro.com/en/',
             'breakfast': True, 'has_onsen': True},
            {'rank': 4, 'name': 'Oyado Koto no Yume', 'type': 'Traditional ryokan',
             'price_low': 150, 'price_high': 200,
             'standout': "Private couple's onsen (kashikiri). A5 Hida beef available.",
             'url': 'http://www.oyado-kotono-yume.com/',
             'breakfast': True, 'has_onsen': True},
            {'rank': 5, 'name': 'Honjin Hiranoya Annex', 'type': 'Premium ryokan',
             'price_low': 180, 'price_high': 260,
             'standout': 'Indoor + outdoor rotenburo, premium A5 kaiseki.',
             'url': 'http://www.honjinhiranoya.com/',
             'breakfast': True, 'has_onsen': True},
        ],
        'Takayama Budget': [
            {'rank': 1, 'name': 'Rickshaw Inn', 'type': 'Guesthouse',
             'price_low': 55, 'price_high': 75,
             'standout': 'Decades of operation, best English support, bicycle rental.',
             'url': 'http://www.rickshawinn.com/'},
            {'rank': 2, 'name': 'Takayama Oasis', 'type': 'Guesthouse',
             'price_low': 50, 'price_high': 70,
             'standout': "K's House successor, same management.",
             'url': 'https://kshouse.jp/takayama-oasis-e/index.html'},
            {'rank': 3, 'name': 'J-Hoppers Takayama', 'type': 'Hostel',
             'price_low': 50, 'price_high': 70,
             'standout': 'Reliable chain (also in Kyoto, Hiroshima).',
             'url': 'https://j-hoppers.com/takayama/'},
            {'rank': 4, 'name': 'Guesthouse Tomaru', 'type': 'Guesthouse',
             'price_low': 55, 'price_high': 80,
             'standout': 'Renovated machiya, tatami rooms.',
             'url': 'https://www.hidatakayama-guesthouse.com/'},
            {'rank': 5, 'name': 'Hostel Murasaki', 'type': 'Hostel',
             'price_low': 45, 'price_high': 65,
             'standout': 'Cheapest, closest to old town.',
             'url': 'https://www.booking.com/hotel/jp/zi-lu-guan.html'},
        ],
        'Kanazawa': [
            {'rank': 1, 'name': 'Minn Kanazawa', 'type': 'Apartment hotel',
             'price_low': 55, 'price_high': 80,
             'standout': 'Apartment-style with kitchen, 300m from Omicho Market.',
             'url': 'https://www.minn-hotels.com/'},
            {'rank': 2, 'name': 'Kaname Inn Tatemachi', 'type': 'Boutique hotel',
             'price_low': 60, 'price_high': 80,
             'standout': 'Vinyl record music bar downstairs, boutique rooms.',
             'url': 'https://kaname-inn.com/'},
            {'rank': 3, 'name': 'Dormy Inn Kanazawa', 'type': 'Business hotel',
             'price_low': 80, 'price_high': 120,
             'standout': 'Onsen + free late-night soba. Near station.',
             'url': 'https://www.hotespa.net/hotels/kanazawa/',
             'has_onsen': True},
            {'rank': 4, 'name': 'Hotel Intergate Kanazawa', 'type': 'Hotel',
             'price_low': 100, 'price_high': 150,
             'standout': 'Free all-day lounge (coffee, snacks, evening drinks).',
             'url': 'https://www.intergatehotels.jp/kanazawa/',
             'breakfast': True},
            {'rank': 5, 'name': 'HATCHi Kanazawa', 'type': 'Design hostel',
             'price_low': 45, 'price_high': 65,
             'standout': 'VERIFY OPEN — previously reported as closed for lodging.',
             'url': 'https://www.thesharehotels.com/hatchi/'},
        ],
        'Kyoto (3 nights)': [
            {'rank': 1, 'name': "K's House Kyoto", 'type': 'Hostel',
             'price_low': 65, 'price_high': 90,
             'standout': 'Near Kyoto Station. Reliable chain.',
             'url': 'https://kshouse.jp/kyoto-e/'},
            {'rank': 2, 'name': 'Piece Hostel Sanjo', 'type': 'Boutique hostel',
             'price_low': 100, 'price_high': 130,
             'standout': 'Sanjo (central), near Keihan Line for Fushimi Inari. 9.0+ rated.',
             'url': 'https://piecekyoto.com/en/'},
            {'rank': 3, 'name': 'Len Kyoto Kawaramachi', 'type': 'Design hostel',
             'price_low': 95, 'price_high': 135,
             'standout': 'Same company as Nui. Great bar + Kamo River location.',
             'url': 'https://backpackersjapan.co.jp/kyotohostel/'},
            {'rank': 4, 'name': 'Dormy Inn Premium Kyoto', 'type': 'Business hotel',
             'price_low': 110, 'price_high': 150,
             'standout': 'Near Kyoto Station. Onsen + free late-night soba.',
             'url': 'https://www.hotespa.net/hotels/kyoto/',
             'has_onsen': True, 'breakfast': True},
            {'rank': 5, 'name': 'Hotel Ethnography Gion', 'type': 'Boutique hotel',
             'price_low': 130, 'price_high': 180,
             'standout': 'Heart of Gion. Walk to geisha district. Most romantic.',
             'url': 'https://ethnography.jp/en/gion-shinmonzen/'},
        ],
        'Kyoto Machiya': [
            {'rank': 1, 'name': 'Rinn Kyoto (Nishijin)', 'type': 'Machiya',
             'price_low': 65, 'price_high': 85,
             'standout': '50+ licensed machiya. Best value. Full private house.',
             'url': 'https://rinn-kyoto.com/en/'},
            {'rank': 2, 'name': 'Rinn Kyoto (Gion)', 'type': 'Machiya',
             'price_low': 90, 'price_high': 110,
             'standout': 'Same quality, steps from geisha district.',
             'url': 'https://rinn-kyoto.com/en/'},
            {'rank': 3, 'name': 'Machiya Residence Inn', 'type': 'Machiya',
             'price_low': 80, 'price_high': 130,
             'standout': 'Oldest licensed operator. Some have hinoki baths.',
             'url': 'https://www.kyomachiya.com/en/'},
            {'rank': 4, 'name': 'Airbnb machiya', 'type': 'Airbnb',
             'price_low': 70, 'price_high': 120,
             'standout': 'Widest selection but 14% service fee. Look for "M" registration.',
             'url': 'https://www.airbnb.com/s/Kyoto/homes?adults=2&checkin=2026-04-15&checkout=2026-04-17&query=machiya'},
            {'rank': 5, 'name': 'Nazuna Kyoto', 'type': 'Luxury machiya',
             'price_low': 120, 'price_high': 200,
             'standout': 'Private hinoki bath, tea ceremony sets.',
             'url': 'https://nazuna.co/en/'},
        ],
        'Tokyo Final Night': [
            {'rank': 1, 'name': 'Toyoko Inn Shinagawa', 'type': 'Budget hotel',
             'price_low': 65, 'price_high': 75,
             'standout': '1 min walk to Narita Express. Free basic breakfast.',
             'url': 'https://www.toyoko-inn.com/eng/search/detail/00054',
             'breakfast': True},
            {'rank': 2, 'name': 'Sotetsu Grand Fresa Shinagawa Seaside', 'type': 'Business hotel',
             'price_low': 60, 'price_high': 80,
             'standout': 'Overlooks Tokyo Bay. 5 min to Shinagawa via Rinkai Line.',
             'url': 'https://sotetsu-hotels.com/en/grand-fresa/shinagawa-seaside/'},
            {'rank': 3, 'name': 'APA Hotel Sengakuji', 'type': 'Budget hotel',
             'price_low': 50, 'price_high': 70,
             'standout': 'Cheapest option. 10 min walk to Shinagawa.',
             'url': 'https://www.apahotel.com/en/hotel/shutoken/tokyo/shinagawa-sengakujiekimae/'},
            {'rank': 4, 'name': 'Dormy Inn Meguro Aobadai', 'type': 'Business hotel',
             'price_low': 75, 'price_high': 100,
             'standout': 'Onsen soak before 20 hrs of travel + free ramen 10 PM.',
             'url': 'https://en.dormy-hotels.com/hotel/kanto/tokyo/12849/',
             'has_onsen': True, 'breakfast': True},
            {'rank': 5, 'name': 'JR-East Hotel Mets Premier Gotanda', 'type': 'Station hotel',
             'price_low': 100, 'price_high': 140,
             'standout': '1 min from Gotanda Stn. JR direct to Shinagawa (3 min).',
             'url': 'https://www.hotelmets.jp/en/gotanda/'},
        ],
    }

    for loc in locations:
        opts = options_data.get(loc.location_name, [])
        for opt in opts:
            nights = loc.num_nights
            option = AccommodationOption(
                location_id=loc.id,
                rank=opt['rank'],
                name=opt['name'],
                property_type=opt.get('type'),
                price_low=opt.get('price_low'),
                price_high=opt.get('price_high'),
                total_low=opt.get('price_low', 0) * nights if opt.get('price_low') else None,
                total_high=opt.get('price_high', 0) * nights if opt.get('price_high') else None,
                breakfast_included=opt.get('breakfast', False),
                has_onsen=opt.get('has_onsen', False),
                standout=opt.get('standout'),
                booking_url=opt.get('url'),
                alt_booking_url=opt.get('alt_url'),
            )
            db.session.add(option)

    print(f"    {AccommodationLocation.query.count()} locations, "
          f"{AccommodationOption.query.count()} options")


def import_budget(master):
    print("  Importing budget...")
    items = [
        ('Flights', 'Outbound (Delta cash)', 1276, 1276, 'CLE → MSP → HND'),
        ('Flights', 'Return (United award)', 100, 100, '~153K miles + taxes'),
        ('Flights', 'Miles purchase (if needed)', 0, 490, 'Only if billing cycle gap'),
        ('Transport', '14-Day JR Pass × 2', 1060, 1060, '¥80,000/person'),
        ('Transport', 'Local transport', 210, 210, 'IC cards, Nohi Bus, Hakone Pass'),
        ('Accommodation', '13 nights + 1 MSP', 1030, 1480, 'Realistic April pricing'),
        ('Food', '13 full days × 2 people', 1040, 1040, '~$40/day/person average'),
        ('Activities', 'Entrance fees & experiences', 250, 250, 'TeamLab, temples, museums'),
        ('Connectivity', 'Pocket WiFi or eSIM', 50, 50, '1 shared device'),
        ('Misc', 'Souvenirs & buffer', 300, 300, ''),
    ]
    for i, (cat, desc, low, high, notes) in enumerate(items, 1):
        item = BudgetItem(
            category=cat, description=desc,
            estimated_low=low, estimated_high=high,
            notes=notes, sort_order=i,
        )
        db.session.add(item)


def import_checklists(master):
    print("  Importing checklists...")

    # (category, title, priority, sort_order, item_type, accommodation_location_name)
    checklists = [
        # Today — booking decisions linked to accommodations
        ('pre_departure_today', 'Book Delta outbound CLE → MSP → HND', 'today', 1, 'decision', None),
        ('pre_departure_today', 'Book Takayama ryokan', 'today', 2, 'decision', 'Takayama Ryokan'),
        ('pre_departure_today', 'Book Piece Hostel Sanjo private room', 'today', 3, 'decision', 'Kyoto (3 nights)'),
        ('pre_departure_today', 'Reserve Nohi Bus (Takayama → Kanazawa)', 'today', 4, 'decision', None),
        # This week — accommodation decisions
        ('pre_departure_week', 'Book Minneapolis hotel', 'this_week', 5, 'decision', 'Minneapolis'),
        ('pre_departure_week', 'Book Tokyo hotel (Asakusa, 3 nights)', 'this_week', 6, 'decision', 'Tokyo (Asakusa area)'),
        ('pre_departure_week', 'Book Takayama budget night', 'this_week', 7, 'decision', 'Takayama Budget'),
        ('pre_departure_week', 'Book Kanazawa hotel (1 night)', 'this_week', 8, 'decision', 'Kanazawa'),
        ('pre_departure_week', 'Purchase 14-day JR Pass', 'this_week', 9, 'decision', None),
        ('pre_departure_week', 'Book Kyoto machiya (2 nights)', 'this_week', 10, 'decision', 'Kyoto Machiya'),
        ('pre_departure_week', 'Book Tokyo final night hotel', 'this_week', 11, 'decision', 'Tokyo Final Night'),
        # When miles post
        ('pre_departure_miles', 'Book United award return NRT → LAX → CLE', 'miles', 12, 'decision', None),
        ('pre_departure_miles', 'Buy remaining miles if needed', 'miles', 13, 'task', None),
        # 2-4 weeks before — research decisions
        ('pre_departure_month', 'Reserve pocket WiFi or purchase eSIM', 'month', 14, 'decision', None),
        ('pre_departure_month', 'Book TeamLab tickets', 'month', 15, 'decision', None),
        ('pre_departure_month', 'Register on Visit Japan Web', 'month', 16, 'task', None),
        ('pre_departure_month', 'Confirm travel insurance coverage', 'month', 17, 'decision', None),
        ('pre_departure_month', 'Notify bank of Japan travel dates', 'month', 18, 'decision', None),
        ('pre_departure_month', 'Download travel apps', 'month', 19, 'decision', None),
        ('pre_departure_month', 'Check passport validity (6+ months)', 'month', 20, 'task', None),
        ('pre_departure_month', 'Make copies of passport + confirmations', 'month', 21, 'task', None),
        # Packing - Essential (simple tasks)
        ('packing_essential', 'Passport', 'packing', 22, 'task', None),
        ('packing_essential', 'Phone + charger', 'packing', 23, 'task', None),
        ('packing_essential', 'Portable battery pack / power bank', 'packing', 24, 'task', None),
        ('packing_essential', 'Comfortable walking shoes (BROKEN IN)', 'packing', 25, 'task', None),
        ('packing_essential', 'Slip-on shoes for temples', 'packing', 26, 'task', None),
        ('packing_essential', 'Light jacket + warm layer for mountains', 'packing', 27, 'task', None),
        ('packing_essential', 'Rain jacket or compact umbrella', 'packing', 28, 'task', None),
        ('packing_essential', 'Small daypack', 'packing', 29, 'task', None),
        # Packing - Helpful (simple tasks)
        ('packing_helpful', 'Neck pillow + eye mask for flight', 'packing', 30, 'task', None),
        ('packing_helpful', 'Compression socks for flight', 'packing', 31, 'task', None),
        ('packing_helpful', 'Small towel/handkerchief', 'packing', 32, 'task', None),
        ('packing_helpful', 'Ziplock bags for snacks/trash', 'packing', 33, 'task', None),
        ('packing_helpful', 'Packing cubes', 'packing', 34, 'task', None),
        ('packing_helpful', 'Small notebook/pen', 'packing', 35, 'task', None),
        ('packing_helpful', 'Earplugs', 'packing', 36, 'task', None),
        ('packing_helpful', 'Sunglasses', 'packing', 37, 'task', None),
    ]

    for cat, title, priority, order, item_type, accom_name in checklists:
        accom_id = None
        if accom_name:
            loc = AccommodationLocation.query.filter_by(
                location_name=accom_name).first()
            if loc:
                accom_id = loc.id
        item = ChecklistItem(
            category=cat, title=title,
            priority=priority, sort_order=order,
            item_type=item_type,
            accommodation_location_id=accom_id,
        )
        db.session.add(item)

    db.session.flush()
    _import_checklist_options()


def _import_checklist_options():
    """Pre-populate research options for non-accommodation decision items."""
    print("  Importing checklist options...")

    options_data = {
        'Book Delta outbound CLE → MSP → HND': [
            {'name': 'Delta CLE → MSP → HND', 'desc': 'Delta via Minneapolis hub',
             'why': 'Direct booking, earn SkyMiles. ~$638/pp.',
             'url': 'https://www.delta.com/', 'price': '~$638/pp'},
        ],
        'Reserve Nohi Bus (Takayama → Kanazawa)': [
            {'name': 'Nohi Bus (Official)', 'desc': 'Direct highway bus, 2hr 15min',
             'why': 'JR Pass does NOT cover this route. Reserve online.',
             'url': 'https://www.nouhibus.co.jp/english/', 'price': '~¥3,900/pp'},
            {'name': 'Hokuriku Railroad Bus', 'desc': 'Alternative operator, same route',
             'why': 'Same price, sometimes different schedule.',
             'url': 'https://www.hokutetsu.co.jp/', 'price': '~¥3,900/pp'},
        ],
        'Purchase 14-day JR Pass': [
            {'name': 'Japan Rail Pass (Official)', 'desc': 'Official JR Pass site — buy exchange order online, activate at JR station',
             'why': 'Most reliable. Order ships to your address.',
             'url': 'https://japanrailpass.net/en/', 'price': '¥50,000/pp (14-day)'},
            {'name': 'JRailPass.com', 'desc': 'Authorized reseller, sometimes has promos',
             'why': 'Good alternative, ships voucher.',
             'url': 'https://www.jrailpass.com/', 'price': '~¥50,000/pp'},
            {'name': 'Buy at JR Station in Japan', 'desc': 'Purchase on arrival at major stations',
             'why': 'Available since 2023 but ~10% more expensive. No shipping needed.',
             'url': 'https://www.japanrailpass.net/en/purchase.html', 'price': '~¥55,000/pp'},
        ],
        'Book United award return NRT → LAX → CLE': [
            {'name': 'United MileagePlus Award', 'desc': 'Book with United miles',
             'why': 'Use accumulated miles. Check saver availability.',
             'url': 'https://www.united.com/en/us/awardtravel', 'price': '~35K-70K miles + taxes'},
            {'name': 'Buy Miles if Short', 'desc': 'Purchase additional miles from United',
             'why': 'Sometimes cheaper than cash tickets if close to having enough.',
             'url': 'https://www.united.com/ual/en/us/fly/mileageplus/buy-miles.html', 'price': '~3.5¢/mile'},
        ],
        'Reserve pocket WiFi or purchase eSIM': [
            {'name': 'Ubigi eSIM', 'desc': 'Digital eSIM — instant activation via app, no physical device',
             'why': 'Works on any eSIM phone (iPhone XS+). No pickup needed.',
             'url': 'https://www.ubigi.com/en/japan-esim', 'price': '$15-30 / 2 weeks'},
            {'name': 'Airalo eSIM', 'desc': 'Largest eSIM marketplace with Japan plans',
             'why': 'More plan options, widely recommended by travelers.',
             'url': 'https://www.airalo.com/japan-esim', 'price': '$15-25 / 2 weeks'},
            {'name': 'Japan Wireless Pocket WiFi', 'desc': 'Physical hotspot device — share between 2 phones',
             'why': 'One device, both phones connected. Strongest signal in rural areas.',
             'url': 'https://www.japan-wireless.com/', 'price': '$4-6/day (~$60-85 total)'},
            {'name': 'Sakura Mobile WiFi', 'desc': 'Airport pickup at Haneda/Narita',
             'why': 'Convenient pickup on arrival. Good coverage.',
             'url': 'https://www.sakuramobile.jp/wifi-rental/', 'price': '$5-7/day'},
        ],
        'Book TeamLab tickets': [
            {'name': 'TeamLab Planets (Toyosu)', 'desc': 'Immersive water art museum — walk barefoot through installations',
             'why': 'The original. Walk through knee-deep water. Sells out 2-3 weeks ahead.',
             'url': 'https://planets.teamlab.art/tokyo/en/', 'price': '~¥3,800/pp'},
            {'name': 'TeamLab Borderless (Azabudai Hills)', 'desc': 'New 2024 location — no fixed path, rooms bleed into each other',
             'why': 'Relocated from Odaiba. Larger, newer. Also sells out fast.',
             'url': 'https://www.teamlab.art/e/borderless-azabudai/', 'price': '~¥4,000/pp'},
        ],
        'Confirm travel insurance coverage': [
            {'name': 'Chase Sapphire Trip Protection', 'desc': 'Credit card included benefit — trip cancellation + interruption',
             'why': 'Free if flights paid with Sapphire. Check your card benefits.',
             'url': 'https://www.chase.com/personal/credit-cards/sapphire/preferred', 'price': 'Free (included)'},
            {'name': 'World Nomads', 'desc': 'Comprehensive travel insurance — adventure activities covered',
             'why': 'Popular with travelers. Covers medical, gear, adventure sports.',
             'url': 'https://www.worldnomads.com/', 'price': '~$50-80 / 2 weeks'},
            {'name': 'SafetyWing', 'desc': 'Subscription travel insurance — month-to-month',
             'why': 'Flexible monthly billing. Good for longer trips.',
             'url': 'https://safetywing.com/', 'price': '~$40 / 4 weeks'},
        ],
        'Notify bank of Japan travel dates': [
            {'name': 'Chase Travel Notice', 'desc': 'Set travel notification in Chase app or website',
             'why': 'Prevents fraud blocks on Japan transactions. Takes 30 seconds.',
             'url': 'https://www.chase.com/digital/login', 'price': 'Free'},
            {'name': 'Check ATM Strategy', 'desc': 'Know where to get yen: 7-Eleven ATMs accept foreign cards',
             'why': '7-Eleven and Japan Post ATMs are most reliable for foreign cards.',
             'url': 'https://www.japan-guide.com/e/e2208.html', 'price': '~$3-5 fee/withdrawal'},
        ],
        'Download travel apps': [
            {'name': 'Google Translate (offline JP pack)', 'desc': 'Download Japanese offline pack for camera translate',
             'why': 'Camera mode reads menus, signs. Works offline.',
             'url': 'https://translate.google.com/', 'price': 'Free'},
            {'name': 'Navitime for Japan Travel', 'desc': 'Best app for Japan train routes, includes IC card balance',
             'why': 'Better than Google Maps for trains. Shows platform numbers.',
             'url': 'https://www.navitime.co.jp/inbound/', 'price': 'Free'},
            {'name': 'Suica/PASMO (Apple Wallet)', 'desc': 'Add IC card to Apple Wallet — tap to ride trains, pay at konbini',
             'why': 'No physical card needed. Recharge in-app. Works everywhere.',
             'url': 'https://support.apple.com/en-us/HT207154', 'price': 'Free (load ¥)'},
            {'name': 'Google Maps (offline: Tokyo, Kyoto, Takayama)', 'desc': 'Download offline maps for each city',
             'why': 'Works without data. Download before you leave.',
             'url': 'https://support.google.com/maps/answer/6291838', 'price': 'Free'},
            {'name': 'Tabelog', 'desc': 'Japan\'s #1 restaurant rating app (like Japanese Yelp)',
             'why': 'More accurate than Google reviews for Japan. 3.5+ is excellent.',
             'url': 'https://tabelog.com/', 'price': 'Free'},
        ],
        'Register on Visit Japan Web': [
            {'name': 'Visit Japan Web', 'desc': 'Pre-fill customs & immigration forms online before landing',
             'why': 'Skip the paper forms on the plane. QR code at immigration.',
             'url': 'https://www.vjw.digital.go.jp/', 'price': 'Free'},
        ],
    }

    for title, opts in options_data.items():
        item = ChecklistItem.query.filter_by(title=title).first()
        if not item:
            continue
        for i, opt in enumerate(opts, 1):
            option = ChecklistOption(
                checklist_item_id=item.id,
                name=opt['name'],
                description=opt.get('desc'),
                why=opt.get('why'),
                url=opt.get('url'),
                price_note=opt.get('price'),
                sort_order=i,
            )
            db.session.add(option)


def import_reference(master):
    print("  Importing reference content...")

    refs = [
        ('cultural_tips', 'Shoes Off Indoors',
         'Remove shoes when entering homes, ryokan, many restaurants, temples. '
         'Look for a step up and a shoe rack. Use slippers provided. '
         'Separate toilet slippers in bathrooms.'),
        ('cultural_tips', 'No Tipping. Ever.',
         'Tipping is not customary and can cause confusion. '
         'Service is already excellent. The price is the price.'),
        ('cultural_tips', 'Quiet on Trains',
         'Phone calls on trains are a major faux pas. Set phones to silent. '
         'Speak in low voices. Eating on local trains is frowned upon '
         '(Shinkansen = OK, bento eating encouraged).'),
        ('cultural_tips', 'Bowing',
         'A slight 15-degree nod for greetings, thanks, apologies. '
         'When someone bows to you, bow back.'),
        ('cultural_tips', "Don't Eat While Walking",
         'Eat at the stall where you bought food, or at a designated area. '
         'Standing and eating = fine. Walking and eating = not fine.'),

        ('onsen', 'Onsen Etiquette',
         '1. Wash thoroughly at shower stations BEFORE entering the bath.\n'
         '2. No swimsuits — onsen are nude.\n'
         '3. Small towel: bring it but do NOT put it in the water.\n'
         '4. Tattoo note: some onsen prohibit visible tattoos. '
         'Private onsen (kashikiri) are an alternative.\n'
         '5. Gender-separated. Kashikiri baths allow couples.'),

        ('temple_shrine', 'Shrine Ritual (Shinto — torii gates)',
         '1. Bow slightly at the entrance gate\n'
         '2. Purification fountain: rinse left hand → right hand → rinse mouth → rinse left hand\n'
         '3. Toss a coin (¥5 is lucky), bow twice, clap twice, make your wish, bow once'),
        ('temple_shrine', 'Temple Ritual (Buddhist — gate guardians)',
         '1. Bow at the gate\n'
         '2. Light incense if available, waft smoke toward you\n'
         '3. Put hands together and bow. NO clapping at temples.'),

        ('phrases', 'Essential Phrases',
         'Konnichiwa (kohn-nee-chee-wah) = Hello\n'
         'Arigato gozaimasu (ah-ree-gah-toh go-zah-ee-mahs) = Thank you\n'
         'Sumimasen (sue-mee-mah-sen) = Excuse me / Sorry\n'
         'Eigo wa daijoubu desu ka? = Is English OK?\n'
         'Oishii! (oy-shee) = Delicious!'),

        ('conversation', 'Restaurant & Shopping Phrases',
         'Okaikei onegaishimasu = Check please\n'
         'Ikura desu ka? = How much?\n'
         'Kore o kudasai = This one please\n'
         'Futari desu = Table for two\n'
         'Kanpai! = Cheers!\n'
         'Gochisousama deshita = Thank you for the meal'),
        ('conversation', 'Numbers',
         '1 = ichi | 2 = ni | 3 = san | 4 = yon | 5 = go'),

        ('money', 'Cash & Payment',
         'Japan is more cash-based than expected. Many small restaurants are cash-only.\n'
         'Always carry ¥10,000-30,000 in cash (~$65-200).\n'
         'ATMs: 7-Eleven ATMs are most reliable for international cards (24/7).\n'
         'IC cards (Suica) work at convenience stores, vending machines, many restaurants.\n'
         'Place money on the small tray at the register, don\'t hand it directly.'),

        ('survival', 'Weather & Clothing (April)',
         'Tokyo/Kyoto: 60-70°F highs, 45-55°F lows. Layers: t-shirt + light jacket.\n'
         'Takayama/Alps: 50-60°F highs, 35-45°F lows. Warm layer needed.\n'
         'Rain likely: April averages 8-10 rainy days. Carry compact umbrella.\n'
         'Comfortable walking shoes CRITICAL — 10-15 miles/day.'),
        ('survival', 'Connectivity',
         'Pocket WiFi: ~$4-6/day, share 1 device, strong signal.\n'
         'eSIM (Ubigi/Airalo): ~$15-30 for 2 weeks, no extra device.\n'
         'Your own internet makes navigation 100x easier.'),
        ('survival', 'Toilets',
         'Japanese toilets: heated seats, bidet, dryer, sound effects. '
         'Buttons usually labeled in English. Big button with water waves = bidet.'),
        ('survival', 'Safety',
         'Japan is extremely safe. Lost wallets often returned with cash.\n'
         'Trash cans are rare — carry a small bag.\n'
         'Escalators: stand LEFT in Tokyo, stand RIGHT in Osaka.'),
        ('survival', 'No Power Adapter Needed',
         'Japan uses same plugs as US (Type A). Voltage is 100V vs 120V — '
         'all modern electronics handle this fine.'),

        ('emergency', 'Emergency Numbers',
         'Police: 110 (English available)\n'
         'Fire / Ambulance: 119\n'
         'JNTO Visitor Hotline (24/7): 050-3816-2787\n'
         'US Embassy Tokyo: 03-3224-5000\n'
         'US Consulate Osaka: 06-6315-5900'),
        ('emergency', 'Essential Apps',
         'Google Maps — #1 most important, perfect for Japanese trains\n'
         'Google Translate — camera mode translates signs/menus\n'
         'Suica app (iPhone) — contactless transit\n'
         'Tabelog — Japan\'s Yelp, more accurate than Google reviews'),

        ('troubleshooting', 'Common Issues',
         'Nohi Bus sold out: Skip Kanazawa or take JR trains via Nagoya.\n'
         'Ryokan sold out: Try Japanican, Booking.com, or Rakuten Travel.\n'
         'Rain on Hakone day: Swap for Kamakura or indoor Tokyo activities.\n'
         'Sick/exhausted: Rest. Pharmacies have green cross signs.\n'
         'Missed a train: Another comes in 10-30 min. JR Pass covers any JR train.\n'
         'Lost something on train: Report to station staff immediately.\n'
         'Credit card blocked: Use 7-Eleven ATM for cash.'),

        ('transport', 'JR Pass Info',
         'Price: ¥80,000/person (~$530)\n'
         'Activate: Day 5 (April 8) at Tokyo Station\n'
         'Expires: April 21 (covers everything through departure)\n'
         'Covers: All JR trains including Shinkansen (Hikari, Kodama, etc.), '
         'JR ferry to Miyajima, Narita Express\n'
         'Does NOT cover: Nozomi/Mizuho Shinkansen, city subways, '
         'private railways, Nohi Bus'),
        ('transport', 'IC Card (Suica)',
         'Get Welcome Suica at Haneda on arrival.\n'
         'Valid 28 days. Load ¥3,000 initially.\n'
         'Works on: all subways, non-JR trains, buses, convenience stores, '
         'vending machines.\n'
         'iPhone: can add mobile Suica to Apple Wallet.'),
        ('transport', 'Luggage Forwarding (Takkyubin)',
         'Ship suitcases hotel-to-hotel, arrives next day.\n'
         'Cost: ~¥2,000/bag (~$13-14).\n'
         'Ask front desk for "takkyubin" — they handle everything.\n'
         'Use on Day 5: send bags from Tokyo to Kyoto, travel light through Alps.'),
    ]

    for i, (section, title, content) in enumerate(refs, 1):
        ref = ReferenceContent(
            section=section, title=title, content=content, sort_order=i,
        )
        db.session.add(ref)


def print_stats():
    print(f"\n--- Import Statistics ---")
    print(f"  Trip: {Trip.query.count()}")
    print(f"  Locations: {Location.query.count()}")
    print(f"  Days: {Day.query.count()}")
    print(f"  Activities: {Activity.query.count()}")
    print(f"  Flights: {Flight.query.count()}")
    print(f"  Transport routes: {TransportRoute.query.count()}")
    print(f"  Accommodation locations: {AccommodationLocation.query.count()}")
    print(f"  Accommodation options: {AccommodationOption.query.count()}")
    print(f"  Budget items: {BudgetItem.query.count()}")
    print(f"  Checklist items: {ChecklistItem.query.count()}")
    print(f"  Reference items: {ReferenceContent.query.count()}")


if __name__ == '__main__':
    main()
