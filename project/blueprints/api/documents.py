# --- File: ./project/blueprints/api/documents.py (Updated to allow sorting by ID) ---
import re
from flask import jsonify, request, g, abort

from . import api_bp
from .helpers import get_document_or_404, escape_like, get_base_document_query_fields
from ...database import get_db
from ...utils import _create_entity_snippet
from ...config import DOCUMENTS_DIR, ENTITY_LABELS_TO_DISPLAY
from ..auth import login_required
import processing_pipeline

@api_bp.route('/dashboard/status')
@login_required
def dashboard_status():
    """
    Returns the current status for the main dashboard UI, 
    including a PAGINATED list of documents.
    """
    from ...utils import _get_dashboard_state
    db = get_db()
    
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    sort_key = request.args.get('sort_key', 'relative_path')
    sort_dir = request.args.get('sort_dir', 'asc')
    
    allowed_sort_keys = [
        'id', 'relative_path', 'status', 'file_type', 'file_size_bytes', 
        'duration_seconds', 'page_count', 'comment_count', 'tag_count', 'processed_at'
    ]

    if sort_key not in allowed_sort_keys:
        sort_key = 'relative_path'
    if sort_dir.lower() not in ['asc', 'desc']:
        sort_dir = 'asc'

    offset = (page - 1) * per_page

    total_docs_query = db.execute("SELECT COUNT(id) FROM documents").fetchone()
    total_docs = total_docs_query[0] if total_docs_query else 0

    doc_query = f"""
        SELECT d.id, d.status, d.status_message, d.processed_at, d.page_count, 
               d.color, d.relative_path, d.file_size_bytes, d.file_type, d.duration_seconds, 
               d.linked_audio_path, d.linked_video_path,
               (SELECT COUNT(*) FROM document_comments WHERE doc_id = d.id) as comment_count, 
               (SELECT COUNT(*) FROM document_tags WHERE doc_id = d.id) as tag_count,
               (SELECT 1 FROM document_catalogs dc JOIN catalogs c ON dc.catalog_id = c.id WHERE dc.doc_id = d.id AND c.catalog_type = 'podcast' LIMIT 1) as is_podcast_episode
        FROM documents d
        ORDER BY {sort_key} {sort_dir.upper()}
        LIMIT ? OFFSET ?
    """
    docs_data = db.execute(doc_query, (per_page, offset)).fetchall()
    
    state_data = _get_dashboard_state(db)
    
    return jsonify({
        'documents': [dict(row) for row in docs_data],
        'total_documents': total_docs,
        'page': page,
        'per_page': per_page,
        'queue_size': state_data['queue_size'],
        'task_states': state_data['task_states']
    })

# --- START OF CORRECTED FUNCTION ---
@api_bp.route('/documents_by_tags')
@login_required
def get_documents_by_tags():
    """
    Fetches documents that match a combination of tags, color, and catalog filters.
    This version fixes the SQL syntax error by building the query in the correct order.
    """
    db = get_db()
    tags = request.args.getlist('tag')
    color = request.args.get('color')
    catalog_id = request.args.get('catalog_id', type=int)

    if not tags and not color and not catalog_id:
        return jsonify([])

    # Start building the query parts
    select_clause = f"SELECT DISTINCT {get_base_document_query_fields()}"
    from_clause = "FROM documents d"
    join_clauses = []
    where_clauses = []
    params = [g.user['id']]

    # Add joins and conditions for each tag
    if tags:
        for i, tag in enumerate(tags):
            # Each join needs a unique alias to handle multiple tag filters
            tag_alias = f"t{i}"
            doc_tag_alias = f"dt{i}"
            join_clauses.append(f"JOIN document_tags {doc_tag_alias} ON d.id = {doc_tag_alias}.doc_id")
            join_clauses.append(f"JOIN tags {tag_alias} ON {doc_tag_alias}.tag_id = {tag_alias}.id")
            where_clauses.append(f"{tag_alias}.name = ?")
            params.append(tag)
            
    # Add join and condition for catalog
    if catalog_id:
        join_clauses.append("JOIN document_catalogs dc ON d.id = dc.doc_id")
        where_clauses.append("dc.catalog_id = ?")
        params.append(catalog_id)

    # Add condition for color
    if color:
        where_clauses.append("d.color = ?")
        params.append(color)

    # Assemble the final query in the correct SQL order
    query = select_clause + " " + from_clause
    if join_clauses:
        query += " " + " ".join(join_clauses)
    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)
    
    query += " ORDER BY d.relative_path COLLATE NOCASE"
    
    documents = db.execute(query, params).fetchall()
    return jsonify([dict(row) for row in documents])
# --- END OF CORRECTED FUNCTION ---

@api_bp.route('/documents/types', methods=['POST'])
@login_required
def get_document_types():
    doc_ids = request.json.get('doc_ids', [])
    if not doc_ids: return jsonify({})
    db = get_db()
    safe_doc_ids = [int(id) for id in doc_ids]
    placeholders = ','.join('?' for _ in safe_doc_ids)
    query = f"SELECT id, file_type FROM documents WHERE id IN ({placeholders})"
    results = db.execute(query, safe_doc_ids).fetchall()
    return jsonify({str(row['id']): row['file_type'] for row in results})

