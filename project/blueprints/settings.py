# --- File: ./project/blueprints/settings.py ---
import os
import sqlite3
import secrets
import json
import ollama # <--- Added import
from datetime import datetime
from pathlib import Path

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, g, jsonify, current_app, send_file
)
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename

from ..database import get_db
from ..background import restart_executor_event, get_system_settings
from .auth import admin_required, login_required, SecureForm
from ..export_import import export_knowledge_package, import_knowledge_package
from ..config import DOCUMENTS_DIR # <--- Added import for .rlink management

settings_bp = Blueprint('settings', __name__, url_prefix='/settings')

@settings_bp.route('/')
@admin_required
def settings_page():
    db = get_db()
    users = db.execute("SELECT id, username, role, created_at FROM users ORDER BY username").fetchall()
    tokens_query = """SELECT it.id, it.token_value, it.created_at, it.claimed_at, creator.username as creator_username, claimer.username as claimer_username FROM invitation_tokens it JOIN users creator ON it.created_by_user_id = creator.id LEFT JOIN users claimer ON it.claimed_by_user_id = claimer.id ORDER BY it.created_at DESC"""
    tokens = db.execute(tokens_query).fetchall()
    form = SecureForm()
    system_settings = get_system_settings()
    
    missing_docs = db.execute("SELECT id, relative_path, file_size_bytes, status_message FROM documents WHERE status = 'Missing' ORDER BY relative_path COLLATE NOCASE").fetchall()
    
    try:
        ollama_models = [m['name'] for m in ollama.list().get('models', [])]
    except Exception:
        ollama_models = []

    # --- NEW: Fetch Download Setting ---
    allow_downloads_row = db.execute("SELECT value FROM app_settings WHERE key = 'allow_document_downloads'").fetchone()
    allow_downloads = allow_downloads_row['value'] == 'true' if allow_downloads_row else False

    # --- NEW: Fetch .rlink alias files ---
    rlink_files = []
    if DOCUMENTS_DIR.exists():
        for rlink_path in DOCUMENTS_DIR.glob('*.rlink'):
            if rlink_path.is_file():
                try:
                    target = rlink_path.read_text(encoding='utf-8').strip()
                    rlink_files.append({
                        'name': rlink_path.name,
                        'target': target,
                        'is_valid': Path(target).is_dir()
                    })
                except Exception:
                    pass

    return render_template(
        'settings.html',
        users=users,
        tokens=tokens,
        missing_docs=missing_docs,
        rlink_files=rlink_files, # --- Passed to template ---
        form=form,
        max_workers=system_settings['max_workers'],
        use_gpu=system_settings['use_gpu'],
        allow_downloads=allow_downloads, # --- Passed to template ---
        cpu_count=os.cpu_count(),
        html_parsing_mode=system_settings['html_parsing_mode'],
        reasoning_model=system_settings['reasoning_model'],
        ollama_models=ollama_models
    )

