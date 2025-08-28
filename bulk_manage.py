# --- File: ./bulk_manage.py (Updated to overwrite existing links) ---
import argparse
import os
import sys
import json
from pathlib import Path
import hashlib
from lxml import etree as ET
from email.utils import parsedate_to_datetime
from typing import Union
import requests

# Add the project directory to the Python path
project_dir = Path(__file__).resolve().parent
sys.path.append(str(project_dir))

from project import create_app
from project.database import get_db
from project.config import DOCUMENTS_DIR

# --- Helper functions (Unchanged) ---

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
                    matches.append({'xml_path': str(xml_path.relative_to(DOCUMENTS_DIR).as_posix()),'item_hash': item_hash,'enclosure_url': enclosure_url,'confidence': 'high' if is_match and enclosure_url and Path(enclosure_url.split('/')[-1].split('?')[0]).stem == target_srt_basename else 'low'})
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

# --- Main Command Functions (Unchanged) ---

def link_podcast_metadata():
    # ... (code is unchanged)
    pass

def link_local_audio():
    # ... (code is unchanged)
    pass

def unpodcast_documents():
    # ... (code is unchanged)
    pass

# --- UPDATED: Main function for Archive.org linking ---

def link_archive_org(archive_id: str):
    """Links SRTs to audio files hosted in a specified Archive.org item."""
    archive_file_map = _fetch_archive_files(archive_id)
    if not archive_file_map:
        print("\n--- Operation cancelled due to failure fetching archive data. ---")
        return

    app = create_app()
    with app.app_context():
        db = get_db()
        
        # --- THIS IS THE CHANGE: Select ALL SRTs, not just unlinked ones ---
        docs_to_process = db.execute("""
            SELECT id, relative_path FROM documents
            WHERE file_type = 'SRT'
            ORDER BY relative_path
        """).fetchall()
        
        if not docs_to_process:
            print("\n--- No SRT documents found in the database. Nothing to do. ---")
            return
            
        print(f"\n--- Found {len(docs_to_process)} total SRT documents to check. ---")
        # --- NEW: Add a confirmation prompt because this is a destructive action ---
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


# --- Main CLI setup (Unchanged) ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Redleaf Engine Bulk Management Tool.")
    subparsers = parser.add_subparsers(dest='command', required=True, help='Available commands')

    parser_link_meta = subparsers.add_parser(
        'link-podcast-metadata',
        help="Attempt to link metadata AND media for unprocessed SRT files from XMLs."
    )
    
    parser_link_audio = subparsers.add_parser(
        'link-local-audio',
        help="[RECOMMENDED] Scans for local audio for any SRT without linked media."
    )
    
    parser_unpodcast = subparsers.add_parser(
        'unpodcast',
        help="[DESTRUCTIVE] Resets all SRTs by removing metadata, media links, and podcast collections."
    )

    parser_link_archive = subparsers.add_parser(
        'link-archive-org',
        help="[NEW] Links SRTs to audio files hosted on an Archive.org item."
    )
    parser_link_archive.add_argument(
        'archive_id',
        type=str,
        help="The identifier of the item on Archive.org (e.g., 'gaslit')."
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