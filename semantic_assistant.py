# --- File: ./semantic_assistant.py ---
import sys
from pathlib import Path
from typing import Dict, Union, List, Tuple
from collections import defaultdict

# --- Add project to Python path ---
project_dir = Path(__file__).resolve().parent
sys.path.append(str(project_dir))

# --- Imports from Redleaf project ---
from project import create_app
from project.assistant_core import BaseAssistant, get_page_content, read_specific_pages
from project.config import REDLEAF_BASE_URL, REASONING_MODEL, EMBEDDING_MODEL
from project.database import get_db
from project.prompts import ROUTER_PROMPT
import numpy as np
import ollama
import re
import json

# --- Tool Definitions ---

def _find_pages_for_topic(db, doc_id: int, search_term: str) -> set:
    """Helper function to find page numbers for a single topic within a specific doc."""
    entity_sql = "SELECT id FROM entities WHERE ? LIKE '%' || text || '%'"
    entity = db.execute(entity_sql, (search_term,)).fetchone()
    if entity:
        pages = db.execute("SELECT page_number FROM entity_appearances WHERE entity_id = ? AND doc_id = ?", (entity['id'], doc_id)).fetchall()
        if pages:
            return {row['page_number'] for row in pages}
            
    terms_to_search = {term for term in search_term.split()}; terms_to_search.update({term.upper() for term in terms_to_search})
    escaped_terms = [term.replace('"', '""') for term in terms_to_search]; fts_query = " OR ".join([f'"{term}"' for term in escaped_terms])
    pages = db.execute("SELECT page_number FROM content_index WHERE doc_id = ? AND content_index MATCH ?", (doc_id, fts_query)).fetchall()
    return {row['page_number'] for row in pages}

def _find_all_pages_for_topic(db, search_term: str) -> Dict[int, set]:
    """Helper for global search: finds all pages for a topic across all documents."""
    pages_by_doc = defaultdict(set)
    entity_sql = "SELECT id FROM entities WHERE ? LIKE '%' || text || '%'"
    entity = db.execute(entity_sql, (search_term,)).fetchone()
    if entity:
        mentions = db.execute("SELECT doc_id, page_number FROM entity_appearances WHERE entity_id = ?", (entity['id'],)).fetchall()
        for mention in mentions:
            pages_by_doc[mention['doc_id']].add(mention['page_number'])

    terms_to_search = {term for term in search_term.split()}; terms_to_search.update({term.upper() for term in terms_to_search})
    escaped_terms = [term.replace('"', '""') for term in terms_to_search]; fts_query = " OR ".join([f'"{term}"' for term in escaped_terms])
    matches = db.execute("SELECT doc_id, page_number FROM content_index WHERE content_index MATCH ?", (fts_query,)).fetchall()
    for match in matches:
        pages_by_doc[match['doc_id']].add(match['page_number'])
        
    return pages_by_doc

def research_across_all_documents(topics: list[str]) -> str:
    """Finds pages where ALL listed topics are mentioned together across the entire knowledge base and returns their content."""
    db = get_db()
    if not topics: return "Error: You must provide at least one topic to research."
    
    # Start with the universe of all documents for the first topic
    master_pages_by_doc = _find_all_pages_for_topic(db, topics[0])
    
    # Iteratively find the intersection for subsequent topics
    for topic in topics[1:]:
        next_pages_by_doc = _find_all_pages_for_topic(db, topic)
        # Find the set of document IDs that are common to both results
        intersecting_docs = set(master_pages_by_doc.keys()).intersection(set(next_pages_by_doc.keys()))
        
        temp_master = defaultdict(set)
        for doc_id in intersecting_docs:
            # For each common document, find the intersection of the page numbers
            intersecting_pages = master_pages_by_doc[doc_id].intersection(next_pages_by_doc[doc_id])
            if intersecting_pages:
                temp_master[doc_id] = intersecting_pages
        master_pages_by_doc = temp_master

    if not master_pages_by_doc: return f"No documents found containing all of the specified topics together: {', '.join(topics)}"

    sources = []
    # Limit the results to prevent overwhelming the context window
    MAX_DOCS_TO_READ = 3
    MAX_PAGES_PER_DOC = 2
    
    doc_count = 0
    for doc_id, page_numbers in sorted(master_pages_by_doc.items()):
        if doc_count >= MAX_DOCS_TO_READ: break
        page_count = 0
        for page_num in sorted(list(page_numbers)):
            if page_count >= MAX_PAGES_PER_DOC: break
            sources.append({"doc_id": doc_id, "page_number": page_num})
            page_count += 1
        doc_count += 1
            
    return read_specific_pages(db, sources)

