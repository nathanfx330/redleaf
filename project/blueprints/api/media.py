# --- File: ./project/blueprints/api/media.py ---
import sqlite3
import json
import requests
from flask import jsonify, request, g, url_for, abort
from pathlib import Path
from lxml import etree as ET
import hashlib

from . import api_bp
from .helpers import get_document_or_404, find_xml_matches_for_doc, parse_podcast_xml_to_csl
from ...database import get_db
from ...config import DOCUMENTS_DIR
from ..auth import login_required

# ===================================================================
# --- START: NEW AND MODIFIED MEDIA ENDPOINTS ---
# ===================================================================

@api_bp.route('/document/<int:doc_id>/media_status', methods=['GET'])
@login_required
def get_media_status(doc_id):
    """
    Checks the database for linked media for a document and returns its status and URL.
    This is the primary endpoint used by the SRT viewer to find its media source.
    """
    db = get_db()
    doc = get_document_or_404(db, doc_id)
    
    path_to_serve = None
    media_type = None
    source = None

    if doc['linked_audio_url']:
        path_to_serve = doc['linked_audio_url']
        source = 'web'
        # Basic check for media type based on URL extension
        if path_to_serve.lower().endswith('.mp4'):
            media_type = 'video'
        else:
            media_type = 'audio' # Default to audio for web links
    elif doc['linked_video_path']:
        path_to_serve = url_for('main.serve_document', relative_path=doc['linked_video_path'])
        media_type = 'video'
        source = 'local'
    elif doc['linked_audio_path']:
        path_to_serve = url_for('main.serve_document', relative_path=doc['linked_audio_path'])
        media_type = 'audio'
        source = 'local'

    if path_to_serve:
        return jsonify({
            'linked': True,
            'path': path_to_serve,
            'type': media_type,
            'source': source,
            'position': doc['last_audio_position'],
            'offset': doc['audio_offset_seconds']
        })
    else:
        return jsonify({'linked': False})

@api_bp.route('/document/<int:doc_id>/save_audio_position', methods=['POST'])
@login_required
def save_audio_position(doc_id):
    """Saves the last playback position for a media file."""
    position = request.json.get('position')
    if position is None:
        abort(400, 'Position not provided.')
    db = get_db()
    get_document_or_404(db, doc_id)
    db.execute("UPDATE documents SET last_audio_position = ? WHERE id = ?", (position, doc_id))
    db.commit()
    return jsonify({'success': True})

@api_bp.route('/document/<int:doc_id>/save_audio_offset', methods=['POST'])
@login_required
def save_audio_offset(doc_id):
    """Saves the synchronization offset for a media file."""
    offset = request.json.get('offset')
    if offset is None:
        abort(400, 'Offset not provided.')
    db = get_db()
    get_document_or_404(db, doc_id)
    db.execute("UPDATE documents SET audio_offset_seconds = ? WHERE id = ?", (offset, doc_id))
    db.commit()
    return jsonify({'success': True})

# ===================================================================
# --- END: NEW AND MODIFIED MEDIA ENDPOINTS ---
# ===================================================================

def _find_and_link_local_media(doc_id: int, extension: str) -> tuple[dict, int]:
    """Helper to scan for and link local media files (.mp3, .mp4)."""
    db = get_db()
    doc = get_document_or_404(db, doc_id)
    if doc['file_type'] != 'SRT':
        return {'success': False, 'message': 'Document is not an SRT file.'}, 400
    
    doc_path = Path(doc['relative_path'])
    media_filename = doc_path.with_suffix(extension).name
    
    path_in_same_dir = doc_path.parent / media_filename
    if (DOCUMENTS_DIR / path_in_same_dir).is_file():
        found_path_str = path_in_same_dir.as_posix()
    else:
        found_files = list(DOCUMENTS_DIR.rglob(media_filename))
        if not found_files:
            return {'success': False, 'message': f"No '{media_filename}' file found anywhere."}, 404
        found_path_str = found_files[0].relative_to(DOCUMENTS_DIR).as_posix()

    audio_path, video_path = (found_path_str, None) if extension == '.mp3' else (None, found_path_str)
    
    db.execute("""
        UPDATE documents 
        SET linked_audio_path = ?, linked_video_path = ?, linked_audio_url = NULL, last_audio_position = 0.0
        WHERE id = ?
    """, (audio_path, video_path, doc_id))
    db.commit()
    
    return {'success': True, 'media_url': url_for('main.serve_document', relative_path=found_path_str)}, 200

