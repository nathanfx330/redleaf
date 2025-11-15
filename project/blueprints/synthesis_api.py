# --- File: ./project/blueprints/synthesis_api.py ---
import uuid
import json
import traceback
from collections import Counter
from datetime import datetime
from flask import Blueprint, request, jsonify, g, current_app, send_file

from .auth import login_required
from ..database import get_db
from ..export_utils import generate_odt

# Note: The URL prefix is now defined directly on the Blueprint
synthesis_api_bp = Blueprint('synthesis_api', __name__, url_prefix='/api/synthesis')

# ===================================================================
# --- Report Management Endpoints ---
# ===================================================================

@synthesis_api_bp.route('/reports', methods=['GET', 'POST'])
@login_required
def manage_reports():
    """Gets all reports for a user or creates a new one."""
    db = get_db()
    
    if request.method == 'GET':
        reports = db.execute("SELECT id, title, updated_at FROM synthesis_reports WHERE owner_id = ? ORDER BY updated_at DESC", (g.user['id'],)).fetchall()
        return jsonify([dict(row) for row in reports])

    if request.method == 'POST':
        title = (request.json.get('title') or f"Untitled Synthesis - {datetime.now().strftime('%Y-%m-%d')}").strip()
        try:
            cursor = db.execute("INSERT INTO synthesis_reports (title, owner_id, updated_at) VALUES (?, ?, ?)", (title, g.user['id'], datetime.now()))
            db.commit()
            return jsonify({'success': True, 'report': {'id': cursor.lastrowid, 'title': title}}), 201
        except Exception as e:
            db.rollback()
            return jsonify({'success': False, 'message': str(e)}), 500

