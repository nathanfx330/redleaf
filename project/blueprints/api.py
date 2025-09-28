# --- File: ./project/blueprints/api.py (Updated) ---
import sqlite3
import json
import os
import re
import hashlib
import requests
from flask import Blueprint, jsonify, request, g, session, current_app, url_for
from pathlib import Path
from lxml import etree as ET
from email.utils import parsedate_to_datetime
from typing import Union

from ..database import get_db
from ..utils import _get_dashboard_state, _create_manual_snippet, _truncate_long_snippet, _create_entity_snippet
from .auth import login_required, admin_required
from ..config import DOCUMENTS_DIR, ENTITY_LABELS_TO_DISPLAY
import processing_pipeline

api_bp = Blueprint('api', __name__)

def escape_like(s):
    """Escapes strings for use in a SQL LIKE clause."""
    if not s:
        return ""
    s = s.replace('\\', '\\\\')
    s = s.replace('%', '\\%')
    s = s.replace('_', '\\_')
    return s

def _parse_podcast_xml_to_csl(item_element: ET.Element, channel_title_text: str = None, channel_author_text: str = None) -> tuple[dict, Union[str, None]]:
    """
    Parses a single <item> XML element from a podcast RSS feed into a CSL-JSON dict
    and also extracts the enclosure URL for the media file.
    """
    csl_data = {}
    if item_element is None:
        return None, None

    namespaces = {'itunes': 'http://www.itunes.com/dtds/podcast-1.0.dtd'}

    title_element = item_element.find('itunes:title', namespaces)
    if title_element is None or not title_element.text:
        title_element = item_element.find('title')
    
    if title_element is not None and title_element.text:
        csl_data['title'] = title_element.text.strip()

    if channel_title_text:
        csl_data['container-title'] = channel_title_text.strip()

    author_tag = item_element.find('itunes:author', namespaces)
    author_text = None
    if author_tag is not None and author_tag.text:
        author_text = author_tag.text.strip()
    elif channel_author_text:
        author_text = channel_author_text.strip()
    
    if author_text:
        csl_data['author'] = [{'literal': author_text}]

    pub_date_tag = item_element.find('pubDate')
    if pub_date_tag is not None and pub_date_tag.text:
        try:
            dt = parsedate_to_datetime(pub_date_tag.text)
            csl_data['issued'] = {'date-parts': [[dt.year, dt.month, dt.day]]}
        except (ValueError, TypeError):
            pass

    link_tag = item_element.find('link')
    if link_tag is not None and link_tag.text:
        csl_data['URL'] = link_tag.text.strip()
        
    enclosure_url = None
    enclosure_tag = item_element.find('enclosure')
    if enclosure_tag is not None and enclosure_tag.get('url'):
        enclosure_url = enclosure_tag.get('url').strip()

    csl_data['type'] = 'interview'
    
    return csl_data, enclosure_url

@api_bp.route('/dashboard/status')
@login_required
def dashboard_status():
    db = get_db()
    doc_query = """
        SELECT d.id, d.status, d.status_message, d.processed_at, d.page_count, 
               d.color, d.relative_path, d.file_size_bytes, d.file_type, d.duration_seconds, 
               d.linked_audio_path, d.linked_video_path,
               (SELECT COUNT(*) FROM document_comments WHERE doc_id = d.id) as comment_count, 
               (SELECT COUNT(*) FROM document_tags WHERE doc_id = d.id) as tag_count,
               (SELECT 1 FROM document_catalogs dc JOIN catalogs c ON dc.catalog_id = c.id WHERE dc.doc_id = d.id AND c.catalog_type = 'podcast' LIMIT 1) as is_podcast_episode
        FROM documents d
    """
    docs_data = db.execute(doc_query).fetchall()
    state_data = _get_dashboard_state(db)
    return jsonify({
        'documents': [dict(row) for row in docs_data],
        'queue_size': state_data['queue_size'],
        'task_states': state_data['task_states']
    })

@api_bp.route('/documents/types', methods=['POST'])
@login_required
def get_document_types():
    doc_ids = request.json.get('doc_ids', [])
    if not doc_ids:
        return jsonify({})
    db = get_db()
    safe_doc_ids = [int(id) for id in doc_ids]
    placeholders = ','.join('?' for _ in safe_doc_ids)
    query = f"SELECT id, file_type FROM documents WHERE id IN ({placeholders})"
    results = db.execute(query, safe_doc_ids).fetchall()
    type_map = {str(row['id']): row['file_type'] for row in results}
    return jsonify(type_map)

@api_bp.route('/document/<int:doc_id>/entities')
@login_required
def get_document_entities(doc_id):
    db = get_db()
    query = """
        SELECT
            e.text,
            e.label,
            COUNT(ea.page_number) as appearance_count,
            GROUP_CONCAT(ea.page_number ORDER BY ea.page_number) as pages
        FROM entity_appearances ea
        JOIN entities e ON ea.entity_id = e.id
        WHERE ea.doc_id = ?
        GROUP BY e.id, e.text, e.label
        ORDER BY appearance_count DESC, e.text COLLATE NOCASE;
    """
    entities = db.execute(query, (doc_id,)).fetchall()
    entities_by_label = {label: [] for label in ENTITY_LABELS_TO_DISPLAY}
    for entity in entities:
        if entity['label'] in entities_by_label:
            entities_by_label[entity['label']].append(dict(entity))
    return jsonify({'entities_by_label': entities_by_label, 'sorted_labels': ENTITY_LABELS_TO_DISPLAY})

