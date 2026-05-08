# --- File: ./project/blueprints/api/discovery.py (OPTIMIZED) ---
import sqlite3
import json
import uuid 
import re 
from collections import defaultdict
import numpy as np
import heapq
import ollama

from flask import jsonify, request, g, abort

from . import api_bp
from .helpers import get_base_document_query_fields, escape_like
from ...database import get_db
from ...config import ENTITY_LABELS_TO_DISPLAY, BASE_DIR, EMBEDDING_MODEL
from ..auth import login_required
from ...utils import _create_manual_snippet, _create_entity_snippet
from ...assistant_core import _internal_fts_search, _internal_semantic_search, read_specific_pages

# === THE FIX: Use relative import to ensure we grab the true global CSRF instance ===
from ... import csrf

# ===================================================================
# --- Optimized Entity Discovery Endpoints ---
# ===================================================================

@api_bp.route('/discover/stats')
@login_required
def get_discovery_stats():
    """
    Returns only the counts for each entity label.
    This allows the Discovery page to load instantly without fetching millions of rows.
    """
    db = get_db()
    
    # This query uses the new covering indexes to be extremely fast
    query = """
        SELECT entity_label, COUNT(*) as unique_count 
        FROM browse_cache 
        WHERE entity_label IN ({})
        GROUP BY entity_label
    """.format(','.join('?' for _ in ENTITY_LABELS_TO_DISPLAY))
    
    stats = db.execute(query, ENTITY_LABELS_TO_DISPLAY).fetchall()
    stats_map = {row['entity_label']: row['unique_count'] for row in stats}
    
    # Ensure all configured labels are present in the response, even if count is 0
    final_stats = {label: stats_map.get(label, 0) for label in ENTITY_LABELS_TO_DISPLAY}
    
    return jsonify({
        'stats': final_stats,
        'sorted_labels': ENTITY_LABELS_TO_DISPLAY
    })

@api_bp.route('/discover/list/<label>')
@login_required
def get_discovery_list(label):
    """
    Server-side pagination and filtering for a specific entity label.
    Lazy-loaded when the user expands an accordion.
    """
    db = get_db()
    
    # Pagination parameters
    page = request.args.get('page', 1, type=int)
    per_page = 100
    offset = (page - 1) * per_page
    
    # Filter and Sort parameters
    search_query = request.args.get('q', '').strip()
    sort_by = request.args.get('sort', 'mentions') # mentions, docs, alpha

    # Base query
    sql = "SELECT entity_text, document_count, appearance_count FROM browse_cache WHERE entity_label = ?"
    params = [label]

    # Apply Search Filter (if present)
    if search_query:
        # Uses the new COLLATE NOCASE index for fast case-insensitive search
        sql += " AND entity_text LIKE ?"
        params.append(f"%{search_query}%")

    # Apply Sorting
    if sort_by == 'alpha':
        sql += " ORDER BY entity_text COLLATE NOCASE ASC"
    elif sort_by == 'docs':
        sql += " ORDER BY document_count DESC, entity_text COLLATE NOCASE"
    else: # default 'mentions'
        # Uses the new index on (entity_label, appearance_count DESC)
        sql += " ORDER BY appearance_count DESC, entity_text COLLATE NOCASE"

    # Apply Pagination
    sql += " LIMIT ? OFFSET ?"
    params.extend([per_page, offset])

    results = db.execute(sql, params).fetchall()
    
    return jsonify({
        'items': [dict(row) for row in results],
        'has_more': len(results) == per_page
    })

@api_bp.route('/search/entities_autocomplete')
@login_required
def entities_autocomplete():
    """
    Fast endpoint for UI dropdowns. Searches the browse_cache for partial entity matches.
    Can optionally be filtered by a specific label.
    """
    query = request.args.get('q', '').strip()
    label_filter = request.args.get('label', '').strip()
    
    if not query or len(query) < 2:
        return jsonify([])
        
    db = get_db()
    
    sql = "SELECT entity_text as text, entity_label as label, appearance_count as count FROM browse_cache WHERE entity_text LIKE ?"
    params = [f"%{query}%"]
    
    if label_filter:
        sql += " AND entity_label = ?"
        params.append(label_filter)
        
    # Sort by how common it is, then alphabetical, limit to 10 for quick dropdown rendering
    sql += " ORDER BY appearance_count DESC, entity_text COLLATE NOCASE ASC LIMIT 10"
    
    results = db.execute(sql, params).fetchall()
    return jsonify([dict(row) for row in results])

