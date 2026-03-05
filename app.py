import os
from flask import Flask, redirect, url_for, session, request, render_template
from flask_socketio import SocketIO
from config import Config
from models import db

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
        ('checklist_item', 'item_type', "TEXT DEFAULT 'task'"),
        ('checklist_item', 'status', "TEXT DEFAULT 'pending'"),
        ('checklist_item', 'accommodation_location_id', 'INTEGER'),
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

    app.register_blueprint(itinerary_bp)
    app.register_blueprint(accommodations_bp)
    app.register_blueprint(checklists_bp)
    app.register_blueprint(uploads_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(reference_bp)

    # Google Maps link filter
    @app.template_filter('maps_link')
    def maps_link_filter(address):
        from urllib.parse import quote
        return f"https://www.google.com/maps/search/?api=1&query={quote(address)}"

    # Auth routes
    @app.route('/login', methods=['GET', 'POST'])
    def login():
        error = None
        if request.method == 'POST':
            if request.form.get('password') == app.config['TRIP_PASSWORD']:
                session['authenticated'] = True
                return redirect(url_for('itinerary.index'))
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

    return app


if __name__ == '__main__':
    app = create_app()
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
