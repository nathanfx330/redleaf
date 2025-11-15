# --- File: ./curator_data_manager.py (FINAL, CORRECTING BAKE SCRIPT) ---
import argparse
import sys
from pathlib import Path
import sqlite3
import duckdb
import pandas as pd
from tqdm import tqdm

project_dir = Path(__file__).resolve().parent
sys.path.append(str(project_dir))

from project.config import DOCUMENTS_DIR
import storage_setup

DUCKDB_FILE = project_dir / "curator_workspace.duckdb"

def get_db_conn():
    """Gets a direct database connection to the DuckDB workspace."""
    try:
        return duckdb.connect(database=str(DUCKDB_FILE), read_only=True)
    except Exception as e:
        print(f"[FATAL] Could not connect to DuckDB: {e}")
        sys.exit(1)

def bake_sqlite_database(output_path_str: str):
    """
    Reads all processed data from the DuckDB workspace and writes it into a
    new, clean, and portable SQLite database file for the web application.
    """
    output_path = Path(output_path_str).resolve()
    print(f"\n--- Starting bake-down to SQLite at '{output_path}' ---")
    
    # --- START OF MODIFICATION ---
    TABLES_TO_TRANSFER = [
        'documents', 'document_metadata', 'email_metadata', 'catalogs', 
        'document_catalogs', 'content_index', 'entities', 'entity_appearances', 
        'browse_cache', 'entity_relationships', 'super_embedding_chunks',
        'srt_cues' # Added srt_cues to the list of tables to transfer
    ]
    # --- END OF MODIFICATION ---
    CHUNK_SIZE = 100000

    if output_path.exists():
        output_path.unlink()
        print("[OK]   Removed existing SQLite database.")
    
    try:
        storage_setup.create_unified_index(output_path, is_bake_operation=True)
        print("[OK]   Created fresh SQLite schema.")
    except Exception as e:
        print(f"[FATAL] Could not create SQLite schema: {e}")
        return
    
    duck_conn, sqlite_conn = None, None
    try:
        duck_conn = get_db_conn()
        sqlite_conn = sqlite3.connect(output_path)
        print("[OK]   Connected to both databases.")

        for table in TABLES_TO_TRANSFER:
            print(f"  [INFO] Processing table: '{table}'...")
            try:
                row_count_result = duck_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                total_rows = row_count_result[0] if row_count_result else 0
                
                if total_rows == 0:
                    print(f"    [SKIP] Table is empty.")
                    continue
                
                print(f"    [INFO] Found {total_rows} rows to transfer...")
                
                stream_result = duck_conn.execute(f"SELECT * FROM {table}").fetch_record_batch(CHUNK_SIZE)
                column_names = stream_result.schema.names

                for chunk in tqdm(stream_result, total=(total_rows // CHUNK_SIZE) + 1, desc=f"    Transferring '{table}'"):
                    df = chunk.to_pandas()
                    df.columns = column_names
                    df.to_sql(table, sqlite_conn, if_exists='append', index=False)

            except duckdb.CatalogException:
                print(f"    [WARN] Table '{table}' not found in DuckDB. Skipping.")
            except Exception as e:
                print(f"    [FAIL] Error transferring table '{table}': {e}")
                raise

        sqlite_conn.commit()
        print(f"\n[SUCCESS] Bake-down complete! Database is ready at: {output_path}")

    except Exception as e:
        print(f"\n[FATAL] Bake-down failed: {e}")
        if sqlite_conn:
            sqlite_conn.rollback()
    finally:
        if duck_conn:
            duck_conn.close()
        if sqlite_conn:
            sqlite_conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Redleaf Curator Data Manager (DuckDB).")
    subparsers = parser.add_subparsers(dest='command', required=True)
    
    bake_parser = subparsers.add_parser('bake-sqlite', help="Bake data to SQLite.")
    bake_parser.add_argument('--output', type=str, default='knowledge_base.db', help="Output SQLite file name.")
    
    args = parser.parse_args()

    if args.command == 'bake-sqlite':
        if not DUCKDB_FILE.exists(): 
            print(f"[ERROR] DuckDB source file not found at '{DUCKDB_FILE}'. Run the discovery and processing steps first.")
            sys.exit(1)
        bake_sqlite_database(args.output)