@api_bp.route('/search')
@login_required
def api_global_search():
    """Provides a JSON endpoint for Hybrid Global Search (FTS + Semantic)"""
    query = request.args.get('q', '').strip()
    limit = request.args.get('limit', 15, type=int)
    
    mode = request.args.get('mode', 'hybrid').strip().lower()
    
    if not query:
        return jsonify([])
        
    db = get_db()
    
    # 1. Run the fast FTS text search
    fts_hits = _internal_fts_search(db, query, limit=limit * 2)

    rrf_scores = defaultdict(float)
    source_data = {}
    k = 60
    
    for rank, source in enumerate(fts_hits): 
        key = (source['doc_id'], source['page_number'])
        rrf_scores[key] += 1 / (k + rank + 1)
        if key not in source_data:
            # FTS uses <<< and >>> for highlighting
            source_data[key] = source.get('snippet', '').replace('<<<', '').replace('>>>', '')
            
    # 2. ONLY run the heavy semantic search if mode is 'hybrid'
    if mode == 'hybrid':
        sem_hits = _internal_semantic_search(db, query, limit=limit * 2)
        for rank, source in enumerate(sem_hits): 
            key = (source['doc_id'], source['page_number'])
            rrf_scores[key] += 1 / (k + rank + 1)
            if key not in source_data:
                 source_data[key] = source.get('snippet', '')
    
    sorted_keys = sorted(rrf_scores.keys(), key=lambda s: rrf_scores[s], reverse=True)
    
    # 3. Format output for Flutter
    results = []
    for doc_id, page_num in sorted_keys[:limit]:
        doc = db.execute("""
            SELECT d.relative_path, dm.csl_json 
            FROM documents d 
            LEFT JOIN document_metadata dm ON d.id = dm.doc_id 
            WHERE d.id = ?
        """, (doc_id,)).fetchone()
        
        title = doc['relative_path'] if doc else f"Document #{doc_id}"
        
        # --- NEW: Build metadata string into the title so it passes through existing pipelines automatically ---
        if doc and doc['csl_json']:
            try:
                csl = json.loads(doc['csl_json'])
                csl_title = csl.get('title', '')
                author = ""
                if csl.get('author') and csl['author'][0]:
                    author = csl['author'][0].get('literal') or csl['author'][0].get('family', '')
                year = ""
                if csl.get('issued', {}).get('date-parts'):
                    year = str(csl['issued']['date-parts'][0][0])
                
                parts = []
                if csl_title: parts.append(f'"{csl_title}"')
                if author: parts.append(f"by {author}")
                if year: parts.append(f"({year})")
                if parts: title += " | METADATA: " + " ".join(parts)
            except: pass

        snippet = source_data.get((doc_id, page_num), "Snippet unavailable.")
        
        results.append({
            'doc_id': doc_id,
            'page_number': page_num,
            'title': f"{title} (Page {page_num})",
            'snippet': snippet.strip()
        })
        
    return jsonify(results)

