# --- File: ./bulk_manage.py (FINAL, WITH ZIP COMPRESSION) ---
import argparse
import os
import sys
import json
from pathlib import Path
import hashlib
import subprocess
from lxml import etree as ET
from email.utils import parsedate_to_datetime
from typing import Union
import requests
from datetime import datetime
import zipfile  # <-- ADDED IMPORT

# Add the project directory to the Python path
project_dir = Path(__file__).resolve().parent
sys.path.append(str(project_dir))

from project import create_app
from project.database import get_db
from project.config import DOCUMENTS_DIR, DATABASE_FILE, INSTANCE_DIR

# --- Helper functions (No changes in this section) ---

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

# --- Main Command Functions (No changes until export_precomputed_state) ---

def link_podcast_metadata():
    # ... (This function remains unchanged)
    app = create_app()
    with app.app_context():
        db = get_db()
        docs_to_process = db.execute("""
            SELECT d.id, d.relative_path
            FROM documents d
            LEFT JOIN document_metadata dm ON d.id = dm.doc_id
            WHERE d.file_type = 'SRT'
              AND d.status = 'Indexed'
              AND dm.csl_json IS NULL
              AND d.linked_audio_path IS NULL
              AND d.linked_video_path IS NULL
              AND d.linked_audio_url IS NULL
            ORDER BY d.relative_path
        """).fetchall()

        if not docs_to_process:
            print("\n--- No unprocessed SRT documents found that need metadata. Nothing to do. ---")
            return

        print(f"\n--- Found {len(docs_to_process)} SRTs to process for podcast metadata linking. ---")
        counts = {'success': 0, 'no_match': 0, 'ambiguous': 0, 'no_enclosure': 0}

        for doc in docs_to_process:
            print(f"\n[INFO] Processing: {doc['relative_path']} (Doc ID: {doc['id']})")
            matches = find_xml_matches_for_doc(doc['relative_path'])

            if not matches:
                counts['no_match'] += 1
                print("  [FAIL] No potential XML match found.")
                continue

            high_confidence = [m for m in matches if m['confidence'] == 'high']
            best_match = None
            if len(high_confidence) == 1:
                best_match = high_confidence[0]
                print("  [OK]   Found one high-confidence XML match.")
            elif len(high_confidence) > 1:
                counts['ambiguous'] += 1
                print(f"  [SKIP] Ambiguous: Found {len(high_confidence)} high-confidence matches.")
                continue
            elif len(matches) == 1:
                best_match = matches[0]
                print("  [OK]   Found one low-confidence (title-based) XML match.")
            else:
                counts['ambiguous'] += 1
                print(f"  [SKIP] Ambiguous: Found {len(matches)} low-confidence matches.")
                continue

            if not best_match:
                continue

            csl_data = best_match.get('csl_data')
            enclosure_url = best_match.get('enclosure_url')

            if not csl_data:
                print("  [FAIL] Matched item could not be parsed into CSL data.")
                continue
            
            if not enclosure_url:
                counts['no_enclosure'] += 1
                print("  [SKIP] Matched XML item has no <enclosure> media URL. Saving metadata only.")

            try:
                csl_data['id'] = f"doc-{doc['id']}"
                csl_json_text = json.dumps(csl_data, indent=2)
                podcast_name = csl_data.get('container-title', '').strip()

                db.execute("BEGIN")
                db.execute("""
                    INSERT INTO document_metadata (doc_id, csl_json) VALUES (?, ?)
                    ON CONFLICT(doc_id) DO UPDATE SET csl_json=excluded.csl_json;
                """, (doc['id'], csl_json_text))

                if enclosure_url:
                    db.execute("UPDATE documents SET linked_audio_url = ? WHERE id = ?", (enclosure_url, doc['id']))

                if podcast_name:
                    catalog_row = db.execute("SELECT id FROM catalogs WHERE name = ? AND catalog_type = 'podcast'", (podcast_name,)).fetchone()
                    catalog_id = catalog_row['id'] if catalog_row else db.execute(
                        "INSERT INTO catalogs (name, description, catalog_type) VALUES (?, ?, 'podcast')",
                        (podcast_name, f"Automatically generated collection for the '{podcast_name}' podcast.")
                    ).lastrowid
                    db.execute("INSERT OR IGNORE INTO document_catalogs (doc_id, catalog_id) VALUES (?, ?)", (doc['id'], catalog_id))
                
                db.commit()
                counts['success'] += 1
                print(f"  [SUCCESS] Linked metadata and media from '{best_match['xml_path']}'.")
            except Exception as e:
                db.rollback()
                print(f"  [FAIL] A database error occurred: {e}")

        print("\n--- Podcast Metadata Linking Summary ---")
        print(f"  Successfully linked:    {counts['success']}")
        print(f"  Skipped (ambiguous):    {counts['ambiguous']}")
        print(f"  No XML match found:     {counts['no_match']}")
        print(f"  XML match had no audio: {counts['no_enclosure']}")
        print("----------------------------------------")

