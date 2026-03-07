import os
from dotenv import load_dotenv

load_dotenv()

basedir = os.path.abspath(os.path.dirname(__file__))

# Railway volume mount (persistent storage across deploys)
# Falls back to local directories for development
volume = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', basedir)


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-change-me')
    SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(volume, 'data', 'japan_trip.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = os.path.join(volume, 'uploads')
    MAX_CONTENT_LENGTH = 20 * 1024 * 1024  # 20MB max upload
    THUMBNAIL_SIZE = (400, 400)
    TRIP_PASSWORD = os.environ.get('TRIP_PASSWORD', 'changeme')
    ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')

    # Session security
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = os.environ.get('RAILWAY_ENVIRONMENT') is not None
    PERMANENT_SESSION_LIFETIME = 86400  # 24 hours
