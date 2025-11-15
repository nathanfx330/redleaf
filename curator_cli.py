# --- File: ./curator_cli.py ---
import argparse
import sys
import json
from pathlib import Path
import hashlib
import datetime
import os
from lxml import etree as ET
from email.utils import parsedate_to_datetime
from typing import Union
import duckdb
import subprocess
import requests

# Add the project directory to the Python path
project_dir = Path(__file__).resolve().parent
sys.path.append(str(project_dir))

from project.config import DOCUMENTS_DIR
import curator_pipeline_v2 as pipeline

# --- DuckDB Configuration ---
DUCKDB_FILE = project_dir / "curator_workspace.duckdb"

def get_db_conn():
    """Gets a direct database connection to the DuckDB file."""
    try:
        conn = duckdb.connect(database=str(DUCKDB_FILE))
        conn.execute("INSTALL fts;")
        conn.execute("LOAD fts;")
        return conn
    except Exception as e:
        print(f"[FATAL] Could not connect to DuckDB: {e}"); sys.exit(1)

def setup_duckdb_schema():
    """Creates the Redleaf schema in the DuckDB database."""
    print(f"--- Setting up or verifying DuckDB schema at {DUCKDB_FILE} ---")
    conn = get_db_conn()
    
    # Base tables
    conn.execute("""
        SET TimeZone='UTC';
        CREATE TABLE IF NOT EXISTS documents (id BIGINT PRIMARY KEY, relative_path VARCHAR NOT NULL UNIQUE, file_hash VARCHAR NOT NULL, file_type VARCHAR NOT NULL, status VARCHAR NOT NULL, status_message VARCHAR, added_at TIMESTAMP WITH TIME ZONE DEFAULT current_timestamp, processed_at TIMESTAMP WITH TIME ZONE, file_modified_at TIMESTAMP WITH TIME ZONE, color VARCHAR, page_count INTEGER, file_size_bytes BIGINT, duration_seconds INTEGER, linked_audio_path VARCHAR, linked_video_path VARCHAR, linked_audio_url VARCHAR, last_audio_position DOUBLE DEFAULT 0.0, audio_offset_seconds DOUBLE DEFAULT 0.0, last_pdf_zoom DOUBLE, last_pdf_page INTEGER);
        CREATE TABLE IF NOT EXISTS document_metadata (doc_id BIGINT PRIMARY KEY, csl_json VARCHAR, last_updated TIMESTAMP WITH TIME ZONE DEFAULT current_timestamp, updated_by_user_id BIGINT, FOREIGN KEY (doc_id) REFERENCES documents(id));
        CREATE TABLE IF NOT EXISTS email_metadata ( doc_id BIGINT PRIMARY KEY, from_address VARCHAR, to_addresses VARCHAR, cc_addresses VARCHAR, subject VARCHAR, sent_at TIMESTAMP WITH TIME ZONE, FOREIGN KEY (doc_id) REFERENCES documents(id) );
        CREATE TABLE IF NOT EXISTS catalogs (id BIGINT PRIMARY KEY, name VARCHAR NOT NULL UNIQUE, description VARCHAR, catalog_type VARCHAR NOT NULL DEFAULT 'user', created_at TIMESTAMP WITH TIME ZONE DEFAULT current_timestamp);
        CREATE TABLE IF NOT EXISTS document_catalogs (doc_id BIGINT NOT NULL, catalog_id BIGINT NOT NULL, added_at TIMESTAMP WITH TIME ZONE DEFAULT current_timestamp, PRIMARY KEY (doc_id, catalog_id), FOREIGN KEY (doc_id) REFERENCES documents(id), FOREIGN KEY (catalog_id) REFERENCES catalogs(id));
        CREATE TABLE IF NOT EXISTS entities (id BIGINT PRIMARY KEY, text VARCHAR NOT NULL, label VARCHAR NOT NULL, UNIQUE(text, label));
        CREATE TABLE IF NOT EXISTS entity_appearances (doc_id BIGINT NOT NULL, entity_id BIGINT NOT NULL, page_number INTEGER NOT NULL, PRIMARY KEY (doc_id, entity_id, page_number), FOREIGN KEY (doc_id) REFERENCES documents(id), FOREIGN KEY (entity_id) REFERENCES entities(id));
        CREATE TABLE IF NOT EXISTS entity_relationships (id BIGINT PRIMARY KEY, subject_entity_id BIGINT NOT NULL, object_entity_id BIGINT NOT NULL, relationship_phrase VARCHAR NOT NULL, doc_id BIGINT NOT NULL, page_number INTEGER NOT NULL, FOREIGN KEY (subject_entity_id) REFERENCES entities(id), FOREIGN KEY (object_entity_id) REFERENCES entities(id), FOREIGN KEY (doc_id) REFERENCES documents(id));
        CREATE TABLE IF NOT EXISTS browse_cache (entity_id BIGINT PRIMARY KEY, entity_text VARCHAR NOT NULL, entity_label VARCHAR NOT NULL, document_count BIGINT NOT NULL, appearance_count BIGINT NOT NULL, FOREIGN KEY (entity_id) REFERENCES entities(id));
        CREATE TABLE IF NOT EXISTS super_embedding_chunks ( id BIGINT PRIMARY KEY, doc_id BIGINT NOT NULL, page_number INTEGER NOT NULL, entity_id BIGINT NOT NULL, chunk_text VARCHAR NOT NULL, embedding BLOB NOT NULL, FOREIGN KEY (doc_id) REFERENCES documents(id), FOREIGN KEY (entity_id) REFERENCES entities(id) );
        
        -- START OF MODIFICATION --
        CREATE TABLE IF NOT EXISTS srt_cues (id BIGINT PRIMARY KEY, doc_id BIGINT NOT NULL, sequence INTEGER NOT NULL, timestamp VARCHAR NOT NULL, dialogue VARCHAR NOT NULL, UNIQUE(doc_id, sequence));
        -- END OF MODIFICATION --
    """)
    
    # Staging tables for the new pipeline
    pipeline.create_staging_tables(conn)

    print("Creating Full-Text Search index (on final table)..."); 
    conn.execute("CREATE TABLE IF NOT EXISTS content_index (doc_id BIGINT, page_number INTEGER, page_content VARCHAR);")
    conn.execute("PRAGMA create_fts_index('content_index', 'doc_id', 'page_number', 'page_content', overwrite=true);")

    print("Creating sequences..."); 
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_documents_id START 1;"); 
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_catalogs_id START 1;"); 
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_entities_id START 1;");
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_entity_relationships_id START 1;");
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_super_embedding_chunks_id START 1;")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_srt_cues_id START 1;") # <-- ADDED

    print("--- Schema setup complete. ---")
    conn.close()

