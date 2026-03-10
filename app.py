import os
import time
from collections import defaultdict
from flask import Flask, redirect, url_for, session, request, render_template
from flask_socketio import SocketIO
from config import Config
from models import db

# Simple in-memory rate limiter for login
_login_attempts = defaultdict(list)  # ip -> [timestamps]
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300  # 5 minutes


def _is_rate_limited(ip):
    """Check if IP has exceeded login attempt limit."""
    now = time.time()
    # Prune old attempts outside window
    _login_attempts[ip] = [t for t in _login_attempts[ip]
                           if now - t < LOGIN_WINDOW_SECONDS]
    # Clean up stale IPs periodically (every check, remove empty entries)
    stale = [k for k, v in _login_attempts.items() if not v]
    for k in stale:
        del _login_attempts[k]
    return len(_login_attempts[ip]) >= LOGIN_MAX_ATTEMPTS


def _record_attempt(ip):
    _login_attempts[ip].append(time.time())

socketio = SocketIO()


def _run_migrations(app):
    """Add new columns to existing tables if they don't exist."""
    db_path = app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
    if not os.path.exists(db_path):
        return
    import sqlite3
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
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
    ]
    for table, column, col_type in migrations:
        try:
            cursor.execute(f'ALTER TABLE {table} ADD COLUMN {column} {col_type}')
        except sqlite3.OperationalError:
            pass  # Column already exists
    conn.commit()
    conn.close()


def _migrate_remove_kanazawa(app):
    """One-time data migration: remove Kanazawa overnight, extend Takayama to 3 nights.
    Idempotent — checks if already applied."""
    from datetime import date as dt_date
    from models import (db, Day, Activity, Location, AccommodationLocation,
                        AccommodationOption)

    # Check if already applied: Takayama accom has 3 nights
    takayama_accom = AccommodationLocation.query.filter_by(
        location_name='Takayama').first()
    if not takayama_accom or takayama_accom.num_nights >= 3:
        return  # Already migrated

    print("Running data migration: remove Kanazawa, extend Takayama...")

    # 1. Clean up Day 7 — remove K's House checkout/checkin shuffle
    day7 = Day.query.filter_by(day_number=7).first()
    if day7:
        for act in Activity.query.filter_by(day_id=day7.id).all():
            if "Check out of ryokan" in (act.title or ''):
                db.session.delete(act)
            elif "Check into K" in (act.title or ''):
                db.session.delete(act)

    # 2. Repurpose Day 8 as Takayama Day 3
    day8 = Day.query.filter_by(day_number=8).first()
    if day8:
        Activity.query.filter_by(day_id=day8.id).delete()
        day8.title = "TAKAYAMA DAY 3 -- Slow Morning & Alps Life"
        day8.location_id = Location.query.filter_by(name='Takayama').first().id
        for title, slot, order in [
            ("Miyagawa Morning Market (round 2)", "morning", 1),
            ("Breakfast at a local kissaten (retro coffee shop)", "morning", 2),
            ("Rent bikes & ride along Miyagawa River", "morning", 3),
            ("Train to Hida-Furukawa -- quieter version of Takayama", "morning", 4),
            ("Furukawa's White-Walled Storehouses & koi canal", "afternoon", 5),
            ("Hida Crafts Museum or sake tasting", "afternoon", 6),
            ("Train back to Takayama", "afternoon", 7),
            ("Afternoon onsen soak", "afternoon", 8),
            ("Final Hida beef dinner -- go all out", "evening", 9),
            ("Pack & prep for early departure", "evening", 10),
        ]:
            db.session.add(Activity(day_id=day8.id, title=title,
                                    time_slot=slot, sort_order=order))

    # 3. Repurpose Day 9 as Takayama->Shirakawa->Kyoto
    day9 = Day.query.filter_by(day_number=9).first()
    if day9:
        Activity.query.filter_by(day_id=day9.id).delete()
        kyoto_loc = Location.query.filter_by(name='Kyoto').first()
        day9.title = "TAKAYAMA -> SHIRAKAWA-GO -> KYOTO"
        day9.location_id = kyoto_loc.id if kyoto_loc else day9.location_id
        for title, slot, order in [
            ("Check out of Takayama accommodation", "morning", 1),
            ("Nohi Bus: Takayama -> Shirakawa-go (~50 min)", "morning", 2),
            ("Shirakawa-go UNESCO Village", "morning", 3),
            ("Wada House (oldest thatched-roof house)", "morning", 4),
            ("Hike to Shiroyama observation deck", "morning", 5),
            ("Village lunch", "afternoon", 6),
            ("Nohi Bus: Shirakawa-go -> Kanazawa Station (~75 min)", "afternoon", 7),
            ("Hokuriku Shinkansen + Thunderbird: Kanazawa -> Kyoto (~2.5 hrs)", "afternoon", 8),
            ("Check into Piece Hostel Sanjo", "evening", 9),
            ("Stroll along Kamo River", "evening", 10),
            ("Pontocho Alley dinner", "evening", 11),
        ]:
            db.session.add(Activity(day_id=day9.id, title=title,
                                    time_slot=slot, sort_order=order))

    # 4. Update Takayama accommodation: 2->3 nights
    takayama_accom.num_nights = 3
    takayama_accom.check_out_date = dt_date(2026, 4, 12)

    # 5. Remove Kanazawa accommodation
    kanazawa_accom = AccommodationLocation.query.filter_by(
        location_name='Kanazawa').first()
    if kanazawa_accom:
        AccommodationOption.query.filter_by(location_id=kanazawa_accom.id).delete()
        db.session.delete(kanazawa_accom)

    # 6. Update Takayama location departure date
    takayama_loc = Location.query.filter_by(name='Takayama').first()
    if takayama_loc:
        takayama_loc.departure_date = dt_date(2026, 4, 12)

    # 7. Update Kanazawa location dates (transit only)
    kanazawa_loc = Location.query.filter_by(name='Kanazawa').first()
    if kanazawa_loc:
        kanazawa_loc.arrival_date = dt_date(2026, 4, 12)
        kanazawa_loc.departure_date = dt_date(2026, 4, 12)

    # 8. Delete "SKIP KANAZAWA ENTIRELY" activity if it exists
    skip_acts = Activity.query.filter(Activity.title.ilike('%SKIP KANAZAWA%')).all()
    for act in skip_acts:
        db.session.delete(act)

    db.session.commit()
    print("Migration complete: Kanazawa removed, Takayama extended to 3 nights.")


def _migrate_add_osaka_day(app):
    """One-time: add Day 16, extend Osaka to 2 nights, shift Tokyo return/departure.
    Idempotent — checks if already applied."""
    from datetime import date as dt_date
    from models import db, Day, Activity, Flight, Location, AccommodationLocation, Trip

    # Check if already applied: Day 16 exists OR trip has been restructured to 14 days
    if Day.query.filter_by(day_number=16).first():
        return
    # Also skip if trip already restructured to 14 days (no Day 15 either)
    if not Day.query.filter_by(day_number=15).first():
        return

    print("Running data migration: add Osaka day, extend trip to 16 days...")

    day14 = Day.query.filter_by(day_number=14).first()
    day15 = Day.query.filter_by(day_number=15).first()
    if not day14 or not day15:
        return

    # Shift departure day (15->16) first to avoid unique date conflict
    day15.day_number = 16
    day15.date = dt_date(2026, 4, 19)
    day15.title = "DEPARTURE DAY"
    db.session.flush()

    # Shift Tokyo return day (14->15)
    day14.day_number = 15
    day14.date = dt_date(2026, 4, 18)
    day14.title = "OSAKA -> TOKYO (LAST EVENING)"
    db.session.flush()

    # Update departure day activities: Narita -> Haneda
    for act in Activity.query.filter_by(day_id=day15.id).all():
        if "Narita Express" in (act.title or ''):
            act.title = "Train to Haneda Airport"
        elif "Narita Airport" in (act.title or ''):
            act.title = "Haneda Airport shops & last meal"

    # Update Tokyo return day: Kyoto -> Osaka references
    for act in Activity.query.filter_by(day_id=day14.id).all():
        if act.title and "Kyoto" in act.title:
            act.title = act.title.replace("Kyoto", "Osaka")
        if act.title and "Shinkansen Kyoto" in act.title:
            act.title = "Shinkansen Osaka -> Tokyo"

    # Create new Day 14 — Osaka Day 2
    osaka_loc = Location.query.filter_by(name="Osaka").first()
    new_day14 = Day(
        day_number=14,
        date=dt_date(2026, 4, 17),
        title="OSAKA DAY 2 -- Deeper Cuts",
        location_id=osaka_loc.id if osaka_loc else None,
        theme="exploration",
    )
    db.session.add(new_day14)
    db.session.flush()

    for title, slot, order in [
        ("Morning coffee & konbini breakfast", "morning", 1),
        ("Nara day trip -- deer park, Todai-ji temple", "morning", 2),
        ("JR Nara Line back to Osaka", "afternoon", 3),
        ("Explore Amerikamura (American Village)", "afternoon", 4),
        ("Shinsaibashi shopping arcade", "afternoon", 5),
        ("Dinner: Dotonbori round 2 -- try what you missed", "evening", 6),
        ("Night views from Umeda Sky Building", "evening", 7),
        ("Check out of Osaka hotel (store luggage)", "night", 8),
    ]:
        db.session.add(Activity(day_id=new_day14.id, title=title,
                                time_slot=slot, sort_order=order))

    # Remove Nara day trip from Day 13 (moved to Day 14)
    day13 = Day.query.filter_by(day_number=13).first()
    if day13:
        nara = Activity.query.filter_by(day_id=day13.id).filter(
            Activity.title.ilike('%nara%')).first()
        if nara:
            db.session.delete(nara)

    # Update Osaka accommodation: 1->2 nights
    osaka_accom = AccommodationLocation.query.filter_by(
        location_name='Osaka').first()
    if osaka_accom and osaka_accom.num_nights < 2:
        osaka_accom.num_nights = 2
        osaka_accom.check_out_date = dt_date(2026, 4, 18)

    # Update Tokyo Final Night dates
    tokyo_final = AccommodationLocation.query.filter_by(
        location_name='Tokyo Final Night').first()
    if tokyo_final:
        tokyo_final.check_in_date = dt_date(2026, 4, 18)
        tokyo_final.check_out_date = dt_date(2026, 4, 19)

    # Update Osaka location departure
    if osaka_loc:
        osaka_loc.departure_date = dt_date(2026, 4, 18)

    # Update Tokyo location departure
    tokyo_loc = Location.query.filter_by(name='Tokyo').first()
    if tokyo_loc:
        tokyo_loc.departure_date = dt_date(2026, 4, 19)

    # Update return flights: HND -> SFO -> CLE
    Flight.query.filter_by(direction='return').delete()
    db.session.add(Flight(direction='return', leg_number=1,
                          flight_number='TBD', airline='TBD',
                          route_from='HND', route_to='SFO',
                          depart_date=dt_date(2026, 4, 19),
                          depart_time='3:55 PM',
                          arrive_date=dt_date(2026, 4, 19),
                          booking_status='not_booked'))
    db.session.add(Flight(direction='return', leg_number=2,
                          flight_number='TBD', airline='TBD',
                          route_from='SFO', route_to='CLE',
                          depart_date=dt_date(2026, 4, 19),
                          arrive_time='10:13 PM',
                          arrive_date=dt_date(2026, 4, 19),
                          booking_status='not_booked'))

    # Update trip end date
    trip = Trip.query.first()
    if trip:
        trip.end_date = dt_date(2026, 4, 19)

    db.session.commit()
    print("Migration complete: trip extended to 16 days, Osaka 2 nights.")


def _seed_checklist_decisions(app):
    """Upgrade existing checklist items to decision type and seed options.
    Runs on every startup but skips if already done (idempotent)."""
    from models import ChecklistItem, ChecklistOption, AccommodationLocation

    # Check if already seeded (any decision items exist)
    if ChecklistItem.query.filter_by(item_type='decision').first():
        return

    # Map checklist titles to accommodation location names
    ACCOM_MAP = {
        'Book Takayama ryokan': 'Takayama Ryokan',
        'Book Piece Hostel Sanjo private room': 'Kyoto (3 nights)',
        'Book Minneapolis hotel': 'Minneapolis',
        'Book Tokyo hotel (Asakusa, 3 nights)': 'Tokyo (Asakusa area)',
        'Book Takayama budget night': 'Takayama Budget',
        'Book Kanazawa hotel (1 night)': 'Kanazawa',
        'Book Kyoto machiya (2 nights)': 'Kyoto Machiya',
        'Book Tokyo final night hotel': 'Tokyo Final Night',
        # Also match old-style titles from previous import
        'Book Takayama ryokan on Japanican.com': 'Takayama Ryokan',
        'Book Dormy Inn Asakusa (3 nights, Apr 6-8)': 'Tokyo (Asakusa area)',
        'Book Takayama budget night (Rickshaw Inn)': 'Takayama Budget',
        'Book Kaname Inn Kanazawa (1 night, Apr 11)': 'Kanazawa',
        'Book Kyoto machiya (Rinn or Airbnb, 2 nights)': 'Kyoto Machiya',
        'Book Toyoko Inn Shinagawa (1 night, Apr 17)': 'Tokyo Final Night',
        'Book Minneapolis hotel via united.com (apply $100 credit)': 'Minneapolis',
    }

    # Titles that should be decision items (booking/research)
    DECISION_TITLES = set(ACCOM_MAP.keys()) | {
        'Book Delta outbound CLE → MSP → HND',
        'Book Delta outbound CLE → MSP → HND ($638/pp)',
        'Reserve Nohi Bus (Takayama → Kanazawa)',
        'Reserve Nohi Bus (nouhibus.co.jp)',
        'Purchase 14-day JR Pass',
        'Purchase 14-day JR Pass at japanrailpass.net',
        'Book United award return NRT → LAX → CLE',
        'Reserve pocket WiFi or purchase eSIM',
        'Book TeamLab tickets',
        'Book TeamLab Planets tickets',
        'Register on Visit Japan Web',
        'Register on Visit Japan Web (vjw.digital.go.jp)',
        'Confirm travel insurance coverage',
        'Notify bank of Japan travel dates',
        'Download travel apps',
        'Download apps: Google Maps, Translate, Tabelog',
    }

    items = ChecklistItem.query.all()
    for item in items:
        if item.title in DECISION_TITLES:
            item.item_type = 'decision'
            # Link accommodation
            accom_name = ACCOM_MAP.get(item.title)
            if accom_name and not item.accommodation_location_id:
                loc = AccommodationLocation.query.filter_by(
                    location_name=accom_name).first()
                if loc:
                    item.accommodation_location_id = loc.id

    db.session.flush()

    # Seed ChecklistOption records for non-accommodation decision items
    OPTIONS_DATA = {
        'Reserve pocket WiFi or purchase eSIM': [
            ('Ubigi eSIM', 'Digital eSIM, instant activation', 'Works on any eSIM phone. No pickup needed.',
             'https://www.ubigi.com/en/japan-esim', '$15-30 / 2 weeks'),
            ('Airalo eSIM', 'Largest eSIM marketplace', 'More plan options, widely recommended.',
             'https://www.airalo.com/japan-esim', '$15-25 / 2 weeks'),
            ('Japan Wireless Pocket WiFi', 'Physical hotspot device', 'One device, both phones. Strongest signal.',
             'https://www.japan-wireless.com/', '$4-6/day (~$60-85)'),
            ('Sakura Mobile WiFi', 'Airport pickup at Haneda/Narita', 'Convenient pickup on arrival.',
             'https://www.sakuramobile.jp/wifi-rental/', '$5-7/day'),
        ],
        'Book TeamLab tickets': [
            ('TeamLab Planets (Toyosu)', 'Immersive water art museum', 'Walk through knee-deep water. Sells out 2-3 weeks ahead.',
             'https://planets.teamlab.art/tokyo/en/', '~\u00a53,800/pp'),
            ('TeamLab Borderless (Azabudai Hills)', 'New 2024 location', 'Larger, newer. Also sells out fast.',
             'https://www.teamlab.art/e/borderless-azabudai/', '~\u00a54,000/pp'),
        ],
        'Book TeamLab Planets tickets': [
            ('TeamLab Planets (Toyosu)', 'Immersive water art museum', 'Walk through knee-deep water. Sells out 2-3 weeks ahead.',
             'https://planets.teamlab.art/tokyo/en/', '~\u00a53,800/pp'),
            ('TeamLab Borderless (Azabudai Hills)', 'New 2024 location', 'Larger, newer. Also sells out fast.',
             'https://www.teamlab.art/e/borderless-azabudai/', '~\u00a54,000/pp'),
        ],
        'Confirm travel insurance coverage': [
            ('Chase Sapphire Trip Protection', 'Credit card benefit', 'Free if flights paid with Sapphire.',
             'https://www.chase.com/personal/credit-cards/sapphire/preferred', 'Free'),
            ('World Nomads', 'Comprehensive travel insurance', 'Covers medical, gear, adventure sports.',
             'https://www.worldnomads.com/', '~$50-80 / 2 weeks'),
            ('SafetyWing', 'Subscription travel insurance', 'Flexible monthly billing.',
             'https://safetywing.com/', '~$40 / 4 weeks'),
        ],
        'Purchase 14-day JR Pass': [
            ('Japan Rail Pass (Official)', 'Official site, buy exchange order', 'Most reliable. Ships to your address.',
             'https://japanrailpass.net/en/', '\u00a550,000/pp (14-day)'),
            ('JRailPass.com', 'Authorized reseller', 'Good alternative, ships voucher.',
             'https://www.jrailpass.com/', '~\u00a550,000/pp'),
            ('Buy at JR Station', 'Purchase on arrival', '~10% more expensive. No shipping needed.',
             'https://www.japanrailpass.net/en/purchase.html', '~\u00a555,000/pp'),
        ],
        'Purchase 14-day JR Pass at japanrailpass.net': [
            ('Japan Rail Pass (Official)', 'Official site, buy exchange order', 'Most reliable. Ships to your address.',
             'https://japanrailpass.net/en/', '\u00a550,000/pp (14-day)'),
            ('JRailPass.com', 'Authorized reseller', 'Good alternative, ships voucher.',
             'https://www.jrailpass.com/', '~\u00a550,000/pp'),
            ('Buy at JR Station', 'Purchase on arrival', '~10% more expensive. No shipping needed.',
             'https://www.japanrailpass.net/en/purchase.html', '~\u00a555,000/pp'),
        ],
        'Notify bank of Japan travel dates': [
            ('Chase Travel Notice', 'Set in Chase app', 'Prevents fraud blocks. Takes 30 seconds.',
             'https://www.chase.com/digital/login', 'Free'),
            ('ATM Strategy: 7-Eleven', 'Use 7-Eleven ATMs for cash', 'Most reliable for foreign cards.',
             'https://www.japan-guide.com/e/e2208.html', '~$3-5 fee/withdrawal'),
        ],
        'Download travel apps': [
            ('Google Translate (offline JP)', 'Camera reads menus/signs offline', 'Download Japanese offline pack before trip.',
             'https://translate.google.com/', 'Free'),
            ('Navitime for Japan Travel', 'Best train route app', 'Better than Google Maps for trains. Shows platform numbers.',
             'https://www.navitime.co.jp/inbound/', 'Free'),
            ('Suica in Apple Wallet', 'Tap to ride trains, pay at konbini', 'No physical card needed. Recharge in-app.',
             'https://support.apple.com/en-us/HT207154', 'Free (load \u00a5)'),
            ('Google Maps offline', 'Download offline maps', 'Download Tokyo, Kyoto, Takayama areas.',
             'https://support.google.com/maps/answer/6291838', 'Free'),
            ('Tabelog', 'Japan #1 restaurant ratings', '3.5+ is excellent. More accurate than Google reviews.',
             'https://tabelog.com/', 'Free'),
        ],
        'Download apps: Google Maps, Translate, Tabelog': [
            ('Google Translate (offline JP)', 'Camera reads menus/signs offline', 'Download Japanese offline pack before trip.',
             'https://translate.google.com/', 'Free'),
            ('Navitime for Japan Travel', 'Best train route app', 'Better than Google Maps for trains.',
             'https://www.navitime.co.jp/inbound/', 'Free'),
            ('Suica in Apple Wallet', 'Tap to ride trains', 'No physical card needed.',
             'https://support.apple.com/en-us/HT207154', 'Free'),
            ('Tabelog', 'Japan #1 restaurant ratings', '3.5+ is excellent.',
             'https://tabelog.com/', 'Free'),
        ],
        'Reserve Nohi Bus (Takayama → Kanazawa)': [
            ('Nohi Bus (Official)', '2hr 15min highway bus', 'JR Pass does NOT cover this. Reserve online.',
             'https://www.nouhibus.co.jp/english/', '~\u00a53,900/pp'),
        ],
        'Reserve Nohi Bus (nouhibus.co.jp)': [
            ('Nohi Bus (Official)', '2hr 15min highway bus', 'JR Pass does NOT cover this. Reserve online.',
             'https://www.nouhibus.co.jp/english/', '~\u00a53,900/pp'),
        ],
        'Register on Visit Japan Web': [
            ('Visit Japan Web', 'Pre-fill customs forms online', 'QR code at immigration. Skip paper forms.',
             'https://www.vjw.digital.go.jp/', 'Free'),
        ],
        'Register on Visit Japan Web (vjw.digital.go.jp)': [
            ('Visit Japan Web', 'Pre-fill customs forms online', 'QR code at immigration.',
             'https://www.vjw.digital.go.jp/', 'Free'),
        ],
    }

    for title, opts in OPTIONS_DATA.items():
        item = ChecklistItem.query.filter_by(title=title).first()
        if not item or item.item_type != 'decision':
            continue
        # Skip if options already exist
        if ChecklistOption.query.filter_by(checklist_item_id=item.id).first():
            continue
        for i, (name, desc, why, url, price) in enumerate(opts, 1):
            db.session.add(ChecklistOption(
                checklist_item_id=item.id, name=name, description=desc,
                why=why, url=url, price_note=price, sort_order=i,
            ))

    db.session.commit()
    app.logger.info('Checklist decisions seeded.')


def _fix_booking_urls(app):
    """Fix generic search URLs with property-specific pages (idempotent)."""
    from models import AccommodationOption
    URL_FIXES = {
        'https://www.japanican.com/en/hotel/list/?ar=190402&dt=20260409&ad=2&rm=1':
            'https://tanabe-ryokan.jp/english.html',
        'https://www.booking.com/searchresults.html?ss=Ryokan+Asunaro+Takayama':
            'https://www.yado-asunaro.com/en/',
        'https://kshouse.jp/':
            'https://kshouse.jp/takayama-oasis-e/index.html',
        'https://www.booking.com/searchresults.html?ss=Guesthouse+Tomaru+Takayama':
            'https://www.hidatakayama-guesthouse.com/',
        'https://www.booking.com/searchresults.html?ss=Hostel+Murasaki+Takayama':
            'https://www.booking.com/hotel/jp/zi-lu-guan.html',
        'https://sotetsu-hotels.com/en/fresa-inn/shinagawa-higashiguchi/':
            'https://sotetsu-hotels.com/en/grand-fresa/shinagawa-seaside/',
        'https://www.hotespa.net/hotels/gotanda/':
            'https://en.dormy-hotels.com/hotel/kanto/tokyo/12849/',
        'https://www.hotelmets.jp/en/shinagawa/':
            'https://www.hotelmets.jp/en/gotanda/',
        'https://www.kanameinn.com/':
            'https://kaname-inn.com/',
    }
    # Also fix property names that were wrong
    NAME_FIXES = {
        'Sotetsu Fresa Inn Shinagawa': 'Sotetsu Grand Fresa Shinagawa Seaside',
        'Dormy Inn Premium Gotanda': 'Dormy Inn Meguro Aobadai',
        'Hotel Mets Shinagawa': 'JR-East Hotel Mets Premier Gotanda',
    }
    changed = False
    for old_url, new_url in URL_FIXES.items():
        opt = AccommodationOption.query.filter_by(booking_url=old_url).first()
        if opt:
            opt.booking_url = new_url
            changed = True
    for old_name, new_name in NAME_FIXES.items():
        opt = AccommodationOption.query.filter_by(name=old_name).first()
        if opt:
            opt.name = new_name
            changed = True
    if changed:
        db.session.commit()
        app.logger.info('Fixed booking URLs and property names.')


def _seed_guide_urls(app):
    """Add travel guide URLs to locations (idempotent)."""
    from models import Location
    GUIDE_URLS = {
        'Tokyo': 'https://www.japan-guide.com/e/e2164.html',
        'Hakone': 'https://www.japan-guide.com/e/e5200.html',
        'Takayama': 'https://www.japan-guide.com/e/e5900.html',
        'Shirakawa-go': 'https://www.japan-guide.com/e/e5950.html',
        'Kanazawa': 'https://www.japan-guide.com/e/e2167.html',
        'Kyoto': 'https://www.japan-guide.com/e/e2158.html',
        'Osaka': 'https://www.japan-guide.com/e/e2157.html',
    }
    changed = False
    for name, url in GUIDE_URLS.items():
        loc = Location.query.filter_by(name=name).first()
        if loc and not loc.guide_url:
            loc.guide_url = url
            changed = True
    if changed:
        db.session.commit()
        app.logger.info('Seeded travel guide URLs for locations.')


def _seed_location_coords(app):
    """Populate latitude/longitude on Location records (idempotent)."""
    from models import Location
    COORDS = {
        'Minneapolis': (44.9778, -93.2650),
        'Tokyo': (35.6762, 139.6503),
        'Hakone': (35.2326, 139.1070),
        'Takayama': (36.1461, 137.2522),
        'Shirakawa-go': (36.2578, 136.9060),
        'Kanazawa': (36.5613, 136.6562),
        'Kyoto': (35.0116, 135.7681),
        'Osaka': (34.6937, 135.5023),
    }
    changed = False
    for name, (lat, lng) in COORDS.items():
        loc = Location.query.filter_by(name=name).first()
        if loc and loc.latitude is None:
            loc.latitude = lat
            loc.longitude = lng
            changed = True
    if changed:
        db.session.commit()


def _restructure_osaka(app):
    """Give Osaka its own night: reassign Day 13, split Kyoto Machiya,
    add Osaka accommodation + transport. Idempotent."""
    from models import Location, Day, AccommodationLocation, AccommodationOption, \
        TransportRoute, ChecklistItem
    from datetime import date

    # Guard: already done?
    if AccommodationLocation.query.filter_by(location_name='Osaka').first():
        return

    osaka_loc = Location.query.filter_by(name='Osaka').first()
    if not osaka_loc:
        return

    # Update Osaka location metadata
    osaka_loc.arrival_date = date(2026, 4, 16)
    osaka_loc.departure_date = date(2026, 4, 17)
    if osaka_loc.latitude is None:
        osaka_loc.latitude = 34.6937
        osaka_loc.longitude = 135.5023

    # 1. Reassign Day 13 to Osaka
    day13 = Day.query.filter_by(day_number=13).first()
    if day13:
        day13.location_id = osaka_loc.id
        day13.title = 'Osaka: Neon Chaos & Street Food'
        day13.is_buffer_day = False
        day13.theme = 'Full Day + Night'

    # 2. Shorten Kyoto Machiya from 2 nights to 1
    machiya = AccommodationLocation.query.filter_by(
        location_name='Kyoto Machiya').first()
    if machiya and machiya.num_nights == 2:
        machiya.check_out_date = date(2026, 4, 16)
        machiya.num_nights = 1
        machiya.quick_notes = 'Traditional townhouse. One night stay before Osaka.'

    # 3. Create Osaka AccommodationLocation
    max_sort = db.session.query(
        db.func.max(AccommodationLocation.sort_order)).scalar() or 7
    osaka_accom = AccommodationLocation(
        location_name='Osaka',
        check_in_date=date(2026, 4, 16),
        check_out_date=date(2026, 4, 17),
        num_nights=1,
        quick_notes='One wild night in Osaka. Book near Namba/Dotonbori for nightlife.',
        sort_order=max_sort + 1,
    )
    db.session.add(osaka_accom)
    db.session.flush()

    # 4. Osaka accommodation options
    options = [
        AccommodationOption(
            location_id=osaka_accom.id, rank=1,
            name='Cross Hotel Osaka',
            property_type='Boutique hotel',
            price_low=80, price_high=120,
            total_low=80, total_high=120,
            standout='Stylish design hotel in Shinsaibashi. Walking distance to Dotonbori and Amerikamura.',
            booking_url='https://www.crosshotel.com/osaka/en/',
            alt_booking_url='https://www.agoda.com/cross-hotel-osaka/hotel/osaka-jp.html',
            address='2-5-15 Shinsaibashisuji, Chuo-ku, Osaka',
        ),
        AccommodationOption(
            location_id=osaka_accom.id, rank=2,
            name='Dormy Inn Premium Namba',
            property_type='Business hotel',
            price_low=80, price_high=110,
            total_low=80, total_high=110,
            standout='Rooftop onsen + free late-night ramen. Same chain as Tokyo stay.',
            booking_url='https://www.hotespa.net/hotels/namba/',
            alt_booking_url='https://www.agoda.com/dormy-inn-premium-namba/hotel/osaka-jp.html',
            has_onsen=True, breakfast_included=True,
            address='2-1-7 Nipponbashi, Chuo-ku, Osaka',
        ),
        AccommodationOption(
            location_id=osaka_accom.id, rank=3,
            name='Hotel Monterey Grasmere Osaka',
            property_type='Hotel',
            price_low=70, price_high=100,
            total_low=70, total_high=100,
            standout='Connected to JR Namba station. European-inspired decor. Great location.',
            booking_url='https://www.hotelmonterey.co.jp/en/grasmere_osaka/',
            address='1-2-3 Minatomachi, Naniwa-ku, Osaka',
        ),
        AccommodationOption(
            location_id=osaka_accom.id, rank=4,
            name='First Cabin Namba',
            property_type='Capsule hotel',
            price_low=30, price_high=50,
            total_low=60, total_high=100,
            standout='Upscale capsule hotel — culture shock experience. Compact but clean private pods.',
            booking_url='https://first-cabin.jp/en/',
            address='Namba, Chuo-ku, Osaka',
        ),
    ]
    for opt in options:
        db.session.add(opt)

    # 5. Transport routes: Kyoto → Osaka, Osaka → Tokyo
    if not TransportRoute.query.filter_by(
            route_from='Kyoto', route_to='Osaka').first():
        db.session.add(TransportRoute(
            route_from='Kyoto', route_to='Osaka',
            transport_type='JR Special Rapid',
            duration='~30 min', jr_pass_covered=True,
            sort_order=100,
        ))
    if not TransportRoute.query.filter_by(
            route_from='Osaka', route_to='Tokyo').first():
        db.session.add(TransportRoute(
            route_from='Osaka', route_to='Tokyo',
            transport_type='Shinkansen', train_name='Hikari',
            duration='~3h', jr_pass_covered=True,
            cost_if_not_covered='¥13,870',
            sort_order=101,
        ))

    # 6. Checklist item for Osaka booking
    max_cl_sort = db.session.query(
        db.func.max(ChecklistItem.sort_order)).scalar() or 99
    cl_item = ChecklistItem(
        category='pre_departure_today',
        title='Book Osaka hotel (1 night, Apr 16)',
        is_completed=False,
        priority='high',
        sort_order=max_cl_sort + 1,
        item_type='decision',
        status='pending',
        accommodation_location_id=osaka_accom.id,
    )
    db.session.add(cl_item)
    db.session.commit()
    app.logger.info('Restructured itinerary: Osaka gets its own night.')