# ===================================================================
# --- NEW: ADVANCED AGENT SEARCH ENDPOINT ---
# ===================================================================
@api_bp.route('/search/advanced', methods=['POST'])
@csrf.exempt  # <-- THE FIX: Guarantee CSRF is disabled for this programmatic endpoint
@login_required
def api_advanced_search():
    """
    JSON API for Advanced Search (Used by Autonomous Agents).
    Now upgraded to perform HYBRID (FTS + Semantic) search while
    strictly respecting entity and file_type SQL filters.
    """
    data = request.get_json(silent=True) or {}
    
    query = data.get('q', '').strip()
    entities = data.get('entities', [])
    file_types = data.get('file_types', [])
    limit = data.get('limit', 5)

    db = get_db()
    
    # 1. Base Document Filters
    doc_where = ["d.status != 'Missing'"]
    doc_filter_params = []
    
    if file_types:
        placeholders = ','.join(['?'] * len(file_types))
        doc_where.append(f"d.file_type IN ({placeholders})")
        doc_filter_params.extend(file_types)
        
    doc_where_str = " AND ".join(doc_where)
    
    # 2. Entity Intersection SQL Logic
    entity_intersection_sql = ""
    entity_params = []
    if entities:
        entity_intersection_sql = """
            SELECT ea0.doc_id, ea0.page_number 
            FROM entity_appearances ea0
            JOIN entities e0 ON ea0.entity_id = e0.id
        """
        for i in range(1, len(entities)):
            entity_intersection_sql += f"""
                JOIN entity_appearances ea{i} 
                  ON ea0.doc_id = ea{i}.doc_id 
                  AND ea0.page_number = ea{i}.page_number
                JOIN entities e{i} ON ea{i}.entity_id = e{i}.id
            """
        ent_where_clauses = []
        for i, ent in enumerate(entities):
            clause = f"e{i}.text LIKE ?"
            entity_params.append(f"%{escape_like(ent.get('text', ''))}%")
            if ent.get('label'):
                clause += f" AND e{i}.label = ?"
                entity_params.append(ent['label'])
            ent_where_clauses.append(clause)
        entity_intersection_sql += " WHERE " + " AND ".join(ent_where_clauses)

    fetch_limit = limit

    # ==========================================================
    # SCENARIO A: Agent only searched for Entities (No keyword query)
    # ==========================================================
    if not query:
        if not entities:
            return jsonify([])
            
        sql_params = entity_params + doc_filter_params + [fetch_limit]
        sql_query = f"""
            SELECT d.id as doc_id, d.relative_path, d.file_type, 
                   tm.page_number, '' as snippet, dm.csl_json
            FROM (
                {entity_intersection_sql}
                AND ea0.doc_id IN (SELECT id FROM documents d WHERE {doc_where_str})
                ORDER BY ea0.doc_id, ea0.page_number
                LIMIT ?
            ) tm
            JOIN documents d ON tm.doc_id = d.id
            LEFT JOIN document_metadata dm ON d.id = dm.doc_id
            ORDER BY d.relative_path COLLATE NOCASE, tm.page_number;
        """
        db_results = db.execute(sql_query, sql_params).fetchall()
        
        results = []
        for row in db_results:
            row_dict = dict(row)
            page_row = db.execute("SELECT page_content FROM content_index WHERE doc_id = ? AND page_number = ?", (row_dict['doc_id'], row_dict['page_number'])).fetchone()
            if page_row:
                raw_text = page_row['page_content']
                snippet_html = _create_entity_snippet(raw_text, entities[0].get('text', ''), context=200)
                row_dict['snippet'] = re.sub(r'<[^>]+>', '', snippet_html)
            else:
                 row_dict['snippet'] = "Snippet unavailable."
                 
            # --- NEW: Extract and format CSL-JSON into a string ---
            metadata_str = ""
            if row_dict.get('csl_json'):
                try:
                    csl = json.loads(row_dict['csl_json'])
                    title = csl.get('title', '')
                    author = ""
                    if csl.get('author') and csl['author'][0]:
                        author = csl['author'][0].get('literal') or csl['author'][0].get('family', '')
                    year = ""
                    if csl.get('issued', {}).get('date-parts'):
                        year = str(csl['issued']['date-parts'][0][0])
                    
                    parts = []
                    if title: parts.append(f'"{title}"')
                    if author: parts.append(f"by {author}")
                    if year: parts.append(f"({year})")
                    if parts: metadata_str = " ".join(parts)
                except: pass
            
            row_dict['metadata_str'] = metadata_str
            row_dict.pop('csl_json', None)
                 
            results.append(row_dict)
            
        return jsonify(results)

    # ==========================================================
    # SCENARIO B: HYBRID SEARCH (Keyword + Vectors)
    # ==========================================================
    safe_query = re.sub(r'[^\w\s]', '', query).strip()
    words = [w for w in safe_query.split() if w.lower() != 'and']
    fts_query = " AND ".join([f'"{w}"' for w in words])

    rrf_scores = defaultdict(float)
    snippet_map = {}
    k = 60

    # --- 1. FULL-TEXT SEARCH PHASE ---
    if fts_query:
        if entities:
            fts_sql = f"""
                SELECT ci.doc_id, ci.page_number, snippet(ci.content_index, 2, '', '', '...', 40) as snippet, ci.rank
                FROM content_index ci
                JOIN ({entity_intersection_sql}) ei ON ci.doc_id = ei.doc_id AND ci.page_number = ei.page_number
                WHERE ci.doc_id IN (SELECT id FROM documents d WHERE {doc_where_str}) AND ci.content_index MATCH ? 
                ORDER BY ci.rank LIMIT ?
            """
            fts_params = entity_params + doc_filter_params + [fts_query, fetch_limit * 2]
        else:
            fts_sql = f"""
                SELECT doc_id, page_number, snippet(content_index, 2, '', '', '...', 40) as snippet, rank
                FROM content_index 
                WHERE doc_id IN (SELECT id FROM documents d WHERE {doc_where_str}) AND content_index MATCH ? 
                ORDER BY rank LIMIT ?
            """
            fts_params = doc_filter_params + [fts_query, fetch_limit * 2]
            
        fts_hits = db.execute(fts_sql, fts_params).fetchall()
        for rank, row in enumerate(fts_hits):
            key = (row['doc_id'], row['page_number'])
            rrf_scores[key] += 1 / (k + rank + 1)
            snippet_map[key] = row['snippet']

    # --- 2. SEMANTIC (VECTOR) SEARCH PHASE ---
    try:
        q_embed_res = ollama.embeddings(model=EMBEDDING_MODEL, prompt=query)
        q_vec = np.array(q_embed_res['embedding'], dtype=np.float32)
        norm_q_vec = q_vec / np.linalg.norm(q_vec)
        
        top_k_heap = []
        
        # Check both standard and super embedding tables
        for table in ["embedding_chunks", "super_embedding_chunks"]:
            try:
                if entities:
                    sem_sql = f"""
                        SELECT e.doc_id, e.page_number, e.chunk_text, e.embedding 
                        FROM {table} e
                        JOIN ({entity_intersection_sql}) ei ON e.doc_id = ei.doc_id AND e.page_number = ei.page_number
                        JOIN documents d ON e.doc_id = d.id
                        WHERE {doc_where_str}
                    """
                    sem_params = entity_params + doc_filter_params
                else:
                    sem_sql = f"""
                        SELECT e.doc_id, e.page_number, e.chunk_text, e.embedding 
                        FROM {table} e
                        JOIN documents d ON e.doc_id = d.id
                        WHERE {doc_where_str}
                    """
                    sem_params = doc_filter_params

                # Stream the batches to keep RAM usage extremely low
                cursor = db.execute(sem_sql, sem_params)
                while True:
                    batch = cursor.fetchmany(2000)
                    if not batch: 
                        break
                    
                    embeddings = np.array([np.frombuffer(row['embedding'], dtype=np.float32) for row in batch])
                    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
                    norms[norms == 0] = 1
                    norm_embeddings = embeddings / norms
                    
                    similarities = np.dot(norm_embeddings, norm_q_vec)
                    
                    for i, score in enumerate(similarities):
                        if len(top_k_heap) < fetch_limit * 2:
                            heapq.heappush(top_k_heap, (score, batch[i]['doc_id'], batch[i]['page_number'], batch[i]['chunk_text']))
                        else:
                            if score > top_k_heap[0][0]:
                                heapq.heapreplace(top_k_heap, (score, batch[i]['doc_id'], batch[i]['page_number'], batch[i]['chunk_text']))
            except sqlite3.OperationalError:
                continue # Skip if embedding table isn't baked yet
                
        # Merge Semantic scores into RRF
        top_k_heap.sort(key=lambda x: x[0], reverse=True)
        for rank, (score, d_id, p_num, chunk_text) in enumerate(top_k_heap):
            key = (d_id, p_num)
            rrf_scores[key] += 1 / (k + rank + 1)
            if key not in snippet_map:
                snippet_map[key] = chunk_text
                
    except Exception as e:
        print(f"[ERROR] Semantic phase failed during agent search: {e}")

    # --- 3. MERGE, SORT, AND RETURN ---
    sorted_keys = sorted(rrf_scores.keys(), key=lambda s: rrf_scores[s], reverse=True)[:fetch_limit]
    
    results = []
    for doc_id, page_num in sorted_keys:
        # --- MODIFIED: JOIN document_metadata to fetch CSL-JSON ---
        doc_row = db.execute("""
            SELECT d.relative_path, d.file_type, dm.csl_json 
            FROM documents d 
            LEFT JOIN document_metadata dm ON d.id = dm.doc_id 
            WHERE d.id = ?
        """, (doc_id,)).fetchone()
        
        if not doc_row: continue
        
        # Strip HTML markup since agents don't need UI tags
        clean_snippet = re.sub(r'<[^>]+>', '', snippet_map.get((doc_id, page_num), ""))
        
        # --- NEW: Generate Metadata String for Flutter ---
        metadata_str = ""
        if doc_row['csl_json']:
            try:
                csl = json.loads(doc_row['csl_json'])
                title = csl.get('title', '')
                author = ""
                if csl.get('author') and csl['author'][0]:
                    author = csl['author'][0].get('literal') or csl['author'][0].get('family', '')
                year = ""
                if csl.get('issued', {}).get('date-parts'):
                    year = str(csl['issued']['date-parts'][0][0])
                
                parts = []
                if title: parts.append(f'"{title}"')
                if author: parts.append(f"by {author}")
                if year: parts.append(f"({year})")
                if parts: metadata_str = " ".join(parts)
            except: pass

        results.append({
            'doc_id': doc_id,
            'relative_path': doc_row['relative_path'],
            'file_type': doc_row['file_type'],
            'page_number': page_num,
            'snippet': clean_snippet,
            'metadata_str': metadata_str # <-- Added this field
        })
        
    return jsonify(results)

