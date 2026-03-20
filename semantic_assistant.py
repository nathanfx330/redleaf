# --- File: ./semantic_assistant.py ---
import sys
import argparse
from pathlib import Path
from typing import Dict, Union, List, Tuple
from collections import defaultdict

# --- Add project to Python path ---
project_dir = Path(__file__).resolve().parent
sys.path.append(str(project_dir))

# --- Imports from Redleaf project ---
from project import create_app
# MODIFIED: Added _internal_fts_search to imports for the brute force tool
from project.assistant_core import BaseAssistant, get_page_content, read_specific_pages, Style, _internal_fts_search
from project.config import REDLEAF_BASE_URL, REASONING_MODEL, EMBEDDING_MODEL
from project.database import get_db
from project.prompts import ROUTER_PROMPT
from project.background import get_system_settings
import ollama

# --- Tool Definitions ---

def _find_pages_for_topic(db, doc_id: int, search_term: str) -> set:
    """Helper function to find page numbers for a single topic within a specific doc."""
    # 1. Try exact entity match first
    entity_sql = "SELECT id FROM entities WHERE text LIKE ?"
    entity = db.execute(entity_sql, (f"%{search_term}%",)).fetchone()
    
    pages = set()
    if entity:
        entity_pages = db.execute("SELECT page_number FROM entity_appearances WHERE entity_id = ? AND doc_id = ?", (entity['id'], doc_id)).fetchall()
        for row in entity_pages:
            pages.add(row['page_number'])
            
    # 2. Fallback to Full-Text Search (FTS)
    terms_to_search = {term for term in search_term.split()}
    terms_to_search.update({term.upper() for term in terms_to_search})
    
    escaped_terms = [term.replace('"', '""') for term in terms_to_search]
    if escaped_terms:
        fts_query = " OR ".join([f'"{term}"' for term in escaped_terms])
        matches = db.execute("SELECT page_number FROM content_index WHERE doc_id = ? AND content_index MATCH ?", (doc_id, fts_query)).fetchall()
        for row in matches:
            pages.add(row['page_number'])
            
    return pages

def _find_all_pages_for_topic(db, search_term: str) -> Dict[int, set]:
    """Helper for global search: finds all pages for a topic across all documents."""
    pages_by_doc = defaultdict(set)
    
    # 1. Entity Search
    entity_sql = "SELECT id FROM entities WHERE text LIKE ?"
    entity = db.execute(entity_sql, (f"%{search_term}%",)).fetchone()
    if entity:
        mentions = db.execute("SELECT doc_id, page_number FROM entity_appearances WHERE entity_id = ?", (entity['id'],)).fetchall()
        for mention in mentions:
            pages_by_doc[mention['doc_id']].add(mention['page_number'])

    # 2. FTS Search
    terms_to_search = {term for term in search_term.split()}
    terms_to_search.update({term.upper() for term in terms_to_search})
    escaped_terms = [term.replace('"', '""') for term in terms_to_search]
    
    if escaped_terms:
        fts_query = " OR ".join([f'"{term}"' for term in escaped_terms])
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
        
        temp_master = defaultdict(set)
        # Only keep docs that exist in both sets
        common_doc_ids = set(master_pages_by_doc.keys()).intersection(set(next_pages_by_doc.keys()))
        
        for doc_id in common_doc_ids:
            # Only keep pages that exist in both sets for that doc
            common_pages = master_pages_by_doc[doc_id].intersection(next_pages_by_doc[doc_id])
            if common_pages:
                temp_master[doc_id] = common_pages
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
    entity_sql = "SELECT id FROM entities WHERE text LIKE ?"
    entity = db.execute(entity_sql, (f"%{entity_text}%",)).fetchone()
    if not entity: return f"Entity '{entity_text}' not found."
    pages = db.execute("SELECT DISTINCT page_number FROM entity_appearances WHERE entity_id = ? AND doc_id = ? ORDER BY page_number ASC", (entity['id'], doc_id)).fetchall()
    if not pages: return f"No mentions found for '{entity_text}' in document {doc_id}."
    sources = [{"doc_id": doc_id, "page_number": row["page_number"]} for row in pages]
    return read_specific_pages(db, sources)

# --- Other tools for interactive mode ---

