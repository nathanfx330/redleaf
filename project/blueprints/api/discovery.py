# --- File: ./project/blueprints/api/discovery.py ---
import sqlite3
import json
from flask import jsonify, request, g, abort

from . import api_bp
from .helpers import get_base_document_query_fields
from ...database import get_db
from ...config import ENTITY_LABELS_TO_DISPLAY
from ..auth import login_required
from ...utils import _create_manual_snippet, _create_entity_snippet

# ===================================================================
# --- Entity & Relationship Endpoints ---
# ===================================================================

@api_bp.route('/discover/all-entities')
@login_required
def get_all_discover_entities():
    """Fetches pre-aggregated entity data from the browse_cache for the Discovery page."""
    db = get_db()
    
    cached_entities = db.execute("""
        SELECT entity_label, entity_text, document_count, appearance_count 
        FROM browse_cache 
        WHERE entity_label IN ({})
        ORDER BY entity_label, appearance_count DESC, entity_text COLLATE NOCASE
    """.format(','.join('?' for _ in ENTITY_LABELS_TO_DISPLAY)), ENTITY_LABELS_TO_DISPLAY).fetchall()
    
    entities_by_label = {label: [] for label in ENTITY_LABELS_TO_DISPLAY}
    for entity_row in cached_entities:
        entities_by_label[entity_row['entity_label']].append(dict(entity_row))
            
    return jsonify({
        'entities_by_label': entities_by_label,
        'sorted_labels': ENTITY_LABELS_TO_DISPLAY
    })

@api_bp.route('/relationships/top')
@login_required
def get_top_relationships():
    """Retrieves the most frequently occurring, non-archived relationships."""
    db = get_db()
    limit = request.args.get('limit', 100, type=int)
    query = """
        SELECT s.id as subject_id, s.text as subject_text, s.label as subject_label, 
               o.id as object_id, o.text as object_text, o.label as object_label, 
               r.relationship_phrase, COUNT(r.id) as rel_count 
        FROM entity_relationships r 
        JOIN entities s ON r.subject_entity_id = s.id 
        JOIN entities o ON r.object_entity_id = o.id 
        LEFT JOIN archived_relationships ar ON r.subject_entity_id = ar.subject_entity_id 
                                            AND r.object_entity_id = ar.object_entity_id 
                                            AND r.relationship_phrase = ar.relationship_phrase 
        WHERE ar.subject_entity_id IS NULL 
        GROUP BY s.id, o.id, r.relationship_phrase 
        ORDER BY rel_count DESC 
        LIMIT ?;
    """
    top_relations = db.execute(query, (limit,)).fetchall()
    return jsonify([dict(row) for row in top_relations])

@api_bp.route('/entity/<int:entity_id>/relationships')
@login_required
def get_entity_relationships(entity_id):
    """Gets all relationships connected to a single entity."""
    db = get_db()
    query = """
        WITH RECURSIVE numbered_rels AS (
            SELECT *, ROW_NUMBER() OVER(PARTITION BY subject_entity_id, object_entity_id, relationship_phrase ORDER BY doc_id, page_number) as rn 
            FROM entity_relationships
        ), aggregated_rels AS (
            SELECT subject_entity_id, object_entity_id, relationship_phrase, COUNT(*) as rel_count 
            FROM numbered_rels 
            GROUP BY subject_entity_id, object_entity_id, relationship_phrase
        ) 
        SELECT 'subject' as role, ar.relationship_phrase, e.id as other_entity_id, e.text as other_entity_text, e.label as other_entity_label, ar.rel_count as count 
        FROM aggregated_rels ar JOIN entities e ON ar.object_entity_id = e.id 
        WHERE ar.subject_entity_id = ? 
        UNION ALL 
        SELECT 'object' as role, ar.relationship_phrase, e.id as other_entity_id, e.text as other_entity_text, e.label as other_entity_label, ar.rel_count as count 
        FROM aggregated_rels ar JOIN entities e ON ar.subject_entity_id = e.id 
        WHERE ar.object_entity_id = ? 
        ORDER BY count DESC;
    """
    relationships = db.execute(query, (entity_id, entity_id)).fetchall()
    return jsonify([dict(row) for row in relationships])

