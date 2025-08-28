# --- File: ./project/export_import.py ---
import sqlite3
import json
import zipfile
import re
import hashlib
import shutil
import io
from pathlib import Path
from datetime import datetime

from .config import DATABASE_FILE, DOCUMENTS_DIR
from .background import task_queue

def export_knowledge_package():
    """
    Creates a shareable Redleaf knowledge package (.rklf file) in memory.
    """
    TABLES_TO_EXCLUDE = [
        'users', 'invitation_tokens', 'document_curation', 
        'synthesis_reports', 'synthesis_citations', 'app_settings',
        'browse_cache' 
    ]

    temp_dir = Path(DATABASE_FILE).parent / f"redleaf_export_temp_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    temp_dir.mkdir(exist_ok=True)
    
    temp_db_path = temp_dir / 'data.db'
    manifest_path = temp_dir / 'manifest.json'

    main_conn, source_conn, dest_conn = None, None, None

    try:
        main_conn = sqlite3.connect(f"file:{DATABASE_FILE}?mode=ro", uri=True)
        main_conn.row_factory = sqlite3.Row
        docs = main_conn.execute("SELECT relative_path, file_hash FROM documents").fetchall()
        manifest = { "redleaf_version": "2.0", "export_date": datetime.utcnow().isoformat(), "files": [dict(row) for row in docs] }
        with open(manifest_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2)
        main_conn.close()
        main_conn = None

        print("Creating a safe copy of the database...")
        source_conn = sqlite3.connect(DATABASE_FILE)
        source_conn.execute("PRAGMA journal_mode=WAL;") 
        dest_conn = sqlite3.connect(temp_db_path)
        source_conn.backup(dest_conn)
        source_conn.close()
        source_conn = None
        print("Database copy complete. Sanitizing...")

        temp_cursor = dest_conn.cursor()
        for table in TABLES_TO_EXCLUDE:
            temp_cursor.execute(f"DROP TABLE IF EXISTS {table};")
        temp_cursor.execute("VACUUM;")
        dest_conn.commit()
        
        print("Finalizing the export database...")
        dest_conn.execute("PRAGMA journal_mode = DELETE;")
        dest_conn.close()
        dest_conn = None

        print("Zipping package in memory...")
        memory_zip_file = io.BytesIO()
        with zipfile.ZipFile(memory_zip_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.write(manifest_path, arcname='manifest.json')
            zf.write(temp_db_path, arcname='data.db')
        
        memory_zip_file.seek(0)
        
        print("In-memory package created successfully.")
        return True, memory_zip_file

    except Exception as e:
        print(f"Error creating export package: {e}")
        return False, str(e)
    finally:
        if main_conn: main_conn.close()
        if source_conn: source_conn.close()
        if dest_conn: dest_conn.close()
        if temp_dir.exists():
            shutil.rmtree(temp_dir)

def import_knowledge_package(package_path: Path):
    """
    Validates and imports a Redleaf knowledge package into the main database.
    """
    temp_dir = package_path.parent / f"redleaf_import_temp_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    
    try:
        with zipfile.ZipFile(package_path, 'r') as zf:
            zf.extractall(temp_dir)
        
        manifest_path = temp_dir / 'manifest.json'
        import_db_path = temp_dir / 'data.db'

        if not manifest_path.exists() or not import_db_path.exists():
            raise ValueError("Invalid package: missing manifest.json or data.db.")

        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)

        print("Verifying document integrity...")
        errors = []
        for file_info in manifest.get('files', []):
            local_path = DOCUMENTS_DIR / file_info['relative_path']
            if not local_path.exists():
                errors.append(f"Missing file: {file_info['relative_path']}")
                continue
            
            hasher = hashlib.md5()
            with open(local_path, 'rb') as f:
                for chunk in iter(lambda: f.read(65536), b''):
                    hasher.update(chunk)
            local_hash = hasher.hexdigest()

            if local_hash != file_info['file_hash']:
                errors.append(f"Hash mismatch for: {file_info['relative_path']}")
        
        if errors:
            raise FileNotFoundError("Import failed. The following files are missing or have been modified:\n" + "\n".join(errors))

        print("Verification successful. Merging databases...")
        
        # === THIS IS THE FINAL FIX: Manual Two-Connection Merge ===
        conn_main = None
        conn_import = None
        try:
            conn_main = sqlite3.connect(DATABASE_FILE, timeout=30)
            conn_main.execute("PRAGMA journal_mode = WAL;")
            
            # Open the temporary import DB in read-only mode.
            conn_import = sqlite3.connect(f"file:{import_db_path}?mode=ro", uri=True)

            TABLES_TO_IMPORT = [ 'documents', 'entities', 'tags', 'catalogs', 'content_index', 'entity_appearances',
                                 'entity_relationships', 'document_tags', 'document_catalogs', 'document_metadata', 'srt_cues' ]

            conn_main.execute("BEGIN;")
            for table in TABLES_TO_IMPORT:
                print(f"Merging table: {table}...")
                
                # Read all data from the source table into memory
                read_cursor = conn_import.cursor()
                read_cursor.execute(f"SELECT * FROM {table}")
                rows = read_cursor.fetchall()
                
                if rows:
                    # Get column count to create the correct number of placeholders
                    num_columns = len(rows[0])
                    placeholders = ", ".join("?" * num_columns)
                    
                    # Insert all rows into the main database table
                    conn_main.executemany(f"INSERT OR IGNORE INTO {table} VALUES ({placeholders})", rows)

            conn_main.commit()
            print("Database merge complete.")
            
        except Exception as e:
            if conn_main: conn_main.rollback()
            raise RuntimeError(f"A database error occurred during the merge: {e}")
        finally:
            if conn_main: conn_main.close()
            if conn_import: conn_import.close()

        # Step 4: Trigger background tasks to update the UI
        task_queue.put(('discover', None))
        task_queue.put(('cache', None))
        print("Queued discovery and browse cache update.")
        return True, f"Import successful! {len(manifest.get('files',[]))} documents were merged into your knowledge base."
            
    except Exception as e:
        return False, str(e)
    finally:
        # Step 5: Cleanup
        if temp_dir.exists():
            shutil.rmtree(temp_dir)