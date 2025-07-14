# --- File: ./project/blueprints/main.py ---
import os
import queue
import re
from pathlib import Path
from urllib.parse import unquote
from datetime import datetime

from flask import (
    Blueprint, render_template, g, request, redirect, url_for, flash, send_from_directory, abort, session
)

from ..database import get_db
from ..background import task_queue
from ..utils import _get_dashboard_state, _truncate_long_snippet
from ..config import DOCUMENTS_DIR, ENTITY_LABELS_TO_DISPLAY
from .auth import login_required, admin_required, SecureForm
import processing_pipeline

main_bp = Blueprint('main', __name__)

# --- (All existing routes up to view_html_document are unchanged) ---
@main_bp.route('/')
@main_bp.route('/dashboard')
@login_required
def dashboard():
    db = get_db()
    doc_query = "SELECT d.*, (SELECT COUNT(*) FROM document_comments WHERE doc_id = d.id) as comment_count, (SELECT COUNT(*) FROM document_tags WHERE doc_id = d.id) as tag_count FROM documents d"
    documents = [dict(row) for row in db.execute(doc_query).fetchall()]
    state_data = _get_dashboard_state(db)
    form = SecureForm()
    return render_template('dashboard.html', documents=documents, doc_dir=DOCUMENTS_DIR, queue_size=state_data['queue_size'], form=form, task_states=state_data['task_states'])

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
        for doc_id in doc_ids:
            processing_pipeline.update_document_status(db, doc_id, 'Queued', 'Waiting for free worker...')
            task_queue.put(('process', doc_id))
        flash(f"Queued {len(doc_ids)} documents for processing.", "success")
    return redirect(url_for('main.dashboard'))

@main_bp.route('/dashboard/process/<int:doc_id>')
@login_required
def dashboard_process_single(doc_id):
    processing_pipeline.update_document_status(get_db(), doc_id, 'Queued', 'Waiting for re-processing...')
    task_queue.put(('process', doc_id))
    flash(f"Queued document ID {doc_id} for re-processing.", "info")
    return redirect(url_for('main.dashboard'))

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
    db = get_db()
    cached_entities = db.execute("SELECT entity_label, entity_text, document_count, appearance_count FROM browse_cache ORDER BY entity_label, appearance_count DESC, entity_text COLLATE NOCASE").fetchall()
    entities_by_label = {label: [] for label in ENTITY_LABELS_TO_DISPLAY}
    for entity_row in cached_entities:
        label = entity_row['entity_label']
        if label in entities_by_label:
            entities_by_label[label].append(dict(entity_row))
    form = SecureForm()
    return render_template('discover.html', entities_by_label=entities_by_label, sorted_labels=ENTITY_LABELS_TO_DISPLAY, form=form)

@main_bp.route('/discover/search')
@login_required
def search_results():
    query = request.args.get('q', '').strip()
    if not query:
        return redirect(url_for('main.discover_view'))
    sanitized_query = query.replace('"', '""')
    fts_query = f'"{sanitized_query}"'
    db = get_db()
    sql_query = """
        SELECT d.id as doc_id, d.relative_path, d.color, d.page_count, d.file_type, 
               ci.page_number, snippet(content_index, 2, '<strong>', '</strong>', '...', 20) as snippet,
               (SELECT COUNT(*) FROM document_comments WHERE doc_id = d.id) as comment_count,
               (SELECT 1 FROM document_curation WHERE doc_id = d.id AND user_id = ?) as has_personal_note,
               (SELECT 1 FROM document_tags WHERE doc_id = d.id LIMIT 1) as has_tags,
               (SELECT GROUP_CONCAT(c.name, ', ') FROM catalogs c JOIN document_catalogs dc ON c.id = dc.catalog_id WHERE dc.doc_id = d.id) as catalog_names
        FROM content_index ci
        JOIN documents d ON ci.doc_id = d.id
        WHERE content_index MATCH ? ORDER BY rank;
    """
    db_results = db.execute(sql_query, (g.user['id'], fts_query)).fetchall()
    
    results = []
    for row in db_results:
        row_dict = dict(row)
        if row_dict.get('file_type') in ['SRT', 'HTML']:
            row_dict['snippet'] = _truncate_long_snippet(row_dict['snippet'])
        results.append(row_dict)

    return render_template('search_results.html', query=query, results=results)

