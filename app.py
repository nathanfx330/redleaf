# --- File: ./app.py ---
import os
import sqlite3
import sys
import traceback
import queue
import time
import threading
import datetime
import multiprocessing
import secrets
import fitz
import math
import json
import re
from pathlib import Path
from urllib.parse import unquote
from concurrent.futures import ProcessPoolExecutor, CancelledError

try:
    from concurrent.futures import BrokenProcessPool
except ImportError:
    class BrokenProcessPool(Exception):
        pass

import functools
from functools import wraps
from flask import Flask, render_template, g, request, redirect, url_for, flash, jsonify, send_from_directory, abort, session
from werkzeug.exceptions import NotFound, Forbidden
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf import CSRFProtect, FlaskForm
from markupsafe import Markup

# Import our backend engine modules
import storage_setup
import processing_pipeline

# --- Configuration ---
DATABASE_FILE = "knowledge_base.db"
DOCUMENTS_DIR = Path("./documents").resolve()
DOCUMENTS_DIR.mkdir(exist_ok=True)
ENTITY_LABELS_TO_DISPLAY = ['PERSON', 'GPE', 'LOC', 'ORG', 'DATE']

# --- NEW: Improved Secret Key Handling ---
# Create an 'instance' folder for non-public, instance-specific files.
# This is a standard Flask convention.
INSTANCE_DIR = Path("./instance").resolve()
INSTANCE_DIR.mkdir(exist_ok=True)
SECRET_KEY_FILE = INSTANCE_DIR / "secret.key"

def get_or_create_secret_key():
    """
    Looks for a secret key file. If it exists, reads the key.
    If not, it creates a new secure key and saves it.
    This ensures each Redleaf installation is automatically secure.
    """
    if SECRET_KEY_FILE.exists():
        # Key already exists, load it
        return SECRET_KEY_FILE.read_text().strip()
    else:
        # First time setup: Generate and save a new key
        print("\n!!! First time setup: Generating a new, unique secret key. !!!")
        print(f"!!! This will be stored at: {SECRET_KEY_FILE} !!!\n")
        new_key = secrets.token_hex(24)
        try:
            # Save the key to the file
            SECRET_KEY_FILE.write_text(new_key)
            # On POSIX systems (Linux/macOS), set restrictive permissions
            if os.name == 'posix':
                os.chmod(SECRET_KEY_FILE, 0o600) # Only readable/writable by the owner
        except Exception as e:
            print(f"FATAL: Could not write secret key to file! Error: {e}")
            # This is a critical failure, so we should exit.
            sys.exit("Application cannot start without a secret key.")
        return new_key

# --- Flask App Setup ---
app = Flask(__name__, instance_path=INSTANCE_DIR)
# Load the secret key using our new function
app.config['SECRET_KEY'] = get_or_create_secret_key()
# Load other config from this file (like DATABASE_FILE)
app.config.from_object(__name__)
csrf = CSRFProtect(app)

class SecureForm(FlaskForm):
    pass