def discover_documents():
    print(f"--- Scanning for documents in {DOCUMENTS_DIR} ---")
    db = get_db_conn()
    try:
        db_files = {row[0]: row[1] for row in db.execute("SELECT relative_path, file_hash FROM documents").fetchall()}
    except duckdb.CatalogException:
        db_files = {}
    registered_count = 0
    for pattern in ["*.pdf", "*.txt", "*.html", "*.srt", "*.eml"]:
        for file_path in DOCUMENTS_DIR.rglob(pattern):
            if not file_path.is_file(): continue
            rel_path_str = file_path.relative_to(DOCUMENTS_DIR).as_posix()
            stats = file_path.stat()
            hasher = hashlib.md5()
            with open(file_path, 'rb') as f:
                while chunk := f.read(65536): hasher.update(chunk)
            current_hash_str = hasher.hexdigest()
            if rel_path_str not in db_files or db_files.get(rel_path_str) != current_hash_str:
                print(f"Registering: {rel_path_str}")
                db.execute(
                    "INSERT INTO documents (id, relative_path, file_hash, file_type, status, file_size_bytes, file_modified_at) VALUES (nextval('seq_documents_id'), ?, ?, ?, 'New', ?, ?) ON CONFLICT(relative_path) DO UPDATE SET file_hash=excluded.file_hash, status='New', file_size_bytes=excluded.file_size_bytes, file_modified_at=excluded.file_modified_at", 
                    (rel_path_str, current_hash_str, file_path.suffix[1:].upper(), stats.st_size, datetime.datetime.fromtimestamp(stats.st_mtime))
                )
                registered_count += 1
    db.close()
    print(f"\n--- Discovery complete. Registered {registered_count} new/modified documents. ---")