@api_bp.route('/relationships/detail')
@login_required
def get_relationship_details():
    """Gets all document occurrences for a specific relationship triplet."""
    subject_id = request.args.get('subject_id', type=int)
    object_id = request.args.get('object_id', type=int)
    phrase = request.args.get('phrase', '')
    if not all([subject_id, object_id, phrase]):
        return jsonify({"error": "Missing required parameters"}), 400
    
    db = get_db()
    subject = db.execute("SELECT text FROM entities WHERE id = ?", (subject_id,)).fetchone()
    object_entity = db.execute("SELECT text FROM entities WHERE id = ?", (object_id,)).fetchone()
    if not subject or not object_entity:
        return jsonify({"error": "One or both entities not found."}), 404

    # --- START OF MODIFICATION ---
    # The original query was mostly correct, but we no longer need the page_content subquery here,
    # as we will fetch it inside the loop with the corrected logic.
    query = f"""
        SELECT r.doc_id, r.page_number, d.relative_path, d.color, d.page_count, d.file_type,
               {get_base_document_query_fields()}
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
        content_for_snippet = ""
        file_type = row_dict['file_type']
        page_num_for_query = row_dict['page_number']

        # This is the key fix: If the file type is SRT (or other single-page types from the
        # DuckDB pipeline), we must query page_number 1 from the content_index.
        if file_type in ['SRT', 'HTML', 'EML']:
            page_num_for_query = 1

        page_row = db.execute(
            "SELECT page_content FROM content_index WHERE doc_id = ? AND page_number = ?",
            (row_dict['doc_id'], page_num_for_query)
        ).fetchone()

        if page_row:
            content_for_snippet = page_row['page_content']

        # Now, we manually add back the SRT-specific details for the UI if needed
        if file_type == 'SRT':
            # Note: We still query srt_cues, but ONLY for UI display (timestamp), not for the snippet text.
            # This part will gracefully fail if srt_cues is empty, which is fine.
            cue = db.execute("SELECT sequence, timestamp FROM srt_cues WHERE doc_id = ? AND sequence = ?", (row_dict['doc_id'], row_dict['page_number'])).fetchone()
            if cue:
                row_dict['srt_cue_sequence'] = cue['sequence']
                row_dict['srt_timestamp'] = cue['timestamp']
        
        row_dict['snippet'] = _create_manual_snippet(content_for_snippet, subject['text'], object_entity['text'], phrase)
        final_results.append(row_dict)
    # --- END OF MODIFICATION ---
        
    return jsonify(final_results)

# ===================================================================
# --- PROFILE & BOOSTING ENDPOINTS ---
# ===================================================================

@api_bp.route('/entity/<int:entity_id>/profile-details')
@login_required
def get_entity_profile_details(entity_id):
    db = get_db()
    query = """
        SELECT 'subject' as role, e.id as other_id, e.text as other_text, e.label as other_label, COUNT(r.id) as count
        FROM entity_relationships r JOIN entities e ON r.object_entity_id = e.id
        WHERE r.subject_entity_id = ? GROUP BY e.id, e.text, e.label
        UNION ALL
        SELECT 'object' as role, e.id as other_id, e.text as other_text, e.label as other_label, COUNT(r.id) as count
        FROM entity_relationships r JOIN entities e ON r.subject_entity_id = e.id
        WHERE r.object_entity_id = ? GROUP BY e.id, e.text, e.label
        ORDER BY count DESC;
    """
    related_entities = db.execute(query, (entity_id, entity_id)).fetchall()
    boosted_rows = db.execute("SELECT target_entity_id FROM boosted_relationships WHERE user_id = ? AND source_entity_id = ?", (g.user['id'], entity_id)).fetchall()
    boosted_ids = {row['target_entity_id'] for row in boosted_rows}
    categorized_results = {}
    for entity in related_entities:
        label = entity['other_label']
        if label not in categorized_results:
            categorized_results[label] = []
        categorized_results[label].append({ 'id': entity['other_id'], 'text': entity['other_text'], 'count': entity['count'], 'is_boosted': entity['other_id'] in boosted_ids })
    return jsonify(categorized_results)

@api_bp.route('/entity/boost', methods=['POST'])
@login_required
def update_boosted_relationship():
    data = request.json
    source_entity_id = data.get('source_entity_id')
    target_entity_id = data.get('target_entity_id')
    should_boost = data.get('boost_status')
    if not all([source_entity_id, target_entity_id, should_boost is not None]):
        abort(400, "Missing required parameters.")
    db = get_db()
    if should_boost:
        db.execute("INSERT OR IGNORE INTO boosted_relationships (user_id, source_entity_id, target_entity_id) VALUES (?, ?, ?)", (g.user['id'], source_entity_id, target_entity_id))
    else:
        db.execute("DELETE FROM boosted_relationships WHERE user_id = ? AND source_entity_id = ? AND target_entity_id = ?", (g.user['id'], source_entity_id, target_entity_id))
    db.commit()
    return jsonify({'success': True, 'message': 'Preference saved.'})

# ===================================================================
# --- CORRECTED CO-MENTIONS ENDPOINT ---
# ===================================================================
@api_bp.route('/entity/<int:entity_id>/co-mentions')
@login_required
def get_entity_co_mentions(entity_id):
    """
    Fetches paginated mentions for a primary entity, but only on pages
    where a specific secondary entity also appears.
    """
    db = get_db()
    page = request.args.get('page', 1, type=int)
    filter_entity_id = request.args.get('filter_entity_id', type=int)
    limit = 50
    offset = (page - 1) * limit

    if not filter_entity_id:
        abort(400, "A 'filter_entity_id' parameter is required.")

    entity = db.execute("SELECT text FROM entities WHERE id = ?", (entity_id,)).fetchone()
    if not entity:
        abort(404, "Primary entity not found.")
    entity_text = entity['text']
    
    count_query = """
        SELECT COUNT(*) FROM (
            SELECT 1
            FROM entity_appearances ea1
            JOIN entity_appearances ea2 ON ea1.doc_id = ea2.doc_id AND ea1.page_number = ea2.page_number
            WHERE ea1.entity_id = ? AND ea2.entity_id = ? AND ea1.entity_id != ea2.entity_id
            GROUP BY ea1.doc_id, ea1.page_number
        );
    """
    total_count_row = db.execute(count_query, (entity_id, filter_entity_id)).fetchone()
    total_count = total_count_row[0] if total_count_row else 0
    
    # --- START OF FIX ---
    # This query is now fully explicit, avoiding the helper function to
    # eliminate any potential column name ambiguity. It guarantees that the
    # document ID is returned as 'doc_id'.
    results_query = """
        WITH CoMentions AS (
            SELECT ea1.doc_id, ea1.page_number
            FROM entity_appearances ea1
            JOIN entity_appearances ea2 ON ea1.doc_id = ea2.doc_id AND ea1.page_number = ea2.page_number
            WHERE ea1.entity_id = ? AND ea2.entity_id = ? AND ea1.entity_id != ea2.entity_id
            GROUP BY ea1.doc_id, ea1.page_number
        )
        SELECT 
            d.id as doc_id, 
            d.relative_path, 
            d.color, 
            d.page_count, 
            d.file_type,
            (SELECT COUNT(*) FROM document_comments WHERE doc_id = d.id) as comment_count,
            (SELECT 1 FROM document_curation WHERE doc_id = d.id AND user_id = ?) as has_personal_note,
            (SELECT 1 FROM document_tags WHERE doc_id = d.id LIMIT 1) as has_tags,
            (SELECT GROUP_CONCAT(c.name, ', ') FROM catalogs c JOIN document_catalogs dc ON c.id = dc.catalog_id WHERE dc.doc_id = d.id) as catalog_names,
            cm.page_number
        FROM CoMentions cm
        JOIN documents d ON cm.doc_id = d.id
        ORDER BY d.relative_path COLLATE NOCASE, cm.page_number
        LIMIT ? OFFSET ?;
    """
    db_results = db.execute(results_query, (entity_id, filter_entity_id, g.user['id'], limit, offset)).fetchall()
    # --- END OF FIX ---

    results = []
    for row in db_results:
        row_dict = dict(row)
        content_for_snippet = ""
        doc_id_for_query = row_dict['doc_id'] # Use the standardized key
        file_type = row_dict.get('file_type')
        page_num_for_query = row_dict['page_number']

        if file_type in ['SRT', 'HTML', 'EML']:
            page_num_for_query = 1
        
        page_row = db.execute("SELECT page_content FROM content_index WHERE doc_id = ? AND page_number = ?", (doc_id_for_query, page_num_for_query)).fetchone()
        if page_row: content_for_snippet = page_row['page_content']

        row_dict['snippet'] = _create_entity_snippet(content_for_snippet, entity_text)
        results.append(row_dict)
        
    return jsonify({
        'mentions': results,
        'total_count': total_count,
        'page': page,
        'has_more': (page * limit) < total_count
    })

# ===================================================================
# --- Podcast Collection Endpoints (Unchanged) ---
# ===================================================================
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
    years = [row['year'] for row in db.execute("SELECT DISTINCT json_extract(m.csl_json, '$.issued.\"date-parts\"[0][0]') as year FROM document_metadata m JOIN document_catalogs dc ON m.doc_id = dc.doc_id WHERE dc.catalog_id = ? AND year IS NOT NULL ORDER BY year DESC;", (catalog_id,)).fetchall()]
    letters = [row['first_letter'] for row in db.execute("SELECT DISTINCT UPPER(SUBSTR(TRIM(COALESCE(json_extract(m.csl_json, '$.title'), d.relative_path)), 1, 1)) as first_letter FROM documents d JOIN document_catalogs dc ON d.id = dc.doc_id LEFT JOIN document_metadata m ON d.id = m.doc_id WHERE dc.catalog_id = ? ORDER BY first_letter;", (catalog_id,)).fetchall() if row['first_letter']]
    return jsonify({'years': years, 'letters': letters})
@api_bp.route('/podcasts/<int:catalog_id>/episodes')
@login_required
def get_podcast_episodes(catalog_id):
    db = get_db()
    args = request.args
    offset = args.get('offset', 0, type=int)
    limit = args.get('limit', 25, type=int)
    params = [g.user['id'], catalog_id]
    where_clauses = []
    if args.get('year', type=int): where_clauses.append("json_extract(m.csl_json, '$.issued.\"date-parts\"[0][0]') = ?"); params.append(args.get('year', type=int))
    if args.get('duration'):
        duration_map = {'under_30': (0, 1800), 'under_60': (1800, 3600), 'under_120': (3600, 7200), 'over_120': (7200, 9e9)}
        if args.get('duration') in duration_map: where_clauses.append("d.duration_seconds BETWEEN ? AND ?"); params.extend(duration_map[args.get('duration')])
    if args.get('alpha'):
        title_expr = "COALESCE(json_extract(m.csl_json, '$.title'), d.relative_path)"
        if args.get('alpha') == '#': where_clauses.append(f"TRIM({title_expr}) GLOB '[0-9]*'")
        else: where_clauses.append(f"{title_expr} LIKE ?"); params.append(f"{args.get('alpha')}%")
    if args.get('has_tags') == 'true': where_clauses.append("tag_count > 0")
    if args.get('has_comments') == 'true': where_clauses.append("comment_count > 0")
    if args.get('has_notes') == 'true': where_clauses.append("has_personal_note = 1")
    sort_key = args.get('sort_key', 'date'); sort_dir = 'ASC' if args.get('sort_dir') == 'asc' else 'DESC'
    sort_cols = { 'date': "json_extract(m.csl_json, '$.issued.\"date-parts\"')", 'title': "COALESCE(json_extract(m.csl_json, '$.title'), d.relative_path) COLLATE NOCASE", 'author': "COALESCE(json_extract(m.csl_json, '$.author[0].literal'), 'zzzz') COLLATE NOCASE", 'duration': "d.duration_seconds", 'tags': "tag_count", 'comments': "comment_count", 'notes': "has_personal_note" }
    order_by = f"{sort_cols.get(sort_key, sort_cols['date'])} {sort_dir}, {sort_cols['date']} DESC, d.relative_path {sort_dir}"
    where_str = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    total_count = db.execute(f"SELECT COUNT(DISTINCT d.id) FROM documents d JOIN document_catalogs dc ON d.id = dc.doc_id LEFT JOIN document_metadata m ON d.id = m.doc_id LEFT JOIN document_tags dt ON d.id = dt.doc_id LEFT JOIN document_comments comm ON d.id = comm.doc_id LEFT JOIN document_curation cur ON d.id = cur.doc_id AND cur.user_id = ? {where_str} AND dc.catalog_id = ?", params).fetchone()[0]
    final_query = f"SELECT d.id as doc_id, d.relative_path, d.color, d.duration_seconds, m.csl_json, COUNT(DISTINCT dt.tag_id) as tag_count, COUNT(DISTINCT comm.id) as comment_count, MAX(CASE WHEN cur.user_id = ? THEN 1 ELSE 0 END) as has_personal_note FROM documents d JOIN document_catalogs dc ON d.id = dc.doc_id LEFT JOIN document_metadata m ON d.id = m.doc_id LEFT JOIN document_tags dt ON d.id = dt.doc_id LEFT JOIN document_comments comm ON d.id = comm.doc_id LEFT JOIN document_curation cur ON d.id = cur.doc_id WHERE dc.catalog_id = ? GROUP BY d.id HAVING 1=1 {' AND '.join(f'AND {c}' for c in where_clauses)} ORDER BY {order_by} LIMIT ? OFFSET ?;"
    final_params = [params[0], params[1]] + params[2:] + [limit, offset]
    rows = db.execute(final_query, final_params).fetchall()
    episodes = []
    for row in rows:
        ep_data = {"doc_id": row['doc_id'], "title": row['relative_path'], "author": "N/A", "date": None, "color": row['color'], "tag_count": row['tag_count'], "comment_count": row['comment_count'], "has_personal_note": row['has_personal_note'], "duration_seconds": row['duration_seconds']}
        if row['csl_json']:
            try:
                csl = json.loads(row['csl_json']); ep_data.update({ 'title': csl.get('title', row['relative_path']), 'author': (csl.get('author', [{}])[0].get('literal', 'N/A')), 'date': "-".join(str(p).zfill(2) for p in csl.get('issued', {}).get('date-parts', [[]])[0]) })
            except (json.JSONDecodeError, IndexError, TypeError): pass
        episodes.append(ep_data)
    return jsonify({'episodes': episodes, 'total_filtered_count': total_count})