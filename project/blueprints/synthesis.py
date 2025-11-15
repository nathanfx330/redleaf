# --- File: ./project/blueprints/synthesis.py ---

from flask import (
    Blueprint, render_template, g, request, redirect, url_for, flash, abort, current_app
)
from ..database import get_db
from .auth import login_required, SecureForm
from datetime import datetime

synthesis_bp = Blueprint('synthesis', __name__, url_prefix='/synthesis')

@synthesis_bp.route('/')
@login_required
def list_reports():
    db = get_db()
    
    # Find the most recently updated report for the user.
    user_report = db.execute(
        """
        SELECT id FROM synthesis_reports 
        WHERE owner_id = ? 
        ORDER BY updated_at DESC, id ASC 
        LIMIT 1
        """, 
        (g.user['id'],)
    ).fetchone()

    # If the user has reports, redirect to the most recent one.
    if user_report:
        return redirect(url_for('synthesis.report_view', report_id=user_report['id']))
    else:
        # If the user has no reports, create one and then redirect.
        try:
            title = "Report 1"
            
            cursor = db.execute(
                "INSERT INTO synthesis_reports (title, owner_id, updated_at) VALUES (?, ?, ?)",
                (title, g.user['id'], datetime.now())
            )
            db.commit()
            new_report_id = cursor.lastrowid
            flash("Welcome to Synthesis! We've created your first report.", "success")
            
            return redirect(url_for('synthesis.report_view', report_id=new_report_id, first_visit='true'))

        except Exception as e:
            db.rollback()
            current_app.logger.error(f"FATAL: Could not create first report for user {g.user['id']}: {e}")
            flash("There was a critical error creating your first report. Please contact an administrator.", "danger")
            return redirect(url_for('main.dashboard'))


@synthesis_bp.route('/report/<int:report_id>')
@login_required
def report_view(report_id):
    db = get_db()
    
    report = db.execute(
        'SELECT * FROM synthesis_reports WHERE id = ? AND owner_id = ?', (report_id, g.user['id'])
    ).fetchone()

    if not report:
        abort(404, "Report not found or you do not have permission to view it.")

    doc_to_load = None
    doc_id_to_load = request.args.get('load_doc', type=int)
    if doc_id_to_load:
        doc_row = db.execute(
            "SELECT * FROM documents WHERE id = ?", (doc_id_to_load,)
        ).fetchone()
        
        if doc_row:
            doc_to_load = dict(doc_row)
            
            # This logic correctly determines which viewer URL to use for each file type.
            file_type = doc_to_load.get('file_type')
            viewer_url = None

            if file_type == 'PDF':
                viewer_url = url_for('main.view_pdf_document', doc_id=doc_id_to_load)
            elif file_type == 'TXT':
                viewer_url = url_for('main.view_text_document', doc_id=doc_id_to_load)
            elif file_type == 'HTML':
                viewer_url = url_for('main.view_html_document', doc_id=doc_id_to_load)
            elif file_type == 'SRT':
                viewer_url = url_for('main.view_srt_document', doc_id=doc_id_to_load)
            elif file_type == 'EML':
                viewer_url = url_for('main.view_eml_document', doc_id=doc_id_to_load)
            else:
                viewer_url = url_for('main.serve_document', relative_path=doc_to_load.get('relative_path'))
            
            doc_to_load['viewer_url'] = viewer_url

    form = SecureForm()
    return render_template(
        'synthesis.html', 
        report=report, 
        form=form, 
        doc_to_load=doc_to_load
    )