# ===================================================================
# --- NEW: AI Context Endpoints for Node-Leaf ---
# ===================================================================

def _get_pages_for_topic(db, search_term):
    """Helper: Finds all pages where a specific term or entity appears."""
    pages_by_doc = defaultdict(set)
    
    # 1. Entity Search
    entity = db.execute("SELECT id FROM entities WHERE text LIKE ?", (f"%{search_term}%",)).fetchone()
    if entity:
        mentions = db.execute("SELECT doc_id, page_number FROM entity_appearances WHERE entity_id = ?", (entity['id'],)).fetchall()
        for mention in mentions:
            pages_by_doc[mention['doc_id']].add(mention['page_number'])

    # 2. FTS Search
    terms = {term for term in search_term.split()}
    terms.update({term.upper() for term in terms})
    escaped_terms = [term.replace('"', '""') for term in terms]
    
    if escaped_terms:
        fts_query = " OR ".join([f'"{term}"' for term in escaped_terms])
        matches = db.execute("SELECT doc_id, page_number FROM content_index WHERE content_index MATCH ?", (fts_query,)).fetchall()
        for match in matches:
            pages_by_doc[match['doc_id']].add(match['page_number'])
        
    return pages_by_doc

@api_bp.route('/search/intersection')
@login_required
def api_search_intersection():
    """Finds pages where ALL specified topics are mentioned together and returns the raw text."""
    topics = request.args.getlist('topic')
    if not topics:
        return jsonify({"context": "No topics provided."})
        
    db = get_db()
    
    # Get universe of pages for the first topic
    master_pages = _get_pages_for_topic(db, topics[0])
    
    # Intersect with subsequent topics
    for topic in topics[1:]:
        next_pages = _get_pages_for_topic(db, topic)
        temp_master = defaultdict(set)
        common_docs = set(master_pages.keys()).intersection(set(next_pages.keys()))
        
        for doc_id in common_docs:
            common_pages = master_pages[doc_id].intersection(next_pages[doc_id])
            if common_pages:
                temp_master[doc_id] = common_pages
        master_pages = temp_master

    if not master_pages:
        return jsonify({"context": f"No documents found containing all topics together: {', '.join(topics)}"})

    # Limit to top 5 docs, 3 pages each to protect LLM context window
    sources = []
    doc_count = 0
    for doc_id, page_numbers in sorted(master_pages.items()):
        if doc_count >= 5: break
        page_count = 0
        for page_num in sorted(list(page_numbers)):
            if page_count >= 3: break
            sources.append({"doc_id": doc_id, "page_number": page_num})
            page_count += 1
        doc_count += 1
        
    context = read_specific_pages(db, sources)
    return jsonify({"context": context})