@settings_bp.route('/downloads', methods=['POST'])
@admin_required
def update_downloads_setting():
    data = request.json
    if 'allow_downloads' not in data:
        return jsonify({'success': False, 'message': 'Invalid request.'}), 400

    allow_downloads_enabled = data.get('allow_downloads', False)
    allow_str = 'true' if allow_downloads_enabled else 'false'
    
    try:
        db = get_db()
        db.execute("INSERT INTO app_settings (key, value) VALUES ('allow_document_downloads', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (allow_str,))
        db.commit()
        status = "enabled" if allow_downloads_enabled else "disabled"
        return jsonify({'success': True, 'message': f'Document downloads have been {status}.'})
    except sqlite3.Error as e:
        db.rollback()
        return jsonify({'success': False, 'message': f'Database error: {e}'}), 500

@settings_bp.route('/model', methods=['POST'])
@admin_required
def update_model_settings():
    form = SecureForm()
    if form.validate_on_submit():
        new_model = request.form.get('reasoning_model')
        if new_model:
            db = get_db()
            db.execute("INSERT INTO app_settings (key, value) VALUES ('reasoning_model', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (new_model,))
            db.commit()
            flash(f'Reasoning model updated to "{new_model}".', 'success')
        else:
            flash('Invalid model selected.', 'danger')
    else:
        flash("CSRF validation failed.", 'danger')
    return redirect(url_for('settings.settings_page'))

@settings_bp.route('/export', methods=['POST'])
@admin_required
def export_package():
    form = SecureForm()
    if not form.validate_on_submit():
        flash("CSRF validation failed. Could not start export.", "danger")
        return redirect(url_for('settings.settings_page'))

    filename = f"redleaf_export_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.rklf"
    
    success, result_or_error = export_knowledge_package()
    
    if success:
        memory_buffer = result_or_error
        return send_file(
            memory_buffer,
            mimetype='application/zip',
            as_attachment=True,
            download_name=filename
        )
    else:
        flash(f"Export failed: {result_or_error}", "danger")
        return redirect(url_for('settings.settings_page'))

@settings_bp.route('/import', methods=['POST'])
@admin_required
def import_package():
    form = SecureForm()
    if not form.validate_on_submit():
        flash("CSRF validation failed. Could not import.", "danger")
        return redirect(url_for('settings.settings_page'))
    
    if 'package_file' not in request.files:
        flash('No file part in the request.', 'danger')
        return redirect(url_for('settings.settings_page'))
        
    file = request.files['package_file']
    if file.filename == '':
        flash('No file selected for upload.', 'danger')
        return redirect(url_for('settings.settings_page'))

    if file and (file.filename.endswith('.rklf') or file.filename.endswith('.zip')):
        upload_dir = Path(current_app.instance_path) / "uploads"
        upload_dir.mkdir(exist_ok=True)
        
        filename = secure_filename(file.filename)
        temp_path = upload_dir / filename
        file.save(temp_path)
        
        success, message = import_knowledge_package(temp_path)
        
        if success:
            flash(message, 'success')
        else:
            flash(f"Import Failed: {message}", 'danger')
            
        if temp_path.exists():
            temp_path.unlink()
            
        return redirect(url_for('settings.settings_page'))
    else:
        flash('Invalid file type. Please upload a .rklf or .zip package.', 'danger')
        return redirect(url_for('settings.settings_page'))

@settings_bp.route('/import-contributions', methods=['POST'])
@admin_required
def import_contributions():
    form = SecureForm()
    if not form.validate_on_submit():
        flash("CSRF validation failed.", "danger")
        return redirect(url_for('settings.settings_page'))

    if 'contribution_file' not in request.files:
        flash('No file part in the request.', 'danger')
        return redirect(url_for('settings.settings_page'))
        
    file = request.files['contribution_file']
    if file.filename == '' or not file.filename.endswith('.json'):
        flash('No file selected or invalid file type (must be .json).', 'danger')
        return redirect(url_for('settings.settings_page'))

    upload_dir = Path(current_app.instance_path) / "contributions"
    upload_dir.mkdir(exist_ok=True)
    
    base_filename = secure_filename(file.filename).replace('.json', '')
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    final_filename = f"{base_filename}_{timestamp}.json"
    temp_path = upload_dir / final_filename
    
    try:
        file.save(temp_path)
        flash(f"Contribution package '{file.filename}' uploaded successfully. Please review the suggestions below.", "success")
        return redirect(url_for('settings.review_contributions', filename=final_filename))
    except Exception as e:
        flash(f"Failed to save uploaded file: {e}", "danger")
        return redirect(url_for('settings.settings_page'))

@settings_bp.route('/review-contributions/<filename>')
@admin_required
def review_contributions(filename):
    contributions_dir = Path(current_app.instance_path) / "contributions"
    file_path = contributions_dir / secure_filename(filename)

    if not file_path.exists():
        flash("Contribution file not found.", "danger")
        return redirect(url_for('settings.settings_page'))

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        flash(f"Error reading contribution file: {e}", "danger")
        return redirect(url_for('settings.settings_page'))
        
    form = SecureForm()
    return render_template('review_contributions.html', contributions=data, filename=filename, form=form)

@settings_bp.route('/workers', methods=['POST'])
@admin_required
def update_workers():
    form = SecureForm()
    if form.validate_on_submit():
        try:
            max_cpus = (os.cpu_count() or 2) * 4
            new_worker_count = int(request.form.get('max_workers'))
            if new_worker_count < 1 or new_worker_count > max_cpus:
                 flash(f'Please enter a reasonable number of workers (e.g., 1 to {max_cpus}).', 'danger')
            else:
                db = get_db()
                db.execute("UPDATE app_settings SET value = ? WHERE key = 'max_workers'", (str(new_worker_count),))
                db.commit()
                restart_executor_event.set()
                flash(f'Worker count updated to {new_worker_count}. The change will apply once current tasks are finished.', 'success')
        except (ValueError, TypeError):
            flash('Invalid number entered for worker count.', 'danger')
    else:
        flash("CSRF validation failed.", 'danger')
    return redirect(url_for('settings.settings_page'))

@settings_bp.route('/gpu', methods=['POST'])
@admin_required
def update_gpu_setting():
    data = request.json
    if 'use_gpu' not in data:
        return jsonify({'success': False, 'message': 'Invalid request.'}), 400

    use_gpu_enabled = data.get('use_gpu', False)
    use_gpu_str = 'true' if use_gpu_enabled else 'false'
    
    try:
        db = get_db()
        db.execute("UPDATE app_settings SET value = ? WHERE key = 'use_gpu'", (use_gpu_str,))
        db.commit()
        restart_executor_event.set()
        status = "enabled" if use_gpu_enabled else "disabled"
        message = f'GPU acceleration has been {status}. Change will apply after current tasks finish.'
        return jsonify({'success': True, 'message': message})
    except sqlite3.Error as e:
        db.rollback()
        return jsonify({'success': False, 'message': f'Database error: {e}'}), 500

@settings_bp.route('/html', methods=['POST'])
@admin_required
def update_html_settings():
    form = SecureForm()
    if form.validate_on_submit():
        new_mode = request.form.get('html_parsing_mode')
        if new_mode in ['generic', 'pipermail']:
            db = get_db()
            db.execute("UPDATE app_settings SET value = ? WHERE key = 'html_parsing_mode'", (new_mode,))
            db.commit()
            flash(f'HTML parsing strategy updated to "{new_mode}".', 'success')
        else:
            flash('Invalid HTML parsing mode selected.', 'danger')
    else:
        flash("CSRF validation failed.", 'danger')
    return redirect(url_for('settings.settings_page'))

@settings_bp.route('/create_token', methods=['POST'])
@admin_required
def create_token():
    form = SecureForm()
    if form.validate_on_submit():
        db = get_db()
        new_token = secrets.token_urlsafe(16)
        db.execute("INSERT INTO invitation_tokens (token_value, created_by_user_id) VALUES (?, ?)", (new_token, g.user['id']))
        db.commit()
        flash(f"New invitation token created: {new_token}", "success")
    else:
        flash("CSRF validation failed.", "danger")
    return redirect(url_for('settings.settings_page'))

@settings_bp.route('/delete_user/<int:user_id>', methods=['POST'])
@admin_required
def delete_user(user_id):
    form = SecureForm()
    if form.validate_on_submit():
        if user_id == g.user['id']:
            flash("You cannot delete your own account.", "danger")
        else:
            db = get_db()
            user = db.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
            if user:
                db.execute("DELETE FROM users WHERE id = ?", (user_id,))
                db.commit()
                flash(f"Successfully deleted user '{user['username']}'.", "success")
            else:
                flash("User not found.", "danger")
    else:
        flash("CSRF validation failed.", "danger")
    return redirect(url_for('settings.settings_page'))

@settings_bp.route('/revoke_token/<int:token_id>', methods=['POST'])
@admin_required
def revoke_token(token_id):
    form = SecureForm()
    if form.validate_on_submit():
        db = get_db()
        cursor = db.execute("DELETE FROM invitation_tokens WHERE id = ? AND claimed_by_user_id IS NULL", (token_id,))
        db.commit()
        if cursor.rowcount > 0:
            flash("Invitation token revoked.", "success")
        else:
            flash("Could not revoke token. It might have already been claimed or deleted.", "warning")
    else:
        flash("CSRF validation failed.", "danger")
    return redirect(url_for('settings.settings_page'))

@settings_bp.route('/user/<int:user_id>/change-password', methods=['POST'])
@login_required
def change_user_password(user_id):
    if g.user['role'] != 'admin' and g.user['id'] != user_id:
        return jsonify({'success': False, 'message': 'You do not have permission to perform this action.'}), 403
    new_password = request.json.get('new_password', '').strip()
    if not new_password or len(new_password) < 8:
        return jsonify({'success': False, 'message': 'Password cannot be empty and must be at least 8 characters long.'}), 400
    db = get_db()
    user_to_update = db.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user_to_update:
        return jsonify({'success': False, 'message': 'User not found.'}), 404
    try:
        hashed_password = generate_password_hash(new_password)
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hashed_password, user_id))
        db.commit()
        return jsonify({'success': True, 'message': 'Password updated successfully.'})
    except sqlite3.Error as e:
        db.rollback()
        return jsonify({'success': False, 'message': f'Database error: {e}'}), 500