def link_local_audio():
    # ... (This function remains unchanged)
    app = create_app()
    with app.app_context():
        db = get_db()
        docs_to_check = db.execute("""
            SELECT id, relative_path FROM documents
            WHERE file_type = 'SRT'
              AND linked_audio_path IS NULL
              AND linked_video_path IS NULL
              AND linked_audio_url IS NULL
            ORDER BY relative_path
        """).fetchall()

        if not docs_to_check:
            print("\n--- No SRT documents found needing local media links. ---")
            return

        print(f"\n--- Found {len(docs_to_check)} SRTs to check for local media. ---")
        found_count = 0
        all_media_files = {p.relative_to(DOCUMENTS_DIR).as_posix() for p in DOCUMENTS_DIR.rglob('*') if p.suffix.lower() in ['.mp3', '.mp4']}

        for doc in docs_to_check:
            srt_path = Path(doc['relative_path'])
            target_stem = srt_path.stem
            
            preferred_mp3 = srt_path.with_suffix('.mp3').as_posix()
            preferred_mp4 = srt_path.with_suffix('.mp4').as_posix()

            found_path, media_type = None, None
            if preferred_mp4 in all_media_files:
                found_path, media_type = preferred_mp4, 'video'
            elif preferred_mp3 in all_media_files:
                found_path, media_type = preferred_mp3, 'audio'
            else:
                for media_file in all_media_files:
                    if Path(media_file).stem == target_stem:
                        found_path = media_file
                        media_type = 'video' if media_file.endswith('.mp4') else 'audio'
                        break

            if found_path:
                print(f"[OK] Found '{found_path}' for '{doc['relative_path']}'")
                if media_type == 'audio':
                    db.execute("UPDATE documents SET linked_audio_path = ? WHERE id = ?", (found_path, doc['id']))
                else:
                    db.execute("UPDATE documents SET linked_video_path = ? WHERE id = ?", (found_path, doc['id']))
                db.commit()
                found_count += 1
        
        print(f"\n--- Summary: Linked {found_count} SRTs to local media. ---")

def unpodcast_documents():
    # ... (This function remains unchanged)
    confirm = input("[WARNING] This will remove ALL bibliographic metadata and media links from ALL SRT documents.\nIt will also delete ALL 'podcast' type collections.\nThis is a destructive action. Are you sure? [y/N]: ")
    if confirm.lower() != 'y':
        print("Operation cancelled.")
        return
        
    app = create_app()
    with app.app_context():
        db = get_db()
        print("Resetting SRT media links...")
        db.execute("UPDATE documents SET linked_audio_path=NULL, linked_video_path=NULL, linked_audio_url=NULL WHERE file_type='SRT'")
        print("Deleting all bibliographic metadata from SRTs...")
        db.execute("DELETE FROM document_metadata WHERE doc_id IN (SELECT id FROM documents WHERE file_type='SRT')")
        print("Deleting all 'podcast' type collections...")
        db.execute("DELETE FROM catalogs WHERE catalog_type='podcast'")
        db.commit()
        print("\n--- All SRT documents have been reset. ---")