@api_bp.route('/document/<int:doc_id>/search_srt')
@login_required
def search_srt_cues(doc_id):
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({'success': False, 'message': 'Search query cannot be empty.'}), 400
    db = get_db()
    doc_type = db.execute("SELECT file_type FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc_type or doc_type['file_type'] != 'SRT':
        return jsonify({'success': False, 'message': 'This document is not an SRT file.'}), 404
    sql_query = "SELECT sequence, timestamp, dialogue FROM srt_cues WHERE doc_id = ? AND dialogue LIKE ? ESCAPE '\\' ORDER BY sequence ASC"
    matches = db.execute(sql_query, (doc_id, f'%{escape_like(query)}%')).fetchall()
    results = []
    highlight_pattern = re.compile(f'({re.escape(query)})', re.IGNORECASE)
    for row in matches:
        snippet = highlight_pattern.sub(r'<mark>\1</mark>', row['dialogue'])
        results.append({'sequence': row['sequence'], 'timestamp': row['timestamp'], 'snippet': snippet})
    return jsonify({'success': True, 'results': results})

@api_bp.route('/relationships/detail')
@login_required
def get_relationship_details():
    subject_id = request.args.get('subject_id', type=int)
    object_id = request.args.get('object_id', type=int)
    phrase = request.args.get('phrase', '')
    if not all([subject_id, object_id, phrase]):
        return jsonify({"error": "Missing required parameters"}), 400
    db = get_db()
    subject_text = db.execute("SELECT text FROM entities WHERE id = ?", (subject_id,)).fetchone()['text']
    object_text = db.execute("SELECT text FROM entities WHERE id = ?", (object_id,)).fetchone()['text']
    query = """
        SELECT r.doc_id, r.page_number, d.relative_path, d.color, d.page_count, d.file_type,
               (SELECT page_content FROM content_index ci WHERE ci.doc_id = r.doc_id AND ci.page_number = r.page_number) as page_content,
               (SELECT COUNT(*) FROM document_comments WHERE doc_id = d.id) as comment_count,
               (SELECT 1 FROM document_curation WHERE doc_id = d.id AND user_id = ?) as has_personal_note,
               (SELECT 1 FROM document_tags WHERE doc_id = d.id LIMIT 1) as has_tags,
               (SELECT GROUP_CONCAT(c.name, ', ') FROM catalogs c JOIN document_catalogs dc ON c.id = dc.catalog_id WHERE dc.doc_id = d.id) as catalog_names
        FROM entity_relationships r
        JOIN documents d ON r.doc_id = d.id
        WHERE r.subject_entity_id = ? AND r.object_entity_id = ? AND r.relationship_phrase = ?
        ORDER BY d.relative_path COLLATE NOCASE, r.page_number;
    """
    params = (g.user['id'], subject_id, object_id, phrase)
    db_results = db.execute(query, params).fetchall()
    final_results = []
    for row in db_results:
        row_dict = dict(row)
        if row_dict['file_type'] == 'SRT':
            doc_id = row_dict['doc_id']
            safe_subject = f'%{escape_like(subject_text)}%'
            safe_object = f'%{escape_like(object_text)}%'
            safe_phrase = f'%{escape_like(phrase)}%'
            cue_query = "SELECT sequence, timestamp, dialogue FROM srt_cues WHERE doc_id = ? AND dialogue LIKE ? ESCAPE '\\' AND dialogue LIKE ? ESCAPE '\\' AND dialogue LIKE ? ESCAPE '\\' ORDER BY sequence"
            containing_cue = db.execute(cue_query, (doc_id, safe_subject, safe_object, safe_phrase)).fetchone()
            if containing_cue:
                row_dict['srt_cue_sequence'] = containing_cue['sequence']
                row_dict['srt_timestamp'] = containing_cue['timestamp']
                row_dict['snippet'] = _create_manual_snippet(containing_cue['dialogue'], subject_text, object_text, phrase)
            else:
                row_dict['snippet'] = "<em>Could not locate specific cue.</em>"
        else:
            page_content = row_dict.pop('page_content', '')
            row_dict['snippet'] = _create_manual_snippet(page_content, subject_text, object_text, phrase)
        final_results.append(row_dict)
    return jsonify(final_results)

@api_bp.route('/relationships/top')
@login_required
def get_top_relationships():
    db = get_db()
    limit = request.args.get('limit', 100, type=int)
    query = """SELECT s.id as subject_id, s.text as subject_text, s.label as subject_label, o.id as object_id, o.text as object_text, o.label as object_label, r.relationship_phrase, COUNT(r.id) as rel_count FROM entity_relationships r JOIN entities s ON r.subject_entity_id = s.id JOIN entities o ON r.object_entity_id = o.id LEFT JOIN archived_relationships ar ON r.subject_entity_id = ar.subject_entity_id AND r.object_entity_id = ar.object_entity_id AND r.relationship_phrase = ar.relationship_phrase WHERE ar.subject_entity_id IS NULL GROUP BY s.id, s.text, s.label, o.id, o.text, o.label, r.relationship_phrase ORDER BY rel_count DESC LIMIT ?;"""
    top_relations = db.execute(query, (limit,)).fetchall()
    return jsonify([dict(row) for row in top_relations])

@api_bp.route('/relationships/archive', methods=['POST'])
@login_required
def archive_relationships():
    data = request.json
    if not data or 'relationships' not in data:
        return jsonify({'success': False, 'message': 'Invalid request body.'}), 400
    params = [(rel['subject_id'], rel['object_id'], rel['phrase']) for rel in data['relationships']]
    if not params:
        return jsonify({'success': False, 'message': 'No relationships provided.'}), 400
    db = get_db()
    try:
        db.executemany("INSERT OR IGNORE INTO archived_relationships (subject_entity_id, object_entity_id, relationship_phrase) VALUES (?, ?, ?)", params)
        db.commit()
        return jsonify({'success': True, 'message': f'Archived {len(params)} relationships.'})
    except (sqlite3.Error, KeyError) as e:
        db.rollback()
        return jsonify({'success': False, 'message': f'Database error or malformed data: {e}'}), 500

@api_bp.route('/settings/archived-relationships', methods=['GET'])
@admin_required
def get_archived_relationships():
    db = get_db()
    query = "SELECT ar.subject_entity_id, s.text as subject_text, ar.object_entity_id, o.text as object_text, ar.relationship_phrase, ar.archived_at FROM archived_relationships ar JOIN entities s ON ar.subject_entity_id = s.id JOIN entities o ON ar.object_entity_id = o.id ORDER BY ar.archived_at DESC;"
    archived_list = db.execute(query).fetchall()
    return jsonify([dict(row) for row in archived_list])

@api_bp.route('/settings/unarchive-relationship', methods=['POST'])
@admin_required
def unarchive_relationship():
    data = request.json
    try:
        subject_id = data['subject_id']
        object_id = data['object_id']
        phrase = data['phrase']
    except KeyError:
        return jsonify({'success': False, 'message': 'Missing required relationship data.'}), 400
    db = get_db()
    try:
        cursor = db.execute("DELETE FROM archived_relationships WHERE subject_entity_id = ? AND object_entity_id = ? AND relationship_phrase = ?", (subject_id, object_id, phrase))
        db.commit()
        if cursor.rowcount > 0:
            return jsonify({'success': True, 'message': 'Relationship un-archived.'})
        else:
            return jsonify({'success': False, 'message': 'Relationship not found in archive.'}), 404
    except sqlite3.Error as e:
        db.rollback()
        return jsonify({'success': False, 'message': f'Database error: {e}'}), 500

@api_bp.route('/entity/<int:entity_id>/relationships')
@login_required
def get_entity_relationships(entity_id):
    db = get_db()
    query = """WITH RECURSIVE numbered_rels AS (SELECT *, ROW_NUMBER() OVER(PARTITION BY subject_entity_id, object_entity_id, relationship_phrase ORDER BY doc_id, page_number) as rn FROM entity_relationships), aggregated_rels AS (SELECT subject_entity_id, object_entity_id, relationship_phrase, COUNT(*) as rel_count FROM numbered_rels GROUP BY subject_entity_id, object_entity_id, relationship_phrase) SELECT 'subject' as role, ar.relationship_phrase, e.id as other_entity_id, e.text as other_entity_text, e.label as other_entity_label, ar.rel_count as count FROM aggregated_rels ar JOIN entities e ON ar.object_entity_id = e.id WHERE ar.subject_entity_id = ? UNION ALL SELECT 'object' as role, ar.relationship_phrase, e.id as other_entity_id, e.text as other_entity_text, e.label as other_entity_label, ar.rel_count as count FROM aggregated_rels ar JOIN entities e ON ar.subject_entity_id = e.id WHERE ar.object_entity_id = ? ORDER BY count DESC;"""
    relationships = db.execute(query, (entity_id, entity_id)).fetchall()
    return jsonify([dict(row) for row in relationships])

@api_bp.route('/document/<int:doc_id>/text', methods=['GET'])
@login_required
def get_document_text(doc_id):
    db = get_db()
    doc = db.execute("SELECT relative_path, file_type FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc:
        return jsonify({'success': False, 'message': 'Document not found.'}), 404
    full_path = DOCUMENTS_DIR / doc['relative_path']
    if not full_path.exists():
        return jsonify({'success': False, 'message': 'Document file is missing from the filesystem.'}), 404
    start_page = request.args.get('start_page', type=int)
    end_page = request.args.get('end_page', type=int)
    text_content = processing_pipeline.extract_text_for_copying(full_path, file_type=doc['file_type'], start_page=start_page, end_page=end_page)
    return jsonify({'success': True, 'text': text_content})

@api_bp.route('/document/<int:doc_id>/curation', methods=['GET'])
@login_required
def get_curation_data(doc_id):
    db = get_db()
    user_id = session['user_id']
    curation = db.execute("SELECT note FROM document_curation WHERE doc_id = ? AND user_id = ?", (doc_id, user_id)).fetchone()
    comments_query = "SELECT c.id, c.comment_text, c.created_at, u.username, u.id as user_id FROM document_comments c JOIN users u ON c.user_id = u.id WHERE c.doc_id = ? ORDER BY c.created_at ASC"
    comments = db.execute(comments_query, (doc_id,)).fetchall()
    
    all_catalogs_query = "SELECT id, name FROM catalogs WHERE catalog_type IN ('user', 'favorites') ORDER BY name"
    all_catalogs = db.execute(all_catalogs_query).fetchall()
    
    member_of_catalogs_rows = db.execute("SELECT catalog_id FROM document_catalogs WHERE doc_id = ?", (doc_id,)).fetchall()
    member_of_catalogs = {row['catalog_id'] for row in member_of_catalogs_rows}
    favorites_catalog = db.execute("SELECT id FROM catalogs WHERE name = '⭐ Favorites'").fetchone()
    favorites_catalog_id = favorites_catalog['id'] if favorites_catalog else None
    is_favorite = favorites_catalog_id in member_of_catalogs if favorites_catalog_id else False
    return jsonify({'is_favorite': is_favorite, 'note': curation['note'] if curation and curation['note'] else '', 'all_catalogs': [dict(cat) for cat in all_catalogs], 'member_of_catalogs': list(member_of_catalogs), 'comments': [dict(comment) for comment in comments], 'current_user': {'id': g.user['id'], 'role': g.user['role']}})

@api_bp.route('/document/<int:doc_id>/curation', methods=['POST'])
@login_required
def update_curation_data(doc_id):
    data = request.json
    db = get_db()
    db.execute("INSERT INTO document_curation (doc_id, user_id, note, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP) ON CONFLICT(doc_id, user_id) DO UPDATE SET note=excluded.note, updated_at=excluded.updated_at", (doc_id, session['user_id'], data.get('note', '')))
    db.commit()
    return jsonify({'success': True})

@api_bp.route('/document/<int:doc_id>/color', methods=['POST'])
@login_required
def set_document_color(doc_id):
    color = request.json.get('color')
    db = get_db()
    db.execute("UPDATE documents SET color = ? WHERE id = ?", (color, doc_id))
    db.commit()
    return jsonify({'success': True, 'color': color})

@api_bp.route('/document/<int:doc_id>/comments', methods=['POST'])
@login_required
def add_document_comment(doc_id):
    comment_text = request.json.get('comment_text', '').strip()
    if not comment_text:
        return jsonify({'success': False, 'message': 'Comment cannot be empty.'}), 400
    db = get_db()
    db.execute("INSERT INTO document_comments (doc_id, user_id, comment_text) VALUES (?, ?, ?)", (doc_id, g.user['id'], comment_text))
    db.commit()
    return jsonify({'success': True, 'message': 'Comment added.'}), 201

@api_bp.route('/comments/<int:comment_id>', methods=['DELETE'])
@login_required
def delete_comment(comment_id):
    db = get_db()
    comment = db.execute("SELECT user_id FROM document_comments WHERE id = ?", (comment_id,)).fetchone()
    if not comment:
        return jsonify({'success': False, 'message': 'Comment not found.'}), 404
    if comment['user_id'] != g.user['id'] and g.user['role'] != 'admin':
        return jsonify({'success': False, 'message': 'You do not have permission to delete this comment.'}), 403
    db.execute("DELETE FROM document_comments WHERE id = ?", (comment_id,))
    db.commit()
    return jsonify({'success': True, 'message': 'Comment deleted.'})

@api_bp.route('/tags', methods=['GET'])
@login_required
def get_all_tags():
    db = get_db()
    all_tags = [row['name'] for row in db.execute("SELECT name FROM tags ORDER BY name")]
    return jsonify(all_tags)

@api_bp.route('/document/<int:doc_id>/tags', methods=['GET'])
@login_required
def get_document_tags(doc_id):
    db = get_db()
    tags = [row['name'] for row in db.execute("SELECT t.name FROM tags t JOIN document_tags dt ON t.id = dt.tag_id WHERE dt.doc_id = ? ORDER BY t.name", (doc_id,)).fetchall()]
    return jsonify({'success': True, 'tags': tags})

@api_bp.route('/document/<int:doc_id>/tags', methods=['POST'])
@login_required
def set_document_tags(doc_id):
    tags = sorted(list(set(t.strip().lower() for t in request.json.get('tags', []) if t.strip())))
    db = get_db()
    try:
        db.execute("BEGIN")
        if tags:
            db.executemany("INSERT OR IGNORE INTO tags (name) VALUES (?)", [(tag,) for tag in tags])
            placeholders = ','.join('?' for _ in tags)
            tag_ids = [row['id'] for row in db.execute(f"SELECT id FROM tags WHERE name IN ({placeholders})", tags).fetchall()]
        else:
            tag_ids = []
        db.execute("DELETE FROM document_tags WHERE doc_id = ?", (doc_id,))
        if tag_ids:
            db.executemany("INSERT INTO document_tags (doc_id, tag_id) VALUES (?, ?)", [(doc_id, tag_id) for tag_id in tag_ids])
        db.commit()
        return jsonify({'success': True, 'tags': tags})
    except sqlite3.Error as e:
        db.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@api_bp.route('/document/<int:doc_id>/catalogs', methods=['POST'])
@login_required
def set_document_catalogs(doc_id):
    catalog_ids = request.json.get('catalog_ids', [])
    db = get_db()
    try:
        db.execute("BEGIN")
        db.execute("DELETE FROM document_catalogs WHERE doc_id = ?", (doc_id,))
        if catalog_ids:
            db.executemany("INSERT INTO document_catalogs (doc_id, catalog_id) VALUES (?, ?)", [(doc_id, cat_id) for cat_id in catalog_ids])
        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@api_bp.route('/tags/rename', methods=['PUT'])
@admin_required
def rename_tag():
    data = request.json
    old_name = data.get('old_name')
    new_name = data.get('new_name', '').strip().lower()
    if not old_name or not new_name:
        return jsonify({'success': False, 'message': 'Old and new tag names are required.'}), 400
    if old_name == new_name:
        return jsonify({'success': True, 'message': 'No changes made.'})
    db = get_db()
    try:
        db.execute("BEGIN")
        if db.execute("SELECT id FROM tags WHERE name = ?", (new_name,)).fetchone():
            db.rollback()
            return jsonify({'success': False, 'message': f"Tag '{new_name}' already exists."}), 409
        cursor = db.execute("UPDATE tags SET name = ? WHERE name = ?", (new_name, old_name))
        if cursor.rowcount > 0:
            db.commit()
            return jsonify({'success': True, 'message': 'Tag renamed successfully.'})
        else:
            db.rollback()
            return jsonify({'success': False, 'message': 'Original tag not found.'}), 404
    except sqlite3.Error as e:
        db.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@api_bp.route('/tags/delete', methods=['DELETE'])
@admin_required
def delete_tag():
    tag_name = request.json.get('name')
    if not tag_name:
        return jsonify({'success': False, 'message': 'Tag name is required.'}), 400
    db = get_db()
    try:
        db.execute("BEGIN")
        tag_row = db.execute("SELECT id FROM tags WHERE name = ?", (tag_name,)).fetchone()
        if not tag_row:
            db.rollback()
            return jsonify({'success': False, 'message': 'Tag not found.'}), 404
        db.execute("DELETE FROM tags WHERE id = ?", (tag_row['id'],))
        db.commit()
        return jsonify({'success': True, 'message': f"Tag '{tag_name}' deleted."})
    except sqlite3.Error as e:
        db.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
        
@api_bp.route('/catalogs', methods=['POST'])
@login_required
def api_create_catalog():
    name = request.json.get('name', '').strip()
    description = request.json.get('description', '').strip()
    if not name:
        return jsonify({'success': False, 'message': 'Catalog name cannot be empty.'}), 400
    db = get_db()
    try:
        cursor = db.execute("INSERT INTO catalogs (name, description) VALUES (?, ?)", (name, description))
        db.commit()
        new_catalog = {'id': cursor.lastrowid, 'name': name, 'description': description}
        return jsonify({'success': True, 'catalog': new_catalog}), 201
    except sqlite3.IntegrityError:
        db.rollback()
        return jsonify({'success': False, 'message': 'A catalog with this name already exists.'}), 409

@api_bp.route('/catalogs/<int:catalog_id>', methods=['PUT'])
@admin_required
def update_catalog(catalog_id):
    data = request.json
    name = data.get('name', '').strip()
    description = data.get('description', '').strip()
    if not name:
        return jsonify({'success': False, 'message': 'Catalog name cannot be empty.'}), 400
    db = get_db()
    fav_catalog = db.execute("SELECT id FROM catalogs WHERE name = '⭐ Favorites'").fetchone()
    if fav_catalog and fav_catalog['id'] == catalog_id:
        return jsonify({'success': False, 'message': 'The Favorites catalog cannot be modified.'}), 403
    try:
        db.execute("UPDATE catalogs SET name = ?, description = ? WHERE id = ?", (name, description, catalog_id))
        db.commit()
        return jsonify({'success': True, 'message': 'Catalog updated successfully.'})
    except sqlite3.IntegrityError:
        db.rollback()
        return jsonify({'success': False, 'message': f"A catalog with the name '{name}' already exists."}), 409
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@api_bp.route('/catalogs/<int:catalog_id>', methods=['DELETE'])
@admin_required
def api_delete_catalog(catalog_id):
    db = get_db()
    fav_catalog = db.execute("SELECT id FROM catalogs WHERE name = '⭐ Favorites'").fetchone()
    if fav_catalog and fav_catalog['id'] == catalog_id:
        return jsonify({'success': False, 'message': 'The Favorites catalog cannot be deleted.'}), 403
    cursor = db.execute("DELETE FROM catalogs WHERE id = ?", (catalog_id,))
    db.commit()
    if cursor.rowcount > 0:
        return jsonify({'success': True, 'message': 'Catalog deleted successfully.'})
    else:
        return jsonify({'success': False, 'message': 'Catalog not found.'}), 404
        
@api_bp.route('/documents_by_tags')
@login_required
def get_documents_by_tags():
    db = get_db()
    user_id = g.user['id']
    query = request.args.get('q', '').strip()
    tags = request.args.getlist('tag')
    color = request.args.get('color')
    catalog_id = request.args.get('catalog_id', type=int)
    if not query and not tags and not color and not catalog_id:
        return jsonify([])
    params = [user_id]
    where_clauses = []
    base_query = """
        SELECT DISTINCT d.id, d.relative_path, d.color, d.page_count,
               (SELECT COUNT(*) FROM document_comments dc_count WHERE dc_count.doc_id = d.id) as comment_count,
               (SELECT 1 FROM document_curation d_cur WHERE d_cur.doc_id = d.id AND d_cur.user_id = ?) as has_personal_note,
               (SELECT 1 FROM document_tags WHERE doc_id = d.id LIMIT 1) as has_tags
        FROM documents d
    """
    if query:
        base_query += " JOIN content_index ci ON d.id = ci.doc_id"
        sanitized_query = query.replace('"', '""')
        fts_query = f'"{sanitized_query}"'
        where_clauses.append("ci.content_index MATCH ?")
        params.append(fts_query)
    if color:
        where_clauses.append("d.color = ?")
        params.append(color)
    if catalog_id:
        where_clauses.append("d.id IN (SELECT doc_id FROM document_catalogs WHERE catalog_id = ?)")
        params.append(catalog_id)
    if tags:
        placeholders = ','.join('?' for _ in tags)
        where_clauses.append(f"d.id IN (SELECT doc_id FROM document_tags JOIN tags ON tags.id = document_tags.tag_id WHERE tags.name IN ({placeholders}) GROUP BY doc_id HAVING COUNT(DISTINCT tags.id) = ?)")
        params.extend(tags)
        params.append(len(tags))
    if where_clauses:
        base_query += " WHERE " + " AND ".join(where_clauses)
    base_query += " ORDER BY d.relative_path COLLATE NOCASE"
    documents = db.execute(base_query, params).fetchall()
    return jsonify([dict(doc) for doc in documents])

@api_bp.route('/document/<int:doc_id>/metadata', methods=['GET'])
@login_required
def get_document_metadata(doc_id):
    db = get_db()
    metadata = db.execute("SELECT csl_json FROM document_metadata WHERE doc_id = ?", (doc_id,)).fetchone()
    if metadata and metadata['csl_json']:
        return jsonify({'csl_json': metadata['csl_json']})
    else:
        return jsonify({'csl_json': None})

@api_bp.route('/document/<int:doc_id>/metadata', methods=['POST'])
@login_required
def save_document_metadata(doc_id):
    data = request.json
    csl_json_text = data.get('csl_json')

    podcast_name = None
    if csl_json_text:
        try:
            csl_data = json.loads(csl_json_text)
            if csl_data.get('type') in ['interview', 'broadcast'] and csl_data.get('container-title'):
                podcast_name = csl_data['container-title'].strip()
        except (json.JSONDecodeError, KeyError):
            csl_json_text = None 
            podcast_name = None

    db = get_db()
    try:
        db.execute("BEGIN")
        
        db.execute("""
            INSERT INTO document_metadata (doc_id, csl_json, last_updated, updated_by_user_id)
            VALUES (?, ?, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(doc_id) DO UPDATE SET
                csl_json=excluded.csl_json,
                last_updated=excluded.last_updated,
                updated_by_user_id=excluded.updated_by_user_id;
        """, (doc_id, csl_json_text, g.user['id']))

        db.execute("""
            DELETE FROM document_catalogs 
            WHERE doc_id = ? AND catalog_id IN (SELECT id FROM catalogs WHERE catalog_type = 'podcast')
        """, (doc_id,))

        if podcast_name:
            catalog_row = db.execute("SELECT id FROM catalogs WHERE name = ? AND catalog_type = 'podcast'", (podcast_name,)).fetchone()
            if catalog_row:
                catalog_id = catalog_row['id']
            else:
                cursor = db.execute(
                    "INSERT INTO catalogs (name, description, catalog_type) VALUES (?, ?, 'podcast')",
                    (podcast_name, f"Automatically generated collection for the '{podcast_name}' podcast.")
                )
                catalog_id = cursor.lastrowid
            
            db.execute("INSERT OR IGNORE INTO document_catalogs (doc_id, catalog_id) VALUES (?, ?)", (doc_id, catalog_id))

        db.commit()
        return jsonify({'success': True, 'message': 'Metadata saved and collections updated.'})
    except sqlite3.Error as e:
        db.rollback()
        return jsonify({'success': False, 'message': f'Database error: {e}'}), 500

@api_bp.route('/document/<int:doc_id>/find_audio', methods=['POST'])
@login_required
def find_document_audio(doc_id):
    db = get_db()
    doc = db.execute("SELECT relative_path, file_type FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc or doc['file_type'] != 'SRT':
        return jsonify({'success': False, 'message': 'Document is not an SRT file.'}), 404
    doc_path = Path(doc['relative_path'])
    audio_filename = doc_path.with_suffix('.mp3').name
    path_in_same_dir = doc_path.parent / audio_filename
    if (DOCUMENTS_DIR / path_in_same_dir).is_file():
        relative_audio_path_str = path_in_same_dir.as_posix()
        db.execute("UPDATE documents SET linked_audio_path = ?, linked_video_path = NULL, linked_audio_url = NULL WHERE id = ?", (relative_audio_path_str, doc_id))
        db.commit()
        return jsonify({'success': True, 'media_url': url_for('main.serve_document', relative_path=relative_audio_path_str)})
    found_files = list(DOCUMENTS_DIR.rglob(audio_filename))
    if found_files:
        first_match_path = found_files[0]
        relative_audio_path = first_match_path.relative_to(DOCUMENTS_DIR)
        relative_audio_path_str = relative_audio_path.as_posix()
        db.execute("UPDATE documents SET linked_audio_path = ?, linked_video_path = NULL, linked_audio_url = NULL WHERE id = ?", (relative_audio_path_str, doc_id))
        db.commit()
        return jsonify({'success': True, 'media_url': url_for('main.serve_document', relative_path=relative_audio_path_str)})
    return jsonify({'success': False, 'message': f"No '{audio_filename}' file found."}), 404

@api_bp.route('/document/<int:doc_id>/find_video', methods=['POST'])
@login_required
def find_document_video(doc_id):
    db = get_db()
    doc = db.execute("SELECT relative_path, file_type FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc or doc['file_type'] != 'SRT':
        return jsonify({'success': False, 'message': 'Document is not an SRT file.'}), 404
    doc_path = Path(doc['relative_path'])
    video_filename = doc_path.with_suffix('.mp4').name
    path_in_same_dir = doc_path.parent / video_filename
    if (DOCUMENTS_DIR / path_in_same_dir).is_file():
        relative_video_path_str = path_in_same_dir.as_posix()
        db.execute("UPDATE documents SET linked_video_path = ?, linked_audio_path = NULL, linked_audio_url = NULL WHERE id = ?", (relative_video_path_str, doc_id))
        db.commit()
        return jsonify({'success': True, 'media_url': url_for('main.serve_document', relative_path=relative_video_path_str)})
    found_files = list(DOCUMENTS_DIR.rglob(video_filename))
    if found_files:
        first_match_path = found_files[0]
        relative_video_path = first_match_path.relative_to(DOCUMENTS_DIR)
        relative_video_path_str = relative_video_path.as_posix()
        db.execute("UPDATE documents SET linked_video_path = ?, linked_audio_path = NULL, linked_audio_url = NULL WHERE id = ?", (relative_video_path_str, doc_id))
        db.commit()
        return jsonify({'success': True, 'media_url': url_for('main.serve_document', relative_path=relative_video_path_str)})
    return jsonify({'success': False, 'message': f"No '{video_filename}' file found."}), 404

@api_bp.route('/document/<int:doc_id>/find_metadata_xml', methods=['POST'])
@login_required
def find_metadata_xml(doc_id):
    db = get_db()
    doc = db.execute("SELECT relative_path FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc:
        return jsonify({'success': False, 'message': 'Document not found.'}), 404

    target_srt_basename = Path(doc['relative_path']).stem
    
    matches = []
    all_xml_files = list(DOCUMENTS_DIR.rglob('*.xml'))
    xml_files_scanned = len(all_xml_files)
    
    parser = ET.XMLParser(recover=True)
    
    for xml_path in all_xml_files:
        try:
            tree_bytes = xml_path.read_bytes()
            tree = ET.fromstring(tree_bytes, parser=parser)
            
            if parser.error_log:
                first_error = parser.error_log[0]
                print(f"Warning: XML file '{xml_path}' has parsing errors. Attempting to recover.")
                print(f"  - Error Details: {first_error.message} (Line: {first_error.line}, Column: {first_error.column})")

            channel_title_element = tree.find('channel/title')
            channel_title = channel_title_element.text if channel_title_element is not None else ""
            
            namespaces = {'itunes': 'http://www.itunes.com/dtds/podcast-1.0.dtd'}
            channel_author_element = tree.find('channel/itunes:author', namespaces)
            channel_author = channel_author_element.text if channel_author_element is not None and channel_author_element.text else ""
            
            for item in tree.findall('channel/item'):
                is_match = False
                enclosure = item.find('enclosure')
                if enclosure is not None and enclosure.get('url'):
                    url_filename = enclosure.get('url').split('/')[-1].split('?')[0]
                    enclosure_basename = Path(url_filename).stem
                    if enclosure_basename == target_srt_basename: is_match = True

                if not is_match:
                    title_element = item.find('itunes:title', namespaces) or item.find('title')
                    if title_element is not None and title_element.text and target_srt_basename in title_element.text:
                        is_match = True

                if is_match:
                    preview_data, enclosure_url = _parse_podcast_xml_to_csl(item, channel_title, channel_author)
                    if not preview_data: continue
                    guid_element = item.find('guid')
                    hash_content = (guid_element.text if guid_element is not None and guid_element.text else preview_data.get('title', ''))
                    item_hash = hashlib.md5(hash_content.encode()).hexdigest()
                    matches.append({
                        'xml_path': xml_path.relative_to(DOCUMENTS_DIR).as_posix(), 'preview': preview_data,
                        'item_hash': item_hash, 'enclosure_url': enclosure_url
                    })
                    continue 

        except ET.XMLSyntaxError:
            if parser.error_log:
                first_error = parser.error_log[0]
                print(f"Warning: Could not process XML file '{xml_path}'.")
                print(f"  - Fatal Error: {first_error.message} (Line: {first_error.line}, Column: {first_error.column})")
            else:
                 print(f"Warning: Could not process severely malformed XML file '{xml_path}'.")
            continue
            
    return jsonify({'success': True, 'matches': matches, 'xml_files_scanned': xml_files_scanned})

@api_bp.route('/document/<int:doc_id>/import_from_xml', methods=['POST'])
@login_required
def import_metadata_from_xml(doc_id):
    data = request.json
    xml_path_str, item_hash = data.get('xml_path'), data.get('item_hash')
    if not xml_path_str or not item_hash:
        return jsonify({'success': False, 'message': 'Missing XML path or item identifier.'}), 400
    
    db = get_db()
    
    full_xml_path = DOCUMENTS_DIR / xml_path_str
    if not full_xml_path.is_file():
        return jsonify({'success': False, 'message': 'XML file not found at the specified path.'}), 404

    try:
        parser = ET.XMLParser(recover=True)
        tree = ET.fromstring(full_xml_path.read_bytes(), parser=parser)
        
        channel_title = (tree.find('channel/title').text if tree.find('channel/title') is not None else "")
        namespaces = {'itunes': 'http://www.itunes.com/dtds/podcast-1.0.dtd'}
        channel_author_element = tree.find('channel/itunes:author', namespaces)
        channel_author = channel_author_element.text if channel_author_element is not None and channel_author_element.text else ""
        
        found_item = None
        for item in tree.findall('channel/item'):
            hash_content = (item.findtext('guid', '') or item.findtext('title', ''))
            if hashlib.md5(hash_content.encode()).hexdigest() == item_hash:
                found_item = item
                break
        
        if not found_item:
            return jsonify({'success': False, 'message': 'Could not find the selected item within the XML file.'}), 404
        
        csl_data, _ = _parse_podcast_xml_to_csl(found_item, channel_title, channel_author)
        if not csl_data:
            return jsonify({'success': False, 'message': 'Failed to parse the found XML item into CSL data.'}), 500
        
        csl_data['id'] = f"doc-{doc_id}"
        csl_json_text = json.dumps(csl_data, indent=2)
        
        db.execute("""INSERT INTO document_metadata (doc_id, csl_json, last_updated, updated_by_user_id) VALUES (?, ?, CURRENT_TIMESTAMP, ?)
                      ON CONFLICT(doc_id) DO UPDATE SET csl_json=excluded.csl_json, last_updated=excluded.last_updated, updated_by_user_id=excluded.updated_by_user_id;
                   """, (doc_id, csl_json_text, g.user['id']))
        db.commit()
        return jsonify({'success': True, 'message': 'Metadata imported successfully.', 'csl_json': csl_json_text})
    except Exception as e:
        db.rollback()
        current_app.logger.error(f"Error during XML import: {e}")
        return jsonify({'success': False, 'message': f'An unexpected error occurred: {e}'}), 500

@api_bp.route('/document/<int:doc_id>/media_status', methods=['GET'])
@login_required
def get_media_status(doc_id):
    db = get_db()
    doc = db.execute("SELECT linked_audio_path, linked_video_path, linked_audio_url, last_audio_position, audio_offset_seconds FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc: return jsonify({'linked': False})
    response_data = {'linked': True, 'position': doc['last_audio_position'], 'offset': doc['audio_offset_seconds']}
    if doc['linked_audio_url']: response_data.update({'type': 'audio', 'path': doc['linked_audio_url'], 'source': 'web'})
    elif doc['linked_video_path']: response_data.update({'type': 'video', 'path': url_for('main.serve_document', relative_path=doc['linked_video_path'], _external=True), 'source': 'local'})
    elif doc['linked_audio_path']: response_data.update({'type': 'audio', 'path': url_for('main.serve_document', relative_path=doc['linked_audio_path'], _external=True), 'source': 'local'})
    else: return jsonify({'linked': False})
    return jsonify(response_data)

@api_bp.route('/document/<int:doc_id>/unlink_media', methods=['POST'])
@login_required
def unlink_document_media(doc_id):
    db = get_db()
    db.execute("UPDATE documents SET linked_audio_path = NULL, linked_video_path = NULL, linked_audio_url = NULL, last_audio_position = 0.0 WHERE id = ?", (doc_id,))
    db.commit()
    return jsonify({'success': True, 'message': 'Media link removed.'})

@api_bp.route('/document/<int:doc_id>/link_audio_from_url', methods=['POST'])
@login_required
def link_audio_from_url(doc_id):
    url = request.json.get('url')
    if not url: return jsonify({'success': False, 'message': 'URL is required.'}), 400
    db = get_db()
    db.execute("UPDATE documents SET linked_audio_url = ?, linked_audio_path = NULL, linked_video_path = NULL WHERE id = ?", (url, doc_id))
    db.commit()
    return jsonify({'success': True, 'media_url': url})

@api_bp.route('/document/<int:doc_id>/check_url_status', methods=['POST'])
@login_required
def check_url_status(doc_id):
    url = request.json.get('url')
    if not url: return jsonify({'success': False, 'message': 'URL not provided.'}), 400
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

@api_bp.route('/discover/all-entities')
@login_required
def get_all_discover_entities():
    db = get_db()
    
    cached_entities = db.execute("""
        SELECT entity_label, entity_text, document_count, appearance_count 
        FROM browse_cache 
        ORDER BY entity_label, appearance_count DESC, entity_text COLLATE NOCASE
    """).fetchall()
    
    entities_by_label = {label: [] for label in ENTITY_LABELS_TO_DISPLAY}
    for entity_row in cached_entities:
        label = entity_row['entity_label']
        if label in entities_by_label:
            entities_by_label[label].append(dict(entity_row))
            
    return jsonify({
        'entities_by_label': entities_by_label,
        'sorted_labels': ENTITY_LABELS_TO_DISPLAY
    })

@api_bp.route('/podcasts/collections')
@login_required
def get_podcast_collections():
    db = get_db()
    query = "SELECT c.id as catalog_id, c.name as podcast_title, COUNT(dc.doc_id) as episode_count FROM catalogs c JOIN document_catalogs dc ON c.id = dc.catalog_id WHERE c.catalog_type = 'podcast' GROUP BY c.id, c.name ORDER BY c.name COLLATE NOCASE;"
    return jsonify([dict(row) for row in db.execute(query).fetchall()])

@api_bp.route('/podcasts/<int:catalog_id>/filters')
@login_required
def get_podcast_filters(catalog_id):
    db = get_db()
    years_query = """
        SELECT DISTINCT json_extract(m.csl_json, '$.issued."date-parts"[0][0]') as year
        FROM document_metadata m
        JOIN document_catalogs dc ON m.doc_id = dc.doc_id
        WHERE dc.catalog_id = ? AND year IS NOT NULL
        ORDER BY year DESC;
    """
    years = [row['year'] for row in db.execute(years_query, (catalog_id,)).fetchall()]

    letters_query = """
        SELECT DISTINCT UPPER(SUBSTR(TRIM(COALESCE(json_extract(m.csl_json, '$.title'), d.relative_path)), 1, 1)) as first_letter
        FROM documents d
        JOIN document_catalogs dc ON d.id = dc.doc_id
        LEFT JOIN document_metadata m ON d.id = m.doc_id
        WHERE dc.catalog_id = ?
        ORDER BY first_letter;
    """
    letters = [row['first_letter'] for row in db.execute(letters_query, (catalog_id,)).fetchall() if row['first_letter']]

    return jsonify({'years': years, 'letters': letters})

@api_bp.route('/podcasts/<int:catalog_id>/episodes')
@login_required
def get_podcast_episodes(catalog_id):
    db = get_db()
    offset = request.args.get('offset', 0, type=int)
    limit = request.args.get('limit', 25, type=int)
    
    year = request.args.get('year')
    duration = request.args.get('duration')
    alpha = request.args.get('alpha')
    has_tags = request.args.get('has_tags') == 'true'
    has_comments = request.args.get('has_comments') == 'true'
    has_notes = request.args.get('has_notes') == 'true'
    sort_key = request.args.get('sort_key', 'date')
    sort_dir = request.args.get('sort_dir', 'desc')

    params = [g.user['id'], catalog_id]
    where_clauses = []

    if year and year.isdigit():
        where_clauses.append("json_extract(m.csl_json, '$.issued.\"date-parts\"[0][0]') = ?")
        params.append(int(year))
    
    if duration:
        duration_map = { 'under_30': (0, 1800), 'under_60': (1800, 3600), 'under_120': (3600, 7200), 'over_120': (7200, 999999) }
        if duration in duration_map:
            min_sec, max_sec = duration_map[duration]
            where_clauses.append("d.duration_seconds BETWEEN ? AND ?")
            params.extend([min_sec, max_sec])

    if alpha:
        title_expression = "COALESCE(json_extract(m.csl_json, '$.title'), d.relative_path)"
        if alpha == '#': where_clauses.append(f"TRIM({title_expression}) GLOB '[0-9]*'")
        elif len(alpha) == 1 and alpha.isalpha(): where_clauses.append(f"{title_expression} LIKE ?"); params.append(f"{alpha}%")

    if has_tags: where_clauses.append("tag_count > 0")
    if has_comments: where_clauses.append("comment_count > 0")
    if has_notes: where_clauses.append("has_personal_note = 1")

    where_string = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    sort_columns = {
        'date': "json_extract(m.csl_json, '$.issued.\"date-parts\"')", 'title': "COALESCE(json_extract(m.csl_json, '$.title'), d.relative_path) COLLATE NOCASE",
        'author': "COALESCE(json_extract(m.csl_json, '$.author[0].literal'), 'zzzz') COLLATE NOCASE", 'duration': "d.duration_seconds",
        'tags': "tag_count", 'comments': "comment_count", 'notes': "has_personal_note"
    }
    
    primary_order_by_col = sort_columns.get(sort_key, sort_columns['date'])
    direction = "ASC" if sort_dir == 'asc' else "DESC"
    order_by_clause = f"{primary_order_by_col} {direction}"
    if sort_key != 'date':
        order_by_clause += f", {sort_columns['date']} DESC"
    order_by_clause += f", d.relative_path {direction}"

    base_query = f"""
        SELECT 
            d.id as doc_id, d.relative_path, d.color, d.duration_seconds,
            m.csl_json,
            COUNT(DISTINCT dt.tag_id) as tag_count,
            COUNT(DISTINCT comm.id) as comment_count,
            MAX(CASE WHEN cur.user_id = ? THEN 1 ELSE 0 END) as has_personal_note
        FROM documents d
        JOIN document_catalogs dc ON d.id = dc.doc_id
        LEFT JOIN document_metadata m ON d.id = m.doc_id
        LEFT JOIN document_tags dt ON d.id = dt.doc_id
        LEFT JOIN document_comments comm ON d.id = comm.doc_id
        LEFT JOIN document_curation cur ON d.id = cur.doc_id
        WHERE dc.catalog_id = ?
    """

    count_query = f"""
        SELECT COUNT(DISTINCT d.id) 
        FROM documents d
        JOIN document_catalogs dc ON d.id = dc.doc_id
        LEFT JOIN document_metadata m ON d.id = m.doc_id
        LEFT JOIN document_tags dt ON d.id = dt.doc_id
        LEFT JOIN document_comments comm ON d.id = comm.doc_id
        LEFT JOIN document_curation cur ON d.id = cur.doc_id AND cur.user_id = ?
        {where_string} AND dc.catalog_id = ?
    """
    total_count = db.execute(count_query, params).fetchone()[0]

    final_query = f"""
        {base_query}
        GROUP BY d.id
        HAVING 1=1 {' AND '.join(f'AND {c}' for c in where_clauses)}
        ORDER BY {order_by_clause}
        LIMIT ? OFFSET ?;
    """
    final_params = [params[0], params[1]] + params[2:] + [limit, offset]
    rows = db.execute(final_query, final_params).fetchall()
    
    episodes = []
    for row in rows:
        ep_title, ep_author, ep_date, ep_date_sort = row['relative_path'], "N/A", None, "0"
        if row['csl_json']:
            try:
                csl = json.loads(row['csl_json'])
                ep_title = csl.get('title', ep_title)
                if csl.get('author') and csl['author'][0].get('literal'): ep_author = csl['author'][0]['literal']
                if csl.get('issued') and csl['issued'].get('date-parts'):
                    parts = csl['issued']['date-parts'][0]
                    ep_date_sort = "-".join(str(p).zfill(2) for p in parts)
                    ep_date = ep_date_sort
            except (json.JSONDecodeError, IndexError, TypeError): pass
        
        episodes.append({
            "doc_id": row['doc_id'], "title": ep_title, "author": ep_author, "date": ep_date,
            "sort_key": ep_date_sort, "color": row['color'], "tag_count": row['tag_count'],
            "comment_count": row['comment_count'], "has_personal_note": row['has_personal_note'],
            "duration_seconds": row['duration_seconds']
        })

    return jsonify({'episodes': episodes, 'total_filtered_count': total_count})

@api_bp.route('/document/<int:doc_id>/save_audio_position', methods=['POST'])
@login_required
def save_audio_position(doc_id):
    position = request.json.get('position', 0.0)
    if position is None: return jsonify({'success': False, 'message': 'Position not provided.'}), 400
    db = get_db()
    try:
        db.execute("UPDATE documents SET last_audio_position = ? WHERE id = ?", (position, doc_id)); db.commit()
        return jsonify({'success': True})
    except sqlite3.Error as e:
        db.rollback(); return jsonify({'success': False, 'message': f'Database error: {e}'}), 500

@api_bp.route('/document/<int:doc_id>/save_audio_offset', methods=['POST'])
@login_required
def save_audio_offset(doc_id):
    offset = request.json.get('offset', 0.0)
    if offset is None: return jsonify({'success': False, 'message': 'Offset not provided.'}), 400
    db = get_db()
    try:
        db.execute("UPDATE documents SET audio_offset_seconds = ? WHERE id = ?", (offset, doc_id)); db.commit()
        return jsonify({'success': True, 'message': 'Offset saved.'})
    except sqlite3.Error as e:
        db.rollback(); return jsonify({'success': False, 'message': f'Database error: {e}'}), 500

@api_bp.route('/document/<int:doc_id>/save_pdf_zoom', methods=['POST'])
@login_required
def save_pdf_zoom(doc_id):
    zoom_level = request.json.get('zoom')
    if zoom_level is None or not isinstance(zoom_level, (int, float)):
        return jsonify({'success': False, 'message': 'Valid zoom level not provided.'}), 400
    db = get_db()
    try:
        db.execute("UPDATE documents SET last_pdf_zoom = ? WHERE id = ?", (zoom_level, doc_id)); db.commit()
        return jsonify({'success': True, 'message': 'Zoom level saved.'})
    except sqlite3.Error as e:
        db.rollback(); return jsonify({'success': False, 'message': f'Database error: {e}'}), 500

@api_bp.route('/document/<int:doc_id>/save_pdf_page', methods=['POST'])
@login_required
def save_pdf_page(doc_id):
    page = request.json.get('page')
    if page is None or not isinstance(page, int):
        return jsonify({'success': False, 'message': 'Valid page number not provided.'}), 400
    db = get_db()
    try:
        db.execute("UPDATE documents SET last_pdf_page = ? WHERE id = ?", (page, doc_id)); db.commit()
        return jsonify({'success': True, 'message': 'Page position saved.'})
    except sqlite3.Error as e:
        db.rollback(); return jsonify({'success': False, 'message': f'Database error: {e}'}), 500

@api_bp.route('/entity/<int:entity_id>/mentions')
@login_required
def get_entity_mentions_paginated(entity_id):
    """
    Fetches the mentions for a specific entity with pagination for performance.
    """
    db = get_db()
    page = request.args.get('page', 1, type=int)
    limit = 50
    offset = (page - 1) * limit

    total_count_row = db.execute("SELECT COUNT(*) FROM entity_appearances WHERE entity_id = ?", (entity_id,)).fetchone()
    total_count = total_count_row[0] if total_count_row else 0

    sql_query = """
        SELECT 
            d.id as doc_id, d.relative_path, d.color, d.page_count, d.file_type, ea.page_number,
            (SELECT COUNT(*) FROM document_comments WHERE doc_id = d.id) as comment_count,
            (SELECT 1 FROM document_curation WHERE doc_id = d.id AND user_id = ?) as has_personal_note,
            (SELECT 1 FROM document_tags WHERE doc_id = d.id LIMIT 1) as has_tags,
            (SELECT GROUP_CONCAT(c.name, ', ') FROM catalogs c JOIN document_catalogs dc ON c.id = dc.catalog_id WHERE dc.doc_id = d.id) as catalog_names
        FROM entity_appearances ea
        JOIN documents d ON ea.doc_id = d.id
        WHERE ea.entity_id = ? 
        ORDER BY d.relative_path COLLATE NOCASE, ea.page_number
        LIMIT ? OFFSET ?;
    """
    db_results = db.execute(sql_query, (g.user['id'], entity_id, limit, offset)).fetchall()
    entity = db.execute("SELECT text FROM entities WHERE id = ?", (entity_id,)).fetchone()
    entity_text = entity['text'] if entity else ''

    results = []
    for row in db_results:
        row_dict = dict(row)
        if row_dict.get('file_type') == 'SRT':
            cue_content_row = db.execute("SELECT dialogue FROM srt_cues WHERE doc_id = ? AND sequence = ?", (row_dict['doc_id'], row_dict['page_number'])).fetchone()
            content_for_snippet = cue_content_row['dialogue'] if cue_content_row else ''
        else:
            page_num_to_query = 1 if row_dict.get('file_type') == 'HTML' else row_dict['page_number']
            page_content_row = db.execute("SELECT page_content FROM content_index WHERE doc_id = ? AND page_number = ?", (row_dict['doc_id'], page_num_to_query)).fetchone()
            content_for_snippet = page_content_row['page_content'] if page_content_row else ''
            
        row_dict['snippet'] = _create_entity_snippet(content_for_snippet, entity_text)
        results.append(row_dict)

    return jsonify({
        'mentions': results,
        'total_count': total_count,
        'page': page,
        'has_more': (page * limit) < total_count
    })

# ===================================================================
# --- START: NEW API ENDPOINTS FOR MISSION II ---
# ===================================================================
@api_bp.route('/contributions/accept-tag', methods=['POST'])
@admin_required
def accept_contribution_tag():
    data = request.json
    doc_path = data.get('doc_path')
    tag_name = data.get('value')
    if not doc_path or not tag_name:
        return jsonify({'success': False, 'message': 'Missing document path or tag name.'}), 400

    db = get_db()
    try:
        doc_row = db.execute("SELECT id FROM documents WHERE relative_path = ?", (doc_path,)).fetchone()
        if not doc_row:
            return jsonify({'success': False, 'message': f'Document not found: {doc_path}'}), 404
        doc_id = doc_row['id']

        db.execute("BEGIN")
        db.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name,))
        tag_id = db.execute("SELECT id FROM tags WHERE name = ?", (tag_name,)).fetchone()['id']
        db.execute("INSERT OR IGNORE INTO document_tags (doc_id, tag_id) VALUES (?, ?)", (doc_id, tag_id))
        db.commit()
        return jsonify({'success': True, 'message': 'Tag accepted.'})
    except sqlite3.Error as e:
        db.rollback()
        return jsonify({'success': False, 'message': f'Database error: {e}'}), 500

@api_bp.route('/contributions/accept-comment', methods=['POST'])
@admin_required
def accept_contribution_comment():
    data = request.json
    doc_path = data.get('doc_path')
    comment_text = data.get('value')
    contributor = data.get('contributor', 'Explorer')
    if not doc_path or not comment_text:
        return jsonify({'success': False, 'message': 'Missing document path or comment text.'}), 400

    db = get_db()
    try:
        doc_row = db.execute("SELECT id FROM documents WHERE relative_path = ?", (doc_path,)).fetchone()
        if not doc_row:
            return jsonify({'success': False, 'message': f'Document not found: {doc_path}'}), 404
        doc_id = doc_row['id']
        
        # We attribute the comment to the admin who accepted it, but prefix the text
        full_comment_text = f"[From {contributor}]:\n\n{comment_text}"

        db.execute(
            "INSERT INTO document_comments (doc_id, user_id, comment_text) VALUES (?, ?, ?)",
            (doc_id, g.user['id'], full_comment_text)
        )
        db.commit()
        return jsonify({'success': True, 'message': 'Comment accepted.'})
    except sqlite3.Error as e:
        db.rollback()
        return jsonify({'success': False, 'message': f'Database error: {e}'}), 500
# ===================================================================
# --- END: NEW API ENDPOINTS FOR MISSION II ---
# ===================================================================