@api_bp.route('/catalogs/all')
@login_required
def get_all_catalogs_api():
    """Returns a simple list of all catalogs for the Node-Leaf UI."""
    db = get_db()
    catalogs = db.execute("SELECT id, name, catalog_type FROM catalogs ORDER BY name COLLATE NOCASE").fetchall()
    return jsonify([dict(c) for c in catalogs])

@api_bp.route('/catalogs/<int:catalog_id>/context')
@login_required
def get_catalog_context(catalog_id):
    """Extracts the beginning pages of documents in a catalog for AI summarization."""
    db = get_db()
    limit = request.args.get('limit', 5, type=int) # Max docs to read
    
    # Get documents in the catalog
    docs = db.execute("SELECT doc_id FROM document_catalogs WHERE catalog_id = ? LIMIT ?", (catalog_id, limit)).fetchall()
    if not docs:
        return jsonify({"context": "This collection is empty or does not exist."})
        
    sources = []
    for doc in docs:
        # Grab first 2 pages of each document as context
        sources.append({"doc_id": doc['doc_id'], "page_number": 1})
        sources.append({"doc_id": doc['doc_id'], "page_number": 2})
        
    context = read_specific_pages(db, sources)
    return jsonify({"context": context})

# ===================================================================
# --- Relationship Endpoints ---
# ===================================================================

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

        if file_type in ['SRT', 'HTML', 'EML']:
            page_num_for_query = 1

        page_row = db.execute(
            "SELECT page_content FROM content_index WHERE doc_id = ? AND page_number = ?",
            (row_dict['doc_id'], page_num_for_query)
        ).fetchone()

        if page_row:
            content_for_snippet = page_row['page_content']

        if file_type == 'SRT':
            cue = db.execute("SELECT sequence, timestamp FROM srt_cues WHERE doc_id = ? AND sequence = ?", (row_dict['doc_id'], row_dict['page_number'])).fetchone()
            if cue:
                row_dict['srt_cue_sequence'] = cue['sequence']
                row_dict['srt_timestamp'] = cue['timestamp']
        
        row_dict['snippet'] = _create_manual_snippet(content_for_snippet, subject['text'], object_entity['text'], phrase)
        final_results.append(row_dict)
        
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

    results = []
    for row in db_results:
        row_dict = dict(row)
        content_for_snippet = ""
        doc_id_for_query = row_dict['doc_id'] 
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

