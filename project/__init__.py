# --- File: ./project/__init__.py (Definitive Fix) ---
import os
import sqlite3
import threading
import multiprocessing
from pathlib import Path

from flask import Flask, g, session, request, redirect, url_for, flash
from flask_wtf import CSRFProtect

from .config import INSTANCE_DIR, SECRET_KEY, DATABASE_FILE, BASE_DIR
from .database import get_db, close_connection
from .utils import register_template_filters
from .background import start_manager_thread
import storage_setup

csrf = CSRFProtect()

# --- START OF FIX: Add `start_background_thread` parameter ---
def create_app(test_config=None, start_background_thread=True):
# --- END OF FIX ---
    """Application factory function."""
    app = Flask(
        __name__, 
        instance_path=str(INSTANCE_DIR),
        static_folder=os.path.join(BASE_DIR, 'static'),
        template_folder=os.path.join(BASE_DIR, 'templates')
    )

    app.config.from_mapping(
        SECRET_KEY=SECRET_KEY,
        DATABASE_FILE=DATABASE_FILE,
    )
    if test_config is not None:
        app.config.update(test_config)
    app.config.from_object('project.config')

    app.config['PRECOMPUTED_MARKER'] = INSTANCE_DIR / "precomputed.marker"
    
    csrf.init_app(app)
    app.teardown_appcontext(close_connection)
    register_template_filters(app)

    # --- Register Blueprints ---
    from .blueprints.auth import auth_bp
    from .blueprints.main import main_bp
    from .blueprints.settings import settings_bp
    from .blueprints.api import api_bp
    from .blueprints.synthesis import synthesis_bp
    from .blueprints.synthesis_api import synthesis_api_bp
    
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(synthesis_bp)
    app.register_blueprint(synthesis_api_bp)

    @app.before_request
    def check_setup_and_load_user():
        g.user = None
        g.is_precomputed = app.config['PRECOMPUTED_MARKER'].exists()

        is_public_endpoint = request.endpoint in [
            'static', 'auth.setup', 'auth.login', 'auth.register',
            'auth.welcome'
        ]

        if 'user_id' in session:
            try:
                db = get_db()
                g.user = db.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
                if not g.user:
                    session.clear()
            except sqlite3.OperationalError:
                session.clear()
        
        if is_public_endpoint:
            if request.endpoint == 'auth.setup':
                if g.is_precomputed:
                    return redirect(url_for('auth.welcome'))
                try:
                    db = get_db()
                    if db.execute('SELECT COUNT(id) FROM users').fetchone()[0] > 0:
                        return redirect(url_for('auth.login'))
                except sqlite3.OperationalError:
                    pass
            elif request.endpoint == 'auth.welcome':
                if not g.is_precomputed:
                     return redirect(url_for('auth.setup'))
                try:
                    db = get_db()
                    if db.execute('SELECT COUNT(id) FROM users').fetchone()[0] > 0:
                        return redirect(url_for('auth.login'))
                except sqlite3.OperationalError:
                    pass
            return

        db_path = Path(app.config['DATABASE_FILE'])
        if not db_path.exists():
            if not g.is_precomputed:
                flash("Database not found. Please complete the initial setup.", "info")
                return redirect(url_for('auth.setup'))
            else:
                return "Building precomputed database, please wait a moment and refresh...", 503

        try:
            db = get_db()
            user_count = db.execute('SELECT COUNT(id) FROM users').fetchone()[0]
            if user_count == 0:
                if g.is_precomputed:
                    flash("Welcome! Please create your personal account to begin exploring.", "info")
                    return redirect(url_for('auth.welcome'))
                else:
                    flash("No users found in the database. Please complete the initial setup.", "info")
                    return redirect(url_for('auth.setup'))
        except sqlite3.OperationalError:
            close_connection(None)
            if not g.is_precomputed:
                if db_path.exists():
                    db_path.unlink()
                storage_setup.create_unified_index(db_path)
                flash("Database was corrupted and has been reset. Please create a new admin account.", "warning")
                return redirect(url_for('auth.setup'))
            else:
                 return "Database is being built, please wait...", 503
        
        if g.user is None:
            flash("You must be logged in to access this page.", "warning")
            return redirect(url_for('auth.login'))

    # --- START OF FIX: Conditionally start the background thread ---
    if start_background_thread and not hasattr(app, 'manager_thread_started'):
        start_manager_thread(app)
        app.manager_thread_started = True
    # --- END OF FIX ---

    return app