def reset_document_status(status_filter: str = None, file_type_filter: str = None):
    print("\n--- Resetting Document Status to 'New' ---")
    where_clauses, params = [], []
    if status_filter:
        statuses = [s.strip().capitalize() for s in status_filter.split(',')]; where_clauses.append(f"status IN ({','.join(['?']*len(statuses))})"); params.extend(statuses); print(f"Targeting statuses: {', '.join(statuses)}")
    if file_type_filter:
        file_types = [t.strip().upper() for t in file_type_filter.split(',')]; where_clauses.append(f"file_type IN ({','.join(['?']*len(file_types))})"); params.extend(file_types); print(f"Targeting file types: {', '.join(file_types)}")
    if not where_clauses:
        if input("[WARNING] No filters specified. Reset ALL documents to 'New'? [y/N]: ").lower() != 'y': print("Operation cancelled."); return
    db = get_db_conn()
    try:
        sql = "UPDATE documents SET status = 'New', status_message = 'Reset by user'" + (" WHERE " + " AND ".join(where_clauses) if where_clauses else "")
        result = db.execute(sql, params); print(f"\n[SUCCESS] Reset {result.fetchone()[0]} document(s) to 'New' status.")
    except Exception as e: print(f"\n[FAIL] An error occurred: {e}")
    finally: db.close()

# ===================================================================
# --- UNIFIED MEDIA LINKING FUNCTIONS ---
# ===================================================================

def _fetch_archive_files(archive_id: str) -> Union[dict, None]:
    metadata_url = f"https://archive.org/metadata/{archive_id}"
    print(f"[INFO] Fetching file manifest from {metadata_url} ...")
    try:
        response = requests.get(metadata_url, timeout=15)
        response.raise_for_status()
        data = response.json()
        if not data or 'files' not in data:
            print("[FAIL] No 'files' key found in Archive.org metadata response.")
            return None
        file_map = {
            file_info['name']: f"https://archive.org/download/{archive_id}/{file_info['name']}"
            for file_info in data['files']
            if 'name' in file_info
        }
        print(f"[OK]   Found {len(file_map)} files in the '{archive_id}' archive.")
        return file_map
    except requests.exceptions.RequestException as e:
        print(f"[FAIL] Could not fetch data from Archive.org: {e}")
        return None
    except json.JSONDecodeError:
        print("[FAIL] Could not parse the JSON response from Archive.org.")
        return None

def _parse_podcast_xml_to_csl(item_element: ET.Element, channel_title_text: str = None, channel_author_text: str = None) -> tuple[dict, Union[str, None]]:
    csl_data = {}
    if item_element is None: return None, None
    namespaces = {'itunes': 'http://www.itunes.com/dtds/podcast-1.0.dtd'}
    title_element = item_element.find('itunes:title', namespaces) or item_element.find('title')
    if title_element is not None and title_element.text: csl_data['title'] = title_element.text.strip()
    if channel_title_text: csl_data['container-title'] = channel_title_text.strip()
    author_tag = item_element.find('itunes:author', namespaces)
    author_text = (author_tag.text.strip() if author_tag is not None and author_tag.text else channel_author_text.strip() if channel_author_text else None)
    if author_text: csl_data['author'] = [{'literal': author_text}]
    pub_date_tag = item_element.find('pubDate')
    if pub_date_tag is not None and pub_date_tag.text:
        try:
            dt = parsedate_to_datetime(pub_date_tag.text)
            csl_data['issued'] = {'date-parts': [[dt.year, dt.month, dt.day]]}
        except (ValueError, TypeError): pass
    link_tag = item_element.find('link')
    if link_tag is not None and link_tag.text: csl_data['URL'] = link_tag.text.strip()
    enclosure_url = None
    enclosure_tag = item_element.find('enclosure')
    if enclosure_tag is not None and enclosure_tag.get('url'): enclosure_url = enclosure_tag.get('url').strip()
    csl_data['type'] = 'interview'
    return csl_data, enclosure_url

