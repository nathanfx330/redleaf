# --- File: ./project/__init__.py (Corrected) ---
import os
import sqlite3
import json
import re
from pathlib import Path  # --- ADDED: Missing import ---
from markupsafe import escape

from flask import Flask, g, session, request, redirect, url_for, flash
from flask_wtf import CSRFProtect

from .config import INSTANCE_DIR, SECRET_KEY, DATABASE_FILE, BASE_DIR
from .database import get_db, close_connection
from .utils import register_template_filters
from .background import start_manager_thread
import storage_setup

csrf = CSRFProtect()

def create_app(test_config=None, start_background_thread=True):
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

    # Load user-specific configurations from instance folder
    config_path = INSTANCE_DIR / "config.json"
    about_md_path = INSTANCE_DIR / "ABOUT.md"
    project_name = "Redleaf"
    about_html = """
    <p><strong>Welcome to Redleaf.</strong></p>
    <p>This is the default 'About' text. To customize this message for your own fork or distribution, create a file named <code>ABOUT.md</code> inside your <code>instance/</code> directory and write your own content there using Markdown syntax.</p>
    """

    if config_path.exists():
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                user_config = json.load(f)
                project_name = user_config.get("PROJECT_NAME", "Redleaf").strip()
        except (json.JSONDecodeError, IOError) as e:
            print(f"[WARNING] Could not read or parse instance/config.json: {e}")

    if about_md_path.exists():
        try:
            about_md_content = about_md_path.read_text(encoding='utf-8')
            escaped_content = escape(about_md_content)
            paragraphs = re.split(r'\n\s*\n', escaped_content.strip())
            processed_paragraphs = [p.replace('\n', '<br>') for p in paragraphs]
            about_html = "".join(f"<p>{p}</p>" for p in processed_paragraphs)
        except IOError as e:
            print(f"[WARNING] Could not read instance/ABOUT.md: {e}")

    app.config['PROJECT_NAME'] = project_name
    app.config['ABOUT_CONTENT_HTML'] = about_html
    app.config['PRECOMPUTED_MARKER'] = INSTANCE_DIR / "precomputed.marker"

    csrf.init_app(app)
    app.teardown_appcontext(close_connection)
    register_template_filters(app)

    # --- BLUEPRINT REGISTRATION ---
    from .blueprints.auth import auth_bp
    from .blueprints.main import main_bp
    from .blueprints.settings import settings_bp
    from .blueprints.synthesis import synthesis_bp
    from .blueprints.api import api_bp
    from .blueprints.synthesis_api import synthesis_api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(synthesis_bp)
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(synthesis_api_bp)

    @app.before_request
    def check_setup_and_load_user():
        g.user = None
        g.is_precomputed = app.config['PRECOMPUTED_MARKER'].exists()
        g.project_name = app.config.get('PROJECT_NAME', 'Redleaf')
        g.about_content_html = app.config.get('ABOUT_CONTENT_HTML', '')

        is_public_endpoint = request.endpoint in [
            'static', 'auth.setup', 'auth.login', 'auth.register', 'auth.welcome'
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
                if g.is_precomputed: return redirect(url_for('auth.welcome'))
                try:
                    if get_db().execute('SELECT COUNT(id) FROM users').fetchone()[0] > 0:
                        return redirect(url_for('auth.login'))
                except sqlite3.OperationalError: pass
            elif request.endpoint == 'auth.welcome':
                if not g.is_precomputed: return redirect(url_for('auth.setup'))
                try:
                    if get_db().execute('SELECT COUNT(id) FROM users').fetchone()[0] > 0:
                        return redirect(url_for('auth.login'))
                except sqlite3.OperationalError: pass
            return

        db_path = Path(app.config['DATABASE_FILE'])
        if not db_path.exists():
            if g.is_precomputed:
                return "Building precomputed database, please wait a moment and refresh...", 503
            else:
                flash("Database not found. Please complete the initial setup.", "info")
                return redirect(url_for('auth.setup'))

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
            if g.is_precomputed:
                 return "Database is being built, please wait...", 503
            else:
                if db_path.exists(): db_path.unlink()
                storage_setup.create_unified_index(db_path)
                flash("Database was corrupted and has been reset. Please create a new admin account.", "warning")
                return redirect(url_for('auth.setup'))

        if g.user is None:
            flash("You must be logged in to access this page.", "warning")
            return redirect(url_for('auth.login'))

    if start_background_thread and not hasattr(app, 'manager_thread_started'):
        start_manager_thread(app)
        app.manager_thread_started = True

    return app