def search_document_content(doc_id: int, search_term: str) -> str:
    """Searches for a term within a document and returns relevant pages."""
    db = get_db()
    pages = _find_pages_for_topic(db, doc_id, search_term)
    if not pages: return f"No pages mentioning '{search_term}' found."
    sources = [{"doc_id": doc_id, "page_number": page_num} for page_num in sorted(list(pages))]
    return read_specific_pages(db, sources)
    
def find_entity(entity_text: str, entity_label: str = None) -> str:
    """Checks if an entity exists in the global database."""
    db = get_db()
    sql = "SELECT id, text, label FROM entities WHERE text LIKE ?"
    params = [f"%{entity_text}%"]
    if entity_label: sql += " AND label = ?"; params.append(entity_label.upper())
    entity = db.execute(sql, params).fetchone()
    if not entity: return f"No entity matching '{entity_text}' found."
    return f"Found entity: '{entity['text']}' ({entity['label']})."

def get_page_content_tool_wrapper(doc_id: int, page_number: int) -> str:
    """Fetches the raw text of a specific page."""
    page_text, doc_info = get_page_content(db=get_db(), doc_id=doc_id, page_number=page_number)
    if not doc_info: return f"Error retrieving info for Doc ID {doc_id}."
    doc_url = f"{REDLEAF_BASE_URL}/document/{doc_id}"
    return f"--- CONTEXT from Document #{doc_id} ({doc_info['relative_path']}) URL: {doc_url}, Page {page_number} ---\n{page_text}\n\n"

def summarize_document(doc_id: int, topic: str = None) -> str:
    """Reads the beginning of a document to provide a summary."""
    db = get_db()
    sources = [{"doc_id": doc_id, "page_number": i} for i in range(1, 4)]
    context = read_specific_pages(db, sources)
    if topic: return f"Here is the beginning content of Document {doc_id}. Please summarize it with a focus on '{topic}':\n\n{context}"
    return f"Here is the beginning content of Document {doc_id}. Please provide a general summary:\n\n{context}"

def find_most_mentioned_entities(doc_id: int, entity_label: str = None, limit: int = 5) -> str: 
    """Finds top entities in a document."""
    db = get_db()
    sql = "SELECT e.text, e.label, COUNT(ea.page_number) as count FROM entity_appearances ea JOIN entities e ON ea.entity_id = e.id WHERE ea.doc_id = ?"
    params = [doc_id]
    if entity_label: sql += " AND e.label = ?"; params.append(entity_label.upper())
    sql += " GROUP BY e.text, e.label ORDER BY count DESC LIMIT ?"
    params.append(limit)
    results = db.execute(sql, params).fetchall()
    if not results: return "No entities found."
    return "\n".join([f"- {r['text']} ({r['label']}): {r['count']} mentions" for r in results])

def find_documents(query: Union[str, int]) -> str:
    """Finds documents by title/path match or ID."""
    db = get_db()
    if isinstance(query, int) or (isinstance(query, str) and query.isdigit()):
        doc = db.execute("SELECT id, relative_path FROM documents WHERE id = ?", (int(query),)).fetchone()
        if doc: return f"Found: Doc #{doc['id']} - {doc['relative_path']}"
    results = db.execute("SELECT id, relative_path FROM documents WHERE relative_path LIKE ? LIMIT 5", (f"%{query}%",)).fetchall()
    if not results: return "No documents found."
    return "\n".join([f"Doc #{r['id']}: {r['relative_path']}" for r in results])