def _seed_osaka_and_substitutes(app):
    """Replace Day 13 activities with Osaka content, add substitutes
    across trip, and populate URLs on ticketed activities. Idempotent."""
    from models import Day, Activity

    # Guard: already seeded?
    day13 = Day.query.filter_by(day_number=13).first()
    if not day13:
        return
    if Activity.query.filter_by(day_id=day13.id).filter(
            Activity.title.contains('Dotonbori')).first():
        return

    # Delete old Day 13 activities
    Activity.query.filter_by(day_id=day13.id).delete()

    # New Osaka activities
    osaka_activities = [
        # Morning
        Activity(day_id=day13.id, title='Check out of Kyoto machiya',
                 time_slot='morning', sort_order=1,
                 description='Send bags to Osaka hotel via takkyubin or carry daypacks.'),
        Activity(day_id=day13.id, title='JR Special Rapid to Osaka',
                 time_slot='morning', sort_order=2,
                 description='Kyoto → Osaka in 30 min. JR Pass covered.',
                 jr_pass_covered=True),
        Activity(day_id=day13.id, title='Osaka Castle Park',
                 time_slot='morning', sort_order=3,
                 description='The exterior and park are stunning — skip the interior (modern concrete museum). Cherry blossoms around the moat.',
                 address='1-1 Osakajo, Chuo-ku, Osaka',
                 url='https://www.osakacastle.net/english/'),
        Activity(day_id=day13.id, title='Kuromon Market street food crawl',
                 time_slot='morning', sort_order=4,
                 description="Osaka's Kitchen — fresh sashimi, grilled seafood, tamagoyaki, mochi. Eat your way through.",
                 address='2-4-1 Nipponbashi, Chuo-ku, Osaka',
                 url='https://kuromon.com/en/'),
        # Afternoon
        Activity(day_id=day13.id, title='Shinsekai district + Tsutenkaku Tower',
                 time_slot='afternoon', sort_order=5,
                 description='Retro neighborhood frozen in time. Eat kushikatsu (deep-fried skewers) at a standing counter. Tower has views.',
                 cost_per_person=900, cost_note='¥900 tower entry',
                 address='1-18-6 Ebisuhigashi, Naniwa-ku, Osaka',
                 url='https://www.tsutenkaku.co.jp/en/'),
        Activity(day_id=day13.id, title='Spa World',
                 time_slot='afternoon', sort_order=6,
                 description='Giant themed onsen with Egyptian, Roman, and Japanese baths across multiple floors. Total culture shock. Swimsuits NOT allowed — everyone is naked.',
                 cost_per_person=1500, cost_note='¥1,500 entry',
                 is_optional=True,
                 address='3-4-24 Ebisuhigashi, Naniwa-ku, Osaka',
                 url='https://www.spaworld.co.jp/english/'),
        Activity(day_id=day13.id, title='Den Den Town',
                 time_slot='afternoon', sort_order=7,
                 description="Osaka's Akihabara — retro game arcades, anime shops, maid cafes, figure stores. More authentic than Tokyo's version.",
                 is_optional=True,
                 address='Nipponbashi, Naniwa-ku, Osaka'),
        # Evening
        Activity(day_id=day13.id, title='Dotonbori Night Walk',
                 time_slot='evening', sort_order=8,
                 description='The iconic neon-lit canal strip. Giant Glico Running Man sign, mechanical crab, overwhelming sensory overload. Peak energy after dark.',
                 address='Dotonbori, Chuo-ku, Osaka'),
        Activity(day_id=day13.id, title='Takoyaki crawl — Wanaka, Kukuru, Aizuya',
                 time_slot='evening', sort_order=9,
                 description='Try octopus balls from 3+ different vendors and compare. ¥500-800 per serving. Each shop has a different style.',
                 cost_per_person=600, cost_note='~¥500-800 per serving'),
        Activity(day_id=day13.id, title='Hozenji Yokocho',
                 time_slot='evening', sort_order=10,
                 description='Lantern-lit stone alley hidden behind Dotonbori. Splash water on the moss-covered Fudo Myo-o statue for good luck. 60+ tiny restaurants.',
                 address='1-2 Nanba, Chuo-ku, Osaka'),
        # Night
        Activity(day_id=day13.id, title='Ura-Namba bar crawl',
                 time_slot='night', sort_order=11,
                 description='Tight alleyways packed with local izakayas east of Namba station. This is where locals drink — not tourists. Cheap drinks, warm atmosphere, elbow-to-elbow.',
                 address='Sennichimae, Chuo-ku, Osaka'),
        Activity(day_id=day13.id, title='Amerikamura (American Village)',
                 time_slot='night', sort_order=12,
                 description='Youth culture hub. Record bars, vintage shops, street art. Try Bar Nayuta for jazz/vinyl vibes or Club Joule for dancing.',
                 is_optional=True,
                 address='Amerikamura, Chuo-ku, Osaka'),
        Activity(day_id=day13.id, title='Check into Osaka hotel',
                 time_slot='night', sort_order=13,
                 description='Late check-in — most hotels allow until midnight.'),
    ]
    for a in osaka_activities:
        db.session.add(a)

    # Day 13 substitutes: Nara & Relaxed Kyoto
    subs_day13 = [
        Activity(day_id=day13.id,
                 title='Nara day trip — deer park, Todai-ji',
                 is_substitute=True, substitute_for='Osaka day',
                 sort_order=90,
                 description='Friendly bowing deer in the park, massive Buddha statue in Todai-ji. More chill than Osaka. 45 min from Kyoto by JR.',
                 url='https://www.japan-guide.com/e/e4100.html'),
        Activity(day_id=day13.id,
                 title='Relaxed Kyoto — Nijo Castle + tea ceremony',
                 is_substitute=True, substitute_for='Osaka day',
                 sort_order=91,
                 description='If you need a low-energy day. Nijo Castle (¥800) has nightingale floors that squeak when you walk. Book a traditional tea ceremony.',
                 cost_per_person=800,
                 url='https://nijo-jocastle.city.kyoto.lg.jp/en/'),
    ]
    for a in subs_day13:
        db.session.add(a)

    # Substitutes across other days
    _add_substitute_activities()

    # URLs for existing ticketed activities
    _seed_activity_urls()

    db.session.commit()
    app.logger.info('Seeded Osaka activities, substitutes, and activity URLs.')


def _add_substitute_activities():
    """Add substitute/alternative activities across the trip."""
    from models import Day, Activity

    subs = [
        # Day 4 (Tokyo) — alt for Golden Gai
        (4, 'Robot Restaurant (Shinjuku Kabukicho)',
         'Golden Gai', 'night',
         'Bikini-clad performers riding neon robots with lasers and taiko drums. Pure sensory overload. Book online — sells out.',
         'https://www.shinjuku-robot.com/pc/en/', 8000),
        # Day 4 (Tokyo) — alt for Harajuku
        (4, 'Shimokitazawa — bohemian neighborhood',
         'Harajuku', 'afternoon',
         "Tokyo's Brooklyn. Vintage shops, live music venues, indie cafes, thrift stores. More authentic than tourist-heavy Harajuku.",
         None, None),
        # Day 4 (Tokyo) — Yozakura cherry blossoms
        (4, 'Yozakura at Chidorigafuchi — night cherry blossoms by rowboat',
         'Evening plans', 'evening',
         'Rent a rowboat on the Imperial Palace moat under illuminated cherry blossoms. One of the most magical experiences in Tokyo during hanami season. Boats until 8:30 PM.',
         'https://visit-chiyoda.tokyo/en/spots/detail/31', 800),
        # Day 10 (Kyoto) — alt for Philosopher's Path
        (10, 'Fushimi sake brewery district — tastings',
         "Philosopher's Path", 'afternoon',
         "Beyond the shrine — explore the sake breweries nearby. Gekkeikan Okura Sake Museum offers tastings. Buy sake directly from the source.",
         'https://www.gekkeikan.co.jp/english/kyotofushimi/', 400),
        # Day 11 (Kyoto) — alt for Arashiyama
        (11, 'Kurama-Kibune mountain villages + onsen',
         'Arashiyama', 'morning',
         'Scenic mountain train to ancient villages north of Kyoto. Hike between Kurama Temple and Kibune Shrine. Natural hot spring at Kurama Onsen.',
         'https://www.japan-guide.com/e/e3927.html', None),
        # Day 14 (Tokyo) — alt for TeamLab
        (14, 'Akihabara deep dive — maid cafes, arcades, themed bars',
         'TeamLab', 'afternoon',
         "Electric Town — multi-story arcades, maid cafes where costumed waitresses serve you, anime mega-stores. Total culture shock. Go to @home Cafe for the full maid cafe experience.",
         None, None),
    ]

    for day_num, title, sub_for, slot, desc, url, cost in subs:
        day = Day.query.filter_by(day_number=day_num).first()
        if not day:
            continue
        # Skip if already exists
        if Activity.query.filter_by(day_id=day.id, title=title).first():
            continue
        a = Activity(
            day_id=day.id, title=title,
            is_substitute=True, substitute_for=sub_for,
            time_slot=slot, description=desc, url=url,
            cost_per_person=cost,
            sort_order=90,
        )
        db.session.add(a)


def _seed_activity_urls():
    """Add URLs to existing ticketed/notable activities (idempotent)."""
    from models import Activity
    URL_MAP = {
        'TeamLab Planets': 'https://planets.teamlab.art/tokyo/en/',
        'Hakone Loop': 'https://www.hakonenavi.jp/en/',
        'Senso-ji Temple': 'https://www.senso-ji.jp/english/',
        'Meiji Shrine': 'https://www.meijijingu.or.jp/en/',
        'Fushimi Inari': 'https://inari.jp/en/',
        'Kiyomizu-dera': 'https://www.kiyomizudera.or.jp/en/',
        'Kinkaku-ji': 'https://www.shokoku-ji.jp/en/kinkakuji/',
        'Hiroshima Peace Memorial': 'https://hpmmuseum.jp/?lang=eng',
        'Tenzan Tohji-kyo': 'https://www.tenzan.jp/en/',
        'Hida Folk Village': 'https://www.hidanosato-tpo.jp/english/',
        'Monkey Park Iwatayama': 'https://www.monkeypark.jp/en/',
        'Takayama Jinya': 'https://jinya.gifu.jp/en/',
        'Bamboo Grove': 'https://www.japan-guide.com/e/e3912.html',
        'Tenryu-ji': 'https://www.tenryuji.com/en/',
    }
    for title_substr, url in URL_MAP.items():
        acts = Activity.query.filter(
            Activity.title.contains(title_substr),
            Activity.url.is_(None)
        ).all()
        for a in acts:
            a.url = url


def _fix_checklist_consistency(app):
    """Fix duplicate checklist items and ensure accommodation links are correct.
    Idempotent: skips if sentinel exists."""
    from models import ChecklistItem, AccommodationLocation

    if ChecklistItem.query.filter_by(title='__checklist_v2_fixed').first():
        return

    with app.app_context():
        # Remove duplicate "Book Minneapolis hotel" entries (keep the one with accom link)
        dupes = ChecklistItem.query.filter(
            ChecklistItem.title.contains('Minneapolis hotel')
        ).all()
        linked = [d for d in dupes if d.accommodation_location_id]
        unlinked = [d for d in dupes if not d.accommodation_location_id]
        if linked and unlinked:
            for item in unlinked:
                from models import ChecklistOption
                ChecklistOption.query.filter_by(checklist_item_id=item.id).delete()
                db.session.delete(item)

        # Ensure the linked Minneapolis item exists; create if missing
        mpls_loc = AccommodationLocation.query.filter_by(
            location_name='Minneapolis').first()
        if mpls_loc:
            mpls_item = ChecklistItem.query.filter_by(
                accommodation_location_id=mpls_loc.id).first()
            if not mpls_item:
                mpls_item = ChecklistItem(
                    category='pre_departure_week',
                    title='Book Minneapolis hotel',
                    item_type='decision',
                    status='pending',
                    accommodation_location_id=mpls_loc.id,
                    sort_order=5,
                )
                db.session.add(mpls_item)

        # Sentinel to prevent re-running
        db.session.add(ChecklistItem(
            category='packing_helpful', title='__checklist_v2_fixed',
            item_type='task', status='completed', is_completed=True,
            sort_order=9999,
        ))
        db.session.commit()


def _revise_itinerary_activities(app):
    """Comprehensive revision of itinerary activities — fixes sparse days,
    wrong time slots, incorrect optional flags, and missing activities.
    Idempotent: skips if sentinel activity already exists."""
    from models import Day, Activity

    # Guard: check if already revised
    if Activity.query.filter(Activity.title.contains('ACTIVATE 14-Day JR Pass')).first():
        return

    def _replace_day(day_num, activities_data):
        """Delete non-substitute activities for a day and insert new ones."""
        day = Day.query.filter_by(day_number=day_num).first()
        if not day:
            return
        Activity.query.filter_by(day_id=day.id).filter(
            Activity.is_substitute == False  # noqa: E712
        ).delete()
        for i, a_data in enumerate(activities_data):
            a = Activity(
                day_id=day.id,
                title=a_data['title'],
                description=a_data.get('desc'),
                time_slot=a_data.get('slot'),
                start_time=a_data.get('start_time'),
                cost_per_person=a_data.get('cost'),
                cost_note=a_data.get('cost_note'),
                is_optional=a_data.get('optional', False),
                jr_pass_covered=a_data.get('jr', False),
                address=a_data.get('address'),
                url=a_data.get('url'),
                sort_order=i + 1,
            )
            db.session.add(a)

    # ── Day 3: Arrive Tokyo ──
    _replace_day(3, [
        {'title': 'Pick up Welcome Suica IC card',
         'slot': 'afternoon', 'desc': 'At Haneda Terminal 3 JR East Travel Service Center. Load ¥3,000 initially. Expires 28 days from purchase.'},
        {'title': 'Activate eSIM or pick up pocket WiFi',
         'slot': 'afternoon', 'desc': 'At airport counter. Essential for navigation and translation.'},
        {'title': 'Keikyu Line to Asakusa',
         'slot': 'afternoon', 'desc': '~30 min, ~¥500. Use IC card.', 'cost': 500},
        {'title': 'Check into Dormy Inn Asakusa',
         'slot': 'afternoon', 'desc': 'Drop bags and freshen up. Rooftop onsen + free late-night ramen available.'},
        {'title': 'Light dinner nearby',
         'slot': 'evening', 'optional': True,
         'desc': 'Walk-up ramen shop (¥800-1,200), conveyor belt sushi, or grab onigiri + bento from 7-Eleven.'},
        {'title': 'Senso-ji Temple at night',
         'slot': 'evening', 'optional': True,
         'desc': 'Beautifully illuminated, almost empty at night. Completely different atmosphere than daytime. 5-min walk from Dormy Inn.',
         'address': '2-3-1 Asakusa, Taito-ku, Tokyo',
         'url': 'https://www.senso-ji.jp/english/'},
        {'title': 'Rooftop onsen bath at Dormy Inn',
         'slot': 'night', 'optional': True,
         'desc': 'Soak away the long journey. Open late.'},
        {'title': 'Free late-night ramen at Dormy Inn',
         'slot': 'night', 'optional': True,
         'desc': 'Served ~9:30 PM. A beloved Dormy Inn perk.'},
    ])

    # ── Day 5: Hakone Day Trip ──
    _replace_day(5, [
        {'title': 'ACTIVATE 14-Day JR Pass',
         'slot': 'morning', 'start_time': '~8:00 AM',
         'desc': 'Tokyo Station JR Ticket Office (Marunouchi side). Bring exchange voucher + passports. This activates your pass for all JR trains through Apr 21.'},
        {'title': 'Shinkansen Tokyo → Odawara',
         'slot': 'morning', 'jr': True,
         'desc': '~35 min on Kodama or Hikari. First use of your JR Pass!'},
        {'title': 'Buy Hakone Free Pass at Odawara Station',
         'slot': 'morning', 'cost': 6000, 'cost_note': '¥6,000/person 2-day pass',
         'desc': 'Covers all Hakone Loop transport (train, cable car, ropeway, pirate ship, bus). Separate from JR Pass. Buy at Odawara Station Hakone Tozan counter.',
         'url': 'https://www.hakonenavi.jp/en/'},
        {'title': 'Hakone Loop: Switchback Train to Gora',
         'slot': 'morning',
         'desc': 'Odawara → Gora on the Hakone Tozan Railway. Scenic mountain railway that zigzags up steep slopes.'},
        {'title': 'Hakone Loop: Cable Car to Owakudani',
         'slot': 'morning',
         'desc': 'Volcanic valley with steam vents, sulfur smell, dramatic moonscape. Try the famous black eggs cooked in volcanic steam — supposedly add 7 years to your life!'},
        {'title': 'Hakone Loop: Ropeway over mountains',
         'slot': 'afternoon',
         'desc': 'Aerial gondola with panoramic mountain views. On clear days, Mt. Fuji visible. Continues to Togendai on Lake Ashi.'},
        {'title': 'Hakone Loop: Lake Ashi Pirate Ship',
         'slot': 'afternoon',
         'desc': 'Cruise across the lake on a replica pirate ship. Mt. Fuji views across the water on clear days. Very scenic.'},
        {'title': 'Hakone Open-Air Museum',
         'slot': 'afternoon', 'optional': True,
         'desc': 'Sculptures + Picasso collection in a stunning mountain setting. Worth the detour if time allows.',
         'url': 'https://www.hakone-oam.or.jp/en/'},
        {'title': 'Day-use onsen: Tenzan Tohji-kyo',
         'slot': 'afternoon', 'cost': 1300, 'cost_note': '¥1,300 entry',
         'desc': 'Excellent outdoor baths in a forest setting near Hakone-Yumoto. The perfect end to the Hakone loop.',
         'url': 'https://www.tenzan.jp/en/',
         'address': '208 Yumoto-chaya, Hakone-machi'},
        {'title': 'Shinkansen Odawara → Tokyo',
         'slot': 'evening', 'jr': True,
         'desc': '~35 min return trip. JR Pass covered.'},
        {'title': 'Arrange takkyubin luggage forwarding',
         'slot': 'evening', 'cost': 2000, 'cost_note': '~¥2,000/bag',
         'desc': 'IMPORTANT: At Dormy Inn front desk, send big bags to your Kyoto hotel. Arrives in 1-2 days. Pack daypacks only for the Alps leg (Takayama/Kanazawa = 3 nights). Ask for "takkyubin".'},
        {'title': 'Last free late-night ramen at Dormy Inn',
         'slot': 'night', 'optional': True,
         'desc': 'Your final night at Dormy Inn. Enjoy the ramen one last time.'},
    ])

    # ── Day 6: Tokyo → Takayama ──
    _replace_day(6, [
        {'title': 'Check out of Dormy Inn Asakusa',
         'slot': 'morning',
         'desc': 'Bags already sent to Kyoto via takkyubin — travel with daypacks only!'},
        {'title': 'Shinkansen Tokyo → Nagoya',
         'slot': 'morning', 'jr': True,
         'desc': '~1h 40min on Hikari. Use Hikari, not Nozomi (JR Pass does not cover Nozomi).'},
        {'title': 'JR Hida Limited Express: Nagoya → Takayama',
         'slot': 'morning', 'jr': True,
         'desc': "~2h 20min. One of Japan's most scenic train rides — the train winds through mountain gorges, crosses rivers, passes through tiny villages. Sit by the window."},
        {'title': 'Check into ryokan',
         'slot': 'afternoon',
         'desc': 'Traditional Japanese inn. Green tea on arrival, yukata robe provided. Tatami room with futon.'},
        {'title': 'Explore Sanmachi Suji historic district',
         'slot': 'afternoon',
         'desc': 'Preserved Edo-era merchant streets — dark wooden buildings, narrow alleys, willow trees. Craft shops, small galleries, pickled vegetable shops.'},
        {'title': 'Sake brewery tastings',
         'slot': 'afternoon', 'cost': 300, 'cost_note': '¥200-500 for tasting flights',
         'desc': 'Look for sugidama (cedar balls) hanging outside — they indicate new sake. Many breweries offer tastings of a dozen varieties. You will buy bottles.'},
        {'title': 'Takayama Jinya',
         'slot': 'afternoon',
         'desc': 'Beautifully preserved Edo-period government building with a rice storehouse. One of the last surviving magistrate offices in Japan.',
         'url': 'https://jinya.gifu.jp/en/'},
        {'title': 'Multi-course kaiseki dinner at ryokan',
         'slot': 'evening',
         'desc': '8-12 courses of seasonal Hida cuisine served in your room or a private dining area. Hida beef featured — possibly as sashimi, grilled, or in hoba miso. This is culinary art.'},
        {'title': 'Onsen bath at ryokan',
         'slot': 'night',
         'desc': 'Indoor and/or outdoor baths depending on ryokan. Ask about kashikiri (private bath reservation) if you want to soak together.'},
    ])

    # ── Day 7: Full Day Takayama ──
    _replace_day(7, [
        {'title': 'Miyagawa Morning Market',
         'slot': 'morning', 'start_time': '~6:00 AM',
         'desc': 'Local farmers selling produce, pickles, crafts, and miso along the riverside. Try apple butter, mountain vegetable pickles, handmade crafts.'},
        {'title': 'Ryokan breakfast (included)',
         'slot': 'morning',
         'desc': 'Traditional Japanese breakfast — rice, miso soup, grilled fish, pickles, egg. Included with ryokan stay.'},
        {'title': 'Check out of ryokan',
         'slot': 'morning', 'desc': 'Say goodbye to your tatami room.'},
        {'title': 'Check into K\'s House Takayama',
         'slot': 'morning',
         'desc': 'Budget accommodation for the second night. Drop your bags.'},
        {'title': 'Hida Folk Village (Hida no Sato)',
         'slot': 'morning',
         'desc': 'Open-air museum with 30+ traditional buildings on a hillside overlooking the Japanese Alps. Same gassho-zukuri architecture as Shirakawa-go.',
         'url': 'https://www.hidanosato-tpo.jp/english/'},
        {'title': 'Hida beef sushi for lunch',
         'slot': 'afternoon', 'cost': 750, 'cost_note': '~¥600-900 for 2 pieces',
         'desc': 'Slices of seared wagyu on rice, eaten by hand. Or try hoba miso (beef and vegetables grilled on a magnolia leaf over charcoal).'},
        {'title': 'Sanmachi Suji & more sake breweries',
         'slot': 'afternoon',
         'desc': 'Return to the old town for more exploration. Different breweries, different sake. Cherry blossoms should be at or near peak bloom!'},
        {'title': 'Takayama Festival Floats Museum (Yatai Kaikan)',
         'slot': 'afternoon', 'optional': True,
         'desc': 'Elaborate festival floats with intricate mechanical puppets (karakuri). The annual festival is one of Japan\'s most famous.'},
        {'title': 'Lantern-lit old town night walk',
         'slot': 'evening',
         'desc': 'Walk the old streets at night — lantern-lit, nearly empty, completely different atmosphere than daytime. This is the opposite of Tokyo, and that\'s the point.'},
        {'title': 'Izakaya dinner — Hida beef',
         'slot': 'evening',
         'desc': 'Try Hida beef in another preparation: sukiyaki, shabu-shabu, or sushi again. Local izakayas serve generous portions at reasonable prices.'},
    ])

    # ── Day 8: Takayama → Shirakawa-go → Kanazawa ──
    _replace_day(8, [
        {'title': 'Check out of K\'s House Takayama',
         'slot': 'morning', 'desc': 'Bags packed, ready for a big travel day.'},
        {'title': 'Nohi Bus: Takayama → Shirakawa-go',
         'slot': 'morning', 'cost': 2800, 'cost_note': '¥2,800/person',
         'desc': '~50 min. MUST be pre-booked at nouhibus.co.jp — sells out in cherry blossom season!',
         'url': 'https://www.nouhibus.co.jp/english/'},
        {'title': 'Shirakawa-go UNESCO Village',
         'slot': 'morning',
         'desc': 'Walk among gassho-zukuri farmhouses — steep thatched roofs built 250+ years ago to handle heavy snowfall. A storybook mountain village.'},
        {'title': 'Wada House',
         'slot': 'morning', 'cost': 300, 'cost_note': '~¥300 entry',
         'desc': 'Largest preserved farmhouse, open to public. See the massive timber frame structure from inside.'},
        {'title': 'Hike to observation deck',
         'slot': 'morning',
         'desc': 'THE iconic panoramic photo of the trip — the entire village spread out below with mountains behind.'},
        {'title': 'Village lunch',
         'slot': 'morning', 'optional': True,
         'desc': 'Try soba noodles or gohei-mochi (grilled rice cakes with walnut-miso glaze) at a village restaurant.'},
        {'title': 'Nohi Bus: Shirakawa-go → Kanazawa',
         'slot': 'afternoon', 'cost': 2800, 'cost_note': '¥2,800/person',
         'desc': '~1h 15min. Second pre-booked bus segment.'},
        {'title': 'Check into Kaname Inn Tatemachi',
         'slot': 'afternoon',
         'desc': 'Boutique hotel with a vinyl record music bar downstairs. Drop bags, freshen up.'},
        {'title': 'Kenrokuen Garden',
         'slot': 'afternoon',
         'desc': "One of Japan's Top 3 gardens! Possible late cherry blossoms — the garden's trees bloom slightly later. Take your time — this garden rewards slow wandering.",
         'url': 'https://www.pref.ishikawa.jp/siro-niwa/kenrokuen/e/'},
        {'title': 'Kanazawa Castle Park',
         'slot': 'afternoon',
         'desc': 'Historic castle grounds right next to Kenrokuen. Stone walls and gates with mountain backdrop.'},
        {'title': 'Higashi Chaya Geisha District',
         'slot': 'evening',
         'desc': 'Atmospheric wooden teahouses lit by warm lanterns at dusk. You may hear shamisen music drifting from inside. Beautifully preserved — feels like stepping into old Japan.'},
        {'title': 'Fresh seafood dinner at Omicho Market',
         'slot': 'evening',
         'desc': "Kanazawa is on the Sea of Japan coast = exceptional sushi and sashimi. Try the kaisendon (sashimi rice bowl). Snow crab if in season, buri (yellowtail), nodoguro (blackthroat seaperch).",
         'address': 'Omicho Market, Kanazawa'},
        {'title': 'Sai River evening walk',
         'slot': 'night', 'optional': True,
         'desc': 'Quiet and atmospheric riverside stroll to end the day.'},
    ])

    # ── Day 9: Kanazawa → Kyoto ──
    _replace_day(9, [
        {'title': '21st Century Museum of Contemporary Art',
         'slot': 'morning',
         'desc': 'Free outdoor installations, ticketed indoor exhibits. The "Swimming Pool" installation — you look down through glass at people who appear underwater — is iconic.',
         'url': 'https://www.kanazawa21.jp/en/'},
        {'title': 'D.T. Suzuki Museum',
         'slot': 'morning',
         'desc': 'Serene, minimalist museum of Zen Buddhism with a stunning reflective water garden. One of the most peaceful spaces in Japan. Perfect for quiet contemplation.'},
        {'title': 'Nagamachi Samurai District',
         'slot': 'morning',
         'desc': 'Historic samurai residences with earthen walls and narrow stone-lined canals. Restored Nomura Samurai House is open to visitors.'},
        {'title': 'Gold leaf ice cream at Hakuichi',
         'slot': 'morning', 'cost': 900, 'cost_note': '~¥900',
         'desc': 'An entire sheet of gold leaf on soft-serve ice cream. Kanazawa produces 99% of Japan\'s gold leaf. Instagram-worthy and delicious.'},
        {'title': 'Last Omicho Market meal',
         'slot': 'afternoon', 'optional': True,
         'desc': 'Final chance for Kanazawa seafood. Or try Kanazawa curry — the city has its own distinct curry style.'},
        {'title': 'Hokuriku Shinkansen: Kanazawa → Tsuruga',
         'slot': 'afternoon', 'jr': True,
         'desc': '~35-45 min. Use Hakutaka (faster) or Tsurugi (local shinkansen). JR Pass covered.'},
        {'title': 'Thunderbird Express: Tsuruga → Kyoto',
         'slot': 'afternoon', 'jr': True,
         'desc': '~75-80 min. Timed connection at Tsuruga — same platform or adjacent, 5-10 min to transfer. Total Kanazawa→Kyoto: ~2h with transfer.'},
        {'title': 'Check into Piece Hostel Sanjo',
         'slot': 'evening',
         'desc': 'Boutique hostel in central Kyoto near Keihan Line (useful for Fushimi Inari tomorrow).'},
        {'title': 'Stroll along Kamo River',
         'slot': 'evening',
         'desc': 'Locals sit along the riverbanks at dusk. One of Kyoto\'s signature scenes. Very peaceful.'},
        {'title': 'Pontocho Alley dinner',
         'slot': 'evening',
         'desc': 'Narrow, lantern-lit alley of restaurants running parallel to the Kamo River. Many have kamogawa terraces — outdoor riverside deck seating. Find a spot with river views. ¥2,000-5,000/person.'},
    ])

    # ── Day 12: Hiroshima & Miyajima ──
    _replace_day(12, [
        {'title': 'Shinkansen Kyoto → Hiroshima',
         'slot': 'morning', 'jr': True, 'start_time': '~7:30 AM',
         'desc': '~1h 45min on Hikari. Depart early to maximize your day. Use Hikari, not Nozomi (JR Pass).'},
        {'title': 'Hiroshima Peace Memorial Park & Museum',
         'slot': 'morning', 'cost': 200, 'cost_note': '¥200 museum entry',
         'desc': 'Deeply moving. The museum tells individual human stories — letters, belongings, shadows burned into stone. Take your time. It\'s emotional. It\'s important.',
         'url': 'https://hpmmuseum.jp/?lang=eng',
         'address': '1-2 Nakajimacho, Naka-ku, Hiroshima'},
        {'title': 'A-Bomb Dome',
         'slot': 'morning',
         'desc': 'UNESCO World Heritage Site — the only structure left standing near the hypocenter, preserved as a memorial. A short walk from the museum within Peace Park.',
         'address': '1-10 Otemachi, Naka-ku, Hiroshima'},
        {'title': 'Hiroshima-style okonomiyaki lunch',
         'slot': 'afternoon',
         'desc': 'Savory pancake layered with noodles, cabbage, pork, egg, sauce. Different from Osaka style (Osaka mixes; Hiroshima layers). Okonomimura building near Peace Park has dozens of stalls.',
         'address': 'Okonomimura, 5-13 Shintenchi, Naka-ku, Hiroshima'},
        {'title': 'JR train to Miyajimaguchi + JR Ferry to Miyajima',
         'slot': 'afternoon', 'jr': True,
         'desc': 'Train from Hiroshima station (~30 min), then JR ferry (~10 min). Both covered by JR Pass. Take the JR ferry, not the competing line.'},
        {'title': 'Floating Itsukushima Torii Gate',
         'slot': 'afternoon',
         'desc': 'The iconic red gate standing in the water (restored 2022). At high tide it appears to float; at low tide you can walk to it. Check tide tables.',
         'address': 'Miyajima, Hatsukaichi, Hiroshima'},
        {'title': 'Itsukushima Shrine & shopping street',
         'slot': 'afternoon',
         'desc': 'Walk the charming shopping street to the shrine. Try momiji manju (maple leaf-shaped cakes). Friendly wild deer roam the island — they\'ll walk right up to you.'},
        {'title': 'JR Ferry + Shinkansen Hiroshima → Kyoto',
         'slot': 'evening', 'jr': True,
         'desc': 'Return ferry + train to Hiroshima station, then Shinkansen back to Kyoto. Arrive ~7-8 PM. Quiet evening in your machiya.'},
    ])

    # ── Day 14: Kyoto → Tokyo ──
    _replace_day(14, [
        {'title': 'Last Kyoto exploration & omiyage shopping',
         'slot': 'morning',
         'desc': 'Pick up omiyage (souvenir gifts) — Kyoto is known for matcha sweets, yatsuhashi (cinnamon mochi), pickles. Revisit a favorite spot.'},
        {'title': 'Check out of Kyoto accommodation',
         'slot': 'morning', 'desc': 'Grab bags, say goodbye to Kyoto.'},
        {'title': 'Shinkansen Kyoto → Tokyo',
         'slot': 'afternoon', 'jr': True,
         'desc': '~2h 15min on Hikari. Sit on the RIGHT side (seats A/B) for Mt. Fuji views around Shin-Fuji station (~1 hour in). Clear days: stunning. Cloudy: nothing. It\'s luck.',
         'cost_note': 'Buy an ekiben (station bento box) for lunch on the train — a beloved Japanese tradition'},
        {'title': 'Check into Toyoko Inn Shinagawa',
         'slot': 'afternoon',
         'desc': '1 night only — departure from Narita tomorrow.'},
        {'title': 'TeamLab Planets',
         'slot': 'evening',
         'desc': 'Immersive digital art where you wade through water and walk through light. Located in Toyosu area, ~30 min from Shinagawa. Book at teamlab.art in advance — sells out! Expect 60-90 min inside.',
         'url': 'https://planets.teamlab.art/tokyo/en/'},
        {'title': 'Final dinner — make it special',
         'slot': 'night',
         'desc': 'Sushi omakase (chef\'s choice) in Ginza/Shinagawa area (¥3,000-8,000/person), or return to Omoide Yokocho for one last smoky yakitori session, or a quiet ramen shop — full circle from your first night.'},
    ])

    # ── Day 15: Departure ──
    _replace_day(15, [
        {'title': 'Tsukiji Outer Market farewell breakfast',
         'slot': 'morning', 'start_time': '~7:00 AM',
         'desc': 'Fresh sushi, tamago (egg omelette), grilled scallops, tamagoyaki. Get there early to maximize time.',
         'address': 'Tsukiji 4-chome, Chuo-ku, Tokyo'},
        {'title': 'Last-minute shopping',
         'slot': 'morning', 'optional': True,
         'desc': 'Don Quijote (discount megastore) for snack souvenirs and quirky gifts. Uniqlo for Japanese-exclusive items.'},
        {'title': 'Check out of Toyoko Inn',
         'slot': 'morning', 'desc': 'Grab bags. Leave Shinagawa by ~12:00 PM.'},
        {'title': 'Narita Express: Shinagawa → Narita Airport',
         'slot': 'afternoon', 'jr': True, 'start_time': '~12:00 PM',
         'desc': '~50-60 min. Comfortable reserved-seat train with luggage space. Covered by JR Pass (still active through Apr 21).'},
        {'title': 'Narita Airport omiyage shops & last meal',
         'slot': 'afternoon', 'optional': True,
         'desc': 'Last chance for Tokyo Banana, Royce chocolate, Kit Kat flavors, wagashi. Restaurant floor has surprisingly good food. Tax-free shopping available with passport.'},
    ])

    # ── Days 1 & 2: Minor fixes ──
    # Day 1: fix time slots
    day1 = Day.query.filter_by(day_number=1).first()
    if day1:
        afternoon_titles = ['Stone Arch Bridge', 'North Loop', 'First Avenue',
                            'Brewery tour', 'Dinner']
        for a in Activity.query.filter_by(day_id=day1.id).all():
            if a.is_substitute:
                continue
            for title_frag in afternoon_titles:
                if title_frag.lower() in a.title.lower():
                    a.time_slot = 'afternoon'
                    break
            # Fix dinner noise
            if a.title.startswith('Dinner:') or a.title.startswith('Dinner —'):
                a.title = 'Dinner in North Loop'
                a.description = 'North Loop area has excellent restaurants at reasonable prices.'
                a.time_slot = 'evening'

    # Day 2: remove noise activities
    day2 = Day.query.filter_by(day_number=2).first()
    if day2:
        noise_fragments = ['walk the aisle', 'nonstop flight', '12-13h']
        for a in Activity.query.filter_by(day_id=day2.id).all():
            if a.is_substitute:
                continue
            for frag in noise_fragments:
                if frag.lower() in a.title.lower():
                    db.session.delete(a)
                    break
            # Fix truncated breakfast title
            if 'Grab breakfast' in a.title and 'If time allows' in a.title:
                a.title = 'Grab breakfast near the hotel'
                a.time_slot = 'morning'

    db.session.commit()
    app.logger.info('Revised itinerary activities for all days.')


