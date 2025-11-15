# --- File: ./project/blueprints/api/curation.py ---
import sqlite3
from flask import jsonify, request, g

from . import api_bp
from .helpers import get_document_or_404
from ...database import get_db
from ..auth import login_required

@api_bp.route('/document/<int:doc_id>/curation', methods=['GET', 'POST'])
@login_required
def document_curation(doc_id):
    """Handles getting and saving all curation data for a document."""
    db = get_db()
    get_document_or_404(db, doc_id)
    
    if request.method == 'GET':
        curation = db.execute("SELECT note FROM document_curation WHERE doc_id = ? AND user_id = ?", (doc_id, g.user['id'])).fetchone()
        comments = db.execute("SELECT c.id, c.comment_text, c.created_at, u.username, u.id as user_id FROM document_comments c JOIN users u ON c.user_id = u.id WHERE c.doc_id = ? ORDER BY c.created_at ASC", (doc_id,)).fetchall()
        all_catalogs = db.execute("SELECT id, name FROM catalogs WHERE catalog_type IN ('user', 'favorites') ORDER BY name").fetchall()
        member_of_ids = {row['catalog_id'] for row in db.execute("SELECT catalog_id FROM document_catalogs WHERE doc_id = ?", (doc_id,)).fetchall()}
        favorites_catalog = db.execute("SELECT id FROM catalogs WHERE name = '⭐ Favorites'").fetchone()
        is_favorite = favorites_catalog['id'] in member_of_ids if favorites_catalog else False
        
        return jsonify({
            'is_favorite': is_favorite,
            'note': curation['note'] if curation and curation['note'] else '',
            'all_catalogs': [dict(cat) for cat in all_catalogs],
            'member_of_catalogs': list(member_of_ids),
            'comments': [dict(c) for c in comments],
            'current_user': {'id': g.user['id'], 'role': g.user['role']}
        })

    if request.method == 'POST':
        note = request.json.get('note', '')
        db.execute("""
            INSERT INTO document_curation (doc_id, user_id, note, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(doc_id, user_id) DO UPDATE SET note=excluded.note, updated_at=excluded.updated_at
        """, (doc_id, g.user['id'], note))
        db.commit()
        return jsonify({'success': True})

@api_bp.route('/document/<int:doc_id>/color', methods=['POST'])
@login_required
def set_document_color(doc_id):
    """Sets the color tag for a document."""
    color = request.json.get('color')
    db = get_db()
    get_document_or_404(db, doc_id)
    db.execute("UPDATE documents SET color = ? WHERE id = ?", (color, doc_id))
    db.commit()
    return jsonify({'success': True, 'color': color})

@api_bp.route('/document/<int:doc_id>/comments', methods=['POST'])
@login_required
def add_document_comment(doc_id):
    """Adds a new public comment to a document."""
    comment_text = request.json.get('comment_text', '').strip()
    if not comment_text:
        return jsonify({'success': False, 'message': 'Comment cannot be empty.'}), 400
    
    db = get_db()
    get_document_or_404(db, doc_id)
    db.execute("INSERT INTO document_comments (doc_id, user_id, comment_text) VALUES (?, ?, ?)", (doc_id, g.user['id'], comment_text))
    db.commit()
    return jsonify({'success': True, 'message': 'Comment added.'}), 201

@api_bp.route('/comments/<int:comment_id>', methods=['DELETE'])
@login_required
def delete_comment(comment_id):
    """Deletes a comment if the user has permission."""
    db = get_db()
    comment = db.execute("SELECT user_id FROM document_comments WHERE id = ?", (comment_id,)).fetchone()
    if not comment:
        return jsonify({'success': False, 'message': 'Comment not found.'}), 404
    if comment['user_id'] != g.user['id'] and g.user['role'] != 'admin':
        return jsonify({'success': False, 'message': 'You do not have permission to delete this comment.'}), 403
    
    db.execute("DELETE FROM document_comments WHERE id = ?", (comment_id,))
    db.commit()
    return jsonify({'success': True, 'message': 'Comment deleted.'})

@api_bp.route('/tags', methods=['GET'])
@login_required
def get_all_tags():
    """Returns a list of all unique tags in the system."""
    db = get_db()
    all_tags = [row['name'] for row in db.execute("SELECT name FROM tags ORDER BY name")]
    return jsonify(all_tags)

@api_bp.route('/document/<int:doc_id>/tags', methods=['GET', 'POST'])
@login_required
def document_tags(doc_id):
    """Gets or sets the tags for a specific document."""
    db = get_db()
    get_document_or_404(db, doc_id)

    if request.method == 'GET':
        tags = [row['name'] for row in db.execute("SELECT t.name FROM tags t JOIN document_tags dt ON t.id = dt.tag_id WHERE dt.doc_id = ? ORDER BY t.name", (doc_id,)).fetchall()]
        return jsonify({'success': True, 'tags': tags})
    
    if request.method == 'POST':
        tags = sorted(list(set(t.strip().lower() for t in request.json.get('tags', []) if t.strip())))
        try:
            db.execute("BEGIN")
            db.execute("DELETE FROM document_tags WHERE doc_id = ?", (doc_id,))
            if tags:
                db.executemany("INSERT OR IGNORE INTO tags (name) VALUES (?)", [(tag,) for tag in tags])
                placeholders = ','.join('?' for _ in tags)
                tag_ids = [row['id'] for row in db.execute(f"SELECT id FROM tags WHERE name IN ({placeholders})", tags)]
                db.executemany("INSERT INTO document_tags (doc_id, tag_id) VALUES (?, ?)", [(doc_id, tag_id) for tag_id in tag_ids])
            db.commit()
            return jsonify({'success': True, 'tags': tags})
        except sqlite3.Error as e:
            db.rollback()
            return jsonify({'success': False, 'message': str(e)}), 500

@api_bp.route('/document/<int:doc_id>/catalogs', methods=['POST'])
@login_required
def set_document_catalogs(doc_id):
    """Updates the catalog membership for a document."""
    catalog_ids = request.json.get('catalog_ids', [])
    db = get_db()
    get_document_or_404(db, doc_id)
    try:
        db.execute("BEGIN")
        # We only manage user/favorites types here. Podcast catalogs are managed automatically.
        db.execute("""
            DELETE FROM document_catalogs 
            WHERE doc_id = ? AND catalog_id IN (SELECT id FROM catalogs WHERE catalog_type IN ('user', 'favorites'))
        """, (doc_id,))
        if catalog_ids:
            # Ensure we are only trying to insert into valid catalogs
            db.executemany("INSERT INTO document_catalogs (doc_id, catalog_id) VALUES (?, ?)", [(doc_id, cat_id) for cat_id in catalog_ids])
        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500