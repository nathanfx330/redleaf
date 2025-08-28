# --- File: ./storage_setup.py ---
import sqlite3
from pathlib import Path

DATABASE_FILE = "knowledge_base.db"

def create_unified_index(db_path):
    """
    Creates the complete database schema for the Redleaf Engine.
    This function establishes the "Unified Index" in a single SQLite file.
    """
    print(f"--- Setting up the Unified Index at {db_path} ---")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # --- Set up essential PRAGMA for performance and integrity ---
    cursor.execute("PRAGMA foreign_keys = ON;")
    cursor.execute("PRAGMA journal_mode = WAL;")

    # === NEW: 0. Application Settings Table ===
    print("Creating App Settings table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    # Set default values for settings
    cursor.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('max_workers', '2');")
    cursor.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('html_parsing_mode', 'generic');")
    # --- ADDED: Default setting for GPU usage ---
    cursor.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('use_gpu', 'false');")


    # === 1. Document Registry ===
    print("Creating Document Registry...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY,
            relative_path TEXT NOT NULL UNIQUE,
            file_hash TEXT NOT NULL,
            file_type TEXT NOT NULL,
            status TEXT NOT NULL,
            status_message TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed_at TIMESTAMP,
            file_modified_at TIMESTAMP,
            color TEXT,
            page_count INTEGER,
            file_size_bytes INTEGER,
            duration_seconds INTEGER,
            linked_audio_path TEXT,
            linked_video_path TEXT,
            linked_audio_url TEXT,
            last_audio_position REAL DEFAULT 0.0,
            -- === START OF CHANGE ===
            audio_offset_seconds REAL DEFAULT 0.0,
            -- === END OF CHANGE ===
            last_pdf_zoom REAL,
            last_pdf_page INTEGER
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_doc_status ON documents (status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_doc_file_type ON documents (file_type);")


    # === 2. Content Index (Full-Text Search) ===
    print("Creating Content Index (FTS5)...")
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS content_index USING fts5(
            doc_id UNINDEXED, page_number, page_content,
            tokenize = 'porter unicode61'
        );
    """)


    # === 3. Metadata Index (Extracted Entities) ===
    print("Creating Metadata Index...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY,
            text TEXT NOT NULL,
            label TEXT NOT NULL,
            UNIQUE(text, label)
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_entity_label ON entities (label);")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS entity_appearances (
            doc_id INTEGER NOT NULL,
            entity_id INTEGER NOT NULL,
            page_number INTEGER NOT NULL,
            FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE,
            FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE,
            PRIMARY KEY (doc_id, entity_id, page_number)
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_appearance_entity_id ON entity_appearances (entity_id);")


    # === 4. Curation, User, and Tagging Layer ===
    print("Creating Curation, User, and Tagging Layer...")
    print("Creating Users table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL, -- 'admin' or 'user'
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    print("Creating Invitation Tokens table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS invitation_tokens (
            id INTEGER PRIMARY KEY,
            token_value TEXT NOT NULL UNIQUE,
            created_by_user_id INTEGER NOT NULL,
            claimed_by_user_id INTEGER UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            claimed_at TIMESTAMP,
            FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (claimed_by_user_id) REFERENCES users(id) ON DELETE SET NULL
        );
    """)
    print("Creating Catalogs table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS catalogs (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            catalog_type TEXT NOT NULL DEFAULT 'user', -- 'user', 'podcast', 'favorites'
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_catalog_type ON catalogs (catalog_type);")

    try:
        cursor.execute(
            "INSERT INTO catalogs (name, description, catalog_type) VALUES (?, ?, ?)",
            ('⭐ Favorites', 'Documents you have marked as a favorite.', 'favorites')
        )
        print("Created default '⭐ Favorites' catalog.")
    except sqlite3.IntegrityError:
        print("'⭐ Favorites' catalog already exists.")

    print("Creating Document-Catalogs link table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS document_catalogs (
            doc_id INTEGER NOT NULL,
            catalog_id INTEGER NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE,
            FOREIGN KEY (catalog_id) REFERENCES catalogs(id) ON DELETE CASCADE,
            PRIMARY KEY (doc_id, catalog_id)
        );
    """)
    print("Creating Document Curation (Private Notes) table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS document_curation (
            doc_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            note TEXT,
            updated_at TIMESTAMP,
            PRIMARY KEY (doc_id, user_id),
            FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
    """)
    print("Creating Document Comments table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS document_comments (
            id INTEGER PRIMARY KEY,
            doc_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            comment_text TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
    """)
    print("Creating Tags table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE
        );
    """)
    print("Creating Document-Tags link table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS document_tags (
            doc_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE,
            PRIMARY KEY (doc_id, tag_id)
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_doctags_tag_id ON document_tags (tag_id);")


    # === 5. Aggregated View Cache ===
    print("Creating Aggregated View Cache...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS browse_cache (
            entity_id INTEGER PRIMARY KEY,
            entity_text TEXT NOT NULL,
            entity_label TEXT NOT NULL,
            document_count INTEGER NOT NULL,
            appearance_count INTEGER NOT NULL,
            FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cache_label_count ON browse_cache (entity_label, document_count DESC);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cache_label_text ON browse_cache (entity_label, entity_text COLLATE NOCASE);")


    # === 6. Entity Relationship Graph ===
    print("Creating Entity Relationship Graph...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS entity_relationships (
            id INTEGER PRIMARY KEY,
            subject_entity_id INTEGER NOT NULL,
            object_entity_id INTEGER NOT NULL,
            relationship_phrase TEXT NOT NULL,
            doc_id INTEGER NOT NULL,
            page_number INTEGER NOT NULL,
            FOREIGN KEY (subject_entity_id) REFERENCES entities(id) ON DELETE CASCADE,
            FOREIGN KEY (object_entity_id) REFERENCES entities(id) ON DELETE CASCADE,
            FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_rel_subject ON entity_relationships (subject_entity_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_rel_object ON entity_relationships (object_entity_id);")
    
    # === NEW: 6b. SRT Cue Index ===
    print("Creating SRT Cue Index...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS srt_cues (
            id INTEGER PRIMARY KEY,
            doc_id INTEGER NOT NULL,
            sequence INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            dialogue TEXT NOT NULL,
            FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE,
            UNIQUE(doc_id, sequence)
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_srt_cues_doc_id ON srt_cues (doc_id);")


    # === 7. Archived Relationships ===
    print("Creating Archived Relationships table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS archived_relationships (
            subject_entity_id INTEGER NOT NULL,
            object_entity_id INTEGER NOT NULL,
            relationship_phrase TEXT NOT NULL,
            archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (subject_entity_id, object_entity_id, relationship_phrase)
        );
    """)

    # === 8. Synthesis & Reporting Layer ===
    print("Creating Synthesis & Reporting Layer...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS document_metadata (
            doc_id INTEGER PRIMARY KEY,
            csl_json TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_by_user_id INTEGER,
            FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE,
            FOREIGN KEY (updated_by_user_id) REFERENCES users(id) ON DELETE SET NULL
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS synthesis_reports (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            content_json TEXT,
            owner_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP,
            FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS synthesis_citations (
            id INTEGER PRIMARY KEY,
            citation_instance_uuid TEXT NOT NULL UNIQUE,
            report_id INTEGER NOT NULL,
            source_doc_id INTEGER NOT NULL,
            page_number INTEGER,
            quoted_text TEXT,
            prefix TEXT,
            suffix TEXT,
            suppress_author BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (report_id) REFERENCES synthesis_reports(id) ON DELETE CASCADE,
            FOREIGN KEY (source_doc_id) REFERENCES documents(id) ON DELETE CASCADE
        );
    """)


    conn.commit()
    conn.close()
    print("--- Unified Index setup is complete. ---")


if __name__ == '__main__':
    db_file = Path(DATABASE_FILE)
    if db_file.exists():
        # A simple way to get confirmation before deleting a database.
        response = input(f"Are you sure you want to delete the existing database at '{db_file}'? [y/N] ")
        if response.lower() == 'y':
            db_file.unlink()
            print(f"Removed existing database file: {db_file}")
            create_unified_index(db_file)
            if db_file.exists():
                print(f"\nSuccessfully created '{db_file}'.")
            else:
                print(f"\nError: Database file '{db_file}' was not created.")
        else:
            print("Aborting. No changes made.")
    else:
        create_unified_index(db_file)
        if db_file.exists():
            print(f"\nSuccessfully created '{db_file}'.")
        else:
            print(f"\nError: Database file '{db_file}' was not created.")