# --- File: assistant_cli.py (With Analytical 'find_most_mentioned' Tool) ---
import sys
import getpass
import re
import json
import ollama
from pathlib import Path
from typing import Dict, Any, Union, List, Tuple

# --- Add project to Python path to access its modules ---
project_dir = Path(__file__).resolve().parent
sys.path.append(str(project_dir))

# --- Import from your existing Redleaf project ---
from project import create_app
from project.database import get_db
from werkzeug.security import check_password_hash
from processing_pipeline import extract_text_for_copying
from project.config import DOCUMENTS_DIR

# --- Configuration ---
MODEL_NAME = "gemma3:12b"
MAX_REASONING_TURNS = 5
MAX_CONTEXT_CHARS = 12000

# --- Style class ---
class Style:
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    END = '\033[0m'

# --- AI Tools ---
def find_documents(query: Union[str, int]) -> str:
    """Finds documents by either their ID number or a query string matching their filename."""
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
    """
    Searches for a specific term within a document's content using a more forgiving OR search.
    Can exclude pages that have already been seen.
    """
    db = get_db()
    doc = db.execute("SELECT relative_path, file_type FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc: return f"Error: No document found with ID {doc_id}."

    terms_to_search = {term for term in search_term.split()}
    terms_to_search.update({term.upper() for term in terms_to_search})
    
    escaped_terms = [term.replace('"', '""') for term in terms_to_search]
    fts_query = " OR ".join([f'"{term}"' for term in escaped_terms])
    
    sql = "SELECT DISTINCT page_number FROM content_index WHERE doc_id = ? AND content_index MATCH ? "
    params = [doc_id, fts_query]
    
    if exclude_pages:
        placeholders = ', '.join('?' for _ in exclude_pages)
        sql += f"AND page_number NOT IN ({placeholders}) "
        params.extend(exclude_pages)
        
    sql += "ORDER BY page_number ASC"
    results = db.execute(sql, params).fetchall()
    
    if not results:
        if exclude_pages:
            return f"No *other* pages mentioning '{search_term}' found in document ID {doc_id}."
        return f"No pages mentioning '{search_term}' found in document ID {doc_id}."

    context_block = ""
    for row in results:
        page_num = row['page_number']
        page_text = extract_text_for_copying(DOCUMENTS_DIR / doc['relative_path'], doc['file_type'], start_page=page_num, end_page=page_num)
        page_entry = f"--- CONTEXT from Page {page_num} ---\n{page_text}\n\n"
        if len(context_block) + len(page_entry) > MAX_CONTEXT_CHARS:
            context_block += "[INFO] Context limit reached. Some matching pages were omitted.\n"
            break
        context_block += page_entry
    return context_block

def get_page_content(doc_id: int, page_number: int) -> str:
    """
    Retrieves the full text content of a specific page number from a document.
    """
    db = get_db()
    doc = db.execute("SELECT relative_path, file_type, page_count FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc: return f"Error: No document found with ID {doc_id}."
    
    if doc['page_count'] and (page_number < 1 or page_number > doc['page_count']):
        return f"Error: Invalid page number. Document {doc_id} has {doc['page_count']} pages."

    page_text = extract_text_for_copying(DOCUMENTS_DIR / doc['relative_path'], doc['file_type'], start_page=page_number, end_page=page_number)
    
    if not page_text.strip():
        return f"Page {page_number} was found but contains no text."
        
    return f"--- CONTEXT from Page {page_number} ---\n{page_text}\n\n"

# --- NEW TOOL START ---
def find_most_mentioned_entities(doc_id: int, entity_label: str = None, limit: int = 5) -> str:
    """
    Finds the most frequently mentioned entities (e.g., PERSON, ORG) in a document by analyzing the entity index.
    """
    db = get_db()
    sql = """
        SELECT e.text, e.label, COUNT(ea.entity_id) as mention_count
        FROM entity_appearances ea
        JOIN entities e ON ea.entity_id = e.id
        WHERE ea.doc_id = ?
    """
    params = [doc_id]
    
    if entity_label:
        # A list of common, valid spaCy labels to be safe.
        valid_labels = ['PERSON', 'GPE', 'LOC', 'ORG', 'DATE', 'EVENT', 'PRODUCT', 'WORK_OF_ART', 'LAW', 'LANGUAGE', 'NORP', 'FACILITY', 'MONEY', 'QUANTITY', 'ORDINAL', 'CARDINAL']
        if entity_label.upper() in valid_labels:
            sql += " AND e.label = ?"
            params.append(entity_label.upper())
        else:
            return f"Error: Invalid entity label '{entity_label}'. Please use a standard label like 'PERSON' or 'ORG'."

    sql += " GROUP BY ea.entity_id ORDER BY mention_count DESC LIMIT ?"
    params.append(limit)

    results = db.execute(sql, params).fetchall()

    if not results:
        return f"No entities of type '{entity_label}' found in document ID {doc_id}." if entity_label else f"No entities found in document ID {doc_id}."

    output = [f"- {row['text']} ({row['label']}): {row['mention_count']} mentions" for row in results]
    return "Top mentioned entities:\n" + "\n".join(output)
# --- NEW TOOL END ---

def summarize_document(doc_id: int, topic: str = None) -> str:
    """
    Retrieves relevant parts of a document for a summary.
    If a topic is provided, it finds the most relevant pages for that topic.
    Otherwise, it samples pages from the start and end of the document.
    """
    db = get_db()
    doc = db.execute("SELECT relative_path, file_type, page_count FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc: return f"Error: No document found with ID {doc_id}."

    page_numbers_to_fetch = set()

    if topic:
        print(f"{Style.CYAN}[Memory] Summarizing based on topic: '{topic}'{Style.END}")
        terms_to_search = {term for term in topic.split()}
        terms_to_search.update({term.upper() for term in terms_to_search})
        escaped_terms = [term.replace('"', '""') for term in terms_to_search]
        fts_query = " OR ".join([f'"{term}"' for term in escaped_terms])
        
        relevant_pages = db.execute(
            "SELECT page_number FROM content_index WHERE doc_id = ? AND content_index MATCH ? ORDER BY rank LIMIT 5",
            (doc_id, fts_query)
        ).fetchall()
        page_numbers_to_fetch.update([row['page_number'] for row in relevant_pages])

    if not page_numbers_to_fetch:
        print(f"{Style.CYAN}[Memory] No topic provided or found, using default summary.{Style.END}")
        sample_pages = db.execute("SELECT page_number FROM content_index WHERE doc_id = ? ORDER BY page_number ASC LIMIT 3", (doc_id,)).fetchall()
        page_numbers_to_fetch.update([row['page_number'] for row in sample_pages])
        if doc['page_count'] and doc['page_count'] > 5:
            sample_pages = db.execute("SELECT page_number FROM content_index WHERE doc_id = ? ORDER BY page_number DESC LIMIT 2", (doc_id,)).fetchall()
            page_numbers_to_fetch.update([row['page_number'] for row in sample_pages])
    
    if not page_numbers_to_fetch: return "This document has no indexed content to summarize."

    context_block = ""
    for page_num in sorted(list(page_numbers_to_fetch)):
        page_text = extract_text_for_copying(DOCUMENTS_DIR / doc['relative_path'], doc['file_type'], start_page=page_num, end_page=page_num)
        page_entry = f"--- CONTEXT from Page {page_num} ---\n{page_text}\n\n"
        if len(context_block) + len(page_entry) > MAX_CONTEXT_CHARS: break
        context_block += page_entry
    return context_block

AVAILABLE_TOOLS = {
    "find_documents": find_documents,
    "search_document_content": search_document_content,
    "get_page_content": get_page_content,
    "find_most_mentioned_entities": find_most_mentioned_entities,
    "summarize_document": summarize_document,
}

# --- System Prompts ---
ROUTER_PROMPT = """
You are a tool-routing assistant. Your only job is to analyze the user's request and choose which single tool to use.
Respond with ONLY a single JSON object for the tool call.

IMPORTANT: Use the user's keywords and acronyms EXACTLY as they provide them. DO NOT expand acronyms (e.g., if the user says "OUN", search for "OUN", not "Organization of Ukrainian Nationalists").

## User's Goal:
- To **find or load** a document, use `find_documents`.
- For a **general summary** or a **general question about the whole document** (like "summarize this"), use `summarize_document`. If there is a current topic of conversation, pass it to the `topic` argument.
- To **get, read, or summarize a specific page number** (like 'what does page 17 say?'), use `get_page_content`.
- **ANALYTICAL RULE**: To get a **list of the most frequent entities by name and count** (e.g., 'who are the most mentioned people?'), use `find_most_mentioned_entities`. If the user specifies a type like 'person' or 'organization', pass it as the `entity_label`.
- **INFORMATION RULE**: For a **specific question about a topic** (like "what about OUN?" or "tell me about the person most mentioned"), you MUST use `search_document_content`. This is true even if the user asks about the 'most mentioned' item and does not provide its name.
- If the user asks for **"more"** or **"other"** pages on a topic, use `search_document_content` and pass the `exclude_pages` argument.

## Active Document Context:
The user is working with Document ID {doc_id}: '{doc_path}'.
Pages already discussed in this conversation: {seen_pages}
Current topic of conversation: '{current_topic}'

## Available Tools:
- `find_documents(query: str | int)`
- `summarize_document(doc_id: int, topic: str = None)`
- `get_page_content(doc_id: int, page_number: int)`
- `find_most_mentioned_entities(doc_id: int, entity_label: str = None, limit: int = 5)`
- `search_document_content(doc_id: int, search_term: str, exclude_pages: list[int] = None)`
"""

WRITER_PROMPT = """
You are a factual reporting agent. Your sole purpose is to synthesize an answer to the user's question using ONLY the provided "Tool Output / Context".

Follow these steps precisely:
1.  **Analyze Context:** Read the user's question and the provided context.
2.  **Synthesize Answer:** Write a concise answer that directly addresses the user's question.
    - You MUST base your answer *exclusively* on the text in the context block.
    - At the end of every sentence that uses information from the context, you MUST add a citation like [p. XX].
    - It is critical that you DO NOT add any information that is not present in the context. Do not use your own knowledge.
3.  **Handle Missing Information:** If the context block is empty or does not contain information relevant to the user's question, your ONLY response MUST be: "The provided text excerpts do not contain information to answer that question."
"""

# --- Main Script Logic ---

def get_assistant_response(messages: List[Dict], use_json: bool = False) -> str:
    try:
        response = ollama.chat(model=MODEL_NAME, messages=messages, format="json" if use_json else "")
        return response['message']['content']
    except Exception as e: return f"Error contacting Ollama: {e}"

def print_help():
    print(f"\n{Style.BOLD}Redleaf AI Assistant Commands:{Style.END}\n  {Style.GREEN}/help{Style.END}      - Show this help message.\n  {Style.GREEN}/clear{Style.END}      - Unload the current document.\n  {Style.GREEN}/exit{Style.END}       - Exit the assistant.\n  \nYou can also use natural language to find and load documents:\n  e.g., `find book about war`\n  e.g., `load doc 1`\n    ")

def handle_local_command(db, user_input: str) -> Tuple[bool, Union[Dict, None]]:
    find_match = re.search(r'^(?:find|search for)\s+(.+)', user_input, re.IGNORECASE)
    load_match = re.search(r'^(?:load|open|get)\s+(?:doc|document)?\s*(?:#|id)?\s*(\S+)', user_input, re.IGNORECASE)
    if find_match:
        query = find_match.group(1).strip()
        results = db.execute("SELECT id, relative_path FROM documents WHERE relative_path LIKE ?", (f"%{query}%",)).fetchall()
        if not results: print(f"{Style.YELLOW}[INFO] No documents found matching '{query}'.{Style.END}")
        else:
            print(f"{Style.GREEN}[INFO] Found {len(results)} matching document(s):{Style.END}")
            for doc in results: print(f"  {Style.BOLD}#{doc['id']}:{Style.END} {doc['relative_path']}")
        return True, None
    if load_match:
        identifier = load_match.group(1).strip()
        doc_to_load = None
        try:
            doc_id = int(identifier)
            doc_to_load = db.execute("SELECT id, relative_path, file_type, page_count FROM documents WHERE id = ?", (doc_id,)).fetchone()
        except (ValueError, TypeError):
            doc_to_load = db.execute("SELECT id, relative_path, file_type, page_count FROM documents WHERE relative_path LIKE ?", (f"%{identifier}%",)).fetchone()
        if not doc_to_load:
            print(f"{Style.RED}[FAIL] Document matching '{identifier}' not found.{Style.END}")
            return True, None
        active_doc = dict(doc_to_load)
        print(f"{Style.GREEN}[OK] Loaded Document ID {active_doc['id']}: '{active_doc['relative_path']}'{Style.END}")
        print(f"{Style.GREEN}     What would you like to know about it?{Style.END}")
        return True, active_doc
    return False, None

def main():
    app = create_app(start_background_thread=False)
    with app.app_context():
        db = get_db()
        print(f"{Style.BOLD}--- Redleaf AI Assistant Login ---{Style.END}")
        while True:
            username = input("Enter your username: ")
            if not username: continue
            password = getpass.getpass("Enter your password: ")
            user = db.execute("SELECT id, username, password_hash FROM users WHERE username = ?", (username,)).fetchone()
            if user and check_password_hash(user['password_hash'], password):
                print(f"\n{Style.GREEN}Welcome, {user['username']}!{Style.END} Model: {Style.BLUE}{MODEL_NAME}{Style.END}")
                print_help()
                break
            else:
                print(f"{Style.RED}[FAIL] Invalid username or password. Please try again.{Style.END}\n")
        
        active_doc = None
        conversation_history = []
        seen_pages_for_doc = set()
        last_search_term = ""
        
        while True:
            doc_prompt = f"#{active_doc['id']}" if active_doc else "No Doc"
            try: user_input = input(f"{Style.BOLD}({doc_prompt}){Style.END} > ").strip()
            except (KeyboardInterrupt, EOFError): print("\nGoodbye!"); break
            if not user_input: continue

            if user_input.startswith('/'):
                command = user_input.split(maxsplit=1)[0].lower()
                if command == '/exit': print("Goodbye!"); break
                elif command == '/help': print_help()
                elif command == '/clear':
                    active_doc = None
                    conversation_history = []
                    seen_pages_for_doc = set()
                    last_search_term = ""
                    print("[OK] Document context cleared.")
                else: print(f"{Style.RED}Unknown command: {command}.{Style.END}")
                continue

            was_handled, new_doc = handle_local_command(db, user_input)
            if was_handled:
                if new_doc:
                    active_doc = new_doc
                    conversation_history = []
                    seen_pages_for_doc = set()
                    last_search_term = ""
                continue

            if not active_doc:
                print(f"{Style.YELLOW}Please load a document first (e.g., `load doc 1`).{Style.END}")
                continue

            print(f"{Style.CYAN}[Thinking... Deciding which tool to use]{Style.END}")
            router_system_prompt = ROUTER_PROMPT.format(
                doc_id=active_doc['id'], 
                doc_path=active_doc['relative_path'],
                seen_pages=sorted(list(seen_pages_for_doc)) if seen_pages_for_doc else "[]",
                current_topic=last_search_term
            )
            
            router_messages = [
                {"role": "system", "content": router_system_prompt},
                *conversation_history,
                {"role": "user", "content": user_input}
            ]
            
            tool_choice_response = get_assistant_response(router_messages, use_json=True)
            
            try:
                tool_call_data = json.loads(tool_choice_response)
                
                if isinstance(tool_call_data, dict) and "tool_call" in tool_call_data and isinstance(tool_call_data["tool_call"], dict):
                    print(f"{Style.YELLOW}[INFO] Unwrapping nested tool call.{Style.END}")
                    tool_call_data = tool_call_data["tool_call"]

                tool_name, tool_args = None, None
                if isinstance(tool_call_data, dict):
                    for key in ["tool_name", "tool", "function_name", "name"]:
                        if key in tool_call_data:
                            tool_name = tool_call_data.pop(key)
                            break
                    if "arguments" in tool_call_data and isinstance(tool_call_data["arguments"], dict):
                        tool_args = tool_call_data["arguments"]
                    else:
                        tool_args = tool_call_data

                if tool_name not in AVAILABLE_TOOLS or not isinstance(tool_args, dict):
                    raise ValueError("Parsed JSON is not a valid tool call.")
                
                # --- FIX FOR UnboundLocalError ---
                # Execute the tool call and store the output immediately.
                print(f"{Style.YELLOW}[Using Tool] {tool_name}({', '.join(f'{k}={v}' for k, v in tool_args.items())}){Style.END}")
                tool_output_raw = AVAILABLE_TOOLS[tool_name](**tool_args)
                print(f"{Style.MAGENTA}[Tool Output Received]{Style.END}")
                
                # --- APPLY LOGIC AND UPDATE STATE ---
                if tool_name == "search_document_content":
                    tool_args['exclude_pages'] = list(seen_pages_for_doc)
                    if tool_args.get('search_term'):
                        last_search_term = tool_args.get('search_term')

                elif tool_name == "find_most_mentioned_entities":
                    # Regex to capture the entity name from the tool output: "- Name (LABEL): 123 mentions"
                    top_entity_match = re.search(r"^- (.+?) \([A-Z]+\):", tool_output_raw, re.MULTILINE)
                    if top_entity_match:
                        new_topic = top_entity_match.group(1).strip()
                        # Avoid setting a generic failure message as the topic
                        if new_topic and not new_topic.lower().startswith('no entities found'):
                             last_search_term = new_topic
                             print(f"{Style.CYAN}[Memory] New topic set from entity tool: '{last_search_term}'{Style.END}")
                    else:
                        last_search_term = "" # Clear if analysis failed

                if tool_name in ["search_document_content", "get_page_content"] and tool_output_raw:
                    found_pages = re.findall(r"--- CONTEXT from Page (\d+) ---", tool_output_raw)
                    if found_pages:
                        newly_seen_pages = {int(p) for p in found_pages}
                        seen_pages_for_doc.update(newly_seen_pages)
                        print(f"{Style.CYAN}[Memory] Now seen pages: {sorted(list(seen_pages_for_doc))}{Style.END}")
                
                # --- GENERATE FINAL ANSWER ---
                print(f"{Style.CYAN}[Thinking... Generating final answer]{Style.END}")
                writer_messages = [
                    {"role": "system", "content": WRITER_PROMPT},
                    {"role": "user", "content": f"My question was: '{user_input}'"},
                    {"role": "assistant", "content": f"Here is the context I found:\n\n{tool_output_raw}"} # Use the raw output here
                ]
                
                final_answer = get_assistant_response(writer_messages)
                print(f"\n{Style.GREEN}{final_answer}{Style.END}\n")
                
                conversation_history.append({"role": "user", "content": user_input})
                conversation_history.append({"role": "assistant", "content": final_answer})

            except (json.JSONDecodeError, TypeError, ValueError, KeyError) as e:
                print(f"{Style.RED}[Error] The AI did not choose a valid tool or an error occurred. Raw response:\n{tool_choice_response}{Style.END}")

if __name__ == "__main__":
    main()