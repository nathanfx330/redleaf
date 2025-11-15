# --- File: ./project/blueprints/api/helpers.py ---
import hashlib
from pathlib import Path
from typing import Union
from lxml import etree as ET
from email.utils import parsedate_to_datetime
from flask import abort, g

from ...config import DOCUMENTS_DIR

# ===================================================================
# --- DATABASE & DATA ACCESS HELPERS ---
# ===================================================================

def get_document_or_404(db, doc_id: int):
    """Fetches a document by its ID from the database or aborts with a 404 error."""
    doc = db.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc:
        abort(404, description=f"Document with ID {doc_id} not found.")
    return doc

def escape_like(s: str) -> str:
    """Escapes strings for safe use in a SQL LIKE clause."""
    if not s:
        return ""
    return s.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')

def get_base_document_query_fields() -> str:
    """
    Returns a string of common SQL fields for document list queries.
    It uses a placeholder '?' for the user_id and aliases d.id to doc_id.
    """
    return """
        d.id as doc_id, d.relative_path, d.color, d.page_count, d.file_type,
        (SELECT COUNT(*) FROM document_comments WHERE doc_id = d.id) as comment_count,
        (SELECT 1 FROM document_curation WHERE doc_id = d.id AND user_id = ?) as has_personal_note,
        (SELECT 1 FROM document_tags WHERE doc_id = d.id LIMIT 1) as has_tags,
        (SELECT GROUP_CONCAT(c.name, ', ') FROM catalogs c JOIN document_catalogs dc ON c.id = dc.catalog_id WHERE dc.doc_id = d.id) as catalog_names
    """

def get_aggregated_document_query_fields() -> str:
    """
    Returns a string of common SQL fields for document list queries that use GROUP BY.
    It wraps non-aggregated columns in MAX() to satisfy SQL requirements, which is safe
    since all rows in the group will have the same value for these columns.
    """
    return """
        d.id as doc_id, MAX(d.relative_path) as relative_path, MAX(d.color) as color, 
        MAX(d.page_count) as page_count, MAX(d.file_type) as file_type,
        (SELECT COUNT(*) FROM document_comments WHERE doc_id = d.id) as comment_count,
        (SELECT 1 FROM document_curation WHERE doc_id = d.id AND user_id = ?) as has_personal_note,
        (SELECT 1 FROM document_tags WHERE doc_id = d.id LIMIT 1) as has_tags,
        (SELECT GROUP_CONCAT(c.name, ', ') FROM catalogs c JOIN document_catalogs dc ON c.id = dc.catalog_id WHERE dc.doc_id = d.id) as catalog_names
    """

# ===================================================================
# --- PODCAST & XML PARSING LOGIC (Centralized) ---
# ===================================================================

def parse_podcast_xml_to_csl(item_element: ET.Element, channel_title: str = "", channel_author: str = "") -> tuple[dict, Union[str, None]]:
    """Parses a single <item> XML element from an RSS feed into CSL-JSON format."""
    if item_element is None:
        return None, None
        
    csl_data = {}
    namespaces = {'itunes': 'http://www.itunes.com/dtds/podcast-1.0.dtd'}

    title_el = item_element.find('itunes:title', namespaces) or item_element.find('title')
    if title_el is not None and title_el.text:
        csl_data['title'] = title_el.text.strip()
        
    if channel_title:
        csl_data['container-title'] = channel_title.strip()

    author_el = item_element.find('itunes:author', namespaces)
    author_text = (author_el.text.strip() if author_el is not None and author_el.text else channel_author.strip() if channel_author else None)
    if author_text:
        csl_data['author'] = [{'literal': author_text}]

    pub_date_el = item_element.find('pubDate')
    if pub_date_el is not None and pub_date_el.text:
        try:
            dt = parsedate_to_datetime(pub_date_el.text)
            csl_data['issued'] = {'date-parts': [[dt.year, dt.month, dt.day]]}
        except (ValueError, TypeError):
            pass  # Ignore malformed dates

    link_el = item_element.find('link')
    if link_el is not None and link_el.text:
        csl_data['URL'] = link_el.text.strip()
    
    enclosure_el = item_element.find('enclosure')
    enclosure_url = enclosure_el.get('url', '').strip() if enclosure_el is not None else None
    
    csl_data['type'] = 'interview'
    return csl_data, enclosure_url

def find_xml_matches_for_doc(doc_relative_path: str) -> list:
    """Scans all XML files to find potential metadata matches for a given SRT document."""
    target_srt_basename = Path(doc_relative_path).stem
    matches = []
    parser = ET.XMLParser(recover=True)

    for xml_path in DOCUMENTS_DIR.rglob('*.xml'):
        try:
            tree = ET.parse(str(xml_path), parser=parser)
            channel_title = tree.findtext('channel/title', "")
            namespaces = {'itunes': 'http://www.itunes.com/dtds/podcast-1.0.dtd'}
            channel_author = tree.findtext('channel/itunes:author', "", namespaces=namespaces)
            
            for item in tree.findall('channel/item'):
                is_match = False
                
                enclosure_tag = item.find('enclosure')
                if enclosure_tag is not None and enclosure_tag.get('url'):
                    url_filename = enclosure_tag.get('url').split('/')[-1].split('?')[0]
                    if Path(url_filename).stem == target_srt_basename:
                        is_match = True

                if not is_match:
                    title_el = item.find('itunes:title', namespaces) or item.find('title')
                    if title_el is not None and title_el.text and target_srt_basename in title_el.text:
                        is_match = True

                if is_match:
                    preview_data, enclosure_url = parse_podcast_xml_to_csl(item, channel_title, channel_author)
                    if not preview_data: continue
                    
                    guid_el = item.find('guid')
                    hash_content = (guid_el.text if guid_el is not None and guid_el.text else preview_data.get('title', ''))
                    item_hash = hashlib.md5(hash_content.encode()).hexdigest()
                    
                    matches.append({
                        'xml_path': xml_path.relative_to(DOCUMENTS_DIR).as_posix(),
                        'preview': preview_data,
                        'item_hash': item_hash,
                        'enclosure_url': enclosure_url
                    })
        except ET.XMLSyntaxError:
            continue
            
    return matches