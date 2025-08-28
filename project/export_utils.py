# --- File: ./project/export_utils.py ---
import io
import json
from bs4 import BeautifulSoup

# ODFPY imports for creating the ODT document
from odf.opendocument import OpenDocumentText
from odf.style import Style, TextProperties
from odf.text import H, P, Span

# Corrected CITEPROC-PY IMPORTS
from citeproc import Citation, CitationItem, CitationStylesStyle, CitationStylesBibliography
from citeproc.source.json import CiteProcJSON
from citeproc.formatter import html as html_formatter

from .database import get_db
from .config import BASE_DIR

def warn(citation_item):
    """A simple function to handle warnings from the citation processor."""
    print(f"CSL Processor Warning: {citation_item}")

def generate_odt(editor_html: str, report_id: int, user_id: int) -> io.BytesIO:
    """
    Generates an ODT document from editor HTML, including formatted citations.
    """
    # --- 1. SETUP ---
    soup = BeautifulSoup(editor_html, 'html.parser')
    doc = OpenDocumentText()

    # Define basic styles
    bold_style = Style(name="Bold", family="text")
    bold_style.addElement(TextProperties(fontweight="bold"))
    doc.styles.addElement(bold_style)

    italic_style = Style(name="Italic", family="text")
    italic_style.addElement(TextProperties(fontstyle="italic"))
    doc.styles.addElement(italic_style)

    # --- 2. GATHER ALL DATA FOR THE BIBLIOGRAPHY AT THE END ---
    db = get_db()
    
    citation_spans = soup.find_all('span', class_='citation-pill')
    unique_doc_ids = {span.get('data-doc-id') for span in citation_spans if span.get('data-doc-id')}
    
    csl_data_map = {}
    if unique_doc_ids:
        placeholders = ','.join('?' for _ in unique_doc_ids)
        metadata_rows = db.execute(
            f"SELECT doc_id, csl_json FROM document_metadata WHERE doc_id IN ({placeholders})",
            list(unique_doc_ids)
        ).fetchall()
        for row in metadata_rows:
            if row['csl_json']:
                try:
                    csl_data_map[str(row['doc_id'])] = json.loads(row['csl_json'])
                except json.JSONDecodeError:
                    continue

    # --- 3. INITIALIZE CITEPROC (used only for the final bibliography) ---
    style_path = str(BASE_DIR / "styles" / "chicago-author-date.csl")
    bib_source = CiteProcJSON(list(csl_data_map.values()))
    bib_style = CitationStylesStyle(style_path, validate=False)
    bibliography = CitationStylesBibliography(bib_style, bib_source, html_formatter)
    
    # Register all unique sources so the bibliography can be generated correctly.
    for csl_item in csl_data_map.values():
        if csl_item and csl_item.get('id'):
            bibliography.register(Citation([CitationItem(csl_item['id'])]))
            
    # --- 4. BUILD THE ODT DOCUMENT BODY ---
    for tag in soup.find_all(True, recursive=False):
        if tag.name.startswith('h') and tag.name[1].isdigit():
            level = int(tag.name[1])
            odt_element = H(outlinelevel=level)
        else:
            odt_element = P()

        for child in tag.children:
            if isinstance(child, str):
                odt_element.addText(child)
            elif child.name == 'strong':
                span = Span(stylename=bold_style, text=child.get_text())
                odt_element.addElement(span)
            elif child.name == 'em':
                span = Span(stylename=italic_style, text=child.get_text())
                odt_element.addElement(span)
            
            # ===================================================================
            # === THE FIX: Use the existing text from the pill directly ========
            # ===================================================================
            elif child.name == 'span' and 'citation-pill' in child.get('class', []):
                # The Tiptap editor already created the correct in-text citation.
                # We just need to extract that text from the HTML.
                citation_text = child.get_text()
                odt_element.addText(citation_text)
        
        # Add the completed paragraph or heading to the document
        doc.text.addElement(odt_element)
    
    # --- 5. GENERATE AND ADD THE BIBLIOGRAPHY AT THE END ---
    if csl_data_map:
        doc.text.addElement(H(outlinelevel=1, text="Bibliography"))
        
        for entry in bibliography.bibliography():
            entry_soup = BeautifulSoup(str(entry), 'html.parser')
            p = P()
            # This logic preserves italics for titles in the final bibliography.
            for content_part in entry_soup.contents:
                if isinstance(content_part, str):
                    p.addText(content_part)
                elif content_part.name in ['i', 'em']:
                    p.addElement(Span(stylename=italic_style, text=content_part.get_text()))
                elif content_part.name in ['b', 'strong']:
                    p.addElement(Span(stylename=bold_style, text=content_part.get_text()))
                else:
                    p.addText(content_part.get_text())
            doc.text.addElement(p)
            
    # --- 6. SAVE AND RETURN THE ODT FILE ---
    odt_file = io.BytesIO()
    doc.save(odt_file)
    odt_file.seek(0)
    
    return odt_file