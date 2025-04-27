# --- File: ./app.py ---
# --- File: ./app.py --- (Refactored - Caching Removed, TypeError Fix, max_tasks_per_child removed, Separate Indexing Limit, Multi-Type Support, Polling API Added)

import sqlite3
import os
import re
import urllib.parse
import subprocess # Still needed for single file process
import sys
import time
import gc # Garbage collector
from pathlib import Path
from flask import Flask, render_template, g, abort, send_from_directory, request, redirect, url_for, flash, jsonify # Added jsonify
from collections import defaultdict
from werkzeug.exceptions import NotFound
import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed # Using standard import
import traceback # For detailed error logging

# --- Configuration ---
DATABASE = './redleaf_data.db'
# --- CHANGED: Rename PDF_INPUT_DIR for clarity ---
INPUT_DIR = Path("./documents").resolve() # Now holds PDFs, HTML folders, TXT files
# --- CHANGED: Rename TEXT_OUTPUT_DIR for clarity ---
PDF_TEXT_OUTPUT_DIR = Path("./output_text_direct").resolve() # ONLY for text extracted from PDFs
INPUT_DIR.mkdir(exist_ok=True)
PDF_TEXT_OUTPUT_DIR.mkdir(exist_ok=True)
# --- Define supported source extensions ---
SUPPORTED_SOURCE_EXTENSIONS = {".pdf", ".html", ".txt"} # Add others if needed

ENTITY_LABELS_TO_DISPLAY = ['PERSON', 'GPE', 'LOC', 'ORG', 'DATE']
SECRET_KEY = os.urandom(24)
SNIPPET_CONTEXT_CHARS = 80
# Default/Max for general tasks (like text extract)
MAX_CONCURRENT_TASKS = os.cpu_count() or 2
# Separate, lower limit specifically for RAM-heavy indexing
MAX_INDEXING_WORKERS = 1 # <<<--- START CONSERVATIVELY (e.g., 1 or 2)

# --- Flask App Setup ---
app = Flask(__name__)
app.config.from_object(__name__)
# --- CHANGED: Update config keys ---
app.config['INPUT_DIR_DISPLAY'] = str(INPUT_DIR)
app.config['PDF_TEXT_OUTPUT_DIR_DISPLAY'] = str(PDF_TEXT_OUTPUT_DIR)
app.config['MAX_CONCURRENT_TASKS'] = MAX_CONCURRENT_TASKS
app.config['MAX_INDEXING_WORKERS'] = MAX_INDEXING_WORKERS # Store new config

# --- Database Handling ---
# (setup_database, get_db, close_connection, get_api_db - unchanged functionally,
# but text_filepath column in documents table now has dual meaning)
def setup_database():
    db_path = Path(app.config['DATABASE']).resolve()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")
    cursor.execute("PRAGMA journal_mode = WAL;") # Enable WAL mode
    # Create tables...
    # documents.text_filepath:
    #   - For PDF sources: Path to file in PDF_TEXT_OUTPUT_DIR
    #   - For HTML/TXT sources: Path to original file in INPUT_DIR
    cursor.execute("CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY AUTOINCREMENT, relative_path TEXT NOT NULL, page_number INTEGER NOT NULL, text_filepath TEXT NOT NULL, processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(relative_path, page_number));")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_doc_rel_path ON documents (relative_path);") # Index for faster lookup by path

    # ... (rest of setup_database is unchanged) ...
    cursor.execute("CREATE TABLE IF NOT EXISTS entities (id INTEGER PRIMARY KEY AUTOINCREMENT, entity_text TEXT NOT NULL, entity_label TEXT NOT NULL, UNIQUE(entity_text, entity_label));")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_entity_label ON entities (entity_label);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_entity_text ON entities (entity_text);") # Index for faster text lookup

    cursor.execute("CREATE TABLE IF NOT EXISTS document_entities (document_id INTEGER NOT NULL, entity_id INTEGER NOT NULL, FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE, FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE, PRIMARY KEY (document_id, entity_id));")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_doc_entities_doc ON document_entities (document_id);") # Added index
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_doc_entities_entity ON document_entities (entity_id);")

    # pdf_status now tracks PDFs, HTMLs, TXTs
    cursor.execute("CREATE TABLE IF NOT EXISTS pdf_status (relative_path TEXT PRIMARY KEY NOT NULL, status TEXT NOT NULL, last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP);")

    cursor.execute("CREATE TABLE IF NOT EXISTS catalogs (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, description TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);")

    cursor.execute("CREATE TABLE IF NOT EXISTS document_catalogs (doc_relative_path TEXT NOT NULL, catalog_id INTEGER NOT NULL, added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (catalog_id) REFERENCES catalogs(id) ON DELETE CASCADE, PRIMARY KEY (doc_relative_path, catalog_id));")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_doc_catalog_id ON document_catalogs (catalog_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_doc_catalog_path ON document_catalogs (doc_relative_path);") # Added index

    cursor.execute("CREATE TABLE IF NOT EXISTS favorites (doc_relative_path TEXT PRIMARY KEY NOT NULL, is_favorite INTEGER NOT NULL DEFAULT 1, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);")

    cursor.execute("CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY AUTOINCREMENT, doc_relative_path TEXT NOT NULL UNIQUE, note_content TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);") # Added UNIQUE constraint
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_note_doc_path ON notes (doc_relative_path);")

    conn.commit()
    conn.close()
    print("Database setup/check complete.")

def get_db():
    if 'db' not in g:
        db_path = Path(app.config['DATABASE']).resolve()
        if not db_path.exists(): setup_database()
        g.db = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES, timeout=20)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA journal_mode = WAL")
    return g.db

@app.teardown_appcontext
def close_connection(exception):
    db = g.pop('db', None)
    if db is not None: db.close()

def get_api_db():
    db_path = Path(app.config['DATABASE']).resolve()
    conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# (Template filter _jinja2_filter_datetime unchanged)
@app.template_filter('strftime')
def _jinja2_filter_datetime(date, fmt='%Y-%m-%d %H:%M:%S'):
    if not date: return "N/A"
    if isinstance(date, str):
        try: date = datetime.datetime.fromisoformat(date.replace('Z', '+00:00'))
        except ValueError:
             try: date = datetime.datetime.strptime(date, '%Y-%m-%d %H:%M:%S.%f')
             except ValueError:
                  try: date = datetime.datetime.strptime(date, '%Y-%m-%d %H:%M:%S')
                  except ValueError: return date # Return original string if all parsing fails
    if isinstance(date, datetime.datetime): return date.strftime(fmt)
    return date # Return original object if not string or datetime

