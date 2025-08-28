# --- File: ./project/blueprints/synthesis_api.py ---
import uuid
import json
import os
import traceback
from collections import Counter
from datetime import datetime
from flask import Blueprint, request, jsonify, g, current_app, send_file

from .auth import login_required
from ..database import get_db
from ..config import BASE_DIR
from ..export_utils import generate_odt

synthesis_api_bp = Blueprint('synthesis_api', __name__, url_prefix='/api/synthesis')

@synthesis_api_bp.route('/reports', methods=['GET'])
@login_required
def get_all_reports():
    db = get_db()
    reports = db.execute("SELECT id, title, updated_at FROM synthesis_reports WHERE owner_id = ? ORDER BY updated_at DESC", (g.user['id'],)).fetchall()
    return jsonify([dict(row) for row in reports])

@synthesis_api_bp.route('/reports', methods=['POST'])
@login_required
def create_report():
    db = get_db()
    data = request.json or {}
    title = data.get('title', '').strip()
    # Use a different, more descriptive default for subsequent new reports
    if not title: title = f"Untitled Synthesis - {datetime.now().strftime('%Y-%m-%d')}"
    try:
        cursor = db.execute("INSERT INTO synthesis_reports (title, owner_id, updated_at) VALUES (?, ?, ?)", (title, g.user['id'], datetime.now()))
        db.commit()
        return jsonify({'success': True, 'report': {'id': cursor.lastrowid, 'title': title}}), 201
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

# (The rest of the file is unchanged)
@synthesis_api_bp.route('/report/<int:report_id>', methods=['PUT', 'DELETE'])
@login_required
def manage_report(report_id):
    db = get_db()
    report = db.execute("SELECT id, owner_id FROM synthesis_reports WHERE id = ?", (report_id,)).fetchone()
    if not report or report['owner_id'] != g.user['id']:
        return jsonify({'success': False, 'message': 'Report not found or permission denied.'}), 404
    if request.method == 'PUT':
        new_title = (request.json.get('title') or '').strip()
        if not new_title: return jsonify({'success': False, 'message': 'Title cannot be empty.'}), 400
        db.execute("UPDATE synthesis_reports SET title = ? WHERE id = ?", (new_title, report_id))
        db.commit()
        return jsonify({'success': True, 'message': 'Report renamed.'})
    if request.method == 'DELETE':
        count = db.execute("SELECT COUNT(id) FROM synthesis_reports WHERE owner_id = ?", (g.user['id'],)).fetchone()[0]
        if count <= 1: return jsonify({'success': False, 'message': 'Cannot delete the last report.'}), 400
        db.execute("DELETE FROM synthesis_reports WHERE id = ?", (report_id,))
        db.commit()
        return jsonify({'success': True, 'message': 'Report deleted.'})

def generate_in_text_citation(db, citation_data):
    doc_id = citation_data['source_doc_id']
    metadata_row = db.execute("SELECT csl_json FROM document_metadata WHERE doc_id = ?", (doc_id,)).fetchone()
    author, year, title = "", "", ""
    if metadata_row and metadata_row['csl_json']:
        try:
            metadata = json.loads(metadata_row['csl_json'])
            if metadata.get('author') and isinstance(metadata['author'], list) and len(metadata['author']) > 0:
                author = metadata['author'][0].get('family', '')
            if metadata.get('issued') and isinstance(metadata['issued'].get('date-parts'), list):
                year = metadata['issued']['date-parts'][0][0]
            title = metadata.get('title', '')
        except (json.JSONDecodeError, IndexError, TypeError): pass
    main_part = ""
    if author and year and not citation_data.get('suppress_author'): main_part = f"{author}, {year}"
    elif year and citation_data.get('suppress_author'): main_part = str(year)
    elif title and year: short_title = (title[:25] + '...') if len(title) > 25 else title; main_part = f'"{short_title}", {year}'
    elif author and not citation_data.get('suppress_author'): main_part = author
    elif title: short_title = (title[:25] + '...') if len(title) > 25 else title; main_part = f'"{short_title}"'
    else: doc_row = db.execute("SELECT relative_path FROM documents WHERE id = ?", (doc_id,)).fetchone(); main_part = doc_row['relative_path'] if doc_row else f"Doc. {doc_id}"
    page_number = citation_data.get('page_number')
    page_part = f", p. {page_number}" if page_number else ""
    parts = [main_part, page_part]
    prefix = citation_data.get('prefix', '').strip()
    suffix = citation_data.get('suffix', '').strip()
    if prefix: parts.insert(0, f"{prefix} ")
    full_citation = "".join(filter(None, parts))
    if suffix: full_citation += f", {suffix}"
    return f"({full_citation})"