def _migrate_14day_restructure(app):
    """Restructure trip from 16 to 14 days: remove Minneapolis, update flights
    to confirmed bookings, remove Tokyo Final Night. Idempotent."""
    import sqlite3
    from datetime import date as dt_date
    from models import (db, Day, Activity, Flight, Location, Trip,
                        AccommodationLocation, AccommodationOption,
                        ChecklistItem, ChecklistOption)

    # Guard: already applied if Day 1 title contains CLEVELAND->TOKYO travel
    day1 = Day.query.filter_by(day_number=1).first()
    if day1 and 'CLEVELAND' in (day1.title or '') and 'TOKYO' in (day1.title or ''):
        return

    # Also guard: if no Day 16 exists and Day 1 isn't Minneapolis, skip
    if not Day.query.filter_by(day_number=16).first() and day1 and 'MINNEAPOLIS' not in (day1.title or '').upper():
        return

    print("Running data migration: restructure trip to 14 days...")

    # Use raw sqlite3 for day renumbering (avoids ORM unique constraint issues)
    db_path = app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')

    import sqlite3
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # 1. Delete Day 1 (Minneapolis) activities and day
    c.execute("SELECT id FROM day WHERE day_number=1")
    d1 = c.fetchone()
    if d1:
        c.execute("DELETE FROM activity WHERE day_id=?", (d1[0],))
        c.execute("DELETE FROM day WHERE id=?", (d1[0],))

    # 2. Repurpose Day 2 as Day 1 (CLE->DTW->HND travel)
    c.execute("SELECT id FROM day WHERE day_number=2")
    d2 = c.fetchone()
    if d2:
        d2_id = d2[0]
        c.execute("DELETE FROM activity WHERE day_id=?", (d2_id,))
        c.execute("""UPDATE day SET day_number=1, date='2026-04-05',
                     title='TRAVEL DAY -- CLEVELAND -> TOKYO'
                     WHERE id=?""", (d2_id,))
        for title, slot, order, desc in [
            ('Depart CLE 10:30 AM -- Delta DL5392 to Detroit', 'morning', 1,
             'Endeavor Air regional jet. ~56 min flight. Confirmation: HBPF75'),
            ('Arrive DTW 11:26 AM -- layover', 'morning', 2,
             '2h 39min layover at Detroit Metropolitan. Grab lunch.'),
            ('Depart DTW 2:05 PM -- Delta DL275 to Tokyo Haneda', 'afternoon', 3,
             'Boeing 767-400ER. ~13h 10min flight. Main Basic (E class). Seats assigned at gate.'),
        ]:
            c.execute("""INSERT INTO activity (day_id, title, time_slot, sort_order, description,
                         is_optional, is_substitute, jr_pass_covered)
                         VALUES (?, ?, ?, ?, ?, 0, 0, 0)""",
                      (d2_id, title, slot, order, desc))

    # 3. Shift Days 3-14 down by 1
    for old_num in range(3, 15):
        c.execute("UPDATE day SET day_number=? WHERE day_number=?", (old_num - 1, old_num))

    # 4. Repurpose Day 15 as Day 14 (Departure from Osaka)
    c.execute("SELECT id FROM day WHERE day_number=15")
    d15 = c.fetchone()
    if d15:
        d15_id = d15[0]
        c.execute("DELETE FROM activity WHERE day_id=?", (d15_id,))
        c.execute("""UPDATE day SET day_number=14, date='2026-04-18',
                     title='DEPARTURE DAY -- OSAKA -> HOME'
                     WHERE id=?""", (d15_id,))
        for title, slot, order, desc, jr in [
            ('Early checkout from Osaka hotel', 'morning', 1,
             'Pack up. Check out by 8 AM for the journey home.', 0),
            ('Shinkansen Osaka -> Shinagawa (~2h 30min)', 'morning', 2,
             'Hikari shinkansen. JR Pass covered. Last ekiben lunch on the train!', 1),
            ('Transfer to Haneda Airport', 'afternoon', 3,
             'Keikyu Line from Shinagawa to Haneda Terminal 3 (~15 min, ~500 yen).', 0),
            ('Haneda Airport -- last shopping & check-in', 'afternoon', 4,
             'Arrive by 1:30 PM for 3:50 PM departure. Tax-free omiyage shops in terminal.', 0),
            ('United UA876 HND 3:50 PM -> SFO 9:35 AM', 'afternoon', 5,
             'Boeing 777-200. Seats: Jacob 52B, Jessica 52A (window pair, no third seat). Confirmation: I91ZHJ', 0),
            ('SFO layover (4h 45min)', 'evening', 6,
             'Arrive 9:35 AM same day (cross dateline). Long layover -- grab food, stretch legs.', 0),
            ('United UA1470 SFO 2:20 PM -> CLE 10:13 PM', 'evening', 7,
             'Seats: Jacob 37C, Jessica 37B. Confirmation: I91ZHJ. Welcome home!', 0),
        ]:
            c.execute("""INSERT INTO activity (day_id, title, time_slot, sort_order, description,
                         is_optional, is_substitute, jr_pass_covered)
                         VALUES (?, ?, ?, ?, ?, 0, 0, ?)""",
                      (d15_id, title, slot, order, desc, jr))

    # 5. Delete Day 16
    c.execute("SELECT id FROM day WHERE day_number=16")
    d16 = c.fetchone()
    if d16:
        c.execute("DELETE FROM activity WHERE day_id=?", (d16[0],))
        c.execute("DELETE FROM day WHERE id=?", (d16[0],))

    # 6. Update flights
    c.execute("DELETE FROM flight")
    for vals in [
        ('outbound', 1, 'DL5392', 'Delta (Endeavor Air)', 'CLE', 'DTW',
         '2026-04-05', '10:30 AM', '2026-04-05', '11:26 AM', '56 min',
         'CRJ-900', 'cash', '$775.00/person', 'booked', 'HBPF75',
         'Main Basic (E class). Seats assigned at gate. Operated by Endeavor Air.'),
        ('outbound', 2, 'DL275', 'Delta', 'DTW', 'HND',
         '2026-04-05', '2:05 PM', '2026-04-06', '4:15 PM', '13h 10min',
         'Boeing 767-400ER', 'cash', '$775.00/person', 'booked', 'HBPF75',
         'Main Basic (E class). Seats assigned at gate.'),
        ('return', 1, 'UA876', 'United', 'HND', 'SFO',
         '2026-04-18', '3:50 PM', '2026-04-18', '9:35 AM', '9h 45min',
         'Boeing 777-200', 'miles', '61,800 miles + $49.03/person', 'booked', 'I91ZHJ',
         'Jessica: seat 52A / Jacob: seat 52B (window pair, 2-seat section)'),
        ('return', 2, 'UA1470', 'United', 'SFO', 'CLE',
         '2026-04-18', '2:20 PM', '2026-04-18', '10:13 PM', '4h 53min',
         None, 'miles', '61,800 miles + $49.03/person', 'booked', 'I91ZHJ',
         'Jessica: seat 37B / Jacob: seat 37C'),
    ]:
        c.execute("""INSERT INTO flight (direction, leg_number, flight_number, airline,
                     route_from, route_to, depart_date, depart_time, arrive_date, arrive_time,
                     duration, aircraft, cost_type, cost_amount, booking_status,
                     confirmation_number, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", vals)

    # 7. Remove Minneapolis
    c.execute("SELECT id FROM accommodation_location WHERE location_name='Minneapolis'")
    mpls = c.fetchone()
    if mpls:
        c.execute("DELETE FROM accommodation_option WHERE location_id=?", (mpls[0],))
        c.execute("SELECT id FROM checklist_item WHERE accommodation_location_id=?", (mpls[0],))
        for ci in c.fetchall():
            c.execute("DELETE FROM checklist_option WHERE checklist_item_id=?", (ci[0],))
            c.execute("DELETE FROM checklist_item WHERE id=?", (ci[0],))
        c.execute("DELETE FROM accommodation_location WHERE id=?", (mpls[0],))
    c.execute("SELECT id FROM checklist_item WHERE title LIKE '%Minneapolis%' OR title LIKE '%MSP%'")
    for ci in c.fetchall():
        c.execute("DELETE FROM checklist_option WHERE checklist_item_id=?", (ci[0],))
        c.execute("DELETE FROM checklist_item WHERE id=?", (ci[0],))
    c.execute("DELETE FROM location WHERE name='Minneapolis'")

    # 8. Remove Tokyo Final Night accommodation
    c.execute("SELECT id FROM accommodation_location WHERE location_name='Tokyo Final Night'")
    tfn = c.fetchone()
    if tfn:
        c.execute("DELETE FROM accommodation_option WHERE location_id=?", (tfn[0],))
        c.execute("UPDATE checklist_item SET accommodation_location_id=NULL WHERE accommodation_location_id=?", (tfn[0],))
        c.execute("DELETE FROM accommodation_location WHERE id=?", (tfn[0],))
    c.execute("SELECT id FROM checklist_item WHERE title LIKE '%Tokyo final night%'")
    for ci in c.fetchall():
        c.execute("DELETE FROM checklist_option WHERE checklist_item_id=?", (ci[0],))
        c.execute("DELETE FROM checklist_item WHERE id=?", (ci[0],))

    # 9. Update checklist items for flights
    c.execute("""UPDATE checklist_item SET title='Book Delta outbound CLE -> DTW -> HND',
                 is_completed=1, status='completed' WHERE title LIKE '%Delta outbound%'""")
    c.execute("""UPDATE checklist_item SET title='Book United return HND -> SFO -> CLE',
                 is_completed=1, status='completed'
                 WHERE title LIKE '%United%return%' OR title LIKE '%United award return%'""")

    # 10. Update location/trip dates
    c.execute("UPDATE location SET departure_date='2026-04-18' WHERE name='Tokyo'")
    c.execute("UPDATE location SET departure_date='2026-04-18' WHERE name='Osaka'")
    c.execute("""UPDATE trip SET start_date='2026-04-05', end_date='2026-04-18',
                 notes='14-day cherry blossom trip. Cleveland -> Tokyo -> Alps -> Kyoto -> Osaka -> Home'
                 WHERE id=1""")

    conn.commit()
    conn.close()

    # Clear SQLAlchemy session to pick up raw SQL changes
    db.session.expire_all()
    print("Migration complete: trip restructured to 14 days (Apr 5-18).")


def _migrate_consolidate_kyoto(app):
    """Merge Kyoto Machiya into Kyoto (3 nights) -> Kyoto (4 nights). Idempotent."""
    import sqlite3
    from models import db, AccommodationLocation

    machiya = AccommodationLocation.query.filter_by(location_name='Kyoto Machiya').first()
    if not machiya:
        return

    print("Running data migration: consolidate Kyoto accommodations...")
    db_path = app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    c.execute("""UPDATE accommodation_location SET
        location_name='Kyoto (4 nights)', num_nights=4, check_out_date='2026-04-16',
        quick_notes='Kyoto April = hardest booking in Japan. Private rooms sell out months ahead. 4 nights covers all Kyoto days + Hiroshima day trip.'
        WHERE location_name='Kyoto (3 nights)'""")

    machiya_id = machiya.id
    c.execute("DELETE FROM accommodation_option WHERE location_id=?", (machiya_id,))
    c.execute("SELECT id FROM checklist_item WHERE accommodation_location_id=?", (machiya_id,))
    for ci in c.fetchall():
        c.execute("DELETE FROM checklist_option WHERE checklist_item_id=?", (ci[0],))
        c.execute("DELETE FROM checklist_item WHERE id=?", (ci[0],))
    c.execute("SELECT id FROM checklist_item WHERE title LIKE '%machiya%'")
    for ci in c.fetchall():
        c.execute("DELETE FROM checklist_option WHERE checklist_item_id=?", (ci[0],))
        c.execute("DELETE FROM checklist_item WHERE id=?", (ci[0],))
    c.execute("DELETE FROM accommodation_location WHERE id=?", (machiya_id,))

    conn.commit()
    conn.close()
    db.session.expire_all()
    print("Migration complete: Kyoto consolidated to 4 nights.")


def _migrate_add_addresses_and_cleanup_transport(app):
    """Add verified addresses to all accommodation options, clean up outdated transport routes. Idempotent."""
    import sqlite3
    from models import AccommodationOption

    # Idempotent check: if first Tokyo hotel already has address, skip
    opt = AccommodationOption.query.filter_by(name='Onyado Nono Asakusa Natural Hot Springs').first()
    if opt and opt.address:
        return

    print("Running data migration: adding hotel addresses and cleaning transport routes...")
    db_path = app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # === Hotel addresses (all verified via official sites / booking platforms) ===
    addresses = {
        'Onyado Nono Asakusa Natural Hot Springs': '2-7-20 Asakusa, Taito-ku, Tokyo 111-0032, Japan',
        'THE GATE HOTEL Kaminarimon by Hulic': '2-16-11 Kaminarimon, Taito-ku, Tokyo 111-0034, Japan',
        'Richmond Hotel Premier Asakusa International': '2-6-7 Asakusa, Taito-ku, Tokyo 111-0032, Japan',
        'Dormy Inn Express Asakusa': '1-3-4 Hanakawado, Taito-ku, Tokyo 111-0033, Japan',
        'Nui. Hostel & Bar Lounge': '2-14-13 Kuramae, Taito-ku, Tokyo, Japan',
        'Tanabe Ryokan': '58 Aioi-machi, Takayama-shi, Gifu 506-0014, Japan',
        'Sumiyoshi Ryokan': '4-21 Honmachi, Takayama-shi, Gifu 506-0011, Japan',
        'Oyado Koto no Yume': '6-11 Hanasato-machi, Takayama-shi, Gifu 506-0026, Japan',
        'Rickshaw Inn': '54 Suehiro-cho, Takayama-shi, Gifu 506-0016, Japan',
        'J-Hoppers Takayama': '5-52 Nada-cho, Takayama-shi, Gifu 506-0021, Japan',
        'Piece Hostel Sanjo': '531 Asakura-cho, Tominokoji Sanjo-kudaru, Nakagyo-ku, Kyoto 604-8074, Japan',
        'Hotel Grand Bach Kyoto Select': '363 Naramonocho, Shijodori Teramachi Nishi-iru, Shimogyo-ku, Kyoto 600-8004, Japan',
        'The Celestine Kyoto Gion': '572 Komatsu-cho, Yasaka-dori Higashioji Nishi-iru, Higashiyama-ku, Kyoto 605-0933, Japan',
        'Hotel Ethnography Gion Shinmonzen': '219-2 Nishino-cho, Shinmonzen-dori, Higashiyama-ku, Kyoto 605-0088, Japan',
        'Len Kyoto Kawaramachi': '709-3 Uematsu-cho, Kawaramachi-dori Matsubara-sagaru, Shimogyo-ku, Kyoto 600-8028, Japan',
        'Cross Hotel Osaka': '2-5-15 Shinsaibashi-suji, Chuo-ku, Osaka-shi, Osaka 542-0085, Japan',
        'Dormy Inn Premium Namba': '2-14-23 Shimanouchi, Chuo-ku, Osaka-shi, Osaka 542-0082, Japan',
        'Dotonbori Hotel': '2-3-25 Dotonbori, Chuo-ku, Osaka-shi, Osaka 542-0071, Japan',
        'Holiday Inn Osaka Namba': '5-15 Soemon-cho, Chuo-ku, Osaka-shi, Osaka 542-0084, Japan',
        'MIMARU Osaka Namba Station': '3-6-24 Nipponbashi, Naniwa-ku, Osaka-shi, Osaka 556-0005, Japan',
    }

    for name, addr in addresses.items():
        c.execute("UPDATE accommodation_option SET address=? WHERE name=?", (addr, name))

    # === Clean up outdated transport routes ===
    # Remove Kanazawa-related routes (no longer on itinerary)
    c.execute("DELETE FROM transport_route WHERE route_from='Shirakawa-go' AND route_to='Kanazawa'")
    c.execute("DELETE FROM transport_route WHERE route_from='Kanazawa' AND route_to='Tsuruga'")
    c.execute("DELETE FROM transport_route WHERE route_from='Tsuruga' AND route_to='Kyoto'")

    # Remove old Kyoto->Tokyo and Osaka->Tokyo routes (we go Osaka->Shinagawa now)
    c.execute("DELETE FROM transport_route WHERE route_from='Kyoto' AND route_to='Tokyo'")
    c.execute("DELETE FROM transport_route WHERE route_from='Osaka' AND route_to='Tokyo'")

    # Remove Shinagawa->Narita (we fly from Haneda now)
    c.execute("DELETE FROM transport_route WHERE route_from='Shinagawa' AND route_to='Narita Airport'")

    # Update Shirakawa-go route: goes to Kyoto via bus+train now
    c.execute("""UPDATE transport_route SET route_to='Kanazawa/Kyoto',
                 notes='Bus to Kanazawa (~1h15), then Thunderbird/Shinkansen to Kyoto (~2h30)'
                 WHERE route_from='Takayama' AND route_to='Shirakawa-go'""")

    # Add Shinagawa->Haneda route if not exists
    c.execute("SELECT id FROM transport_route WHERE route_from='Shinagawa' AND route_to='Haneda Airport'")
    if not c.fetchone():
        c.execute("""INSERT INTO transport_route (route_from, route_to, transport_type, train_name,
                     duration, jr_pass_covered, cost_if_not_covered, sort_order)
                     VALUES ('Shinagawa', 'Haneda Airport', 'Keikyu Line', NULL, '~15 min', 0, '~500 yen', 20)""")

    # Add Osaka->Shinagawa shinkansen if not exists (departure day route)
    c.execute("SELECT id FROM transport_route WHERE route_from='Osaka' AND route_to='Shinagawa'")
    if not c.fetchone():
        c.execute("""INSERT INTO transport_route (route_from, route_to, transport_type, train_name,
                     duration, jr_pass_covered, cost_if_not_covered, sort_order)
                     VALUES ('Osaka', 'Shinagawa', 'Shinkansen', 'Hikari', '~2h 30min', 1, '¥13,870', 19)""")

    conn.commit()
    conn.close()
    db.session.expire_all()
    print("Migration complete: hotel addresses added, transport routes cleaned up.")


def _migrate_data_cleanup(app):
    """Fix stale data: Takayama nights, location_ids, Kanazawa refs. Idempotent."""
    import sqlite3
    from models import AccommodationLocation

    accom = AccommodationLocation.query.filter_by(location_name='Takayama').first()
    if accom and '3 nights' in (accom.quick_notes or ''):
        return  # Already applied

    print("Running data migration: cleanup stale data...")
    db_path = app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # Fix Takayama quick_notes: 2 nights -> 3 nights
    c.execute("""UPDATE accommodation_location SET
                 quick_notes='Central Takayama, 3 nights. Mix of ryokan and budget options.'
                 WHERE location_name='Takayama'""")

    # Fix Day 1 (travel day): clear orphaned location_id
    c.execute("UPDATE day SET location_id=NULL WHERE day_number=1 AND location_id IS NOT NULL")

    # Fix Day 14 (departure from Osaka): set to Osaka location
    c.execute("""UPDATE day SET location_id=(SELECT id FROM location WHERE name='Osaka')
                 WHERE day_number=14""")

    # Remove Kanazawa location if no days reference it
    c.execute("SELECT COUNT(*) FROM day WHERE location_id=(SELECT id FROM location WHERE name='Kanazawa')")
    if c.fetchone()[0] == 0:
        c.execute("DELETE FROM location WHERE name='Kanazawa'")

    # Link transport routes to their days
    route_day_map = {
        ('Tokyo', 'Odawara'): 4, ('Tokyo', 'Nagoya'): 5,
        ('Nagoya', 'Takayama'): 5, ('Takayama', 'Kanazawa/Kyoto'): 8,
        ('Kyoto', 'Hiroshima'): 11, ('Hiroshima', 'Miyajima'): 11,
        ('Kyoto', 'Osaka'): 12, ('Osaka', 'Shinagawa'): 14,
        ('Shinagawa', 'Haneda Airport'): 14,
    }
    for (rf, rt), day_num in route_day_map.items():
        c.execute("SELECT id FROM day WHERE day_number=?", (day_num,))
        day_row = c.fetchone()
        if day_row:
            c.execute("UPDATE transport_route SET day_id=? WHERE route_from=? AND route_to=? AND day_id IS NULL",
                      (day_row[0], rf, rt))

    # Fix activity descriptions mentioning Kanazawa
    c.execute("SELECT id, title, description FROM activity WHERE title LIKE '%Kanazawa%' OR description LIKE '%Kanazawa%'")
    for aid, title, desc in c.fetchall():
        if desc and 'Kanazawa' in desc:
            new_desc = desc.replace('Takayama/Kanazawa = 3 nights', 'Takayama = 3 nights').replace('Kanazawa', 'Kyoto')
            c.execute("UPDATE activity SET description=? WHERE id=?", (new_desc, aid))
        if title and 'Kanazawa' in title:
            new_title = title.replace('Kanazawa', 'Kyoto')
            c.execute("UPDATE activity SET title=? WHERE id=?", (new_title, aid))

    conn.commit()
    conn.close()
    db.session.expire_all()
    print("Migration complete: stale data cleaned up.")


def _migrate_enrich_activities(app):
    """Enrich activities with addresses, categories, why reasoning, merge description-activities,
    and add missing transport routes. Idempotent."""
    import sqlite3
    from models import Activity

    # Guard: if Senso-ji already has a category, skip
    act = Activity.query.filter(Activity.title.contains('Senso-ji Temple')).first()
    if act and act.category:
        return

    print("Running data migration: enriching activities with addresses, categories, and reasoning...")
    db_path = app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # === 1. Add addresses to activities that don't have them ===
    address_map = {
        # Tokyo
        'Senso-ji Temple at night': '2-3-1 Asakusa, Taito-ku, Tokyo',
        'Senso-ji Temple': '2-3-1 Asakusa, Taito-ku, Tokyo',
        'Tokyo Skytree': '1-1-2 Oshiage, Sumida-ku, Tokyo',
        'Meiji Shrine': '1-1 Yoyogi Kamizono-cho, Shibuya-ku, Tokyo',
        'Harajuku': 'Takeshita-dori, Jingumae, Shibuya-ku, Tokyo',
        'Shibuya Crossing': 'Shibuya Scramble Crossing, Shibuya-ku, Tokyo',
        'Golden Gai': 'Kabukicho 1-chome, Shinjuku-ku, Tokyo',
        'Omoide Yokocho': '1-2 Nishishinjuku, Shinjuku-ku, Tokyo',
        'Ueno Zoo': '9-83 Uenokoen, Taito-ku, Tokyo',
        'Hakone Open-Air Museum': '1121 Ninotaira, Hakone-machi, Ashigarashimo-gun, Kanagawa',
        # Takayama
        'Sanmachi Suji': 'Sanmachi, Takayama-shi, Gifu',
        'Takayama Jinya': '1-5 Hachiken-machi, Takayama-shi, Gifu',
        'Miyagawa Morning Market': 'Miyagawa Asaichi, Shimosannomachi, Takayama-shi, Gifu',
        'Hida Folk Village': '1-590 Kamiokamoto-machi, Takayama-shi, Gifu',
        'Takayama Festival Floats Museum': '178 Sakura-machi, Takayama-shi, Gifu',
        'Hida beef sushi': 'Sanmachi area, Takayama-shi, Gifu',
        # Shirakawa-go
        'Shirakawa-go UNESCO Village': 'Ogimachi, Shirakawa-mura, Ono-gun, Gifu',
        'Wada House': 'Ogimachi 997, Shirakawa-mura, Ono-gun, Gifu',
        # Kyoto
        'Fushimi Inari': '68 Fukakusa Yabunouchimachi, Fushimi-ku, Kyoto',
        'Kiyomizu-dera': '1-294 Kiyomizu, Higashiyama-ku, Kyoto',
        'Kinkaku-ji': '1 Kinkakuji-cho, Kita-ku, Kyoto',
        'Bamboo Grove': 'Sagaogurayama Tabuchiyama-cho, Ukyo-ku, Kyoto',
        'Tenryu-ji': '68 Saga-Tenryuji Susukinobaba-cho, Ukyo-ku, Kyoto',
        'Togetsukyo Bridge': 'Arashiyama, Ukyo-ku, Kyoto',
        'Monkey Park Iwatayama': '8 Arashiyama Genrokuzancho, Nishikyo-ku, Kyoto',
        'Nishiki Market': 'Nishikikoji-dori, Nakagyo-ku, Kyoto',
        'Pontocho Alley': 'Pontocho, Nakagyo-ku, Kyoto',
        'Stroll along Kamo River': 'Kamo River, Kyoto',
        'Hanamikoji Street': 'Hanamikoji-dori, Higashiyama-ku, Kyoto',
        # Nara
        'Nara day trip': 'Nara Park, Nara',
        # Osaka
        'Morning coffee': 'near hotel, Osaka',
        'Shinsaibashi shopping': 'Shinsaibashi-suji, Chuo-ku, Osaka',
        'Umeda Sky Building': '1-1-88 Oyodonaka, Kita-ku, Osaka',
    }

    for title_fragment, addr in address_map.items():
        c.execute("""UPDATE activity SET address=? WHERE title LIKE ? AND address IS NULL""",
                  (addr, f'%{title_fragment}%'))

    # === 2. Add categories to all activities ===
    category_rules = [
        # Temples & shrines
        ('temple', ['Senso-ji', 'Meiji Shrine', 'Fushimi Inari', 'Kiyomizu-dera',
                     'Kinkaku-ji', 'Tenryu-ji', 'Todai-ji', 'Itsukushima Shrine',
                     'Peace Memorial', 'A-Bomb Dome', 'Nijo Castle', 'Kurama']),
        # Food & dining
        ('food', ['dinner', 'lunch', 'breakfast', 'ramen', 'takoyaki', 'okonomiyaki',
                  'sushi', 'Kuromon Market', 'Nishiki Market', 'Omicho Market',
                  'kaiseki', 'beef', 'Pontocho', 'konbini', 'ice cream', 'coffee',
                  'Omoide Yokocho', 'Miyagawa Morning Market', 'food crawl',
                  'sake brewery', 'ekiben', 'omiyage', 'mochi']),
        # Nightlife & entertainment
        ('nightlife', ['Golden Gai', 'bar crawl', 'Ura-Namba', 'Robot Restaurant',
                       'Amerikamura', 'night walk', 'izakaya']),
        # Shopping
        ('shopping', ['Nakamise-dori', 'Harajuku', 'Den Den Town', 'Shinsaibashi',
                      'shopping', 'Don Quijote', 'Shimokitazawa']),
        # Nature & outdoors
        ('nature', ['Bamboo Grove', 'Monkey Park', 'Hakone Loop', 'Lake Ashi',
                    'Ropeway', 'onsen', 'Kamo River', 'hike', 'observation deck',
                    'Philosopher', 'Togetsukyo', 'Deer', 'Sai River',
                    'cherry blossom', 'Yozakura', 'Osaka Castle Park']),
        # Culture & museums
        ('culture', ['Museum', 'TeamLab', 'Jinya', 'Hida Folk Village', 'Shibuya Crossing',
                     'Wada House', 'Shirakawa-go', 'Sanmachi', 'tea ceremony',
                     'Geisha', 'Hanamikoji', 'Hozenji', 'Kenrokuen',
                     'Nagamachi', '21st Century', 'D.T. Suzuki', 'Shinsekai',
                     'Tsutenkaku', 'Spa World', 'Skytree', 'Ueno Zoo',
                     'Float', 'Yatai', 'lantern']),
        # Transit/logistics
        ('transit', ['Shinkansen', 'Express', 'Check out', 'Check in', 'ACTIVATE',
                     'Keikyu', 'Narita Express', 'JR ', 'Bus:', 'Nohi Bus',
                     'Transfer', 'Ferry', 'Train to', 'Line to',
                     'luggage', 'takkyubin', 'Suica', 'eSIM', 'WiFi',
                     'Pick up', 'Arrange', 'Buy Hakone']),
    ]

    for category, patterns in category_rules:
        for pattern in patterns:
            c.execute("""UPDATE activity SET category=? WHERE category IS NULL
                        AND title LIKE ?""", (category, f'%{pattern}%'))

    # Catch-all: tag remaining untagged as 'culture'
    c.execute("UPDATE activity SET category='culture' WHERE category IS NULL AND is_substitute=0")
    c.execute("UPDATE activity SET category='culture' WHERE category IS NULL AND is_substitute=1")

    # === 3. Merge "description activities" into parent descriptions ===
    # These are activities that are actually descriptions of the previous activity
    desc_merges = [
        # Day 3/4 (Tokyo)
        ("Tokyo's oldest temple (founded 628 AD)", 'Senso-ji Temple'),
        ("Nakamise-dori: 250m of traditional shops", 'Senso-ji Temple'),
        ("Walk to Tokyo Skytree for panoramic city views", 'Senso-ji Temple'),
        ("Massive Shinto shrine hidden in a 170-acre forest", 'Meiji Shrine'),
        ("Cover charge:", 'Golden Gai'),
        ("Grab a stool elbow-to-elbow", 'Omoide Yokocho'),
        # Day 10 (Kyoto south)
        ("Mid-morning: Kiyomizu-dera", 'GO EARLY: Fushimi Inari'),
        ("Walk down Sannenzaka", 'GO EARLY: Fushimi Inari'),
        ("Lunch: In the Higashiyama area", 'GO EARLY: Fushimi Inari'),
        ("Afternoon: Philosopher's Path", 'GO EARLY: Fushimi Inari'),
        ("2 km path lined with cherry trees", 'GO EARLY: Fushimi Inari'),
        ("Be respectful", 'Walk Hanamikoji'),
        ("Best time: 5:30", 'Walk Hanamikoji'),
        ("Dinner: Gion area restaurant", 'Walk along the Kamo River'),
        # Day 11 (Kyoto north)
        ("Entry:", 'Kinkaku-ji'),
        ("Lunch: Riverside restaurant", 'Togetsukyo Bridge'),
        ("Afternoon: Monkey Park", 'Togetsukyo Bridge'),
        ("Try: dashimaki tamago", 'Nishiki Market'),
        ("Good for souvenir shopping", 'Nishiki Market'),
    ]

    for desc_fragment, parent_fragment in desc_merges:
        # Find the description activity
        c.execute("SELECT id, title, description, day_id FROM activity WHERE title LIKE ?",
                  (f'{desc_fragment}%',))
        desc_acts = c.fetchall()
        for desc_act in desc_acts:
            desc_id, desc_title, desc_desc, desc_day_id = desc_act
            # Find parent on same day
            c.execute("SELECT id, description FROM activity WHERE day_id=? AND title LIKE ?",
                      (desc_day_id, f'%{parent_fragment}%'))
            parent = c.fetchone()
            if parent:
                parent_id, parent_desc = parent
                # Append this title to parent description
                addition = desc_title
                if desc_desc:
                    addition += f' — {desc_desc}'
                new_desc = f'{parent_desc}\n\n{addition}' if parent_desc else addition
                c.execute("UPDATE activity SET description=? WHERE id=?", (new_desc, parent_id))
                c.execute("DELETE FROM activity WHERE id=?", (desc_id,))

    # === 4. Add "why" reasoning for key choices and alternatives ===
    why_updates = [
        ('Senso-ji Temple at night', 'Best at night — beautifully lit, almost no crowds. The nighttime visit is more atmospheric than daytime (when it is packed with tour groups). Visit again in morning if you want the full Nakamise shopping experience.'),
        ('Golden Gai', 'vs Omoide Yokocho: Golden Gai = tiny themed bars (fits 5-8 people each), ¥300-1000 cover. Omoide Yokocho = open-air yakitori grills, no cover. Do both — they are 5 min apart. Start with Omoide Yokocho for food, end at Golden Gai for drinks.'),
        ('Omoide Yokocho', 'Pair with Golden Gai. Better for dinner (grilled meats, yakitori). No cover charge. More casual than Golden Gai. Go first while hungry.'),
        ('Robot Restaurant', 'SUBSTITUTE for evening plans. Pure spectacle — not cultural, not subtle. Book online, ¥8000/person. Worth it if you want something completely outrageous. Skip if you prefer authentic nightlife.'),
        ('Yozakura at Chidorigafuchi', 'SUBSTITUTE for evening. Only available during cherry blossom season (late March-mid April). Rowboats on Imperial Palace moat under lit cherry trees — potentially the most memorable moment of the trip. Check bloom forecast.'),
        ('Shimokitazawa', 'SUBSTITUTE for Harajuku. Less touristy, more authentic. Vintage shops, live music, indie cafes. Good if Harajuku feels too crowded/commercial.'),
        ('Hakone Open-Air Museum', 'Optional detour during Hakone loop. Adds 1-2 hours. Best if you love art. Skip if you prefer more onsen time or are tired from the loop.'),
        ('Day-use onsen: Tenzan Tohji-kyo', 'Best day-use onsen in Hakone area. Outdoor forest baths. Skip if you want more time at Lake Ashi or the museum. ¥1,300 is reasonable for the quality.'),
        ('Fushimi Inari', 'GO AT 6:30 AM — the vermillion gates are nearly empty before 7 AM. By 9 AM it becomes a dense crowd. The full hike is 2-3 hours but you can turn back at the halfway shrine (45 min up). Dawn light through the torii is magical.'),
        ('Kinkaku-ji', 'Worth the hype despite crowds. Best light is 9-10 AM (golden reflection strongest). ¥500 entry. The "ticket" is a beautiful calligraphy charm. Quick visit — 30-45 min is enough.'),
        ('Bamboo Grove', 'Go early (before 9 AM) or skip. During peak hours it is shoulder-to-shoulder tourists. The walk itself is short (10-15 min). Pair with Tenryu-ji temple garden right next to it.'),
        ('Hiroshima Peace Memorial', 'Allow 2-3 hours minimum. The museum is emotionally heavy but essential. Individual stories and artifacts are more impactful than the statistics. The Children\'s Peace Monument is particularly moving.'),
        ('Floating Itsukushima Torii', 'Check tide tables! High tide: gate "floats" in water (more photogenic). Low tide: walk out to the gate and touch it. Both are worth seeing. Plan your timing around tides.'),
        ('Dotonbori Night Walk', 'vs Shinsaibashi: Dotonbori is the neon canal strip (louder, more food). Shinsaibashi is the covered shopping arcade (more retail). They run parallel — do both by walking up one, back on the other.'),
        ('Nara day trip', 'SUBSTITUTE for Day 13 Osaka activities. More relaxed, nature-focused. The deer are genuinely delightful. Todai-ji has the world\'s largest bronze Buddha. Good if Osaka feels like "too much city" after Tokyo+Kyoto.'),
        ('Nishiki Market', 'Kyoto\'s 400-year-old food market. Different from Osaka\'s Kuromon — more refined, pickles and tea vs raw seafood. Try dashimaki tamago (rolled omelette) and fresh mochi. Good for souvenir shopping (packaged teas, spices).'),
        ('Monkey Park Iwatayama', 'Short but steep 20-min hike. Monkeys roam free on mountaintop — you go inside the enclosure, they don\'t. Panoramic views of Kyoto from the top. Fun and different from temple-hopping.'),
        ('Spa World', 'OPTIONAL. Giant themed onsen complex (Egyptian, Roman, Japanese baths). Culture shock — everyone is naked, no swimsuits. Great if you love onsen. Skip if you\'re uncomfortable or short on time.'),
        ('TeamLab Planets', 'BOOK IN ADVANCE — sells out weeks ahead. Immersive digital art. You wade through knee-deep water, walk barefoot through light. 60-90 min inside. Unique experience unlike anything else on the trip.'),
        ('Kurama-Kibune', 'SUBSTITUTE for Arashiyama. Mountain villages + natural hot spring. More adventurous, fewer tourists. The hike between Kurama and Kibune is beautiful. Good for active travelers who want nature over temples.'),
    ]

    for title_fragment, why_text in why_updates:
        c.execute("UPDATE activity SET why=? WHERE title LIKE ? AND why IS NULL",
                  (why_text, f'%{title_fragment}%'))

    # === 5. Restructure Day 10 (Kyoto South) — separate Kiyomizu into its own activity ===
    c.execute("SELECT id FROM day WHERE day_number=10")
    day10_row = c.fetchone()
    if day10_row:
        day10_id = day10_row[0]
        # Check if Kiyomizu already exists as its own activity
        c.execute("SELECT id FROM activity WHERE day_id=? AND title LIKE 'Kiyomizu-dera%' AND is_substitute=0",
                  (day10_id,))
        if not c.fetchone():
            c.execute("SELECT MAX(sort_order) FROM activity WHERE day_id=?", (day10_id,))
            max_order = (c.fetchone()[0] or 0) + 1
            # Add Kiyomizu-dera as its own morning activity
            c.execute("""INSERT INTO activity (day_id, title, description, time_slot, address, url,
                         category, why, sort_order, is_optional, is_substitute, is_eliminated, jr_pass_covered)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0)""",
                      (day10_id,
                       'Kiyomizu-dera Temple',
                       'Iconic wooden stage jutting out over a cliff. Stunning views of Kyoto, especially with cherry blossoms. Walk down Sannenzaka & Ninenzaka — atmospheric stone-paved lanes with traditional shops.',
                       'morning',
                       '1-294 Kiyomizu, Higashiyama-ku, Kyoto',
                       'https://www.kiyomizudera.or.jp/en/',
                       'temple',
                       'Go after Fushimi Inari — it opens at 6 AM but the crowd builds later. By 10 AM you\'ll be ahead of the tour bus rush. The walk down through Ninenzaka is as good as the temple itself.',
                       max_order))

    # === 6. Restructure Day 11 (Kyoto North) — ensure Monkey Park is separate ===
    c.execute("SELECT id FROM day WHERE day_number=11")
    day11_row = c.fetchone()
    if day11_row:
        day11_id = day11_row[0]
        c.execute("SELECT id FROM activity WHERE day_id=? AND title LIKE 'Monkey Park%' AND is_substitute=0",
                  (day11_id,))
        if not c.fetchone():
            c.execute("SELECT MAX(sort_order) FROM activity WHERE day_id=?", (day11_id,))
            max_order = (c.fetchone()[0] or 0) + 1
            c.execute("""INSERT INTO activity (day_id, title, description, time_slot, address, url,
                         category, why, sort_order, is_optional, is_substitute, is_eliminated, jr_pass_covered)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, 0, 0)""",
                      (day11_id,
                       'Monkey Park Iwatayama',
                       'Short but steep 20-min hike to mountaintop. Wild monkeys roam free — you\'re in their enclosure. Panoramic views of Kyoto from the top.',
                       'afternoon',
                       '8 Arashiyama Genrokuzancho, Nishikyo-ku, Kyoto',
                       'https://www.monkeypark.jp/en/',
                       'nature',
                       'Fun and different from temple-hopping. Good if you want a break from shrines. The views alone are worth the hike.',
                       max_order))

    # === 7. Add missing URLs ===
    url_updates = {
        'Tokyo Skytree': 'https://www.tokyo-skytree.jp/en/',
        'Shibuya Crossing': 'https://www.shibuya-scramble-square.com/',
        'Ueno Zoo': 'https://www.tokyo-zoo.net/english/ueno/',
        'Nishiki Market': 'https://www.kyoto-nishiki.or.jp/en/',
        'Togetsukyo': 'https://www.japan-guide.com/e/e3912.html',
        'Osaka Castle': 'https://www.osakacastle.net/english/',
        'Umeda Sky Building': 'https://www.skybldg.co.jp/en/',
        'Shirakawa-go': 'https://shirakawa-go.org/en/',
    }
    for title_fragment, url in url_updates.items():
        c.execute("UPDATE activity SET url=? WHERE title LIKE ? AND url IS NULL",
                  (url, f'%{title_fragment}%'))

    # === 8. Add intra-day transport notes for the Day view ===
    # Add JR Nara Line route for Fushimi Inari day (Day 10)
    c.execute("SELECT id FROM transport_route WHERE route_from='Kyoto Station' AND route_to='Fushimi Inari'")
    if not c.fetchone():
        c.execute("SELECT id FROM day WHERE day_number=10")
        d10 = c.fetchone()
        if d10:
            c.execute("""INSERT INTO transport_route (route_from, route_to, transport_type, train_name,
                         duration, jr_pass_covered, cost_if_not_covered, notes, day_id, sort_order)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                      ('Kyoto Station', 'Fushimi Inari', 'JR Nara Line', 'JR Nara Line',
                       '5 min', 1, '¥150', 'Just 2 stops. JR Pass covered. Keihan Line also works from downtown.', d10[0], 25))
    else:
        # Fix day_id if route already exists with wrong day
        c.execute("SELECT id FROM day WHERE day_number=10")
        d10 = c.fetchone()
        if d10:
            c.execute("UPDATE transport_route SET day_id=? WHERE route_from='Kyoto Station' AND route_to='Fushimi Inari'", (d10[0],))

    # JR Nara Line for Nara day trip (Day 13)
    c.execute("SELECT id FROM transport_route WHERE route_from='Osaka' AND route_to='Nara'")
    if not c.fetchone():
        c.execute("SELECT id FROM day WHERE day_number=13")
        d13 = c.fetchone()
        if d13:
            c.execute("""INSERT INTO transport_route (route_from, route_to, transport_type, train_name,
                         duration, jr_pass_covered, cost_if_not_covered, notes, day_id, sort_order)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                      ('Osaka', 'Nara', 'JR Yamatoji Line', 'JR Rapid',
                       '~50 min', 1, '¥810', 'Direct from Osaka-Namba or Tennoji. JR Pass covered.', d13[0], 26))
    else:
        # Fix day_id if route already exists with wrong day
        c.execute("SELECT id FROM day WHERE day_number=13")
        d13 = c.fetchone()
        if d13:
            c.execute("UPDATE transport_route SET day_id=? WHERE route_from='Osaka' AND route_to='Nara'", (d13[0],))

    # Hakone internal transport (Day 5)
    c.execute("SELECT id FROM transport_route WHERE route_from='Odawara' AND route_to LIKE 'Hakone%'")
    if not c.fetchone():
        c.execute("SELECT id FROM day WHERE day_number=5")
        d5 = c.fetchone()
        if d5:
            c.execute("""INSERT INTO transport_route (route_from, route_to, transport_type, train_name,
                         duration, jr_pass_covered, cost_if_not_covered, notes, day_id, sort_order)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                      ('Odawara', 'Hakone (Loop)', 'Hakone Tozan Railway', 'Switchback Train',
                       '~40 min', 0, 'Hakone Free Pass', 'Covered by Hakone Free Pass (¥6,000). Scenic mountain railway.', d5[0], 24))
    else:
        # Fix day_id if wrong
        c.execute("SELECT id FROM day WHERE day_number=5")
        d5 = c.fetchone()
        if d5:
            c.execute("UPDATE transport_route SET day_id=? WHERE route_from='Odawara' AND route_to LIKE 'Hakone%'", (d5[0],))

    conn.commit()
    conn.close()
    db.session.expire_all()
    print("Migration complete: activities enriched with addresses, categories, and reasoning.")