@api_bp.route('/document/<int:doc_id>/find_audio', methods=['POST'])
@login_required
def find_document_audio(doc_id):
    result, status_code = _find_and_link_local_media(doc_id, '.mp3')
    return jsonify(result), status_code

@api_bp.route('/document/<int:doc_id>/find_video', methods=['POST'])
@login_required
def find_document_video(doc_id):
    result, status_code = _find_and_link_local_media(doc_id, '.mp4')
    return jsonify(result), status_code

@api_bp.route('/document/<int:doc_id>/link_audio_from_url', methods=['POST'])
@login_required
def link_audio_from_url(doc_id):
    url = request.json.get('url')
    if not url:
        return jsonify({'success': False, 'message': 'URL is required.'}), 400
    
    db = get_db()
    get_document_or_404(db, doc_id)
    db.execute("UPDATE documents SET linked_audio_url = ?, linked_audio_path = NULL, linked_video_path = NULL, last_audio_position = 0.0 WHERE id = ?", (url, doc_id))
    db.commit()
    return jsonify({'success': True, 'media_url': url})

@api_bp.route('/document/<int:doc_id>/unlink_media', methods=['POST'])
@login_required
def unlink_document_media(doc_id):
    db = get_db()
    get_document_or_404(db, doc_id)
    db.execute("UPDATE documents SET linked_audio_path = NULL, linked_video_path = NULL, linked_audio_url = NULL, last_audio_position = 0.0 WHERE id = ?", (doc_id,))
    db.commit()
    return jsonify({'success': True, 'message': 'Media link removed.'})

@api_bp.route('/document/<int:doc_id>/check_url_status', methods=['POST'])
@login_required
def check_url_status(doc_id):
    url = request.json.get('url')
    if not url:
        return jsonify({'success': False, 'message': 'URL not provided.'}), 400
    try:
        response = requests.head(url, allow_redirects=True, timeout=5)
        content_type = response.headers.get('Content-Type', '')
        if response.ok:
            if any(ct in content_type for ct in ['audio', 'video', 'octet-stream']):
                 return jsonify({'success': True, 'status': 'online', 'message': f'Link is online. (Content-Type: {content_type})'})
            return jsonify({'success': True, 'status': 'warning', 'message': f'Link is online, but may not be audio/video. (Content-Type: {content_type})'})
        return jsonify({'success': True, 'status': 'offline', 'message': f'Link is offline or inaccessible (Status: {response.status_code}).'})
    except requests.exceptions.RequestException as e:
        return jsonify({'success': False, 'status': 'error', 'message': f'Error checking URL: {e}'})

# ===================================================================
# --- Metadata & XML Syncing ---
# ===================================================================

