# --- File: ./run.py (Corrected for Pre-compute Workflow) ---
import sqlite3
import multiprocessing
import subprocess
from pathlib import Path
import zipfile

from project import create_app
from project.config import DATABASE_FILE, INSTANCE_DIR
import storage_setup

def run_startup_logic():
    multiprocessing.freeze_support()

    db_path = Path(DATABASE_FILE)
    marker_path = Path(INSTANCE_DIR) / "precomputed.marker"
    zip_path = Path(INSTANCE_DIR) / "initial_state.sql.zip"

    # --- KEY CHANGE: The condition now checks for BOTH the marker AND the zip file ---
    # This ensures the one-time build only runs on the very first startup in pre-computed mode.
    if marker_path.exists() and zip_path.exists():
        print("--- Precomputed Mode: Initial one-time database build detected ---")
        if db_path.exists():
            print("  [INFO] Removing existing database for a fresh build...")
            db_path.unlink()
        
        print(f"Building database from '{zip_path.name}'...")
        try:
            sql_content = None
            with zipfile.ZipFile(zip_path, 'r') as zf:
                with zf.open('initial_state.sql', 'r') as f_in:
                    sql_content = f_in.read()
            
            if sql_content:
                # Use Python's native sqlite3 to avoid external dependency
                conn = sqlite3.connect(db_path)
                conn.executescript(sql_content.decode('utf-8'))
                conn.commit()
                conn.close()
            else:
                raise ValueError("SQL content from zip file is empty.")
            
            print("  [OK]   Database successfully built.")
            zip_path.unlink() # Clean up the zip file AFTER successful import
            print("  [OK]   Cleaned up temporary SQL zip file.")
            print("--- Initial build complete. Subsequent runs will use the created database. ---")
        except Exception as e:
            print(f"[FATAL] Failed to build precomputed database from zip.")
            print(f"       Error: {e}")
            if db_path.exists(): db_path.unlink()
            exit(1)
        # We are done with startup for the initial build, so we return.
        return

    # If the code reaches here, it means one of two things:
    # 1. We are in normal "Curator" mode.
    # 2. We are in "Explorer" mode, but the initial build is already done.
    # In both cases, we just need to perform the standard cleanup.
    temp_app = create_app(start_background_thread=False)
    with temp_app.app_context():
        if not db_path.exists():
            print("Database not found. Creating a new one...")
            storage_setup.create_unified_index(db_path)
        else:
            print("--- Performing startup cleanup ---")
            conn = None
            try:
                conn = sqlite3.connect(db_path)
                # This cleanup is safe to run in both modes. It just resets stale documents.
                count = conn.execute("SELECT COUNT(*) FROM documents WHERE status IN ('Queued', 'Indexing')").fetchone()[0]
                if count > 0:
                    print(f"Found {count} stale documents. Resetting to 'New'...")
                    conn.execute("UPDATE documents SET status = 'New', status_message='Reset on startup' WHERE status IN ('Queued', 'Indexing')")
                    conn.commit()
                    print("Cleanup complete.")
                else:
                    print("System state is clean.")
            except Exception as e:
                print(f"!!! ERROR during startup cleanup: {e} !!!")
            finally:
                if conn: conn.close()

if __name__ == '__main__':
    run_startup_logic()
    app = create_app(start_background_thread=True)
    print("--- Redleaf Engine Starting ---")
    print(f"--- Access at: http://0.0.0.0:5000 ---")
    app.run(debug=False, host='0.0.0.0', port=5000, use_reloader=False)