# --- Database Handling ---
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(
            app.config['DATABASE_FILE'], detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES, timeout=15
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON;")
        g.db.execute("PRAGMA journal_mode = WAL;")
    return g.db

@app.teardown_appcontext
def close_connection(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

# --- Manager Thread Architecture ---
task_queue = queue.Queue()
active_tasks = {}
active_tasks_lock = threading.Lock()
executor = None
restart_executor_event = threading.Event()

# --- REFACTORED: Central function to get all system settings ---
def get_system_settings():
    """Reads all settings from the database and returns them as a dict."""
    settings = {'max_workers': 2, 'use_gpu': False, 'html_parsing_mode': 'generic'}
    conn = None
    try:
        conn = sqlite3.connect(app.config['DATABASE_FILE'])
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
        db_settings = {row[0]: row[1] for row in rows}
        
        if db_settings.get('max_workers', '').isdigit():
            settings['max_workers'] = int(db_settings['max_workers'])
        
        settings['use_gpu'] = db_settings.get('use_gpu') == 'true'
        
        if db_settings.get('html_parsing_mode') in ['generic', 'pipermail']:
            settings['html_parsing_mode'] = db_settings['html_parsing_mode']
            
    except Exception as e:
        print(f"Could not read app_settings from DB, using defaults. Error: {e}")
    finally:
        if conn:
            conn.close()
    return settings

# --- MODIFIED: init_worker now accepts the use_gpu flag ---
def init_worker(use_gpu=False):
    """Initializes a worker process, optionally enabling GPU."""
    print(f"Initializing worker process: {os.getpid()}...")
    if use_gpu:
        try:
            spacy.require_gpu()
            print(f"--- SUCCESS: Worker {os.getpid()} has acquired GPU access. ---")
        except Exception as e:
            # This is a warning, not a fatal error. The worker can proceed with CPU.
            print(f"!!! WARNING: Worker {os.getpid()} failed to acquire GPU, falling back to CPU. Error: {e} !!!")
    
    try:
        processing_pipeline.load_spacy_model()
        print(f"Worker {os.getpid()} successfully initialized spaCy model.")
    except Exception as e:
        print(f"FATAL ERROR initializing spaCy in worker {os.getpid()}: {e}")
        # This will cause the process pool to break, which is intended.
        raise

def manager_thread_loop():
    global executor, active_tasks
    print("--- Task Manager Thread Started ---")
    
    # --- MODIFIED: Use the new helper function ---
    current_settings = get_system_settings()

    while True:
        try:
            if restart_executor_event.is_set() and not active_tasks:
                print("--- Restarting Process Pool Executor... ---")
                if executor:
                    executor.shutdown(wait=True)
                executor = None
                restart_executor_event.clear()
                # Re-fetch settings on restart
                current_settings = get_system_settings()

            if executor is None:
                print(f"Manager: Creating new ProcessPoolExecutor with {current_settings['max_workers']} workers. GPU: {current_settings['use_gpu']}")
                # --- MODIFIED: Use functools.partial to pass the GPU setting to the initializer ---
                initializer_func = functools.partial(init_worker, use_gpu=current_settings['use_gpu'])
                executor = ProcessPoolExecutor(max_workers=current_settings['max_workers'], initializer=initializer_func)
                with active_tasks_lock:
                    active_tasks = {}

            with active_tasks_lock:
                active_process_count = sum(1 for v in active_tasks.values() if v[0] == 'process')
            
            try:
                task_type, item_id = task_queue.get_nowait()

                if task_type == 'process':
                    if active_process_count < current_settings['max_workers']:
                        future = executor.submit(processing_pipeline.process_document, item_id)
                        with active_tasks_lock:
                            active_tasks[future] = (task_type, item_id)
                    else:
                        task_queue.put((task_type, item_id)) 
                
                elif task_type in ['discover', 'cache']:
                    print(f"Manager: Starting lightweight task '{task_type}' in a new thread.")
                    target_func = None
                    if task_type == 'discover':
                        target_func = processing_pipeline.discover_and_register_documents
                    elif task_type == 'cache':
                        target_func = processing_pipeline.update_browse_cache
                    
                    if target_func:
                        thread = threading.Thread(target=target_func)
                        with active_tasks_lock:
                            active_tasks[thread] = (task_type, item_id)
                        thread.start()
            except queue.Empty:
                pass

            with active_tasks_lock:
                if not active_tasks:
                    time.sleep(1)
                    continue
                
                done_tasks = []
                for task, info in active_tasks.items():
                    if isinstance(task, threading.Thread):
                        if not task.is_alive():
                            done_tasks.append(task)
                    else: 
                        if task.done():
                            done_tasks.append(task)

            finished_tasks_info = []
            for task in done_tasks:
                with active_tasks_lock:
                    if task in active_tasks:
                        task_info = active_tasks.pop(task)
                        finished_tasks_info.append(task_info)
                
                if not isinstance(task, threading.Thread): 
                    try:
                        task.result()
                        print(f"Manager: Process task '{task_info[0]}' for item '{task_info[1]}' completed successfully.")
                    except Exception as e:
                        print(f"!!! MANAGER DETECTED A WORKER FAILURE for task '{task_info[0]}' on item '{task_info[1]}': {type(e).__name__} !!!")
                        print(traceback.format_exc())
                else: 
                    print(f"Manager: Thread task '{task_info[0]}' completed.")

            if any(info[0] == 'process' for info in finished_tasks_info):
                with active_tasks_lock:
                    is_cache_task_pending = any(v[0] == 'cache' for v in active_tasks.values()) or \
                                            any(item[0] == 'cache' for item in list(task_queue.queue))
                if not is_cache_task_pending:
                    print("Manager: Document processing finished. Automatically queueing browse cache update.")
                    task_queue.put(('cache', None))
                    
            time.sleep(1)
        except (BrokenProcessPool, Exception) as e:
            print(f"!!! FATAL ERROR IN MANAGER THREAD: {e} !!!")
            print(traceback.format_exc())
            if executor:
                executor.shutdown(wait=False, cancel_futures=True)
            executor = None
            time.sleep(10)

# --- Template Filters ---
@app.template_filter('strftime')
def _jinja2_filter_datetime(date, fmt='%Y-%m-%d %H:%M'):
    if not date: return "N/A"
    if isinstance(date, str):
        try: date = datetime.datetime.fromisoformat(date)
        except (ValueError, TypeError):
            try: date = datetime.datetime.strptime(date, '%Y-%m-%d %H:%M:%S.%f')
            except (ValueError, TypeError):
                try: date = datetime.datetime.strptime(date, '%Y-%m-%d %H:%M:%S')
                except (ValueError, TypeError): return date
    return date.strftime(fmt) if hasattr(date, 'strftime') else date

@app.template_filter('filesize')
def _jinja2_filter_filesize(size_bytes):
    if size_bytes is None:
        return "N/A"
    if size_bytes == 0:
        return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"

@app.template_filter('escapejs')
def escapejs_filter(value):
    """Escape strings for inclusion in Javascript."""
    return Markup(json.dumps(value)[1:-1])

# ===================================================================
# AUTH, SETUP & REGISTRATION
# ===================================================================
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not g.user:
            flash("Please log in to access this page.", "info")
            return redirect(url_for('login', next=request.url))
        if g.user['role'] != 'admin':
            abort(403, "You must be an administrator to access this page.")
        return f(*args, **kwargs)
    return decorated_function

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not g.user:
            flash("Please log in to access this page.", "info")
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

@app.before_request
def check_setup_and_load_user():
    g.user = None
    # Allowlist endpoints that do not require a full setup check
    if request.endpoint in ['static', 'setup', 'login', 'register']:
        if request.endpoint == 'setup':
            db = get_db()
            try:
                if db.execute('SELECT COUNT(id) FROM users').fetchone()[0] > 0:
                    return redirect(url_for('login'))
            except sqlite3.OperationalError:
                pass # Table doesn't exist, allow setup to proceed
        if 'user_id' in session:
            try:
                db = get_db()
                g.user = db.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
            except sqlite3.OperationalError:
                # DB was just deleted, session is invalid
                session.clear()
        return

    # For all other endpoints, perform the rigorous check
    db_path = Path(app.config['DATABASE_FILE'])
    if not db_path.exists():
        # This can happen if the db is deleted while the app is running
        storage_setup.create_unified_index(db_path)
        return redirect(url_for('setup'))

    db = get_db()
    try:
        # Check if users table exists and is populated
        if db.execute('SELECT COUNT(id) FROM users').fetchone()[0] == 0:
            return redirect(url_for('setup'))
    except sqlite3.OperationalError:
        # This case handles a corrupted or partially created DB
        close_connection(None) 
        if db_path.exists(): db_path.unlink()
        storage_setup.create_unified_index(db_path)
        return redirect(url_for('setup'))

    if 'user_id' in session:
        g.user = db.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
        if not g.user:
            session.clear()

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    db = get_db()
    # Double-check in case of race conditions
    try:
        if db.execute('SELECT COUNT(id) FROM users').fetchone()[0] > 0:
            return redirect(url_for('login'))
    except sqlite3.OperationalError:
        # DB is malformed, recreate it and redirect back to setup
        close_connection(None)
        db_path = Path(app.config['DATABASE_FILE'])
        if db_path.exists(): db_path.unlink()
        storage_setup.create_unified_index(db_path)
        return redirect(url_for('setup'))

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
            return redirect(url_for('login'))
    return render_template('setup.html', form=form)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if g.user:
        return redirect(url_for('dashboard'))
    form = SecureForm()
    if form.validate_on_submit():
        username = request.form.get('username')
        password = request.form.get('password')
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        if user and check_password_hash(user['password_hash'], password):
            session.clear()
            session['user_id'] = user['id']
            next_page = request.args.get('next') or url_for('dashboard')
            return redirect(next_page)
        flash('Incorrect username or password.', 'danger')
    return render_template('login.html', form=form)

@app.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if g.user:
        return redirect(url_for('dashboard'))
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
                return redirect(url_for('login'))
            except sqlite3.Error as e:
                db.rollback()
                flash(f"An error occurred: {e}", "danger")
    return render_template('register.html', form=form)

# ===================================================================
# HELPER FUNCTIONS
# ===================================================================

def _get_dashboard_state(db):
    """Helper to get the current state for the dashboard UI."""
    statuses = [row['status'] for row in db.execute("SELECT status FROM documents").fetchall()]
    
    with active_tasks_lock:
        active_task_list = list(active_tasks.values())
    queued_task_types = [item[0] for item in list(task_queue.queue)]

    task_states = {'discover': 'standard', 'process': 'standard', 'cache': 'standard'}
    
    is_discover_active = any(t[0] == 'discover' for t in active_task_list) or 'discover' in queued_task_types
    is_process_active = any(t[0] == 'process' for t in active_task_list) or 'process' in queued_task_types
    is_cache_active = any(t[0] == 'cache' for t in active_task_list) or 'cache' in queued_task_types

    if is_discover_active:
        primary_action = 'discover'
    elif is_process_active:
        primary_action = 'process'
    elif is_cache_active:
        primary_action = 'cache'
    elif 'New' in statuses:
        primary_action = 'process'
    elif 'Indexed' in statuses:
        primary_action = 'cache'
    else:
        primary_action = 'discover'
        
    task_states[primary_action] = 'primary'
    
    if is_discover_active: task_states['discover'] = 'disabled'
    if is_process_active: task_states['process'] = 'disabled'
    if is_cache_active: task_states['cache'] = 'disabled'
        
    return {
        'queue_size': task_queue.qsize(),
        'task_states': task_states
    }

def _create_manual_snippet(page_content, subject, object_text, context=80):
    """
    Manually creates a highlighted snippet from raw text.
    This is more robust than fts5 snippet() when search terms are not valid fts5 tokens.
    """
    if not page_content:
        return ""

    content_lower = page_content.lower()
    subject_lower = subject.lower()
    object_lower = object_text.lower()

    try:
        subject_start = content_lower.index(subject_lower)
        object_start = content_lower.index(object_lower)
    except ValueError:
        return page_content[:context*2] + "..."

    first_entity_start = min(subject_start, object_start)
    last_entity_end = max(subject_start + len(subject), object_start + len(object_text))

    snippet_start = max(0, first_entity_start - context)
    snippet_end = min(len(page_content), last_entity_end + context)

    snippet = ""
    if snippet_start > 0:
        snippet += "... "
    snippet += page_content[snippet_start:snippet_end]
    if snippet_end < len(page_content):
        snippet += " ..."
    
    snippet = re.sub(f'({re.escape(subject)})', r'<strong>\1</strong>', snippet, flags=re.IGNORECASE)
    snippet = re.sub(f'({re.escape(object_text)})', r'<strong>\1</strong>', snippet, flags=re.IGNORECASE)

    return Markup(snippet)

# ===================================================================
# MAIN APPLICATION ROUTES
# ===================================================================

@app.route('/settings')
@admin_required
def settings():
    db = get_db()
    users = db.execute("SELECT id, username, role, created_at FROM users ORDER BY username").fetchall()
    tokens_query = """
        SELECT it.id, it.token_value, it.created_at, it.claimed_at,
               creator.username as creator_username,
               claimer.username as claimer_username
        FROM invitation_tokens it
        JOIN users creator ON it.created_by_user_id = creator.id
        LEFT JOIN users claimer ON it.claimed_by_user_id = claimer.id
        ORDER BY it.created_at DESC
    """
    tokens = db.execute(tokens_query).fetchall()
    form = SecureForm()
    
    # --- MODIFIED: Use the new helper function ---
    system_settings = get_system_settings()

    return render_template('settings.html', 
        users=users, 
        tokens=tokens, 
        form=form, 
        max_workers=system_settings['max_workers'],
        use_gpu=system_settings['use_gpu'], # --- ADDED: Pass GPU status to template
        cpu_count=os.cpu_count(),
        html_parsing_mode=system_settings['html_parsing_mode']
    )

@app.route('/settings/workers', methods=['POST'])
@admin_required
def update_workers():
    form = SecureForm()
    if form.validate_on_submit():
        try:
            max_cpus = (os.cpu_count() or 2) * 4
            new_worker_count = int(request.form.get('max_workers'))
            if new_worker_count < 1 or new_worker_count > max_cpus:
                 flash(f'Please enter a reasonable number of workers (e.g., 1 to {max_cpus}).', 'danger')
            else:
                db = get_db()
                db.execute("UPDATE app_settings SET value = ? WHERE key = 'max_workers'", (str(new_worker_count),))
                db.commit()
                restart_executor_event.set()
                flash(f'Worker count updated to {new_worker_count}. The change will apply once current tasks are finished.', 'success')
        except (ValueError, TypeError):
            flash('Invalid number entered for worker count.', 'danger')
    else:
        flash("CSRF validation failed.", 'danger')
    return redirect(url_for('settings'))

# --- ADDED: New route to handle GPU setting change ---
@app.route('/settings/gpu', methods=['POST'])
@admin_required
def update_gpu_setting():
    form = SecureForm()
    if form.validate_on_submit():
        # A checkbox sends 'on' when checked, and nothing when not.
        use_gpu_enabled = request.form.get('use_gpu') == 'on'
        use_gpu_str = 'true' if use_gpu_enabled else 'false'
        
        db = get_db()
        db.execute("UPDATE app_settings SET value = ? WHERE key = 'use_gpu'", (use_gpu_str,))
        db.commit()
        
        restart_executor_event.set()
        
        status = "enabled" if use_gpu_enabled else "disabled"
        flash(f'GPU acceleration has been {status}. The change will apply once current tasks are finished.', 'success')
    else:
        flash("CSRF validation failed.", 'danger')
    return redirect(url_for('settings'))

@app.route('/settings/html', methods=['POST'])
@admin_required
def update_html_settings():
    form = SecureForm()
    if form.validate_on_submit():
        new_mode = request.form.get('html_parsing_mode')
        if new_mode in ['generic', 'pipermail']:
            db = get_db()
            db.execute("UPDATE app_settings SET value = ? WHERE key = 'html_parsing_mode'", (new_mode,))
            db.commit()
            flash(f'HTML parsing strategy updated to "{new_mode}".', 'success')
        else:
            flash('Invalid HTML parsing mode selected.', 'danger')
    else:
        flash("CSRF validation failed.", 'danger')
    return redirect(url_for('settings'))

@app.route('/settings/create_token', methods=['POST'])
@admin_required
def create_token():
    form = SecureForm()
    if form.validate_on_submit():
        db = get_db()
        new_token = secrets.token_urlsafe(16)
        db.execute("INSERT INTO invitation_tokens (token_value, created_by_user_id) VALUES (?, ?)", (new_token, g.user['id']))
        db.commit()
        flash(f"New invitation token created: {new_token}", "success")
    else:
        flash("CSRF validation failed.", "danger")
    return redirect(url_for('settings'))

@app.route('/settings/delete_user/<int:user_id>', methods=['POST'])
@admin_required
def delete_user(user_id):
    form = SecureForm()
    if form.validate_on_submit():
        if user_id == g.user['id']:
            flash("You cannot delete your own account.", "danger")
        else:
            db = get_db()
            user = db.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
            if user:
                db.execute("DELETE FROM users WHERE id = ?", (user_id,))
                db.commit()
                flash(f"Successfully deleted user '{user['username']}'.", "success")
            else:
                flash("User not found.", "danger")
    else:
        flash("CSRF validation failed.", "danger")
    return redirect(url_for('settings'))

@app.route('/settings/revoke_token/<int:token_id>', methods=['POST'])
@admin_required
def revoke_token(token_id):
    form = SecureForm()
    if form.validate_on_submit():
        db = get_db()
        cursor = db.execute("DELETE FROM invitation_tokens WHERE id = ? AND claimed_by_user_id IS NULL", (token_id,))
        db.commit()
        if cursor.rowcount > 0:
            flash("Invitation token revoked.", "success")
        else:
            flash("Could not revoke token. It might have already been claimed or deleted.", "warning")
    else:
        flash("CSRF validation failed.", "danger")
    return redirect(url_for('settings'))

@app.route('/')
@app.route('/dashboard')
@login_required
def dashboard():
    db = get_db()
    
    doc_query = """
        SELECT
            d.*,
            (SELECT COUNT(*) FROM document_comments WHERE doc_id = d.id) as comment_count,
            (SELECT COUNT(*) FROM document_tags WHERE doc_id = d.id) as tag_count
        FROM documents d
    """
    documents = [dict(row) for row in db.execute(doc_query).fetchall()]
    
    state_data = _get_dashboard_state(db)
    form = SecureForm()

    return render_template(
        'dashboard.html', 
        documents=documents, 
        doc_dir=DOCUMENTS_DIR, 
        queue_size=state_data['queue_size'], 
        form=form,
        task_states=state_data['task_states']
    )

@app.route('/dashboard/discover')
@login_required
def dashboard_discover():
    task_queue.put(('discover', None))
    return redirect(url_for('dashboard'))

@app.route('/dashboard/process/all_new')
@login_required
def dashboard_process_all_new():
    db = get_db()
    doc_ids = [row['id'] for row in db.execute("SELECT id FROM documents WHERE status = 'New'").fetchall()]
    if not doc_ids:
        flash("No 'New' documents found to process.", "info")
    else:
        for doc_id in doc_ids:
            processing_pipeline.update_document_status(db, doc_id, 'Queued', 'Waiting for free worker...')
            task_queue.put(('process', doc_id))
        flash(f"Queued {len(doc_ids)} documents for processing.", "info")
    return redirect(url_for('dashboard'))

@app.route('/dashboard/process/<int:doc_id>')
@login_required
def dashboard_process_single(doc_id):
    processing_pipeline.update_document_status(get_db(), doc_id, 'Queued', 'Waiting for re-processing...')
    task_queue.put(('process', doc_id))
    flash(f"Queued document ID {doc_id} for re-processing.", "info")
    return redirect(url_for('dashboard'))

@app.route('/dashboard/update_cache')
@login_required
def dashboard_update_cache():
    task_queue.put(('cache', None))
    return redirect(url_for('dashboard'))

@app.route('/dashboard/reset_database', methods=['POST'])
@admin_required
def dashboard_reset_database():
    form = SecureForm()
    if form.validate_on_submit():
        # Clear the session before deleting the DB to prevent user loading errors
        session.clear()
        close_connection(None)
        db_path = Path(DATABASE_FILE)
        if db_path.exists():
            # Ensure the queue is empty before deleting the DB file
            while not task_queue.empty():
                try:
                    task_queue.get_nowait()
                except queue.Empty:
                    break
            db_path.unlink()
        flash("System has been completely reset. Please create a new admin account.", "success")
        return redirect(url_for('setup'))
    else:
        flash("CSRF validation failed.", "danger")
        return redirect(url_for('dashboard'))

@app.route('/discover')
@login_required
def discover_view():
    db = get_db()
    cached_entities = db.execute("SELECT entity_label, entity_text, document_count, appearance_count FROM browse_cache ORDER BY entity_label, appearance_count DESC, entity_text COLLATE NOCASE").fetchall()
    entities_by_label = {label: [] for label in ENTITY_LABELS_TO_DISPLAY}
    
    for entity_row in cached_entities:
        label = entity_row['entity_label']
        if label in entities_by_label:
            entities_by_label[label].append(dict(entity_row))
    
    # --- MODIFIED: Added form object to context ---
    form = SecureForm()
    return render_template(
        'discover.html', 
        entities_by_label=entities_by_label, 
        sorted_labels=ENTITY_LABELS_TO_DISPLAY,
        form=form
    )

@app.route('/discover/relationship')
@login_required
def relationship_detail_view():
    subject_id = request.args.get('subject_id', type=int)
    object_id = request.args.get('object_id', type=int)
    phrase = request.args.get('phrase', '')
    
    if not all([subject_id, object_id, phrase]):
        abort(400, "Missing required parameters for relationship detail.")

    db = get_db()
    subject = db.execute("SELECT text, label FROM entities WHERE id = ?", (subject_id,)).fetchone()
    object_entity = db.execute("SELECT text, label FROM entities WHERE id = ?", (object_id,)).fetchone()

    if not all([subject, object_entity]):
        abort(404, "One or more entities for this relationship could not be found.")

    return render_template(
        'relationship_detail.html',
        subject=subject,
        object_entity=object_entity,
        phrase=phrase,
        subject_id=subject_id,
        object_id=object_id
    )

@app.route('/discover/search')
@login_required
def search_results():
    query = request.args.get('q', '').strip()
    if not query:
        return redirect(url_for('discover_view'))

    sanitized_query = query.replace('"', '""')
    fts_query = f'"{sanitized_query}"'

    db = get_db()
    sql_query = """
        SELECT
            d.id as doc_id, d.relative_path, d.color, d.page_count, ci.page_number,
            snippet(content_index, 2, '<strong>', '</strong>', '...', 20) as snippet,
            (SELECT COUNT(*) FROM document_comments WHERE doc_id = d.id) as comment_count,
            (SELECT 1 FROM document_curation WHERE doc_id = d.id AND user_id = ?) as has_personal_note,
            (SELECT 1 FROM document_tags WHERE doc_id = d.id LIMIT 1) as has_tags,
            (SELECT GROUP_CONCAT(c.name, ', ') 
             FROM catalogs c 
             JOIN document_catalogs dc ON c.id = dc.catalog_id 
             WHERE dc.doc_id = d.id) as catalog_names
        FROM content_index ci
        JOIN documents d ON ci.doc_id = d.id
        WHERE content_index MATCH ? ORDER BY rank;
    """
    db_results = db.execute(sql_query, (g.user['id'], fts_query)).fetchall()
    results = [dict(row) for row in db_results]
    return render_template('search_results.html', query=query, results=results)

@app.route('/discover/entity/<label>/<path:text>')
@login_required
def entity_detail(label, text):
    db = get_db()
    entity = db.execute("SELECT id FROM entities WHERE text = ? AND label = ?", (text, label)).fetchone()
    if not entity:
        abort(404, "Entity not found in the database.")

    user_id = g.user['id']
    query_text = f'"{text.replace(" ", " NEAR/2 ")}"'
    
    sql_query = """
        SELECT
            d.id as doc_id,
            d.relative_path,
            d.color,
            d.page_count,
            ea.page_number,
            (SELECT snippet(content_index, 2, '<strong>', '</strong>', '...', 20)
             FROM content_index ci
             WHERE ci.doc_id = d.id AND ci.page_number = ea.page_number AND content_index MATCH ?) as snippet,
            (SELECT COUNT(*) FROM document_comments WHERE doc_id = d.id) as comment_count,
            (SELECT 1 FROM document_curation WHERE doc_id = d.id AND user_id = ?) as has_personal_note,
            (SELECT 1 FROM document_tags WHERE doc_id = d.id LIMIT 1) as has_tags,
            (SELECT GROUP_CONCAT(c.name, ', ') 
             FROM catalogs c 
             JOIN document_catalogs dc ON c.id = dc.catalog_id 
             WHERE dc.doc_id = d.id) as catalog_names
        FROM entity_appearances ea
        JOIN entities e ON ea.entity_id = e.id
        JOIN documents d ON ea.doc_id = d.id
        WHERE e.id = ?
        ORDER BY d.relative_path COLLATE NOCASE, ea.page_number;
    """

    db_results = db.execute(sql_query, (query_text, user_id, entity['id'])).fetchall()
    results = [dict(row) for row in db_results]
    return render_template('entity_detail.html', label=label, text=text, entity_id=entity['id'], results=results)

@app.route('/tags')
@login_required
def tags_index():
    db = get_db()
    tags = db.execute("""
        SELECT t.name, COUNT(dt.doc_id) as doc_count
        FROM tags t 
        LEFT JOIN document_tags dt ON t.id = dt.tag_id
        GROUP BY t.id, t.name
        ORDER BY t.name COLLATE NOCASE
    """).fetchall()
    catalogs = db.execute("SELECT id, name FROM catalogs ORDER BY name COLLATE NOCASE").fetchall()
    form = SecureForm()
    return render_template('tags_index.html', tags=tags, catalogs=catalogs, form=form)

@app.route('/catalogs')
@login_required
def catalog_view():
    db = get_db()
    
    view_query = """
        SELECT
            c.id as catalog_id, c.name as catalog_name, c.description as catalog_description,
            d.id as doc_id, d.relative_path, d.color,
            (SELECT COUNT(*) FROM document_comments WHERE doc_id = d.id) as comment_count,
            (SELECT 1 FROM document_curation WHERE doc_id = d.id AND user_id = ?) as has_personal_note,
            (SELECT 1 FROM document_tags WHERE doc_id = d.id LIMIT 1) as has_tags
        FROM catalogs c
        LEFT JOIN document_catalogs dc ON c.id = dc.catalog_id
        LEFT JOIN documents d ON dc.doc_id = d.id
        ORDER BY c.name COLLATE NOCASE, d.relative_path COLLATE NOCASE;
    """
    view_results = db.execute(view_query, (g.user['id'],)).fetchall()
    catalogs_for_view = {}
    for row in view_results:
        cat_id = row['catalog_id']
        if cat_id not in catalogs_for_view:
            catalogs_for_view[cat_id] = {'id': cat_id, 'name': row['catalog_name'], 'description': row['catalog_description'], 'documents': []}
        if row['doc_id']:
            doc_data = dict(row)
            doc_data['id'] = row['doc_id']
            catalogs_for_view[cat_id]['documents'].append(doc_data)
    
    manage_query = """
        SELECT c.id, c.name, c.description, COUNT(dc.doc_id) as doc_count
        FROM catalogs c
        LEFT JOIN document_catalogs dc ON c.id = dc.catalog_id
        GROUP BY c.id, c.name, c.description
        ORDER BY c.name COLLATE NOCASE;
    """
    catalogs_for_management = db.execute(manage_query).fetchall()
    
    form = SecureForm()
    return render_template('catalogs.html', 
                           catalogs_for_view=list(catalogs_for_view.values()), 
                           catalogs_for_management=catalogs_for_management, 
                           form=form)

@app.route('/document/<int:doc_id>')
@login_required
def document_view(doc_id):
    db = get_db()
    doc = db.execute("SELECT id, relative_path, color, page_count, file_type FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc: abort(404)
    form = SecureForm()
    return render_template('document_view.html', doc=doc, form=form)

@app.route('/serve_doc/<path:relative_path>')
@login_required
def serve_document(relative_path):
    safe_path = DOCUMENTS_DIR.joinpath(relative_path).resolve()
    if not safe_path.is_relative_to(DOCUMENTS_DIR.resolve()):
        abort(403)
    return send_from_directory(DOCUMENTS_DIR, relative_path)

@app.route('/view_text/<int:doc_id>')
@login_required
def view_text_document(doc_id):
    """
    Renders a custom, paginated HTML viewer for plain text documents.
    """
    db = get_db()
    # Fetch the document title first
    doc_meta = db.execute("SELECT relative_path, file_type FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc_meta:
        abort(404)
    if doc_meta['file_type'] != 'TXT':
        # This route is only for TXT files, redirect others to the raw file
        return redirect(url_for('serve_document', relative_path=doc_meta['relative_path']))

    # Fetch all pages for this document from the content index
    pages_cursor = db.execute(
        "SELECT page_number, page_content FROM content_index WHERE doc_id = ? ORDER BY page_number ASC",
        (doc_id,)
    )
    pages = [row['page_content'] for row in pages_cursor.fetchall()]

    if not pages:
        return "This text document has not been indexed yet or contains no content.", 404

    return render_template('text_viewer.html', pages=pages, doc_title=doc_meta['relative_path'], doc_id=doc_id)

@app.route('/view_html/<int:doc_id>')
@login_required
def view_html_document(doc_id):
    """
    Renders a custom, paginated HTML viewer for the extracted text of HTML documents.
    """
    db = get_db()
    doc_meta = db.execute("SELECT relative_path, file_type FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc_meta:
        abort(404)
    if doc_meta['file_type'] != 'HTML':
        # This should not happen if the frontend is correct, but as a safeguard:
        return redirect(url_for('serve_document', relative_path=doc_meta['relative_path']))

    pages_cursor = db.execute(
        "SELECT page_number, page_content FROM content_index WHERE doc_id = ? ORDER BY page_number ASC",
        (doc_id,)
    )
    pages = [row['page_content'] for row in pages_cursor.fetchall()]

    if not pages:
        return "This HTML document has not been indexed yet or contains no extractable content.", 404

    return render_template('html_viewer.html', pages=pages, doc_title=doc_meta['relative_path'], doc_id=doc_id)

# ===================================================================
# API ROUTES
# ===================================================================

@app.route('/api/dashboard/status')
@login_required
def api_dashboard_status():
    """Provides real-time status updates for the dashboard."""
    db = get_db()
    
    doc_query = """
        SELECT
            d.id, d.status, d.status_message, d.processed_at, d.page_count, d.color, d.relative_path, 
            d.file_size_bytes, d.file_type,
            (SELECT COUNT(*) FROM document_comments WHERE doc_id = d.id) as comment_count,
            (SELECT COUNT(*) FROM document_tags WHERE doc_id = d.id) as tag_count
        FROM documents d
    """
    docs_data = db.execute(doc_query).fetchall()
    
    state_data = _get_dashboard_state(db)

    return jsonify({
        'documents': [dict(row) for row in docs_data],
        'queue_size': state_data['queue_size'],
        'task_states': state_data['task_states']
    })

@app.route('/api/relationships/detail')
@login_required
def get_relationship_details():
    subject_id = request.args.get('subject_id', type=int)
    object_id = request.args.get('object_id', type=int)
    phrase = request.args.get('phrase', '')

    if not all([subject_id, object_id, phrase]):
        return jsonify({"error": "Missing required parameters"}), 400

    db = get_db()
    
    subject_text = db.execute("SELECT text FROM entities WHERE id = ?", (subject_id,)).fetchone()['text']
    object_text = db.execute("SELECT text FROM entities WHERE id = ?", (object_id,)).fetchone()['text']
    
    query = """
        SELECT
            r.doc_id, r.page_number, d.relative_path, d.color, d.page_count,
            (SELECT page_content FROM content_index ci WHERE ci.doc_id = r.doc_id AND ci.page_number = r.page_number) as page_content,
            (SELECT COUNT(*) FROM document_comments WHERE doc_id = d.id) as comment_count,
            (SELECT 1 FROM document_curation WHERE doc_id = d.id AND user_id = ?) as has_personal_note,
            (SELECT 1 FROM document_tags WHERE doc_id = d.id LIMIT 1) as has_tags,
            (SELECT GROUP_CONCAT(c.name, ', ') 
             FROM catalogs c 
             JOIN document_catalogs dc ON c.id = dc.catalog_id 
             WHERE dc.doc_id = d.id) as catalog_names
        FROM entity_relationships r
        JOIN documents d ON r.doc_id = d.id
        WHERE r.subject_entity_id = ? AND r.object_entity_id = ? AND r.relationship_phrase = ?
        ORDER BY d.relative_path COLLATE NOCASE, r.page_number;
    """
    
    params = (g.user['id'], subject_id, object_id, phrase)
    db_results = db.execute(query, params).fetchall()

    final_results = []
    for row in db_results:
        row_dict = dict(row)
        page_content = row_dict.pop('page_content', '')
        row_dict['snippet'] = _create_manual_snippet(page_content, subject_text, object_text)
        final_results.append(row_dict)
    
    return jsonify(final_results)

@app.route('/api/relationships/top')
@login_required
def get_top_relationships():
    db = get_db()
    limit = request.args.get('limit', 100, type=int)
    
    # --- MODIFIED: The query now LEFT JOINs against the archived table and excludes matches ---
    query = """
        SELECT 
            s.id as subject_id, s.text as subject_text, s.label as subject_label,
            o.id as object_id, o.text as object_text, o.label as object_label,
            r.relationship_phrase, COUNT(r.id) as rel_count
        FROM entity_relationships r
        JOIN entities s ON r.subject_entity_id = s.id
        JOIN entities o ON r.object_entity_id = o.id
        LEFT JOIN archived_relationships ar 
            ON r.subject_entity_id = ar.subject_entity_id 
            AND r.object_entity_id = ar.object_entity_id 
            AND r.relationship_phrase = ar.relationship_phrase
        WHERE ar.subject_entity_id IS NULL
        GROUP BY s.id, s.text, s.label, o.id, o.text, o.label, r.relationship_phrase
        ORDER BY rel_count DESC LIMIT ?;
    """
    top_relations = db.execute(query, (limit,)).fetchall()
    return jsonify([dict(row) for row in top_relations])

# --- ADDED: API endpoint to archive a batch of relationships ---
@app.route('/api/relationships/archive', methods=['POST'])
@login_required
def archive_relationships():
    relationships_to_archive = request.json.get('relationships', [])
    if not relationships_to_archive:
        return jsonify({'success': False, 'message': 'No relationships provided.'}), 400
    
    db = get_db()
    try:
        # We use a list of tuples for executemany
        params = [
            (rel['subject_id'], rel['object_id'], rel['phrase'])
            for rel in relationships_to_archive
        ]
        db.executemany(
            "INSERT OR IGNORE INTO archived_relationships (subject_entity_id, object_entity_id, relationship_phrase) VALUES (?, ?, ?)",
            params
        )
        db.commit()
        return jsonify({'success': True, 'message': f'Archived {len(params)} relationships.'})
    except (sqlite3.Error, KeyError) as e:
        db.rollback()
        return jsonify({'success': False, 'message': f'Database error or malformed data: {e}'}), 500

# --- ADDED: API endpoint to get all archived relationships for the settings page ---
@app.route('/api/settings/archived-relationships', methods=['GET'])
@admin_required
def get_archived_relationships():
    db = get_db()
    query = """
        SELECT 
            ar.subject_entity_id, s.text as subject_text,
            ar.object_entity_id, o.text as object_text,
            ar.relationship_phrase, ar.archived_at
        FROM archived_relationships ar
        JOIN entities s ON ar.subject_entity_id = s.id
        JOIN entities o ON ar.object_entity_id = o.id
        ORDER BY ar.archived_at DESC;
    """
    archived_list = db.execute(query).fetchall()
    return jsonify([dict(row) for row in archived_list])

# --- ADDED: API endpoint to un-archive a single relationship ---
@app.route('/api/settings/unarchive-relationship', methods=['POST'])
@admin_required
def unarchive_relationship():
    data = request.json
    try:
        subject_id = data['subject_id']
        object_id = data['object_id']
        phrase = data['phrase']
    except KeyError:
        return jsonify({'success': False, 'message': 'Missing required relationship data.'}), 400
        
    db = get_db()
    try:
        cursor = db.execute(
            "DELETE FROM archived_relationships WHERE subject_entity_id = ? AND object_entity_id = ? AND relationship_phrase = ?",
            (subject_id, object_id, phrase)
        )
        db.commit()
        if cursor.rowcount > 0:
            return jsonify({'success': True, 'message': 'Relationship un-archived.'})
        else:
            return jsonify({'success': False, 'message': 'Relationship not found in archive.'}), 404
    except sqlite3.Error as e:
        db.rollback()
        return jsonify({'success': False, 'message': f'Database error: {e}'}), 500

@app.route('/api/entity/<int:entity_id>/relationships')
@login_required
def get_entity_relationships(entity_id):
    db = get_db()
    query = """
        WITH RECURSIVE numbered_rels AS (
            SELECT *, ROW_NUMBER() OVER(PARTITION BY subject_entity_id, object_entity_id, relationship_phrase ORDER BY doc_id, page_number) as rn
            FROM entity_relationships
        ), aggregated_rels AS (
            SELECT subject_entity_id, object_entity_id, relationship_phrase, COUNT(*) as rel_count
            FROM numbered_rels
            GROUP BY subject_entity_id, object_entity_id, relationship_phrase
        )
        SELECT 'subject' as role, ar.relationship_phrase, e.id as other_entity_id, e.text as other_entity_text, e.label as other_entity_label, ar.rel_count as count
        FROM aggregated_rels ar JOIN entities e ON ar.object_entity_id = e.id
        WHERE ar.subject_entity_id = ?
        UNION ALL
        SELECT 'object' as role, ar.relationship_phrase, e.id as other_entity_id, e.text as other_entity_text, e.label as other_entity_label, ar.rel_count as count
        FROM aggregated_rels ar JOIN entities e ON ar.subject_entity_id = e.id
        WHERE ar.object_entity_id = ?
        ORDER BY count DESC;
    """
    relationships = db.execute(query, (entity_id, entity_id)).fetchall()
    return jsonify([dict(row) for row in relationships])

@app.route('/settings/user/<int:user_id>/change-password', methods=['POST'])
@login_required
def change_user_password(user_id):
    if g.user['role'] != 'admin' and g.user['id'] != user_id:
        abort(403, "You do not have permission to perform this action.")
    new_password = request.json.get('new_password', '').strip()
    if not new_password or len(new_password) < 8:
        return jsonify({'success': False, 'message': 'Password cannot be empty and must be at least 8 characters long.'}), 400
    db = get_db()
    user_to_update = db.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user_to_update:
        return jsonify({'success': False, 'message': 'User not found.'}), 404
    try:
        hashed_password = generate_password_hash(new_password)
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hashed_password, user_id))
        db.commit()
        return jsonify({'success': True, 'message': 'Password updated successfully.'})
    except sqlite3.Error as e:
        db.rollback()
        return jsonify({'success': False, 'message': f'Database error: {e}'}), 500

@app.route('/catalogs/create', methods=['POST'])
@login_required
def create_catalog():
    form = SecureForm()
    if form.validate_on_submit():
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        if not name:
            flash('Catalog name cannot be empty.', 'danger')
        else:
            db = get_db()
            try:
                db.execute("INSERT INTO catalogs (name, description) VALUES (?, ?)", (name, description))
                db.commit()
                flash(f"Catalog '{name}' created successfully.", 'success')
            except sqlite3.IntegrityError:
                db.rollback()
                flash('A catalog with this name already exists.', 'danger')
    else:
        flash("CSRF validation failed.", "danger")
    return redirect(url_for('catalog_view'))

@app.route('/catalogs/delete/<int:catalog_id>', methods=['POST'])
@login_required
def delete_catalog(catalog_id):
    form = SecureForm()
    if form.validate_on_submit():
        db = get_db()
        fav_catalog = db.execute("SELECT id FROM catalogs WHERE name = '⭐ Favorites'").fetchone()
        if fav_catalog and fav_catalog['id'] == catalog_id:
            flash('The Favorites catalog cannot be deleted.', 'danger')
        else:
            cursor = db.execute("DELETE FROM catalogs WHERE id = ?", (catalog_id,))
            db.commit()
            if cursor.rowcount > 0:
                flash('Catalog deleted successfully.', 'success')
            else:
                flash('Catalog not found.', 'warning')
    else:
        flash("CSRF validation failed.", "danger")
    return redirect(url_for('catalog_view'))

@app.route('/api/catalogs', methods=['POST'])
@login_required
def api_create_catalog():
    name = request.json.get('name', '').strip()
    description = request.json.get('description', '').strip()
    if not name:
        return jsonify({'success': False, 'message': 'Catalog name cannot be empty.'}), 400
    db = get_db()
    try:
        cursor = db.execute("INSERT INTO catalogs (name, description) VALUES (?, ?)", (name, description))
        db.commit()
        new_catalog = {'id': cursor.lastrowid, 'name': name, 'description': description}
        return jsonify({'success': True, 'catalog': new_catalog}), 201
    except sqlite3.IntegrityError:
        db.rollback()
        return jsonify({'success': False, 'message': 'A catalog with this name already exists.'}), 409

@app.route('/api/catalogs/<int:catalog_id>', methods=['PUT'])
@admin_required
def update_catalog(catalog_id):
    data = request.json
    name = data.get('name', '').strip()
    description = data.get('description', '').strip()
    if not name:
        return jsonify({'success': False, 'message': 'Catalog name cannot be empty.'}), 400
    db = get_db()
    fav_catalog = db.execute("SELECT id FROM catalogs WHERE name = '⭐ Favorites'").fetchone()
    if fav_catalog and fav_catalog['id'] == catalog_id:
        return jsonify({'success': False, 'message': 'The Favorites catalog cannot be modified.'}), 403
    try:
        db.execute("UPDATE catalogs SET name = ?, description = ? WHERE id = ?", (name, description, catalog_id))
        db.commit()
        return jsonify({'success': True, 'message': 'Catalog updated successfully.'})
    except sqlite3.IntegrityError:
        db.rollback()
        return jsonify({'success': False, 'message': f"A catalog with the name '{name}' already exists."}), 409
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/catalogs/<int:catalog_id>', methods=['DELETE'])
@admin_required
def api_delete_catalog(catalog_id):
    db = get_db()
    fav_catalog = db.execute("SELECT id FROM catalogs WHERE name = '⭐ Favorites'").fetchone()
    if fav_catalog and fav_catalog['id'] == catalog_id:
        return jsonify({'success': False, 'message': 'The Favorites catalog cannot be deleted.'}), 403
    cursor = db.execute("DELETE FROM catalogs WHERE id = ?", (catalog_id,))
    db.commit()
    if cursor.rowcount > 0:
        return jsonify({'success': True, 'message': 'Catalog deleted successfully.'})
    else:
        return jsonify({'success': False, 'message': 'Catalog not found.'}), 404
        
@app.route('/api/document/<int:doc_id>/text', methods=['GET'])
@login_required
def get_document_text(doc_id):
    db = get_db()
    doc = db.execute("SELECT relative_path, file_type FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc:
        return jsonify({'success': False, 'message': 'Document not found.'}), 404
    full_path = DOCUMENTS_DIR / doc['relative_path']
    if not full_path.exists():
        return jsonify({'success': False, 'message': 'Document file is missing from the filesystem.'}), 404
    start_page = request.args.get('start_page', type=int)
    end_page = request.args.get('end_page', type=int)
    
    text_content = processing_pipeline.extract_text_for_copying(
        full_path, 
        file_type=doc['file_type'], 
        start_page=start_page, 
        end_page=end_page
    )
    
    return jsonify({'success': True, 'text': text_content})

@app.route('/api/document/<int:doc_id>/curation', methods=['GET'])
@login_required
def get_curation_data(doc_id):
    db = get_db()
    user_id = session['user_id']
    curation = db.execute("SELECT note FROM document_curation WHERE doc_id = ? AND user_id = ?", (doc_id, user_id)).fetchone()
    comments_query = """
        SELECT c.id, c.comment_text, c.created_at, u.username, u.id as user_id
        FROM document_comments c JOIN users u ON c.user_id = u.id
        WHERE c.doc_id = ? ORDER BY c.created_at ASC
    """
    comments = db.execute(comments_query, (doc_id,)).fetchall()
    all_catalogs = db.execute("SELECT id, name FROM catalogs ORDER BY name").fetchall()
    member_of_catalogs_rows = db.execute("SELECT catalog_id FROM document_catalogs WHERE doc_id = ?", (doc_id,)).fetchall()
    member_of_catalogs = {row['catalog_id'] for row in member_of_catalogs_rows}
    favorites_catalog = db.execute("SELECT id FROM catalogs WHERE name = '⭐ Favorites'").fetchone()
    favorites_catalog_id = favorites_catalog['id'] if favorites_catalog else None
    is_favorite = favorites_catalog_id in member_of_catalogs if favorites_catalog_id else False
    return jsonify({
        'is_favorite': is_favorite,
        'note': curation['note'] if curation and curation['note'] else '',
        'all_catalogs': [dict(cat) for cat in all_catalogs],
        'member_of_catalogs': list(member_of_catalogs),
        'comments': [dict(comment) for comment in comments],
        'current_user': {'id': g.user['id'], 'role': g.user['role']}
    })

@app.route('/api/document/<int:doc_id>/curation', methods=['POST'])
@login_required
def update_curation_data(doc_id):
    data = request.json
    db = get_db()
    db.execute(
        "INSERT INTO document_curation (doc_id, user_id, note, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP) ON CONFLICT(doc_id, user_id) DO UPDATE SET note=excluded.note, updated_at=excluded.updated_at",
        (doc_id, session['user_id'], data.get('note', ''))
    )
    db.commit()
    return jsonify({'success': True})

@app.route('/api/document/<int:doc_id>/color', methods=['POST'])
@login_required
def set_document_color(doc_id):
    color = request.json.get('color')
    db = get_db()
    db.execute("UPDATE documents SET color = ? WHERE id = ?", (color, doc_id))
    db.commit()
    return jsonify({'success': True, 'color': color})

@app.route('/api/document/<int:doc_id>/comments', methods=['POST'])
@login_required
def add_document_comment(doc_id):
    comment_text = request.json.get('comment_text', '').strip()
    if not comment_text:
        return jsonify({'success': False, 'message': 'Comment cannot be empty.'}), 400
    db = get_db()
    db.execute("INSERT INTO document_comments (doc_id, user_id, comment_text) VALUES (?, ?, ?)", (doc_id, g.user['id'], comment_text))
    db.commit()
    return jsonify({'success': True, 'message': 'Comment added.'}), 201

@app.route('/api/comments/<int:comment_id>', methods=['DELETE'])
@login_required
def delete_comment(comment_id):
    db = get_db()
    comment = db.execute("SELECT user_id FROM document_comments WHERE id = ?", (comment_id,)).fetchone()
    if not comment:
        return jsonify({'success': False, 'message': 'Comment not found.'}), 404
    if comment['user_id'] != g.user['id'] and g.user['role'] != 'admin':
        return jsonify({'success': False, 'message': 'You do not have permission to delete this comment.'}), 403
    db.execute("DELETE FROM document_comments WHERE id = ?", (comment_id,))
    db.commit()
    return jsonify({'success': True, 'message': 'Comment deleted.'})

@app.route('/api/tags', methods=['GET'])
@login_required
def get_all_tags():
    db = get_db()
    all_tags = [row['name'] for row in db.execute("SELECT name FROM tags ORDER BY name")]
    return jsonify(all_tags)

@app.route('/api/document/<int:doc_id>/tags', methods=['GET'])
@login_required
def get_document_tags(doc_id):
    db = get_db()
    tags = [row['name'] for row in db.execute("SELECT t.name FROM tags t JOIN document_tags dt ON t.id = dt.tag_id WHERE dt.doc_id = ? ORDER BY t.name", (doc_id,)).fetchall()]
    return jsonify({'success': True, 'tags': tags})

@app.route('/api/document/<int:doc_id>/tags', methods=['POST'])
@login_required
def set_document_tags(doc_id):
    tags = sorted(list(set(t.strip().lower() for t in request.json.get('tags', []) if t.strip())))
    db = get_db()
    try:
        db.execute("BEGIN")
        if tags:
            db.executemany("INSERT OR IGNORE INTO tags (name) VALUES (?)", [(tag,) for tag in tags])
            placeholders = ','.join('?' for _ in tags)
            tag_ids = [row['id'] for row in db.execute(f"SELECT id FROM tags WHERE name IN ({placeholders})", tags).fetchall()]
        else:
            tag_ids = []
        db.execute("DELETE FROM document_tags WHERE doc_id = ?", (doc_id,))
        if tag_ids:
            db.executemany("INSERT INTO document_tags (doc_id, tag_id) VALUES (?, ?)", [(doc_id, tag_id) for tag_id in tag_ids])
        db.commit()
        return jsonify({'success': True, 'tags': tags})
    except sqlite3.Error as e:
        db.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/document/<int:doc_id>/catalogs', methods=['POST'])
@login_required
def set_document_catalogs(doc_id):
    catalog_ids = request.json.get('catalog_ids', [])
    db = get_db()
    try:
        db.execute("BEGIN")
        db.execute("DELETE FROM document_catalogs WHERE doc_id = ?", (doc_id,))
        if catalog_ids:
            db.executemany("INSERT INTO document_catalogs (doc_id, catalog_id) VALUES (?, ?)", [(doc_id, cat_id) for cat_id in catalog_ids])
        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/tags/rename', methods=['PUT'])
@admin_required
def rename_tag():
    data = request.json
    old_name = data.get('old_name')
    new_name = data.get('new_name', '').strip().lower()
    if not old_name or not new_name:
        return jsonify({'success': False, 'message': 'Old and new tag names are required.'}), 400
    if old_name == new_name:
        return jsonify({'success': True, 'message': 'No changes made.'})
    db = get_db()
    try:
        db.execute("BEGIN")
        if db.execute("SELECT id FROM tags WHERE name = ?", (new_name,)).fetchone():
            db.rollback()
            return jsonify({'success': False, 'message': f"Tag '{new_name}' already exists."}), 409
        cursor = db.execute("UPDATE tags SET name = ? WHERE name = ?", (new_name, old_name))
        if cursor.rowcount > 0:
            db.commit()
            return jsonify({'success': True, 'message': 'Tag renamed successfully.'})
        else:
            db.rollback()
            return jsonify({'success': False, 'message': 'Original tag not found.'}), 404
    except sqlite3.Error as e:
        db.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/tags/delete', methods=['DELETE'])
@admin_required
def delete_tag():
    tag_name = request.json.get('name')
    if not tag_name:
        return jsonify({'success': False, 'message': 'Tag name is required.'}), 400
    db = get_db()
    try:
        db.execute("BEGIN")
        tag_row = db.execute("SELECT id FROM tags WHERE name = ?", (tag_name,)).fetchone()
        if not tag_row:
            db.rollback()
            return jsonify({'success': False, 'message': 'Tag not found.'}), 404
        db.execute("DELETE FROM tags WHERE id = ?", (tag_row['id'],))
        db.commit()
        return jsonify({'success': True, 'message': f"Tag '{tag_name}' deleted."})
    except sqlite3.Error as e:
        db.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
        
@app.route('/api/documents_by_tags')
@login_required
def get_documents_by_tags():
    db = get_db()
    user_id = g.user['id']
    tags = request.args.getlist('tag')
    color = request.args.get('color')
    catalog_id = request.args.get('catalog_id', type=int)
    if not tags and not color and not catalog_id:
        return jsonify([])
    params = [user_id]
    where_clauses = []
    base_query = """
        SELECT
            d.id, d.relative_path, d.color,
            (SELECT COUNT(*) FROM document_comments dc_count WHERE dc_count.doc_id = d.id) as comment_count,
            (SELECT 1 FROM document_curation d_cur WHERE d_cur.doc_id = d.id AND d_cur.user_id = ?) as has_personal_note
        FROM documents d
    """
    if color:
        where_clauses.append("d.color = ?")
        params.append(color)
    if catalog_id:
        catalog_subquery = "d.id IN (SELECT doc_id FROM document_catalogs WHERE catalog_id = ?)"
        where_clauses.append(catalog_subquery)
        params.append(catalog_id)
    if tags:
        placeholders = ','.join('?' for _ in tags)
        tag_subquery = f"""
            d.id IN (
                SELECT doc_id FROM document_tags
                JOIN tags ON tags.id = document_tags.tag_id
                WHERE tags.name IN ({placeholders})
                GROUP BY doc_id
                HAVING COUNT(DISTINCT tags.id) = ?
            )
        """
        where_clauses.append(tag_subquery)
        params.extend(tags)
        params.append(len(tags))
    if where_clauses:
        base_query += " WHERE " + " AND ".join(where_clauses)
    base_query += " ORDER BY d.relative_path COLLATE NOCASE"
    documents = db.execute(base_query, params).fetchall()
    return jsonify([dict(doc) for doc in documents])

# --- Main Application Runner ---
if __name__ == '__main__':
    multiprocessing.freeze_support()
    db_path = Path(DATABASE_FILE)
    if not db_path.exists():
        print("Database not found. Creating new one...")
        storage_setup.create_unified_index(db_path)
    else:
        print("--- Performing startup cleanup ---")
        try:
            conn = sqlite3.connect(db_path)
            count = conn.execute("SELECT COUNT(*) FROM documents WHERE status IS 'Queued'").fetchone()[0]
            if count > 0:
                print(f"Found {count} stale 'Queued' documents. Resetting them to 'New'...")
                conn.execute("UPDATE documents SET status = 'New', status_message='Reset on startup' WHERE status = 'Queued'")
                conn.commit()
                print("Cleanup complete.")
            else:
                print("No stale documents found. System state is clean.")
            conn.close()
        except Exception as e:
            print(f"!!! ERROR during startup cleanup: {e} !!!")

    manager = threading.Thread(target=manager_thread_loop, daemon=True)
    manager.start()

    print("--- Redleaf Engine Starting ---")
    print("Task Manager thread is running.")
    app.run(debug=False, host='0.0.0.0', port=5000, use_reloader=False)