@settings_bp.route('/hard-delete-document/<int:doc_id>', methods=['POST'])
@admin_required
def hard_delete_document(doc_id):
    form = SecureForm()
    if form.validate_on_submit():
        db = get_db()
        try:
            db.execute("BEGIN TRANSACTION;")
            db.execute("DELETE FROM content_index WHERE doc_id = ?", (doc_id,))
            db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            
            db.execute("DELETE FROM entities WHERE id NOT IN (SELECT DISTINCT entity_id FROM entity_appearances)")
            db.execute("DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM document_tags)")
            
            db.commit()
            flash("Document permanently deleted.", "success")
        except Exception as e:
            db.rollback()
            flash(f"Error deleting document: {e}", "danger")
    return redirect(url_for('settings.settings_page'))

@settings_bp.route('/empty-recycle-bin', methods=['POST'])
@admin_required
def empty_recycle_bin():
    form = SecureForm()
    if form.validate_on_submit():
        db = get_db()
        try:
            db.execute("BEGIN TRANSACTION;")
            missing_ids = [row['id'] for row in db.execute("SELECT id FROM documents WHERE status = 'Missing'").fetchall()]
            if missing_ids:
                placeholders = ','.join('?' for _ in missing_ids)
                db.execute(f"DELETE FROM content_index WHERE doc_id IN ({placeholders})", missing_ids)
                db.execute("DELETE FROM documents WHERE status = 'Missing'")
                db.execute("DELETE FROM entities WHERE id NOT IN (SELECT DISTINCT entity_id FROM entity_appearances)")
                db.execute("DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM document_tags)")
            db.commit()
            flash(f"Emptied {len(missing_ids)} documents from the Recycle Bin.", "success")
        except Exception as e:
            db.rollback()
            flash(f"Error emptying recycle bin: {e}", "danger")
    return redirect(url_for('settings.settings_page'))

