# --- File: ./curator_data_manager.py (OPTIMIZED BAKE SCRIPT) ---
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

def apply_sqlite_optimizations(sqlite_conn):
    """
    Applies performance indexes and maintenance triggers directly to the SQLite DB.
    This replaces the need for a separate db_optimize.py script.
    """
    print(f"\n--- Applying SQLite Performance Optimizations ---")
    cursor = sqlite_conn.cursor()
    
    print("  [INFO] Installing maintenance triggers (Comments & Tags)...")
    # Comment Triggers
    cursor.execute("DROP TRIGGER IF EXISTS trg_comment_added")
    cursor.execute("""
        CREATE TRIGGER trg_comment_added AFTER INSERT ON document_comments
        BEGIN UPDATE documents SET cached_comment_count = cached_comment_count + 1 WHERE id = NEW.doc_id; END;
    """)

    cursor.execute("DROP TRIGGER IF EXISTS trg_comment_deleted")
    cursor.execute("""
        CREATE TRIGGER trg_comment_deleted AFTER DELETE ON document_comments
        BEGIN UPDATE documents SET cached_comment_count = MAX(0, cached_comment_count - 1) WHERE id = OLD.doc_id; END;
    """)

    # Tag Triggers
    cursor.execute("DROP TRIGGER IF EXISTS trg_tag_added")
    cursor.execute("""
        CREATE TRIGGER trg_tag_added AFTER INSERT ON document_tags
        BEGIN UPDATE documents SET cached_tag_count = cached_tag_count + 1 WHERE id = NEW.doc_id; END;
    """)

    cursor.execute("DROP TRIGGER IF EXISTS trg_tag_deleted")
    cursor.execute("""
        CREATE TRIGGER trg_tag_deleted AFTER DELETE ON document_tags
        BEGIN UPDATE documents SET cached_tag_count = MAX(0, cached_tag_count - 1) WHERE id = OLD.doc_id; END;
    """)

    print("  [INFO] Building high-performance indexes...")
    # Dashboard Indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_docs_processed_at ON documents(processed_at DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_docs_rel_path ON documents(relative_path COLLATE NOCASE)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_docs_file_type ON documents(file_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_curation_user_doc ON document_curation(doc_id, user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_comments_doc_lookup ON document_comments(doc_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tags_doc_lookup ON document_tags(doc_id)")

    # Discovery Indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cache_label_appearance ON browse_cache (entity_label, appearance_count DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cache_label_text_search ON browse_cache (entity_label, entity_text COLLATE NOCASE)")

    sqlite_conn.commit()
    print("  [OK]   Optimizations applied successfully.")

def bake_sqlite_database(output_path_str: str):
    """
    Reads all processed data from the DuckDB workspace and writes it into a
    new, clean, and portable SQLite database file for the web application.
    """
    output_path = Path(output_path_str).resolve()
    print(f"\n--- Starting bake-down to SQLite at '{output_path}' ---")
    
    TABLES_TO_TRANSFER = [
        'documents', 'document_metadata', 'email_metadata', 'catalogs', 
        'document_catalogs', 'content_index', 'entities', 'entity_appearances', 
        'browse_cache', 'entity_relationships', 'super_embedding_chunks',
        'srt_cues' 
    ]
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
        
        # Optimization: PRAGMA settings for bulk inserts
        sqlite_conn.execute("PRAGMA synchronous = OFF;")
        sqlite_conn.execute("PRAGMA journal_mode = MEMORY;")
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
        
        # --- NEW: Automatically apply indexes and triggers ---
        apply_sqlite_optimizations(sqlite_conn)
        
        # Restore safe PRAGMA settings after bulk operations
        sqlite_conn.execute("PRAGMA synchronous = NORMAL;")
        sqlite_conn.execute("PRAGMA journal_mode = WAL;")

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
    
    bake_parser = subparsers.add_parser('bake-sqlite', help="Bake data to SQLite and optimize indexes.")
    bake_parser.add_argument('--output', type=str, default='knowledge_base.db', help="Output SQLite file name.")
    
    args = parser.parse_args()

    if args.command == 'bake-sqlite':
        if not DUCKDB_FILE.exists(): 
            print(f"[ERROR] DuckDB source file not found at '{DUCKDB_FILE}'. Run the discovery and processing steps first.")
            sys.exit(1)
        bake_sqlite_database(args.output)