def link_archive_org(archive_id: str):
    # ... (This function remains unchanged)
    archive_file_map = _fetch_archive_files(archive_id)
    if not archive_file_map:
        print("\n--- Operation cancelled due to failure fetching archive data. ---")
        return

    app = create_app()
    with app.app_context():
        db = get_db()
        docs_to_process = db.execute("""
            SELECT id, relative_path FROM documents
            WHERE file_type = 'SRT'
            ORDER BY relative_path
        """).fetchall()
        
        if not docs_to_process:
            print("\n--- No SRT documents found in the database. Nothing to do. ---")
            return
            
        print(f"\n--- Found {len(docs_to_process)} total SRT documents to check. ---")
        confirm = input(f"This will attempt to link them to the '{archive_id}' archive and will OVERWRITE any existing media links (local or web).\nAre you sure you want to continue? [y/N]: ")
        if confirm.lower() != 'y':
            print("Operation cancelled.")
            return

        print(f"\n--- Starting link attempt for {len(docs_to_process)} SRTs... ---")
        counts = {'success': 0, 'no_xml_match': 0, 'no_enclosure': 0, 'not_in_archive': 0, 'skipped': 0}

        for doc in docs_to_process:
            print(f"\n[INFO] Processing: {doc['relative_path']} (Doc ID: {doc['id']})")
            matches = find_xml_matches_for_doc(doc['relative_path'])

            if not matches:
                counts['no_xml_match'] += 1
                print("  [SKIP] No potential XML match found for this SRT.")
                continue

            high_confidence = [m for m in matches if m['confidence'] == 'high']
            best_match = None
            if len(high_confidence) == 1:
                best_match = high_confidence[0]
                print("  [OK]   Found one high-confidence XML match.")
            elif len(high_confidence) > 1:
                counts['skipped'] += 1
                print(f"  [SKIP] Ambiguous: Found {len(high_confidence)} high-confidence matches.")
                continue
            elif len(matches) == 1:
                best_match = matches[0]
                print("  [OK]   Found one low-confidence (title-based) XML match.")
            else:
                counts['skipped'] += 1
                print(f"  [SKIP] Ambiguous: Found {len(matches)} low-confidence matches.")
                continue

            if not best_match:
                continue

            enclosure_url = best_match.get('enclosure_url')
            if not enclosure_url:
                counts['no_enclosure'] += 1
                print("  [SKIP] The matched XML item does not have an <enclosure> URL.")
                continue
            
            original_filename = enclosure_url.split('/')[-1].split('?')[0]
            
            if original_filename in archive_file_map:
                archive_url = archive_file_map[original_filename]
                db.execute(
                    "UPDATE documents SET linked_audio_url = ?, linked_audio_path = NULL, linked_video_path = NULL WHERE id = ?",
                    (archive_url, doc['id'])
                )
                db.commit()
                counts['success'] += 1
                print(f"  [SUCCESS] Linked to Archive.org URL: {archive_url}")
            else:
                counts['not_in_archive'] += 1
                print(f"  [FAIL] Filename '{original_filename}' not found in the '{archive_id}' archive.")

        print("\n--- Archive.org Linking Summary ---")
        print(f"  Successfully linked:    {counts['success']}")
        print(f"  Not in Archive.org:     {counts['not_in_archive']}")
        print(f"  Skipped (ambiguous):    {counts['skipped']}")
        print(f"  No XML match found:     {counts['no_xml_match']}")
        print(f"  XML match had no audio: {counts['no_enclosure']}")
        print("---------------------------------")

