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

    @app.before_request
    def check_auth():
        allowed_endpoints = ['login', 'static']
        if request.endpoint and any(request.endpoint.startswith(a) for a in allowed_endpoints):
            return
        if not session.get('authenticated'):
            return redirect(url_for('login'))

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

    return app


if __name__ == '__main__':
    app = create_app()
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
