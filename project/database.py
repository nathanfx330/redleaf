# --- File: ./project/database.py (FIXED for Timestamp Handling) ---
import sqlite3
from flask import g, current_app

def get_db():
    """
    Connects to the application's configured database. The connection
    is unique for each request and will be reused if this is called
    again during the same request.
    """
    if 'db' not in g:
        # --- START OF FIX ---
        # We are removing the automatic type detection for declared types.
        # This will cause timestamp columns to be returned as strings,
        # preventing the default parser from crashing on DuckDB's format.
        # The application's Jinja filters will handle the string parsing.
        g.db = sqlite3.connect(
            current_app.config['DATABASE_FILE'],
            timeout=15  # Increased timeout for potentially long-running write operations
        )
        # --- END OF FIX ---
        
        # Use sqlite3.Row to allow accessing columns by name
        g.db.row_factory = sqlite3.Row
        # Enable foreign key constraint enforcement
        g.db.execute("PRAGMA foreign_keys = ON;")
        # Enable Write-Ahead Logging for better concurrency
        g.db.execute("PRAGMA journal_mode = WAL;")
    return g.db

def close_connection(exception=None):
    """
    Closes the database connection at the end of the request.
    This function is registered with the app teardown context.
    """
    db = g.pop('db', None)
    if db is not None:
        db.close()