def export_contributions(username: str):
    # ... (This function remains unchanged)
    app = create_app()
    with app.app_context():
        db = get_db()
        user = db.execute("SELECT id, username FROM users WHERE username = ?", (username,)).fetchone()
        if not user:
            print(f"[FAIL] User '{username}' not found in the database.")
            return

        print(f"\n--- Exporting contributions for user: {user['username']} (ID: {user['id']}) ---")
        
        tags_query = """
            SELECT d.relative_path, t.name
            FROM document_tags dt
            JOIN tags t ON dt.tag_id = t.id
            JOIN documents d ON dt.doc_id = d.id
            ORDER BY d.relative_path, t.name;
        """
        user_tags = db.execute(tags_query).fetchall()
        print(f"Found {len(user_tags)} tag associations.")

        comments_query = """
            SELECT d.relative_path, dc.comment_text, dc.created_at
            FROM document_comments dc
            JOIN documents d ON dc.doc_id = d.id
            WHERE dc.user_id = ?
            ORDER BY d.relative_path, dc.created_at;
        """
        user_comments = db.execute(comments_query, (user['id'],)).fetchall()
        print(f"Found {len(user_comments)} comments.")
        
        notes_query = """
            SELECT d.relative_path, cur.note, cur.updated_at
            FROM document_curation cur
            JOIN documents d ON cur.doc_id = d.id
            WHERE cur.user_id = ? AND cur.note IS NOT NULL AND cur.note != ''
            ORDER BY d.relative_path;
        """
        user_notes = db.execute(notes_query, (user['id'],)).fetchall()
        print(f"Found {len(user_notes)} private notes.")
        
        contributions_by_doc = {}
        for row in user_tags:
            doc_path = row['relative_path']
            if doc_path not in contributions_by_doc: contributions_by_doc[doc_path] = {}
            if 'tags' not in contributions_by_doc[doc_path]: contributions_by_doc[doc_path]['tags'] = []
            contributions_by_doc[doc_path]['tags'].append(row['name'])

        for row in user_comments:
            doc_path = row['relative_path']
            if doc_path not in contributions_by_doc: contributions_by_doc[doc_path] = {}
            if 'comments' not in contributions_by_doc[doc_path]: contributions_by_doc[doc_path]['comments'] = []
            contributions_by_doc[doc_path]['comments'].append({'text': row['comment_text'], 'created_at': row['created_at'].isoformat()})

        for row in user_notes:
            doc_path = row['relative_path']
            if doc_path not in contributions_by_doc: contributions_by_doc[doc_path] = {}
            contributions_by_doc[doc_path]['private_note'] = {'text': row['note'], 'updated_at': row['updated_at'].isoformat()}

        output_data = {
            "exported_by": user['username'],
            "export_date_utc": datetime.utcnow().isoformat(),
            "redleaf_version": "2.1-contribution",
            "contributions": contributions_by_doc
        }

        filename = f"contribution-{user['username']}-{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
        output_path = Path.cwd() / filename
        
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(output_data, f, indent=2)
            print(f"\n[SUCCESS] Contributions exported successfully!")
            print(f"File saved to: {output_path.resolve()}")
        except IOError as e:
            print(f"\n[FAIL] Could not write to file: {e}")