@synthesis_api_bp.route('/search/documents')
@login_required
def search_documents():
    query = request.args.get('q', '').strip()
    if not query: return jsonify([])
    db = get_db()
    search_term = f'%{query}%'
    results = db.execute("SELECT id, relative_path, file_type, file_modified_at FROM documents WHERE relative_path LIKE ? ORDER BY relative_path LIMIT 20", (search_term,)).fetchall()
    return jsonify([dict(row) for row in results])

@synthesis_api_bp.route('/<int:report_id>/content', methods=['GET', 'POST'])
@login_required
def report_content(report_id):
    db = get_db()
    report = db.execute("SELECT id FROM synthesis_reports WHERE id = ? AND owner_id = ?", (report_id, g.user['id'])).fetchone()
    if not report: return jsonify({'success': False, 'message': 'Report not found or access denied'}), 404
    if request.method == 'GET':
        report_data = db.execute("SELECT content_json FROM synthesis_reports WHERE id = ?", (report_id,)).fetchone()
        content = json.loads(report_data['content_json']) if report_data['content_json'] else None
        return jsonify({'success': True, 'content': content})
    if request.method == 'POST':
        content_json = request.json
        if not content_json: return jsonify({'success': False, 'message': 'No content provided'}), 400
        try:
            db.execute("UPDATE synthesis_reports SET content_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (json.dumps(content_json), report_id))
            db.commit()
            return jsonify({'success': True, 'message': 'Content saved.'})
        except Exception as e:
            db.rollback()
            return jsonify({'success': False, 'message': f'Database error: {e}'}), 500

@synthesis_api_bp.route('/<int:report_id>/citations', methods=['POST'])
@login_required
def add_citation(report_id):
    data = request.json
    db = get_db()
    if not all(k in data for k in ['source_doc_id', 'page_number']):
        return jsonify({'success': False, 'message': 'Missing required fields'}), 400

    doc_info = db.execute("SELECT file_type FROM documents WHERE id = ?", (data['source_doc_id'],)).fetchone()
    if not doc_info:
        return jsonify({'success': False, 'message': 'Source document not found.'}), 404
    file_type = doc_info['file_type']

    new_uuid = str(uuid.uuid4())
    try:
        db.execute("INSERT INTO synthesis_citations (citation_instance_uuid, report_id, source_doc_id, page_number, quoted_text, prefix, suffix, suppress_author) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (new_uuid, report_id, data['source_doc_id'], data['page_number'], data.get('corrected_text'), data.get('prefix'), data.get('suffix'), data.get('suppress_author', False)))
        db.commit()
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'message': f'Database error: {e}'}), 500

    in_text_label = generate_in_text_citation(db, data)
    
    return jsonify({
        'success': True,
        'citation_instance_uuid': new_uuid,
        'in_text_label': in_text_label,
        'data_doc_id': data['source_doc_id'],
        'data_doc_type': file_type
    }), 201

def find_live_citations_in_json(node):
    if isinstance(node, dict):
        if node.get('type') == 'citationPill' and 'attrs' in node:
            yield node['attrs']
        if 'content' in node and isinstance(node['content'], list):
            for item in node['content']:
                yield from find_live_citations_in_json(item)
    elif isinstance(node, list):
        for item in node:
            yield from find_live_citations_in_json(item)

