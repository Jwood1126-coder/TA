"""Production startup script for Railway deployment."""
import os
import shutil

basedir = os.path.dirname(__file__)
volume = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH')

if volume:
    # Ensure persistent directories exist on Railway volume
    os.makedirs(os.path.join(volume, 'data'), exist_ok=True)
    os.makedirs(os.path.join(volume, 'uploads', 'originals'), exist_ok=True)
    os.makedirs(os.path.join(volume, 'uploads', 'thumbnails'), exist_ok=True)

    # Copy initial database to volume on first deploy
    db_dest = os.path.join(volume, 'data', 'japan_trip.db')
    db_src = os.path.join(basedir, 'data', 'japan_trip.db')
    if not os.path.exists(db_dest) and os.path.exists(db_src):
        print("First deploy: copying initial database to volume...")
        shutil.copy2(db_src, db_dest)

    # One-time migration: replace volume DB with repo DB if marker absent
    migration_marker = os.path.join(volume, 'data', '.migrated_v33')
    if os.path.exists(db_dest) and not os.path.exists(migration_marker):
        print("Migration v32: replacing database with updated version...")
        shutil.copy2(db_src, db_dest)
        with open(migration_marker, 'w') as f:
            f.write('done')

from app import create_app, socketio

app = create_app()
port = int(os.environ.get('PORT', 5000))
debug = os.environ.get('FLASK_DEBUG', '0') == '1'

socketio.run(app, host='0.0.0.0', port=port, debug=debug)