def find_xml_matches_for_doc(doc_relative_path: str):
    target_srt_basename = Path(doc_relative_path).stem
    matches = []
    all_xml_files = list(DOCUMENTS_DIR.rglob('*.xml'))
    parser = ET.XMLParser(recover=True)
    for xml_path in all_xml_files:
        try:
            tree = ET.parse(str(xml_path), parser=parser)
            channel_title = tree.findtext('channel/title', "")
            namespaces = {'itunes': 'http://www.itunes.com/dtds/podcast-1.0.dtd'}
            channel_author = tree.findtext('channel/itunes:author', "", namespaces=namespaces)
            for item in tree.findall('channel/item'):
                is_match, enclosure_url = False, None
                enclosure_tag = item.find('enclosure')
                if enclosure_tag is not None and enclosure_tag.get('url'):
                    enclosure_url = enclosure_tag.get('url')
                    url_filename = enclosure_url.split('/')[-1].split('?')[0]
                    if Path(url_filename).stem == target_srt_basename: is_match = True
                if not is_match:
                    title_element = item.find('itunes:title', namespaces) or item.find('title')
                    if title_element is not None and title_element.text and target_srt_basename in title_element.text: is_match = True
                if is_match:
                    preview_data, _ = _parse_podcast_xml_to_csl(item, channel_title, channel_author)
                    if not preview_data: continue
                    guid_element = item.find('guid')
                    hash_content = (guid_element.text if guid_element is not None and guid_element.text else preview_data.get('title', ''))
                    item_hash = hashlib.md5(hash_content.encode()).hexdigest()
                    matches.append({'xml_path': str(xml_path.relative_to(DOCUMENTS_DIR).as_posix()),'item_hash': item_hash,'csl_data': preview_data, 'enclosure_url': enclosure_url,'confidence': 'high' if is_match and enclosure_url and Path(enclosure_url.split('/')[-1].split('?')[0]).stem == target_srt_basename else 'low'})
        except ET.XMLSyntaxError: continue
    return matches