def _migrate_sumo_bookahead_transit(app):
    """Add sumo event, book_ahead flags, getting_there transit tips, and logistical fixes.
    Idempotent — checks if sumo activity already exists."""
    import sqlite3
    from models import Activity, Day

    # Guard: if sumo activity already exists, skip
    sumo = Activity.query.filter(Activity.title.contains('Sumo')).first()
    if sumo:
        return

    print("Running migration: sumo event, book-ahead flags, transit tips...")
    db_path = app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # === 1. Add Sumo Morning Practice on Day 3 (April 7, full Tokyo day) ===
    c.execute("SELECT id FROM day WHERE day_number=3")
    day3 = c.fetchone()
    if day3:
        # Shift existing activities' sort_order up by 2 to make room at the start
        c.execute("UPDATE activity SET sort_order = sort_order + 2 WHERE day_id=? AND sort_order <= 5", (day3[0],))
        # Add sumo morning practice as the very first activity
        c.execute("""INSERT INTO activity (day_id, title, description, time_slot, start_time,
                     sort_order, category, address, url, book_ahead, book_ahead_note,
                     getting_there, why, is_optional, is_substitute, is_completed, is_eliminated)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                  (day3[0],
                   'Sumo Morning Practice at Arashio Stable',
                   'Watch real sumo wrestlers train through large street-facing windows. Free, no reservation needed. Arrive by 6:45 AM for a good spot. No flash photography. Wrestlers sometimes come outside after practice for photos.',
                   'morning', '6:45 AM', 1, 'culture',
                   'Hama-cho, Nihombashi, Chuo-ku, Tokyo',
                   'https://arashio.net/tour_e.html',
                   1, 'Call the stable 4-8 PM the day before to confirm practice (see arashio.net). Free admission.',
                   'Toei Shinjuku Line to Hamacho Station (1 min walk). ~20 min from Asakusa.',
                   'Unique chance to see real sumo training up close. April is an active training month between March and May tournaments. Free and authentic -- much better than tourist shows.',
                   0, 0, 0, 0))
        # Add Sumo Museum as optional activity
        c.execute("""INSERT INTO activity (day_id, title, description, time_slot,
                     sort_order, category, address, url, is_optional,
                     getting_there, why, is_substitute, is_completed, is_eliminated)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                  (day3[0],
                   'Sumo Museum at Ryogoku Kokugikan',
                   'Small free museum inside the sumo stadium with rotating exhibitions about sumo history. Open weekdays 10:30 AM-4:00 PM. Closed weekends.',
                   'morning', 2, 'culture',
                   '1-3-28 Yokoami, Sumida-ku, Tokyo',
                   'https://www.sumo.or.jp/EnSumoMuseum/',
                   1,
                   'JR Sobu Line to Ryogoku Station (west exit, 2 min walk). ~15 min from Hamacho.',
                   'Quick free stop if you want more sumo culture. Only 30-45 min needed. Combine with Arashio Stable for a sumo morning.',
                   0, 0, 0))

    # === 2. Set book_ahead flags on activities that need advance tickets ===
    book_ahead_activities = [
        # (title_pattern, book_ahead_note)
        ('Hakone Free Pass', 'Buy at Odawara Station HIS counter or online at odakyu.jp. Available day-of but lines can be long.'),
        ('Hakone Open-Air Museum', 'Buy tickets online at hakone-oam.or.jp to skip the line. ~¥1,600/person.'),
        ('Fushimi Inari', 'No tickets needed -- free and open 24 hours. Just go early (6:30 AM) to avoid crowds.'),
        ('Kinkaku-ji', 'No advance booking needed. ¥500 admission at the gate. Arrive by 9 AM to beat tour buses.'),
        ('Bamboo Grove', 'Free, no tickets. Go early morning (before 8 AM) or late afternoon to avoid crowds.'),
        ('Tenryu-ji', 'No advance booking needed. ¥500 garden, ¥800 with temple hall. Opens 8:30 AM.'),
        ('Hiroshima Peace Memorial', 'Buy museum tickets online at hpmmuseum.jp to skip the line. ¥200/person. Opens 8:30 AM.'),
        ('Itsukushima', 'Ferry is free with JR Pass. Shrine: ¥300 at gate. No advance booking needed.'),
        ('TeamLab', 'MUST book 2-3 weeks ahead at teamlab.art. Sells out fast, especially weekends. ~¥3,800/person.'),
        ('Osaka Castle', 'No advance booking needed. ¥600 at gate. Opens 9 AM.'),
        ('Spa World', 'No advance booking. ¥1,500 entry (weekday). Open 10 AM-8:45 AM next day.'),
        ('Umeda Sky Building', 'No advance tickets needed. ¥1,500 at gate. Best at sunset/night.'),
        ('Todai-ji', 'No advance booking needed. ¥600 at gate. Nara deer park is free.'),
        ('Hida Folk Village', 'No advance booking. ¥700 at gate. Opens 8:30 AM.'),
        ('Takayama Festival Floats', 'No advance booking needed. ¥1,000 at gate.'),
        ('Kiyomizu-dera', 'No advance booking needed. ¥400 at gate. Best early morning or late afternoon.'),
        ('Tenzan Tohji-kyo', 'No reservation needed for day-use. ¥1,300 entry. Bring own towel or rent (¥200).'),
        ('JR Pass', 'Order online at japanrailpass.net 2-4 weeks before trip. Exchange voucher at JR counter on arrival.'),
        ('Nohi Bus', 'Reserve online at nouhibus.co.jp 1-2 weeks ahead. ¥3,390/person. Limited seats.'),
        ('Sumo Morning Practice', 'Call stable 4-8 PM day before to confirm practice schedule. See arashio.net.'),
    ]

    for title_pattern, note in book_ahead_activities:
        c.execute("UPDATE activity SET book_ahead=1, book_ahead_note=? WHERE title LIKE ? AND book_ahead IS NOT 1",
                  (note, f'%{title_pattern}%'))

    # === 3. Add getting_there transit tips between activities ===
    # Day 3 - Tokyo Full Day
    transit_tips_day3 = [
        ('Senso-ji Temple', 'morning', 'Already in Asakusa (hotel area). 5 min walk from Dormy Inn.'),
        ('Meiji Shrine', 'Walk to Ginza Line at Asakusa → Omotesando (25 min). Or JR to Harajuku Station.'),
        ('Harajuku', 'Right next to Meiji Shrine. Walk through the shrine forest to Takeshita Street (5 min).'),
        ('Shibuya Crossing', 'Walk south from Harajuku (15 min) or JR Yamanote Line one stop to Shibuya.'),
        ('Golden Gai', 'JR Yamanote Line: Shibuya → Shinjuku (5 min). Golden Gai is 5 min walk from east exit.'),
        ('Omoide Yokocho', 'Right next to Shinjuku Station west exit. 2 min walk from Golden Gai area.'),
    ]
    if day3:
        for title_pattern, *rest in transit_tips_day3:
            if len(rest) == 2:
                tip = rest[1]
            else:
                tip = rest[0]
            c.execute("UPDATE activity SET getting_there=? WHERE day_id=? AND title LIKE ? AND getting_there IS NULL",
                      (tip, day3[0], f'%{title_pattern}%'))

    # Day 4 - Hakone
    c.execute("SELECT id FROM day WHERE day_number=4")
    day4 = c.fetchone()
    if day4:
        hakone_tips = [
            ('Switchback Train', 'From Hakone-Yumoto Station (take Odakyu Romance Car or local train from Odawara).'),
            ('Cable Car to Owakudani', 'Direct connection from Gora Station (end of switchback train line).'),
            ('Ropeway over mountains', 'Continues from Owakudani. Stay on the ropeway system.'),
            ('Lake Ashi Pirate Ship', 'Ropeway ends at Togendai port. Board the pirate ship there.'),
            ('Open-Air Museum', 'Bus or walk from Hakone-Yumoto/Gora. Best visited between train and cable car.'),
            ('Tenzan Tohji-kyo', 'Bus from Hakone-Yumoto Station (~5 min). Or 15 min walk from station.'),
        ]
        for title_pattern, tip in hakone_tips:
            c.execute("UPDATE activity SET getting_there=? WHERE day_id=? AND title LIKE ? AND getting_there IS NULL",
                      (tip, day4[0], f'%{title_pattern}%'))

    # Day 5 - Takayama arrival
    c.execute("SELECT id FROM day WHERE day_number=5")
    day5 = c.fetchone()
    if day5:
        takayama_tips = [
            ('Sanmachi Suji', '10 min walk from Takayama Station. Cross the Miyagawa River bridge.'),
            ('Sake brewery', 'In Sanmachi Suji area. Walk between them (Funasaka, Kawashiri, Harada).'),
            ('Takayama Jinya', '5 min walk south of Sanmachi Suji along the river.'),
        ]
        for title_pattern, tip in takayama_tips:
            c.execute("UPDATE activity SET getting_there=? WHERE day_id=? AND title LIKE ? AND getting_there IS NULL",
                      (tip, day5[0], f'%{title_pattern}%'))

    # Day 6 - Takayama full day
    c.execute("SELECT id FROM day WHERE day_number=6")
    day6 = c.fetchone()
    if day6:
        taka2_tips = [
            ('Miyagawa Morning Market', '10 min walk from station along Miyagawa River east bank.'),
            ('Hida Folk Village', 'Nohi Bus from Takayama Bus Center (~10 min) or 30 min walk west.'),
            ('Hida beef sushi', 'Back in Sanmachi Suji area. Look for Sakaguchiya or Kokorobi (street stalls).'),
            ('Festival Floats Museum', '15 min walk northeast of old town. Near Sakurayama Shrine.'),
        ]
        for title_pattern, tip in taka2_tips:
            c.execute("UPDATE activity SET getting_there=? WHERE day_id=? AND title LIKE ? AND getting_there IS NULL",
                      (tip, day6[0], f'%{title_pattern}%'))

    # Day 8 - Shirakawa-go to Kyoto
    c.execute("SELECT id FROM day WHERE day_number=8")
    day8 = c.fetchone()
    if day8:
        d8_tips = [
            ('Shirakawa-go UNESCO', 'Nohi Bus drops you at the village bus terminal. 2 min walk to village center.'),
            ('Wada House', 'In the village center. 5 min walk from bus stop.'),
            ('Shiroyama observation', '15-20 min uphill walk from the village. Follow signs to "observation deck."'),
            ('Pontocho Alley', 'From Kyoto Station: Karasuma Line to Shijo (10 min) then 5 min walk east to the river.'),
            ('Kamo River', 'Right next to Pontocho Alley. Walk to the riverbank.'),
        ]
        for title_pattern, tip in d8_tips:
            c.execute("UPDATE activity SET getting_there=? WHERE day_id=? AND title LIKE ? AND getting_there IS NULL",
                      (tip, day8[0], f'%{title_pattern}%'))

    # Day 9 - Kyoto Eastern Temples
    c.execute("SELECT id FROM day WHERE day_number=9")
    day9 = c.fetchone()
    if day9:
        d9_tips = [
            ('Fushimi Inari', 'JR Nara Line from Kyoto Station to Inari Station (5 min, 2 stops). Shrine is right at the station exit.'),
            ('Hanamikoji', 'Keihan Line from Fushimi-Inari to Gion-Shijo (10 min). Walk south on Hanamikoji Street.'),
            ('Kamo River', 'Walk west from Gion to the river (5 min). Beautiful lit-up bridges at night.'),
        ]
        for title_pattern, tip in d9_tips:
            c.execute("UPDATE activity SET getting_there=? WHERE day_id=? AND title LIKE ? AND getting_there IS NULL",
                      (tip, day9[0], f'%{title_pattern}%'))

    # Day 10 - Kyoto Western
    c.execute("SELECT id FROM day WHERE day_number=10")
    day10 = c.fetchone()
    if day10:
        d10_tips = [
            ('Kinkaku-ji', 'Bus #205 from Kyoto Station to Kinkaku-ji-michi (40 min). Or taxi (~¥2,000).'),
            ('Bamboo Grove', 'Bus #205 from Kinkaku-ji to Arashiyama (25 min). Or JR Sagano Line.'),
            ('Tenryu-ji', 'At the south end of Bamboo Grove. 2 min walk.'),
            ('Togetsukyo Bridge', '5 min walk south from Tenryu-ji through the shopping street.'),
            ('Nishiki Market', 'JR Sagano Line from Saga-Arashiyama to central Kyoto. Market is near Shijo-Karasuma.'),
            ('Kiyomizu-dera', 'Bus #207 from Shijo to Kiyomizu-michi (15 min) then 10 min uphill walk.'),
        ]
        for title_pattern, tip in d10_tips:
            c.execute("UPDATE activity SET getting_there=? WHERE day_id=? AND title LIKE ? AND getting_there IS NULL",
                      (tip, day10[0], f'%{title_pattern}%'))

    # Day 11 - Hiroshima day trip
    c.execute("SELECT id FROM day WHERE day_number=11")
    day11 = c.fetchone()
    if day11:
        d11_tips = [
            ('Peace Memorial', 'Hiroshima streetcar (tram) from JR Hiroshima Station to Genbaku-Dome mae (15 min). ¥220.'),
            ('A-Bomb Dome', 'Right across the river from Peace Memorial Park. 3 min walk.'),
            ('okonomiyaki', 'Walk to Okonomimura building near Peace Park (5 min). Multiple floors of okonomiyaki stalls.'),
            ('JR train to Miyajimaguchi', 'JR Sanyo Line from Hiroshima to Miyajimaguchi (25 min). JR Pass covered.'),
            ('Floating.*Torii', 'JR Ferry from Miyajimaguchi pier to Miyajima Island (10 min). JR Pass covered.'),
            ('Itsukushima Shrine', '10 min walk from Miyajima ferry terminal along the waterfront.'),
        ]
        for title_pattern, tip in d11_tips:
            c.execute("UPDATE activity SET getting_there=? WHERE day_id=? AND title LIKE ? AND getting_there IS NULL",
                      (tip, day11[0], f'%{title_pattern}%'))

    # Day 12 - Osaka
    c.execute("SELECT id FROM day WHERE day_number=12")
    day12 = c.fetchone()
    if day12:
        d12_tips = [
            ('Osaka Castle', 'JR Loop Line to Osakajo-koen Station (2 min walk to park entrance). Or Tanimachi Line to Tanimachi 4-chome.'),
            ('Kuromon Market', 'Osaka Metro Sakaisuji Line to Nipponbashi Station (exit 10). 2 min walk.'),
            ('Shinsekai', 'Walk south from Kuromon Market (15 min) or Osaka Metro to Dobutsuen-mae Station.'),
            ('Spa World', 'In Shinsekai district. 3 min walk from Tsutenkaku Tower.'),
            ('Den Den Town', 'Walk north from Shinsekai (10 min) or Osaka Metro to Ebisucho Station.'),
            ('Dotonbori', 'Osaka Metro Midosuji Line to Namba Station (exit 14). Walk north to canal.'),
            ('Takoyaki crawl', 'All along Dotonbori canal. Wanaka, Kukuru, and Aizuya are within 200m of each other.'),
            ('Hozenji Yokocho', 'Tiny alley just south of Dotonbori. Look for the moss-covered Fudo statue.'),
            ('Ura-Namba', 'South side of Namba Station. Walk south from Dotonbori (5 min).'),
            ('Amerikamura', '10 min walk west from Dotonbori. Cross Mido-suji boulevard.'),
        ]
        for title_pattern, tip in d12_tips:
            c.execute("UPDATE activity SET getting_there=? WHERE day_id=? AND title LIKE ? AND getting_there IS NULL",
                      (tip, day12[0], f'%{title_pattern}%'))

    # Day 13 - Osaka Day 2
    c.execute("SELECT id FROM day WHERE day_number=13")
    day13 = c.fetchone()
    if day13:
        d13_tips = [
            ('Nara', 'JR Yamatoji Rapid from Osaka-Namba or JR Osaka to JR Nara (45-50 min). JR Pass covered.'),
            ('Amerikamura', 'JR back to Osaka, then Midosuji Line to Shinsaibashi. Walk west to Amerikamura.'),
            ('Shinsaibashi', 'Connected to Amerikamura. Walk east along Shinsaibashi-suji arcade.'),
            ('Dotonbori round 2', 'Walk south from Shinsaibashi (5 min). Same canal area as Day 12.'),
            ('Umeda Sky Building', 'Midosuji Line to Umeda/Osaka Station. 10 min walk northwest. Best at sunset.'),
        ]
        for title_pattern, tip in d13_tips:
            c.execute("UPDATE activity SET getting_there=? WHERE day_id=? AND title LIKE ? AND getting_there IS NULL",
                      (tip, day13[0], f'%{title_pattern}%'))

    # === 4. Logistical fixes: move Monkey Park from Day 11 to Day 10 ===
    # Monkey Park Iwatayama is in Arashiyama (Kyoto), NOT Hiroshima.
    # It's listed on Day 11 (Hiroshima day trip) but belongs on Day 10 (Arashiyama day).
    if day10 and day11:
        c.execute("""UPDATE activity SET day_id=?, time_slot='afternoon', sort_order=6,
                     getting_there='Cross Togetsukyo Bridge to the south side. Entrance is a 5 min walk up the hill.'
                     WHERE title LIKE '%Monkey Park%' AND day_id=?""",
                  (day10[0], day11[0]))

    # === 5. Fix Ueno Zoo placement: it's on Day 3 but sort_order=91. Also optional. ===
    if day3:
        c.execute("UPDATE activity SET is_optional=1, sort_order=90, getting_there='JR Yamanote or Ginza Line to Ueno Station (park exit). Zoo is inside Ueno Park.' WHERE title LIKE '%Ueno Zoo%' AND day_id=?",
                  (day3[0],))

    conn.commit()
    conn.close()
    db.session.expire_all()
    print("Migration complete: sumo event added, book-ahead flags set, transit tips added.")