# ===================================================================
# --- NEW: VIRTUAL FOLDER (.rlink) MANAGEMENT ---
# ===================================================================

@settings_bp.route('/add-rlink', methods=['POST'])
@admin_required
def add_rlink():
    form = SecureForm()
    if form.validate_on_submit():
        alias_name = request.form.get('alias_name', '').strip()
        target_path = request.form.get('target_path', '').strip()
        
        if not alias_name or not target_path:
            flash("Both Alias Name and Target Path are required.", "danger")
            return redirect(url_for('settings.settings_page'))
            
        # Ensure proper extension and safe filename
        if not alias_name.endswith('.rlink'):
            alias_name += '.rlink'
        safe_name = secure_filename(alias_name)
        
        # Check if target actually exists and is a directory
        target_dir = Path(target_path)
        if not target_dir.exists() or not target_dir.is_dir():
            flash(f"The target OS path '{target_path}' does not exist or is not a directory.", "danger")
            return redirect(url_for('settings.settings_page'))
        
        # Write the file
        try:
            rlink_file = DOCUMENTS_DIR / safe_name
            rlink_file.write_text(str(target_dir.resolve()), encoding='utf-8')
            flash(f"Virtual folder link '{safe_name}' created successfully. Run 'Discover Docs' on the Dashboard to index its contents.", "success")
        except Exception as e:
            flash(f"Failed to create link file: {e}", "danger")
    else:
        flash("CSRF validation failed.", "danger")
        
    return redirect(url_for('settings.settings_page'))

@settings_bp.route('/delete-rlink/<filename>', methods=['POST'])
@admin_required
def delete_rlink(filename):
    form = SecureForm()
    if form.validate_on_submit():
        safe_name = secure_filename(filename)
        if not safe_name.endswith('.rlink'):
            flash("Invalid file type.", "danger")
            return redirect(url_for('settings.settings_page'))
            
        rlink_file = DOCUMENTS_DIR / safe_name
        try:
            if rlink_file.exists():
                rlink_file.unlink()
                flash(f"Virtual folder link '{safe_name}' deleted. Run 'Discover Docs' on the Dashboard to move its disconnected documents to the Recycle Bin.", "success")
            else:
                flash("Link file not found.", "warning")
        except Exception as e:
            flash(f"Failed to delete link file: {e}", "danger")
    else:
        flash("CSRF validation failed.", "danger")
        
    return redirect(url_for('settings.settings_page'))