# --- MODIFIED FUNCTION ---
def export_precomputed_state():
    """Dumps the public data to a compressed SQL zip file for precomputed distribution."""
    print("\n--- Exporting Precomputed State for Distribution ---")
    
    app = create_app()
    with app.app_context():
        db = get_db()
        user_count = db.execute("SELECT COUNT(id) FROM users").fetchone()[0]
        
        print("\n[WARNING] This process will create a public snapshot of your database.")
        print("To ensure no private data is included, the following tables will be WIPED from your CURRENT database before exporting:")
        print("  - users, invitation_tokens, document_curation, synthesis_reports, synthesis_citations")
        print(f"This will permanently delete all {user_count} user accounts and their associated private data from THIS instance.")
        
        confirm = input("This is a DESTRUCTIVE action. Are you absolutely sure you want to proceed? [y/N]: ")
        if confirm.lower() != 'y':
            print("Operation cancelled.")
            return

        print("\n[1/5] Wiping private data from the current database...")
        try:
            tables_to_wipe = [
                'users', 'invitation_tokens', 'document_curation', 
                'synthesis_reports', 'synthesis_citations'
            ]
            for table in tables_to_wipe:
                db.execute(f"DELETE FROM {table};")
            db.commit()
            print("  [OK]   Private data has been wiped.")
        except Exception as e:
            db.rollback()
            print(f"[FAIL] Could not wipe private data. Aborting. Error: {e}")
            return

    INSTANCE_DIR.mkdir(exist_ok=True)
    # --- START OF MODIFICATION ---
    output_zip_path = INSTANCE_DIR / "initial_state.sql.zip"
    marker_path = INSTANCE_DIR / "precomputed.marker"
    
    print(f"[2/5] Checking for existing database at '{DATABASE_FILE}'...")
    if not DATABASE_FILE.exists():
        print(f"[FAIL] Database file not found. Cannot export.")
        return

    print(f"[3/5] Dumping and compressing clean database to '{output_zip_path}'...")
    try:
        # Use subprocess.Popen to stream the output of sqlite3 .dump
        process = subprocess.Popen(['sqlite3', str(DATABASE_FILE), '.dump'], stdout=subprocess.PIPE)
        
        # Write the streamed output directly to a compressed file within the zip archive
        with zipfile.ZipFile(output_zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            with zf.open('initial_state.sql', 'w') as f_out:
                for line in iter(process.stdout.readline, b''):
                    f_out.write(line)
        
        process.wait() # Wait for the sqlite3 process to finish
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, process.args)
            
        print("  [OK]   Database dump and compression successful.")
    except (subprocess.CalledProcessError, FileNotFoundError, IOError) as e:
        print(f"[FAIL] Could not dump and compress database. Ensure 'sqlite3' is in your system's PATH.")
        print(f"       Error: {e}")
        if output_zip_path.exists():
            output_zip_path.unlink()
        return
    # --- END OF MODIFICATION ---

    print(f"[4/5] Creating precomputed mode marker file...")
    try:
        marker_path.touch()
        print(f"  [OK]   Marker file created at '{marker_path}'")
    except IOError as e:
        print(f"[FAIL] Could not create marker file: {e}")
        return

    print("\n--- Precomputed State Export Complete! ---")
    print("[5/5] IMPORTANT: Your local instance no longer has any user accounts.")
    print("You will need to create a new admin account the next time you run the app in normal (Curator) mode.")
    print("\nCommit the following files to your repository for distribution:")
    # --- MODIFIED PRINT STATEMENTS ---
    print(f"  - {output_zip_path.relative_to(project_dir.parent)}")
    print(f"  - {marker_path.relative_to(project_dir.parent)}")
    print("  - Your entire 'documents/' directory.")

# --- Main CLI setup (No changes in this section) ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Redleaf Engine Bulk Management Tool.")
    subparsers = parser.add_subparsers(dest='command', required=True, help='Available commands')

    parser_link_meta = subparsers.add_parser(
        'link-podcast-metadata',
        help="[RECOMMENDED] Links unprocessed SRTs to metadata AND web media from XMLs."
    )
    
    parser_link_audio = subparsers.add_parser(
        'link-local-audio',
        help="Scans for local audio/video for any SRT without linked media."
    )
    
    parser_unpodcast = subparsers.add_parser(
        'unpodcast',
        help="[DESTRUCTIVE] Resets all SRTs by removing metadata, media links, and podcast collections."
    )

    parser_link_archive = subparsers.add_parser(
        'link-archive-org',
        help="Links SRTs to audio files hosted on an Archive.org item (overwrites existing)."
    )
    parser_link_archive.add_argument(
        'archive_id',
        type=str,
        help="The identifier of the item on Archive.org (e.g., 'gaslit')."
    )

    parser_export_contrib = subparsers.add_parser(
        'export-contributions',
        help="Exports a user's annotations (tags, comments, notes) to a JSON file."
    )
    parser_export_contrib.add_argument(
        'username',
        type=str,
        help="The username of the user whose contributions you want to export."
    )
    
    parser_export_precomputed = subparsers.add_parser(
        'export-precomputed-state',
        help="[CURATOR] Exports the public DB state for precomputed distribution."
    )

    args = parser.parse_args()

    if args.command == 'link-podcast-metadata':
        link_podcast_metadata()
    elif args.command == 'unpodcast':
        unpodcast_documents()
    elif args.command == 'link-local-audio':
        link_local_audio()
    elif args.command == 'link-archive-org':
        link_archive_org(args.archive_id)
    elif args.command == 'export-contributions':
        export_contributions(args.username)
    elif args.command == 'export-precomputed-state':
        export_precomputed_state()
