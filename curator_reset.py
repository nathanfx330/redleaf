# --- File: ./curator_reset.py ---
import os
import sys
import subprocess
from pathlib import Path

# --- Configuration ---
# Add the project directory to the Python path to allow imports
project_dir = Path(__file__).resolve().parent
sys.path.append(str(project_dir))

# Define the files to be deleted
DUCKDB_FILE = project_dir / "curator_workspace.duckdb"
DUCKDB_WAL_FILE = project_dir / "curator_workspace.duckdb.wal"
SQLITE_DB_FILE = project_dir / "knowledge_base.db"
CURATOR_CLI_SCRIPT = project_dir / "curator_cli.py"

def run_command(command):
    """Runs a command and prints its output in real-time."""
    print(f"\n--- Running: {' '.join(command)} ---")
    try:
        # Using Popen to stream output for a better user experience
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8'
        )
        for line in iter(process.stdout.readline, ''):
            print(line, end='')
        process.wait()
        if process.returncode != 0:
            print(f"\n[ERROR] Command failed with exit code {process.returncode}")
            sys.exit(1)
    except FileNotFoundError:
        print(f"\n[ERROR] Command not found: '{command[0]}'. Make sure you are in the correct directory.")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] An unexpected error occurred: {e}")
        sys.exit(1)

def main():
    """Main function to perform the hard reset."""
    print("======================================================")
    print("=== Redleaf Curator - Hard Reset and Discovery Tool ===")
    print("======================================================")
    print("\nThis script will perform the following IRREVERSIBLE actions:")
    print("  1. DELETE the DuckDB workspace (curator_workspace.duckdb)")
    print("  2. DELETE any stale lock files (.wal)")
    print("  3. DELETE the final SQLite database (knowledge_base.db)")
    print("  4. RE-INITIALIZE a new, empty DuckDB workspace.")
    print("  5. RE-DISCOVER all documents from your ./documents directory.")
    print("\nThis is useful for starting a fresh build from scratch.")
    
    confirm = input("\nAre you absolutely sure you want to proceed? [y/N]: ")
    if confirm.lower() != 'y':
        print("Operation cancelled.")
        sys.exit(0)

    # --- Step 1: Deleting files ---
    print("\n--- Deleting existing database files... ---")
    files_to_delete = [DUCKDB_FILE, DUCKDB_WAL_FILE, SQLITE_DB_FILE]
    for f in files_to_delete:
        if f.exists():
            try:
                f.unlink()
                print(f"  [OK] Deleted: {f.name}")
            except OSError as e:
                print(f"  [FAIL] Could not delete {f.name}: {e}")
                sys.exit(1)
        else:
            print(f"  [INFO] Not found, skipping: {f.name}")
    
    # --- Step 2: Run init-db ---
    run_command(['python', str(CURATOR_CLI_SCRIPT), 'init-db'])
    
    # --- Step 3: Run discover-docs ---
    run_command(['python', str(CURATOR_CLI_SCRIPT), 'discover-docs'])

    print("\n=============================================")
    print("=== Hard Reset and Discovery Complete! ===")
    print("=============================================")
    print("\nYour workspace is now clean and all documents have been registered.")
    print("The next step is to run the new high-throughput processing pipeline.")
    print("\nNext Command:")
    print("  python curator_cli.py process-docs run-all --workers 8 --batch-size 128")
    print("=============================================")

if __name__ == "__main__":
    main()