# --- Background Task Helpers ---
# (_run_background_task unchanged)
def _run_background_task(task_name, relative_path_str):
    """Launches a task in a detached background process using run_task.py."""
    try:
        python_executable = sys.executable
        task_script_path = Path(__file__).parent / 'run_task.py'
        if not task_script_path.is_file():
            print(f"CRITICAL ERROR: Background task script '{task_script_path}' not found.")
            flash(f"CRITICAL ERROR: Background task script '{task_script_path.name}' not found.", "error")
            return False

        log_dir = Path("./logs").resolve()
        log_dir.mkdir(exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        # Sanitize relative path for use in filename
        safe_rel_path = re.sub(r'[\\/*?:"<>|]', '_', relative_path_str) # Basic unsafe chars
        safe_rel_path = re.sub(r'[^a-zA-Z0-9_\-. ]', '_', safe_rel_path) # Allow sensible chars
        log_filename = log_dir / f"task_{task_name}_{safe_rel_path[:100]}_{timestamp}.log" # Limit length

        cmd = [python_executable, str(task_script_path), task_name, relative_path_str]

        # Open log file with UTF-8 encoding
        log_file = open(log_filename, "w", encoding='utf-8')

        print(f"Running single task command: {' '.join(cmd)} (Output to {log_filename})")

        # Platform-specific process creation flags for detachment
        creationflags = 0
        start_new_session = False
        if sys.platform == "win32":
            # DETACHED_PROCESS: No console window.
            # CREATE_NEW_PROCESS_GROUP: Allows closing Flask without killing task (usually).
            creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else: # POSIX (Linux, macOS)
            start_new_session = True # Runs in a new session, detaching from Flask's session.

        # Launch the subprocess
        subprocess.Popen(cmd, stdout=log_file, stderr=log_file,
                         creationflags=creationflags, start_new_session=start_new_session,
                         close_fds=(sys.platform != "win32"), # Close inherited handles on POSIX
                         encoding='utf-8') # Specify encoding for stdout/stderr redirection

        # We don't close log_file here; the Popen object holds the handle,
        # and it will be closed when the subprocess exits.
        return True

    except Exception as e:
        print(f"Error starting single task subprocess for {relative_path_str} ({task_name}): {e}")
        traceback.print_exc()
        # Ensure log file is closed if Popen failed
        if 'log_file' in locals() and log_file:
            try: log_file.close()
            except Exception: pass
        return False

# (run_processing_task modified for new task structure)
def run_processing_task(task_name, relative_path_str):
    """Function executed by ProcessPoolExecutor workers for BULK tasks."""
    worker_pid = os.getpid()
    result_status = "Error (Unknown)"
    print(f"Worker (PID:{worker_pid}) starting task '{task_name}' for '{relative_path_str}'")
    try:
        # Import inside worker to ensure fresh state and avoid potential parent/child issues
        import processing_tasks
        file_extension = Path(relative_path_str).suffix.lower()

        if task_name == "extract_text":
            if file_extension == ".pdf":
                print(f"  Worker calling extract_text_for_pdf for {relative_path_str}")
                processing_tasks.extract_text_for_pdf(relative_path_str)
                # Status is updated within the called function
                result_status = "Delegate: extract_text_for_pdf" # Indicate what was called
            elif file_extension == ".html":
                print(f"  Worker calling extract_text_for_html for {relative_path_str}")
                processing_tasks.extract_text_for_html(relative_path_str)
                # Status is updated within the called function
                result_status = "Delegate: extract_text_for_html"
            else:
                 # This case should not be reached if app.py checks extensions
                 print(f"  Worker received 'extract_text' for unsupported type: {relative_path_str}")
                 result_status = "Error (Unsupported Type for Extract)"
                 # Optionally update status to error here if needed, though the called function should handle it
        elif task_name == "index_entities":
            # The index_entities_for_source function handles type internally
            print(f"  Worker calling index_entities_for_source for {relative_path_str}")
            processing_tasks.index_entities_for_source(relative_path_str)
            # Status is updated within the called function
            result_status = "Delegate: index_entities_for_source"
        else:
            print(f"Worker (PID:{worker_pid}) received unknown task: {task_name}")
            result_status = "Error (Unknown Task)"

        # The actual completion status (like 'Indexed', 'Text Extracted', 'Error:...')
        # is set within the processing_tasks functions. We retrieve it here if needed,
        # but the primary goal is just logging that the worker finished.
        # Retrieving final status might require another DB call here, or modifying processing_tasks
        # to return the final status string. For now, we rely on processing_tasks setting it.
        print(f"Worker (PID:{worker_pid}) finished task '{task_name}' for '{relative_path_str}' (Delegated Status Update)")

    except AttributeError as ae:
         # Catch if processing functions are missing (e.g., typo in function name)
         print(f"!!! Worker (PID:{worker_pid}) Error: Missing function in processing_tasks.py for '{relative_path_str}' ({task_name}): {ae}")
         traceback.print_exc()
         result_status = f"Error (Missing Task Function: {ae})"
         # Attempt to update DB status to error from worker if possible
         try:
            import processing_tasks # Re-import just for status update
            processing_tasks.update_pdf_status(relative_path_str, result_status)
         except Exception as update_e: print(f"  Worker failed to update status after AttributeError: {update_e}")
    except Exception as e:
        print(f"!!! Worker (PID:{worker_pid}) Error processing '{relative_path_str}' ({task_name}): {e}")
        traceback.print_exc()
        result_status = f"Error ({type(e).__name__})"
        # Attempt to update DB status to error from worker if possible
        try:
            import processing_tasks # Re-import just for status update
            processing_tasks.update_pdf_status(relative_path_str, result_status)
        except Exception as update_e: print(f"  Worker failed to update status after general Exception: {update_e}")
    finally:
        # Ensure garbage collection runs in the worker process
        gc.collect()
    # Return value indicates worker completion and the type of task delegated
    # It doesn't necessarily reflect the *final DB status* which is set async
    return (relative_path_str, result_status)


# (check_and_update_status needs minor change for new status)
def check_and_update_status(relative_path_str):
    """Checks DB status and potentially updates based on file system state or DB entries."""
    conn = None
    updated = False
    ext = Path(relative_path_str).suffix.lower()
    print(f"Checking status for: {relative_path_str}")
    try:
        conn = get_api_db()
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM pdf_status WHERE relative_path = ?", (relative_path_str,))
        result = cursor.fetchone()
        if not result:
            print(f"  Status Check: No status found for {relative_path_str}.")
            return False # No status to check/update

        current_status = result['status']
        print(f"  Current DB Status: '{current_status}'")

        target_status = None # Status to potentially update to

        # --- Check conditions based on current status ---
        if current_status == 'Text Extract In Progress':
            if ext == ".pdf":
                # Check for output text file in PDF_TEXT_OUTPUT_DIR
                txt_path = (PDF_TEXT_OUTPUT_DIR / Path(relative_path_str).with_suffix(".txt")).resolve()
                if txt_path.is_file() and txt_path.stat().st_size > 0:
                    target_status = 'Text Extracted'
                    print(f"  Condition Met: PDF text file found at {txt_path}")
                else:
                    print(f"  Condition Not Met: PDF text file missing or empty at {txt_path}")
            elif ext == ".html":
                 # Check for output text file next to source in INPUT_DIR
                 txt_path = (INPUT_DIR / Path(relative_path_str).with_suffix(".txt")).resolve()
                 if txt_path.is_file() and txt_path.stat().st_size > 0:
                     target_status = 'Text Ready' # The status after HTML extraction
                     print(f"  Condition Met: HTML text file found at {txt_path}")
                 else:
                    print(f"  Condition Not Met: HTML text file missing or empty at {txt_path}")
            # No action for .txt files in 'Text Extract In Progress' state (shouldn't happen)

        elif current_status == 'Indexing In Progress':
            # Check works for all types by looking for DB entries
            cursor.execute("SELECT 1 FROM documents WHERE relative_path = ? LIMIT 1", (relative_path_str,))
            if cursor.fetchone():
                target_status = 'Indexed'
                print(f"  Condition Met: Found indexed entries in 'documents' table.")
            else:
                print(f"  Condition Not Met: No indexed entries found in 'documents' table.")

        # --- Perform Update if target status determined ---
        if target_status:
             print(f"  Attempting DB Update: {relative_path_str} -> {target_status}")
             # Atomically update only if current status matches expected 'In Progress' state
             rows = cursor.execute("""
                 UPDATE pdf_status
                 SET status = ?, last_updated = CURRENT_TIMESTAMP
                 WHERE relative_path = ? AND status = ?
             """, (target_status, relative_path_str, current_status)).rowcount

             if rows > 0:
                 conn.commit()
                 updated = True
                 print(f"  Status Update Successful: {relative_path_str} -> {target_status}")
             else:
                 conn.rollback() # Ensure no partial transaction
                 print(f"  Status Update Failed: Status might have changed since check (expected '{current_status}').")
        else:
             print(f"  No status update needed based on checks.")

    except sqlite3.Error as e:
        print(f"Error in check_and_update_status DB operation for {relative_path_str}: {e}")
        if conn and conn.in_transaction:
             try: conn.rollback()
             except Exception as rb_e: print(f"  Rollback error: {rb_e}")
    except Exception as e:
        print(f"Unexpected error in check_and_update_status for {relative_path_str}: {e}")
        traceback.print_exc()
    finally:
        if conn: conn.close()
    print(f"Finished status check for {relative_path_str}. Updated: {updated}")
    return updated


# --- Standard Routes ---

# --- CHANGED: dashboard() function ---
@app.route('/')
@app.route('/dashboard')
def dashboard():
    db = get_db(); cursor = db.cursor(); all_files_status = []; dashboard_error = False
    try:
        # Fetch all statuses currently tracked in the database
        cursor.execute("SELECT relative_path, status, last_updated FROM pdf_status ORDER BY relative_path")
        status_from_db = {row['relative_path']: {'status': row['status'], 'last_updated': row['last_updated']} for row in cursor.fetchall()}

        # --- Scan filesystem for all supported source files ---
        found_source_files = set()
        input_dir_resolved = INPUT_DIR.resolve()
        pdf_text_output_dir_resolved = PDF_TEXT_OUTPUT_DIR.resolve() # Needed for filtering .txt

        try:
            for ext in SUPPORTED_SOURCE_EXTENSIONS:
                # Use rglob to find files recursively
                for p in input_dir_resolved.rglob(f"*{ext}"):
                    if p.is_file():
                        # Special check for .txt: IGNORE those inside the PDF_TEXT_OUTPUT_DIR
                        if ext == ".txt":
                            # Check if the resolved path of the .txt file is *within* the PDF text output dir
                            try: # Use is_relative_to for robust check (requires Python 3.9+)
                                if p.resolve().is_relative_to(pdf_text_output_dir_resolved):
                                    # print(f"  Skipping scan of TXT file inside PDF output dir: {p}")
                                    continue # Skip this file, it's an output, not a source
                            except AttributeError: # Fallback for older Python versions
                                if str(p.resolve()).startswith(str(pdf_text_output_dir_resolved)):
                                     continue
                        # Add the file relative to the INPUT_DIR using as_posix() for consistency
                        found_source_files.add(p.relative_to(input_dir_resolved).as_posix())
        except FileNotFoundError:
            flash(f"Error: Input directory '{INPUT_DIR}' not found during scan.", "error"); dashboard_error = True
        except Exception as scan_e:
            flash(f"Error scanning input directory '{INPUT_DIR}': {scan_e}", "error"); dashboard_error = True
            traceback.print_exc()
        # --- End Filesystem Scan ---

        db_paths = set(status_from_db.keys()) # Paths currently tracked in DB

        # Determine differences
        paths_to_add = found_source_files - db_paths # Files found on disk but not in DB status table
        paths_to_remove_status = db_paths - found_source_files # Files in DB status table but not found on disk
        paths_to_check = db_paths.intersection(found_source_files) # Files found both places (common case)

        all_files_status = [] # Final list to be passed to the template

        # --- 1. Check existing tracked files still present on disk ---
        for rel_path_str in paths_to_check:
            data = status_from_db[rel_path_str].copy() # Work with a copy
            data['exists'] = True
            data['relative_path'] = rel_path_str
            ext = Path(rel_path_str).suffix.lower()

            # If a file was marked 'Missing' but has reappeared on disk
            if data['status'].startswith('Missing'):
                print(f"File reappeared: {rel_path_str}. Determining new status.")
                # Determine correct initial status based on type and existing text files
                new_status = 'Unknown'
                if ext == ".pdf":
                    txt_path = PDF_TEXT_OUTPUT_DIR / Path(rel_path_str).with_suffix(".txt")
                    new_status = 'Text Extracted' if txt_path.is_file() and txt_path.stat().st_size > 0 else 'New'
                elif ext == ".html":
                     # Reappeared HTML needs parsing again, even if .txt exists (source might have changed)
                     new_status = 'New'
                elif ext == ".txt":
                     # Reappeared TXT is ready to be indexed directly
                     new_status = 'Text Ready'
                # else: new_status remains 'Unsupported Type' or 'Unknown'

                if new_status not in ['Unknown', 'Unsupported Type']:
                    print(f"  Resetting status for reappeared file {rel_path_str} to '{new_status}'.")
                    try:
                        # Update DB status from 'Missing...' to the determined status
                        cursor.execute("UPDATE pdf_status SET status = ?, last_updated=CURRENT_TIMESTAMP WHERE relative_path = ?", (new_status, rel_path_str))
                        db.commit()
                        data['status'] = new_status # Update the dictionary being added to the list
                        data['last_updated'] = datetime.datetime.now() # Reflect update time
                    except sqlite3.Error as e:
                        print(f"  DB Error updating status for reappeared file {rel_path_str}: {e}"); db.rollback()
                        data['status'] = 'DB Error (Update)' # Reflect DB error
                else:
                     # If type is unsupported or something went wrong, might leave as Missing or set specific error
                     print(f"  Reappeared file {rel_path_str} is unsupported or status unclear. Keeping previous status.")
                     pass # Keep the 'Missing...' status for now if type is not handled

            all_files_status.append(data)

        # --- 2. Handle files that were in the DB but are now missing from disk ---
        for rel_path_str in paths_to_remove_status:
            data = status_from_db[rel_path_str].copy()
            data['exists'] = False
            data['relative_path'] = rel_path_str

            # Only update status if it wasn't already 'Missing' or an irrecoverable 'Error'
            if not data['status'].startswith('Missing') and not data['status'].startswith('Error: File Not Found'):
                new_missing_status = 'Missing (Removed from Folder)'
                print(f"File missing from disk: {rel_path_str}. Setting status to '{new_missing_status}'.")
                try:
                    cursor.execute("UPDATE pdf_status SET status = ?, last_updated=CURRENT_TIMESTAMP WHERE relative_path = ?", (new_missing_status, rel_path_str))
                    db.commit()
                    data['status'] = new_missing_status # Update the dictionary
                    data['last_updated'] = datetime.datetime.now()
                except sqlite3.Error as e:
                    print(f"  DB Error updating status for missing file {rel_path_str}: {e}"); db.rollback()
                    data['status'] = 'DB Error (Update)' # Reflect DB error
            # else: Keep existing 'Missing' or 'Error' status

            all_files_status.append(data)

        # --- 3. Add newly found files to the DB and the list ---
        for rel_path_str in paths_to_add:
             initial_status = 'Unknown'
             file_path = INPUT_DIR / rel_path_str
             ext = Path(rel_path_str).suffix.lower()

             # Double check it exists and is a file (should be true from scan)
             if not file_path.is_file():
                 print(f"Warning: File {rel_path_str} found during scan but now missing?")
                 continue

             # Determine initial status based on type and existence of corresponding text files
             if ext == ".pdf":
                 # Check for pre-existing text output file
                 txt_path = PDF_TEXT_OUTPUT_DIR / Path(rel_path_str).with_suffix(".txt")
                 initial_status = 'Text Extracted' if txt_path.is_file() and txt_path.stat().st_size > 0 else 'New'
             elif ext == ".html":
                 # HTML always starts as 'New' as it needs parsing, even if a .txt file exists nearby
                 initial_status = 'New'
             elif ext == ".txt":
                 # TXT files (already filtered from PDF output dir) are immediately ready for indexing
                 initial_status = 'Text Ready'
             else:
                 # File extension is not in SUPPORTED_SOURCE_EXTENSIONS
                 initial_status = 'Unsupported Type'

             # Add to DB status table only if it's a supported type we can process
             if initial_status not in ['Unknown', 'Unsupported Type']:
                 try:
                     cursor.execute("INSERT INTO pdf_status (relative_path, status, last_updated) VALUES (?, ?, CURRENT_TIMESTAMP)", (rel_path_str, initial_status))
                     db.commit()
                     print(f"New file found: {rel_path_str}. Added to DB with status '{initial_status}'.")
                     # Add to the list being sent to the template
                     all_files_status.append({
                         'relative_path': rel_path_str,
                         'status': initial_status,
                         'last_updated': datetime.datetime.now(),
                         'exists': True
                     })
                 except sqlite3.Error as e:
                     # Handle potential race condition or other DB error
                     print(f"DB error inserting new file status for {rel_path_str}: {e}"); db.rollback()
                     # Add to list with error status
                     all_files_status.append({
                         'relative_path': rel_path_str,
                         'status': 'DB Error (Insert)',
                         'last_updated': datetime.datetime.now(),
                         'exists': True
                     })
             else:
                 # Log unsupported types but don't add to DB or list for processing
                 print(f"Ignoring newly found file {rel_path_str} with status '{initial_status}'")


        # Sort the final list by relative path for consistent display
        all_files_status.sort(key=lambda x: x['relative_path'])

    except sqlite3.Error as e:
        print(f"Database error loading dashboard: {e}"); traceback.print_exc()
        flash(f"Database error loading dashboard: {e}", "error"); dashboard_error = True
    except Exception as e:
        print(f"Unexpected error loading dashboard: {e}"); traceback.print_exc()
        flash("An unexpected error occurred loading the dashboard.", "error"); dashboard_error = True

    # Pass the combined & sorted list to the template
    return render_template('dashboard.html', files=all_files_status, error=dashboard_error)


# --- CHANGED: process_file() function ---
@app.route('/process/<task_type>/<path:relative_path_str>')
def process_file(task_type, relative_path_str):
    db = get_db(); cursor = db.cursor()
    ext = Path(relative_path_str).suffix.lower()

    # --- Input validation ---
    if task_type not in ["text", "index"]:
        flash(f"Invalid task type: {task_type}", "error"); return redirect(url_for('dashboard'))

    # Task 'extract_text' only valid for PDF or HTML
    if task_type == "text" and ext not in [".pdf", ".html"]:
         flash(f"Task 'extract_text' is only valid for .pdf or .html files, not {ext}", "error")
         return redirect(url_for('dashboard'))

    # Task 'index' valid for all supported source types
    if task_type == "index" and ext not in SUPPORTED_SOURCE_EXTENSIONS:
         flash(f"Task 'index' is not valid for file type {ext}", "error")
         return redirect(url_for('dashboard'))

    # Verify the source file actually exists in INPUT_DIR
    file_path = INPUT_DIR / relative_path_str
    if not file_path.is_file():
         flash(f"Source file not found: {relative_path_str}", "error")
         # Attempt to update status to reflect file missing at trigger time
         try:
             # Use INSERT OR REPLACE for simplicity, will update timestamp if exists
             cursor.execute("INSERT OR REPLACE INTO pdf_status (relative_path, status, last_updated) VALUES (?, ?, CURRENT_TIMESTAMP)",
                            (relative_path_str, f'Error: File Not Found ({task_type.capitalize()} Trigger)'))
             db.commit()
         except sqlite3.Error as e: print(f"DB Error updating status for missing file {relative_path_str}: {e}"); db.rollback()
         return redirect(url_for('dashboard'))

    # --- Determine task details ---
    task_to_run = None
    new_status_in_progress = None
    required_current_status_for_start = [] # Statuses required to START the task (can be flexible)

    if task_type == "text": # Only for PDF or HTML
        task_to_run = "extract_text"
        new_status_in_progress = 'Text Extract In Progress'
        # Allow starting if 'New', or re-running if already processed or errored
        required_current_status_for_start = ['New', 'Text Extracted', 'Text Ready', 'Indexed', 'Error: Text Extract Failed', 'Error: PDF Not Found', 'Error: HTML Not Found'] # Adjust as needed
    elif task_type == "index": # For PDF, HTML, TXT
        task_to_run = "index_entities" # Use the unified task name for run_task.py / processing_tasks.py
        new_status_in_progress = 'Indexing In Progress'
        # Require text to be ready, allow re-running if indexed or errored during indexing
        required_current_status_for_start = ['Text Extracted', 'Text Ready', 'Indexed', 'Error: Indexing Failed', 'Error: SpaCy Model Load Failed', 'Error: Failed to Delete Old Entries'] # Adjust as needed

    if task_to_run is None: # Should not happen due to earlier checks
         flash("Internal error determining task to run.", "error"); return redirect(url_for('dashboard'))

    # --- Atomically update status and start task ---
    try:
        # Ensure the row exists (in case dashboard missed it or race condition)
        cursor.execute("INSERT OR IGNORE INTO pdf_status (relative_path, status, last_updated) VALUES (?, 'Unknown', CURRENT_TIMESTAMP)", (relative_path_str,))
        db.commit() # Commit the potential insert

        # Check current status *before* attempting update
        cursor.execute("SELECT status FROM pdf_status WHERE relative_path = ?", (relative_path_str,))
        current_status_row = cursor.fetchone()
        current_status = current_status_row['status'] if current_status_row else 'Unknown' # Should exist now

        # Optional: Check if current status allows starting (more restrictive)
        # if current_status not in required_current_status_for_start and not current_status.startswith('Error'):
        #    flash(f"Cannot start task '{task_to_run}' for {relative_path_str}. Current status is '{current_status}'. Required: {required_current_status_for_start}", "warning")
        #    return redirect(url_for('dashboard'))
        # Example: Allow starting even if current status is unexpected, assuming user wants to force it

        # Attempt to update status to 'In Progress' ATOMICALLY
        # We don't need to check the status again here, just update it.
        # The background task's *final* update will handle success/error states.
        rows_affected = cursor.execute("UPDATE pdf_status SET status = ?, last_updated = CURRENT_TIMESTAMP WHERE relative_path = ?",
                                       (new_status_in_progress, relative_path_str)).rowcount
        db.commit()

        if rows_affected == 0:
            # This could happen if the row was deleted between INSERT/SELECT and UPDATE (very unlikely)
            flash(f"Failed to lock status to '{new_status_in_progress}' for {relative_path_str}. Task not started.", "error");
            return redirect(url_for('dashboard'))

    except sqlite3.Error as e:
        flash(f"DB error preparing task for {relative_path_str}. Task not started: {e}", "error");
        if db.in_transaction: db.rollback()
        return redirect(url_for('dashboard'))

    # --- Launch background task ---
    if _run_background_task(task_to_run, relative_path_str):
        flash(f"Started background task '{task_to_run}' for {relative_path_str}. Check dashboard/logs for progress.", "info")
    else:
        # If starting the subprocess failed, try to revert status back from 'In Progress'
        flash(f"Error starting background task '{task_to_run}' for {relative_path_str}. Check Flask console & logs.", "error")
        try:
            # Revert status to indicate failure to start
            error_status = f"Error: Failed to start {task_to_run} task"
            cursor.execute("UPDATE pdf_status SET status = ? WHERE relative_path = ? AND status = ?",
                           (error_status, relative_path_str, new_status_in_progress))
            db.commit()
        except sqlite3.Error as e:
            print(f"DB Error reverting status after task start failure for {relative_path_str}: {e}")
            if db.in_transaction: db.rollback()

    return redirect(url_for('dashboard'))


# --- CHANGED: process_bulk() function ---
@app.route('/process/bulk/<bulk_action_type>')
def process_bulk(bulk_action_type):
    """Triggers bulk processing using ProcessPoolExecutor with adjusted limits."""
    db = get_db(); cursor = db.cursor()
    tasks_submitted = 0; tasks_completed_ok = 0; tasks_failed_err = 0
    skipped_missing = 0; skipped_status_changed = 0; error_db_update = 0
    target_status_list = [] # Statuses eligible for this bulk action
    task_to_run = None
    next_status = None # Status to set files to while processing
    action_name = "Unknown Bulk Action"

    # Define actions, target statuses, task, and next status
    if bulk_action_type == 'extract_new':
        target_status_list = ['New'] # Only files marked as New
        task_to_run = 'extract_text' # Task for PDF/HTML extraction
        next_status = 'Text Extract In Progress'
        action_name = "Bulk Extract Text for New PDFs/HTMLs"
    elif bulk_action_type == 'index_ready':
        # Select files whose text is ready for indexing (PDFs, HTMLs, TXTs)
        target_status_list = ['Text Extracted', 'Text Ready']
        task_to_run = 'index_entities' # Unified indexing task
        next_status = 'Indexing In Progress'
        action_name = "Bulk Index Entities for Ready Files"
    else:
        flash(f"Invalid bulk action type: {bulk_action_type}", "error")
        return redirect(url_for('dashboard'))

    # Build query to find eligible files
    placeholders = ','.join('?' * len(target_status_list))
    sql_query = f"SELECT relative_path FROM pdf_status WHERE status IN ({placeholders})"

    try:
        cursor.execute(sql_query, target_status_list)
        paths_to_process = [row['relative_path'] for row in cursor.fetchall()]
    except sqlite3.Error as e:
        flash(f"Database error finding files for bulk action: {e}", "error")
        return redirect(url_for('dashboard'))

    total_files_eligible = len(paths_to_process)
    if not paths_to_process:
        flash(f"No files found with status(es) '{', '.join(target_status_list)}' for bulk action '{action_name}'.", "info")
        return redirect(url_for('dashboard'))

    # ---- Determine worker limit for this specific run ----
    if task_to_run == 'index_entities':
        max_workers_for_this_run = app.config['MAX_INDEXING_WORKERS']
        print(f"Starting bulk action '{action_name}' for {total_files_eligible} eligible files (INDEXING LIMIT: {max_workers_for_this_run} workers)...")
    else: # extract_text
        max_workers_for_this_run = app.config['MAX_CONCURRENT_TASKS']
        print(f"Starting bulk action '{action_name}' for {total_files_eligible} eligible files (General Limit: {max_workers_for_this_run} workers)...")

    futures = {} # Dictionary to map Future objects back to relative paths
    try:
        print(f"--- DEBUG: Attempting to create ProcessPoolExecutor with max_workers={max_workers_for_this_run} ---")
        # Context manager ensures pool is shut down properly
        with ProcessPoolExecutor(max_workers=max_workers_for_this_run) as executor:
            print(f"--- DEBUG: ProcessPoolExecutor created successfully ---")
            print("--- Submitting tasks ---")
            for rel_path_str in paths_to_process:
                # 1. Check file existence *before* updating status or submitting
                if not (INPUT_DIR / rel_path_str).is_file():
                    print(f"Skipping missing source file: {rel_path_str}")
                    skipped_missing += 1
                    # Optionally update status to missing here if desired
                    # try:
                    #    cursor.execute("UPDATE pdf_status SET status='Missing (Bulk Trigger)' WHERE relative_path=?", (rel_path_str,))
                    #    db.commit()
                    # except: db.rollback()
                    continue # Skip to next file

                # 2. Atomically update status, checking against the target list
                try:
                    # This query updates status ONLY IF it's still one of the target statuses
                    update_sql = f"UPDATE pdf_status SET status = ?, last_updated = CURRENT_TIMESTAMP WHERE relative_path = ? AND status IN ({placeholders})"
                    rows_updated = cursor.execute(update_sql, [next_status, rel_path_str] + target_status_list).rowcount
                    db.commit() # Commit the update attempt

                    if rows_updated == 0:
                        # If 0 rows updated, the status must have changed since the initial query
                        print(f"Skipping task for {rel_path_str}: Status no longer in '{', '.join(target_status_list)}' or DB update failed.")
                        skipped_status_changed += 1
                        continue # Skip submitting task
                except sqlite3.Error as e:
                    print(f"DB Error locking status for {rel_path_str}: {e}")
                    if db.in_transaction: db.rollback()
                    error_db_update += 1
                    continue # Skip submitting task if DB error occurred

                # 3. Submit task to the executor if status update was successful
                try:
                    # run_processing_task handles the actual work and final status update
                    future = executor.submit(run_processing_task, task_to_run, rel_path_str)
                    futures[future] = rel_path_str # Map future to path for result handling
                    tasks_submitted += 1
                    # print(f"  Submitted task: {task_to_run} for {rel_path_str}") # Verbose logging if needed
                except Exception as submit_e: # Errors from the executor itself (rare)
                    print(f"Error submitting task {rel_path_str} to executor: {submit_e}")
                    tasks_failed_err += 1
                    # Try to revert status back from 'In Progress' if submission failed
                    try:
                        error_status = f"Error: Failed to submit {task_to_run} to pool"
                        cursor.execute("UPDATE pdf_status SET status = ? WHERE relative_path = ? AND status = ?",
                                       (error_status, rel_path_str, next_status))
                        db.commit()
                    except Exception as db_e:
                        print(f"DB Error reverting status after submit failure for {rel_path_str}: {db_e}")
                        if db.in_transaction: db.rollback()

            # --- Waiting for tasks to complete ---
            print(f"--- Waiting for {tasks_submitted} submitted tasks to complete ---")
            # Process results as they become available
            for future in as_completed(futures):
                rel_path = futures[future] # Get path associated with this future
                try:
                    # Get the result tuple (path, delegate_status) from the worker function
                    _, delegate_status = future.result()
                    # Note: The worker function (`run_processing_task`) is responsible
                    # for calling the actual processing task which updates the DB status
                    # to the final state ('Indexed', 'Text Extracted', 'Error: ...', etc.).
                    # We mainly check here if the worker itself reported an error or died.
                    if "Error" in delegate_status:
                        tasks_failed_err += 1
                        # Log that the worker reported an error, but DB status should already be set by the worker
                        print(f"Worker reported error for {rel_path}: {delegate_status}")
                    else:
                        tasks_completed_ok += 1
                        # Optionally log worker completion: print(f"Worker finished successfully for {rel_path}")
                        # We *could* call check_and_update_status here, but it might be redundant
                        # if the worker's final update was successful. Rely on worker for now.

                except Exception as e: # Catches errors *retrieving* the result (e.g., worker process died unexpectedly)
                     print(f"!!! Error retrieving result for task {rel_path}: {e}"); traceback.print_exc();
                     tasks_failed_err += 1
                     # If the worker died, its final status update likely never happened.
                     # Update status to reflect this specific error.
                     try:
                        error_status = "Error: Worker Died Unexpectedly"
                        # Update status only if it's still showing 'In Progress'
                        rows_updated = cursor.execute("UPDATE pdf_status SET status=? , last_updated = CURRENT_TIMESTAMP WHERE relative_path=? AND status LIKE '%In Progress'",
                                                      (error_status, rel_path)).rowcount
                        if rows_updated > 0:
                            db.commit()
                            print(f"  Updated status to '{error_status}' for {rel_path}")
                        else:
                            # Status was likely already updated or changed, no need to rollback
                            print(f"  Status for {rel_path} was not 'In Progress' when worker death detected.")
                            pass
                     except Exception as db_e:
                         print(f"DB Error marking status after worker death for {rel_path}: {db_e}")
                         if db.in_transaction: db.rollback()

    except Exception as ex: # Catch errors during ProcessPoolExecutor setup or management
        print(f"!!! General Error during Bulk Processing setup/management: {ex}"); traceback.print_exc()
        flash("An unexpected error occurred during bulk processing initialization or management. Check logs.", "error")
        return redirect(url_for('dashboard'))

    # --- Summarize results ---
    summary = f"Bulk action '{action_name}' finished. Submitted {tasks_submitted}/{total_files_eligible} eligible tasks."
    details = []
    # Note: tasks_completed_ok only counts workers that finished without error *reporting*.
    # The actual success/failure is reflected in the DB status set by the worker.
    if tasks_completed_ok > 0: details.append(f"{tasks_completed_ok} workers completed reporting")
    if tasks_failed_err > 0: details.append(f"{tasks_failed_err} workers failed/errored")
    if skipped_missing > 0: details.append(f"skipped {skipped_missing} missing files")
    if skipped_status_changed > 0: details.append(f"skipped {skipped_status_changed} due to status change")
    if error_db_update > 0: details.append(f"{error_db_update} DB errors during pre-submit")

    if details: summary += f" ({', '.join(details)})."
    elif tasks_submitted == 0: summary += " No tasks were submitted."

    flash_category = "info"
    if tasks_failed_err > 0 or skipped_missing > 0 or skipped_status_changed > 0 or error_db_update > 0:
        flash_category = "warning" # If any issues occurred
    elif tasks_submitted > 0: # If tasks were submitted and no errors reported by pool manager
        flash_category = "success" # Indicates pool ran, check dashboard for final results

    flash(summary + " Check dashboard for final status updates (refresh may be needed).", flash_category)
    return redirect(url_for('dashboard'))


# (check_single_status unchanged)
@app.route('/check_status/<path:relative_path_str>')
def check_single_status(relative_path_str):
    """Manually triggers a status check for a single file."""
    # Call the checking function which attempts to update the status in the DB
    # based on filesystem or DB state for 'In Progress' items.
    if check_and_update_status(relative_path_str):
        flash(f"Status for {relative_path_str} checked and updated based on results.", "success")
    else:
        flash(f"Status for {relative_path_str} checked. No update needed or possible (already completed/errored, or task not finished).", "info")
    return redirect(url_for('dashboard'))

# (check_bulk_status unchanged)
@app.route('/check_status/bulk')
def check_bulk_status():
    """Checks all files 'In Progress' and updates status if completed."""
    db = get_db(); cursor = db.cursor(); updated_count = 0; checked_count = 0; error_count = 0
    try:
        # Find all files currently marked as being processed
        cursor.execute("SELECT relative_path FROM pdf_status WHERE status LIKE '%In Progress'")
        paths_to_check = [row['relative_path'] for row in cursor.fetchall()]
    except sqlite3.Error as e:
        flash(f"DB error finding files to check: {e}", "error")
        return redirect(url_for('dashboard'))

    checked_count = len(paths_to_check)
    if not paths_to_check:
        flash("No files currently 'In Progress' found to check.", "info")
        return redirect(url_for('dashboard'))

    print(f"Starting Bulk Status Check for {checked_count} 'In Progress' files...")
    # Iterate and call the check function for each
    for rel_path in paths_to_check:
        try:
            if check_and_update_status(rel_path):
                updated_count += 1
        except Exception as e:
            # Catch errors during the check itself (e.g., DB connection issue during the check)
            error_count += 1
            print(f"Error checking status for {rel_path}: {e}")
            traceback.print_exc()

    # --- Summarize Results ---
    summary = f"Bulk Status Check: Checked {checked_count} 'In Progress' file(s)."
    details = []
    if updated_count > 0: details.append(f"updated {updated_count} to completed status")
    if error_count > 0: details.append(f"errors during check for {error_count}")

    if details: summary += f" ({', '.join(details)})."
    elif checked_count > 0: summary += " No completed tasks found needing status update."

    flash_category = "info"
    if error_count > 0: flash_category = "warning"
    elif updated_count > 0: flash_category = "success"

    flash(summary, flash_category)
    return redirect(url_for('dashboard'))

# --- CHANGED: reset_all_data function ---
@app.route('/reset_all', methods=['POST'])
def reset_all_data():
    """Deletes all indexed data and resets statuses based on current files."""
    print("--- Starting Reset All Data ---")
    conn = None
    try:
        conn = get_api_db() # Use separate connection for safety
        cursor = conn.cursor()
        cursor.execute("BEGIN") # Start transaction

        # --- Delete Indexed Data ---
        print("Deleting indexed data (document_entities, documents, entities)...")
        cursor.execute("DELETE FROM document_entities"); dc = cursor.rowcount
        cursor.execute("DELETE FROM documents"); dd = cursor.rowcount
        # Note: Deleting from 'entities' might be too aggressive if you want to preserve the entity list
        # across resets. Consider if this is desired. If not, comment out the next line.
        cursor.execute("DELETE FROM entities"); de = cursor.rowcount
        print(f"  Deleted {dc} entity links, {dd} document pages/chunks, {de} unique entities.")

        # --- Delete Auxiliary Data ---
        print("Deleting auxiliary data (document_catalogs, favorites, notes)...")
        cursor.execute("DELETE FROM document_catalogs"); dcat = cursor.rowcount
        cursor.execute("DELETE FROM favorites"); dfav = cursor.rowcount
        cursor.execute("DELETE FROM notes"); dnot = cursor.rowcount
        print(f"  Deleted {dcat} catalog links, {dfav} favorites, {dnot} notes.")

        # --- Rescan filesystem for current source files ---
        print(f"Scanning for current source files in {INPUT_DIR}...")
        current_source_files = set()
        input_dir_resolved = INPUT_DIR.resolve()
        pdf_text_output_dir_resolved = PDF_TEXT_OUTPUT_DIR.resolve()
        try:
            for ext in SUPPORTED_SOURCE_EXTENSIONS:
                 for p in input_dir_resolved.rglob(f"*{ext}"):
                     if p.is_file():
                         # Filter out .txt files from PDF output dir
                         if ext == ".txt":
                            try:
                                if p.resolve().is_relative_to(pdf_text_output_dir_resolved): continue
                            except AttributeError: # Fallback for < Py 3.9
                                if str(p.resolve()).startswith(str(pdf_text_output_dir_resolved)): continue
                         current_source_files.add(p.relative_to(input_dir_resolved).as_posix())
        except Exception as scan_e:
             print(f"  Error during filesystem scan in reset: {scan_e}")
             raise # Re-raise to abort reset if scan fails

        print(f"Found {len(current_source_files)} source files in directory.")
        # --- End Rescan ---

        # --- Reset Status Table ---
        # Option 1: Delete all rows and re-insert (simpler)
        print("Deleting all existing entries from pdf_status table...")
        cursor.execute("DELETE FROM pdf_status"); del_stat = cursor.rowcount
        print(f"  Deleted {del_stat} status entries.")

        # Option 2: More complex diff (like dashboard, but maybe overkill for reset)
        # cursor.execute("SELECT relative_path FROM pdf_status"); paths_in_status_table = set(row['relative_path'] for row in cursor.fetchall())
        # print(f"Found {len(paths_in_status_table)} entries currently in pdf_status table.")
        # paths_to_remove_status = paths_in_status_table - current_source_files
        # ... (delete logic) ...

        # --- Insert status for currently found files ---
        print(f"Inserting status for {len(current_source_files)} current source files...")
        statuses_to_set = []
        for rel_path_str in current_source_files:
            ext = Path(rel_path_str).suffix.lower()
            initial_status = 'Unknown'
            if ext == ".pdf":
                 # Check if text file already exists from previous runs
                 txt_path = PDF_TEXT_OUTPUT_DIR / Path(rel_path_str).with_suffix(".txt")
                 initial_status = 'Text Extracted' if txt_path.is_file() and txt_path.stat().st_size > 0 else 'New'
            elif ext == ".html":
                 initial_status = 'New' # Always needs parsing on reset
            elif ext == ".txt":
                 initial_status = 'Text Ready' # Ready for indexing

            if initial_status not in ['Unknown', 'Unsupported Type']: # Should not happen with current logic
                 statuses_to_set.append((rel_path_str, initial_status))
            else:
                 print(f"  Warning: Unsupported file type encountered during reset scan: {rel_path_str}")


        # Use executemany for efficient bulk insert
        if statuses_to_set:
            cursor.executemany("INSERT INTO pdf_status (relative_path, status, last_updated) VALUES (?, ?, CURRENT_TIMESTAMP)", statuses_to_set)
            print(f"  Inserted status for {cursor.rowcount} current source files.")
        else:
            print("  No supported source files found to insert status for.")

        # --- Commit the transaction ---
        conn.commit()
        flash("System reset successfully. All indexed data, links, notes, favorites deleted. Statuses refreshed based on current files.", "success")
        print("--- Reset All Data Finished Successfully ---")

    except Exception as e:
        # Rollback transaction if any error occurred
        if conn and conn.in_transaction:
            print("Rolling back transaction due to error.")
            conn.rollback()
        flash(f"Error during reset: {e}. Database might be inconsistent. Check logs.", "error")
        print(f"!!! Error during Reset All: {e} !!!"); traceback.print_exc()
    finally:
        # Ensure connection is closed
        if conn: conn.close()

    return redirect(url_for('dashboard'))


# --- Browse & Search Routes ---
# (browse_index unchanged)
@app.route('/browse')
def browse_index():
    print("--- Generating browse index via LIVE database query ---")
    db = get_db(); cursor = db.cursor(); start_time = time.time()
    entities_by_label = defaultdict(list); query_error = False
    try:
        if not ENTITY_LABELS_TO_DISPLAY:
            flash("No entity labels configured for display (ENTITY_LABELS_TO_DISPLAY is empty).", "warning")
            return render_template('index.html', entities_by_label={}, sorted_labels=[], display_labels=[], error=False)

        placeholders = ','.join('?' * len(ENTITY_LABELS_TO_DISPLAY))
        # Query joins entities, links, and documents to count distinct source files per entity
        query = f"""
            SELECT e.entity_text, e.entity_label, COUNT(DISTINCT d.relative_path) as appearance_count
            FROM entities e
            JOIN document_entities de ON e.id = de.entity_id
            JOIN documents d ON de.document_id = d.id
            WHERE e.entity_label IN ({placeholders})
            GROUP BY e.entity_text, e.entity_label
            ORDER BY e.entity_label, appearance_count DESC, e.entity_text COLLATE NOCASE;
        """
        cursor.execute(query, ENTITY_LABELS_TO_DISPLAY)
        all_entities = cursor.fetchall() # Fetch all results
        query_time = time.time() - start_time
        print(f"  Fetched {len(all_entities)} entity groups in {query_time:.3f}s.")

        # Process results for display
        proc_start = time.time()
        for entity in all_entities:
             try:
                 # URL-encode the entity text for use in links
                 # Using 'safe=""' ensures even slashes are encoded if present
                 encoded_text = urllib.parse.quote(entity['entity_text'], safe='', errors='replace')
                 entities_by_label[entity['entity_label']].append({
                    'text': entity['entity_text'],
                    'encoded_text': encoded_text,
                    'count': entity['appearance_count']
                 })
             except Exception as enc_e:
                 # Log if encoding fails for some reason
                 print(f"  Warning: Skipping entity during browse due to encoding error: {entity['entity_text']} ({enc_e})")

        proc_time = time.time() - proc_start
        print(f"  Processed entities for display in {proc_time:.3f}s.")

    except sqlite3.Error as e:
        print(f"Error querying entities for browse: {e}"); traceback.print_exc()
        flash(f"Database error loading browse index: {e}.", "error"); query_error = True
    except Exception as e:
        print(f"Unexpected error generating browse index: {e}"); traceback.print_exc()
        flash(f"An unexpected error occurred generating the browse index: {e}", "error"); query_error = True

    # Ensure labels are sorted according to the order defined in ENTITY_LABELS_TO_DISPLAY
    sorted_labels = sorted(
        # Get labels that actually have entities AND are in the display list
        [label for label in entities_by_label if label in ENTITY_LABELS_TO_DISPLAY],
        # Sort them based on their index in the original display list
        key=lambda x: ENTITY_LABELS_TO_DISPLAY.index(x)
    )

    total_time = time.time() - start_time
    print(f"--- Finished generating browse index from LIVE query in {total_time:.3f}s ---")
    # Pass the structured data and sorted labels to the template
    return render_template('index.html', entities_by_label=entities_by_label, sorted_labels=sorted_labels, display_labels=ENTITY_LABELS_TO_DISPLAY, error=query_error)


# --- CHANGED: entity_detail() function ---
@app.route('/entity/<label>/<path:encoded_text>')
def entity_detail(label, encoded_text):
    try:
        # Decode the entity text from the URL path segment
        entity_text = urllib.parse.unquote(encoded_text)
    except Exception as decode_err:
        abort(400, description=f"Invalid entity encoding in URL: {decode_err}")

    # Validate the requested label against allowed labels
    if label not in ENTITY_LABELS_TO_DISPLAY:
        abort(404, description=f"Invalid entity label requested: {label}")

    db = get_db(); cursor = db.cursor(); results = []; error_message = None
    start_time = time.time()

    # Basic check if directories exist (only relevant if snippet generation needs them)
    if not INPUT_DIR.is_dir():
        print(f"Warning: Input directory ({INPUT_DIR}) not found, snippet generation might fail for non-PDFs.")
    if not PDF_TEXT_OUTPUT_DIR.is_dir():
         print(f"Warning: PDF text output directory ({PDF_TEXT_OUTPUT_DIR}) not found, snippet generation might fail for PDFs.")

    try:
        # Query to find all document pages/chunks where this specific entity appears
        # Joins documents -> document_entities -> entities
        cursor.execute("""
            SELECT d.relative_path, d.page_number, d.text_filepath
            FROM documents d
            JOIN document_entities de ON d.id = de.document_id
            JOIN entities e ON de.entity_id = e.id
            WHERE e.entity_label = ? AND e.entity_text = ?
            ORDER BY d.relative_path COLLATE NOCASE, d.page_number;
        """, (label, entity_text))
        documents = cursor.fetchall() # Get all occurrences
        db_time = time.time() - start_time
        print(f"  Entity detail query for '{entity_text}' ({label}) found {len(documents)} occurrences in {db_time:.3f}s.")

        snippet_start_time = time.time()
        # Iterate through each document occurrence to generate a snippet
        for doc in documents:
            rel_path = doc['relative_path']
            page_num = doc['page_number']
            # --- Use the definitive text_filepath stored in the DB ---
            # This path points to the file containing the text where the entity was found
            # (e.g., output_text_direct/file.txt for PDFs, documents/file.txt for HTML/TXT)
            text_abs_path_str = doc['text_filepath']
            snippet_html = "<em>(Snippet N/A: Text path unknown in DB)</em>" # Default snippet
            source_ext = Path(rel_path).suffix.lower()

            if text_abs_path_str: # Proceed only if a text file path is recorded
                text_abs_path = Path(text_abs_path_str)
                if not text_abs_path.is_file():
                    # If the text file is missing on disk
                    snippet_html = f'<em class="error">(Snippet Error: Text file missing at {text_abs_path_str})</em>'
                    print(f"Warning: Text file missing for snippet generation: {text_abs_path_str}")
                else:
                     # --- Read Text and Extract Relevant Page/Chunk ---
                     try:
                         # Read the *entire* text file associated with the document occurrence
                         full_doc_text = text_abs_path.read_text(encoding='utf-8')
                         page_text = None # This will hold the specific text content for the snippet

                         # --- Adapt parsing based on the *original* source type ---
                         if source_ext == ".pdf":
                             # For PDFs, we need to extract the specific page content from the combined text file
                             # using the page markers.
                             if page_num == 1:
                                 # Find content before the first "--- Page 2 ---" marker or end of file
                                 match = re.match(r'(.*?)(?=\n\n--- Page \d+ ---|\Z)', full_doc_text, re.DOTALL)
                                 if match: page_text = match.group(1).strip()
                             else:
                                 # Find content after the specific page marker
                                 page_marker = f'\n\n--- Page {page_num} ---\n\n'
                                 # Use regex to find the marker and capture content until the next marker or end of file
                                 marker_match = re.search(re.escape(page_marker) + r'(.*?)(?=\n\n--- Page \d+ ---|\Z)', full_doc_text, re.DOTALL)
                                 if marker_match: page_text = marker_match.group(1).strip()
                         elif source_ext in [".html", ".txt"]:
                             # For HTML/TXT, the entire text file content corresponds to the single "page" (page_num=1)
                             # For HTML, optionally try to extract only the body content for the snippet
                             if source_ext == ".html":
                                 # Look for the "--- Body ---" marker added during extraction
                                 body_match = re.search(r'\n--- Body ---\n(.*)', full_doc_text, re.DOTALL)
                                 if body_match:
                                     page_text = body_match.group(1).strip() # Use only body content
                                 else:
                                     print(f"  Warning: Body marker not found in HTML text for snippet: {text_abs_path.name}. Using full text.")
                                     page_text = full_doc_text.strip() # Fallback to full text
                             else: # TXT file
                                 page_text = full_doc_text.strip() # Use the whole content
                         else:
                             # Should not happen if only supported types are indexed
                             snippet_html = f'<em class="error">(Snippet Error: Unsupported source type {source_ext})</em>'
                             page_text = None # Ensure snippet isn't generated

                         # --- Generate Snippet HTML from page_text ---
                         if page_text is None and not snippet_html.startswith("<em"):
                             # If parsing failed to get page_text for a supported type
                             snippet_html = f'<em class="error">(Snippet Error: Page {page_num} content not parsed from {text_abs_path.name} for {source_ext})</em>'
                         elif page_text is not None:
                            # Compile regex for the entity text (case-insensitive)
                            pattern = re.compile(re.escape(entity_text), re.IGNORECASE)
                            match = pattern.search(page_text) # Find the first occurrence
                            if match:
                                match_start, match_end = match.span()
                                # Calculate start and end indices for the snippet context
                                start = max(0, match_start - SNIPPET_CONTEXT_CHARS)
                                end = min(len(page_text), match_end + SNIPPET_CONTEXT_CHARS)
                                # Extract the raw snippet text
                                raw_snippet = page_text[start:end]
                                # Highlight the *first* match within the raw snippet
                                highlighted = pattern.sub(r'<strong>\g<0></strong>', raw_snippet, count=1)
                                # Add ellipses if context was truncated
                                prefix = "... " if start > 0 else ""
                                suffix = " ..." if end < len(page_text) else ""
                                snippet_html = prefix + highlighted + suffix
                            else:
                                # This case indicates the entity was indexed but couldn't be found
                                # again in the text content, which suggests an inconsistency.
                                snippet_html = f'<em>(Error: Entity "{entity_text}" not found in parsed content for snippet)</em>'
                                print(f"Error: Entity {entity_text} ({label}) indexed but not found in text for snippet: {rel_path} p{page_num}")
                     except Exception as e:
                         # Catch any other errors during file reading or snippet generation
                         snippet_html = f'<em class="error">(Snippet generation error: {e})</em>'
                         print(f"Error generating snippet for {rel_path} p{page_num}: {e}"); traceback.print_exc()

            # Append the result for this occurrence to the list
            results.append({
                'relative_path': rel_path,   # Original source path
                'page_number': page_num,     # Page number (1 for HTML/TXT)
                'snippet': snippet_html      # Generated HTML snippet
            })

        snippet_time = time.time() - snippet_start_time
        print(f"  Generated {len(results)} snippets in {snippet_time:.3f}s.")

    except sqlite3.Error as db_e:
        error_message = f"Database error retrieving details: {db_e}"; flash(error_message, "error"); print(f"DB Error in entity_detail: {db_e}")
    except Exception as e:
        error_message = f"Unexpected error retrieving details: {e}"; flash(error_message, "error"); print(f"Error in entity_detail: {e}"); traceback.print_exc()

    total_time = time.time() - start_time
    print(f"--- Finished entity detail request in {total_time:.3f}s ---")
    # Render the template with the results
    return render_template('entity_detail.html', entity_label=label, entity_text=entity_text, results=results, error_message=error_message)


# --- CHANGED: search() function ---
@app.route('/search')
def search():
    query = request.args.get('q', '').strip()
    results = []
    search_term_lower = query.lower()
    search_error = None

    if not query:
        # If no query provided, just render the empty results page
        return render_template('search_results.html', query=query, results=[], error_message=None)

    print(f"--- Starting DB-driven keyword search for query: '{query}' ---")
    db = get_db(); cursor = db.cursor()

    # --- Get list of *all* text files that have been indexed ---
    # We fetch the text_filepath (where the content is) and the original relative_path
    try:
        # DISTINCT is important if multiple pages from same source use same text_filepath (shouldn't happen with current setup but safe)
        cursor.execute("SELECT DISTINCT text_filepath, relative_path FROM documents")
        # Creates a list of tuples: [(text_path1, source_path1), (text_path2, source_path2), ...]
        indexed_files_info = cursor.fetchall()
        if not indexed_files_info:
             flash("No documents have been indexed yet. Cannot perform search.", "info")
             return render_template('search_results.html', query=query, results=[], error_message="No indexed documents found.")
    except sqlite3.Error as e:
        search_error = f"Database error retrieving indexed files: {e}"; flash(search_error, "error"); print(f"DB Error in search: {e}")
        return render_template('search_results.html', query=query, results=[], error_message=search_error)
    # --- End DB Query ---

    print(f"  Found {len(indexed_files_info)} unique text file paths associated with indexed documents.")
    start_time = time.time(); files_processed = 0; pages_matched = 0; files_with_matches = 0

    try:
        # Iterate through the list of text files obtained from the database
        for text_abs_path_str, source_relative_path in indexed_files_info:
            # text_abs_path_str: Path to the .txt file (in output_text_direct/ or documents/)
            # source_relative_path: Path to the original source file (.pdf, .html, .txt) in documents/

            if not text_abs_path_str:
                print(f"Warning: Null text_filepath found in DB for source {source_relative_path}")
                continue # Skip if path is null/empty

            text_abs_path = Path(text_abs_path_str)
            if not text_abs_path.is_file():
                print(f"Warning: Indexed text file missing during search: {text_abs_path_str} (Source: {source_relative_path})")
                continue # Skip this file if text is missing

            files_processed += 1
            file_matched_this_iteration = False
            try:
                # Read the entire content of the text file
                full_doc_text = text_abs_path.read_text(encoding='utf-8')

                # --- Quick check: Does the search term exist (case-insensitive) anywhere in the file? ---
                if search_term_lower not in full_doc_text.lower():
                    continue # If not found anywhere, skip detailed page splitting/searching for this file

                # --- If found, proceed with page splitting/snippet generation ---
                source_ext = Path(source_relative_path).suffix.lower()

                if source_ext == ".pdf":
                    # --- PDF: Split by page markers and search each page ---
                    # Handle page 1 separately
                    page1_match = re.match(r'(.*?)(?=\n\n--- Page \d+ ---|\Z)', full_doc_text, re.DOTALL)
                    page1_content = page1_match.group(1).strip() if page1_match else ''
                    if page1_content and search_term_lower in page1_content.lower():
                        snippet_html = generate_search_snippet(page1_content, query)
                        results.append({'relative_path': source_relative_path, 'page_number': 1, 'snippet': snippet_html})
                        pages_matched += 1
                        file_matched_this_iteration = True

                    # Handle subsequent pages using regex split
                    page_split_pattern = r'\n\n--- Page (\d+) ---\n\n'
                    # parts will be [content_before_page2, pagenum2, content_page2, pagenum3, content_page3, ...]
                    parts = re.split(page_split_pattern, full_doc_text)
                    # Iterate through pairs of (page_number_str, page_content_str)
                    for i in range(1, len(parts), 2):
                        if i + 1 < len(parts): # Ensure pair exists
                             try: page_num = int(parts[i]) # Page number
                             except ValueError: continue # Skip if page number isn't integer
                             page_text = parts[i+1].strip() # Page content
                             if page_text and search_term_lower in page_text.lower():
                                 snippet_html = generate_search_snippet(page_text, query)
                                 results.append({'relative_path': source_relative_path, 'page_number': page_num, 'snippet': snippet_html})
                                 pages_matched += 1
                                 file_matched_this_iteration = True

                elif source_ext in [".html", ".txt"]:
                    # --- HTML/TXT: Treat as single page/document (page 1) ---
                    page_text_for_snippet = full_doc_text # Default to full text
                    # Optional: For HTML, try using only body content for snippet
                    if source_ext == ".html":
                         body_match = re.search(r'\n--- Body ---\n(.*)', full_doc_text, re.DOTALL)
                         if body_match: page_text_for_snippet = body_match.group(1).strip()

                    # We already know the term exists in full_doc_text, so generate snippet
                    if page_text_for_snippet: # Check if any text exists after potential stripping
                        snippet_html = generate_search_snippet(page_text_for_snippet, query)
                        results.append({'relative_path': source_relative_path, 'page_number': 1, 'snippet': snippet_html})
                        pages_matched += 1
                        file_matched_this_iteration = True
                else:
                    # Should not happen for indexed files, but good practice
                    print(f"Warning: Unsupported source type '{source_ext}' encountered during search for {source_relative_path}")

                if file_matched_this_iteration:
                    files_with_matches += 1

            except FileNotFoundError:
                # Should be caught by initial check, but handle race condition
                print(f"Warning: Text file vanished during search processing: {text_abs_path.name}")
            except Exception as e:
                print(f"Error processing {text_abs_path.name} for source {source_relative_path} during search: {e}")
                traceback.print_exc()
                search_error = "Errors occurred during search processing. Results may be incomplete."

    except Exception as e:
        search_error = "A general error occurred during file iteration for search.";
        print(f"Error during search iteration: {e}"); traceback.print_exc()

    elapsed_time = time.time() - start_time
    print(f"Search for '{query}': Processed {files_processed} text files, found {pages_matched} matching pages/docs in {files_with_matches} distinct source files. Time: {elapsed_time:.2f}s.")

    # Sort results primarily by path, then by page number
    results.sort(key=lambda x: (x['relative_path'], x['page_number']))

    if search_error: flash(search_error, "warning")
    return render_template('search_results.html', query=query, results=results, error_message=search_error)


# (generate_search_snippet unchanged - operates on passed text block)
def generate_search_snippet(page_text, query):
    """Generates an HTML snippet highlighting the first occurrence of the query."""
    try:
        # Case-insensitive search pattern for the literal query string
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        match = pattern.search(page_text)
        if match:
            match_start, match_end = match.span()
            # Calculate context window start and end
            start = max(0, match_start - SNIPPET_CONTEXT_CHARS)
            end = min(len(page_text), match_end + SNIPPET_CONTEXT_CHARS)
            # Extract raw snippet
            raw_snippet = page_text[start:end]
            # Highlight the first occurrence within the snippet
            # Use re.sub with count=1 for safety, though search already found one
            highlighted = pattern.sub(r'<strong>\g<0></strong>', raw_snippet, count=1)
            # Add ellipses if context is partial
            prefix = "... " if start > 0 else ""
            suffix = " ..." if end < len(page_text) else ""
            return prefix + highlighted + suffix
        else:
             # Fallback: If the search term isn't found (e.g., due to normalization issues
             # or if called incorrectly without pre-check), return start of text.
             return page_text[:SNIPPET_CONTEXT_CHARS * 2].strip() + ("..." if len(page_text) > SNIPPET_CONTEXT_CHARS*2 else "")
    except Exception as e:
        print(f"Error generating search snippet for query '{query}': {e}")
        # Return safe HTML on error
        return "<em>(Error generating snippet)</em>"


# --- File: ./app.py ---
# ... (other imports and code) ...

# --- Catalog Routes ---
@app.route('/catalog')
def catalog_view():
    print("\n--- Entering /catalog route ---")
    db = get_db()
    cursor = db.cursor()
    catalogs_data = []
    notes_data = []  # <<< ADDED: Initialize list for notes data
    query_error = False # <<< ADDED: Flag for potential errors in either query
    start_time = time.time()

    # --- Phase 1: Fetch and Process Catalogs ---
    try:
        print("Fetching catalog data...")
        cursor.execute("""
            SELECT c.id as catalog_id, c.name as catalog_name, c.description as catalog_description,
                   dc.doc_relative_path
            FROM catalogs c
            LEFT JOIN document_catalogs dc ON c.id = dc.catalog_id
            ORDER BY c.name COLLATE NOCASE, dc.doc_relative_path COLLATE NOCASE
        """)
        all_catalog_docs = cursor.fetchall()
        fetch_time_catalogs = time.time() - start_time
        print(f"Fetched all catalog documents ({len(all_catalog_docs)} rows) in {fetch_time_catalogs:.3f}s.")

        proc_start_catalogs = time.time()
        temp_catalogs = {}
        for row in all_catalog_docs:
            catalog_id = row['catalog_id']
            if catalog_id not in temp_catalogs:
                temp_catalogs[catalog_id] = {
                    'id': catalog_id,
                    'name': row['catalog_name'],
                    'description': row['catalog_description'],
                    'documents': []
                }
            if row['doc_relative_path']:
                temp_catalogs[catalog_id]['documents'].append(row['doc_relative_path'])

        catalogs_data = list(temp_catalogs.values())
        proc_time_catalogs = time.time() - proc_start_catalogs
        print(f"Processed data for {len(catalogs_data)} catalogs in {proc_time_catalogs:.3f}s.")

    except sqlite3.Error as e_catalogs:
        flash(f"Database error loading catalog data: {e_catalogs}", "error")
        print(f"!!! DB ERROR fetching catalogs: {e_catalogs} !!!"); traceback.print_exc()
        query_error = True # Mark that an error occurred
    except Exception as e_general_catalogs:
        flash(f"An unexpected error occurred loading catalog data: {e_general_catalogs}", "error")
        print(f"!!! UNEXPECTED ERROR fetching catalogs: {e_general_catalogs} !!!"); traceback.print_exc()
        query_error = True # Mark that an error occurred
    # --- End Phase 1 ---

    # --- Phase 2: Fetch Documents with Notes ---
    # <<< ADDED: This whole block >>>
    try:
        # Only proceed if the catalog fetch didn't already fail critically
        if not query_error:
            print("Fetching documents with notes...")
            notes_fetch_start = time.time()
            cursor.execute("""
                SELECT doc_relative_path, note_content, updated_at
                FROM notes
                ORDER BY doc_relative_path COLLATE NOCASE
            """)
            # Fetch all results directly. cursor.fetchall() returns a list of sqlite3.Row objects
            notes_data = cursor.fetchall()
            notes_fetch_time = time.time() - notes_fetch_start
            print(f"Fetched {len(notes_data)} documents with notes in {notes_fetch_time:.3f}s.")

    except sqlite3.Error as e_notes:
        flash(f"Database error loading documents with notes: {e_notes}", "error")
        print(f"!!! DB ERROR fetching notes: {e_notes} !!!"); traceback.print_exc()
        query_error = True # Mark that an error occurred
    except Exception as e_general_notes:
        flash(f"An unexpected error occurred loading notes data: {e_general_notes}", "error")
        print(f"!!! UNEXPECTED ERROR fetching notes: {e_general_notes} !!!"); traceback.print_exc()
        query_error = True # Mark that an error occurred
    # --- End Phase 2 ---

    total_time = time.time() - start_time
    print(f"--- Catalog view generated (catalogs & notes) in {total_time:.3f}s ---")

    # Render the template, passing BOTH lists and the error flag
    return render_template('catalog.html',
                           catalogs=catalogs_data,
                           notes_data=notes_data,  # Pass the notes data
                           error=query_error)      # Pass the error flag

# --- PDF Serving and Viewing Routes ---
# (serve_raw_pdf unchanged - uses INPUT_DIR)
@app.route('/serve_raw_pdf/<path:relative_path_str>')
def serve_raw_pdf(relative_path_str):
    """Serves the raw PDF file content from the INPUT directory."""
    try:
        # Normalize path separators and perform security checks
        norm_rel_path = Path(relative_path_str).as_posix() # Use POSIX separators
        if norm_rel_path.startswith('/') or '..' in norm_rel_path.split('/'):
             abort(404, description="Invalid path format.")

        # Construct the absolute path and verify it's within the INPUT_DIR
        file_path = (INPUT_DIR / norm_rel_path).resolve()
        if not file_path.is_relative_to(INPUT_DIR.resolve()):
            abort(404, description="Path traversal attempt detected.")

        # Verify the file exists and has a .pdf extension (case-insensitive)
        if not file_path.is_file() or not norm_rel_path.lower().endswith(".pdf"):
            abort(404, description="PDF file not found or invalid type.")

        # Use Flask's send_from_directory for secure serving
        # Set as_attachment=False to display inline in browser/iframe
        return send_from_directory(str(INPUT_DIR), norm_rel_path, as_attachment=False, mimetype='application/pdf')
    except NotFound:
        # send_from_directory raises NotFound if file doesn't exist after path checks
        abort(404, description="File not found via send_from_directory.")
    except Exception as e:
        print(f"Error serving raw PDF {relative_path_str}: {e}")
        traceback.print_exc()
        abort(500, description="Server error while serving PDF.")

# (view_pdf unchanged - uses INPUT_DIR)
@app.route('/view/<path:relative_path_str>')
def view_pdf(relative_path_str):
    """Renders the PDF viewer template for a specific PDF file."""
    try:
        # Normalize path separators and perform security checks
        norm_rel_path = Path(relative_path_str).as_posix()
        if norm_rel_path.startswith('/') or '..' in norm_rel_path.split('/'):
            abort(404, description="Invalid path format.")

        # Construct the absolute path and verify it's within the INPUT_DIR
        pdf_abs_path = (INPUT_DIR / norm_rel_path).resolve()
        if not pdf_abs_path.is_relative_to(INPUT_DIR.resolve()):
             abort(404, description="Path traversal attempt detected.")

        # Verify the file exists and has a .pdf extension (case-insensitive)
        if not pdf_abs_path.is_file() or not norm_rel_path.lower().endswith(".pdf"):
             abort(404, description=f"PDF file not found or inaccessible: {relative_path_str}")

        # Generate the URL to the raw PDF content using the serving route
        raw_pdf_url = url_for('serve_raw_pdf', relative_path_str=norm_rel_path)

        # Render the viewer template, passing necessary data
        return render_template('pdf_viewer.html',
                               relative_path=norm_rel_path, # Pass the normalized path
                               raw_pdf_url=raw_pdf_url,     # URL for the iframe src
                               source_type='pdf')           # Indicate type (optional for JS)
    except Exception as e:
        print(f"Error preparing PDF view for {relative_path_str}: {e}")
        traceback.print_exc()
        abort(500, description="Server error while preparing PDF view.")


# --- ADDED: Text/HTML Serving and Viewing Routes ---
@app.route('/serve_raw_source/<path:relative_path_str>')
def serve_raw_source(relative_path_str):
    """Serves original HTML or TXT files from the INPUT directory."""
    try:
        # Normalize path and check extension
        norm_rel_path = Path(relative_path_str).as_posix()
        ext = Path(norm_rel_path).suffix.lower()
        allowed_exts = [".html", ".htm", ".txt"]
        if norm_rel_path.startswith('/') or '..' in norm_rel_path.split('/'):
            abort(404, description="Invalid path format.")
        if ext not in allowed_exts:
             abort(404, description=f"File type {ext} not allowed for raw source view.")

        # Construct absolute path and verify it's within INPUT_DIR
        file_path = (INPUT_DIR / norm_rel_path).resolve()
        if not file_path.is_relative_to(INPUT_DIR.resolve()):
            abort(404, description="Path traversal attempt detected.")
        if not file_path.is_file():
            abort(404, description="Source file not found.")

        # Determine MIME type based on extension
        mimetype = 'text/html' if ext in ['.html', '.htm'] else 'text/plain'

        # Serve the file inline
        return send_from_directory(str(INPUT_DIR), norm_rel_path, mimetype=mimetype, as_attachment=False)
    except NotFound:
        abort(404, description="File not found via send_from_directory.")
    except Exception as e:
        print(f"Error serving raw source {relative_path_str}: {e}")
        traceback.print_exc()
        abort(500, description="Server error while serving source file.")

@app.route('/view_source/<path:relative_path_str>')
def view_source(relative_path_str):
    """Provides a viewer page for HTML or TXT files using an iframe."""
    try:
        # Normalize path and check extension
        norm_rel_path = Path(relative_path_str).as_posix()
        ext = Path(norm_rel_path).suffix.lower()
        allowed_exts = [".html", ".htm", ".txt"]
        if norm_rel_path.startswith('/') or '..' in norm_rel_path.split('/'):
            abort(404, description="Invalid path format.")
        if ext not in allowed_exts:
            abort(404, description=f"File type {ext} not allowed for source view.")

        # Construct absolute path and verify existence within INPUT_DIR
        source_abs_path = (INPUT_DIR / norm_rel_path).resolve()
        if not source_abs_path.is_relative_to(INPUT_DIR.resolve()) or not source_abs_path.is_file():
             abort(404, description=f"Source file not found or inaccessible: {relative_path_str}")

        # Generate URL for the raw source content
        raw_source_url = url_for('serve_raw_source', relative_path_str=norm_rel_path)

        # Determine source type for the template (e.g., for conditional logic)
        source_type = 'html' if ext in ['.html', '.htm'] else 'text'

        # Render the generic source viewer template
        return render_template('source_viewer.html',
                               relative_path=norm_rel_path,
                               raw_source_url=raw_source_url,
                               source_type=source_type)
    except Exception as e:
        print(f"Error preparing source view for {relative_path_str}: {e}")
        traceback.print_exc()
        abort(500, description="Server error while preparing source view.")


# --- API Endpoints ---
# Helper to decode path and perform basic security checks
def _get_path_from_encoded(encoded_path):
    """Decodes URL-encoded path and performs basic security checks."""
    try:
        relative_path = urllib.parse.unquote(encoded_path)
        # Security check: Ensure path doesn't try to escape INPUT_DIR
        # Check for leading slash or ".." components
        # Note: Path(...) automatically normalizes separators
        if relative_path.startswith('/') or '..' in Path(relative_path).parts:
            print(f"Warning: Invalid path format detected in API request: {relative_path}")
            return None
        # Optional: Check if the path actually resolves within INPUT_DIR?
        # Might be too slow for an API helper, rely on main routes/DB checks.
        # full_path = (INPUT_DIR / relative_path).resolve()
        # if not full_path.is_relative_to(INPUT_DIR.resolve()): return None
        return relative_path
    except Exception as e:
        print(f"Error decoding path '{encoded_path}': {e}")
        return None

# --- Favorite API ---
@app.route('/api/documents/<path:encoded_path>/favorite_status', methods=['GET'])
def get_favorite_status(encoded_path):
    relative_path = _get_path_from_encoded(encoded_path)
    if not relative_path: return jsonify(detail='Invalid or disallowed document path'), 400
    conn = None
    try:
        conn = get_api_db(); cursor = conn.cursor()
        # Check if an entry exists in the favorites table for this path
        cursor.execute("SELECT 1 FROM favorites WHERE doc_relative_path = ?", (relative_path,))
        is_fav = cursor.fetchone() is not None # True if a row exists, False otherwise
        conn.close()
        return jsonify(is_favorite=is_fav)
    except Exception as e:
        if conn: conn.close()
        print(f"API Error getting favorite status for {relative_path}: {e}")
        return jsonify(detail='Database error checking favorite status'), 500

@app.route('/api/documents/<path:encoded_path>/favorite', methods=['POST'])
def set_favorite_status(encoded_path):
    relative_path = _get_path_from_encoded(encoded_path)
    if not relative_path: return jsonify(detail='Invalid or disallowed document path'), 400
    data = request.json
    # Validate input JSON
    if data is None or 'is_favorite' not in data or not isinstance(data['is_favorite'], bool):
        return jsonify(detail='Missing or invalid "is_favorite" (boolean) in request body'), 400

    is_favorite = data['is_favorite']
    conn = None
    try:
        conn = get_api_db(); cursor = conn.cursor()
        conn.execute("BEGIN") # Use transaction for insert/delete consistency
        if is_favorite:
            # Add to favorites: Insert if not present, ignore if already there
            cursor.execute("INSERT OR IGNORE INTO favorites (doc_relative_path, is_favorite, updated_at) VALUES (?, 1, CURRENT_TIMESTAMP)", (relative_path,))
            print(f"API: Marked '{relative_path}' as favorite.")
        else:
            # Remove from favorites
            cursor.execute("DELETE FROM favorites WHERE doc_relative_path = ?", (relative_path,))
            print(f"API: Removed '{relative_path}' from favorites.")
        conn.commit()
        conn.close()
        return jsonify(success=True, is_favorite=is_favorite) # Return the final state
    except sqlite3.Error as e:
        if conn:
            try: conn.rollback() # Rollback on error
            except: pass
            conn.close()
        print(f"API DB Error setting favorite for {relative_path}: {e}")
        return jsonify(detail=f'Database error setting favorite: {e}'), 500
    except Exception as e:
         # Catch unexpected errors
         if conn: conn.close()
         print(f"API Unexpected Error setting favorite for {relative_path}: {e}")
         traceback.print_exc()
         return jsonify(detail='Unexpected server error'), 500


# --- Notes API ---
@app.route('/api/documents/<path:encoded_path>/notes', methods=['GET'])
def get_notes(encoded_path):
    relative_path = _get_path_from_encoded(encoded_path)
    if not relative_path: return jsonify(detail='Invalid or disallowed document path'), 400
    conn = None
    try:
        conn = get_api_db(); cursor = conn.cursor()
        # Fetch the note content for the given path
        cursor.execute("SELECT note_content FROM notes WHERE doc_relative_path = ?", (relative_path,))
        note = cursor.fetchone()
        conn.close()
        # Return the content, or an empty string if no note exists
        return jsonify(note_content=note['note_content'] if note else '')
    except Exception as e:
        if conn: conn.close()
        print(f"API Error getting note for {relative_path}: {e}")
        return jsonify(detail='Database error getting note'), 500

@app.route('/api/documents/<path:encoded_path>/notes', methods=['POST'])
def save_note(encoded_path):
    relative_path = _get_path_from_encoded(encoded_path)
    if not relative_path: return jsonify(detail='Invalid or disallowed document path'), 400
    data = request.json
    # Validate input JSON
    if data is None or 'note_content' not in data or not isinstance(data['note_content'], str):
        return jsonify(detail='Missing or invalid "note_content" (string) in request body'), 400

    note_content = data['note_content'].strip() # Trim whitespace
    conn = None
    try:
         conn = get_api_db(); cursor = conn.cursor()
         conn.execute("BEGIN")
         if note_content: # If content is provided, insert or update
             # Use INSERT OR REPLACE with ON CONFLICT for UPSERT behavior based on UNIQUE constraint
             # Simpler than INSERT OR IGNORE + UPDATE
             cursor.execute("""
                 INSERT INTO notes (doc_relative_path, note_content, created_at, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                 ON CONFLICT(doc_relative_path) DO UPDATE SET
                    note_content=excluded.note_content,
                    updated_at=CURRENT_TIMESTAMP;
             """, (relative_path, note_content))
             print(f"API: Saved/Updated note for {relative_path}")
         else: # If content is empty, delete the note
             cursor.execute("DELETE FROM notes WHERE doc_relative_path = ?", (relative_path,))
             print(f"API: Deleted note for {relative_path} (empty content received)")
         conn.commit()
         conn.close()
         return jsonify(success=True)
    except sqlite3.Error as e:
        if conn:
            try: conn.rollback()
            except: pass
            conn.close()
        print(f"API DB Error saving note for {relative_path}: {e}")
        return jsonify(detail=f'Database error saving note: {e}'), 500
    except Exception as e:
         if conn: conn.close()
         print(f"API Unexpected Error saving note for {relative_path}: {e}")
         traceback.print_exc()
         return jsonify(detail='Unexpected server error'), 500


# --- Catalog API ---
@app.route('/api/catalogs', methods=['GET'])
def list_catalogs():
    conn = None
    try:
        conn = get_api_db(); cursor = conn.cursor()
        # Get all catalogs, ordered by name case-insensitively
        cursor.execute("SELECT id, name FROM catalogs ORDER BY name COLLATE NOCASE")
        # Format as a list of dictionaries
        catalogs = [{'id': row['id'], 'name': row['name']} for row in cursor.fetchall()]
        conn.close()
        return jsonify(catalogs=catalogs)
    except Exception as e:
        if conn: conn.close()
        print(f"API Error listing catalogs: {e}")
        return jsonify(detail='Database error listing catalogs'), 500

@app.route('/api/catalogs', methods=['POST'])
def create_catalog():
    data = request.json
    # Validate input
    if not data or not data.get('name') or not isinstance(data['name'], str):
        return jsonify(detail='Catalog "name" (string) is required in request body'), 400
    name = data['name'].strip()
    if not name:
        return jsonify(detail='Catalog name cannot be empty'), 400
    # Get optional description
    description = data.get('description', '').strip()
    conn = None
    try:
        conn = get_api_db(); cursor = conn.cursor()
        # Insert new catalog
        cursor.execute("INSERT INTO catalogs (name, description) VALUES (?, ?)", (name, description))
        new_id = cursor.lastrowid # Get the ID of the newly inserted row
        conn.commit()
        conn.close()
        print(f"API: Created catalog '{name}' (ID: {new_id})")
        # Return success with details of the new catalog
        return jsonify(success=True, id=new_id, name=name, description=description), 201 # 201 Created status
    except sqlite3.IntegrityError:
        # Handle UNIQUE constraint violation (catalog name already exists)
        if conn: conn.rollback(); conn.close()
        print(f"API: Attempt to create duplicate catalog name '{name}'")
        return jsonify(detail=f"Catalog name '{name}' already exists."), 409 # 409 Conflict status
    except sqlite3.Error as e:
        if conn: conn.rollback(); conn.close()
        print(f"API DB Error creating catalog '{name}': {e}")
        return jsonify(detail=f'Database error creating catalog: {e}'), 500
    except Exception as e:
         if conn: conn.close()
         print(f"API Unexpected Error creating catalog '{name}': {e}")
         traceback.print_exc()
         return jsonify(detail='Unexpected server error'), 500

@app.route('/api/catalogs/<int:catalog_id>/documents', methods=['POST'])
def add_doc_to_catalog(catalog_id):
    data = request.json
    # Validate input: requires doc_relative_path in body
    if not data or not data.get('doc_relative_path'):
        return jsonify(detail='doc_relative_path is required in request body'), 400

    # Get path directly from JSON body (assume it's sent plain)
    relative_path = data['doc_relative_path']
    # Perform security checks on the path from the body
    if not relative_path or relative_path.startswith('/') or '..' in Path(relative_path).parts:
         return jsonify(detail='Invalid document path format in request body'), 400

    conn = None
    try:
        conn = get_api_db(); cursor = conn.cursor()
        conn.execute("BEGIN")
        # Check if the target catalog exists
        cursor.execute("SELECT 1 FROM catalogs WHERE id = ?", (catalog_id,))
        if not cursor.fetchone():
            conn.rollback(); conn.close()
            return jsonify(detail='Catalog not found'), 404

        # Optional check: Ensure the document exists in the status table?
        # This adds overhead but ensures only known files can be added.
        # cursor.execute("SELECT 1 FROM pdf_status WHERE relative_path = ?", (relative_path,))
        # if not cursor.fetchone():
        #     conn.rollback(); conn.close()
        #     return jsonify(detail='Document not found in system records'), 404

        # Add the document to the catalog link table.
        # INSERT OR IGNORE prevents errors if the link already exists.
        cursor.execute("INSERT OR IGNORE INTO document_catalogs (doc_relative_path, catalog_id, added_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                       (relative_path, catalog_id))
        conn.commit()
        conn.close()
        print(f"API: Added doc '{relative_path}' to catalog ID {catalog_id}")
        # Return 201 if created, 200 if ignored (already existed) - or just 200/201 always
        return jsonify(success=True), 201 # Indicate resource potentially created
    except sqlite3.Error as e:
        if conn: conn.rollback(); conn.close()
        print(f"API DB Error adding doc {relative_path} to catalog {catalog_id}: {e}")
        return jsonify(detail=f'Database error adding document to catalog: {e}'), 500
    except Exception as e:
         if conn: conn.close()
         print(f"API Unexpected Error adding doc {relative_path} to catalog {catalog_id}: {e}")
         traceback.print_exc()
         return jsonify(detail='Unexpected server error'), 500

@app.route('/api/catalogs/<int:catalog_id>', methods=['DELETE'])
def delete_catalog(catalog_id):
     conn = None
     try:
         conn = get_api_db(); cursor = conn.cursor()
         # Delete the catalog. Foreign key CASCADE should handle document_catalogs links.
         cursor.execute("DELETE FROM catalogs WHERE id = ?", (catalog_id,))
         rows_deleted = cursor.rowcount # Check if a row was actually deleted
         conn.commit()
         conn.close()
         if rows_deleted > 0:
             print(f"API: Deleted catalog ID {catalog_id}")
             return jsonify(success=True), 200 # OK status
         else:
             # Catalog with that ID didn't exist
             return jsonify(detail='Catalog not found'), 404
     except sqlite3.Error as e:
         if conn: conn.rollback(); conn.close()
         print(f"API DB Error deleting catalog {catalog_id}: {e}")
         return jsonify(detail=f'Database error deleting catalog: {e}'), 500
     except Exception as e:
          if conn: conn.close()
          print(f"API Unexpected Error deleting catalog {catalog_id}: {e}")
          traceback.print_exc()
          return jsonify(detail='Unexpected server error'), 500

@app.route('/api/catalogs/<int:catalog_id>/documents/<path:encoded_path>', methods=['DELETE'])
def remove_doc_from_catalog(catalog_id, encoded_path):
    # Get path from URL segment
    relative_path = _get_path_from_encoded(encoded_path)
    if not relative_path: return jsonify(detail='Invalid or disallowed document path in URL'), 400
    conn = None
    try:
        conn = get_api_db(); cursor = conn.cursor()
        # Delete the specific link between the document and the catalog
        cursor.execute("DELETE FROM document_catalogs WHERE catalog_id = ? AND doc_relative_path = ?", (catalog_id, relative_path))
        rows_deleted = cursor.rowcount # Check if a link was actually deleted
        conn.commit()
        conn.close()
        if rows_deleted > 0:
            print(f"API: Removed doc '{relative_path}' from catalog ID {catalog_id}")
            return jsonify(success=True), 200 # OK status
        else:
            # Link didn't exist (or catalog didn't exist)
            return jsonify(detail='Document not found in specified catalog or catalog not found'), 404
    except sqlite3.Error as e:
        if conn: conn.rollback(); conn.close()
        print(f"API DB Error removing doc {relative_path} from catalog {catalog_id}: {e}")
        return jsonify(detail=f'Database error removing document from catalog: {e}'), 500
    except Exception as e:
         if conn: conn.close()
         print(f"API Unexpected Error removing doc {relative_path} from catalog {catalog_id}: {e}")
         traceback.print_exc()
         return jsonify(detail='Unexpected server error'), 500

# --- NEW: API Endpoint for Status Polling ---
@app.route('/api/status_updates', methods=['GET'])
def api_status_updates():
    """
    API endpoint for JavaScript polling.
    Returns the current status of files likely to be processing or recently changed.
    """
    updated_statuses = {}
    conn = None
    try:
        # Get statuses for files that are 'In Progress' or were recently updated
        # (e.g., updated in the last minute, adjust time as needed)
        # This helps limit the query size compared to fetching all statuses.
        # Using CURRENT_TIMESTAMP requires SQLite 3.38.0+ for datetime('now','-1 minute')
        # Fallback to fetching all if needed, or just 'In Progress'
        conn = get_api_db()
        cursor = conn.cursor()

        # Query for files potentially needing UI update
        # Option A: Just check 'In Progress' files (simplest, might miss rapid changes)
        # cursor.execute("SELECT relative_path, status FROM pdf_status WHERE status LIKE '%In Progress'")

        # Option B: Check 'In Progress' OR recently updated (more comprehensive but larger query)
        # Note: Using datetime requires careful handling across DB versions.
        # For broad compatibility, let's just query files in potentially transient states.
        transient_states = ('Text Extract In Progress', 'Indexing In Progress')
        placeholders = ','.join('?' * len(transient_states))
        # Fetch files in progress, order by most recently updated first, limit results
        cursor.execute(f"SELECT relative_path, status, last_updated FROM pdf_status WHERE status IN ({placeholders}) ORDER BY last_updated DESC LIMIT 50", transient_states)
        # We could also add files updated recently, but that adds complexity. Let's stick to this for now.

        results = cursor.fetchall()
        conn.close() # Close connection after fetching

        for row in results:
            # Format last_updated nicely if needed, or just send status
            updated_statuses[row['relative_path']] = {
                'status': row['status'],
                # Use the existing Jinja filter function to format the timestamp for display
                'last_updated_display': _jinja2_filter_datetime(row['last_updated'], '%Y-%m-%d %H:%M')
             }

        return jsonify(success=True, updates=updated_statuses)

    except sqlite3.Error as e:
        if conn: conn.close()
        print(f"API Error in /api/status_updates (DB): {e}")
        # Don't crash the server, return an error response
        return jsonify(success=False, error=f"Database error: {e}"), 500
    except Exception as e:
        if conn: conn.close()
        print(f"API Error in /api/status_updates (General): {e}")
        traceback.print_exc()
        return jsonify(success=False, error="Internal server error"), 500
# --- END NEW API Endpoint ---


# --- Error Handlers ---
# (page_not_found, internal_server_error, bad_request unchanged)
@app.errorhandler(404)
def page_not_found(e):
    # Get description from exception or use default
    description = getattr(e, 'description', "The requested resource was not found.")
    print(f"Error 404: {request.path} - {description}")
    # Return JSON if client prefers it (e.g., API call)
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify(error=description), 404
    # Otherwise, return HTML error page using base template
    return render_template('error_flask.html', error_code=404, error_message=description), 404

@app.errorhandler(500)
def internal_server_error(e):
    description = getattr(e, 'description', "An internal server error occurred.")
    # Log the error to stderr for visibility
    print(f"Error 500: {request.path} - {e}", file=sys.stderr); traceback.print_exc(file=sys.stderr)
    # Return JSON for API clients
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify(error="Internal Server Error"), 500 # Avoid leaking details in JSON
    # Return HTML error page
    return render_template('error_flask.html', error_code=500, error_message=description), 500

@app.errorhandler(400)
def bad_request(e):
     description = getattr(e, 'description', "Bad Request.")
     print(f"Error 400: {request.path} - {description}")
     # Return JSON for API clients
     if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify(error=description), 400
     # Return HTML error page
     return render_template('error_flask.html', error_code=400, error_message=description), 400


# --- Run the App ---
if __name__ == '__main__':
    print(f"--- Python Interpreter Executing app.py ---")
    print(f"sys.executable: {sys.executable}")
    print(f"sys.version: {sys.version}")
    print(f"-----------------------------------------")

    print("Starting Redleaf application...")

    # --- Pre-run Checks ---
    critical_error = False
    # Check essential directories
    if not INPUT_DIR.is_dir():
        print(f"CRITICAL ERROR: Input source directory not found: {INPUT_DIR}"); critical_error = True
    if not PDF_TEXT_OUTPUT_DIR.is_dir():
        # This directory is only strictly needed for PDFs, but create it if missing
        print(f"Warning: PDF Text Output directory not found: {PDF_TEXT_OUTPUT_DIR}. Attempting to create.")
        try: PDF_TEXT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as mkdir_e: print(f"  Failed to create PDF output dir: {mkdir_e}"); critical_error=True # Make critical if creation fails

    # Check for log directory
    log_dir_check = Path("./logs").resolve()
    try: log_dir_check.mkdir(exist_ok=True)
    except Exception as e: print(f"Warning: Could not create log directory {log_dir_check}: {e}") # Non-critical usually

    # Check for background task script
    task_script_path = Path(__file__).parent / 'run_task.py'
    if not task_script_path.is_file():
        print(f"WARNING: Background task script 'run_task.py' not found. Single file processing will fail.")
        # Not necessarily critical if only bulk processing is used, but warn loudly.

    if critical_error:
        print("Aborting startup due to critical errors.")
        sys.exit(1)

    # Setup database (creates if not exists, checks tables)
    try:
        setup_database()
    except Exception as db_err:
        print(f"CRITICAL ERROR during database setup: {db_err}"); traceback.print_exc()
        sys.exit(1)

    # --- Print Configuration ---
    print(f"--- Configuration ---")
    print(f"Input source dir:    {INPUT_DIR}")
    print(f"PDF Text output dir: {PDF_TEXT_OUTPUT_DIR}")
    print(f"Log directory:       {log_dir_check}")
    print(f"Database file:       {Path(DATABASE).resolve()}")
    print(f"Max General Workers: {app.config['MAX_CONCURRENT_TASKS']}")
    print(f"Max Indexing Workers:{app.config['MAX_INDEXING_WORKERS']}")
    print(f"---------------------")

    print(f"Starting Flask app server on http://0.0.0.0:5000...")
    # Use threaded=False because we rely on ProcessPoolExecutor/subprocess for true parallelism.
    # Flask's built-in threading can interfere with process pools or cause unexpected behavior.
    # debug=False is recommended for production or when using multiprocessing.
    app.run(debug=False, host='0.0.0.0', port=5000, threaded=False)