@main_bp.route('/discover/entity/<label>/<path:text>')
@login_required
def entity_detail(label, text):
    db = get_db()
    entity_text = unquote(text)
    entity = db.execute("SELECT id FROM entities WHERE text = ? AND label = ?", (entity_text, label)).fetchone()
    if not entity:
        abort(404, "Entity not found in the database.")
    
    query_text = f'"{entity_text.replace(" ", " NEAR/2 ")}"'
    sql_query = """
        SELECT d.id as doc_id, d.relative_path, d.color, d.page_count, d.file_type, ea.page_number,
               (SELECT snippet(content_index, 2, '<strong>', '</strong>', '...', 20) 
                FROM content_index ci 
                WHERE ci.doc_id = d.id AND ci.page_number = ea.page_number AND content_index MATCH ?) as snippet,
               (SELECT COUNT(*) FROM document_comments WHERE doc_id = d.id) as comment_count,
               (SELECT 1 FROM document_curation WHERE doc_id = d.id AND user_id = ?) as has_personal_note,
               (SELECT 1 FROM document_tags WHERE doc_id = d.id LIMIT 1) as has_tags,
               (SELECT GROUP_CONCAT(c.name, ', ') FROM catalogs c JOIN document_catalogs dc ON c.id = dc.catalog_id WHERE dc.doc_id = d.id) as catalog_names
        FROM entity_appearances ea
        JOIN entities e ON ea.entity_id = e.id
        JOIN documents d ON ea.doc_id = d.id
        WHERE e.id = ? ORDER BY d.relative_path COLLATE NOCASE, ea.page_number;
    """
    db_results = db.execute(sql_query, (query_text, g.user['id'], entity['id'])).fetchall()
    
    results = []
    for row in db_results:
        row_dict = dict(row)
        if row_dict.get('file_type') in ['SRT', 'HTML']:
            row_dict['snippet'] = _truncate_long_snippet(row_dict['snippet'])
        results.append(row_dict)
        
    return render_template('entity_detail.html', label=label, text=entity_text, entity_id=entity['id'], results=results)

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
    view_query = """SELECT c.id as catalog_id, c.name as catalog_name, c.description as catalog_description, d.id as doc_id, d.relative_path, d.color, (SELECT COUNT(*) FROM document_comments WHERE doc_id = d.id) as comment_count, (SELECT 1 FROM document_curation WHERE doc_id = d.id AND user_id = ?) as has_personal_note, (SELECT 1 FROM document_tags WHERE doc_id = d.id LIMIT 1) as has_tags FROM catalogs c LEFT JOIN document_catalogs dc ON c.id = dc.catalog_id LEFT JOIN documents d ON dc.doc_id = d.id ORDER BY c.name COLLATE NOCASE, d.relative_path COLLATE NOCASE;"""
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
            
    manage_query = """SELECT c.id, c.name, c.description, COUNT(dc.doc_id) as doc_count FROM catalogs c LEFT JOIN document_catalogs dc ON c.id = dc.catalog_id GROUP BY c.id, c.name, c.description ORDER BY c.name COLLATE NOCASE;"""
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

@main_bp.route('/document/<int:doc_id>')
@login_required
def document_view(doc_id):
    db = get_db()
    doc = db.execute("SELECT id, relative_path, color, page_count, file_type FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc:
        abort(404)
    form = SecureForm()
    return render_template('document_view.html', doc=doc, form=form)

@main_bp.route('/serve_doc/<path:relative_path>')
@login_required
def serve_document(relative_path):
    safe_path = DOCUMENTS_DIR.joinpath(relative_path).resolve()
    if not str(safe_path).startswith(str(DOCUMENTS_DIR.resolve())):
        abort(403)
    return send_from_directory(DOCUMENTS_DIR, relative_path)

@main_bp.route('/view_pdf/<int:doc_id>')
@login_required
def view_pdf_document(doc_id):
    db = get_db()
    doc_meta = db.execute("SELECT relative_path, file_type FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc_meta or doc_meta['file_type'] != 'PDF':
        abort(404)
    pdf_url = url_for('main.serve_document', relative_path=doc_meta['relative_path'])
    return render_template('pdf_viewer.html', pdf_url=pdf_url, doc_title=doc_meta['relative_path'], doc_id=doc_id)

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

def _parse_srt_for_viewer(srt_content):
    """Parses SRT content into a list of dicts for the viewer template."""
    cues = []
    # Regex to capture sequence, timestamp, and text, accounting for multi-line text
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
    doc_meta = db.execute("SELECT relative_path, file_type FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc_meta or doc_meta['file_type'] != 'SRT':
        abort(404)
        
    # For the viewer, we read the original file to get timestamps, not the cleaned DB content
    srt_path = DOCUMENTS_DIR / doc_meta['relative_path']
    if not srt_path.exists():
        abort(404, "SRT file is missing from the filesystem.")
        
    try:
        content = srt_path.read_text(encoding='utf-8', errors='ignore')
        cues = _parse_srt_for_viewer(content)
    except Exception:
        cues = []

    return render_template('srt_viewer.html', cues=cues, doc_title=doc_meta['relative_path'], doc_id=doc_id)