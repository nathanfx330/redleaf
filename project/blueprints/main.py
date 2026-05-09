# --- File: ./project/blueprints/main.py ---
import os
import queue
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import unquote
from datetime import datetime
import json

from flask import (
    Blueprint, render_template, g, request, redirect, url_for, flash, send_from_directory, abort, session
)

from ..database import get_db
from ..background import task_queue
from ..utils import _get_dashboard_state, _truncate_long_snippet, _create_entity_snippet
from ..config import DOCUMENTS_DIR, ENTITY_LABELS_TO_DISPLAY, resolve_document_path
from .auth import login_required, admin_required, SecureForm

# --- IMPORT OPTIMIZED DATA FETCHING LOGIC ---
from .api.documents import fetch_dashboard_data
from .api.helpers import escape_like 

main_bp = Blueprint('main', __name__)

@main_bp.route('/')
@main_bp.route('/dashboard')
@login_required
def dashboard():
    status_filters = None
    if 'status' in request.args:
        status_filters = request.args.getlist('status')
    
    sort_key = request.args.get('sort_key', 'relative_path')
    sort_dir = request.args.get('sort_dir', 'asc')

    initial_data = fetch_dashboard_data(
        user_id=g.user['id'],
        page=1, 
        per_page=25, 
        sort_key=sort_key, 
        sort_dir=sort_dir,
        status_filters=status_filters
    )
    
    form = SecureForm()
    
    return render_template('dashboard.html', 
                           documents=initial_data['documents'], 
                           total_documents=initial_data['total_documents'],
                           queue_size=initial_data['queue_size'], 
                           form=form, 
                           task_states=initial_data['task_states'],
                           doc_dir=DOCUMENTS_DIR,
                           initial_data=initial_data)

@main_bp.route('/dashboard/discover')
@login_required
def dashboard_discover():
    task_queue.put(('discover', None))
    flash("Discovery task queued. It will start shortly.", "info")
    return redirect(url_for('main.dashboard'))

@main_bp.route('/dashboard/process/all_new')
@login_required
def dashboard_process_all_new():
    db = get_db()
    doc_ids = [row['id'] for row in db.execute("SELECT id FROM documents WHERE status = 'New'").fetchall()]
    if not doc_ids:
        flash("No 'New' documents found to process.", "info")
    else:
        db.execute("UPDATE documents SET status = 'Queued', status_message = 'Pending assignment' WHERE status = 'New'")
        db.commit()

        for doc_id in doc_ids:
            task_queue.put(('process', doc_id))
        flash(f"Queued {len(doc_ids)} documents for processing.", "success")
        
    return redirect(url_for('main.dashboard', sort_key='status', sort_dir='asc'))

@main_bp.route('/dashboard/process/<int:doc_id>')
@login_required
def dashboard_process_single(doc_id):
    db = get_db()
    db.execute("UPDATE documents SET status = 'Queued', status_message = 'Pending assignment' WHERE id = ?", (doc_id,))
    db.commit()
    
    task_queue.put(('process', doc_id))
    flash(f"Queued document ID {doc_id} for re-processing.", "info")
    
    return redirect(url_for('main.dashboard', sort_key='status', sort_dir='asc'))

@main_bp.route('/dashboard/update_cache')
@login_required
def dashboard_update_cache():
    task_queue.put(('cache', None))
    flash("Browse cache update task queued. It will start shortly.", "info")
    return redirect(url_for('main.dashboard'))

@main_bp.route('/dashboard/reset_database', methods=['POST'])
@admin_required
def dashboard_reset_database():
    form = SecureForm()
    if form.validate_on_submit():
        session.clear()
        from ..database import close_connection
        close_connection(None)
        from ..config import DATABASE_FILE
        db_path = Path(DATABASE_FILE)
        if db_path.exists():
            while not task_queue.empty():
                try: task_queue.get_nowait()
                except queue.Empty: break
            db_path.unlink()
        flash("System has been completely reset. Please create a new admin account.", "success")
        return redirect(url_for('auth.setup'))
    else:
        flash("CSRF validation failed.", "danger")
        return redirect(url_for('main.dashboard'))

@main_bp.route('/discover')
@login_required
def discover_view():
    form = SecureForm()
    return render_template('discover.html', 
                           sorted_labels=ENTITY_LABELS_TO_DISPLAY, 
                           form=form)