def link_podcast_metadata():
    db = get_db_conn()
    try:
        docs_to_process = db.execute("""
            SELECT d.id, d.relative_path
            FROM documents d
            LEFT JOIN document_metadata dm ON d.id = dm.doc_id
            WHERE d.file_type = 'SRT'
              AND dm.csl_json IS NULL
              AND d.linked_audio_path IS NULL
              AND d.linked_video_path IS NULL
            ORDER BY d.relative_path
        """).fetchall()

        if not docs_to_process:
            print("\n--- No unprocessed SRT documents found that need metadata. Nothing to do. ---")
            return

        print(f"\n--- Found {len(docs_to_process)} SRTs to process for podcast metadata linking. ---")
        counts = {'success': 0, 'no_match': 0, 'ambiguous': 0, 'no_enclosure': 0}

        for doc_id, relative_path in docs_to_process:
            print(f"\n[INFO] Processing: {relative_path} (Doc ID: {doc_id})")
            matches = find_xml_matches_for_doc(relative_path)

            if not matches:
                counts['no_match'] += 1; print("  [FAIL] No potential XML match found."); continue

            high_confidence = [m for m in matches if m['confidence'] == 'high']
            best_match = None
            if len(high_confidence) == 1:
                best_match = high_confidence[0]; print("  [OK]   Found one high-confidence XML match.")
            elif len(high_confidence) > 1:
                counts['ambiguous'] += 1; print(f"  [SKIP] Ambiguous: Found {len(high_confidence)} high-confidence matches."); continue
            elif len(matches) == 1:
                best_match = matches[0]; print("  [OK]   Found one low-confidence (title-based) XML match.")
            else:
                counts['ambiguous'] += 1; print(f"  [SKIP] Ambiguous: Found {len(matches)} low-confidence matches."); continue

            if not best_match: continue
            csl_data, enclosure_url = best_match.get('csl_data'), best_match.get('enclosure_url')
            if not csl_data: print("  [FAIL] Matched item could not be parsed into CSL data."); continue
            
            if not enclosure_url:
                counts['no_enclosure'] += 1; print("  [SKIP] Matched XML item has no <enclosure> media URL. Saving metadata only.")

            csl_data['id'] = f"doc-{doc_id}"; csl_json_text = json.dumps(csl_data, indent=2); podcast_name = csl_data.get('container-title', '').strip()
            
            db.execute("BEGIN TRANSACTION;")
            db.execute("INSERT INTO document_metadata (doc_id, csl_json) VALUES (?, ?) ON CONFLICT(doc_id) DO UPDATE SET csl_json=excluded.csl_json;", (doc_id, csl_json_text))
            if enclosure_url:
                db.execute("UPDATE documents SET linked_audio_url = ? WHERE id = ?", (enclosure_url, doc_id))
            if podcast_name:
                catalog_row = db.execute("SELECT id FROM catalogs WHERE name = ? AND catalog_type = 'podcast'", (podcast_name,)).fetchone()
                if catalog_row:
                    catalog_id = catalog_row[0]
                else:
                    catalog_id = db.execute("INSERT INTO catalogs (id, name, description, catalog_type) VALUES (nextval('seq_catalogs_id'), ?, ?, 'podcast') RETURNING id", (podcast_name, f"Automatically generated for '{podcast_name}' podcast.")).fetchone()[0]
                db.execute("INSERT INTO document_catalogs (doc_id, catalog_id) VALUES (?, ?) ON CONFLICT DO NOTHING", (doc_id, catalog_id))
            db.execute("COMMIT;")
            
            counts['success'] += 1
            print(f"  [SUCCESS] Linked metadata and media from '{best_match['xml_path']}'.")

        print("\n--- Podcast Metadata Linking Summary ---")
        print(f"  Successfully linked:    {counts['success']}")
        print(f"  Skipped (ambiguous):    {counts['ambiguous']}")
        print(f"  No XML match found:     {counts['no_match']}")
        print(f"  XML match had no audio: {counts['no_enclosure']}")
        print("----------------------------------------")

    except Exception as e: print(f"\n[FAIL] A database error occurred: {e}")
    finally: db.close()

