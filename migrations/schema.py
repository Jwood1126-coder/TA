"""Schema migrations — adds missing columns/tables to the database.

Idempotent and safe to run on every boot. Uses raw SQLite DDL
so it works even if models have changed.
"""

import os
import sqlite3


def run_schema_migrations(app):
    """Add new columns/tables if they don't exist."""
    db_path = app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
    if not os.path.exists(db_path):
        return
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # --- Create Document table (Phase 6: document-first architecture) ---
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS document (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            original_name TEXT,
            file_type TEXT,
            file_size INTEGER,
            doc_type TEXT NOT NULL,
            extracted_data TEXT,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes TEXT
        )
    """)

    # --- Column additions (idempotent via try/except) ---
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
        ('accommodation_option', 'phone', 'TEXT'),
        # Phase 6: document-first FK columns
        ('accommodation_option', 'document_id', 'INTEGER REFERENCES document(id)'),
        ('flight', 'document_id', 'INTEGER REFERENCES document(id)'),
        # Transport route enrichment
        ('transport_route', 'maps_url', 'TEXT'),
        ('transport_route', 'url', 'TEXT'),
    ]
    for table, column, col_type in migrations:
        try:
            cursor.execute(f'ALTER TABLE {table} ADD COLUMN {column} {col_type}')
        except sqlite3.OperationalError:
            pass  # Column already exists
    conn.commit()
    conn.close()