# === NEW TOOL: Brute Force Summary ===
def summarize_collection(query: str, limit: int = 5) -> str:
    """
    BRUTE FORCE: Finds the top N documents matching a query and summarizes them.
    Useful when you need an overview of a specific topic area rather than finding specific facts.
    """
    db = get_db()
    
    # Get top N docs (using FTS helper from assistant_core)
    limit = min(limit, 10) # Hard cap to prevent context overflow
    results = _internal_fts_search(db, query, limit=limit)
    
    if not results:
        return f"No documents found matching '{query}'."
    
    # Deduplicate docs (FTS returns pages, we just want unique docs)
    unique_doc_ids = list(set(r['doc_id'] for r in results))[:limit]
    
    print(f"{Style.CYAN}[Brute Force] Summarizing {len(unique_doc_ids)} documents for '{query}'...{Style.END}")
    
    master_summary = f"Collection Summary for query '{query}':\n\n"
    
    for doc_id in unique_doc_ids:
        # Get doc title/path
        doc_row = db.execute("SELECT relative_path FROM documents WHERE id = ?", (doc_id,)).fetchone()
        title = doc_row['relative_path'] if doc_row else f"Doc {doc_id}"
        
        # Read first 2 pages (Abstract/Intro) + any specific hits
        # read_specific_pages will automatically inject the Curator Hint if present
        content = read_specific_pages(db, [{'doc_id': doc_id, 'page_number': 1}, {'doc_id': doc_id, 'page_number': 2}])
        
        if content:
            # Quick summarization call (bypassing main agent loop for speed)
            prompt = f"Summarize this document in 3 sentences. Context: {content[:4000]}"
            try:
                response = ollama.chat(model=REASONING_MODEL, messages=[{'role': 'user', 'content': prompt}])
                summary = response['message']['content']
                master_summary += f"--- Document: {title} (ID: {doc_id}) ---\n{summary}\n\n"
            except:
                master_summary += f"--- Document: {title} ---\n(Error generating summary)\n\n"
    
    return master_summary

# === FINALIZED TOOLSET ===
AVAILABLE_TOOLS = { 
    # Used by Study Mode & Research Agent
    "research_across_all_documents": research_across_all_documents,
    "find_co_mentions": find_co_mentions,
    "read_entity_mentions": read_entity_mentions,
    "summarize_collection": summarize_collection, # <--- NEW TOOL
    
    # Interactive Mode Helpers
    "search_document_content": search_document_content,
    "find_entity": find_entity,
    "get_page_content": get_page_content_tool_wrapper, 
    "summarize_document": summarize_document,
    "find_most_mentioned_entities": find_most_mentioned_entities,
    "find_documents": find_documents,
}

def main():
    """Initializes and runs the Semantic Assistant."""
    parser = argparse.ArgumentParser(description="Redleaf Semantic Assistant")
    parser.add_argument('--model', type=str, help="Override the AI model for this session (e.g., 'llama3:8b')")
    
    # === NEW: Persona Argument ===
    parser.add_argument('--persona', type=str, default="Investigative Journalist", 
                        help="The personality of the agent (e.g., 'Skeptical Detective', 'Academic', 'Helpful Librarian')")
    
    # === NEW: Manual Context Override ===
    parser.add_argument('--context', type=str, help="Manually override the system briefing (e.g., 'This is a collection of 2016 emails.')")

    args = parser.parse_args()

    app = create_app(start_background_thread=False)
    with app.app_context():
        # 1. Determine Model
        if args.model:
            selected_model = args.model
            print(f"--- Reasoning Model (CLI Override): {selected_model} ---")
        else:
            settings = get_system_settings()
            selected_model = settings.get('reasoning_model', REASONING_MODEL)
            print(f"--- Reasoning Model (System Setting): {selected_model} ---")

        # 2. Confirm Embedding Model
        print(f"--- Embedding Model (Foundation): {EMBEDDING_MODEL} ---")

        # 3. Connectivity Check
        print(f"[INFO] Connecting to Ollama...")
        try:
            ollama.show(selected_model)
            print(f"[OK]   Reasoning Model '{selected_model}' is reachable.")
            ollama.show(EMBEDDING_MODEL)
            print(f"[OK]   Embedding Model '{EMBEDDING_MODEL}' is reachable.")
        except Exception as e:
            print(f"[FAIL] Could not connect to Ollama or a model was not found.")
            print(f"       Error: {e}")
            print("       Please ensure Ollama is running and both models are pulled.")
            sys.exit(1)

        # 4. Start the Assistant with Persona AND Manual Context
        assistant = BaseAssistant(
            reasoning_model=selected_model, 
            available_tools=AVAILABLE_TOOLS,
            router_prompt=ROUTER_PROMPT,
            search_strategy="hybrid",
            persona=args.persona, # Pass Persona
            manual_context=args.context # Pass Manual Context Override
        )
        assistant.run()

if __name__ == "__main__":
    main()