def link_archive_org(archive_id: str):
    archive_file_map = _fetch_archive_files(archive_id)
    if not archive_file_map: print("\n--- Operation cancelled due to failure fetching archive data. ---"); return

    db = get_db_conn()
    try:
        docs_to_process = db.execute("SELECT id, relative_path FROM documents WHERE file_type = 'SRT' ORDER BY relative_path").fetchall()
        if not docs_to_process: print("\n--- No SRT documents found. ---"); return
            
        print(f"\n--- Found {len(docs_to_process)} total SRTs to check. ---")
        if input(f"This will attempt to link them to '{archive_id}' and OVERWRITE existing media links.\nAre you sure? [y/N]: ").lower() != 'y': print("Operation cancelled."); return

        counts = {'success': 0, 'no_xml_match': 0, 'no_enclosure': 0, 'not_in_archive': 0, 'skipped': 0}
        for doc_id, relative_path in docs_to_process:
            print(f"\n[INFO] Processing: {relative_path} (Doc ID: {doc_id})")
            matches = find_xml_matches_for_doc(relative_path)
            if not matches: counts['no_xml_match'] += 1; print("  [SKIP] No XML match found."); continue

            high_confidence = [m for m in matches if m['confidence'] == 'high']
            best_match = None
            if len(high_confidence) == 1: best_match = high_confidence[0]; print("  [OK]   Found one high-confidence match.")
            elif len(high_confidence) > 1: counts['skipped'] += 1; print(f"  [SKIP] Ambiguous: Found {len(high_confidence)} high-confidence matches."); continue
            elif len(matches) == 1: best_match = matches[0]; print("  [OK]   Found one low-confidence match.")
            else: counts['skipped'] += 1; print(f"  [SKIP] Ambiguous: Found {len(matches)} low-confidence matches."); continue

            if not best_match: continue
            enclosure_url = best_match.get('enclosure_url')
            if not enclosure_url: counts['no_enclosure'] += 1; print("  [SKIP] Matched XML has no <enclosure> URL."); continue
            
            original_filename = enclosure_url.split('/')[-1].split('?')[0]
            if original_filename in archive_file_map:
                archive_url = archive_file_map[original_filename]
                db.execute("UPDATE documents SET linked_audio_url = ?, linked_audio_path = NULL, linked_video_path = NULL WHERE id = ?", (archive_url, doc_id))
                counts['success'] += 1; print(f"  [SUCCESS] Linked to: {archive_url}")
            else:
                counts['not_in_archive'] += 1; print(f"  [FAIL] Filename '{original_filename}' not in '{archive_id}' archive.")

        print("\n--- Archive.org Linking Summary ---")
        print(f"  Successfully linked:    {counts['success']}")
        print(f"  Not in Archive.org:     {counts['not_in_archive']}")
        print(f"  Skipped (ambiguous):    {counts['skipped']}")
        print(f"  No XML match found:     {counts['no_xml_match']}")
        print(f"  XML match had no audio: {counts['no_enclosure']}")
        print("---------------------------------")
    except Exception as e: print(f"\n[FAIL] An error occurred: {e}")
    finally: db.close()