def _migrate_add_shinjuku_hotels(app):
    """Add 5 Shinjuku hotel options to Tokyo location and neighborhood tags to all options.
    Idempotent — skips if Anshin Oyado already exists."""
    from models import AccommodationLocation, AccommodationOption

    if AccommodationOption.query.filter_by(name='Anshin Oyado Tokyo Man Shinjuku').first():
        return

    print("Running migration: add Shinjuku hotels + neighborhood tags to all accommodations...")

    # Rename Tokyo location to drop the Asakusa-only label
    tok_loc = AccommodationLocation.query.filter_by(
        location_name='Tokyo (Asakusa area)').first()
    if tok_loc:
        tok_loc.location_name = 'Tokyo'
    tok_loc = tok_loc or AccommodationLocation.query.filter_by(location_name='Tokyo').first()
    if not tok_loc:
        return

    # Add neighborhood to property_type for all existing options across every location
    neighborhood_by_name = {
        # Tokyo (Asakusa)
        'Nui. Hostel & Bar Lounge':     'Design Hostel · Kuramae / Asakusa',
        'Dormy Inn Asakusa':            'Business Hotel · Asakusa',
        'Airbnb apartment':             'Apartment · Asakusa / Kuramae',
        'CITAN Hostel':                 'Design Hostel · Nihonbashi',
        'THE GATE HOTEL Kaminarimon':   'Boutique Hotel · Asakusa',
        # Takayama Ryokan
        'Tanabe Ryokan':                'Traditional Ryokan · Central Takayama',
        'Sumiyoshi Ryokan':             'Traditional Ryokan · River District',
        'Ryokan Asunaro':               'Traditional Ryokan · Central Takayama',
        'Oyado Koto no Yume':           'Traditional Ryokan · Central Takayama',
        'Honjin Hiranoya Annex':        'Premium Ryokan · Historic Center',
        # Takayama Budget
        'Rickshaw Inn':                 'Guesthouse · Near Sanmachi Suji',
        'Takayama Oasis':               'Guesthouse · Central Takayama',
        'J-Hoppers Takayama':           'Hostel · Central Takayama',
        'Guesthouse Tomaru':            'Guesthouse · Near Old Town',
        'Hostel Murasaki':              'Hostel · Near Old Town',
        # Kanazawa
        'Minn Kanazawa':                'Apartment Hotel · Near Omicho Market',
        'Kaname Inn Tatemachi':         'Boutique Inn · Tatemachi',
        'Dormy Inn Kanazawa':           'Business Hotel · Central Kanazawa',
        'Hotel Intergate Kanazawa':     'Upscale Hotel · Near Omicho Market',
        'HATCHi Kanazawa':              'Design Hotel · Near Kenroku-en',
        # Kyoto 3 nights
        "K's House Kyoto":              'Hostel · Kyoto Station',
        'Piece Hostel Sanjo':           'Boutique Hostel · Sanjo / Central',
        'Len Kyoto Kawaramachi':        'Design Hostel · Kawaramachi',
        'Dormy Inn Premium Kyoto':      'Business Hotel · Kyoto Station',
        'Hotel Ethnography Gion':       'Boutique Hotel · Gion',
        # Kyoto Machiya
        'Rinn Kyoto (Nishijin)':        'Licensed Machiya · Nishijin',
        'Rinn Kyoto (Gion)':            'Licensed Machiya · Gion',
        'Machiya Residence Inn':        'Licensed Machiya · Various Neighborhoods',
        'Airbnb machiya':               'Machiya · Higashiyama / Nakagyo',
        'Nazuna Kyoto':                 'Luxury Machiya · Central Kyoto',
    }
    for opt in AccommodationOption.query.all():
        if opt.name in neighborhood_by_name:
            opt.property_type = neighborhood_by_name[opt.name]

    # Add 5 new Shinjuku hotel options (ranks 6-10 under Tokyo)
    nights = tok_loc.num_nights
    new_hotels = [
        {
            'rank': 6,
            'name': 'Anshin Oyado Tokyo Man Shinjuku',
            'type': 'Business Hotel · Shinjuku West',
            'price': 76,
            'standout': 'STEAL: 41% below normal price. Onsen + sauna included. 4.2★ · 2,100+ reviews. Best value in Shinjuku.',
            'url': 'https://www.booking.com/searchresults.html?ss=Anshin+Oyado+Tokyo+Man+Shinjuku&checkin=2026-04-06&checkout=2026-04-09&group_adults=2',
            'has_onsen': True,
        },
        {
            'rank': 7,
            'name': "La'gent Hotel Shinjuku Kabukicho",
            'type': '3-star Hotel · Shinjuku / Kabukicho',
            'price': 175,
            'standout': 'Right in Kabukicho entertainment district. Walk to Golden Gai + Omoide Yokocho at night. 4.3★ · 501 reviews.',
            'url': 'https://www.booking.com/searchresults.html?ss=Lagent+Hotel+Shinjuku+Kabukicho&checkin=2026-04-06&checkout=2026-04-09&group_adults=2',
        },
        {
            'rank': 8,
            'name': 'DOMO HOTEL',
            'type': 'Boutique Hotel · Shinjuku',
            'price': 152,
            'standout': 'Hidden gem: flagged "GREAT PRICE with excellent reviews". 4.5★ · newer property with very high guest satisfaction.',
            'url': 'https://www.booking.com/searchresults.html?ss=DOMO+HOTEL+Shinjuku+Tokyo&checkin=2026-04-06&checkout=2026-04-09&group_adults=2',
        },
        {
            'rank': 9,
            'name': 'Mitsui Garden Hotel Jingugaien PREMIER',
            'type': '5-star Hotel · Jingugaien / Harajuku',
            'price': 291,
            'standout': 'GREAT PRICE for a 5-star. Public bath, fitness center. Upscale Jingugaien neighborhood near Yoyogi Park. 4.3★ · 1,700+ reviews.',
            'url': 'https://www.booking.com/searchresults.html?ss=Mitsui+Garden+Hotel+Jingugaien+Tokyo+Premier&checkin=2026-04-06&checkout=2026-04-09&group_adults=2',
            'has_onsen': True,
        },
        {
            'rank': 10,
            'name': 'HOTEL GROOVE SHINJUKU (PARKROYAL)',
            'type': 'Luxury Hotel · Shinjuku / Kabukicho Tower',
            'price': 434,
            'standout': 'Inside the Tokyu Kabukicho Tower (2023). Best cyberpunk Tokyo view at night. Bar + restaurant. 4.5★ · "Excellent location".',
            'url': 'https://www.booking.com/searchresults.html?ss=Hotel+Groove+Shinjuku+Parkroyal&checkin=2026-04-06&checkout=2026-04-09&group_adults=2',
        },
    ]
    for h in new_hotels:
        db.session.add(AccommodationOption(
            location_id=tok_loc.id,
            rank=h['rank'],
            name=h['name'],
            property_type=h['type'],
            price_low=h['price'],
            price_high=h['price'],
            total_low=h['price'] * nights,
            total_high=h['price'] * nights,
            standout=h['standout'],
            booking_url=h['url'],
            has_onsen=h.get('has_onsen', False),
        ))

    db.session.commit()
    print("  Migration complete: 5 Shinjuku hotels added, neighborhood tags applied to all options.")


def _migrate_add_booking_resources(app):
    """Add HotelTonight reference entry and Google Hotels alt links to Shinjuku hotels.
    Idempotent — skips if HotelTonight reference already exists."""
    from models import ReferenceContent, AccommodationOption

    if ReferenceContent.query.filter_by(title='HotelTonight').first():
        return

    print("Running migration: add HotelTonight reference + Google Hotels links...")

    # Add HotelTonight to reference page under 'accommodation' section
    max_sort = db.session.query(db.func.max(ReferenceContent.sort_order)).scalar() or 0
    db.session.add(ReferenceContent(
        section='accommodation',
        title='HotelTonight',
        content=(
            'Same-day luxury hotel deals — rooms drop 30-50% after ~6pm if unsold.\n'
            'Use on the day you want to upgrade for a spontaneous nice night.\n'
            'Filter: "Luxe" tier for 4-5 star deals in Shinjuku/Tokyo.\n'
            'App: hoteltonight.com or iOS/Android app.\n'
            'Strategy: book 2 nights at a mid-range hotel, check HotelTonight on '
            'night 3 morning for a last-minute luxury upgrade.'
        ),
        sort_order=max_sort + 1,
    ))

    # Add Google Hotels as alt_booking_url for the 5 new Shinjuku hotels
    google_links = {
        'Anshin Oyado Tokyo Man Shinjuku':
            'https://www.google.com/travel/hotels?q=Anshin+Oyado+Tokyo+Man+Shinjuku&checkin=2026-04-06&checkout=2026-04-09&adults=2',
        "La'gent Hotel Shinjuku Kabukicho":
            'https://www.google.com/travel/hotels?q=Lagent+Hotel+Shinjuku+Kabukicho&checkin=2026-04-06&checkout=2026-04-09&adults=2',
        'DOMO HOTEL':
            'https://www.google.com/travel/hotels?q=DOMO+HOTEL+Shinjuku+Tokyo&checkin=2026-04-06&checkout=2026-04-09&adults=2',
        'Mitsui Garden Hotel Jingugaien PREMIER':
            'https://www.google.com/travel/hotels?q=Mitsui+Garden+Hotel+Jingugaien+Tokyo+Premier&checkin=2026-04-06&checkout=2026-04-09&adults=2',
        'HOTEL GROOVE SHINJUKU (PARKROYAL)':
            'https://www.google.com/travel/hotels?q=Hotel+Groove+Shinjuku+Parkroyal&checkin=2026-04-06&checkout=2026-04-09&adults=2',
    }
    for name, url in google_links.items():
        opt = AccommodationOption.query.filter_by(name=name).first()
        if opt and not opt.alt_booking_url:
            opt.alt_booking_url = url

    db.session.commit()
    print("  Migration complete: HotelTonight added to reference, Google Hotels links added.")


def _migrate_swap_tokyo_hotel_links(app):
    """Swap booking_url <-> alt_booking_url for the 5 Shinjuku hotels so Google Hotels
    is the primary 'Website' button and Booking.com becomes the 'Link' button.
    Idempotent — skips if primary URL already points to Google."""
    from models import AccommodationOption

    hotels = [
        'Anshin Oyado Tokyo Man Shinjuku',
        "La'gent Hotel Shinjuku Kabukicho",
        'DOMO HOTEL',
        'Mitsui Garden Hotel Jingugaien PREMIER',
        'HOTEL GROOVE SHINJUKU (PARKROYAL)',
    ]
    changed = False
    for name in hotels:
        opt = AccommodationOption.query.filter_by(name=name).first()
        if opt and opt.booking_url and 'google.com' not in opt.booking_url:
            opt.booking_url, opt.alt_booking_url = opt.alt_booking_url, opt.booking_url
            changed = True

    if not changed:
        return

    db.session.commit()
    print("  Migration complete: Google Hotels is now primary link for Shinjuku hotels.")


def _migrate_add_neighborhood_descriptions(app):
    """Add a brief neighborhood area description to user_notes for every accommodation option.
    Idempotent — skips if already applied (checks for sentinel text in first option)."""
    from models import AccommodationOption

    sentinel = AccommodationOption.query.filter_by(name='Nui. Hostel & Bar Lounge').first()
    if sentinel and sentinel.user_notes and 'Kuramae' in (sentinel.user_notes or ''):
        return

    print("Running migration: add neighborhood descriptions to all accommodation options...")

    area_notes = {
        # Tokyo — Asakusa / Kuramae
        'Nui. Hostel & Bar Lounge':
            'Kuramae / Asakusa — Tokyo\'s most artsy pocket. Independent coffee shops, ceramics studios, craft bars. Quiet at night but 15 min subway to Shinjuku.',
        'Dormy Inn Asakusa':
            'Asakusa — Old Tokyo at its best. Senso-ji temple, street food, rickshaws. Calm evenings. Traditional feel, not a nightlife base.',
        'Airbnb apartment':
            'Asakusa / Kuramae — Residential and creative. Best for self-catering and a local feel. Quiet after 9pm.',
        'CITAN Hostel':
            'Nihonbashi — Old merchant district, now a design hub. Very central, quiet streets, good ramen nearby. Easy subway to everywhere.',
        'THE GATE HOTEL Kaminarimon':
            'Asakusa — Prime tourist Asakusa, steps from Kaminarimon Gate. Views of Senso-ji and Tokyo Skytree from the rooftop.',
        # Tokyo — Shinjuku
        'Anshin Oyado Tokyo Man Shinjuku':
            'Shinjuku West — The business/skyscraper side of Shinjuku. Quiet streets but 5-min walk to the east side nightlife. Best of both worlds.',
        "La'gent Hotel Shinjuku Kabukicho":
            'Shinjuku / Kabukicho — Tokyo\'s entertainment capital. Neon signs, Golden Gai bars, Omoide Yokocho (Memory Lane). Walk out the door into the action.',
        'DOMO HOTEL':
            'Shinjuku — Central Shinjuku, close to the station and nightlife. Everything within walking distance: restaurants, bars, shopping.',
        'Mitsui Garden Hotel Jingugaien PREMIER':
            'Jingugaien / Harajuku — Upscale, tree-lined neighborhood between Shibuya and Shinjuku. Near Yoyogi Park, Meiji Shrine, and Omotesando high-end shopping.',
        'HOTEL GROOVE SHINJUKU (PARKROYAL)':
            'Shinjuku / Kabukicho Tower — Inside the 2023 Tokyu Kabukicho Tower skyscraper. Literally in the center of Tokyo\'s most electric nightlife district.',
        # Takayama Ryokan
        'Tanabe Ryokan':
            'Central Takayama — Walking distance to Sanmachi Suji historic district, morning markets, and Miyagawa River. Everything is walkable.',
        'Sumiyoshi Ryokan':
            'River District Takayama — Along the Miyagawa River, steps from the morning market. Peaceful, scenic, and very central.',
        'Ryokan Asunaro':
            'Central Takayama — In the historic core. Easy walk to old town sake breweries, lacquerware shops, and Higashiyama temple walk.',
        'Oyado Koto no Yume':
            'Central Takayama — Quiet residential streets near the historic district. Small and intimate — feels like staying with a local family.',
        'Honjin Hiranoya Annex':
            'Historic Center Takayama — One of Takayama\'s most prestigious addresses. Samurai-era neighborhood, 200+ year old inn lineage.',
        # Takayama Budget
        'Rickshaw Inn':
            'Near Sanmachi Suji — 8-min walk to the preserved edo-era merchant district. Great base for early morning walks before the crowds arrive.',
        'Takayama Oasis':
            'Central Takayama — Near the train station and old town. Good launchpad for day trips to Shirakawa-go.',
        'J-Hoppers Takayama':
            'Central Takayama — Social hostel atmosphere, 10-min walk to Sanmachi Suji. Popular with backpackers passing through the Alps.',
        'Guesthouse Tomaru':
            'Near Old Town — Renovated traditional machiya (townhouse). Narrow lane location gives an authentic Takayama feel.',
        'Hostel Murasaki':
            'Near Old Town — Closest budget option to the preserved historic streets. Bare-bones but unbeatable for morning access to Sanmachi Suji.',
        # Kanazawa
        'Minn Kanazawa':
            'Near Omicho Market — 300m from the covered fresh seafood market. Great for self-catering. Higashi Chaya geisha district is a 15-min walk.',
        'Kaname Inn Tatemachi':
            'Tatemachi — Central shopping and dining street. Lively evening scene, easy access to Kenroku-en garden and 21st Century Museum.',
        'Dormy Inn Kanazawa':
            'Central Kanazawa — Near the station, convenient for the limited-time Kanazawa stop. Good hub for day-tripping to Omicho and Higashi Chaya.',
        'Hotel Intergate Kanazawa':
            'Near Omicho Market — Central location, short walk to the fresh market and Kazuemachi geisha district along the river.',
        'HATCHi Kanazawa':
            'Near Kenroku-en — Close to Japan\'s most celebrated garden and the 21st Century Museum of Contemporary Art. Design-forward neighborhood.',
        # Kyoto 3 nights
        "K's House Kyoto":
            'Kyoto Station — Maximum transit convenience. All major bus lines and the subway originate here. Nishiki Market and Fushimi Inari are easy day trips.',
        'Piece Hostel Sanjo':
            'Sanjo / Central Kyoto — Heart of the city. Walk to Nishiki Market, Kamo River, Gion. Near Keihan Line for quick access to Fushimi Inari.',
        'Len Kyoto Kawaramachi':
            'Kawaramachi — Right on the Kamo River. Steps from Pontocho alley, Nishiki Market, and Gion. The most central base in Kyoto.',
        'Dormy Inn Premium Kyoto':
            'Kyoto Station — Best transit hub in Kyoto. Unlimited bus access. Good base for Arashiyama and Fushimi Inari day trips.',
        'Hotel Ethnography Gion':
            'Gion — Kyoto\'s famous geisha district. Cobblestone lanes, wooden machiya, chance of spotting maiko in the evening. The most atmospheric location.',
        # Kyoto Machiya
        'Rinn Kyoto (Nishijin)':
            'Nishijin — Traditional weaving district north of central Kyoto. Quiet, residential, and authentic. Full private house with kitchen and washer.',
        'Rinn Kyoto (Gion)':
            'Gion — Same quality as Nishijin but in the heart of the geisha district. Walk to Shirakawa canal, Yasaka Shrine, Maruyama Park.',
        'Machiya Residence Inn':
            'Various Neighborhoods — Licensed machiya across Higashiyama, Nakagyo, and Nishijin. Choose by area when booking.',
        'Airbnb machiya':
            'Higashiyama / Nakagyo — Best areas to filter for. Higashiyama = temple district; Nakagyo = central, near Nishiki Market.',
        'Nazuna Kyoto':
            'Central Kyoto — Boutique machiya hotel with full service. Tea ceremony, private hinoki bath. The most luxurious traditional experience.',
    }

    for name, note in area_notes.items():
        opt = AccommodationOption.query.filter_by(name=name).first()
        if opt:
            opt.user_notes = note

    db.session.commit()
    print("  Migration complete: neighborhood descriptions added to all accommodation options.")


def _migrate_add_maps_urls(app):
    """Add Google Maps search URLs to every accommodation option.
    Idempotent — skips if already applied (checks sentinel option)."""
    from models import AccommodationOption

    sentinel = AccommodationOption.query.filter_by(name='Dormy Inn Asakusa').first()
    if sentinel and sentinel.maps_url:
        return

    print("Running migration: add Google Maps URLs to all accommodation options...")

    maps_urls = {
        # Tokyo — Asakusa / Kuramae
        'Nui. Hostel & Bar Lounge':
            'https://www.google.com/maps/search/?api=1&query=Nui+Hostel+Bar+Lounge+Kuramae+Tokyo+Japan',
        'Dormy Inn Asakusa':
            'https://www.google.com/maps/search/?api=1&query=Dormy+Inn+Asakusa+Tokyo+Japan',
        'CITAN Hostel':
            'https://www.google.com/maps/search/?api=1&query=CITAN+Hostel+Nihonbashi+Tokyo+Japan',
        'THE GATE HOTEL Kaminarimon':
            'https://www.google.com/maps/search/?api=1&query=THE+GATE+HOTEL+Kaminarimon+Asakusa+Tokyo+Japan',
        # Tokyo — Shinjuku
        'Anshin Oyado Tokyo Man Shinjuku':
            'https://www.google.com/maps/search/?api=1&query=Anshin+Oyado+Tokyo+Man+Shinjuku+Japan',
        "La'gent Hotel Shinjuku Kabukicho":
            'https://www.google.com/maps/search/?api=1&query=Lagent+Hotel+Shinjuku+Kabukicho+Tokyo+Japan',
        'DOMO HOTEL':
            'https://www.google.com/maps/search/?api=1&query=DOMO+HOTEL+Shinjuku+Tokyo+Japan',
        'Mitsui Garden Hotel Jingugaien PREMIER':
            'https://www.google.com/maps/search/?api=1&query=Mitsui+Garden+Hotel+Jingugaien+Tokyo+Premier+Japan',
        'HOTEL GROOVE SHINJUKU (PARKROYAL)':
            'https://www.google.com/maps/search/?api=1&query=Hotel+Groove+Shinjuku+Parkroyal+Kabukicho+Tower+Tokyo+Japan',
        # Takayama Ryokan
        'Tanabe Ryokan':
            'https://www.google.com/maps/search/?api=1&query=Tanabe+Ryokan+Takayama+Japan',
        'Sumiyoshi Ryokan':
            'https://www.google.com/maps/search/?api=1&query=Sumiyoshi+Ryokan+Takayama+Japan',
        'Ryokan Asunaro':
            'https://www.google.com/maps/search/?api=1&query=Ryokan+Asunaro+Takayama+Japan',
        'Oyado Koto no Yume':
            'https://www.google.com/maps/search/?api=1&query=Oyado+Koto+no+Yume+Takayama+Japan',
        'Honjin Hiranoya Annex':
            'https://www.google.com/maps/search/?api=1&query=Honjin+Hiranoya+Annex+Takayama+Japan',
        # Takayama Budget
        'Rickshaw Inn':
            'https://www.google.com/maps/search/?api=1&query=Rickshaw+Inn+Takayama+Japan',
        'Takayama Oasis':
            'https://www.google.com/maps/search/?api=1&query=Takayama+Oasis+Hostel+Japan',
        'J-Hoppers Takayama':
            'https://www.google.com/maps/search/?api=1&query=J-Hoppers+Takayama+Japan',
        'Guesthouse Tomaru':
            'https://www.google.com/maps/search/?api=1&query=Guesthouse+Tomaru+Takayama+Japan',
        'Hostel Murasaki':
            'https://www.google.com/maps/search/?api=1&query=Hostel+Murasaki+Takayama+Japan',
        # Kanazawa
        'Minn Kanazawa':
            'https://www.google.com/maps/search/?api=1&query=Minn+Kanazawa+Japan',
        'Kaname Inn Tatemachi':
            'https://www.google.com/maps/search/?api=1&query=Kaname+Inn+Tatemachi+Kanazawa+Japan',
        'Dormy Inn Kanazawa':
            'https://www.google.com/maps/search/?api=1&query=Dormy+Inn+Kanazawa+Japan',
        'Hotel Intergate Kanazawa':
            'https://www.google.com/maps/search/?api=1&query=Hotel+Intergate+Kanazawa+Japan',
        'HATCHi Kanazawa':
            'https://www.google.com/maps/search/?api=1&query=HATCHi+Kanazawa+Japan',
        # Kyoto 3 nights
        "K's House Kyoto":
            'https://www.google.com/maps/search/?api=1&query=Ks+House+Kyoto+Japan',
        'Piece Hostel Sanjo':
            'https://www.google.com/maps/search/?api=1&query=Piece+Hostel+Sanjo+Kyoto+Japan',
        'Len Kyoto Kawaramachi':
            'https://www.google.com/maps/search/?api=1&query=Len+Kyoto+Kawaramachi+Japan',
        'Dormy Inn Premium Kyoto':
            'https://www.google.com/maps/search/?api=1&query=Dormy+Inn+Premium+Kyoto+Japan',
        'Hotel Ethnography Gion':
            'https://www.google.com/maps/search/?api=1&query=Hotel+Ethnography+Gion+Kyoto+Japan',
        # Kyoto Machiya
        'Rinn Kyoto (Nishijin)':
            'https://www.google.com/maps/search/?api=1&query=Rinn+Kyoto+Nishijin+Japan',
        'Rinn Kyoto (Gion)':
            'https://www.google.com/maps/search/?api=1&query=Rinn+Kyoto+Gion+Japan',
        'Nazuna Kyoto':
            'https://www.google.com/maps/search/?api=1&query=Nazuna+Kyoto+Japan',
    }

    for name, url in maps_urls.items():
        opt = AccommodationOption.query.filter_by(name=name).first()
        if opt and not opt.maps_url:
            opt.maps_url = url

    db.session.commit()
    print("  Migration complete: Google Maps URLs added to all accommodation options.")


def _migrate_book_sotetsu_fresa(app):
    """Add Sotetsu Fresa Inn Higashi-Shinjuku as booked Tokyo hotel.
    Mark La'gent as eliminated. Update check-in/check-out info.
    Idempotent — skips if Sotetsu Fresa Inn already exists."""
    from models import AccommodationOption, AccommodationLocation

    existing = AccommodationOption.query.filter_by(
        name='Sotetsu Fresa Inn Higashi-Shinjuku').first()
    if existing:
        return

    print("Running migration: book Sotetsu Fresa Inn Higashi-Shinjuku...")

    tok_loc = AccommodationLocation.query.filter_by(location_name='Tokyo').first()
    if not tok_loc:
        return

    # Add Sotetsu Fresa Inn as booked option
    sotetsu = AccommodationOption(
        location_id=tok_loc.id,
        rank=11,
        name='Sotetsu Fresa Inn Higashi-Shinjuku',
        property_type='Business Hotel · Higashi-Shinjuku / Kabukicho',
        price_low=192,
        price_high=192,
        total_low=576,
        total_high=576,
        breakfast_included=False,
        has_onsen=False,
        standout='BOOKED via Priceline/Agoda. Twin non-smoking, 227 sq ft, fridge, free WiFi. Sotetsu chain (premium business). Right at Higashi-Shinjuku Station — 5 min walk to Kabukicho/Golden Gai. Free cancel before Apr 5.',
        booking_url='https://www.agoda.com/sotetsu-fresa-inn-higashi-shinjuku/hotel/tokyo-jp.html',
        alt_booking_url='https://www.google.com/travel/hotels?q=Sotetsu+Fresa+Inn+Higashi+Shinjuku&checkin=2026-04-06&checkout=2026-04-09&adults=2',
        maps_url='https://www.google.com/maps/search/?api=1&query=Sotetsu+Fresa+Inn+Higashi+Shinjuku+7-27-9+Shinjuku+Tokyo+Japan',
        is_selected=True,
        booking_status='booked',
        confirmation_number='976558450',
        address='7-27-9 Shinjuku Shinjuku-ku, Tokyo, 160-0022, Japan',
        check_in_info='After 3:00 PM (15:00) — MUST ARRIVE BY 9PM or call hotel!',
        check_out_info='Before 11:00 AM',
        user_notes='Higashi-Shinjuku / Kabukicho — Right at Higashi-Shinjuku Station (Oedo Line). 5 min walk to Kabukicho, Golden Gai, and Omoide Yokocho. The nightlife heart of Tokyo is at your doorstep. Contact: +81-3-6892-2032. Note: arrive before 9pm on check-in day or contact hotel directly.',
    )
    db.session.add(sotetsu)

    # Mark La'gent as eliminated (smoking rooms / non-refundable non-smoking)
    lagent = AccommodationOption.query.filter_by(
        name="La'gent Hotel Shinjuku Kabukicho").first()
    if lagent:
        lagent.is_eliminated = True

    # Deselect any other previously selected Tokyo options
    for opt in AccommodationOption.query.filter_by(location_id=tok_loc.id).all():
        if opt.name != 'Sotetsu Fresa Inn Higashi-Shinjuku':
            opt.is_selected = False

    db.session.commit()
    print("  Migration complete: Sotetsu Fresa Inn booked, La'gent eliminated.")