def find_co_mentions(doc_id: int, topic_a: str, topic_b: str) -> str:
    """Finds pages where two topics are mentioned together in a specific document and returns their content."""
    db = get_db()
    pages_a = _find_pages_for_topic(db, doc_id, topic_a)
    if not pages_a: return f"Topic '{topic_a}' was not found in document {doc_id}."
        
    pages_b = _find_pages_for_topic(db, doc_id, topic_b)
    if not pages_b: return f"Topic '{topic_b}' was not found in document {doc_id}."

    intersecting_pages = sorted(list(pages_a.intersection(pages_b)))
    
    if not intersecting_pages: return f"While both '{topic_a}' and '{topic_b}' were found, they do not appear on the same pages."
        
    sources = [{"doc_id": doc_id, "page_number": page_num} for page_num in intersecting_pages]
    return read_specific_pages(db, sources)

def read_entity_mentions(doc_id: int, entity_text: str, entity_label: str = None) -> str:
    """Finds all pages where an entity is mentioned in a document and returns their full text."""
    db = get_db()
    entity_sql = "SELECT id FROM entities WHERE ? LIKE '%' || text || '%'"
    entity = db.execute(entity_sql, (entity_text,)).fetchone()
    if not entity: return f"Entity '{entity_text}' not found."
    pages = db.execute("SELECT DISTINCT page_number FROM entity_appearances WHERE entity_id = ? AND doc_id = ? ORDER BY page_number ASC", (entity['id'], doc_id)).fetchall()
    if not pages: return f"No mentions found for '{entity_text}' in document {doc_id}."
    sources = [{"doc_id": doc_id, "page_number": row["page_number"]} for row in pages]
    return read_specific_pages(db, sources)

# --- Other tools for interactive mode ---
def search_document_content(doc_id: int, search_term: str) -> str:
    db = get_db()
    pages = _find_pages_for_topic(db, doc_id, search_term)
    if not pages: return f"No pages mentioning '{search_term}' found."
    sources = [{"doc_id": doc_id, "page_number": page_num} for page_num in sorted(list(pages))]
    return read_specific_pages(db, sources)
    
def find_entity(entity_text: str, entity_label: str = None) -> str:
    db = get_db()
    sql = "SELECT id, text, label FROM entities WHERE ? LIKE '%' || text || '%'"
    params = [entity_text]
    if entity_label:
        sql += " AND label = ?"; params.append(entity_label.upper())
    entity = db.execute(sql, params).fetchone()
    if not entity: return f"No entity matching '{entity_text}' found."
    return f"Found entity: '{entity['text']}' ({entity['label']})."

def get_page_content_tool_wrapper(doc_id: int, page_number: int) -> str:
    page_text, doc_info = get_page_content(doc_id=doc_id, page_number=page_number)
    if not doc_info: return f"Error retrieving info for Doc ID {doc_id}."
    doc_url = f"{REDLEAF_BASE_URL}/document/{doc_id}"
    return f"--- CONTEXT from Document #{doc_id} ({doc_info['relative_path']}) URL: {doc_url}, Page {page_number} ---\n{page_text}\n\n"

def summarize_document(doc_id: int, topic: str = None) -> str: return "This tool is not used in research mode."
def find_most_mentioned_entities(doc_id: int, entity_label: str = None, limit: int = 5) -> str: return "This tool is not used in research mode."
def find_documents(query: Union[str, int]) -> str: return "This tool is not used in research mode."


# === FINALIZED TOOLSET ===
AVAILABLE_TOOLS = { 
    # Research Agent Tools
    "research_across_all_documents": research_across_all_documents,
    "find_co_mentions": find_co_mentions,
    "read_entity_mentions": read_entity_mentions,
    
    # Interactive Mode & Helper Tools
    "search_document_content": search_document_content,
    "find_entity": find_entity,
    "get_page_content": get_page_content_tool_wrapper, 
    "summarize_document": summarize_document,
    "find_most_mentioned_entities": find_most_mentioned_entities,
    "find_documents": find_documents,
}

def main():
    """Initializes and runs the Semantic Assistant."""
    app = create_app(start_background_thread=False)
    with app.app_context():
        assistant = BaseAssistant(
            reasoning_model=REASONING_MODEL,
            available_tools=AVAILABLE_TOOLS,
            router_prompt=ROUTER_PROMPT,
            search_strategy="hybrid"
        )
        assistant.run()

if __name__ == "__main__":
    main()