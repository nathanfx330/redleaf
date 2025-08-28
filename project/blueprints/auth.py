# --- File: ./project/blueprints/auth.py ---
import sqlite3
from pathlib import Path
from functools import wraps

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, g, session, abort, current_app
)
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf import FlaskForm

from ..database import get_db, close_connection
import storage_setup
import secrets

# We no longer need template_folder here. The app knows where to look.
auth_bp = Blueprint('auth', __name__)

class SecureForm(FlaskForm):
    pass

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if g.user is None:
            flash("Please log in to access this page.", "info")
            return redirect(url_for('auth.login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if g.user is None:
            flash("Please log in to access this page.", "info")
            return redirect(url_for('auth.login', next=request.url))
        if g.user['role'] != 'admin':
            abort(403, "You must be an administrator to access this page.")
        return f(*args, **kwargs)
    return decorated_function

@auth_bp.route('/setup', methods=['GET', 'POST'])
def setup():
    db = get_db()
    try:
        if db.execute('SELECT COUNT(id) FROM users').fetchone()[0] > 0:
            return redirect(url_for('auth.login'))
    except sqlite3.OperationalError:
        close_connection(None)
        db_path = Path(current_app.config['DATABASE_FILE'])
        if db_path.exists():
            db_path.unlink()
        storage_setup.create_unified_index(db_path)
        return redirect(url_for('auth.setup'))
    form = SecureForm()
    if form.validate_on_submit():
        username = request.form.get('username', '').strip()
        password = request.form.get('password')
        if not username or not password:
            flash("Username and password are required.", "danger")
        else:
            hashed_password = generate_password_hash(password)
            db.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')", (username, hashed_password))
            db.commit()
            flash("Admin account created successfully! Please log in.", "success")
            return redirect(url_for('auth.login'))
    return render_template('setup.html', form=form)

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if g.user:
        return redirect(url_for('main.dashboard'))
    form = SecureForm()
    if form.validate_on_submit():
        username = request.form.get('username')
        password = request.form.get('password')
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        if user and check_password_hash(user['password_hash'], password):
            session.clear()
            session['user_id'] = user['id']
            next_page = request.args.get('next') or url_for('main.dashboard')
            return redirect(next_page)
        flash('Incorrect username or password.', 'danger')
    return render_template('login.html', form=form)

@auth_bp.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for('auth.login'))

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if g.user:
        return redirect(url_for('main.dashboard'))
    form = SecureForm()
    if form.validate_on_submit():
        token_value = request.form.get('token', '').strip()
        username = request.form.get('username', '').strip()
        password = request.form.get('password')
        db = get_db()
        token_data = db.execute("SELECT * FROM invitation_tokens WHERE token_value = ? AND claimed_by_user_id IS NULL", (token_value,)).fetchone()
        if not token_data:
            flash("Invalid or already used invitation code.", "danger")
        elif not username or not password:
            flash("Username and password are required.", "danger")
        elif db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
            flash(f"Username '{username}' is already taken.", "danger")
        else:
            try:
                db.execute("BEGIN")
                hashed_password = generate_password_hash(password)
                cursor = db.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'user')", (username, hashed_password))
                db.execute("UPDATE invitation_tokens SET claimed_by_user_id = ?, claimed_at = CURRENT_TIMESTAMP WHERE id = ?", (cursor.lastrowid, token_data['id']))
                db.commit()
                flash("Account created successfully! You can now log in.", "success")
                return redirect(url_for('auth.login'))
            except sqlite3.Error as e:
                db.rollback()
                flash(f"A database error occurred: {e}", "danger")
    return render_template('register.html', form=form)