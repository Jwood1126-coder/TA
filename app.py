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
    ]
    for table, column, col_type in migrations:
        try:
            cursor.execute(f'ALTER TABLE {table} ADD COLUMN {column} {col_type}')
        except sqlite3.OperationalError:
            pass  # Column already exists
    conn.commit()
    conn.close()


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


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    allowed = os.environ.get('CORS_ORIGINS', '*')
    socketio.init_app(app, cors_allowed_origins=allowed, async_mode='eventlet')

    # Register blueprints
    from blueprints.itinerary import itinerary_bp
    from blueprints.accommodations import accommodations_bp
    from blueprints.checklists import checklists_bp
    from blueprints.uploads import uploads_bp
    from blueprints.chat import chat_bp
    from blueprints.reference import reference_bp
    from blueprints.documents import documents_bp
    from blueprints.activities import activities_bp

    app.register_blueprint(itinerary_bp)
    app.register_blueprint(accommodations_bp)
    app.register_blueprint(checklists_bp)
    app.register_blueprint(uploads_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(reference_bp)
    app.register_blueprint(documents_bp)
    app.register_blueprint(activities_bp)

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

    with app.app_context():
        db.create_all()
        _run_migrations(app)
        _seed_checklist_decisions(app)
        _fix_booking_urls(app)
        _seed_guide_urls(app)
        _seed_location_coords(app)
        _restructure_osaka(app)
        _seed_osaka_and_substitutes(app)
        _revise_itinerary_activities(app)
        _fix_checklist_consistency(app)

    return app


if __name__ == '__main__':
    app = create_app()
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
