# --- File: ./project/__init__.py ---
import os
import sqlite3
import threading
import multiprocessing
from pathlib import Path

from flask import Flask, g, session, request, redirect, url_for, flash
from flask_wtf import CSRFProtect

# --- START OF THE FIX ---
# We import BASE_DIR to build absolute paths, which is the most robust method.
from .config import INSTANCE_DIR, SECRET_KEY, DATABASE_FILE, BASE_DIR
# --- END OF THE FIX ---

from .database import get_db, close_connection
from .utils import register_template_filters
from .background import start_manager_thread
import storage_setup

csrf = CSRFProtect()

def create_app(test_config=None):
    """Application factory function."""
    app = Flask(
        __name__, 
        instance_path=INSTANCE_DIR,
        # --- START OF THE FIX ---
        # Using os.path.join with the absolute BASE_DIR guarantees Flask will find these folders.
        # This replaces the unreliable relative paths like '../static'.
        static_folder=os.path.join(BASE_DIR, 'static'),
        template_folder=os.path.join(BASE_DIR, 'templates')
        # --- END OF THE FIX ---
    )

    app.config.from_mapping(
        SECRET_KEY=SECRET_KEY,
        DATABASE_FILE=DATABASE_FILE,
    )
    if test_config is not None:
        app.config.update(test_config)
    app.config.from_object('project.config')

    csrf.init_app(app)
    app.teardown_appcontext(close_connection)
    register_template_filters(app)

    # --- Register Blueprints ---
    # Imports are done here, inside the factory
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

        is_public_endpoint = request.endpoint in [
            'static', 'auth.setup', 'auth.login', 'auth.register'
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
                try:
                    db = get_db()
                    if db.execute('SELECT COUNT(id) FROM users').fetchone()[0] > 0:
                        return redirect(url_for('auth.login'))
                except sqlite3.OperationalError:
                    pass
            return

        db_path = Path(app.config['DATABASE_FILE'])
        if not db_path.exists():
            storage_setup.create_unified_index(db_path)
            flash("Database not found. Please complete the initial setup.", "info")
            return redirect(url_for('auth.setup'))

        try:
            db = get_db()
            if db.execute('SELECT COUNT(id) FROM users').fetchone()[0] == 0:
                flash("No users found in the database. Please complete the initial setup.", "info")
                return redirect(url_for('auth.setup'))
        except sqlite3.OperationalError:
            close_connection(None)
            if db_path.exists():
                db_path.unlink()
            storage_setup.create_unified_index(db_path)
            flash("Database was corrupted and has been reset. Please create a new admin account.", "warning")
            return redirect(url_for('auth.setup'))
        
        if g.user is None:
            flash("You must be logged in to access this page.", "warning")
            return redirect(url_for('auth.login'))


    if not hasattr(app, 'manager_thread_started'):
        start_manager_thread(app)
        app.manager_thread_started = True

    return app