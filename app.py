import os
import shutil
import time
import traceback
from collections import defaultdict
from flask import Flask, redirect, url_for, session, request, render_template
from config import Config
from models import db
from extensions import socketio

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


def create_app(run_data_migrations=True):
    app = Flask(__name__)
    app.config.from_object(Config)
    Config.validate_production()

    db.init_app(app)
    allowed = os.environ.get('CORS_ORIGINS', '*')
    socketio.init_app(app, cors_allowed_origins=allowed, async_mode='gevent')

    # --- Register blueprints ---
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
    from blueprints.gmail_sync import gmail_sync_bp

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
    app.register_blueprint(gmail_sync_bp)

    # --- Template filters ---
    @app.template_filter('maps_link')
    def maps_link_filter(address):
        from urllib.parse import quote
        return f"https://www.google.com/maps/search/?api=1&query={quote(address)}"

    @app.template_filter('from_json')
    def from_json_filter(s):
        import json
        try:
            return json.loads(s) if s else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    @app.template_filter('translate_link')
    def translate_link_filter(url):
        from urllib.parse import quote
        return f"https://translate.google.com/translate?sl=ja&tl=en&u={quote(url, safe='')}"

    @app.template_filter('linkify_stations')
    def linkify_stations_filter(text):
        import re
        from urllib.parse import quote
        from markupsafe import Markup, escape

        if not text:
            return text

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

    # --- Auth routes ---
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

    # --- Ensure upload directories exist ---
    os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'originals'), exist_ok=True)
    os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'thumbnails'), exist_ok=True)
    os.makedirs(os.path.join(os.path.dirname(__file__), 'data'), exist_ok=True)

    # Copy bundled flight PDFs to uploads/documents if not already there
    docs_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'documents')
    os.makedirs(docs_dir, exist_ok=True)
    bundled_docs = os.path.join(os.path.dirname(__file__), 'Documentation', 'flights')
    if os.path.isdir(bundled_docs):
        import uuid
        from werkzeug.utils import secure_filename as _sf
        existing = set(os.listdir(docs_dir))
        for fname in os.listdir(bundled_docs):
            safe = _sf(fname)
            if not any(f.endswith('__' + fname) or f.endswith('__' + safe)
                       or f == fname for f in existing):
                src = os.path.join(bundled_docs, fname)
                unique = f"{uuid.uuid4().hex[:8]}__{safe}"
                dst = os.path.join(docs_dir, unique)
                shutil.copy2(src, dst)

    # --- Boot-time migrations and validation ---
    with app.app_context():
        db.create_all()
        if run_data_migrations:
            from migrations.schema import run_schema_migrations
            run_schema_migrations(app)

            from migrations.legacy import verify_legacy_migrations
            verify_legacy_migrations(app)

            from migrations.validate import validate_schedule
            try:
                validate_schedule(app)
            except Exception as e:
                print(f"ERROR: schedule validation failed: {e}")
                traceback.print_exc()

            # Phase 6: seed Document records for files on disk & exact-match linking
            try:
                from blueprints.documents import seed_document_records, auto_link_documents_exact
                seeded = seed_document_records()
                if seeded:
                    print(f"  Documents: created {seeded} record(s) for files on disk")
                linked = auto_link_documents_exact()
                if linked:
                    print(f"  Documents: auto-linked {linked} document(s) by confirmation/flight number")
            except Exception as e:
                print(f"ERROR: document seeding failed: {e}")
                traceback.print_exc()

    # --- Start Gmail auto-sync (every 6 hours, background thread) ---
    if run_data_migrations:
        try:
            from blueprints.gmail_sync import start_auto_sync
            start_auto_sync(app, interval_hours=6)
        except Exception:
            pass  # Gmail sync is optional — don't block startup

    return app


if __name__ == '__main__':
    app = create_app()
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