@api_bp.route('/document/<int:doc_id>/text', methods=['GET'])
@login_required
def get_document_text(doc_id):
    db = get_db()
    doc = get_document_or_404(db, doc_id)
    full_path = DOCUMENTS_DIR / doc['relative_path']
    if not full_path.exists(): abort(404, description='Document file is missing from the filesystem.')
    start_page = request.args.get('start_page', type=int)
    end_page = request.args.get('end_page', type=int)
    text_content = processing_pipeline.extract_text_for_copying(full_path, file_type=doc['file_type'], start_page=start_page, end_page=end_page)
    return jsonify({'success': True, 'text': text_content})

@api_bp.route('/document/<int:doc_id>/entities')
@login_required
def get_document_entities(doc_id):
    db = get_db()
    get_document_or_404(db, doc_id)
    query = "SELECT e.text, e.label, COUNT(ea.page_number) as appearance_count, GROUP_CONCAT(ea.page_number ORDER BY ea.page_number) as pages FROM entity_appearances ea JOIN entities e ON ea.entity_id = e.id WHERE ea.doc_id = ? AND e.label IN ({}) GROUP BY e.id, e.text, e.label ORDER BY appearance_count DESC, e.text COLLATE NOCASE;".format(','.join('?' for _ in ENTITY_LABELS_TO_DISPLAY))
    entities = db.execute(query, (doc_id, *ENTITY_LABELS_TO_DISPLAY)).fetchall()
    entities_by_label = {label: [] for label in ENTITY_LABELS_TO_DISPLAY}
    for entity in entities:
        entities_by_label[entity['label']].append(dict(entity))
    return jsonify({'entities_by_label': entities_by_label, 'sorted_labels': ENTITY_LABELS_TO_DISPLAY})

@api_bp.route('/document/<int:doc_id>/search_srt')
@login_required
def search_srt_cues(doc_id):
    query = request.args.get('q', '').strip()
    if not query: return jsonify({'success': False, 'message': 'Search query is required.'}), 400
    db = get_db()
    doc = get_document_or_404(db, doc_id)
    if doc['file_type'] != 'SRT': return jsonify({'success': False, 'message': 'This document is not an SRT file.'}), 400
    sql_query = "SELECT sequence, timestamp, dialogue FROM srt_cues WHERE doc_id = ? AND dialogue LIKE ? ESCAPE '\\' ORDER BY sequence ASC"
    matches = db.execute(sql_query, (doc_id, f'%{escape_like(query)}%')).fetchall()
    results = []
    highlight_pattern = re.compile(f'({re.escape(query)})', re.IGNORECASE)
    for row in matches:
        snippet = highlight_pattern.sub(r'<mark>\1</mark>', row['dialogue'])
        results.append({'sequence': row['sequence'], 'timestamp': row['timestamp'], 'snippet': snippet})
    return jsonify({'success': True, 'results': results})

@api_bp.route('/entity/<int:entity_id>/mentions')
@login_required
def get_entity_mentions_paginated(entity_id):
    db = get_db()
    page = request.args.get('page', 1, type=int)
    limit = 50
    offset = (page - 1) * limit
    entity = db.execute("SELECT text FROM entities WHERE id = ?", (entity_id,)).fetchone()
    if not entity: abort(404, "Entity not found.")
    entity_text = entity['text']
    total_count = db.execute("SELECT COUNT(*) FROM entity_appearances WHERE entity_id = ?", (entity_id,)).fetchone()[0]
    from .helpers import get_base_document_query_fields
    sql_query = f"SELECT {get_base_document_query_fields()}, ea.page_number FROM entity_appearances ea JOIN documents d ON ea.doc_id = d.id WHERE ea.entity_id = ? ORDER BY d.relative_path COLLATE NOCASE, ea.page_number LIMIT ? OFFSET ?;"
    db_results = db.execute(sql_query, (g.user['id'], entity_id, limit, offset)).fetchall()
    results = []
    
    # --- START OF MODIFICATION ---
    for row in db_results:
        row_dict = dict(row)
        content_for_snippet = ""
        doc_id_for_query = row_dict['doc_id']
        file_type = row_dict.get('file_type')
        page_num_for_query = row_dict['page_number']

        # The DuckDB pipeline stores SRT, HTML, and EML content on page 1 of the content_index.
        # This logic now correctly handles that case.
        if file_type in ['SRT', 'HTML', 'EML']:
            page_num_for_query = 1
        
        page_row = db.execute(
            "SELECT page_content FROM content_index WHERE doc_id = ? AND page_number = ?", 
            (doc_id_for_query, page_num_for_query)
        ).fetchone()
        
        if page_row: 
            content_for_snippet = page_row['page_content']

        row_dict['snippet'] = _create_entity_snippet(content_for_snippet, entity_text)
        results.append(row_dict)
    # --- END OF MODIFICATION ---

    return jsonify({'mentions': results, 'total_count': total_count, 'page': page, 'has_more': (page * limit) < total_count})