@synthesis_api_bp.route('/<int:report_id>/bibliography', methods=['POST'])
@login_required
def get_bibliography(report_id):
    db = get_db()
    
    editor_content = request.json
    if not editor_content:
        return jsonify({'html': '<p class="text-muted">No content to analyze.</p>'})

    live_citation_attrs = list(find_live_citations_in_json(editor_content))
    if not live_citation_attrs:
        return jsonify({'html': '<p class="text-muted">No citations in the document.</p>'})

    doc_ids_from_pills = [int(attr.get('data-doc-id')) for attr in live_citation_attrs if attr.get('data-doc-id')]
    doc_id_counts = Counter(doc_ids_from_pills)
    
    unique_doc_ids = list(doc_id_counts.keys())
    if not unique_doc_ids:
        return jsonify({'html': '<p class="text-muted">No valid citations in the document.</p>'})

    placeholders = ','.join('?' for _ in unique_doc_ids)
    sql_query = f"""
        SELECT id, relative_path, csl_json
        FROM documents
        LEFT JOIN document_metadata ON documents.id = document_metadata.doc_id
        WHERE documents.id IN ({placeholders})
    """
    
    doc_info_rows = db.execute(sql_query, unique_doc_ids).fetchall()
    doc_info_rows.sort(key=lambda x: x['relative_path'])

    html = '<ul style="list-style: none; padding-left: 0;">'
    for row in doc_info_rows:
        doc_id = row['id']
        author_str, year_str, title_str, publisher_str = "","","",""
        
        if row['csl_json']:
            try:
                meta = json.loads(row['csl_json'])
                if meta.get('author'): author_str = "; ".join([f"{a.get('family', '')}, {a.get('given', '')}" for a in meta['author']]).strip(', ')
                if meta.get('issued') and meta.get('issued', {}).get('date-parts'): year_str = meta['issued']['date-parts'][0][0]
                title_str = meta.get('title', row['relative_path'])
                publisher_str = meta.get('publisher', '')
            except (json.JSONDecodeError, IndexError, TypeError):
                title_str = row['relative_path']
        else:
            title_str = row['relative_path']

        display_parts = []
        if author_str: display_parts.append(f"<strong>{author_str}.</strong>")
        if year_str: display_parts.append(f"({year_str}).")
        if title_str: display_parts.append(f"<em>{title_str}</em>.")
        if publisher_str: display_parts.append(f"{publisher_str}.")
        
        count = doc_id_counts.get(doc_id, 0)
        cite_text = "time" if count == 1 else "times"
        count_chip = f'<span class="chip" style="margin-left: 1rem; background-color: var(--background-light); font-weight: normal;">Cited {count} {cite_text}</span>'

        html += f'<li style="margin-bottom: 1rem; display: flex; justify-content: space-between; align-items: baseline;"><div>{" ".join(display_parts)}</div>{count_chip}</li>'
    
    html += '</ul>'
    return jsonify({'html': html})

@synthesis_api_bp.route('/<int:report_id>/export/odt', methods=['POST'])
@login_required
def export_report_to_odt(report_id):
    db = get_db()
    report = db.execute(
        "SELECT title FROM synthesis_reports WHERE id = ? AND owner_id = ?",
        (report_id, g.user['id'])
    ).fetchone()

    if not report:
        return jsonify({'success': False, 'message': 'Report not found or access denied'}), 404

    editor_html = request.json.get('html_content')
    if not editor_html:
        return jsonify({'success': False, 'message': 'No content provided for export.'}), 400

    try:
        odt_buffer = generate_odt(editor_html, report_id, g.user['id'])
        
        safe_filename = "".join(c for c in report['title'] if c.isalnum() or c in (' ', '-')).rstrip()
        filename = f"{safe_filename or 'synthesis-report'}.odt"

        return send_file(
            odt_buffer,
            mimetype='application/vnd.oasis.opendocument.text',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        tb_str = traceback.format_exc()
        current_app.logger.error(f"ODT Export failed for report {report_id}:\n{tb_str}")
        return jsonify({'success': False, 'message': f'An unexpected error occurred during export: {e}'}), 500