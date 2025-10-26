# --- File: assistant_cli.py (With Bibliography URL Feature) ---
import sys
import getpass
import re
import json
import ollama
from pathlib import Path
from typing import Dict, Any, Union, List, Tuple
from collections import defaultdict

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
MAX_GLOBAL_SEARCH_RESULTS = 10
# --- START OF MODIFICATION 1 ---
# Base URL for your Redleaf instance. Change if you run it on a different host or port.
REDLEAF_BASE_URL = "http://127.0.0.1:5000"
# --- END OF MODIFICATION 1 ---

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

# --- AI Tools & Helpers ---
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
    doc = db.execute("SELECT relative_path, file_type FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc: return f"Error: No document found with ID {doc_id}."
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
        page_text = extract_text_for_copying(DOCUMENTS_DIR / doc['relative_path'], doc['file_type'], start_page=page_num, end_page=page_num)
        page_entry = f"--- CONTEXT from Page {page_num} ---\n{page_text}\n\n"
        if len(context_block) + len(page_entry) > MAX_CONTEXT_CHARS:
            context_block += "[INFO] Context limit reached.\n"; break
        context_block += page_entry
    return context_block
def get_page_content(doc_id: int, page_number: int) -> str:
    db = get_db()
    doc = db.execute("SELECT relative_path, file_type, page_count FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc: return f"Error: No document found with ID {doc_id}."
    if doc['page_count'] and (page_number < 1 or page_number > doc['page_count']): return f"Error: Invalid page number."
    page_text = extract_text_for_copying(DOCUMENTS_DIR / doc['relative_path'], doc['file_type'], start_page=page_number, end_page=page_number)
    if not page_text.strip(): return f"Page {page_number} was found but contains no text."
    return f"--- CONTEXT from Page {page_number} ---\n{page_text}\n\n"
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
    doc = db.execute("SELECT relative_path, file_type, page_count FROM documents WHERE id = ?", (doc_id,)).fetchone()
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
        text = extract_text_for_copying(DOCUMENTS_DIR/doc['relative_path'], doc['file_type'], start_page=num, end_page=num)
        entry = f"--- CONTEXT from Page {num} ---\n{text}\n\n"
        if len(context) + len(entry) > MAX_CONTEXT_CHARS: break
        context += entry
    return context
def _internal_cross_doc_search(query: str, limit: int = MAX_GLOBAL_SEARCH_RESULTS) -> List[Dict]:
    db = get_db()
    sanitized = query.replace('"', '""'); fts_query = f'"{sanitized}"'
    sql = "SELECT ci.doc_id, ci.page_number FROM content_index ci WHERE ci.content_index MATCH ? ORDER BY rank LIMIT ?"
    return [dict(r) for r in db.execute(sql, [fts_query, limit]).fetchall()]

# --- START OF MODIFICATION 2 ---
def read_specific_pages(sources: List[Dict[str, int]]) -> str:
    db = get_db()
    if not sources: return "No sources were provided to read."
    doc_ids = list(set(s['doc_id'] for s in sources))
    placeholders = ','.join('?' for _ in doc_ids)
    doc_map = {r['id']: dict(r) for r in db.execute(f"SELECT id, relative_path, file_type FROM documents WHERE id IN ({placeholders})", doc_ids)}
    context = ""
    for src in sources:
        info = doc_map.get(src['doc_id'])
        if not info: context += f"[ERROR] Could not find doc ID {src['doc_id']}.\n\n"; continue
        text = extract_text_for_copying(DOCUMENTS_DIR/info['relative_path'], info['file_type'], start_page=src['page_number'], end_page=src['page_number'])
        doc_url = f"{REDLEAF_BASE_URL}/document/{src['doc_id']}"
        entry = f"--- CONTEXT from Document #{src['doc_id']} ({info['relative_path']}) URL: {doc_url}, Page {src['page_number']} ---\n{text}\n\n"
        if len(context) + len(entry) > MAX_CONTEXT_CHARS: context += "[INFO] Context limit reached.\n"; break
        context += entry
    return context
# --- END OF MODIFICATION 2 ---

AVAILABLE_TOOLS = { "find_documents": find_documents, "search_document_content": search_document_content, "get_page_content": get_page_content, "find_most_mentioned_entities": find_most_mentioned_entities, "summarize_document": summarize_document, "read_specific_pages": read_specific_pages, }
ROUTER_PROMPT = """You are a tool-routing assistant. Your only job is to analyze a user's request and choose a single tool to use from the list below. The user may use a special `search:` command which is handled by the system; you will only be activated for other questions. Respond with ONLY a single JSON object for the tool call.
## Tool Selection Rules (Requires an Active Document):
- To **find or list** documents, use `find_documents`.
- For a **general summary** of the *loaded document*, use `summarize_document`.
- To **read a specific page number**, use `get_page_content`.
- To get a **list of frequent entities**, use `find_most_mentioned_entities`.
- For a **specific question about a topic within the active document**, use `search_document_content`.
- To get **"more"** pages on a topic, use `search_document_content` and `exclude_pages`.
## Active Document Context:
The user is working with Document ID {doc_id}: '{doc_path}'. Pages already seen: {seen_pages}.
## Available Tools:
- `find_documents(query: str | int)`
- `summarize_document(doc_id: int, topic: str = None)`
- `get_page_content(doc_id: int, page_number: int)`
- `find_most_mentioned_entities(doc_id: int, entity_label: str = None, limit: int = 5)`
- `search_document_content(doc_id: int, search_term: str, exclude_pages: list[int] = None)`
- `read_specific_pages(sources: list[dict])`"""

# --- START OF MODIFICATION 3 ---
WRITER_PROMPT = """You are a factual reporting agent. Your purpose is to synthesize a helpful answer to the user's instruction using ONLY the provided "Tool Output / Context".

Your response MUST follow this two-part structure precisely:
1.  **Summary:** Write a concise, high-level abstract summary that directly addresses the user's instruction.
    - Base your summary *exclusively* on the text in the context block.
    - **Inline Citations:**
        - If context is from `--- CONTEXT from Page XX ---`, cite with `[p. XX]`.
        - If context is from `--- CONTEXT from Document #XX (...), Page YY ---`, cite with the short format `[Doc. XX/p. YY]`.
2.  **Bibliography:** After the summary, add a separator (`---`), a heading `Sources Cited:`, and then list every unique document you cited.
    - For each document, provide its ID, full filename, and the URL provided in the context.
    - Format: `[Doc. XX]: filename.pdf (URL)`

**Example Response Structure:**
This is a summary of the findings based on the provided text [Doc. 12/p. 1]. The text also mentions another point here [Doc. 34/p. 5].

---
Sources Cited:
[Doc. 12]: path/to/first_document.pdf (http://127.0.0.1:5000/document/12)
[Doc. 34]: another/path/report.txt (http://127.0.0.1:5000/document/34)

**CRITICAL:** Do NOT add any information not present in the context. If the context is empty or irrelevant, your ONLY response is: "The provided text excerpts do not contain information to answer that question."
"""
# --- END OF MODIFICATION 3 ---

def get_assistant_response(messages: List[Dict], use_json: bool = False) -> str:
    try:
        response = ollama.chat(model=MODEL_NAME, messages=messages, format="json" if use_json else "")
        return response['message']['content']
    except Exception as e: return f"Error contacting Ollama: {e}"

def print_help():
    print(f"\n{Style.BOLD}Redleaf AI Assistant Commands:{Style.END}")
    print(f"  {Style.GREEN}find [query]{Style.END}            - Find documents by path without loading them.")
    print(f"  {Style.GREEN}load [doc_id|path]{Style.END}   - Load a specific document for focused questions.")
    print(f"  {Style.GREEN}search: [query]{Style.END} - Search all docs and summarize top results.")
    print(f"  {Style.GREEN}search: [query] + [instruction]{Style.END} - Search and follow a custom instruction.")
    print(f"    ↳ e.g., `search: Sam Smith + list all key figures mentioned`")
    print(f"  {Style.GREEN}search: [query] + for each + [instruction]{Style.END} - Run instruction on each found doc.")
    print(f"    ↳ e.g., `search: meeting minutes + for each + summarize the key decisions`")
    print(f"  {Style.GREEN}/help{Style.END}               - Show this help message.")
    print(f"  {Style.GREEN}/clear{Style.END}              - Unload doc and return to the global 'All Docs' view.")
    print(f"  {Style.GREEN}/exit{Style.END}               - Exit the assistant.")

def handle_local_command(db, user_input: str) -> Tuple[bool, Union[Dict, None], Tuple[str, str]]:
    search_match = re.search(r'^search:\s*(.+)', user_input, re.IGNORECASE)
    find_match = re.search(r'^(?:find|search for)\s+(.+)', user_input, re.IGNORECASE)
    load_match = re.search(r'^(?:load|open|get)\s+(?:doc|document)?\s*(?:#|id)?\s*(\S+)', user_input, re.IGNORECASE)

    if search_match:
        full_query = search_match.group(1).strip()
        parts = [p.strip() for p in full_query.split(' + ')]
        search_query = parts[0]
        
        print(f"{Style.CYAN}[INFO] Searching all docs for: '{search_query}'{Style.END}")
        sources = _internal_cross_doc_search(search_query)
        if not sources:
            print(f"{Style.YELLOW}[INFO] No documents mentioned '{search_query}'.{Style.END}")
            return True, None, ("", "")

        combined_context = read_specific_pages(sources=sources)
        
        if len(parts) == 3 and parts[1].lower() == 'for each':
            _, _, for_each_instruction = parts
            
            grouped_sources = defaultdict(list)
            for source in sources: grouped_sources[source['doc_id']].append(source['page_number'])

            print(f"{Style.CYAN}[INFO] Found results in {len(grouped_sources)} documents. Processing each...{Style.END}")
            for doc_id, page_numbers in grouped_sources.items():
                doc_info = db.execute("SELECT relative_path FROM documents WHERE id = ?", (doc_id,)).fetchone()
                print(f"\n{Style.BOLD}--- Analyzing Document #{doc_id} ({doc_info['relative_path']}) ---{Style.END}")
                loop_context = read_specific_pages(sources=[{'doc_id': doc_id, 'page_number': p} for p in page_numbers])
                
                print(f"{Style.CYAN}[Thinking... Fulfilling: '{for_each_instruction}']{Style.END}")
                messages = [{"role": "system", "content": WRITER_PROMPT}, {"role": "user", "content": for_each_instruction}, {"role": "assistant", "content": f"Context:\n\n{loop_context}"}]
                final_answer = get_assistant_response(messages)
                print(f"\n{Style.GREEN}{final_answer}{Style.END}")

            print(f"\n{Style.BOLD}--- Batch processing complete. ---{Style.END}\n")
            
            print(f"{Style.MAGENTA}{Style.BOLD}[Session Active] The combined content from all {len(grouped_sources)} documents is now in memory.{Style.END}")
            print(f"{Style.MAGENTA}You can now ask follow-up questions about the entire search result set.{Style.END}")
            print(f"{Style.MAGENTA}e.g., 'Compare the findings in Doc X and Doc Y' or 'which document mentioned financials?'.{Style.END}\n")

            return True, None, (search_query, combined_context)
        
        else:
            writer_instruction = parts[1] if len(parts) > 1 else f"Give a high-level abstract summary about '{search_query}'."
            
            print(f"{Style.CYAN}[Thinking... Fulfilling: '{writer_instruction}']{Style.END}")
            messages = [{"role": "system", "content": WRITER_PROMPT}, {"role": "user", "content": writer_instruction}, {"role": "assistant", "content": f"Context:\n\n{combined_context}"}]
            final_answer = get_assistant_response(messages)
            print(f"\n{Style.GREEN}{final_answer}{Style.END}\n")
            return True, None, (search_query, combined_context)

    if find_match:
        query = find_match.group(1).strip()
        results = db.execute("SELECT id, relative_path FROM documents WHERE relative_path LIKE ?", (f"%{query}%",)).fetchall()
        if not results: print(f"{Style.YELLOW}[INFO] No documents found matching '{query}'.{Style.END}")
        else: print(f"{Style.GREEN}[INFO] Found {len(results)} doc(s):{Style.END}"); [print(f"  {Style.BOLD}#{doc['id']}:{Style.END} {doc['relative_path']}") for doc in results]
        return True, None, ("", "")
    if load_match:
        identifier = load_match.group(1).strip()
        doc_to_load = None
        try: doc_id = int(identifier); doc_to_load = db.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        except (ValueError, TypeError): doc_to_load = db.execute("SELECT * FROM documents WHERE relative_path LIKE ?", (f"%{identifier}%",)).fetchone()
        if not doc_to_load: print(f"{Style.RED}[FAIL] Document '{identifier}' not found.{Style.END}"); return True, None, ("", "")
        active_doc = dict(doc_to_load)
        print(f"{Style.GREEN}[OK] Loaded Doc ID {active_doc['id']}: '{active_doc['relative_path']}'{Style.END}")
        return True, active_doc, ("", "")
    return False, None, ("", "")

def main():
    app = create_app(start_background_thread=False)
    with app.app_context():
        db = get_db()
        print(f"{Style.BOLD}--- Redleaf AI Assistant Login ---{Style.END}")
        while True:
            username = input("Enter your username: ");
            if not username: continue
            password = getpass.getpass("Enter your password: ")
            user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if user and check_password_hash(user['password_hash'], password):
                print(f"\n{Style.GREEN}Welcome, {user['username']}!{Style.END} Model: {Style.BLUE}{MODEL_NAME}{Style.END}"); print_help(); break
            else: print(f"{Style.RED}[FAIL] Invalid credentials.{Style.END}\n")
        
        active_doc = None; conversation_history = []; seen_pages_for_doc = set()
        last_global_context = ""; last_global_query = ""
        TOOLS_REQUIRING_DOC = ["search_document_content", "get_page_content", "find_most_mentioned_entities", "summarize_document"]

        while True:
            if active_doc: doc_prompt = f"#{active_doc['id']}"
            else: doc_prompt = "All Docs"
                
            try: user_input = input(f"{Style.BOLD}({doc_prompt}){Style.END} > ").strip()
            except (KeyboardInterrupt, EOFError): print("\nGoodbye!"); break
            if not user_input: continue

            if user_input.startswith('/'):
                command = user_input.split(maxsplit=1)[0].lower()
                if command == '/exit': print("Goodbye!"); break
                elif command == '/help': print_help()
                elif command == '/clear':
                    active_doc=None; conversation_history=[]; seen_pages_for_doc=set()
                    last_global_context = ""; last_global_query = ""
                    print("[OK] Document and search context cleared.")
                else: print(f"{Style.RED}Unknown command: {command}.{Style.END}")
                continue
            
            was_handled, new_doc, search_info = handle_local_command(db, user_input)
            
            if search_info and search_info[0]:
                last_global_query, last_global_context = search_info
                if "for each" not in user_input:
                     print(f"{Style.CYAN}[Memory] Stored context from search for '{last_global_query}'. Ask a follow-up question directly.{Style.END}")
            elif was_handled:
                 last_global_context = ""; last_global_query = ""

            if was_handled:
                if new_doc:
                    active_doc = new_doc; conversation_history = []; seen_pages_for_doc = set()
                    last_global_context = ""; last_global_query = ""
                continue
            
            if not was_handled and not active_doc and last_global_context:
                print(f"{Style.CYAN}[INFO] Answering based on the context of the last search for '{last_global_query}'.{Style.END}")
                print(f"{Style.CYAN}[Thinking... Answering your follow-up question]{Style.END}")
                
                writer_messages = [
                    {"role": "system", "content": WRITER_PROMPT},
                    {"role": "user", "content": f"My original search was about '{last_global_query}'. Now, using the same provided text, answer this new question: '{user_input}'"},
                    {"role": "assistant", "content": f"Context:\n\n{last_global_context}"}
                ]
                final_answer = get_assistant_response(writer_messages)
                print(f"\n{Style.GREEN}{final_answer}{Style.END}\n")
                continue

            if not active_doc:
                print(f"{Style.YELLOW}Please load a document (e.g., `load doc 1`) or use `search:` command.{Style.END}"); continue

            print(f"{Style.CYAN}[Thinking... Deciding which tool to use]{Style.END}")
            router_system_prompt = ROUTER_PROMPT.format(doc_id=active_doc['id'], doc_path=active_doc['relative_path'], seen_pages=sorted(list(seen_pages_for_doc)))
            router_messages = [{"role": "system", "content": router_system_prompt}, *conversation_history, {"role": "user", "content": user_input}]
            tool_choice_response = get_assistant_response(router_messages, use_json=True)
            try:
                try:
                    parsed_choice = json.loads(tool_choice_response)
                    pretty_json = json.dumps(parsed_choice, indent=2)
                    print(f"{Style.CYAN}[Thought Process] The model chose:{Style.END}\n{pretty_json}")
                except json.JSONDecodeError:
                    print(f"{Style.YELLOW}[Thought Process] Model returned non-JSON:{Style.END}\n{tool_choice_response}")
                tool_call_data = json.loads(tool_choice_response)
                
                if isinstance(tool_call_data, dict) and "tool_call" in tool_call_data: tool_call_data = tool_call_data["tool_call"]
                tool_name, tool_args = None, None
                if isinstance(tool_call_data, dict):
                    for k in ["tool_name", "name", "tool"]:
                        if k in tool_call_data: tool_name = tool_call_data.pop(k); break
                    tool_args = tool_call_data.get("arguments", tool_call_data)
                if tool_name not in AVAILABLE_TOOLS or not isinstance(tool_args, dict): raise ValueError("Parsed JSON is not a valid tool call.")
                if tool_name in TOOLS_REQUIRING_DOC and not active_doc: print(f"{Style.YELLOW}Load a document to use '{tool_name}'.{Style.END}"); continue

                print(f"{Style.YELLOW}[Using Tool] {tool_name}({', '.join(f'{k}={v}' for k, v in tool_args.items())}){Style.END}")
                tool_output_raw = AVAILABLE_TOOLS[tool_name](**tool_args)
                print(f"{Style.MAGENTA}[Tool Output Received]{Style.END}")
                
                print(f"{Style.CYAN}[Thinking... Generating final answer]{Style.END}")
                final_context_for_writer = tool_output_raw
                if active_doc:
                    # --- START OF MODIFICATION 4 ---
                    doc_url = f"{REDLEAF_BASE_URL}/document/{active_doc['id']}"
                    doc_info_header = (
                        f"[Active Document Information]\n"
                        f"[Doc. {active_doc['id']}]: {active_doc['relative_path']}\n"
                        f"URL: {doc_url}\n"
                        f"---\n\n"
                    )
                    # --- END OF MODIFICATION 4 ---
                    final_context_for_writer = doc_info_header + tool_output_raw
                
                writer_messages = [
                    {"role": "system", "content": WRITER_PROMPT},
                    {"role": "user", "content": f"My question was: '{user_input}'"},
                    {"role": "assistant", "content": f"Context:\n\n{final_context_for_writer}"}
                ]
                final_answer = get_assistant_response(writer_messages)
                print(f"\n{Style.GREEN}{final_answer}{Style.END}\n")
                conversation_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": final_answer}])

            except (json.JSONDecodeError, TypeError, ValueError, KeyError) as e:
                print(f"{Style.RED}[Error] Could not execute AI action. Raw response printed above.{Style.END}\nDetails: {e}")

if __name__ == "__main__":
    main()
