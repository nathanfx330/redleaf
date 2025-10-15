# --- File: ./run.py (FINAL, WITH fileno FIX) ---
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

    if marker_path.exists():
        print("--- Precomputed Mode Detected ---")
        if db_path.exists():
            db_path.unlink()
        if not zip_path.exists():
            print(f"[FATAL] Precomputed mode failed: '{zip_path.name}' not found.")
            exit(1)
        print(f"Building database from '{zip_path.name}'...")
        try:
            # --- START OF MODIFICATION ---
            sql_content = None
            with zipfile.ZipFile(zip_path, 'r') as zf:
                # Open the initial_state.sql file *inside* the zip archive
                with zf.open('initial_state.sql', 'r') as f_in:
                    # Read the entire decompressed content into memory as bytes
                    sql_content = f_in.read()
            
            # Now, run the subprocess, passing the content via the 'input' argument
            # This avoids the 'fileno' error and is cross-platform compatible.
            if sql_content:
                subprocess.run(['sqlite3', str(db_path)], input=sql_content, check=True, capture_output=True)
            else:
                raise ValueError("SQL content from zip file is empty.")
            # --- END OF MODIFICATION ---
            
            print("  [OK]   Database successfully built.")
            zip_path.unlink() # Clean up the zip file after successful import
            print("  [OK]   Cleaned up temporary SQL zip file.")
        except (subprocess.CalledProcessError, FileNotFoundError, IOError, zipfile.BadZipFile, KeyError, ValueError) as e:
            print(f"[FATAL] Failed to build precomputed database from zip.")
            stderr = getattr(e, 'stderr', b'').decode('utf-8', 'ignore')
            print(f"       Error: {stderr or e}")
            if db_path.exists(): db_path.unlink()
            exit(1)
        return

    # Use a temporary app context for DB operations to avoid conflicts
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

    # Create the final app instance that will also start the background manager thread
    app = create_app(start_background_thread=True)
    
    print("--- Redleaf Engine Starting ---")
    print(f"--- Access at: http://0.0.0.0:5000 ---")
    app.run(debug=False, host='0.0.0.0', port=5000, use_reloader=False)