@synthesis_api_bp.route('/report/<int:report_id>', methods=['PUT', 'DELETE'])
@login_required
def manage_single_report(report_id):
    """Renames or deletes a specific synthesis report."""
    db = get_db()
    report = db.execute("SELECT id, owner_id FROM synthesis_reports WHERE id = ? AND owner_id = ?", (report_id, g.user['id'])).fetchone()
    if not report:
        return jsonify({'success': False, 'message': 'Report not found or permission denied.'}), 404

    if request.method == 'PUT':
        new_title = (request.json.get('title') or '').strip()
        if not new_title:
            return jsonify({'success': False, 'message': 'Title cannot be empty.'}), 400
        db.execute("UPDATE synthesis_reports SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (new_title, report_id))
        db.commit()
        return jsonify({'success': True, 'message': 'Report renamed.'})

    if request.method == 'DELETE':
        # Prevent deletion of the last remaining report
        count = db.execute("SELECT COUNT(id) FROM synthesis_reports WHERE owner_id = ?", (g.user['id'],)).fetchone()[0]
        if count <= 1:
            return jsonify({'success': False, 'message': 'Cannot delete the last report.'}), 400
        db.execute("DELETE FROM synthesis_reports WHERE id = ?", (report_id,))
        db.commit()
        return jsonify({'success': True, 'message': 'Report deleted.'})

# ===================================================================
# --- Content & Citation Endpoints ---
# ===================================================================

@synthesis_api_bp.route('/<int:report_id>/content', methods=['GET', 'POST'])
@login_required
def report_content(report_id):
    """Gets or saves the main content (as JSON) of a synthesis report."""
    db = get_db()
    if not db.execute("SELECT id FROM synthesis_reports WHERE id = ? AND owner_id = ?", (report_id, g.user['id'])).fetchone():
        return jsonify({'success': False, 'message': 'Report not found or access denied'}), 404

    if request.method == 'GET':
        report_data = db.execute("SELECT content_json FROM synthesis_reports WHERE id = ?", (report_id,)).fetchone()
        content = json.loads(report_data['content_json']) if report_data and report_data['content_json'] else None
        return jsonify({'success': True, 'content': content})

    if request.method == 'POST':
        content_json = request.json
        if not content_json:
            return jsonify({'success': False, 'message': 'No content provided'}), 400
        try:
            db.execute("UPDATE synthesis_reports SET content_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (json.dumps(content_json), report_id))
            db.commit()
            return jsonify({'success': True, 'message': 'Content saved.'})
        except Exception as e:
            db.rollback()
            return jsonify({'success': False, 'message': f'Database error: {e}'}), 500

def _generate_in_text_citation(db, citation_data):
    """Helper function to create a Chicago-style in-text citation string."""
    doc_id = citation_data.get('source_doc_id')
    meta_row = db.execute("SELECT csl_json FROM document_metadata WHERE doc_id = ?", (doc_id,)).fetchone()
    
    author, year, title = "", "", ""
    if meta_row and meta_row['csl_json']:
        try:
            meta = json.loads(meta_row['csl_json'])
            if meta.get('author') and meta['author'][0]:
                author = meta['author'][0].get('family') or meta['author'][0].get('literal', '')
            if meta.get('issued', {}).get('date-parts', [[]])[0]:
                year = meta['issued']['date-parts'][0][0]
            title = meta.get('title', '')
        except (json.JSONDecodeError, IndexError, TypeError): pass
    
    main_part = ""
    suppress_author = citation_data.get('suppress_author', False)
    if author and year and not suppress_author: main_part = f"{author}, {year}"
    elif year and suppress_author: main_part = str(year)
    elif title and year: main_part = f'"{title[:25]}…", {year}' if len(title) > 25 else f'"{title}", {year}'
    elif author and not suppress_author: main_part = author
    elif title: main_part = f'"{title[:25]}…"' if len(title) > 25 else f'"{title}"'
    else: main_part = f"Doc. {doc_id}"

    page_part = f", p. {citation_data['page_number']}" if citation_data.get('page_number') else ""
    prefix = f"{citation_data['prefix']} " if citation_data.get('prefix') else ""
    suffix = f", {citation_data['suffix']}" if citation_data.get('suffix') else ""
    
    return f"({prefix}{main_part}{page_part}{suffix})"

@synthesis_api_bp.route('/<int:report_id>/citations', methods=['POST'])
@login_required
def add_citation(report_id):
    """Creates a new citation instance and returns its details."""
    data = request.json
    if 'source_doc_id' not in data:
        return jsonify({'success': False, 'message': 'Missing required source_doc_id.'}), 400

    db = get_db()
    doc = db.execute("SELECT file_type FROM documents WHERE id = ?", (data['source_doc_id'],)).fetchone()
    if not doc:
        return jsonify({'success': False, 'message': 'Source document not found.'}), 404

    new_uuid = str(uuid.uuid4())
    try:
        db.execute("""
            INSERT INTO synthesis_citations 
            (citation_instance_uuid, report_id, source_doc_id, page_number, quoted_text, prefix, suffix, suppress_author) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (new_uuid, report_id, data['source_doc_id'], data.get('page_number'), data.get('corrected_text'), 
              data.get('prefix'), data.get('suffix'), data.get('suppress_author', False)))
        db.commit()
    except sqlite3.Error as e:
        db.rollback()
        return jsonify({'success': False, 'message': f'Database error: {e}'}), 500

    return jsonify({
        'success': True,
        'citation_instance_uuid': new_uuid,
        'in_text_label': _generate_in_text_citation(db, data),
        'data_doc_id': data['source_doc_id'],
        'data_doc_type': doc['file_type']
    }), 201

# ===================================================================
# --- Utility & Export Endpoints ---
# ===================================================================

@synthesis_api_bp.route('/search/documents')
@login_required
def search_documents():
    """Provides a simple search for documents by filename for the reference viewer."""
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify([])
    db = get_db()
    results = db.execute("SELECT id, relative_path FROM documents WHERE relative_path LIKE ? ORDER BY relative_path LIMIT 20", (f'%{query}%',)).fetchall()
    return jsonify([dict(row) for row in results])

def _find_live_citation_attrs(node):
    """Recursively finds all citation pill attributes in a Tiptap JSON structure."""
    if isinstance(node, dict):
        if node.get('type') == 'citationPill' and 'attrs' in node:
            yield node['attrs']
        if 'content' in node and isinstance(node['content'], list):
            for item in node['content']:
                yield from _find_live_citation_attrs(item)
    elif isinstance(node, list):
        for item in node:
            yield from _find_live_citation_attrs(item)

@synthesis_api_bp.route('/<int:report_id>/bibliography', methods=['POST'])
@login_required
def get_bibliography(report_id):
    """Generates an HTML bibliography from the citations present in the editor's content."""
    editor_content = request.json
    if not editor_content:
        return jsonify({'html': '<p class="text-muted">No content to analyze.</p>'})

    live_citation_attrs = list(_find_live_citation_attrs(editor_content))
    doc_ids_from_pills = [int(attr['data-doc-id']) for attr in live_citation_attrs if attr.get('data-doc-id')]
    if not doc_ids_from_pills:
        return jsonify({'html': '<p class="text-muted">No citations found in the document.</p>'})

    doc_id_counts = Counter(doc_ids_from_pills)
    unique_doc_ids = list(doc_id_counts.keys())
    
    db = get_db()
    placeholders = ','.join('?' for _ in unique_doc_ids)
    doc_info_rows = db.execute(f"SELECT id, csl_json, relative_path FROM documents LEFT JOIN document_metadata ON documents.id = document_metadata.doc_id WHERE documents.id IN ({placeholders})", unique_doc_ids).fetchall()
    doc_info_rows.sort(key=lambda x: x['relative_path'])

    html = '<ul style="list-style: none; padding-left: 0;">'
    for row in doc_info_rows:
        author, year, title, publisher = "","","",""
        if row['csl_json']:
            try:
                meta = json.loads(row['csl_json'])
                if meta.get('author'): author = "; ".join([f"{a.get('family', '')}, {a.get('given', '')}".strip(', ') for a in meta['author']])
                if meta.get('issued', {}).get('date-parts'): year = meta['issued']['date-parts'][0][0]
                title = meta.get('title', row['relative_path'])
                publisher = meta.get('publisher', '')
            except (json.JSONDecodeError, IndexError, TypeError):
                title = row['relative_path']
        else:
            title = row['relative_path']

        parts = [f"<strong>{author}.</strong>" if author else "", f"({year})." if year else "", f"<em>{title}</em>." if title else "", f"{publisher}." if publisher else ""]
        count = doc_id_counts.get(row['id'], 0)
        cite_text = "time" if count == 1 else "times"
        count_chip = f'<span class="chip" style="margin-left: 1rem; background-color: var(--background-light); font-weight: normal;">Cited {count} {cite_text}</span>'
        html += f'<li style="margin-bottom: 1rem; display: flex; justify-content: space-between; align-items: baseline;"><div>{" ".join(filter(None, parts))}</div>{count_chip}</li>'
    
    html += '</ul>'
    return jsonify({'html': html})

@synthesis_api_bp.route('/<int:report_id>/export/odt', methods=['POST'])
@login_required
def export_report_to_odt(report_id):
    """Exports the current report content to an ODT file."""
    db = get_db()
    report = db.execute("SELECT title FROM synthesis_reports WHERE id = ? AND owner_id = ?", (report_id, g.user['id'])).fetchone()
    if not report:
        return jsonify({'success': False, 'message': 'Report not found or access denied'}), 404

    editor_html = request.json.get('html_content')
    if not editor_html:
        return jsonify({'success': False, 'message': 'No content provided for export.'}), 400

    try:
        odt_buffer = generate_odt(editor_html, report_id, g.user['id'])
        safe_filename = "".join(c for c in report['title'] if c.isalnum() or c in (' ', '-')).rstrip() or 'synthesis-report'
        filename = f"{safe_filename}.odt"

        return send_file(odt_buffer, mimetype='application/vnd.oasis.opendocument.text', as_attachment=True, download_name=filename)
    except Exception as e:
        current_app.logger.error(f"ODT Export failed for report {report_id}:\n{traceback.format_exc()}")
        return jsonify({'success': False, 'message': f'An unexpected error occurred during export: {e}'}), 500