# --- File: ./run.py (Definitive Final Version) ---
import sqlite3
import multiprocessing
import subprocess
from pathlib import Path

from project import create_app
from project.config import DATABASE_FILE, INSTANCE_DIR
import storage_setup

def run_startup_logic():
    multiprocessing.freeze_support()

    db_path = Path(DATABASE_FILE)
    marker_path = Path(INSTANCE_DIR) / "precomputed.marker"
    sql_path = Path(INSTANCE_DIR) / "initial_state.sql"

    if marker_path.exists():
        print("--- Precomputed Mode Detected ---")
        if db_path.exists():
            db_path.unlink()
        if not sql_path.exists():
            print(f"[FATAL] Precomputed mode failed: '{sql_path.name}' not found.")
            exit(1)
        print(f"Building database from '{sql_path.name}'...")
        try:
            with open(sql_path, 'r', encoding='utf-8') as f_in:
                subprocess.run(['sqlite3', str(db_path)], stdin=f_in, check=True, capture_output=True)
            print("  [OK]   Database successfully built.")
            sql_path.unlink()
            print("  [OK]   Cleaned up temporary SQL file.")
        except (subprocess.CalledProcessError, FileNotFoundError, IOError) as e:
            print(f"[FATAL] Failed to build precomputed database.")
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