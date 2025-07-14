# --- File: ./project/blueprints/api.py ---
import sqlite3
import json
from flask import Blueprint, jsonify, request, g, session
from pathlib import Path

from ..database import get_db
from ..utils import _get_dashboard_state, _create_manual_snippet, _truncate_long_snippet
from .auth import login_required, admin_required
from ..config import DOCUMENTS_DIR
import processing_pipeline

api_bp = Blueprint('api', __name__)

def escape_like(s):
    """Escapes strings for use in a SQL LIKE clause."""
    return s.replace('%', '[%]').replace('_', '[_]')

@api_bp.route('/dashboard/status')
@login_required
def dashboard_status():
    db = get_db()
    doc_query = "SELECT d.id, d.status, d.status_message, d.processed_at, d.page_count, d.color, d.relative_path, d.file_size_bytes, d.file_type, (SELECT COUNT(*) FROM document_comments WHERE doc_id = d.id) as comment_count, (SELECT COUNT(*) FROM document_tags WHERE doc_id = d.id) as tag_count FROM documents d"
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
            
            # --- START OF THE FIX ---
            # The query is now much more specific. It looks for a cue containing
            # the subject, the object, AND the phrase connecting them.
            cue_query = """
                SELECT sequence, timestamp, dialogue 
                FROM srt_cues 
                WHERE doc_id = ? AND dialogue LIKE ? AND dialogue LIKE ? AND dialogue LIKE ?
                ORDER BY sequence
            """
            # We escape the text to handle special SQL characters like '%'
            # This ensures we find the EXACT cue for THIS relationship instance.
            containing_cue = db.execute(cue_query, (
                doc_id, 
                f'%{escape_like(subject_text)}%', 
                f'%{escape_like(object_text)}%',
                f'%{escape_like(phrase)}%'
            )).fetchone()
            # --- END OF THE FIX ---

            if containing_cue:
                row_dict['srt_cue_sequence'] = containing_cue['sequence']
                row_dict['srt_timestamp'] = containing_cue['timestamp']
                row_dict['snippet'] = _create_manual_snippet(containing_cue['dialogue'], subject_text, object_text, phrase)
            else:
                row_dict['snippet'] = "<em>Could not locate specific cue. Retrying with broader search...</em>"
                # Fallback to the old, less specific logic if the precise one fails
                fallback_cue = db.execute("SELECT sequence, timestamp, dialogue FROM srt_cues WHERE doc_id = ? AND dialogue LIKE ? LIMIT 1", (doc_id, f'%{phrase}%')).fetchone()
                if fallback_cue:
                    row_dict['srt_cue_sequence'] = fallback_cue['sequence']
                    row_dict['srt_timestamp'] = fallback_cue['timestamp']
                    row_dict['snippet'] = _create_manual_snippet(fallback_cue['dialogue'], subject_text, object_text, phrase)
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
    all_catalogs = db.execute("SELECT id, name FROM catalogs ORDER BY name").fetchall()
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
    query = request.args.get('q', '').strip() # For search
    tags = request.args.getlist('tag')
    color = request.args.get('color')
    catalog_id = request.args.get('catalog_id', type=int)
    
    if not query and not tags and not color and not catalog_id:
        return jsonify([])

    params = []
    where_clauses = []
    
    base_query = """
        SELECT DISTINCT d.id, d.relative_path, d.color, d.page_count,
               (SELECT COUNT(*) FROM document_comments dc_count WHERE dc_count.doc_id = d.id) as comment_count,
               (SELECT 1 FROM document_curation d_cur WHERE d_cur.doc_id = d.id AND d_cur.user_id = ?) as has_personal_note,
               (SELECT 1 FROM document_tags WHERE doc_id = d.id LIMIT 1) as has_tags
        FROM documents d
    """
    params.append(user_id)

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
    
    if csl_json_text:
        try:
            json.loads(csl_json_text)
        except json.JSONDecodeError:
            return jsonify({'success': False, 'message': 'Invalid JSON format provided.'}), 400
    
    db = get_db()
    try:
        db.execute("""
            INSERT INTO document_metadata (doc_id, csl_json, last_updated, updated_by_user_id)
            VALUES (?, ?, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(doc_id) DO UPDATE SET
                csl_json=excluded.csl_json,
                last_updated=excluded.last_updated,
                updated_by_user_id=excluded.updated_by_user_id;
        """, (doc_id, csl_json_text, g.user['id']))
        db.commit()
        return jsonify({'success': True, 'message': 'Metadata saved successfully.'})
    except sqlite3.Error as e:
        db.rollback()
        return jsonify({'success': False, 'message': f'Database error: {e}'}), 500