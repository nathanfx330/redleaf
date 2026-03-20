# --- File: ./db_optimize.py ---
import sqlite3
import sys
from pathlib import Path

# Import database path from your config to ensure consistency
try:
    from project.config import DATABASE_FILE
except ImportError:
    # Fallback if running outside the package context
    DATABASE_FILE = Path("knowledge_base.db")

def optimize_database():
    """
    Applies ALL schema optimizations to the SQLite database to handle large datasets (100k+ docs).
    
    1. Adds denormalized 'cached count' columns to the documents table.
    2. Installs SQLite Triggers to automatically keep these counts in sync.
    3. Backfills the counts based on existing data.
    4. Adds high-performance covering indexes for Documents (Dashboard).
    5. Adds high-performance covering indexes for Entities (Discovery).
    """
    
    db_path = Path(DATABASE_FILE)
    if not db_path.exists():
        print(f"[ERROR] Database not found at: {db_path}")
        return

    print(f"--- Starting Database Optimization on: {db_path} ---")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Enable Write-Ahead Logging for concurrency during this operation
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("BEGIN TRANSACTION;")

        # ==============================================================================
        # STEP 1: Add Read-Optimized Columns (Denormalization)
        # ==============================================================================
        print("[1/5] Checking and adding cached count columns...")
        
        # Get list of existing columns to avoid errors if re-running
        cursor.execute("PRAGMA table_info(documents)")
        existing_columns = [row[1] for row in cursor.fetchall()]

        columns_to_add = [
            ("cached_comment_count", "INTEGER DEFAULT 0"),
            ("cached_tag_count", "INTEGER DEFAULT 0")
        ]

        for col_name, col_type in columns_to_add:
            if col_name not in existing_columns:
                print(f"      + Adding column: {col_name}")
                cursor.execute(f"ALTER TABLE documents ADD COLUMN {col_name} {col_type}")
            else:
                print(f"      = Column '{col_name}' already exists. Skipping.")

        # ==============================================================================
        # STEP 2: Install Maintenance Triggers
        # ==============================================================================
        print("[2/5] Installing automatic maintenance triggers...")

        # --- Comment Triggers ---
        cursor.execute("DROP TRIGGER IF EXISTS trg_comment_added")
        cursor.execute("""
            CREATE TRIGGER trg_comment_added AFTER INSERT ON document_comments
            BEGIN
                UPDATE documents 
                SET cached_comment_count = cached_comment_count + 1 
                WHERE id = NEW.doc_id;
            END;
        """)

        cursor.execute("DROP TRIGGER IF EXISTS trg_comment_deleted")
        cursor.execute("""
            CREATE TRIGGER trg_comment_deleted AFTER DELETE ON document_comments
            BEGIN
                UPDATE documents 
                SET cached_comment_count = MAX(0, cached_comment_count - 1) 
                WHERE id = OLD.doc_id;
            END;
        """)

        # --- Tag Triggers ---
        cursor.execute("DROP TRIGGER IF EXISTS trg_tag_added")
        cursor.execute("""
            CREATE TRIGGER trg_tag_added AFTER INSERT ON document_tags
            BEGIN
                UPDATE documents 
                SET cached_tag_count = cached_tag_count + 1 
                WHERE id = NEW.doc_id;
            END;
        """)

        cursor.execute("DROP TRIGGER IF EXISTS trg_tag_deleted")
        cursor.execute("""
            CREATE TRIGGER trg_tag_deleted AFTER DELETE ON document_tags
            BEGIN
                UPDATE documents 
                SET cached_tag_count = MAX(0, cached_tag_count - 1) 
                WHERE id = OLD.doc_id;
            END;
        """)
        print("      + Triggers installed/updated successfully.")

        # ==============================================================================
        # STEP 3: Backfill / Recalculate Data
        # ==============================================================================
        print("[3/5] Recalculating statistics for existing documents...")
        print("      ...calculating comments (this may take a moment)...")
        cursor.execute("""
            UPDATE documents SET cached_comment_count = (
                SELECT COUNT(*) FROM document_comments WHERE document_comments.doc_id = documents.id
            )
        """)
        
        print("      ...calculating tags (this may take a moment)...")
        cursor.execute("""
            UPDATE documents SET cached_tag_count = (
                SELECT COUNT(*) FROM document_tags WHERE document_tags.doc_id = documents.id
            )
        """)

        # ==============================================================================
        # STEP 4: Create Dashboard Performance Indexes
        # ==============================================================================
        print("[4/5] Verifying Dashboard indexes...")
        
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_docs_processed_at ON documents(processed_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_docs_rel_path ON documents(relative_path COLLATE NOCASE)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_docs_file_type ON documents(file_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_curation_user_doc ON document_curation(doc_id, user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_comments_doc_lookup ON document_comments(doc_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tags_doc_lookup ON document_tags(doc_id)")

        # ==============================================================================
        # STEP 5: Create Discovery View Indexes (NEW)
        # ==============================================================================
        print("[5/5] Verifying Discovery View indexes...")
        
        # Optimized for the default Discovery sort (Most Mentions)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_cache_label_appearance 
            ON browse_cache (entity_label, appearance_count DESC)
        """)

        # Optimized for filtering/searching within an entity category
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_cache_label_text_search 
            ON browse_cache (entity_label, entity_text COLLATE NOCASE)
        """)

        conn.commit()
        print("\n[SUCCESS] Full Optimization Complete!")
        print("          - Dashboard is optimized (Cached Columns + Indexes)")
        print("          - Discovery is optimized (Covering Indexes)")

    except Exception as e:
        print(f"\n[FAIL] An error occurred during optimization: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    optimize_database()