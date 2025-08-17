import sqlite3
import multiprocessing
from pathlib import Path

# Import the application factory and configuration from our new 'project' package
from project import create_app
from project.config import DATABASE_FILE
import storage_setup

# Create the Flask app instance using our factory function
app = create_app()

def run_startup_logic():
    """
    Contains logic that should only run once before the server starts.
    This includes checking for the database and cleaning up stale tasks.
    """
    # This is necessary for PyInstaller or when running on Windows.
    multiprocessing.freeze_support()

    db_path = Path(DATABASE_FILE)
    if not db_path.exists():
        print("Database not found. Creating a new one...")
        storage_setup.create_unified_index(db_path)
    else:
        # This cleanup logic makes the app more robust between restarts.
        print("--- Performing startup cleanup ---")
        conn = None
        try:
            conn = sqlite3.connect(db_path)
            # Find any documents that were 'Queued' but never finished (e.g., due to a shutdown).
            count = conn.execute("SELECT COUNT(*) FROM documents WHERE status = 'Queued'").fetchone()[0]
            if count > 0:
                print(f"Found {count} stale 'Queued' documents. Resetting them to 'New'...")
                conn.execute("UPDATE documents SET status = 'New', status_message='Reset on startup' WHERE status = 'Queued'")
                conn.commit()
                print("Cleanup complete.")
            else:
                print("No stale documents found. System state is clean.")
        except Exception as e:
            print(f"!!! ERROR during startup cleanup: {e} !!!")
        finally:
            if conn:
                conn.close()

if __name__ == '__main__':
    # Run the pre-start checks and logic.
    run_startup_logic()

    # Run the Flask application.
    # use_reloader=False is important because our background thread setup
    # is not designed to be run twice by the reloader's two processes.
    print("--- Redleaf Engine Starting ---")
    print("--- Access at: http://0.0.0.0:5000 ---")
    app.run(debug=False, host='0.0.0.0', port=5000, use_reloader=False)