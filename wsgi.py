"""WSGI entry point for Gunicorn."""
from app import create_app
from extensions import socketio  # noqa: F401 — socketio must be imported for gevent patch

app = create_app()
