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

    return app


if __name__ == '__main__':
    app = create_app()
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