def _migrate_update_itinerary_for_sotetsu(app):
    """Update itinerary activities that reference Dormy Inn / Asakusa hotel
    to reference the booked Sotetsu Fresa Inn Higashi-Shinjuku.
    Idempotent — skips if already updated."""
    from models import Activity

    sentinel = Activity.query.filter_by(title='Check into Sotetsu Fresa Inn Higashi-Shinjuku').first()
    if sentinel:
        return

    print("Running migration: update itinerary activities for Sotetsu Fresa Inn...")

    updates = {
        'Keikyu Line to Asakusa': {
            'title': 'Train to Higashi-Shinjuku',
            'description': 'Keikyu Line to Daimon Station, transfer to Toei Oedo Line to Higashi-Shinjuku. ~50 min, ~¥800. Use IC card.',
        },
        'Check into Dormy Inn Asakusa': {
            'title': 'Check into Sotetsu Fresa Inn Higashi-Shinjuku',
            'description': 'Drop bags and freshen up. Twin non-smoking room, 227 sq ft. Address: 7-27-9 Shinjuku. ⚠️ MUST arrive before 9 PM or call +81-3-6892-2032.',
        },
        'Rooftop onsen bath at Dormy Inn': {
            'title': 'Explore Kabukicho at night',
            'description': 'Hotel is steps from Kabukicho. Walk Golden Gai (tiny bars), Omoide Yokocho (Memory Lane yakitori), or just soak in the neon. Perfect jet-lag cure.',
        },
        'Free late-night ramen at Dormy Inn': {
            'title': 'Late-night ramen near hotel',
            'description': 'Fuunji (tsukemen, 5 min walk) or any of dozens of ramen shops near Shinjuku. Open late.',
        },
        'Send luggage ahead via takkyubin': {
            'description': 'IMPORTANT: At Sotetsu Fresa Inn front desk, send big bags to your Kyoto hotel. Arrives in 1-2 days. Pack daypacks only for the Alps leg (Takayama/Kanazawa = 3 nights). Ask for "takkyubin".',
        },
        'Last free late-night ramen at Dormy Inn': {
            'title': 'Last night in Shinjuku',
            'description': 'Final evening in Tokyo. Revisit Golden Gai or grab late-night ramen near the hotel.',
        },
        'Check out of Dormy Inn Asakusa': {
            'title': 'Check out of Sotetsu Fresa Inn',
            'description': 'Check out by 11:00 AM. Luggage storage available at front desk (from 7 AM, pick up before 10 PM).',
        },
    }

    for old_title, changes in updates.items():
        act = Activity.query.filter_by(title=old_title).first()
        if act:
            if 'title' in changes:
                act.title = changes['title']
            if 'description' in changes:
                act.description = changes['description']
            if 'address' in changes:
                act.address = changes['address']

    # Update Senso-ji description to reflect distance from new hotel
    sensoji = Activity.query.filter_by(title='Senso-ji Temple at night').first()
    if sensoji and 'Dormy Inn' in (sensoji.description or ''):
        sensoji.description = 'Beautifully illuminated, almost empty at night. Completely different atmosphere than daytime. ~25 min subway from Higashi-Shinjuku (Oedo Line to Kuramae, walk 10 min).'

    db.session.commit()
    print("  Migration complete: itinerary updated for Sotetsu Fresa Inn.")


def _migrate_book_takanoyu(app):
    """Book TAKANOYU Airbnb as Takayama accommodation (Apr 9-12, 3 nights).
    Idempotent — skips if TAKANOYU already exists."""
    from models import AccommodationOption, AccommodationLocation, ChecklistItem

    existing = AccommodationOption.query.filter(
        AccommodationOption.name.ilike('%TAKANOYU%')).first()
    if existing:
        return

    print("Running migration: book TAKANOYU Takayama Airbnb...")

    tak_loc = AccommodationLocation.query.filter_by(location_name='Takayama').first()
    if not tak_loc:
        return

    # Add TAKANOYU as booked option
    takanoyu = AccommodationOption(
        location_id=tak_loc.id,
        rank=0,
        name='TAKANOYU, Traditional Style, Spa & Sauna',
        property_type='Airbnb Private Room · Traditional Bathhouse Inn',
        price_low=None,
        price_high=None,
        total_low=None,
        total_high=None,
        breakfast_included=False,
        has_onsen=True,
        standout='BOOKED via Airbnb. Traditional bathhouse inn hosted by Hiroto. Two indoor baths (41/43°C), open-air bath (38°C), wood-fired wet sauna, cold plunge. Tattoo-friendly. ~20 min walk or 5 min drive from JR Takayama Station.',
        booking_url='https://takanoyu.jimdofree.com/',
        alt_booking_url='https://www.airbnb.com/s/Takayama--Gifu--Japan/homes?query=takanoyu',
        maps_url='https://www.google.com/maps/search/?api=1&query=TAKANOYU+107+Soyujimachi+Takayama+Gifu+Japan',
        is_selected=True,
        booking_status='booked',
        confirmation_number='Airbnb (confirmed by host Hiroto)',
        address='107 Soyujimachi, Takayama, Gifu 506-0834, Japan',
        check_in_info='3:00 PM (Thursday, April 9)',
        check_out_info='11:00 AM (Sunday, April 12)',
        user_notes='Soyujimachi area, Takayama — ~20 min walk or 5 min drive from JR Takayama Station. '
                   'Traditional bathhouse with indoor baths, open-air rotenburo, wood-fired wet sauna, and cold plunge. '
                   'Bathhouse hours: 1:00 PM - 10:00 PM (closed Wednesdays). Tattoo-friendly. '
                   'Private room in a traditional Japanese home. Host: Hiroto. 2 adults, 3 nights.',
    )
    db.session.add(takanoyu)

    # Deselect all other Takayama options (don't delete — user said don't delete data)
    for opt in AccommodationOption.query.filter_by(location_id=tak_loc.id).all():
        if 'TAKANOYU' not in (opt.name or '').upper():
            opt.is_selected = False

    # Update checklist items for Takayama booking
    for ci in ChecklistItem.query.filter(
        ChecklistItem.title.ilike('%Takayama ryokan%')
    ).all():
        ci.is_completed = True
        ci.status = 'booked'
        ci.title = 'Book Takayama accommodation (TAKANOYU Airbnb)'

    for ci in ChecklistItem.query.filter(
        ChecklistItem.title.ilike('%Takayama budget%')
    ).all():
        ci.is_completed = True
        ci.status = 'booked'
        ci.title = 'Book Takayama budget night (covered by TAKANOYU 3-night stay)'

    db.session.commit()
    print("  Migration complete: TAKANOYU booked for Takayama (Apr 9-12).")


def _migrate_update_itinerary_for_takanoyu(app):
    """Update itinerary activities to reference booked TAKANOYU accommodation.
    Idempotent — skips if already updated."""
    from models import Activity, Day

    sentinel = Activity.query.filter(
        Activity.title.ilike('%Check into TAKANOYU%')).first()
    if sentinel:
        return

    print("Running migration: update itinerary for TAKANOYU...")

    # Day 5 (Apr 9): Check into ryokan → Check into TAKANOYU
    day5 = Day.query.filter_by(day_number=5).first()
    if day5:
        for act in Activity.query.filter_by(day_id=day5.id).all():
            if act.title == 'Check into ryokan':
                act.title = 'Check into TAKANOYU'
                act.description = (
                    'Airbnb private room in a traditional bathhouse inn. '
                    'Address: 107 Soyujimachi, Takayama (~20 min walk or 5 min taxi from station). '
                    'Check-in at 3:00 PM. Host: Hiroto. Drop bags and settle in.'
                )
                act.address = '107 Soyujimachi, Takayama, Gifu 506-0834, Japan'
                act.maps_url = 'https://www.google.com/maps/search/?api=1&query=TAKANOYU+107+Soyujimachi+Takayama+Gifu+Japan'
            elif act.title == 'Multi-course kaiseki dinner at ryokan':
                act.title = 'Dinner out in Takayama old town'
                act.description = (
                    'TAKANOYU does not include dinner. Head to the old town for Hida beef — '
                    'try grilled wagyu, hoba miso, or Hida beef sushi. Many restaurants along Sanmachi Suji.'
                )
            elif act.title == 'Onsen bath at ryokan':
                act.title = 'Evening soak at TAKANOYU bathhouse'
                act.description = (
                    'Two indoor baths (41°C and 43°C), open-air rotenburo (38°C), '
                    'wood-fired wet sauna, and cold plunge. Bathhouse open 1 PM - 10 PM. Tattoo-friendly!'
                )

    # Day 6 (Apr 10): Update ryokan breakfast reference
    day6 = Day.query.filter_by(day_number=6).first()
    if day6:
        for act in Activity.query.filter_by(day_id=day6.id).all():
            if 'Ryokan breakfast' in (act.title or ''):
                act.title = 'Breakfast at morning market or local spot'
                act.description = (
                    'TAKANOYU does not include breakfast. Grab something at Miyagawa Morning Market '
                    '(local farmers, pickles, snacks) or find a kissaten (retro coffee shop) nearby.'
                )

    # Day 7 (Apr 11): Update afternoon onsen reference
    day7 = Day.query.filter_by(day_number=7).first()
    if day7:
        for act in Activity.query.filter_by(day_id=day7.id).all():
            if 'Afternoon onsen soak' in (act.title or ''):
                act.title = 'Afternoon soak at TAKANOYU'
                act.description = (
                    'Head back to your bathhouse inn for a relaxing soak. '
                    'Indoor baths, open-air rotenburo, wood-fired sauna, cold plunge. Open 1 PM - 10 PM.'
                )

    # Day 8 (Apr 12): Update check-out
    day8 = Day.query.filter_by(day_number=8).first()
    if day8:
        for act in Activity.query.filter_by(day_id=day8.id).all():
            if 'Check out of Takayama accommodation' in (act.title or ''):
                act.title = 'Check out of TAKANOYU (by 11:00 AM)'
                act.description = (
                    'Pack up and check out by 11 AM. ~20 min walk or quick taxi to Takayama Bus Center '
                    'for the Nohi Bus to Shirakawa-go.'
                )

    db.session.commit()
    print("  Migration complete: itinerary updated for TAKANOYU.")


def _migrate_audit_data_fixes(app):
    """Fix data issues identified in frontend audit.
    Covers: JR Pass pricing, Osaka dates, stale transit, reference corrections,
    checklist cleanup, missing activities, and luggage forwarding.
    Idempotent — each fix checks before applying."""
    from models import (ChecklistItem, ChecklistOption, AccommodationLocation,
                        AccommodationOption, TransportRoute, ReferenceContent,
                        Activity, Day)

    # Sentinel: skip if already applied (check for the luggage forwarding task we add)
    if ChecklistItem.query.filter(ChecklistItem.title.ilike('%luggage forwarding%')).first():
        return

    print("Running migration: audit data fixes...")

    # --- 1. JR Pass checklist options: ¥50,000 → ¥80,000 ---
    jr_item = ChecklistItem.query.filter(ChecklistItem.title.ilike('%JR Pass%')).first()
    if jr_item:
        for opt in jr_item.options:
            if opt.price_note and '50,000' in opt.price_note:
                opt.price_note = opt.price_note.replace('50,000', '80,000')
            elif opt.price_note and '55,000' in opt.price_note:
                opt.price_note = opt.price_note.replace('55,000', '80,000')

    # --- 2. Osaka accommodation: extend to 2 nights (checkout Apr 18) ---
    osaka_loc = AccommodationLocation.query.filter_by(location_name='Osaka').first()
    if osaka_loc and osaka_loc.num_nights == 1:
        from datetime import date as dt_date
        osaka_loc.num_nights = 2
        osaka_loc.check_out_date = dt_date(2026, 4, 18)
        osaka_loc.quick_notes = 'Two nights in Osaka. Namba/Dotonbori for street food and nightlife.'

    # --- 3. Remove stale Hakone transit from Day 5 ---
    day5 = Day.query.filter_by(day_number=5).first()
    if day5:
        stale_route = TransportRoute.query.filter_by(day_id=day5.id).filter(
            TransportRoute.route_from.ilike('%Odawara%'),
            TransportRoute.route_to.ilike('%Hakone%')
        ).first()
        if stale_route:
            db.session.delete(stale_route)

    # --- 4. Fix JR Pass reference content ---
    jr_ref = ReferenceContent.query.filter_by(title='JR Pass Info').first()
    if jr_ref and jr_ref.content:
        jr_ref.content = jr_ref.content.replace(
            'Activate: Day 5 (April 8)',
            'Activate: Day 4 (April 8)')
        jr_ref.content = jr_ref.content.replace(
            'Expires: April 21 (covers everything through departure)',
            'Expires: April 21 (activated April 8 — valid through departure April 18 with 3 days to spare)')

    # --- 5. Remove Kanazawa hotel checklist item ---
    kanazawa_hotel = ChecklistItem.query.filter(
        ChecklistItem.title.ilike('%Kanazawa hotel%')).first()
    if kanazawa_hotel:
        # Delete its options first
        for opt in kanazawa_hotel.options:
            db.session.delete(opt)
        db.session.delete(kanazawa_hotel)

    # --- 6. Update Nohi Bus checklist: Takayama → Shirakawa-go (not Kanazawa) ---
    nohi_item = ChecklistItem.query.filter(
        ChecklistItem.title.ilike('%Nohi Bus%Kanazawa%')).first()
    if nohi_item:
        nohi_item.title = 'Reserve Nohi Bus (Takayama → Shirakawa-go)'
        nohi_item.description = 'Highway bus from Takayama Bus Center to Shirakawa-go. ~50 min, reservations recommended.'

    # --- 7. Add Hakone return transit to Day 4 ---
    day4 = Day.query.filter_by(day_number=4).first()
    if day4:
        existing_return = Activity.query.filter_by(day_id=day4.id).filter(
            Activity.title.ilike('%return%Tokyo%')).first()
        if not existing_return:
            # Find the max sort_order for afternoon activities on Day 4
            max_sort = db.session.query(db.func.max(Activity.sort_order)).filter_by(
                day_id=day4.id).scalar() or 0
            return_transit = Activity(
                day_id=day4.id,
                title='Return to Tokyo from Hakone',
                description=(
                    'Hakone-Yumoto → Odawara (Hakone Tozan Railway, ~15 min) → '
                    'Tokyo/Shinjuku (Shinkansen Kodama/Hikari ~35 min, JR Pass covered). '
                    'Depart Hakone by ~5-6 PM to reach Shinjuku by ~7 PM.'
                ),
                time_slot='afternoon',
                category='transit',
                sort_order=max_sort + 1,
                jr_pass_covered=True,
            )
            db.session.add(return_transit)

        # Fix Dormy Inn reference in Day 4 evening activity
        for act in Activity.query.filter_by(day_id=day4.id).all():
            if 'Dormy Inn' in (act.description or '') or 'Dormy Inn' in (act.title or ''):
                if 'ramen' in (act.title or '').lower():
                    act.title = 'Last night in Shinjuku'
                    act.description = (
                        'Final evening in Tokyo before heading to the Alps tomorrow. '
                        'Revisit Golden Gai, grab late-night ramen, or explore Kabukicho neon.'
                    )

    # --- 8. Mark booked-flight checklist items as completed ---
    flight_items = ChecklistItem.query.filter(
        ChecklistItem.title.ilike('%Book Delta%') |
        ChecklistItem.title.ilike('%Book United%')
    ).all()
    for item in flight_items:
        if not item.is_completed:
            item.is_completed = True
            item.status = 'completed'

    # --- 9. Add luggage forwarding checklist item ---
    luggage_item = ChecklistItem(
        category='pre_departure_week',
        title='Arrange luggage forwarding (takkyubin) — Tokyo → Kyoto',
        description=(
            'On Day 4 evening: ask hotel front desk to ship large bags to your Kyoto '
            'accommodation via Yamato Transport (takkyubin). Cost: ~¥2,000-2,500/bag. '
            'Arrives next day. Travel light through Takayama/Shirakawa-go with daypacks only. '
            'Verify Kyoto accommodation accepts advance luggage delivery.'
        ),
        is_completed=False,
        status='pending',
        sort_order=50,
    )
    db.session.add(luggage_item)

    # --- 10. Fix Osaka checklist item title (1 night → 2 nights) ---
    osaka_checklist = ChecklistItem.query.filter(
        ChecklistItem.title.ilike('%Osaka hotel%1 night%')).first()
    if osaka_checklist:
        osaka_checklist.title = 'Book Osaka hotel (2 nights, Apr 16-18)'

    # --- 11. Mark Takayama ryokan checklist as completed if TAKANOYU is booked ---
    takayama_checklist = ChecklistItem.query.filter(
        ChecklistItem.title.ilike('%Takayama ryokan%')).first()
    takanoyu = AccommodationOption.query.filter(
        AccommodationOption.name.ilike('%TAKANOYU%'),
        AccommodationOption.is_selected == True
    ).first()
    if takayama_checklist and takanoyu:
        takayama_checklist.is_completed = True
        takayama_checklist.status = 'completed'
        takayama_checklist.title = 'Takayama: TAKANOYU booked (Apr 9-12)'

    # --- 12. Mark Piece Hostel checklist if Kyoto accommodation is booked ---
    kyoto_checklist = ChecklistItem.query.filter(
        ChecklistItem.title.ilike('%Piece Hostel%')).first()
    kyoto_booked = AccommodationOption.query.join(AccommodationLocation).filter(
        AccommodationLocation.location_name.ilike('%Kyoto%'),
        AccommodationOption.is_selected == True,
        AccommodationOption.booking_status.in_(['booked', 'confirmed'])
    ).first()
    if kyoto_checklist and kyoto_booked:
        kyoto_checklist.is_completed = True
        kyoto_checklist.status = 'completed'
        kyoto_checklist.title = f'Kyoto: {kyoto_booked.name} booked'

    # --- 13. Fix Sotetsu Fresa selection if not already selected (local DB fix) ---
    sotetsu = AccommodationOption.query.filter(
        AccommodationOption.name.ilike('%Sotetsu Fresa%')).first()
    if sotetsu and not sotetsu.is_selected:
        sotetsu.is_selected = True
        sotetsu.booking_status = 'booked'
        sotetsu.confirmation_number = sotetsu.confirmation_number or '976558450'
        # Mark Tokyo checklist item as completed
        tokyo_checklist = ChecklistItem.query.filter(
            ChecklistItem.title.ilike('%Tokyo hotel%')).first()
        if tokyo_checklist:
            tokyo_checklist.is_completed = True
            tokyo_checklist.status = 'completed'
            tokyo_checklist.title = 'Tokyo: Sotetsu Fresa Inn booked (Apr 6-9)'

    db.session.commit()
    print("  Migration complete: audit data fixes applied.")


def _migrate_hiroshima_time_warning(app):
    """Add time estimate note to Hiroshima day trip (Day 11).
    Idempotent — skips if notes already contain the estimate."""
    from models import Day

    day11 = Day.query.filter_by(day_number=11).first()
    if not day11:
        return
    if day11.notes and 'hours transit' in day11.notes:
        return

    print("Running migration: Hiroshima time warning...")
    day11.notes = (
        (day11.notes + '\n\n' if day11.notes else '') +
        'Tight schedule: ~10-11 hours total including ~3.5 hours transit. '
        'Leave Kyoto by 7:30 AM, return ~7-8 PM. If the Peace Museum runs long, '
        'see Miyajima torii from the ferry only and skip the shrine interior to save 45 min. '
        'The Himeji + Nara alternative is less rushed if you prefer a slower day.'
    )
    db.session.commit()
    print("  Migration complete: Hiroshima time warning added.")


def _migrate_transport_checklist_and_data_fixes(app):
    """Phase 1-2-4: Add missing transport checklist items, fix remaining data issues.
    Idempotent — checks for sentinel (Hakone Free Pass checklist item)."""
    from models import (ChecklistItem, ChecklistOption, TransportRoute,
                        Day, ReferenceContent, AccommodationLocation,
                        AccommodationOption)

    # Sentinel: skip if Hakone Free Pass checklist already exists
    if ChecklistItem.query.filter(
            ChecklistItem.title.ilike('%Hakone Free Pass%')).first():
        return

    print("Running migration: transport checklist items + data fixes...")

    # --- PHASE 1.2: Hakone Free Pass ---
    hakone_item = ChecklistItem(
        title="Buy Hakone Free Pass at Odawara Station",
        category="pre_departure_today",
        item_type="task",
        status="pending",
        is_completed=False,
        sort_order=5,
    )
    db.session.add(hakone_item)
    db.session.flush()
    db.session.add(ChecklistOption(
        checklist_item_id=hakone_item.id,
        name="Hakone Free Pass (2-day)",
        price_note="~¥6,000/person",
        description="Covers Hakone Loop: switchback train, cable car, ropeway, "
                    "pirate ship, some buses. Buy at Odawara Station on Day 4.",
        url="https://www.hakonenavi.jp/international/en/tickets/freepass/",
        sort_order=1,
    ))

    # --- PHASE 1.3: Welcome Suica IC Card ---
    suica_item = ChecklistItem(
        title="Pick up Welcome Suica IC card at Haneda",
        category="pre_departure_today",
        item_type="task",
        status="pending",
        is_completed=False,
        sort_order=6,
    )
    db.session.add(suica_item)
    db.session.flush()
    db.session.add(ChecklistOption(
        checklist_item_id=suica_item.id,
        name="Welcome Suica (physical card)",
        price_note="¥1,000 deposit + ¥3,000 initial load",
        description="Pick up at Haneda Airport JR counter on arrival (Day 2). "
                    "Works on all subways, buses, convenience stores, vending machines. "
                    "Valid 28 days.",
        sort_order=1,
    ))
    db.session.add(ChecklistOption(
        checklist_item_id=suica_item.id,
        name="Mobile Suica (Apple Wallet)",
        price_note="Free app + ¥3,000 initial load",
        description="Add to Apple Wallet before departure. Load yen via credit card. "
                    "No physical card needed.",
        url="https://support.apple.com/en-us/HT207154",
        sort_order=2,
    ))

    # --- PHASE 1.4: Shirakawa-go → Kyoto second leg ---
    shira_item = ChecklistItem(
        title="Book Shirakawa-go → Kanazawa bus + JR Kanazawa → Kyoto",
        category="pre_departure_today",
        item_type="task",
        status="pending",
        is_completed=False,
        sort_order=5,
    )
    db.session.add(shira_item)
    db.session.flush()
    db.session.add(ChecklistOption(
        checklist_item_id=shira_item.id,
        name="Nohi Bus Shirakawa-go → Kanazawa + JR Thunderbird Kanazawa → Kyoto",
        price_note="~¥2,800 bus + JR Pass covers train",
        description="Bus ~1hr15min to Kanazawa, then JR Thunderbird ~2hr15min to Kyoto. "
                    "Reserve bus in advance.",
        url="https://www.nouhibus.co.jp/english/",
        sort_order=1,
    ))
    db.session.add(ChecklistOption(
        checklist_item_id=shira_item.id,
        name="Direct bus Shirakawa-go → Kyoto (if available)",
        price_note="Check availability",
        description="Some seasonal direct buses exist. Check Nohi Bus or Hokutetsu schedules.",
        sort_order=2,
    ))

    # --- PHASE 1.5: Haneda → Shinjuku arrival transfer ---
    haneda_item = ChecklistItem(
        title="Plan Haneda → Shinjuku arrival transfer",
        category="pre_departure_month",
        item_type="task",
        status="pending",
        is_completed=False,
        sort_order=22,
    )
    db.session.add(haneda_item)
    db.session.flush()
    db.session.add(ChecklistOption(
        checklist_item_id=haneda_item.id,
        name="Keikyu Line + subway",
        price_note="~¥600-800",
        description="Keikyu Airport Express to Shinagawa, transfer to JR Yamanote "
                    "or subway to Shinjuku. ~60-75 min total.",
        sort_order=1,
    ))
    db.session.add(ChecklistOption(
        checklist_item_id=haneda_item.id,
        name="Airport Limousine Bus",
        price_note="~¥1,300",
        description="Direct bus Haneda → Shinjuku Expressway Bus Terminal. "
                    "~60-85 min depending on traffic. No transfers.",
        url="https://www.limousinebus.co.jp/en/",
        sort_order=2,
    ))
    db.session.add(ChecklistOption(
        checklist_item_id=haneda_item.id,
        name="Taxi / private transfer",
        price_note="~¥8,000-12,000",
        description="Direct door-to-door. Expensive but zero navigation needed "
                    "after a 14-hour flight.",
        sort_order=3,
    ))

    # --- PHASE 2.1: Fix JR Pass station purchase price ---
    opt6 = ChecklistOption.query.filter(
        ChecklistOption.name.ilike('%Buy at JR Station%')).first()
    if opt6 and '88,000' not in (opt6.price_note or ''):
        opt6.price_note = "~¥88,000/pp (station markup)"

    # --- PHASE 2.2: Fix Piece Hostel stale checklist ---
    piece = ChecklistItem.query.filter(
        ChecklistItem.title.ilike('%Piece Hostel%')).first()
    if piece and not piece.is_completed:
        # Check if Kyoto accommodation is actually booked
        kyoto_booked = AccommodationOption.query.join(AccommodationLocation).filter(
            AccommodationLocation.location_name.ilike('%Kyoto%'),
            AccommodationOption.is_selected == True,
            AccommodationOption.booking_status.in_(['booked', 'confirmed'])
        ).first()
        if kyoto_booked:
            piece.title = f'Kyoto: {kyoto_booked.name} booked'
            piece.status = 'completed'
            piece.is_completed = True
        else:
            # On local DB where nothing is booked, just mark the name as generic
            piece.title = 'Book Kyoto accommodation (4 nights, Apr 12-16)'

    # --- PHASE 2.8: Fix luggage forwarding reference day ---
    ref23 = ReferenceContent.query.filter(
        ReferenceContent.title.ilike('%Luggage%Takkyubin%')).first()
    if ref23 and 'Day 5' in ref23.content:
        ref23.content = ref23.content.replace(
            'Use on Day 5: send bags from Tokyo to Kyoto, travel light through Alps.',
            'Use on Day 4 evening or Day 5 morning: ask Sotetsu Fresa Inn front desk '
            'to ship bags to your Kyoto accommodation via takkyubin. Bags arrive next '
            'day. Travel light through the Alps.')

    # --- PHASE 2.10: Add Hakone return TransportRoute ---
    day4 = Day.query.filter_by(day_number=4).first()
    if day4:
        existing = TransportRoute.query.filter_by(day_id=day4.id).filter(
            TransportRoute.route_to.ilike('%Tokyo%') |
            TransportRoute.route_to.ilike('%Shinjuku%')).first()
        if not existing:
            max_sort = db.session.query(
                db.func.max(TransportRoute.sort_order)
            ).filter_by(day_id=day4.id).scalar() or 0
            db.session.add(TransportRoute(
                route_from="Hakone-Yumoto",
                route_to="Tokyo (Shinjuku)",
                transport_type="train",
                train_name="Hakone Tozan → Odawara, then Shinkansen",
                duration="~1 hour total",
                jr_pass_covered=True,
                cost_if_not_covered="Hakone Free Pass covers Hakone→Odawara",
                day_id=day4.id,
                sort_order=max_sort + 1,
            ))

    # --- PHASE 4.2: Day 1 and Day 14 flight carrier notes ---
    day1 = Day.query.filter_by(day_number=1).first()
    if day1 and not day1.notes:
        day1.notes = ("Outbound flights are Delta (separate cash booking). "
                      "Check in via Delta app.")

    day14 = Day.query.filter_by(day_number=14).first()
    if day14 and not day14.notes:
        day14.notes = ("Return flights are United (MileagePlus award). "
                       "Check in via United app. Confirmation: I91ZHJ")

    db.session.commit()
    print("  Migration complete: transport checklist + data fixes applied.")


def _migrate_remove_kanazawa_hotel(app):
    """Remove orphaned 'Book Kanazawa hotel' checklist item — no Kanazawa overnight
    exists in the 14-day itinerary. Idempotent — no-op if item doesn't exist."""
    from models import ChecklistItem, ChecklistOption

    kanazawa_hotel = ChecklistItem.query.filter(
        ChecklistItem.title.ilike('%kanazawa hotel%')).first()
    if not kanazawa_hotel:
        return

    print("Running migration: removing orphaned Kanazawa hotel checklist...")
    ChecklistOption.query.filter_by(
        checklist_item_id=kanazawa_hotel.id).delete()
    db.session.delete(kanazawa_hotel)
    db.session.commit()
    print("  Migration complete: Kanazawa hotel item removed.")