@main_bp.route('/discover/advanced')
@login_required
def advanced_search_view():
    db = get_db()
    catalogs = db.execute("SELECT id, name FROM catalogs ORDER BY name COLLATE NOCASE").fetchall()
    tags = db.execute("SELECT name FROM tags ORDER BY name COLLATE NOCASE").fetchall()
    types_query = db.execute("SELECT DISTINCT file_type FROM documents WHERE status != 'Missing' AND file_type IS NOT NULL").fetchall()
    all_types = sorted([row['file_type'] for row in types_query])
    
    form = SecureForm()
    return render_template('advanced_search.html', catalogs=catalogs, tags=tags, all_types=all_types, form=form)

@main_bp.route('/discover/search')
@login_required
def search_results():
    query = request.args.get('q', '').strip()
    title_query = request.args.get('title', '').strip()
    catalog_id = request.args.get('catalog_id', type=int)
    tag_name = request.args.get('tag_name', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page
    
    # --- PARSE ENTITY FILTERS WITH MODE ---
    raw_entity_texts = request.args.getlist('entity_text')
    raw_entity_labels = request.args.getlist('entity_label')
    raw_entity_modes = request.args.getlist('entity_mode')
    
    entity_filters = []
    page_entities = []
    doc_entities = []
    exclude_entities = []

    for i in range(len(raw_entity_texts)):
        text = raw_entity_texts[i].strip()
        if text:
            label = raw_entity_labels[i] if i < len(raw_entity_labels) else ""
            mode = raw_entity_modes[i] if i < len(raw_entity_modes) else "doc"
            
            ent_dict = {"text": text, "label": label, "mode": mode}
            entity_filters.append(ent_dict)
            
            if mode == 'exclude':
                exclude_entities.append(ent_dict)
            elif mode == 'page':
                page_entities.append(ent_dict)
            else:
                doc_entities.append(ent_dict)
    
    if not query and not title_query and not catalog_id and not tag_name and not entity_filters and 'filtered' not in request.args:
        return redirect(url_for('main.discover_view'))
        
    db = get_db()
    
    # 1. Build Document Filters (The universe of allowed documents)
    doc_where = ["status != 'Missing'"]
    doc_params = []
    
    if title_query:
        doc_where.append("relative_path LIKE ? ESCAPE '\\'")
        doc_params.append(f"%{escape_like(title_query)}%")
        
    if catalog_id:
        doc_where.append("id IN (SELECT doc_id FROM document_catalogs WHERE catalog_id = ?)")
        doc_params.append(catalog_id)
        
    if tag_name:
        doc_where.append("id IN (SELECT dt.doc_id FROM document_tags dt JOIN tags t ON dt.tag_id = t.id WHERE t.name = ?)")
        doc_params.append(tag_name)
        
    # --- APPLY DOC-LEVEL ENTITY EXCLUSIONS/INCLUSIONS ---
    for ent in exclude_entities:
        clause = "id NOT IN (SELECT doc_id FROM entity_appearances ea JOIN entities e ON ea.entity_id = e.id WHERE e.text LIKE ?"
        doc_params.append(f"%{escape_like(ent['text'])}%")
        if ent['label']:
            clause += " AND e.label = ?"
            doc_params.append(ent['label'])
        clause += ")"
        doc_where.append(clause)

    for ent in doc_entities:
        clause = "id IN (SELECT doc_id FROM entity_appearances ea JOIN entities e ON ea.entity_id = e.id WHERE e.text LIKE ?"
        doc_params.append(f"%{escape_like(ent['text'])}%")
        if ent['label']:
            clause += " AND e.label = ?"
            doc_params.append(ent['label'])
        clause += ")"
        doc_where.append(clause)
        
    doc_where_str = " AND ".join(doc_where)
    
    # 2. Handle Text Search Context
    if query:
        safe_query = re.sub(r'[^\w\s]', '', query).strip()
        words = [w for w in safe_query.split() if w.lower() != 'and']
        fts_query = " AND ".join([f'"{w}"' for w in words])
    else:
        fts_query = ""
        
    # 3. Fetch valid types to power the UI checkboxes on the results page
    base_types_sql = f"SELECT DISTINCT d.file_type FROM documents d WHERE {doc_where_str} AND d.file_type IS NOT NULL"
    types_params = list(doc_params)
    
    if fts_query:
        base_types_sql = f"""
            SELECT DISTINCT d.file_type 
            FROM content_index ci
            JOIN documents d ON ci.doc_id = d.id
            WHERE d.id IN (SELECT id FROM documents WHERE {doc_where_str})
            AND d.file_type IS NOT NULL 
            AND ci.content_index MATCH ?
        """
        types_params.append(fts_query)
        
    # Restrict types to those containing the page intersection
    entity_intersection_sql = ""
    entity_params = []
    if page_entities:
        entity_intersection_sql = """
            SELECT ea0.doc_id, ea0.page_number 
            FROM entity_appearances ea0
            JOIN entities e0 ON ea0.entity_id = e0.id
        """
        for i in range(1, len(page_entities)):
            entity_intersection_sql += f"""
                JOIN entity_appearances ea{i} 
                  ON ea0.doc_id = ea{i}.doc_id 
                  AND ea0.page_number = ea{i}.page_number
                JOIN entities e{i} ON ea{i}.entity_id = e{i}.id
            """
            
        ent_where_clauses = []
        for i, ent in enumerate(page_entities):
            clause = f"e{i}.text LIKE ?"
            entity_params.append(f"%{escape_like(ent['text'])}%")
            if ent['label']:
                clause += f" AND e{i}.label = ?"
                entity_params.append(ent['label'])
            ent_where_clauses.append(clause)
            
        entity_intersection_sql += " WHERE " + " AND ".join(ent_where_clauses)
        
        base_types_sql = f"""
            SELECT DISTINCT d.file_type FROM ({base_types_sql}) as temp_types
            JOIN documents d ON d.file_type = temp_types.file_type
            JOIN ({entity_intersection_sql}) as ei ON d.id = ei.doc_id
        """
        types_params.extend(entity_params)
        
    types_query_result = db.execute(base_types_sql, types_params).fetchall()
    all_types = sorted([row['file_type'] for row in types_query_result])
    
    if 'filtered' in request.args:
        raw_selected = request.args.getlist('type')
        selected_types = [t for t in raw_selected if t in all_types]
        if raw_selected and not selected_types and all_types:
            return render_template('search_results.html', query=query, title_query=title_query, catalog_id=catalog_id, tag_name=tag_name, entity_filters=entity_filters, results=[], page=page, has_next=False, all_types=all_types, selected_types=[])
    else:
        selected_types = all_types
        
    if selected_types and len(selected_types) < len(all_types):
        placeholders = ','.join(['?'] * len(selected_types))
        doc_where.append(f"file_type IN ({placeholders})")
        doc_params.extend(selected_types)
        
    doc_where_str = " AND ".join(doc_where)
    fetch_limit = per_page + 1
    
    # 4. Main Query Execution
    
    if fts_query and page_entities:
        # BOHT FTS AND PAGE ENTITY SEARCH
        sql_params = entity_params + doc_params + [fts_query, fetch_limit, offset]
        sql_query = f"""
            SELECT d.id as doc_id, d.relative_path, d.color, d.page_count, d.file_type, 
                   tm.page_number, tm.snippet,
                   d.cached_comment_count as comment_count,
                   (d.cached_tag_count > 0) as has_tags,
                   (SELECT 1 FROM document_curation WHERE doc_id = d.id AND user_id = ?) as has_personal_note,
                   (SELECT GROUP_CONCAT(c.name, ', ') FROM catalogs c JOIN document_catalogs dc ON c.id = dc.catalog_id WHERE dc.doc_id = d.id) as catalog_names
            FROM (
                SELECT ci.doc_id, ci.page_number, snippet(ci.content_index, 2, '<strong>', '</strong>', '...', 20) as snippet, ci.rank
                FROM content_index ci
                JOIN ({entity_intersection_sql}) ei ON ci.doc_id = ei.doc_id AND ci.page_number = ei.page_number
                WHERE ci.doc_id IN (SELECT id FROM documents WHERE {doc_where_str}) AND ci.content_index MATCH ? 
                ORDER BY ci.rank 
                LIMIT ? OFFSET ?
            ) tm
            JOIN documents d ON tm.doc_id = d.id
            ORDER BY tm.rank;
        """
        db_results = db.execute(sql_query, [g.user['id']] + sql_params).fetchall()
        
    elif fts_query:
        # FTS SEARCH (Doc-level entities already handled in doc_where_str)
        sql_params = doc_params + [fts_query, fetch_limit, offset]
        sql_query = f"""
            SELECT d.id as doc_id, d.relative_path, d.color, d.page_count, d.file_type, 
                   tm.page_number, tm.snippet,
                   d.cached_comment_count as comment_count,
                   (d.cached_tag_count > 0) as has_tags,
                   (SELECT 1 FROM document_curation WHERE doc_id = d.id AND user_id = ?) as has_personal_note,
                   (SELECT GROUP_CONCAT(c.name, ', ') FROM catalogs c JOIN document_catalogs dc ON c.id = dc.catalog_id WHERE dc.doc_id = d.id) as catalog_names
            FROM (
                SELECT doc_id, page_number, snippet(content_index, 2, '<strong>', '</strong>', '...', 20) as snippet, rank
                FROM content_index 
                WHERE doc_id IN (SELECT id FROM documents WHERE {doc_where_str}) AND content_index MATCH ? 
                ORDER BY rank 
                LIMIT ? OFFSET ?
            ) tm
            JOIN documents d ON tm.doc_id = d.id
            ORDER BY tm.rank;
        """
        db_results = db.execute(sql_query, [g.user['id']] + sql_params).fetchall()
        
    elif page_entities:
        # PAGE ENTITY INTERSECTION ONLY (No FTS keyword provided)
        sql_params = entity_params + doc_params + [fetch_limit, offset]
        sql_query = f"""
            SELECT d.id as doc_id, d.relative_path, d.color, d.page_count, d.file_type, 
                   tm.page_number, '' as snippet,
                   d.cached_comment_count as comment_count,
                   (d.cached_tag_count > 0) as has_tags,
                   (SELECT 1 FROM document_curation WHERE doc_id = d.id AND user_id = ?) as has_personal_note,
                   (SELECT GROUP_CONCAT(c.name, ', ') FROM catalogs c JOIN document_catalogs dc ON c.id = dc.catalog_id WHERE dc.doc_id = d.id) as catalog_names
            FROM (
                {entity_intersection_sql}
                AND ea0.doc_id IN (SELECT id FROM documents WHERE {doc_where_str})
                ORDER BY ea0.doc_id, ea0.page_number
                LIMIT ? OFFSET ?
            ) tm
            JOIN documents d ON tm.doc_id = d.id
            ORDER BY d.relative_path COLLATE NOCASE, tm.page_number;
        """
        db_results = db.execute(sql_query, [g.user['id']] + sql_params).fetchall()
        
    else:
        # METADATA OR DOC ENTITIES ONLY SEARCH (No FTS, no Page Entities)
        sql_params = doc_params + [fetch_limit, offset]
        sql_query = f"""
            SELECT d.id as doc_id, d.relative_path, d.color, d.page_count, d.file_type, 
                   1 as page_number, '' as snippet,
                   d.cached_comment_count as comment_count,
                   (d.cached_tag_count > 0) as has_tags,
                   (SELECT 1 FROM document_curation WHERE doc_id = d.id AND user_id = ?) as has_personal_note,
                   (SELECT GROUP_CONCAT(c.name, ', ') FROM catalogs c JOIN document_catalogs dc ON c.id = dc.catalog_id WHERE dc.doc_id = d.id) as catalog_names
            FROM documents d
            WHERE d.id IN (SELECT id FROM documents WHERE {doc_where_str})
            ORDER BY d.relative_path COLLATE NOCASE
            LIMIT ? OFFSET ?;
        """
        db_results = db.execute(sql_query, [g.user['id']] + sql_params).fetchall()
        
    has_next = len(db_results) > per_page
    if has_next:
        db_results = db_results[:per_page]
        
    results = []
    highlight_re = re.compile('<strong>(.*?)</strong>', re.DOTALL)
    
    for row in db_results:
        row_dict = dict(row)
        
        # Generate custom snippet for entity-only searches
        if (page_entities or doc_entities) and not fts_query:
            page_row = db.execute("SELECT page_content FROM content_index WHERE doc_id = ? AND page_number = ?", (row_dict['doc_id'], row_dict['page_number'])).fetchone()
            if page_row:
                raw_text = page_row['page_content']
                primary_entity = page_entities[0]['text'] if page_entities else doc_entities[0]['text']
                row_dict['snippet'] = _create_entity_snippet(raw_text, primary_entity)
            else:
                 row_dict['snippet'] = "<em>Could not load text for snippet.</em>"

        if row_dict.get('file_type') == 'SRT' and row_dict.get('snippet'):
            snippet_html = row_dict['snippet']
            match = highlight_re.search(snippet_html)
            if match:
                highlighted_term = match.group(1).strip()
                cue_row = db.execute(
                    "SELECT sequence FROM srt_cues WHERE doc_id = ? AND dialogue LIKE ? LIMIT 1",
                    (row_dict['doc_id'], f'%{highlighted_term}%')
                ).fetchone()
                if cue_row:
                    row_dict['page_number'] = cue_row['sequence']
            row_dict['snippet'] = _truncate_long_snippet(snippet_html)
            
        results.append(row_dict)
        
    return render_template('search_results.html', 
                           query=query, 
                           title_query=title_query,
                           catalog_id=catalog_id,
                           tag_name=tag_name,
                           entity_filters=entity_filters,
                           results=results, 
                           page=page, 
                           has_next=has_next, 
                           all_types=all_types, 
                           selected_types=selected_types)

@main_bp.route('/discover/entity/<label>/<path:text>')
@login_required
def entity_detail(label, text):
    db = get_db()
    entity_text = unquote(text)
    
    entity = db.execute(
        "SELECT id FROM entities WHERE text = ? AND label = ?", 
        (entity_text, label)
    ).fetchone()

    if not entity:
        abort(404, "Entity not found in the database.")
    
    form = SecureForm()
    
    return render_template('entity_detail.html', 
                           label=label, 
                           text=entity_text, 
                           entity_id=entity['id'],
                           form=form)

@main_bp.route('/profile/<int:entity_id>')
@login_required
def entity_profile(entity_id):
    """Renders the new, dedicated profile page for an entity."""
    db = get_db()
    entity = db.execute("SELECT id, text, label FROM entities WHERE id = ?", (entity_id,)).fetchone()
    if not entity:
        abort(404, "Entity not found.")
    
    form = SecureForm()
    
    return render_template('profile.html', 
                           entity=entity, 
                           entity_id=entity_id,
                           form=form)

@main_bp.route('/discover/relationship')
@login_required
def relationship_detail_view():
    subject_id = request.args.get('subject_id', type=int)
    object_id = request.args.get('object_id', type=int)
    phrase = request.args.get('phrase', '')
    if not all([subject_id, object_id, phrase]):
        abort(400, "Missing required parameters for relationship detail.")
    db = get_db()
    subject = db.execute("SELECT text, label FROM entities WHERE id = ?", (subject_id,)).fetchone()
    object_entity = db.execute("SELECT text, label FROM entities WHERE id = ?", (object_id,)).fetchone()
    if not all([subject, object_entity]):
        abort(404, "One or more entities for this relationship could not be found.")
    return render_template('relationship_detail.html', subject=subject, object_entity=object_entity, phrase=phrase, subject_id=subject_id, object_id=object_id)

@main_bp.route('/tags')
@login_required
def tags_index():
    db = get_db()
    tags = db.execute("SELECT t.name, COUNT(dt.doc_id) as doc_count FROM tags t LEFT JOIN document_tags dt ON t.id = dt.tag_id GROUP BY t.id, t.name ORDER BY t.name COLLATE NOCASE").fetchall()
    catalogs = db.execute("SELECT id, name FROM catalogs ORDER BY name COLLATE NOCASE").fetchall()
    form = SecureForm()
    return render_template('tags_index.html', tags=tags, catalogs=catalogs, form=form)

@main_bp.route('/catalogs')
@login_required
def catalog_view():
    db = get_db()
    view_query = """
        SELECT 
            c.id as catalog_id, c.name as catalog_name, c.description as catalog_description, 
            d.id as doc_id, d.relative_path, d.color, 
            (SELECT COUNT(*) FROM document_comments WHERE doc_id = d.id) as comment_count, 
            (SELECT 1 FROM document_curation WHERE doc_id = d.id AND user_id = ?) as has_personal_note, 
            (SELECT 1 FROM document_tags WHERE doc_id = d.id LIMIT 1) as has_tags 
        FROM catalogs c 
        LEFT JOIN document_catalogs dc ON c.id = dc.catalog_id 
        LEFT JOIN documents d ON dc.doc_id = d.id 
        WHERE c.catalog_type IN ('user', 'favorites')
        ORDER BY c.name COLLATE NOCASE, d.relative_path COLLATE NOCASE;
    """
    view_results = db.execute(view_query, (g.user['id'],)).fetchall()
    catalogs_for_view = {}
    for row in view_results:
        cat_id = row['catalog_id']
        if cat_id not in catalogs_for_view:
            catalogs_for_view[cat_id] = {'id': cat_id, 'name': row['catalog_name'], 'description': row['catalog_description'], 'documents': []}
        if row['doc_id']:
            doc_data = dict(row)
            doc_data['id'] = doc_data.pop('doc_id')
            catalogs_for_view[cat_id]['documents'].append(doc_data)
            
    manage_query = """
        SELECT c.id, c.name, c.description, COUNT(dc.doc_id) as doc_count 
        FROM catalogs c 
        LEFT JOIN document_catalogs dc ON c.id = dc.catalog_id 
        WHERE c.catalog_type IN ('user', 'favorites')
        GROUP BY c.id, c.name, c.description 
        ORDER BY c.name COLLATE NOCASE;
    """
    catalogs_for_management = db.execute(manage_query).fetchall()
    form = SecureForm()
    return render_template('catalogs.html', catalogs_for_view=list(catalogs_for_view.values()), catalogs_for_management=catalogs_for_management, form=form)

@main_bp.route('/catalogs/create', methods=['POST'])
@login_required
def create_catalog():
    form = SecureForm()
    if form.validate_on_submit():
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        if not name:
            flash('Catalog name cannot be empty.', 'danger')
        else:
            db = get_db()
            try:
                db.execute("INSERT INTO catalogs (name, description) VALUES (?, ?)", (name, description))
                db.commit()
                flash(f"Catalog '{name}' created successfully.", 'success')
            except sqlite3.IntegrityError:
                db.rollback()
                flash('A catalog with this name already exists.', 'danger')
    else:
        flash("CSRF validation failed.", "danger")
    return redirect(url_for('main.catalog_view'))

@main_bp.route('/catalogs/delete/<int:catalog_id>', methods=['POST'])
@login_required
def delete_catalog(catalog_id):
    form = SecureForm()
    if form.validate_on_submit():
        db = get_db()
        fav_catalog = db.execute("SELECT id FROM catalogs WHERE name = '⭐ Favorites'").fetchone()
        if fav_catalog and fav_catalog['id'] == catalog_id:
            flash('The Favorites catalog cannot be deleted.', 'danger')
        else:
            cursor = db.execute("DELETE FROM catalogs WHERE id = ?", (catalog_id,))
            db.commit()
            if cursor.rowcount > 0:
                flash('Catalog deleted successfully.', 'success')
            else:
                flash('Catalog not found.', 'warning')
    else:
        flash("CSRF validation failed.", "danger")
    return redirect(url_for('main.catalog_view'))

@main_bp.route('/send_to_synthesis/<int:doc_id>')
@login_required
def send_to_synthesis(doc_id):
    db = get_db()
    most_recent_report = db.execute(
        "SELECT id FROM synthesis_reports WHERE owner_id = ? ORDER BY updated_at DESC, id ASC LIMIT 1",
        (g.user['id'],)
    ).fetchone()
    if most_recent_report:
        report_id_to_load = most_recent_report['id']
    else:
        try:
            title = f"My First Report"
            cursor = db.execute(
                "INSERT INTO synthesis_reports (title, owner_id, updated_at) VALUES (?, ?, ?)",
                (title, g.user['id'], datetime.now())
            )
            db.commit()
            report_id_to_load = cursor.lastrowid
            flash("Welcome to Synthesis! We've created your first report.", "success")
        except Exception:
            flash("Could not create a new synthesis report.", "danger")
            return redirect(url_for('main.document_view', doc_id=doc_id))
    return redirect(url_for('synthesis.report_view', report_id=report_id_to_load, load_doc=doc_id))

# --- NEW: SECURE DOWNLOAD ROUTE ---
@main_bp.route('/download/<int:doc_id>')
@login_required
def download_document(doc_id):
    db = get_db()
    
    # 1. Check Permissions
    allow_downloads_row = db.execute("SELECT value FROM app_settings WHERE key = 'allow_document_downloads'").fetchone()
    allow_downloads = allow_downloads_row['value'] == 'true' if allow_downloads_row else False
    
    if not allow_downloads and g.user['role'] != 'admin':
        abort(403, "Document downloads are currently disabled by the administrator.")
        
    # 2. Get Document Path
    doc = db.execute("SELECT relative_path FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc:
        abort(404)
        
    resolved_path = resolve_document_path(doc['relative_path']).resolve()
    
    # 3. Security Check (Same as serve_document to prevent directory traversal)
    parts = Path(doc['relative_path']).parts
    if parts and parts[0].endswith('.rlink'):
        rlink_file = DOCUMENTS_DIR / parts[0]
        if not rlink_file.is_file():
            abort(403)
        target_base = Path(rlink_file.read_text(encoding='utf-8').strip()).resolve()
        if not str(resolved_path).startswith(str(target_base)):
            abort(403)
    else:
        if not str(resolved_path).startswith(str(DOCUMENTS_DIR.resolve())):
            abort(403)
            
    if not resolved_path.exists() or not resolved_path.is_file():
        abort(404)
        
    return send_from_directory(resolved_path.parent, resolved_path.name, as_attachment=True)
# --------------------------------

@main_bp.route('/document/<int:doc_id>')
@login_required
def document_view(doc_id):
    db = get_db()
    
    query = """
        SELECT d.*, dm.csl_json 
        FROM documents d
        LEFT JOIN document_metadata dm ON d.id = dm.doc_id
        WHERE d.id = ?
    """
    doc = db.execute(query, (doc_id,)).fetchone()
    
    if not doc:
        abort(404)

    # --- NEW: Fetch Download Permission ---
    allow_downloads_row = db.execute("SELECT value FROM app_settings WHERE key = 'allow_document_downloads'").fetchone()
    allow_downloads = allow_downloads_row['value'] == 'true' if allow_downloads_row else False
    # --------------------------------------

    form = SecureForm()
    timestamp = int(time.time())
    
    pills_data = {}
    if doc['csl_json']:
        try:
            csl_data = json.loads(doc['csl_json'])
            if csl_data.get('type') in ['interview', 'broadcast']:
                pills_data['podcast_title'] = csl_data.get('container-title')
                pills_data['episode_title'] = csl_data.get('title')
                
                if csl_data.get('author') and csl_data['author'][0]:
                    pills_data['author'] = csl_data['author'][0].get('literal') or csl_data['author'][0].get('family')
                
                if csl_data.get('issued', {}).get('date-parts', [[]])[0]:
                    pills_data['year'] = csl_data['issued']['date-parts'][0][0]
        
        except (json.JSONDecodeError, IndexError, TypeError):
            print(f"NOTE: Could not parse CSL JSON for doc_id {doc_id} to generate pills. The data might be malformed.")
            pills_data = {}
    
    return render_template('document_view.html', 
                           doc=doc, 
                           form=form, 
                           timestamp=timestamp,
                           pills_data=pills_data,
                           allow_downloads=allow_downloads) 

# --- FIXED: Serve Document securely handles .rlink virtual paths ---
@main_bp.route('/serve_doc/<path:relative_path>')
@login_required
def serve_document(relative_path):
    resolved_path = resolve_document_path(relative_path).resolve()
    
    parts = Path(relative_path).parts
    if parts and parts[0].endswith('.rlink'):
        # It's an alias. Verify the alias file actually exists to prevent arbitrary path loading
        rlink_file = DOCUMENTS_DIR / parts[0]
        if not rlink_file.is_file():
            abort(403)
        # Prevent escaping the intended target directory using ../
        target_base = Path(rlink_file.read_text(encoding='utf-8').strip()).resolve()
        if not str(resolved_path).startswith(str(target_base)):
            abort(403)
    else:
        # Standard file, verify it stays inside the documents directory
        if not str(resolved_path).startswith(str(DOCUMENTS_DIR.resolve())):
            abort(403)
            
    if not resolved_path.exists() or not resolved_path.is_file():
        abort(404)
        
    return send_from_directory(resolved_path.parent, resolved_path.name)

@main_bp.route('/view_pdf/<int:doc_id>')
@login_required
def view_pdf_document(doc_id):
    db = get_db()
    doc_meta = db.execute("SELECT relative_path, file_type, last_pdf_zoom, last_pdf_page FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc_meta or doc_meta['file_type'] != 'PDF':
        abort(404)
    pdf_url = url_for('main.serve_document', relative_path=doc_meta['relative_path'])
    form = SecureForm()
    return render_template('pdf_viewer.html', 
                           pdf_url=pdf_url, 
                           doc_title=doc_meta['relative_path'], 
                           doc_id=doc_id, 
                           last_pdf_zoom=doc_meta['last_pdf_zoom'],
                           last_pdf_page=doc_meta['last_pdf_page'],
                           form=form)

@main_bp.route('/view_text/<int:doc_id>')
@login_required
def view_text_document(doc_id):
    db = get_db()
    doc_meta = db.execute("SELECT relative_path, file_type FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc_meta or doc_meta['file_type'] != 'TXT':
        abort(404)
    pages_cursor = db.execute("SELECT page_content FROM content_index WHERE doc_id = ? ORDER BY page_number ASC", (doc_id,))
    pages = [row['page_content'] for row in pages_cursor.fetchall()]
    if not pages:
        return "This text document has not been indexed yet or contains no content.", 404
    return render_template('text_viewer.html', pages=pages, doc_title=doc_meta['relative_path'], doc_id=doc_id)

@main_bp.route('/view_html/<int:doc_id>')
@login_required
def view_html_document(doc_id):
    db = get_db()
    doc_meta = db.execute("SELECT relative_path, file_type FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc_meta or doc_meta['file_type'] != 'HTML':
        abort(404)
    pages_cursor = db.execute("SELECT page_content FROM content_index WHERE doc_id = ? ORDER BY page_number ASC", (doc_id,))
    pages = [row['page_content'] for row in pages_cursor.fetchall()]
    if not pages:
        return "This HTML document has not been indexed yet or contains no extractable content.", 404
    return render_template('html_viewer.html', pages=pages, doc_title=doc_meta['relative_path'], doc_id=doc_id)

@main_bp.route('/view_eml/<int:doc_id>')
@login_required
def view_eml_document(doc_id):
    db = get_db()
    doc_meta = db.execute("SELECT relative_path, file_type FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc_meta or doc_meta['file_type'] != 'EML':
        abort(404)
    
    email_data = db.execute("SELECT * FROM email_metadata WHERE doc_id = ?", (doc_id,)).fetchone()
    
    body_content_row = db.execute("SELECT page_content FROM content_index WHERE doc_id = ? AND page_number = 1", (doc_id,)).fetchone()
    body_content = body_content_row['page_content'] if body_content_row else "No body content found for this email."
    
    return render_template(
        'eml_viewer.html', 
        doc_title=doc_meta['relative_path'], 
        doc_id=doc_id,
        email=email_data,
        body=body_content
    )

def _parse_srt_for_viewer(srt_content):
    """Parses SRT content into a list of dicts for the viewer template."""
    cues = []
    cue_pattern = re.compile(r'(\d+)\s*\n(\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3})\s*\n(.*?)(?=\n\n|\Z)', re.DOTALL)
    for match in cue_pattern.finditer(srt_content):
        cues.append({
            'sequence': match.group(1),
            'timestamp': match.group(2).replace('-->', '→'),
            'dialogue': match.group(3).strip().replace('\n', ' ')
        })
    return cues

@main_bp.route('/view_srt/<int:doc_id>')
@login_required
def view_srt_document(doc_id):
    db = get_db()
    doc = db.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc or doc['file_type'] != 'SRT':
        abort(404)
        
    srt_path = resolve_document_path(doc['relative_path'])
    if not srt_path.exists():
        abort(404, "SRT file is missing from the filesystem.")
        
    try:
        content = srt_path.read_text(encoding='utf-8', errors='ignore')
        cues = _parse_srt_for_viewer(content)
    except Exception:
        cues = []

    form = SecureForm()
    
    return render_template('srt_viewer.html', 
                           cues=cues, 
                           doc_title=doc['relative_path'], 
                           doc_id=doc_id, 
                           doc=doc, 
                           form=form)