@api_bp.route('/document/<int:doc_id>/metadata', methods=['GET', 'POST'])
@login_required
def document_metadata(doc_id):
    db = get_db()
    get_document_or_404(db, doc_id)

    if request.method == 'GET':
        metadata = db.execute("SELECT csl_json FROM document_metadata WHERE doc_id = ?", (doc_id,)).fetchone()
        return jsonify({'csl_json': metadata['csl_json'] if metadata else None})

    if request.method == 'POST':
        csl_json_text = request.json.get('csl_json')
        podcast_name = None
        if csl_json_text:
            try:
                csl_data = json.loads(csl_json_text)
                if csl_data.get('type') in ['interview', 'broadcast'] and csl_data.get('container-title'):
                    podcast_name = csl_data['container-title'].strip()
            except (json.JSONDecodeError, KeyError):
                csl_json_text, podcast_name = None, None

        try:
            db.execute("BEGIN")
            db.execute("""
                INSERT INTO document_metadata (doc_id, csl_json, last_updated, updated_by_user_id) VALUES (?, ?, CURRENT_TIMESTAMP, ?)
                ON CONFLICT(doc_id) DO UPDATE SET csl_json=excluded.csl_json, last_updated=excluded.last_updated, updated_by_user_id=excluded.updated_by_user_id;
            """, (doc_id, csl_json_text, g.user['id']))
            
            db.execute("DELETE FROM document_catalogs WHERE doc_id = ? AND catalog_id IN (SELECT id FROM catalogs WHERE catalog_type = 'podcast')", (doc_id,))
            
            if podcast_name:
                catalog_id = db.execute("SELECT id FROM catalogs WHERE name = ? AND catalog_type = 'podcast'", (podcast_name,)).fetchone()
                if not catalog_id:
                    cursor = db.execute("INSERT INTO catalogs (name, description, catalog_type) VALUES (?, ?, 'podcast')", (podcast_name, f"Automatically generated collection for the '{podcast_name}' podcast."))
                    catalog_id = cursor.lastrowid
                else:
                    catalog_id = catalog_id['id']
                db.execute("INSERT OR IGNORE INTO document_catalogs (doc_id, catalog_id) VALUES (?, ?)", (doc_id, catalog_id))
            
            db.commit()
            return jsonify({'success': True, 'message': 'Metadata saved and collections updated.'})
        except sqlite3.Error as e:
            db.rollback()
            return jsonify({'success': False, 'message': f'Database error: {e}'}), 500

@api_bp.route('/document/<int:doc_id>/find_metadata_xml', methods=['POST'])
@login_required
def find_metadata_xml(doc_id):
    db = get_db()
    doc = get_document_or_404(db, doc_id)
    matches = find_xml_matches_for_doc(doc['relative_path'])
    return jsonify({'success': True, 'matches': matches, 'xml_files_scanned': len(list(DOCUMENTS_DIR.rglob('*.xml')))})

@api_bp.route('/document/<int:doc_id>/import_from_xml', methods=['POST'])
@login_required
def import_metadata_from_xml(doc_id):
    data = request.json
    xml_path_str, item_hash = data.get('xml_path'), data.get('item_hash')
    if not xml_path_str or not item_hash:
        return jsonify({'success': False, 'message': 'Missing XML path or item identifier.'}), 400
    
    db = get_db()
    get_document_or_404(db, doc_id)
    
    full_xml_path = DOCUMENTS_DIR / xml_path_str
    if not full_xml_path.is_file():
        return jsonify({'success': False, 'message': 'XML file not found.'}), 404

    try:
        parser = ET.XMLParser(recover=True)
        tree = ET.parse(str(full_xml_path), parser=parser)
        channel_title = tree.findtext('channel/title', "")
        namespaces = {'itunes': 'http://www.itunes.com/dtds/podcast-1.0.dtd'}
        channel_author = tree.findtext('channel/itunes:author', "", namespaces=namespaces)
        
        found_item = None
        for item in tree.findall('channel/item'):
            hash_content = (item.findtext('guid', '') or item.findtext('title', ''))
            if hashlib.md5(hash_content.encode()).hexdigest() == item_hash:
                found_item = item
                break
        
        if found_item is None:
            return jsonify({'success': False, 'message': 'Could not find the selected item within the XML file.'}), 404
        
        csl_data, _ = parse_podcast_xml_to_csl(found_item, channel_title, channel_author)
        if not csl_data:
            return jsonify({'success': False, 'message': 'Failed to parse the found XML item into CSL data.'}), 500
        
        csl_data['id'] = f"doc-{doc_id}"
        csl_json_text = json.dumps(csl_data, indent=2)
        
        db.execute("""
            INSERT INTO document_metadata (doc_id, csl_json, last_updated, updated_by_user_id) VALUES (?, ?, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(doc_id) DO UPDATE SET csl_json=excluded.csl_json, last_updated=excluded.last_updated, updated_by_user_id=excluded.updated_by_user_id;
        """, (doc_id, csl_json_text, g.user['id']))
        db.commit()
        return jsonify({'success': True, 'message': 'Metadata imported successfully.', 'csl_json': csl_json_text})
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'message': f'An unexpected error occurred: {e}'}), 500