def _migrate_production_data_reapply(app):
    """Re-apply audit fixes that were skipped on production.
    The original _migrate_audit_data_fixes used a sentinel (luggage forwarding
    checklist item) that already existed on production, causing the entire
    migration to be skipped. This migration applies each fix individually
    with its own idempotency check.
    Sentinel: checks for a marker note on the Trip record."""
    from models import (ChecklistItem, ChecklistOption, AccommodationLocation,
                        AccommodationOption, TransportRoute, ReferenceContent,
                        Activity, Day, Trip, Flight)
    from datetime import date as dt_date

    # Sentinel: use a unique marker to avoid re-running
    trip = Trip.query.first()
    if trip and trip.notes and '__prod_reapply_v1' in trip.notes:
        return

    print("Running migration: production data re-apply...")
    changed = False

    # --- 1. JR Pass checklist options: fix ALL pricing ---
    jr_item = ChecklistItem.query.filter(
        ChecklistItem.title.ilike('%JR Pass%')).first()
    if jr_item:
        for opt in ChecklistOption.query.filter_by(
                checklist_item_id=jr_item.id).all():
            pn = opt.price_note or ''
            if '50,000' in pn:
                opt.price_note = pn.replace('50,000', '80,000')
                changed = True
            elif '55,000' in pn:
                opt.price_note = '~¥88,000/pp (station markup)'
                changed = True

    # --- 2. Osaka accommodation: extend to 2 nights ---
    osaka_loc = AccommodationLocation.query.filter(
        AccommodationLocation.location_name.ilike('%Osaka%')).first()
    if osaka_loc:
        if osaka_loc.num_nights == 1:
            osaka_loc.num_nights = 2
            osaka_loc.check_out_date = dt_date(2026, 4, 18)
            changed = True
        if osaka_loc.quick_notes and 'wild night' in osaka_loc.quick_notes.lower():
            osaka_loc.quick_notes = ('Two nights in Osaka. Namba/Dotonbori '
                                     'for street food and nightlife.')
            changed = True

    # --- 3. Remove stale Hakone transit from Day 5 ---
    day5 = Day.query.filter_by(day_number=5).first()
    if day5:
        stale_route = TransportRoute.query.filter_by(day_id=day5.id).filter(
            TransportRoute.route_from.ilike('%Odawara%'),
            TransportRoute.route_to.ilike('%Hakone%')
        ).first()
        if stale_route:
            db.session.delete(stale_route)
            changed = True

    # --- 4. Fix JR Pass reference content (Day 5 → Day 4, expiry wording) ---
    jr_ref = ReferenceContent.query.filter(
        ReferenceContent.title.ilike('%JR Pass%')).first()
    if jr_ref and jr_ref.content:
        if 'Day 5 (April 8)' in jr_ref.content:
            jr_ref.content = jr_ref.content.replace(
                'Day 5 (April 8)', 'Day 4 (April 8)')
            changed = True
        if 'Day 5 (Apr 8)' in jr_ref.content:
            jr_ref.content = jr_ref.content.replace(
                'Day 5 (Apr 8)', 'Day 4 (Apr 8)')
            changed = True
        if 'covers everything through departure' in jr_ref.content:
            jr_ref.content = jr_ref.content.replace(
                'Expires: April 21 (covers everything through departure)',
                'Expires: April 21 (activated April 8 — valid through '
                'departure April 18 with 3 days to spare)')
            changed = True

    # --- 5. Fix Osaka checklist title (1 night → 2 nights) ---
    osaka_checklist = ChecklistItem.query.filter(
        ChecklistItem.title.ilike('%Osaka hotel%1 night%')).first()
    if osaka_checklist:
        osaka_checklist.title = 'Book Osaka hotel (2 nights, Apr 16-18)'
        changed = True

    # --- 6. Create Delta outbound checklist if missing ---
    delta_item = ChecklistItem.query.filter(
        ChecklistItem.title.ilike('%Delta%outbound%')).first()
    if not delta_item:
        delta_item = ChecklistItem.query.filter(
            ChecklistItem.title.ilike('%Delta%CLE%')).first()
    if not delta_item:
        db.session.add(ChecklistItem(
            title='Book Delta outbound CLE -> DTW -> HND',
            category='pre_departure_today',
            item_type='task',
            status='completed',
            is_completed=True,
            sort_order=1,
        ))
        changed = True

    # --- 7. Mark booked-flight checklist items as completed ---
    for pattern in ['%Book Delta%', '%Book United%']:
        items = ChecklistItem.query.filter(
            ChecklistItem.title.ilike(pattern)).all()
        for item in items:
            if not item.is_completed:
                item.is_completed = True
                item.status = 'completed'
                changed = True

    # --- 8. Update Nohi Bus title if still references Kanazawa ---
    nohi_item = ChecklistItem.query.filter(
        ChecklistItem.title.ilike('%Nohi Bus%Kanazawa%')).first()
    if nohi_item:
        nohi_item.title = 'Reserve Nohi Bus (Takayama → Shirakawa-go)'
        changed = True

    # --- 9. Mark Piece Hostel / Kyoto checklist if Kyoto is booked ---
    kyoto_checklist = ChecklistItem.query.filter(
        ChecklistItem.title.ilike('%Piece Hostel%')).first()
    if kyoto_checklist:
        kyoto_booked = AccommodationOption.query.join(
            AccommodationLocation
        ).filter(
            AccommodationLocation.location_name.ilike('%Kyoto%'),
            AccommodationOption.is_selected == True,
            AccommodationOption.booking_status.in_(['booked', 'confirmed'])
        ).first()
        if kyoto_booked:
            kyoto_checklist.is_completed = True
            kyoto_checklist.status = 'completed'
            kyoto_checklist.title = f'Kyoto: {kyoto_booked.name} booked'
            changed = True

    # --- 10. Mark Takayama ryokan checklist if TAKANOYU is booked ---
    tak_checklist = ChecklistItem.query.filter(
        ChecklistItem.title.ilike('%Takayama ryokan%')).first()
    if tak_checklist and not tak_checklist.is_completed:
        takanoyu = AccommodationOption.query.filter(
            AccommodationOption.name.ilike('%TAKANOYU%'),
            AccommodationOption.is_selected == True
        ).first()
        if takanoyu:
            tak_checklist.is_completed = True
            tak_checklist.status = 'completed'
            tak_checklist.title = 'Takayama: TAKANOYU booked (Apr 9-12)'
            changed = True

    # --- 11. Fix luggage forwarding reference (Day 5 → Day 4) ---
    ref_luggage = ReferenceContent.query.filter(
        ReferenceContent.title.ilike('%Luggage%')).first()
    if ref_luggage and 'Day 5' in (ref_luggage.content or ''):
        ref_luggage.content = ref_luggage.content.replace(
            'Use on Day 5: send bags from Tokyo to Kyoto, travel light through Alps.',
            'Use on Day 4 evening or Day 5 morning: ask Sotetsu Fresa Inn front desk '
            'to ship bags to your Kyoto accommodation via takkyubin. Bags arrive next '
            'day. Travel light through the Alps.')
        changed = True

    # --- 12. Fix transport route: Odawara→Hakone on wrong day ---
    # Some DBs may have the route on Day 5 (id varies) instead of Day 4
    day4 = Day.query.filter_by(day_number=4).first()
    if day4 and day5:
        misplaced = TransportRoute.query.filter_by(day_id=day5.id).filter(
            TransportRoute.route_from.ilike('%Odawara%')).first()
        if misplaced:
            misplaced.day_id = day4.id
            changed = True

    # Write sentinel marker
    if trip:
        trip.notes = ((trip.notes or '') + '\n__prod_reapply_v1').strip()
    db.session.commit()
    if changed:
        print("  Migration complete: production data re-apply applied fixes.")
    else:
        print("  Migration complete: production data already up to date.")


def _migrate_add_transit_directions(app):
    """Add getting_there transit directions to activities that are missing them.
    Only updates activities where getting_there is NULL or empty.
    Matches by title (ILIKE) for production safety."""
    from models import Activity, Day, Trip

    # Sentinel: check Trip.notes for marker
    trip = Trip.query.first()
    if trip and trip.notes and '__transit_dirs_v1' in trip.notes:
        return

    print("Running migration: add transit directions...")
    count = 0

    # Map of title patterns → getting_there text
    directions = [
        # Day 2 — ARRIVE TOKYO
        ('%Train to Higashi-Shinjuku%',
         'From Haneda Airport: Keikyu Line to Shinagawa, then JR Yamanote Line to Shinjuku, '
         'then Oedo Line or Fukutoshin Line one stop to Higashi-Shinjuku. ~75-90 min total. '
         'Or Airport Limousine Bus direct to Shinjuku Bus Terminal (~60-85 min) then 10 min walk.'),
        ('%Light dinner nearby%',
         'Walk from hotel. Shinjuku area has ramen shops, conveyor belt sushi, and 7-Elevens '
         'within 5 min walk in every direction.'),
        ('%Senso-ji%night%',
         'Oedo Line from Higashi-Shinjuku to Kuramae Station (~25 min), then 10 min walk to '
         'Senso-ji. Or Fukutoshin Line to Asakusa via transfer (~30 min).'),
        ('%Kabukicho%night%',
         'Walk from hotel \u2014 Kabukicho is 5 min walk from Higashi-Shinjuku Station. '
         'Golden Gai and Omoide Yokocho are on the west side of Shinjuku Station (10 min walk).'),
        ('%Late-night ramen%',
         'Walk from hotel. Fuunji (tsukemen specialist) is 5 min walk toward Shinjuku Station. '
         'Dozens of ramen shops within 10 min radius. Open late.'),

        # Day 3 — FULL TOKYO DAY (alternates)
        ('%Robot Restaurant%',
         'In Kabukicho, Shinjuku \u2014 5 min walk from hotel. Near Golden Gai.'),
        ('%Shimokitazawa%',
         'Odakyu Line from Shinjuku Station to Shimokitazawa (3 min express, 7 min local). '
         'South exit, walk into the neighborhood.'),
        ('%Yozakura%Chidorigafuchi%',
         'Hanzomon Line from nearest connection to Kudanshita Station (Exit 2). Or Toei Shinjuku '
         'Line from Shinjuku-Sanchome to Kudanshita (~15 min). Boat rental area is 5 min walk.'),

        # Day 4 — HAKONE (alternates)
        ('%NIKKO%',
         'JR Shinkansen to Utsunomiya (~50 min, JR Pass covered), then JR Nikko Line to Nikko '
         'Station (~45 min). Total ~2 hours from Tokyo. Alternatively, Tobu Railway from Asakusa '
         '(faster but not JR Pass).'),
        ('%Last night in Shinjuku%',
         'Return from Hakone: Hakone Tozan train to Odawara, then Shinkansen to Tokyo (~1 hour '
         'total). Back at hotel area by ~7 PM.'),

        # Day 5 — TOKYO → TAKAYAMA
        ('%Dinner%Takayama old town%',
         '10 min walk from TAKANOYU to Sanmachi Suji old town. Cross the Miyagawa River bridge. '
         'Many restaurants along the preserved streets.'),
        ('%Evening soak%TAKANOYU%',
         'Walk back to TAKANOYU. Bathhouse open 1:00 PM - 10:00 PM. Last entry 9:30 PM.'),

        # Day 6 — FULL DAY TAKAYAMA
        ('%Breakfast%morning market%',
         'At or near the Miyagawa Morning Market \u2014 many food stalls along the river. '
         'Or find a kissaten (retro coffee shop) within 5 min walk of the market area.'),
        ('%Sanmachi Suji%sake brew%',
         'Walk from lunch area \u2014 Sanmachi Suji is the central old town district, 10 min walk '
         'from the station. Everything is walkable within the district.'),
        ('%Lantern-lit%night walk%',
         'Walk from dinner \u2014 the old town streets are all within 10 min of each other. '
         'No transit needed. Lanterns come on at dusk.'),
        ('%Izakaya%Hida beef%',
         'Walk through Sanmachi Suji area. Many izakayas along the old town streets and near the '
         'station. Try streets south of Kokubunji-dori.'),

        # Day 7 — TAKAYAMA DAY 3
        ('%Miyagawa Morning Market%round 2%',
         'Same route as Day 6: 10 min walk from TAKANOYU along the Miyagawa River east bank.'),
        ('%Breakfast%kissaten%',
         'Walk from morning market. Several kissaten (retro coffee shops) in the old town area '
         'near Sanmachi Suji.'),
        ('%Rent bikes%Miyagawa%',
         'Bike rentals available near JR Takayama Station and in the old town area. Some '
         'guesthouses also rent bikes. ~\u00a5200-500/hour.'),
        ('%Train to Hida-Furukawa%',
         'JR Takayama Line from JR Takayama Station to Hida-Furukawa (15 min, JR Pass covered). '
         'Trains roughly every hour. Check schedule.'),
        ('%Furukawa%White-Walled%',
         '5 min walk from JR Hida-Furukawa Station. Exit station, walk south along the main '
         'street to the canal district.'),
        ('%Hida Crafts%sake tasting%',
         'Walk within Hida-Furukawa \u2014 the town is compact. Watanabe Sake Brewery is 5 min '
         'walk from the canal. Museum is near the station.'),
        ('%Train back to Takayama%',
         'JR Takayama Line from Hida-Furukawa back to JR Takayama Station (15 min). '
         'Same line, reverse direction.'),
        ('%Afternoon soak%TAKANOYU%',
         'Walk or taxi from JR Takayama Station back to TAKANOYU (~20 min walk, 5 min taxi). '
         'Bathhouse open 1:00 PM - 10:00 PM.'),
        ('%Final Hida beef dinner%',
         'Walk to Sanmachi Suji old town (10 min from TAKANOYU). For a special last night: '
         'try Le Midi (French-Japanese fusion) or Kyoya (traditional wagyu). Reserve ahead.'),

        # Day 8 — SHIRAKAWA-GO transit
        ('%Village lunch%',
         'Within Shirakawa-go village \u2014 several small restaurants near the bus terminal and '
         'main street. Try soba noodles or local river fish.'),

        # Day 9 — KYOTO DAY 1
        ('%Keihan Line%',
         'Take the Keihan Line from Fushimi-Inari Station to Gion-Shijo or Kiyomizu-Gojo. '
         '~10 min ride. Connects the shrine to central Kyoto/Gion area.'),
        ('%Fushimi sake%district%',
         'Walk south from Fushimi Inari Shrine (~15 min) to the Fushimi sake district. '
         'Or Keihan Line one stop to Chushojima. Gekkeikan Okura Museum is the main attraction.'),

        # Day 10 — KYOTO DAY 2
        ('%Kurama%Kibune%',
         'Eizan Railway from Demachiyanagi Station to Kurama (30 min). Demachiyanagi is reachable '
         'via Keihan Line from Gion-Shijo (~10 min). Hike from Kurama to Kibune (~1.5 hours) or '
         'take each separately.'),

        # Day 11 — HIROSHIMA & MIYAJIMA
        ('%Floating%Itsukushima%Torii%',
         'Walk from the JR Ferry terminal along the waterfront (~10 min). The torii gate is '
         'visible from the ferry. At low tide, walk out to it on the sand. At high tide, view '
         'from shore or take a small boat tour (~\u00a51,500).'),
        ('%HIMEJI%NARA%',
         'Shinkansen Kyoto \u2192 Himeji (~50 min, JR Pass covered). Castle is 15 min walk north '
         'from JR Himeji Station. Then JR Himeji \u2192 Nara (~1 hour via transfer at Osaka). '
         'Nara deer park is 5 min walk from JR Nara Station.'),

        # Day 12 — OSAKA DAY 1
        ('%Nijo Castle%tea ceremony%',
         'Kyoto Metro Tozai Line to Nijojo-mae Station (direct). Or bus #9/#50 from Kyoto '
         'Station. Castle is 1 min from the station exit.'),

        # Day 13 — OSAKA DAY 2
        ('%Morning coffee%konbini%',
         'Walk from Hotel The Leben \u2014 Shinsaibashi/Minamisemba area has Lawson, FamilyMart, '
         'and 7-Eleven within 2 min walk. For specialty coffee, try Lilo Coffee Roasters '
         '(~5 min walk).'),

        # Day 14 — DEPARTURE
        ('%Haneda Airport%last shopping%',
         'Already at Haneda after Shinagawa transfer. International Terminal (T3) has tax-free '
         'shops, ramen street, and souvenir stores past security. Arrive by 1:30 PM for 3:50 PM '
         'departure.'),

        # Logistics activities
        ('%Welcome Suica%',
         'At Haneda Airport arrivals \u2014 look for the JR East Travel Service Center in the '
         'arrivals hall. Open ~7:45 AM - 6:30 PM.'),
        ('%eSIM%pocket WiFi%',
         'eSIM: activate via app before or after landing (needs WiFi). Pocket WiFi: pick up at '
         'designated counter in Haneda arrivals hall.'),
        ('%Check into Sotetsu Fresa%',
         'From Higashi-Shinjuku Station (Oedo/Fukutoshin Lines): Exit B1, 1 min walk. '
         'From Shinjuku Station: 10 min walk east. Address: 7-27-9 Shinjuku.'),
        ('%ACTIVATE%JR Pass%',
         'JR ticket office (Midori no Madoguchi) at any major JR station. Tokyo Station is most '
         'convenient \u2014 go to the JR Central ticket office on the Marunouchi side. Bring your '
         'passport + exchange voucher.'),
        ('%Check into TAKANOYU%',
         'From JR Takayama Station: 20 min walk or 5 min taxi. Address: 107 Soyujimachi, '
         'Takayama. Host Hiroto may offer pickup \u2014 contact via Airbnb.'),
        ('%Check into Piece Hostel%',
         'NOTE: The booked accommodation is Tsukiya-Mikazuki, not Piece Hostel. From Kyoto '
         'Station: Karasuma Line to Gojo Station (1 stop, 3 min). 5 min walk to the machiya.'),
        ('%Check into%machiya%',
         'Kyotofish Teahouse in Miyagawacho. From Tsukiya-Mikazuki: walk north along Kamo River '
         '(~15 min) or bus to Gion-Shijo area. Self check-in via lockbox. Handle washi paper '
         'doors gently.'),
        ('%Check into Osaka%',
         'Hotel The Leben Osaka, Minamisemba. From Osaka Station: Midosuji Line to Shinsaibashi '
         'Station (~10 min). Exit 1, 3 min walk south.'),
    ]

    for pattern, direction_text in directions:
        # Use ILIKE for case-insensitive matching with wildcards
        acts = Activity.query.filter(
            Activity.title.ilike(pattern)
        ).all()
        for act in acts:
            if not act.getting_there:  # Only fill if empty/null
                act.getting_there = direction_text
                count += 1

    # Write sentinel marker
    if trip:
        trip.notes = ((trip.notes or '') + '\n__transit_dirs_v1').strip()
    db.session.commit()
    print(f"  Migration complete: added transit directions to {count} activities.")


def _migrate_restore_hakone_route(app):
    """Re-add the Odawara → Hakone (Loop) transport route on Day 4.
    It was accidentally deleted instead of moved in a prior migration."""
    from models import TransportRoute, Day

    # Check if it already exists
    existing = TransportRoute.query.filter(
        TransportRoute.route_from.ilike('%Odawara%'),
        TransportRoute.route_to.ilike('%Hakone%')
    ).first()
    if existing:
        return

    day4 = Day.query.filter_by(day_number=4).first()
    if not day4:
        return

    print("Running migration: restore Odawara → Hakone transport route...")

    # Find the sort_order — place it between Tokyo→Odawara and Hakone-Yumoto→Tokyo
    tokyo_odawara = TransportRoute.query.filter_by(
        day_id=day4.id, route_from='Tokyo').first()
    sort = (tokyo_odawara.sort_order + 1) if tokyo_odawara and tokyo_odawara.sort_order else 2

    db.session.add(TransportRoute(
        route_from='Odawara',
        route_to='Hakone (Loop)',
        transport_type='Hakone Tozan Railway',
        train_name='Switchback Train',
        jr_pass_covered=False,
        cost_if_not_covered='Hakone Free Pass',
        day_id=day4.id,
        sort_order=sort,
    ))
    db.session.commit()
    print("  Migration complete: Odawara → Hakone route restored on Day 4.")


def _migrate_calendar_warnings_and_data_v2(app):
    """Comprehensive migration: Day 14 timeline, time warnings, data fixes,
    transport routes, transit time estimates, checklist options."""
    from models import (Activity, Day, Trip, TransportRoute, ChecklistItem,
                        ChecklistOption)

    trip = Trip.query.first()
    if trip and trip.notes and '__cal_warnings_v2' in (trip.notes or ''):
        return

    changed = False
    print("Running migration: calendar warnings and data fixes v2...")

    # ---- HELPER ----
    def find_activity(title_pattern, day_number=None):
        q = Activity.query.filter(Activity.title.ilike(title_pattern))
        if day_number:
            day = Day.query.filter_by(day_number=day_number).first()
            if day:
                q = q.filter_by(day_id=day.id)
        return q.first()

    def find_day(day_number):
        return Day.query.filter_by(day_number=day_number).first()

    # ================================================================
    # 1. DAY 14 — Departure timeline warning
    # ================================================================
    day14 = find_day(14)
    if day14:
        timeline_text = (
            "\u26a0\ufe0f TIGHT DEPARTURE TIMELINE — Leave hotel by 8:00 AM\n"
            "8:00 AM  Leave Hotel The Leben (early checkout)\n"
            "8:30 AM  Subway to Shin-Osaka Station (~20 min)\n"
            "9:00 AM  Board Shinkansen Hikari to Shinagawa (~2h 30min)\n"
            "11:30 AM Arrive Shinagawa\n"
            "11:45 AM Keikyu Line to Haneda Airport (~15 min)\n"
            "12:00 PM Arrive Haneda Terminal 3 (International)\n"
            "12:00-1:30 PM  Last shopping, tax-free, check-in\n"
            "1:50 PM  Check-in cutoff (2 hours before departure)\n"
            "3:50 PM  UA876 departs HND \u2192 SFO"
        )
        if not day14.notes or 'TIGHT DEPARTURE' not in day14.notes:
            day14.notes = timeline_text
            changed = True

        # Update "Early checkout" activity start_time
        checkout_act = find_activity('%early checkout%', 14)
        if checkout_act and not checkout_act.start_time:
            checkout_act.start_time = '8:00 AM'
            changed = True

    # ================================================================
    # 2. TIME-SENSITIVE WARNINGS on specific activities
    # ================================================================

    # Day 2 — Suica pickup backup
    suica = find_activity('%Welcome Suica%')
    if suica:
        note = "JR Travel Service Center at Haneda closes ~6:30 PM. If you arrive after it closes, get Suica from any station vending machine instead (available 24/7, look for machines with English option)."
        if not suica.notes or 'closes' not in suica.notes:
            suica.notes = note
            changed = True

    # Day 3 — Sumo stable call reminder (call on Day 2 evening)
    sumo = find_activity('%Sumo%Morning Practice%')
    if sumo:
        note = "Call the stable 4-8 PM the day before (Apr 6) to confirm practice. You may arrive at the hotel around 7-8 PM \u2014 call immediately if you make it in time. If you miss the window, just show up at 6:45 AM on Apr 7 without confirmation."
        if not sumo.book_ahead_note or 'Call the stable' not in sumo.book_ahead_note:
            sumo.book_ahead_note = note
            changed = True

    # Day 4 — Hakone onsen last train warning
    onsen = find_activity('%Tenzan%')
    if onsen:
        note = "\u26a0\ufe0f LAST TRAIN: Onsen closes 10 PM but last Hakone Tozan train to Odawara departs ~9:30 PM. Leave the onsen by 8:30 PM to safely catch the return train to Tokyo."
        if not onsen.notes or 'LAST TRAIN' not in (onsen.notes or ''):
            onsen.notes = note
            changed = True

    # Day 8 — Shirakawa-go last bus
    day8 = find_day(8)
    if day8:
        bus_warning = "\u26a0\ufe0f LAST BUS: The last Nohi Bus from Shirakawa-go to Kanazawa departs ~3:00-4:00 PM. Plan to catch a bus by 2:30 PM. If you miss it, there is no train alternative from Shirakawa-go."
        if not day8.notes or 'LAST BUS' not in (day8.notes or ''):
            day8.notes = ((day8.notes or '') + '\n' + bus_warning).strip()
            changed = True

    # Day 11 — Miyajima return deadline
    torii = find_activity('%Itsukushima Torii%')
    if not torii:
        torii = find_activity('%Itsukushima Shrine%')
    if torii:
        note = "\u26a0\ufe0f RETURN DEADLINE: Leave Miyajima island by 5:30 PM to safely return to Kyoto. Ferry (10 min) + JR train to Hiroshima (25 min) + Shinkansen to Kyoto (1h 45min) = arrive Kyoto ~8:00 PM. Last Shinkansen Hiroshima\u2192Kyoto departs ~9:30 PM."
        if not torii.notes or 'RETURN DEADLINE' not in (torii.notes or ''):
            torii.notes = note
            changed = True

    # Day 2 — Hotel late arrival notice
    checkin = find_activity('%Check into Sotetsu Fresa%')
    if checkin:
        note = "Booking says 'if arriving after 9pm, contact hotel directly.' You should arrive ~7-8 PM, but flight delays could push this close. Consider messaging hotel via Agoda before departure to confirm late check-in is okay. Hotel phone: +81-3-6892-2032."
        if not checkin.notes or 'arriving after 9pm' not in (checkin.notes or ''):
            checkin.notes = note
            changed = True

    # ================================================================
    # 3. DATA FIX: Piece Hostel Sanjo → Tsukiya-Mikazuki
    # ================================================================
    piece = find_activity('%Piece Hostel Sanjo%')
    if piece:
        piece.title = "Check into Tsukiya-Mikazuki (Kyoto machiya B&B)"
        piece.getting_there = "From Kyoto Station: Karasuma Line to Gojo Station (1 stop, 3 min). 5 min walk to the machiya. Address: 139 Ebisucho, Shimogyo-ku. Host phone: +81 75-353-7920."
        changed = True

    # ================================================================
    # 4. TRANSPORT ROUTES — Fix Day 8 chain and add missing segments
    # ================================================================

    # 4a. Day 8 — Break into 3 legs
    if day8:
        # Check if we already have the broken-down routes
        existing_shirakawa = TransportRoute.query.filter(
            TransportRoute.route_from.ilike('%Takayama%Bus%'),
            TransportRoute.day_id == day8.id
        ).first()
        if not existing_shirakawa:
            # Delete or rename the old combined route
            old_route = TransportRoute.query.filter(
                TransportRoute.route_from.ilike('%Takayama%'),
                TransportRoute.day_id == day8.id
            ).first()
            if old_route:
                db.session.delete(old_route)

            db.session.add(TransportRoute(
                route_from='Takayama Bus Center',
                route_to='Shirakawa-go',
                transport_type='Nohi Bus',
                duration='~50 min',
                jr_pass_covered=False,
                cost_if_not_covered='~\u00a52,800/person (reserve in advance)',
                notes='Depart Takayama Bus Center (next to JR Takayama Station). Drops off at Shirakawa-go Bus Terminal.',
                day_id=day8.id,
                sort_order=1,
            ))
            db.session.add(TransportRoute(
                route_from='Shirakawa-go',
                route_to='Kanazawa Station',
                transport_type='Nohi Bus / Hokutetsu Bus',
                duration='~1h 15min',
                jr_pass_covered=False,
                cost_if_not_covered='~\u00a52,800/person (reserve in advance)',
                notes='Departs from same Shirakawa-go Bus Terminal. Reserve ahead \u2014 buses sell out. Last bus typically ~3-4 PM.',
                day_id=day8.id,
                sort_order=2,
            ))
            db.session.add(TransportRoute(
                route_from='Kanazawa Station',
                route_to='Kyoto Station',
                transport_type='JR Thunderbird Limited Express',
                train_name='Thunderbird',
                duration='~2h 15min',
                jr_pass_covered=True,
                cost_if_not_covered='~\u00a57,000 (covered by JR Pass)',
                notes='Direct limited express to Kyoto. JR Pass covered. Runs roughly every 30-60 min.',
                day_id=day8.id,
                sort_order=3,
            ))
            changed = True

    # 4b. Day 2 — Haneda → Shinjuku route
    day2 = find_day(2)
    if day2:
        existing_haneda = TransportRoute.query.filter(
            TransportRoute.route_from.ilike('%Haneda%'),
            TransportRoute.day_id == day2.id
        ).first()
        if not existing_haneda:
            db.session.add(TransportRoute(
                route_from='Haneda Airport',
                route_to='Higashi-Shinjuku',
                transport_type='Keikyu Line + subway OR Limousine Bus',
                duration='~60-90 min',
                jr_pass_covered=False,
                cost_if_not_covered='\u00a5600-1,300 depending on method',
                notes='JR Pass not yet activated. Options: Keikyu Line to Shinagawa + subway (~\u00a5800, 75 min) or Airport Limousine Bus direct to Shinjuku (~\u00a51,300, 60-85 min).',
                day_id=day2.id,
                sort_order=1,
            ))
            changed = True

    # 4c. Day 4 — Update existing Hakone route with better details
    hakone_route = TransportRoute.query.filter(
        TransportRoute.route_from.ilike('%Odawara%'),
        TransportRoute.route_to.ilike('%Hakone%')
    ).first()
    if hakone_route:
        if not hakone_route.duration:
            hakone_route.duration = '~15 min to Hakone-Yumoto, then ~40 min to Gora'
            changed = True
        if not hakone_route.cost_if_not_covered or hakone_route.cost_if_not_covered == 'Hakone Free Pass':
            hakone_route.cost_if_not_covered = 'Covered by Hakone Free Pass'
            hakone_route.route_to = 'Hakone (Loop Start: Hakone-Yumoto)'
            changed = True

    # ================================================================
    # 5. TRANSIT TIME ESTIMATES — Update getting_there with times
    # ================================================================
    transit_updates = [
        ('%Ueno Zoo%', None, '~20 min from Shinjuku'),
        ('%A PIT Autobacs%', None, '~40 min from Shinjuku via Rinkai Line'),
        ('%UpGarage%', None, '~50 min from Shinjuku via Odakyu Line to Machida Station, 5 min walk'),
        ('%Honda Welcome Plaza%', None, '~10 min from Shinjuku via Oedo Line'),
        ('%Route 246%Supercar%', None, '~10 min from Shinjuku'),
        ('%Roppongi Hills%', None, '~15 min from Shinjuku via Oedo Line'),
        ('%UDX%Akihabara%', None, '~20 min from Shinjuku via JR Chuo Line'),
        ('%Daikoku PA%', None, 'Tour is 4.5 hours. Pickup at 6:30 PM, return ~11 PM'),
        ('%Switchback Train%', 4, '~40 min Hakone-Yumoto to Gora'),
        ('%Cable Car%', 4, '~10 min Gora to Owakudani'),
        ('%Ropeway%', 4, '~25 min Owakudani to Togendai'),
        ('%Pirate Ship%', 4, '~30 min cruise across Lake Ashi'),
        ('%Open-Air Museum%', 4, '~5 min walk from Chokoku-no-Mori Station (between Hakone-Yumoto and Gora)'),
        ('%ake brewery%tasting%', None, 'Walk between them, ~2-5 min apart'),
        ('%Hida beef sushi%', None, '~5 min walk within old town'),
        ('%Kamo River%stroll%', None, '~2 min walk to the riverbank'),
        ('%Nishiki Market%', None, '~20 min train to central Kyoto'),
        ('%Dotonbori%Night%', None, '~10 min from Shinsaibashi area'),
        ('%Takoyaki crawl%', None, 'Walk between them, all within ~200m / 2 min'),
        ('%Hozenji Yokocho%', None, '~2 min walk from Dotonbori canal'),
        ('%Nara day trip%', None, 'JR Yamatoji Rapid to JR Nara Station (45-50 min). Deer park is 5 min walk from station east exit.'),
        ('%Amerikamura%', None, '~15 min from Nara Line. 5 min walk west from Shinsaibashi Station.'),
        ('%Shinsaibashi arcade%', None, '~2 min walk east from Amerikamura'),
    ]

    for pattern, day_num, time_info in transit_updates:
        act = find_activity(pattern, day_num)
        if act:
            current = act.getting_there or ''
            if time_info not in current:
                if current:
                    act.getting_there = current.rstrip('.') + '. ' + time_info
                else:
                    act.getting_there = time_info
                changed = True

    # ================================================================
    # 6. CHECKLIST FIXES
    # ================================================================

    # 6a. Delta outbound — add option and set to decision type
    delta_item = ChecklistItem.query.filter(
        ChecklistItem.title.ilike('%Delta%outbound%')
    ).first()
    if not delta_item:
        delta_item = ChecklistItem.query.filter(
            ChecklistItem.title.ilike('%Book Delta%CLE%')
        ).first()
    if delta_item:
        if delta_item.item_type == 'task':
            delta_item.item_type = 'decision'
            delta_item.status = 'completed'
            changed = True
        # Add option if none
        if not delta_item.options:
            db.session.add(ChecklistOption(
                checklist_item_id=delta_item.id,
                name='Delta Endeavor Air DL5392 + DL275',
                price_note='Cash booking',
                description='CLE 10:30 AM \u2192 DTW 11:26 AM (DL5392), DTW 2:05 PM \u2192 HND 4:15 PM+1 (DL275). Confirmation: HBPF75',
                is_selected=True,
                sort_order=0,
            ))
            changed = True

    # 6b. Hakone Free Pass, Shirakawa-go, Suica — set to decision type if task
    for pattern in ['%Hakone Free Pass%', '%Shirakawa%bus%', '%Welcome Suica%',
                    '%Suica%IC%card%']:
        item = ChecklistItem.query.filter(
            ChecklistItem.title.ilike(pattern)
        ).first()
        if item and item.item_type == 'task' and item.options:
            item.item_type = 'decision'
            changed = True

    # ================================================================
    # COMMIT & SENTINEL
    # ================================================================
    if trip:
        trip.notes = ((trip.notes or '') + '\n__cal_warnings_v2').strip()
    db.session.commit()
    if changed:
        print("  Migration complete: calendar warnings and data fixes v2 applied.")
    else:
        print("  Migration complete: calendar warnings v2 — already up to date.")