def unpodcast_documents():
    if input("[WARNING] This will remove ALL bibliographic metadata and media links from ALL SRT documents, and delete ALL 'podcast' collections.\nThis is a destructive action. Are you sure? [y/N]: ").lower() != 'y': print("Operation cancelled."); return
    db = get_db_conn()
    try:
        print("Resetting SRT media links..."); db.execute("UPDATE documents SET linked_audio_path=NULL, linked_video_path=NULL, linked_audio_url=NULL WHERE file_type='SRT'")
        print("Deleting bibliographic metadata from SRTs..."); db.execute("DELETE FROM document_metadata WHERE doc_id IN (SELECT id FROM documents WHERE file_type='SRT')")
        print("Deleting 'podcast' collections..."); db.execute("DELETE FROM catalogs WHERE catalog_type='podcast'")
        print("\n--- All SRT documents have been reset. ---")
    except Exception as e: print(f"\n[FAIL] An error occurred: {e}")
    finally: db.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Redleaf Curator CLI with DuckDB.")
    subparsers = parser.add_subparsers(dest='command', required=True, help='Available commands')
    subparsers.add_parser('init-db', help="Initializes the DuckDB database and all tables.")
    subparsers.add_parser('discover-docs', help="[Step 1] Scans for documents and registers them.")
    
    proc_parser = subparsers.add_parser('process-docs', help="[Step 2] Runs the high-throughput processing pipeline.")
    proc_subparsers = proc_parser.add_subparsers(dest='phase', required=True, help='Pipeline phase to run')
    run_all_parser = proc_subparsers.add_parser('run-all', help='Runs all processing phases in sequence (extract, nlp, embed, finalize).')
    run_all_parser.add_argument('-w', '--workers', type=int, default=os.cpu_count() or 2, help="Number of parallel workers for each phase.")
    run_all_parser.add_argument('--batch-size', type=int, default=128, help="Number of items to send to the embedding model at once.")
    run_all_parser.add_argument('--gpu', action='store_true', help="Enable GPU acceleration for embedding phase.")
    extract_parser = proc_subparsers.add_parser('extract', help='Phase 1: Extracts clean text from all new documents.')
    extract_parser.add_argument('-w', '--workers', type=int, default=os.cpu_count() or 2, help="Number of parallel workers.")
    nlp_parser = proc_subparsers.add_parser('nlp', help='Phase 2: Performs spaCy NLP analysis on extracted text.')
    nlp_parser.add_argument('-w', '--workers', type=int, default=os.cpu_count() or 2, help="Number of parallel workers.")
    embed_parser = proc_subparsers.add_parser('embed', help='Phase 3: Generates AI embeddings for all chunks in batches.')
    embed_parser.add_argument('-w', '--workers', type=int, default=os.cpu_count() or 2, help="Number of parallel workers.")
    embed_parser.add_argument('--batch-size', type=int, default=128, help="Number of items to send to the embedding model at once.")
    embed_parser.add_argument('--gpu', action='store_true', help="Enable GPU acceleration for embedding.")
    proc_subparsers.add_parser('finalize', help='Phase 4: Commits all staged data to the final tables.')
    proc_subparsers.add_parser('cleanup', help='Utility: Removes all temporary staging tables.')
    
    reset_parser = subparsers.add_parser('reset-docs', help="[Utility] Resets document statuses to 'New'.")
    reset_parser.add_argument('--status', type=str, help="Comma-separated list of statuses to reset (e.g., 'Error,Indexing')")
    reset_parser.add_argument('--type', type=str, dest='file_type', help="Comma-separated list of file types to reset (e.g., 'EML,PDF')")
    
    link_parser = subparsers.add_parser('link-media', help="[Optional] Tools for linking media and metadata to SRTs.")
    link_subparsers = link_parser.add_subparsers(dest='link_command', required=True)
    link_subparsers.add_parser('podcast-meta', help="[RECOMMENDED FIRST] Links SRTs to metadata and web media from local XMLs.")
    link_subparsers.add_parser('unpodcast', help="[DESTRUCTIVE] Resets all SRTs by removing metadata, media links, and podcast collections.")
    link_archive_parser = link_subparsers.add_parser('archive-org', help="Links SRTs to audio files on an Archive.org item (overwrites existing links).")
    link_archive_parser.add_argument('archive_id', type=str, help="The identifier of the item on Archive.org (e.g., 'gaslit').")
    
    bake_parser = subparsers.add_parser('bake-sqlite', help="[Step 3] Bakes the final data to SQLite.")
    bake_parser.add_argument('--output', type=str, default='knowledge_base.db', help="Output SQLite file.")
    
    args = parser.parse_args()

    if args.command != 'init-db' and not DUCKDB_FILE.exists():
        print("[ERROR] DuckDB file not found. Run 'init-db' or 'python curator_reset.py' first."), sys.exit(1)
        
    if args.command == 'init-db':
        setup_duckdb_schema()
    elif args.command == 'discover-docs': 
        discover_documents()
    elif args.command == 'process-docs':
        if args.phase == 'run-all':
            pipeline.run_full_pipeline(workers=args.workers, batch_size=args.batch_size, use_gpu=args.gpu)
        elif args.phase == 'extract':
            pipeline.phase_extract_text(workers=args.workers)
        elif args.phase == 'nlp':
            pipeline.phase_nlp_analysis(workers=args.workers)
        elif args.phase == 'embed':
            pipeline.phase_generate_embeddings(workers=args.workers, batch_size=args.batch_size, use_gpu=args.gpu)
        elif args.phase == 'finalize':
            pipeline.phase_finalize_data()
        elif args.phase == 'cleanup':
            pipeline.cleanup_staging_tables()
    elif args.command == 'reset-docs':
        reset_document_status(status_filter=args.status, file_type_filter=args.file_type)
    
    elif args.command == 'link-media':
        if args.link_command == 'podcast-meta':
            link_podcast_metadata()
        elif args.link_command == 'archive-org':
            link_archive_org(args.archive_id)
        elif args.link_command == 'unpodcast':
            unpodcast_documents()

    elif args.command == 'bake-sqlite':
        subprocess.run(['python', 'curator_data_manager.py', 'bake-sqlite', '--output', args.output])