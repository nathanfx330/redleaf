# --- File: ./project/utils.py ---
import math
import json
import re
import datetime
import sqlite3
from markupsafe import Markup
from flask import current_app, g

# Import from our own package to avoid circular dependencies
# This is now the ONLY dependency on background.py, and it's one-way.
from .background import task_queue, active_tasks, active_tasks_lock

# ===================================================================
# TEMPLATE FILTERS
# ===================================================================

def _jinja2_filter_datetime(date, fmt='%Y-%m-%d %H:%M'):
    """Formats a datetime object or a string representation of a date."""
    if not date:
        return "N/A"
    if isinstance(date, str):
        try:
            date = datetime.datetime.fromisoformat(date)
        except (ValueError, TypeError):
            try:
                date = datetime.datetime.strptime(date, '%Y-%m-%d %H:%M:%S.%f')
            except (ValueError, TypeError):
                try:
                    date = datetime.datetime.strptime(date, '%Y-%m-%d %H:%M:%S')
                except (ValueError, TypeError):
                    return date  # Return original string if all parsing fails
    return date.strftime(fmt) if hasattr(date, 'strftime') else date

def _jinja2_filter_filesize(size_bytes):
    """Converts a size in bytes to a human-readable format."""
    if size_bytes is None:
        return "N/A"
    if size_bytes == 0:
        return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"

def escapejs_filter(value):
    """Escapes a string for safe inclusion in a Javascript string literal."""
    return Markup(json.dumps(value)[1:-1])

def register_template_filters(app):
    """Registers all custom template filters with the Flask application."""
    app.template_filter('strftime')(_jinja2_filter_datetime)
    app.template_filter('filesize')(_jinja2_filter_filesize)
    app.template_filter('escapejs')(escapejs_filter)


# ===================================================================
# HELPER FUNCTIONS
# ===================================================================

def _get_dashboard_state(db):
    """Helper to get the current state for the dashboard UI."""
    statuses = [row['status'] for row in db.execute("SELECT status FROM documents").fetchall()]

    with active_tasks_lock:
        active_task_list = list(active_tasks.values())
    queued_task_types = [item[0] for item in list(task_queue.queue)]

    task_states = {'discover': 'standard', 'process': 'standard', 'cache': 'standard'}

    is_discover_active = any(t[0] == 'discover' for t in active_task_list) or 'discover' in queued_task_types
    is_process_active = any(t[0] == 'process' for t in active_task_list) or 'process' in queued_task_types
    is_cache_active = any(t[0] == 'cache' for t in active_task_list) or 'cache' in queued_task_types

    # Determine the primary action button
    if is_discover_active:
        primary_action = 'discover'
    elif is_process_active:
        primary_action = 'process'
    elif is_cache_active:
        primary_action = 'cache'
    elif 'New' in statuses:
        primary_action = 'process'
    elif 'Indexed' in statuses:
        primary_action = 'cache'
    else:
        primary_action = 'discover'

    task_states[primary_action] = 'primary'

    # Disable buttons for tasks that are currently active
    if is_discover_active: task_states['discover'] = 'disabled'
    if is_process_active: task_states['process'] = 'disabled'
    if is_cache_active: task_states['cache'] = 'disabled'

    return {
        'queue_size': task_queue.qsize(),
        'task_states': task_states
    }


def _create_manual_snippet(page_content, subject, object_text, relationship_phrase, context=100):
    """
    Manually creates a highlighted snippet, centered on the specific relationship phrase
    using a flexible regex to account for whitespace differences.
    """
    if not page_content or not relationship_phrase:
        return page_content[:context*2] + "..." if page_content else ""

    # --- THIS IS THE FIX ---
    # Build a regex pattern that finds the phrase words with any whitespace in between.
    words = re.split(r'\s+', relationship_phrase.strip())
    # Escape each word to treat it literally, then join with flexible whitespace matcher.
    pattern = r'\s+'.join(re.escape(word) for word in words)
    
    match = re.search(pattern, page_content, re.IGNORECASE)

    if match:
        # If the phrase is found, center the snippet around it.
        phrase_start = match.start()
        phrase_end = match.end()
    else:
        # Fallback: if the exact phrase isn't found (very rare), find the first
        # occurrences of subject and object and create a window around them.
        try:
            subject_start = page_content.lower().index(subject.lower())
            object_start = page_content.lower().index(object_text.lower())
            phrase_start = min(subject_start, object_start)
            phrase_end = max(subject_start + len(subject), object_start + len(object_text))
        except ValueError:
            # If entities can't even be found, just return the start of the content.
            return Markup(page_content[:context * 2] + "...")

    # Create the snippet window around the located phrase/entities.
    snippet_start = max(0, phrase_start - context)
    snippet_end = min(len(page_content), phrase_end + context)

    # Extract the relevant window from the full page content.
    snippet_text = page_content[snippet_start:snippet_end]
    
    # Add ellipses if the snippet was cut at the beginning or end.
    if snippet_start > 0:
        snippet_text = "... " + snippet_text
    if snippet_end < len(page_content):
        snippet_text = snippet_text + " ..."
    
    # Highlight both entities within this focused window.
    # Sorting by length ensures that longer names are matched before their substrings.
    entities_to_highlight = sorted(list(set([subject, object_text])), key=len, reverse=True)
    highlight_pattern = '|'.join(re.escape(e) for e in entities_to_highlight if e)
    
    if highlight_pattern:
        snippet_text = re.sub(f'({highlight_pattern})', r'<strong>\1</strong>', snippet_text, flags=re.IGNORECASE)

    return Markup(snippet_text)
    # --- END FIX ---


def _truncate_long_snippet(snippet_html, context_length=200):
    """
    Truncates a potentially very long snippet for display, centering on the keyword.
    This is for single-page documents like SRT or HTML where the DB snippet can be huge.
    """
    if not snippet_html:
        return ""

    # The keyword is already wrapped in <strong>...</strong> by the FTS5 snippet function.
    # We'll use this as the focal point.
    strong_tag_start = snippet_html.find('<strong>')
    
    if strong_tag_start == -1:
        # Keyword not found, just truncate from the beginning
        if len(snippet_html) > context_length * 2:
            return Markup(snippet_html[:context_length * 2] + '...')
        return Markup(snippet_html)

    strong_tag_end = snippet_html.find('</strong>', strong_tag_start)
    if strong_tag_end == -1: strong_tag_end = len(snippet_html)

    # Calculate the start and end points for the context window
    start_pos = max(0, strong_tag_start - context_length)
    end_pos = min(len(snippet_html), strong_tag_end + context_length)

    # Extract the truncated snippet
    truncated_snippet = snippet_html[start_pos:end_pos]

    # Add ellipses if the snippet was cut at the beginning or end
    prefix = '...' if start_pos > 0 else ''
    suffix = '...' if end_pos < len(snippet_html) else ''

    return Markup(f"{prefix}{truncated_snippet}{suffix}")