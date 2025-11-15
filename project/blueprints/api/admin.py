# --- File: ./project/blueprints/api/admin.py ---
import sqlite3
from flask import jsonify, request, g

from . import api_bp
from ...database import get_db
from ..auth import admin_required, login_required

# ===================================================================
# --- Tag Management Endpoints (Admin) ---
# ===================================================================

@api_bp.route('/tags/rename', methods=['PUT'])
@admin_required
def rename_tag():
    """Renames a tag throughout the entire system."""
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
        # Check if the new tag name already exists to prevent integrity errors.
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
    """Permanently deletes a tag from the system."""
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
        
        # Deleting from the tags table will cascade and delete all associations
        # in document_tags due to the foreign key constraint.
        db.execute("DELETE FROM tags WHERE id = ?", (tag_row['id'],))
        db.commit()
        return jsonify({'success': True, 'message': f"Tag '{tag_name}' deleted."})
    except sqlite3.Error as e:
        db.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

# ===================================================================
# --- Catalog Management Endpoints (Admin) ---
# ===================================================================

@api_bp.route('/catalogs', methods=['POST'])
@login_required
def create_catalog_api():
    """Creates a new user-created catalog from an API call."""
    data = request.json
    name = data.get('name', '').strip()

    if not name:
        return jsonify({'success': False, 'message': 'Catalog name cannot be empty.'}), 400

    db = get_db()
    try:
        cursor = db.execute("INSERT INTO catalogs (name, description, catalog_type) VALUES (?, ?, 'user')", (name, ''))
        db.commit()
        new_id = cursor.lastrowid
        
        return jsonify({'success': True, 'message': 'Catalog created.', 'catalog': {'id': new_id, 'name': name}}), 201
    except sqlite3.IntegrityError:
        db.rollback()
        return jsonify({'success': False, 'message': f"A catalog with the name '{name}' already exists."}), 409
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@api_bp.route('/catalogs/<int:catalog_id>', methods=['PUT', 'DELETE'])
@admin_required
def manage_catalog(catalog_id):
    """Updates or deletes a user-created catalog."""
    db = get_db()
    
    # Prevent modification of the special 'Favorites' catalog
    fav_catalog = db.execute("SELECT id FROM catalogs WHERE name = '⭐ Favorites'").fetchone()
    if fav_catalog and fav_catalog['id'] == catalog_id:
        return jsonify({'success': False, 'message': 'The Favorites catalog cannot be modified or deleted.'}), 403

    if request.method == 'PUT':
        data = request.json
        name = data.get('name', '').strip()
        if not name:
            return jsonify({'success': False, 'message': 'Catalog name cannot be empty.'}), 400
        
        description = data.get('description', '').strip()
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

    if request.method == 'DELETE':
        cursor = db.execute("DELETE FROM catalogs WHERE id = ?", (catalog_id,))
        db.commit()
        if cursor.rowcount > 0:
            return jsonify({'success': True, 'message': 'Catalog deleted successfully.'})
        else:
            return jsonify({'success': False, 'message': 'Catalog not found.'}), 404

# ===================================================================
# --- Contribution Review Endpoints (Admin) ---
# ===================================================================

@api_bp.route('/contributions/accept-tag', methods=['POST'])
@admin_required
def accept_contribution_tag():
    """Accepts a suggested tag and applies it to the document."""
    data = request.json
    doc_path = data.get('doc_path')
    tag_name = data.get('value')
    if not doc_path or not tag_name:
        return jsonify({'success': False, 'message': 'Missing document path or tag name.'}), 400

    db = get_db()
    try:
        doc_row = db.execute("SELECT id FROM documents WHERE relative_path = ?", (doc_path,)).fetchone()
        if not doc_row:
            return jsonify({'success': False, 'message': f'Document not found: {doc_path}'}), 404
        doc_id = doc_row['id']

        db.execute("BEGIN")
        db.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name,))
        tag_id = db.execute("SELECT id FROM tags WHERE name = ?", (tag_name,)).fetchone()['id']
        db.execute("INSERT OR IGNORE INTO document_tags (doc_id, tag_id) VALUES (?, ?)", (doc_id, tag_id))
        db.commit()
        return jsonify({'success': True, 'message': 'Tag accepted.'})
    except (sqlite3.Error, TypeError) as e: # TypeError for potential fetchone() on None
        db.rollback()
        return jsonify({'success': False, 'message': f'Database error: {e}'}), 500

@api_bp.route('/contributions/accept-comment', methods=['POST'])
@admin_required
def accept_contribution_comment():
    """Accepts a suggested comment and adds it to the document."""
    data = request.json
    doc_path = data.get('doc_path')
    comment_text = data.get('value')
    contributor = data.get('contributor', 'Explorer')
    if not doc_path or not comment_text:
        return jsonify({'success': False, 'message': 'Missing document path or comment text.'}), 400

    db = get_db()
    try:
        doc_row = db.execute("SELECT id FROM documents WHERE relative_path = ?", (doc_path,)).fetchone()
        if not doc_row:
            return jsonify({'success': False, 'message': f'Document not found: {doc_path}'}), 404
        doc_id = doc_row['id']
        
        # Attribute the comment to the admin who accepted it, but prefix with the contributor's name.
        full_comment_text = f"[From {contributor}]:\n\n{comment_text}"

        db.execute(
            "INSERT INTO document_comments (doc_id, user_id, comment_text) VALUES (?, ?, ?)",
            (doc_id, g.user['id'], full_comment_text)
        )
        db.commit()
        return jsonify({'success': True, 'message': 'Comment accepted.'})
    except sqlite3.Error as e:
        db.rollback()
        return jsonify({'success': False, 'message': f'Database error: {e}'}), 500