# ===================================================================
# --- SYSTEM IDENTIFICATION & BRIEFING ---
# ===================================================================
@api_bp.route('/system/info')
@login_required
def system_info():
    """Public endpoint for the Flutter app to identify this specific database."""
    db = get_db()
    
    row = db.execute("SELECT value FROM app_settings WHERE key = 'instance_id'").fetchone()
    
    if row:
        instance_id = row['value']
    else:
        instance_id = str(uuid.uuid4())
        db.execute("INSERT INTO app_settings (key, value) VALUES ('instance_id', ?)", (instance_id,))
        db.commit()
        
    return jsonify({
        'project_name': getattr(g, 'project_name', 'Redleaf'),
        'instance_id': instance_id,
        'base_dir': str(BASE_DIR)
    })

@api_bp.route('/system/briefing')
@login_required
def system_briefing():
    """Generates a high-level briefing of the database contents for AI context."""
    db = get_db()
    briefing_parts = []

    try:
        total_docs = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        
        date_range = db.execute("""
            SELECT MIN(json_extract(csl_json, '$.issued."date-parts"[0][0]')) as min_year,
                   MAX(json_extract(csl_json, '$.issued."date-parts"[0][0]')) as max_year
            FROM document_metadata
        """).fetchone()
        
        if date_range['min_year'] and date_range['max_year']:
            date_str = f"Years covered: {date_range['min_year']} to {date_range['max_year']}"
        else:
            date_str = "Years covered: Unknown"

        top_tags = db.execute("""
            SELECT t.name, COUNT(dt.doc_id) as c 
            FROM tags t JOIN document_tags dt ON t.id = dt.tag_id 
            GROUP BY t.name ORDER BY c DESC LIMIT 5
        """).fetchall()
        tags_str = ", ".join([f"{t['name']} ({t['c']})" for t in top_tags]) if top_tags else "No tags yet"

        top_people = db.execute("SELECT entity_text FROM browse_cache WHERE entity_label = 'PERSON' ORDER BY appearance_count DESC LIMIT 5").fetchall()
        people_str = ", ".join([r['entity_text'] for r in top_people]) if top_people else "None"
        
        top_orgs = db.execute("SELECT entity_text FROM browse_cache WHERE entity_label = 'ORG' ORDER BY appearance_count DESC LIMIT 5").fetchall()
        orgs_str = ", ".join([r['entity_text'] for r in top_orgs]) if top_orgs else "None"

        briefing = (
            f"- Total Documents: {total_docs}\n"
            f"- {date_str}\n"
            f"- Top Tags: {tags_str}\n"
            f"- Prominent People: {people_str}\n"
            f"- Prominent Groups: {orgs_str}"
        )
        return jsonify({'briefing': briefing})
    except Exception as e:
        return jsonify({'briefing': f"System briefing unavailable (Database error: {e})"})