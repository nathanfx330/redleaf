# --- File: ./semantic_assistant.py (DEFINITIVELY FIXED - FINAL) ---
import sys
from pathlib import Path
from typing import Dict, Union, List, Tuple

# --- Add project to Python path ---
project_dir = Path(__file__).resolve().parent
sys.path.append(str(project_dir))

# --- Imports from Redleaf project ---
from project import create_app
from project.assistant_core import BaseAssistant, get_page_content
from project.config import REDLEAF_BASE_URL, REASONING_MODEL, EMBEDDING_MODEL
from project.database import get_db
import numpy as np
import ollama
import re

# --- Tool Definitions ---
def find_documents(query: Union[str, int]) -> str:
    db = get_db()
    results = []
    try:
        doc_id = int(query)
        doc = db.execute("SELECT id, relative_path FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if doc: results.append(doc)
    except (ValueError, TypeError):
        results = db.execute("SELECT id, relative_path FROM documents WHERE relative_path LIKE ?", (f"%{query}%",)).fetchall()
    if not results: return f"No documents found matching '{query}'."
    return "\n".join([f"ID: {doc['id']}, Path: {doc['relative_path']}" for doc in results])

def search_document_content(doc_id: int, search_term: str, exclude_pages: List[int] = None) -> str:
    db = get_db()
    terms_to_search = {term for term in search_term.split()}; terms_to_search.update({term.upper() for term in terms_to_search})
    escaped_terms = [term.replace('"', '""') for term in terms_to_search]; fts_query = " OR ".join([f'"{term}"' for term in escaped_terms])
    sql = "SELECT DISTINCT page_number FROM content_index WHERE doc_id = ? AND content_index MATCH ? "
    params = [doc_id, fts_query]
    if exclude_pages:
        placeholders = ', '.join('?' for _ in exclude_pages); sql += f"AND page_number NOT IN ({placeholders}) "; params.extend(exclude_pages)
    sql += "ORDER BY page_number ASC"
    results = db.execute(sql, params).fetchall()
    if not results: return f"No pages mentioning '{search_term}' found."
    
    context_block = ""
    for row in results:
        page_num = row['page_number']
        page_text, doc_info = get_page_content(doc_id=doc_id, page_number=page_num)
        if not doc_info: continue
        
        doc_url = f"{REDLEAF_BASE_URL}/document/{doc_id}"
        entry = f"--- CONTEXT from Document #{doc_id} ({doc_info['relative_path']}) URL: {doc_url}, Page {page_num} ---\n{page_text}\n\n"
        
        if len(context_block) + len(entry) > 12000:
            context_block += "[INFO] Context limit reached.\n"; break
        context_block += entry
    return context_block

def find_most_mentioned_entities(doc_id: int, entity_label: str = None, limit: int = 5) -> str:
    db = get_db()
    sql = "SELECT e.text, e.label, COUNT(ea.entity_id) as mention_count FROM entity_appearances ea JOIN entities e ON ea.entity_id = e.id WHERE ea.doc_id = ?"
    params = [doc_id]
    if entity_label:
        valid_labels = ['PERSON', 'GPE', 'LOC', 'ORG', 'DATE', 'EVENT', 'PRODUCT', 'WORK_OF_ART', 'LAW', 'LANGUAGE', 'NORP', 'FACILITY', 'MONEY', 'QUANTITY', 'ORDINAL', 'CARDINAL']
        if entity_label.upper() in valid_labels: sql += " AND e.label = ?"; params.append(entity_label.upper())
        else: return f"Error: Invalid entity label '{entity_label}'."
    sql += " GROUP BY ea.entity_id ORDER BY mention_count DESC LIMIT ?"; params.append(limit)
    results = db.execute(sql, params).fetchall()
    if not results: return f"No entities of type '{entity_label}' found." if entity_label else f"No entities found."
    return "Top mentioned entities:\n" + "\n".join([f"- {r['text']} ({r['label']}): {r['mention_count']} mentions" for r in results])

def summarize_document(doc_id: int, topic: str = None) -> str:
    db = get_db()
    doc = db.execute("SELECT id, page_count FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc: return f"Error: No document found with ID {doc_id}."
    pages = set()
    if topic:
        terms = {t for t in topic.split()}; terms.update({t.upper() for t in terms})
        escaped = [t.replace('"', '""') for t in terms]; fts_query = " OR ".join([f'"{t}"' for t in escaped])
        pages.update([r['page_number'] for r in db.execute("SELECT page_number FROM content_index WHERE doc_id = ? AND content_index MATCH ? ORDER BY rank LIMIT 5", (doc_id, fts_query)).fetchall()])
    if not pages:
        pages.update([r['page_number'] for r in db.execute("SELECT page_number FROM content_index WHERE doc_id = ? ORDER BY page_number ASC LIMIT 3", (doc_id,)).fetchall()])
        if doc['page_count'] and doc['page_count'] > 5:
            pages.update([r['page_number'] for r in db.execute("SELECT page_number FROM content_index WHERE doc_id = ? ORDER BY page_number DESC LIMIT 2", (doc_id,)).fetchall()])
    if not pages: return "This document has no indexed content to summarize."
    
    context = ""
    for num in sorted(list(pages)):
        page_text, doc_info = get_page_content(doc_id=doc_id, page_number=num)
        if not doc_info: continue
        
        doc_url = f"{REDLEAF_BASE_URL}/document/{doc_id}"
        entry = f"--- CONTEXT from Document #{doc_id} ({doc_info['relative_path']}) URL: {doc_url}, Page {num} ---\n{page_text}\n\n"
        
        if len(context) + len(entry) > 12000: break
        context += entry
    return context

def semantic_search_document_content(doc_id: int, search_term: str, limit: int = 3) -> str:
    db = get_db()
    doc = db.execute("SELECT id, relative_path, file_type FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc: return f"Error: No document found with ID {doc_id}."
    try:
        query_embedding_response = ollama.embeddings(model=EMBEDDING_MODEL, prompt=search_term)
        query_vector = np.array(query_embedding_response['embedding'], dtype=np.float32)
    except Exception as e: return f"Error: Could not generate query embedding: {e}"
    doc_chunks = db.execute("SELECT page_number, chunk_text, embedding FROM embedding_chunks WHERE doc_id = ?", (doc_id,)).fetchall()
    if not doc_chunks: return f"No indexed content available for semantic search in document {doc_id}."
    similarities = []
    for chunk in doc_chunks:
        chunk_vector = np.frombuffer(chunk['embedding'], dtype=np.float32)
        dot_product = np.dot(query_vector, chunk_vector)
        norm_query = np.linalg.norm(query_vector)
        norm_chunk = np.linalg.norm(chunk_vector)
        if norm_query > 0 and norm_chunk > 0:
            similarity = dot_product / (norm_query * norm_chunk)
            similarities.append({'page_number': chunk['page_number'], 'text': chunk['chunk_text'], 'score': similarity})
    similarities.sort(key=lambda x: x['score'], reverse=True)
    
    context_block = ""
    doc_url = f"{REDLEAF_BASE_URL}/document/{doc_id}"
    for item in similarities[:limit]:
        context_block += f"--- CONTEXT from Document #{doc_id} ({doc['relative_path']}) URL: {doc_url}, Page {item['page_number']} (Semantic Match) ---\n{item['text']}\n\n"
    
    return context_block if context_block else f"No semantically relevant content found for '{search_term}'."

def get_page_content_tool_wrapper(doc_id: int, page_number: int) -> str:
    """A wrapper for get_page_content that formats the output as a string for the tool router."""
    page_text, doc_info = get_page_content(doc_id=doc_id, page_number=page_number)
    
    if not doc_info:
        return f"Error: Could not retrieve information for Document ID {doc_id}."
    
    doc_url = f"{REDLEAF_BASE_URL}/document/{doc_id}"
    return f"--- CONTEXT from Document #{doc_id} ({doc_info['relative_path']}) URL: {doc_url}, Page {page_number} ---\n{page_text}\n\n"

AVAILABLE_TOOLS = { 
    "find_documents": find_documents, 
    "search_document_content": search_document_content, 
    "get_page_content": get_page_content_tool_wrapper, 
    "find_most_mentioned_entities": find_most_mentioned_entities, 
    "summarize_document": summarize_document, 
    "semantic_search_document_content": semantic_search_document_content,
}

ROUTER_PROMPT = """You are a tool-routing assistant. Your only job is to analyze a user's request and choose a single tool to use from the list below. The user may use a special `search:` command which is handled by the system; you will only be activated for other questions. Respond with ONLY a single JSON object for the tool call.
## Tool Selection Rules (Requires an Active Document):
- To **find or list** documents, use `find_documents`.
- For a **general summary** of the *loaded document*, use `summarize_document`.
- To **read a specific page number**, use `get_page_content`.
- To get a **list of frequent entities**, use `find_most_mentioned_entities`.
- For a **specific question about a topic (keyword search)**, use `search_document_content`.
- For a **conceptual or semantic question** about a topic, use `semantic_search_document_content`.
- To get **"more"** pages on a topic, use `search_document_content` and `exclude_pages`.
## Active Document Context:
The user is working with Document ID {doc_id}: '{doc_path}'. Pages already seen: {seen_pages}.
## Available Tools:
- `find_documents(query: str | int)`
- `summarize_document(doc_id: int, topic: str = None)`
- `get_page_content(doc_id: int, page_number: int)`
- `find_most_mentioned_entities(doc_id: int, entity_label: str = None, limit: int = 5)`
- `search_document_content(doc_id: int, search_term: str, exclude_pages: list[int] = None)`
- `semantic_search_document_content(doc_id: int, search_term: str, limit: int = 3)`
"""


ASSISTANT_CORE_REGEX_FOR_EACH = r'^for (?:each )?pages?(?:\s+in\s+\[?(\d+(?:-\d+)?)\]?)?(?:\s*\+\s*)?(.+)'


def main():
    """Initializes and runs the Semantic Assistant."""
    app = create_app(start_background_thread=False)
    with app.app_context():
        # Pass the new regex to the assistant core
        assistant = BaseAssistant(
            reasoning_model=REASONING_MODEL,
            available_tools=AVAILABLE_TOOLS,
            router_prompt=ROUTER_PROMPT,
            search_strategy="hybrid"
        )
        # We will override the regex inside the BaseAssistant class instance
        # to ensure the flexible one is used.
        assistant.for_each_regex = ASSISTANT_CORE_REGEX_FOR_EACH
        assistant.run()

if __name__ == "__main__":
    main()