def create_app(run_data_migrations=True):
    app = Flask(__name__)
    app.config.from_object(Config)
    Config.validate_production()

    db.init_app(app)
    allowed = os.environ.get('CORS_ORIGINS', '*')
    socketio.init_app(app, cors_allowed_origins=allowed, async_mode='gevent')

    # Register blueprints
    from blueprints.itinerary import itinerary_bp
    from blueprints.accommodations import accommodations_bp
    from blueprints.checklists import checklists_bp
    from blueprints.uploads import uploads_bp
    from blueprints.chat import chat_bp
    from blueprints.reference import reference_bp
    from blueprints.documents import documents_bp
    from blueprints.activities import activities_bp
    from blueprints.backup import backup_bp
    from blueprints.export import export_bp
    from blueprints.bookahead import bookahead_bp
    from blueprints.calendar import calendar_bp

    app.register_blueprint(itinerary_bp)
    app.register_blueprint(accommodations_bp)
    app.register_blueprint(checklists_bp)
    app.register_blueprint(uploads_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(reference_bp)
    app.register_blueprint(documents_bp)
    app.register_blueprint(activities_bp)
    app.register_blueprint(backup_bp)
    app.register_blueprint(export_bp)
    app.register_blueprint(bookahead_bp)
    app.register_blueprint(calendar_bp)

    # Google Maps link filter
    @app.template_filter('maps_link')
    def maps_link_filter(address):
        from urllib.parse import quote
        return f"https://www.google.com/maps/search/?api=1&query={quote(address)}"

    # Google Translate link filter — opens a URL translated from Japanese to English
    @app.template_filter('translate_link')
    def translate_link_filter(url):
        from urllib.parse import quote
        return f"https://translate.google.com/translate?sl=ja&tl=en&u={quote(url, safe='')}"

    # Auto-link station/hub names in transit text to Google Maps
    @app.template_filter('linkify_stations')
    def linkify_stations_filter(text):
        import re
        from urllib.parse import quote
        from markupsafe import Markup, escape

        if not text:
            return text

        # Match proper station names: "Name Station", "Name Bus Center", etc.
        # 1-3 capitalized words before the suffix keyword
        station_pattern = re.compile(
            r'\b((?:[A-Z][\w\-]*(?:\s+[A-Z][\w\-]*){0,2})'
            r'\s+(?:Station|Sta\.|Terminal|Port|Bus Center|Bus Stop))\b',
        )

        escaped = str(escape(text))
        parts = []
        last_end = 0
        for m in station_pattern.finditer(escaped):
            name = m.group(1).strip()
            if len(name) < 6:
                continue
            maps_url = f"https://www.google.com/maps/search/?api=1&query={quote(name + ' Japan')}"
            parts.append(escaped[last_end:m.start()])
            parts.append(
                f'<a href="{maps_url}" target="_blank" rel="noopener" '
                f'class="station-link">{name}</a>'
            )
            last_end = m.end()
        if parts:
            parts.append(escaped[last_end:])
            return Markup(''.join(parts))
        return text

    # Auth routes
    @app.route('/login', methods=['GET', 'POST'])
    def login():
        error = None
        if request.method == 'POST':
            ip = request.remote_addr or '0.0.0.0'
            if _is_rate_limited(ip):
                error = 'Too many attempts. Please wait a few minutes.'
            elif request.form.get('password') == app.config['TRIP_PASSWORD']:
                session['authenticated'] = True
                session.permanent = True
                return redirect(url_for('itinerary.index'))
            else:
                _record_attempt(ip)
                error = 'Wrong password'
        return render_template('login.html', error=error)

    @app.route('/logout')
    def logout():
        session.pop('authenticated', None)
        return redirect(url_for('login'))

    # Auth disabled — sharing with friends pre-trip. Re-enable before travel.
    # @app.before_request
    # def check_auth():
    #     allowed_endpoints = ['login', 'static']
    #     if request.endpoint and any(request.endpoint.startswith(a) for a in allowed_endpoints):
    #         return
    #     if not session.get('authenticated'):
    #         return redirect(url_for('login'))

    # Ensure upload directories exist
    os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'originals'), exist_ok=True)
    os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'thumbnails'), exist_ok=True)
    os.makedirs(os.path.join(os.path.dirname(__file__), 'data'), exist_ok=True)

    # Copy bundled flight PDFs to uploads/documents if not already there
    docs_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'documents')
    os.makedirs(docs_dir, exist_ok=True)
    bundled_docs = os.path.join(os.path.dirname(__file__), 'Documentation', 'flights')
    if os.path.isdir(bundled_docs):
        import shutil
        existing = set(os.listdir(docs_dir))
        for fname in os.listdir(bundled_docs):
            # Check if any file ending with this name already exists
            if not any(f.endswith('__' + fname) or f == fname for f in existing):
                src = os.path.join(bundled_docs, fname)
                dst = os.path.join(docs_dir, fname)
                shutil.copy2(src, dst)

    with app.app_context():
        db.create_all()
        if run_data_migrations:
            _run_migrations(app)
            _seed_checklist_decisions(app)
            _fix_booking_urls(app)
            _seed_guide_urls(app)
            _seed_location_coords(app)
            _restructure_osaka(app)
            _seed_osaka_and_substitutes(app)
            _revise_itinerary_activities(app)
            _migrate_add_osaka_day(app)
            _migrate_remove_kanazawa(app)
            _fix_checklist_consistency(app)
            _migrate_14day_restructure(app)
            _migrate_consolidate_kyoto(app)
            _migrate_add_addresses_and_cleanup_transport(app)
            _migrate_data_cleanup(app)
            _migrate_enrich_activities(app)
            _migrate_sumo_bookahead_transit(app)
            _migrate_add_shinjuku_hotels(app)
            _migrate_add_booking_resources(app)
            _migrate_swap_tokyo_hotel_links(app)
            _migrate_add_neighborhood_descriptions(app)
            _migrate_add_maps_urls(app)
            _migrate_book_sotetsu_fresa(app)
            _migrate_update_itinerary_for_sotetsu(app)
            _migrate_book_takanoyu(app)
            _migrate_update_itinerary_for_takanoyu(app)
            _migrate_audit_data_fixes(app)
            _migrate_hiroshima_time_warning(app)
            _migrate_transport_checklist_and_data_fixes(app)
            _migrate_remove_kanazawa_hotel(app)
            _migrate_production_data_reapply(app)
            try:
                _migrate_add_transit_directions(app)
            except Exception as e:
                print(f"WARNING: transit directions migration failed: {e}")
                db.session.rollback()
            try:
                _migrate_restore_hakone_route(app)
            except Exception as e:
                print(f"WARNING: hakone route migration failed: {e}")
                db.session.rollback()
            try:
                _migrate_calendar_warnings_and_data_v2(app)
            except Exception as e:
                print(f"WARNING: calendar warnings v2 migration failed: {e}")
                db.session.rollback()
            try:
                _migrate_transport_hardening_v1(app)
            except Exception as e:
                print(f"WARNING: transport hardening v1 migration failed: {e}")
                db.session.rollback()
            try:
                _migrate_sync_accom_checklist_v1(app)
            except Exception as e:
                print(f"WARNING: sync accom checklist migration failed: {e}")
                db.session.rollback()

    return app


def _migrate_transport_hardening_v1(app):
    """Comprehensive transport hardening: add missing routes, fix route assignments,
    fix activity titles, add getting_there/URLs/addresses. See docs/transport-audit.md."""
    from models import Activity, Day, Trip, TransportRoute

    trip = Trip.query.first()
    if trip and trip.notes and '__transport_hardening_v1' in (trip.notes or ''):
        return

    print("Running migration: transport hardening v1...")
    changed = False

    # ---- HELPERS ----
    def find_day(day_number):
        return Day.query.filter_by(day_number=day_number).first()

    def find_activity(title_pattern, day_number=None):
        q = Activity.query.filter(Activity.title.ilike(title_pattern))
        if day_number:
            day = find_day(day_number)
            if day:
                q = q.filter_by(day_id=day.id)
        return q.first()

    def find_route(from_pattern, to_pattern, day_number=None):
        q = TransportRoute.query.filter(
            TransportRoute.route_from.ilike(from_pattern),
            TransportRoute.route_to.ilike(to_pattern))
        if day_number:
            day = find_day(day_number)
            if day:
                q = q.filter_by(day_id=day.id)
        return q.first()

    def add_route(day_number, route_from, route_to, transport_type, duration,
                  jr_pass, notes=None, sort_order=None):
        nonlocal changed
        day = find_day(day_number)
        if not day:
            return
        existing = find_route(f'%{route_from}%', f'%{route_to}%', day_number)
        if existing:
            return
        max_sort = db.session.query(db.func.max(TransportRoute.sort_order)).filter_by(
            day_id=day.id).scalar() or 0
        r = TransportRoute(
            route_from=route_from, route_to=route_to,
            transport_type=transport_type, duration=duration,
            jr_pass_covered=jr_pass, notes=notes,
            day_id=day.id, sort_order=sort_order or (max_sort + 1))
        db.session.add(r)
        changed = True
        print(f"  + Route: {route_from} → {route_to} (Day {day_number})")

    def set_activity_field(title_pattern, day_number, field, value, overwrite=False):
        nonlocal changed
        act = find_activity(title_pattern, day_number)
        if not act:
            return
        current = getattr(act, field, None)
        if current and not overwrite:
            return
        setattr(act, field, value)
        changed = True
        print(f"  ~ Activity '{act.title}' Day {day_number}: {field} set")

    def rename_activity(title_pattern, new_title, day_number=None):
        nonlocal changed
        act = find_activity(title_pattern, day_number)
        if not act:
            return
        if act.title == new_title:
            return
        old = act.title
        act.title = new_title
        changed = True
        print(f"  ~ Renamed: '{old}' → '{new_title}'")

    # ================================================================
    # PART 1: Add Missing Transport Routes (6 new routes)
    # ================================================================

    # Day 3 — Tokyo internal transit (sumo morning)
    add_route(3, 'Higashi-Shinjuku', 'Hamacho (Arashio Stable)',
              'Toei Oedo Line', '~20 min', False,
              'Leave by 6:15 AM for 6:45 AM sumo practice. Oedo Line from '
              'Higashi-Shinjuku Station direct to Hamacho. ¥220.')

    # Day 5 — First mile to station
    add_route(5, 'Higashi-Shinjuku', 'Tokyo Station',
              'Subway (Fukutoshin → Marunouchi)', '~20 min', False,
              'Transfer at Ikebukuro or take Oedo to Tochomae then walk to '
              'Shinjuku JR. Alternative: taxi ~¥2,000.')

    # Day 6 — Takayama internal (Hida Folk Village)
    add_route(6, 'Takayama Station', 'Hida Folk Village (Hida no Sato)',
              'Sarubobo Bus', '~10 min', False,
              'Runs every 20-30 min from platform #3 at Takayama Bus Center '
              '(next to JR station). ¥210. Or 30 min walk uphill.')

    # Day 12 — Hiroshima day trip (4 routes)
    add_route(12, 'Kyoto Station', 'Hiroshima Station',
              'Shinkansen Sakura/Hikari', '~1h 45min', True,
              'Board by 7:30 AM to maximize time. Nozomi is faster but NOT '
              'covered by JR Pass — take Sakura or Hikari.', sort_order=1)

    add_route(12, 'Hiroshima Station', 'Peace Park (Genbaku-Dome mae)',
              'Hiroshima streetcar (tram)', '~15 min', False,
              'Line 2 or 6 to Genbaku-Dome mae stop. ¥220 flat fare. IC card accepted.',
              sort_order=2)

    add_route(12, 'Hiroshima Station', 'Miyajimaguchi',
              'JR Sanyo Line', '~30 min', True,
              'Walk to JR Ferry terminal (2 min). Ferry ~10 min, JR Pass covered.',
              sort_order=3)

    add_route(12, 'Hiroshima Station', 'Kyoto Station',
              'Shinkansen Sakura/Hikari (return)', '~1h 45min', True,
              'DEADLINE: Last useful Shinkansen ~9 PM. Aim for 6:30-7 PM departure.',
              sort_order=4)

    # Day 14 — First mile to station
    add_route(14, 'Hotel Leben (Shinsaibashi)', 'Shin-Osaka Station',
              'Osaka Metro Midosuji Line', '~15 min', False,
              'Shinsaibashi → Shin-Osaka direct (7 stops). ¥280. Leave hotel by '
              '9:15 AM for recommended buffer.', sort_order=1)

    # ================================================================
    # PART 2: Fix Existing Route Assignments
    # ================================================================

    # Move Kanazawa→Kyoto route from Day 8 to Day 9
    route_kz_ky = find_route('%Kanazawa%', '%Kyoto%', 8)
    day9 = find_day(9)
    if route_kz_ky and day9:
        route_kz_ky.day_id = day9.id
        changed = True
        print("  ~ Moved Kanazawa→Kyoto route from Day 8 to Day 9")

    # Delete old generic Hiroshima routes from Day 11 (replaced by detailed Day 12 routes above)
    day11 = find_day(11)
    if day11:
        hiro_routes = TransportRoute.query.filter_by(day_id=day11.id).all()
        for r in hiro_routes:
            if 'hiroshima' in (r.route_from or '').lower() or \
               'hiroshima' in (r.route_to or '').lower() or \
               'miyajima' in (r.route_to or '').lower():
                db.session.delete(r)
                changed = True
                print(f"  - Deleted old route {r.route_from}→{r.route_to} from Day 11 (replaced by detailed Day 12 routes)")

    # Delete Osaka→Nara route from Day 13
    nara_route = find_route('%Osaka%', '%Nara%', 13)
    if nara_route:
        db.session.delete(nara_route)
        changed = True
        print("  - Deleted Osaka→Nara route from Day 13")

    # Fix Day 4 return JR flag and notes
    hakone_return = find_route('%Hakone%', '%Tokyo%', 4)
    if not hakone_return:
        hakone_return = find_route('%Hakone-Yumoto%', '%Tokyo%', 4)
    if hakone_return:
        hakone_return.notes = ('Hakone Tozan to Odawara (NOT JR, Hakone Free Pass), '
                               'then Shinkansen Odawara→Tokyo (JR Pass covered)')
        changed = True
        print("  ~ Updated Day 4 return route notes (JR clarification)")

    # ================================================================
    # PART 3: Fix Activity Titles (6 renames)
    # ================================================================

    rename_activity('%Nohi Bus%Shirakawa-go%Kyoto%',
                    'Nohi Bus: Shirakawa-go → Kanazawa')

    rename_activity('%Shinkansen Kyoto%Tokyo%',
                    'Shinkansen Shin-Osaka → Shinagawa', day_number=14)

    rename_activity('%Check out of Kyoto%',
                    'Early checkout from Hotel Leben Osaka', day_number=14)

    rename_activity('%Last Kyoto exploration%',
                    'Last Osaka morning — konbini breakfast & walk', day_number=14)

    rename_activity('%Kyoto Castle Park%',
                    'Kanazawa Castle Park', day_number=8)

    rename_activity('%Hokuriku Shinkansen%Kyoto%Tsuruga%',
                    'Hokuriku Shinkansen: Kanazawa → Tsuruga', day_number=9)

    # ================================================================
    # PART 4: Add Missing getting_there (14 activities)
    # ================================================================

    getting_there_updates = [
        ('%Explore Sanmachi Suji%', 6,
         'Walk from K\'s House — 10 min south along the river to the old town.'),
        ('%Takayama Jinya%', 6,
         '5 min walk south from Sanmachi Suji, across the Miyagawa River.'),
        ('%Miyagawa Morning Market%', 7,
         '5 min walk east from K\'s House along the river. Open 6 AM–noon.'),
        ('%Hida Folk Village%', 7,
         'Sarubobo Bus from Takayama Bus Center (~10 min, ¥210). Or 30 min walk uphill from station.'),
        ('%Kenrokuen%', 8,
         '15 min walk or bus from Kanazawa Station (east exit). Bus #6 to Kenrokuen-shita.'),
        ('%Higashi Chaya%', 8,
         '10 min walk east from Kenrokuen across Asanogawa bridge.'),
        ('%21st Century Museum%', 9,
         '15 min walk south from Kanazawa Station. Or bus from east exit, Hirosaka stop.'),
        ('%Hiroshima Peace%Park%', 12,
         'Streetcar from Hiroshima Station, Line 2 or 6 to Genbaku-Dome mae (15 min, ¥220).'),
        ('%A-Bomb Dome%', 12,
         'Across the river from Peace Park, 3 min walk.'),
        ('%JR train to Miyajimaguchi%', 12,
         'JR Sanyo Line from Hiroshima Station (~30 min). Walk to JR Ferry terminal (2 min).'),
        ('%Osaka Castle Park%', 13,
         'JR Loop Line from Osaka Station to Osakajo-Koen (2 stops, ~10 min). Walk through park to castle ~15 min.'),
        ('%Kuromon Market%', 13,
         'Subway from Tanimachi-Yonchome to Nipponbashi (~10 min). Or 20 min walk south from Osaka Castle.'),
        ('%Shinsekai%Tsutenkaku%', 13,
         'Walk south from Kuromon (~10 min) or subway to Ebisucho Station (1 stop).'),
        ('%TeamLab Planets%', 14,
         'Yurikamome Line from Shinbashi to Shin-Toyosu (~15 min). Or walk 10 min from Toyosu Station (Yurakucho Line).'),
    ]

    for pattern, day_num, gt_text in getting_there_updates:
        set_activity_field(pattern, day_num, 'getting_there', gt_text)

    # ================================================================
    # PART 5: Add Missing URLs, Addresses, Fix Broken URLs
    # ================================================================

    # --- URLs ---
    url_updates = [
        ('%Welcome Suica%', None, 'https://www.japan-guide.com/e/e2359_003.html'),
        ('%Nohi Bus%Shirakawa-go%Kanazawa%', None, 'https://www.nouhibus.co.jp/english/'),
        ('%Miyagawa Morning Market%', 7, 'https://www.japan-guide.com/e/e5907.html'),
        ('%Takayama Festival Floats%', 7, 'https://www.japan-guide.com/e/e5905.html'),
        ('%Wada House%', 8, 'https://www.japan-guide.com/e/e5951.html'),
        ('%Higashi Chaya%', 8, 'https://visitkanazawa.jp/en/attractions/detail_10212.html'),
        ('%D.T. Suzuki%', 9, 'https://www.japan-guide.com/e/e4211.html'),
        ('%Nagamachi Samurai%', 9, 'https://www.japan-guide.com/e/e4204.html'),
        ('%Gold leaf ice cream%Hakuichi%', 9, 'https://www.hakuichi.co.jp/'),
        ('%Itsukushima Torii%', 11, 'https://www.itsukushimajinja.jp/en/'),
        ('%A-Bomb Dome%', 12, 'https://hpmmuseum.jp/?lang=eng'),
        ('%Itsukushima Shrine%shopping%', 12, 'https://www.itsukushimajinja.jp/en/'),
        ('%Dotonbori Night Walk%', 13, 'https://www.japan-guide.com/e/e4001.html'),
        ('%Hozenji Yokocho%', 13, 'https://www.osaka-info.jp/en/spot/hozenji-yokocho/'),
    ]

    for pattern, day_num, url in url_updates:
        set_activity_field(pattern, day_num, 'url', url)

    # Fix broken URL on Nohi Bus Shirakawa→Kyoto (now renamed to →Kanazawa)
    nohi_act = find_activity('%Nohi Bus%Shirakawa-go%')
    if nohi_act and nohi_act.url and 'shirakawa-go.org' in nohi_act.url:
        nohi_act.url = 'https://www.nouhibus.co.jp/english/'
        changed = True
        print("  ~ Fixed broken URL on Nohi Bus activity")

    # --- Addresses ---
    address_updates = [
        ('%Tenzan Tohji-kyo%', 4, '208 Yumoto-chaya, Hakone-machi, Kanagawa'),
        ('%Cable Car%Owakudani%', 5, 'Gora, Hakone-machi, Ashigarashimo-gun, Kanagawa'),
        ('%Ropeway%', 5, 'Sounzan, Hakone-machi, Kanagawa'),
        ('%Lake Ashi Pirate Ship%', 5, 'Togendai Port, Hakone-machi, Kanagawa'),
        ('%Sake brewery%', 6, 'Sanmachi, Takayama-shi, Gifu'),
        ('%observation deck%', 8, 'Shiroyama Observation Deck, Ogimachi, Shirakawa-mura, Gifu'),
        ('%Higashi Chaya%', 8, 'Higashiyama, Kanazawa, Ishikawa'),
        ('%Kanazawa Castle Park%', 8, '1-1 Marunouchi, Kanazawa, Ishikawa'),
        ('%21st Century Museum%', 9, '1-2-1 Hirosaka, Kanazawa, Ishikawa'),
        ('%D.T. Suzuki%', 9, '3-4-20 Honda-machi, Kanazawa, Ishikawa'),
        ('%Nagamachi Samurai%', 9, 'Nagamachi, Kanazawa, Ishikawa'),
        ('%Gold leaf ice cream%Hakuichi%', 9, 'Higashiyama 1-15-4, Kanazawa, Ishikawa'),
        ('%Itsukushima Torii%', 11, 'Miyajima-cho, Hatsukaichi, Hiroshima'),
        ('%Itsukushima Shrine%', 12, '1-1 Miyajima-cho, Hatsukaichi, Hiroshima'),
        ('%Takoyaki crawl%', 13, 'Dotonbori, Chuo-ku, Osaka'),
        ('%TeamLab Planets%', 14, '6-1-16 Toyosu, Koto-ku, Tokyo'),
    ]

    for pattern, day_num, address in address_updates:
        set_activity_field(pattern, day_num, 'address', address)

    # --- Transport Route Notes (booking URLs) ---
    nohi_tk = find_route('%Takayama%', '%Shirakawa%')
    if nohi_tk and nohi_tk.notes and 'nouhibus' not in (nohi_tk.notes or ''):
        nohi_tk.notes = (nohi_tk.notes or '') + ' Book: https://www.nouhibus.co.jp/english/'
        changed = True

    nohi_sk = find_route('%Shirakawa%', '%Kanazawa%')
    if nohi_sk and nohi_sk.notes and 'nouhibus' not in (nohi_sk.notes or ''):
        nohi_sk.notes = (nohi_sk.notes or '') + ' Book: https://www.nouhibus.co.jp/english/'
        changed = True

    osaka_shinkansen = find_route('%Osaka%', '%Shinagawa%', 14)
    if osaka_shinkansen and 'Hikari' not in (osaka_shinkansen.notes or ''):
        osaka_shinkansen.notes = ('Use Hikari (not Nozomi). Reserve seat at JR ticket office. '
                                  + (osaka_shinkansen.notes or ''))
        changed = True

    # ================================================================
    # VALIDATION: Double-check transport consistency
    # ================================================================
    warnings = []

    # Check 1: Every day with non-optional activities should have routes or walkable note
    # Day 13 excluded: Osaka-internal (walking/subway), Kyoto→Osaka route is on Day 12
    days_needing_routes = {2, 3, 4, 5, 6, 8, 9, 12, 14}  # transit/multi-location days
    for dn in days_needing_routes:
        day = find_day(dn)
        if day:
            route_count = TransportRoute.query.filter_by(day_id=day.id).count()
            if route_count == 0:
                warnings.append(f"Day {dn} has no transport routes")

    # Check 2: No duplicate routes on same day (same from+to)
    all_days = Day.query.all()
    for day in all_days:
        routes = TransportRoute.query.filter_by(day_id=day.id).all()
        seen = set()
        for r in routes:
            key = (r.route_from.lower().strip(), r.route_to.lower().strip())
            if key in seen:
                warnings.append(f"Day {day.day_number}: duplicate route {r.route_from} → {r.route_to}")
            seen.add(key)

    # Check 3: Activity titles shouldn't reference wrong city for their day
    city_day_map = {
        'Tokyo': {2, 3, 4, 5}, 'Takayama': {6, 7}, 'Kanazawa': {8, 9},
        'Kyoto': {9, 10, 11, 12}, 'Osaka': {13, 14}, 'Hiroshima': {11, 12},
    }
    title_mismatches = [
        (8, '%Kyoto Castle%'),   # Day 8 is Kanazawa, not Kyoto
        (14, '%Kyoto%checkout%'),  # Day 14 is Osaka, not Kyoto
        (14, '%Kyoto%exploration%'),
    ]
    for dn, pattern in title_mismatches:
        bad = find_activity(pattern, dn)
        if bad:
            warnings.append(f"Day {dn}: activity '{bad.title}' references wrong city")

    # Check 4: Renamed activities should exist
    expected_titles = [
        (None, '%Nohi Bus%Kanazawa%'),
        (14, '%Shin-Osaka%Shinagawa%'),
        (8, '%Kanazawa Castle Park%'),
        (9, '%Kanazawa%Tsuruga%'),
    ]
    for dn, pattern in expected_titles:
        if not find_activity(pattern, dn):
            warnings.append(f"Expected activity '{pattern}' on Day {dn or 'any'} not found after rename")

    # Check 5: Key routes should be on correct days
    route_checks = [
        (9, '%Kanazawa%', '%Kyoto%', 'Kanazawa→Kyoto should be on Day 9'),
        (12, '%Kyoto%Station%', '%Hiroshima%Station%', 'Kyoto→Hiroshima should be on Day 12'),
        (12, '%Hiroshima%Station%', '%Kyoto%Station%', 'Hiroshima→Kyoto return should be on Day 12'),
        (3, '%Shinjuku%', '%Hamacho%', 'Sumo transit should be on Day 3'),
        (5, '%Shinjuku%', '%Tokyo Station%', 'First mile should be on Day 5'),
        (14, '%Leben%', '%Shin-Osaka%', 'Hotel→station should be on Day 14'),
    ]
    for dn, from_pat, to_pat, msg in route_checks:
        if not find_route(from_pat, to_pat, dn):
            warnings.append(f"MISSING: {msg}")

    if warnings:
        print(f"  ⚠ TRANSPORT VALIDATION WARNINGS ({len(warnings)}):")
        for w in warnings:
            print(f"    - {w}")
    else:
        print("  ✓ All transport validation checks passed")

    # ================================================================
    # Mark sentinel
    # ================================================================
    if trip:
        trip.notes = (trip.notes or '') + '\n__transport_hardening_v1'
        db.session.commit()
        print(f"Migration transport_hardening_v1 complete (changed={changed})")


def _migrate_sync_accom_checklist_v1(app):
    """Ensure every AccommodationLocation has a linked ChecklistItem,
    and sync booking statuses bidirectionally so documents page shows all reservations."""
    from models import (AccommodationLocation, AccommodationOption,
                        ChecklistItem, Trip, db)
    from datetime import datetime

    trip = Trip.query.first()
    if not trip:
        return
    if '__sync_accom_checklist_v1' in (trip.notes or ''):
        print("sync_accom_checklist_v1: already applied, skipping")
        return

    print("Running sync_accom_checklist_v1 migration...")
    changed = False

    # ================================================================
    # Part A: Create missing ChecklistItems for unlinked AccommodationLocations
    # ================================================================
    max_sort = db.session.query(db.func.max(ChecklistItem.sort_order)).scalar() or 0

    for loc in AccommodationLocation.query.order_by(
            AccommodationLocation.sort_order).all():
        existing = ChecklistItem.query.filter_by(
            accommodation_location_id=loc.id).first()
        if existing:
            continue

        # Build title like "Book Tokyo (3 nights, Apr 6-9)"
        date_range = ''
        if loc.check_in_date and loc.check_out_date:
            cin = loc.check_in_date.strftime('%b %-d')
            cout = loc.check_out_date.strftime('%-d')
            date_range = f', {cin}-{cout}'
        nights = loc.num_nights or 0
        night_str = f"{nights} night{'s' if nights != 1 else ''}"
        title = f"Book {loc.location_name} ({night_str}{date_range})"

        max_sort += 1
        item = ChecklistItem(
            title=title,
            item_type='decision',
            category='pre_departure_today',
            status='pending',
            accommodation_location_id=loc.id,
            sort_order=max_sort,
        )
        db.session.add(item)
        changed = True
        print(f"  + Created checklist item: {title}")

    if changed:
        db.session.flush()

    # ================================================================
    # Part B: Sync booking statuses bidirectionally
    # ================================================================

    # Pass 1: Checklist 'booked'/'completed' → AccommodationOption.booking_status
    for item in ChecklistItem.query.filter(
            ChecklistItem.accommodation_location_id.isnot(None),
            ChecklistItem.status.in_(['booked', 'completed'])).all():
        opt = AccommodationOption.query.filter_by(
            location_id=item.accommodation_location_id,
            is_selected=True).first()
        if opt and opt.booking_status not in ('booked', 'confirmed'):
            opt.booking_status = 'booked'
            changed = True
            print(f"  ~ Synced option '{opt.name}' booking_status → booked "
                  f"(from checklist '{item.title}')")

    # Pass 2: AccommodationOption booked/confirmed → ChecklistItem status
    for opt in AccommodationOption.query.filter(
            AccommodationOption.is_selected == True,
            AccommodationOption.booking_status.in_(['booked', 'confirmed'])).all():
        item = ChecklistItem.query.filter_by(
            accommodation_location_id=opt.location_id).first()
        if item and item.status not in ('booked', 'completed'):
            item.status = 'booked'
            item.is_completed = True
            item.completed_at = datetime.utcnow()
            changed = True
            print(f"  ~ Synced checklist '{item.title}' → booked "
                  f"(from option '{opt.name}')")

    # ================================================================
    # Mark sentinel
    # ================================================================
    trip.notes = (trip.notes or '') + '\n__sync_accom_checklist_v1'
    db.session.commit()
    print(f"Migration sync_accom_checklist_v1 complete (changed={changed})")


if __name__ == '__main__':
    app = create_app()
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
