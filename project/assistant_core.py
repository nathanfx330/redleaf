# --- File: ./project/assistant_core.py ---
import sys
import getpass
import re
import json
import ollama
import html
import heapq  # <-- NEW: Used for keeping top scores without hoarding RAM
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Union, List, Tuple, Set, Optional
from collections import defaultdict
import numpy as np

# --- Add project to Python path to access its modules ---
project_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(project_dir))

# --- Import from your existing Redleaf project ---
from project.database import get_db
from werkzeug.security import check_password_hash
from processing_pipeline import extract_text_for_copying
# FIXED: Importing resolve_document_path directly from config
from project.config import DOCUMENTS_DIR, REDLEAF_BASE_URL, EMBEDDING_MODEL, resolve_document_path

# --- Import prompts ---
from project.prompts import (
    WRITER_PROMPT, 
    CONVERSATION_PROMPT, 
    BATCH_CONVERSATION_PROMPT, 
    RESEARCHER_AGENT_PROMPT,
    ROUTER_PROMPT,
    RECURSIVE_STUDY_PROMPT,
    SELECTOR_PROMPT,
    NOTE_TAKER_PROMPT,
    STUDY_REPORT_PROMPT
)

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

# --- Core Search & Data Retrieval Functions ---
def _internal_fts_search(db, query: str, limit: int = 50) -> List[Dict]:
    # --- FIXED: Smart Multi-Term (A AND B) Search ---
    safe_query = re.sub(r'[^\w\s]', '', query).strip()
    words = [w for w in safe_query.split() if w.lower() != 'and']
    fts_query = " AND ".join([f'"{w}"' for w in words])
    
    if not fts_query:
        return []

    sql = """
        SELECT ci.doc_id, ci.page_number, snippet(content_index, 2, '<<<', '>>>', '...', 20) as snippet 
        FROM content_index ci 
        JOIN documents d ON ci.doc_id = d.id
        WHERE ci.content_index MATCH ? AND d.status != 'Missing'
        ORDER BY rank LIMIT ?
    """
    return [dict(r) for r in db.execute(sql, [fts_query, limit]).fetchall()]

def _internal_semantic_search(db, query: str, limit: int = 50) -> List[Dict]:
    # --- FIXED: RAM-Safe Vector Streaming ---
    try:
        query_embedding_response = ollama.embeddings(model=EMBEDDING_MODEL, prompt=query)
        query_vector = np.array(query_embedding_response['embedding'], dtype=np.float32)
        norm_query_vector = query_vector / np.linalg.norm(query_vector)
    except Exception as e:
        print(f"{Style.RED}[ERROR] Could not generate query embedding: {e}{Style.END}")
        return []

    top_k_heap = [] # Stores tuples of (score, doc_id, page_number, snippet)
    batch_size = 2000 # Stream 2000 rows at a time (Uses < 10MB of RAM)

    def process_batch(batch):
        if not batch: return
        
        # Extract embeddings and normalize them
        embeddings = np.array([np.frombuffer(row['embedding'], dtype=np.float32) for row in batch])
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms[norms == 0] = 1 # Prevent division by zero
        norm_embeddings = embeddings / norms
        
        # Vectorized dot product for blazingly fast similarity calculation
        similarities = np.dot(norm_embeddings, norm_query_vector)
        
        for i, score in enumerate(similarities):
            # Maintain a Min-Heap of the top N results
            if len(top_k_heap) < limit:
                heapq.heappush(top_k_heap, (score, batch[i]['doc_id'], batch[i]['page_number'], batch[i]['chunk_text']))
            else:
                if score > top_k_heap[0][0]:
                    heapq.heapreplace(top_k_heap, (score, batch[i]['doc_id'], batch[i]['page_number'], batch[i]['chunk_text']))

    # Stream from both embedding tables without exhausting memory
    for table in ["embedding_chunks", "super_embedding_chunks"]:
        try:
            cursor = db.execute(f"""
                SELECT e.doc_id, e.page_number, e.chunk_text, e.embedding 
                FROM {table} e
                JOIN documents d ON e.doc_id = d.id
                WHERE d.status != 'Missing'
            """)
            while True:
                batch = cursor.fetchmany(batch_size)
                if not batch: 
                    break
                process_batch(batch)
        except sqlite3.OperationalError:
            continue # Table might not exist yet if the pipeline hasn't finished

    # Sort the heap descending (highest similarity first)
    top_k_heap.sort(key=lambda x: x[0], reverse=True)
    
    top_results = []
    for score, doc_id, page_number, chunk_text in top_k_heap:
        top_results.append({
            'doc_id': doc_id, 
            'page_number': page_number,
            'snippet': chunk_text
        })
            
    return top_results

# === MODIFIED: Reads text AND prepends Curator Hints (Private Notes) and Metadata ===
def read_specific_pages(db, sources: List[Dict[str, int]], MAX_CONTEXT_CHARS=32000) -> str:
    if not sources: return "No sources were provided to read."
    doc_ids = list(set(s['doc_id'] for s in sources))
    if not doc_ids: return ""
    placeholders = ','.join('?' for _ in doc_ids)
    
    # --- MODIFIED: Added dm.csl_json to the query ---
    sql = f"""
        SELECT d.id, d.relative_path, d.file_type, dc.note, dm.csl_json
        FROM documents d
        LEFT JOIN document_curation dc ON d.id = dc.doc_id
        LEFT JOIN document_metadata dm ON d.id = dm.doc_id
        WHERE d.id IN ({placeholders})
    """
    
    doc_map = {r['id']: dict(r) for r in db.execute(sql, doc_ids)}
    
    context = ""
    for src in sources:
        info = doc_map.get(src['doc_id'])
        if not info: continue
        
        resolved_path = resolve_document_path(info['relative_path'])
        text = extract_text_for_copying(resolved_path, info['file_type'], start_page=src['page_number'], end_page=src['page_number'], doc_id=src['doc_id'])
        doc_url = f"{REDLEAF_BASE_URL}/document/{src['doc_id']}"
        
        # --- NEW: Parse CSL-JSON into a readable metadata string ---
        metadata_str = ""
        if info.get('csl_json'):
            try:
                csl = json.loads(info['csl_json'])
                title = csl.get('title', '')
                author = ""
                if csl.get('author') and csl['author'][0]:
                    author = csl['author'][0].get('literal') or csl['author'][0].get('family', '')
                year = ""
                if csl.get('issued', {}).get('date-parts'):
                    year = str(csl['issued']['date-parts'][0][0])
                
                parts = []
                if title: parts.append(f'"{title}"')
                if author: parts.append(f"by {author}")
                if year: parts.append(f"({year})")
                if parts: metadata_str = " | METADATA: " + " ".join(parts)
            except: pass
        
        curator_hint = ""
        if info.get('note') and info['note'].strip():
            curator_hint = f"\n[!!! CURATOR HINT / CONTEXT: {info['note']} !!!]\n"
        
        # --- MODIFIED: Inject metadata_str into the header ---
        entry = f"--- CONTEXT from Document #{src['doc_id']} ({info['relative_path']}){metadata_str} | URL: {doc_url}, Page {src['page_number']} ---{curator_hint}\n{text}\n\n"
        
        if len(context) + len(entry) > MAX_CONTEXT_CHARS: break
        context += entry
    return context

def get_page_content(db, doc_id: int, page_number: int) -> Tuple[str, Dict]:
    doc = db.execute("SELECT id, relative_path, file_type, page_count FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc: return f"Error: No document found with ID {doc_id}.", None
    if doc['page_count'] and (page_number < 1 or page_number > doc['page_count']): return f"Error: Invalid page number.", None
    
    # --- FIX: Resolve the path ---
    resolved_path = resolve_document_path(doc['relative_path'])
    
    # --- FIX: Pass doc_id to extractor ---
    page_text = extract_text_for_copying(resolved_path, doc['file_type'], start_page=page_number, end_page=page_number, doc_id=doc_id)
    
    if not page_text.strip(): return f"Page {page_number} was found but contains no text.", None
    return page_text, dict(doc)


class BaseAssistant:
    def __init__(self, reasoning_model, available_tools, router_prompt, search_strategy="hybrid", max_global_search_results=15, persona="Analytical Researcher", manual_context=None):
        self.reasoning_model = reasoning_model
        self.embedding_model = EMBEDDING_MODEL
        self.available_tools = available_tools
        self.router_prompt = router_prompt
        self.writer_prompt = WRITER_PROMPT
        self.conversation_prompt = CONVERSATION_PROMPT
        self.batch_conversation_prompt = BATCH_CONVERSATION_PROMPT
        self.researcher_prompt = RESEARCHER_AGENT_PROMPT
        self.search_strategy = search_strategy
        self.db = None
        self.user = None
        self.max_global_search_results = max_global_search_results
        self.for_each_regex = r'^for (?:each )?pages?(?:\s+in\s+\[?(\d+(?:-\d+)?)\]?)?(?:\s*\+\s*)?(.+)'
        
        # --- Persona & Context Configuration ---
        self.persona = persona
        self.manual_context = manual_context

    def _login(self):
        self.db = get_db()
        print(f"{Style.BOLD}--- Redleaf AI Assistant Login ---{Style.END}")
        while True:
            username = input("Enter your username: ")
            if not username: continue
            password = getpass.getpass("Enter your password: ")
            user = self.db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if user and check_password_hash(user['password_hash'], password):
                self.user = user; print(f"\n{Style.GREEN}Welcome, {self.user['username']}!{Style.END}"); self._print_help(); return True
            else:
                print(f"{Style.RED}[FAIL] Invalid credentials.{Style.END}\n")

    def _print_help(self):
        print(f"\n{Style.BOLD}Redleaf AI Assistant Commands:{Style.END}")
        print(f"  {Style.GREEN}study: [topic]{Style.END}      - Auto-Research: Recursively finds, reads, and synthesizes info.")
        print(f"  {Style.GREEN}find [query]{Style.END}       - Find documents by path without loading them.")
        print(f"  {Style.GREEN}load [doc_id|path]{Style.END}- Load a specific document for focused questions.")
        print(f"  {Style.GREEN}search: [q]{Style.END}      - Perform {self.search_strategy} search and summarize top results.")
        print(f"  {Style.GREEN}id:[id] + page:[p-p] + [instr]{Style.END} - Run instruction on a specific page range.")
        print(f"  {Style.GREEN}/print{Style.END}            - Export the current chat session to an HTML file.")
        print(f"  {Style.GREEN}/exit{Style.END}             - Exit the assistant.")
    
    def get_assistant_response(self, messages: List[Dict], use_json: bool = False) -> str:
        try:
            response = ollama.chat(model=self.reasoning_model, messages=messages, format="json" if use_json else "")
            return response['message']['content']
        except Exception as e:
            return f"Error contacting Ollama: {e}"

    def get_assistant_response_stream(self, messages: List[Dict]) -> str:
        full_response = ""
        try:
            stream = ollama.chat(model=self.reasoning_model, messages=messages, stream=True)
            for chunk in stream:
                token = chunk['message']['content']
                if token: print(token, end='', flush=True); full_response += token
        except Exception as e:
            error_message = f"Error contacting Ollama: {e}"; print(error_message); return error_message
        print()
        return full_response

    def _ansi_to_html(self, text: str) -> str:
        escaped_text = html.escape(text)
        replacements = { 
            Style.BLUE: '<span class="blue">', 
            Style.GREEN: '<span class="green">', 
            Style.YELLOW: '<span class="yellow">', 
            Style.RED: '<span class="red">', 
            Style.BOLD: '<span class="bold">', 
            Style.MAGENTA: '<span class="magenta">', 
            Style.CYAN: '<span class="cyan">', 
            Style.END: '</span>', 
        }
        for ansi, html_tag in replacements.items():
            escaped_text = escaped_text.replace(html.escape(ansi), html_tag)
        
        url_pattern = re.compile(r'(https?://[^\s<>()"\'`]+)')
        return url_pattern.sub(r'<a href="\1" target="_blank">\1</a>', escaped_text)

    def _generate_html_from_history(self, conversation_history, active_doc):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        doc_info = f"Doc #{active_doc['id']}: {active_doc['relative_path']}" if active_doc else "All Docs"
        chat_body_html = ""
        
        for entry in conversation_history:
            role = entry['role']
            content = entry['content']
            
            if role == 'user':
                chat_body_html += f'<div class="entry user-entry"><div class="header">USER ({html.escape(doc_info)})</div><pre>{html.escape(content)}</pre></div>\n'
            elif role == 'assistant':
                chat_body_html += f'<div class="entry assistant-entry"><div class="header">ASSISTANT</div><pre>{self._ansi_to_html(content)}</pre></div>\n'
        
        return f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Redleaf Chat Export - {timestamp}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.6; margin: 0; padding: 20px; background-color: #121212; color: #E8E8EA; }}
        a {{ color: #82aaff; text-decoration: none; }} a:hover {{ text-decoration: underline; }}
        .container {{ max-width: 1000px; margin: auto; background-color: #1E1E24; border: 1px solid #383842; border-radius: 8px; padding: 25px; }}
        h1 {{ border-bottom: 1px solid #383842; padding-bottom: 10px; font-weight: 300; }}
        .meta-info {{ color: #828290; margin-bottom: 20px; }}
        .entry {{ margin-bottom: 2rem; border-left: 3px solid transparent; padding-left: 1rem; }}
        .entry.user-entry {{ border-left-color: #828290; }}
        .entry.assistant-entry {{ border-left-color: #4caf50; }}
        .header {{ font-weight: bold; font-size: 0.85em; color: #828290; margin-bottom: 0.5rem; }}
        pre {{ white-space: pre-wrap; word-wrap: break-word; margin: 0; font-family: Consolas, monospace; font-size: 1rem; }}
        .assistant-entry pre {{ color: #92cc94; }}
        .bold {{ font-weight: bold; color: #fff; }}
        .blue {{ color: #82aaff; }} 
        .green {{ color: #92cc94; }} 
        .yellow {{ color: #fdea9b; }} 
        .red {{ color: #ff8282; }} 
        .magenta {{ color: #c58af9; }} 
        .cyan {{ color: #7fdbca; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Redleaf Assistant Chat Log</h1>
        <p class="meta-info">Exported on: {timestamp}<br>User: {self.user['username']}</p>
        <hr style="border-color: #383842; margin-bottom: 2rem;">
        {chat_body_html}
    </div>
</body>
</html>"""

    def _export_chat_to_html(self, conversation_history, active_doc):
        if not conversation_history:
            print(f"{Style.YELLOW}[WARN] Chat history is empty. Nothing to print.{Style.END}")
            return
        chats_dir = project_dir / "chats"
        chats_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"chat_export_{timestamp}.html"
        filepath = chats_dir / filename
        html_content = self._generate_html_from_history(conversation_history, active_doc)
        try:
            with open(filepath, 'w', encoding='utf-8') as f: f.write(html_content)
            print(f"{Style.GREEN}[SUCCESS] Chat history exported to: {filepath.resolve()}{Style.END}")
        except IOError as e:
            print(f"{Style.RED}[ERROR] Could not write to file: {e}{Style.END}")

    # ===================================================================
    # --- NEW: SYSTEM AWARENESS & GLOBAL CONTEXT ---
    # ===================================================================
    def _get_global_context(self) -> str:
        """Generates a high-level briefing of what is in the database."""
        
        briefing_parts = []

        # 1. Automatic Scan (Always runs)
        try:
            # Get total doc count
            total_docs = self.db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            
            # Get time range from metadata
            date_range = self.db.execute("""
                SELECT MIN(json_extract(csl_json, '$.issued."date-parts"[0][0]')) as min_year,
                       MAX(json_extract(csl_json, '$.issued."date-parts"[0][0]')) as max_year
                FROM document_metadata
            """).fetchone()
            
            if date_range['min_year'] and date_range['max_year']:
                date_str = f"Years covered: {date_range['min_year']} to {date_range['max_year']}"
            else:
                date_str = "Years covered: Unknown"

            # Get top 5 tags
            top_tags = self.db.execute("""
                SELECT t.name, COUNT(dt.doc_id) as c 
                FROM tags t JOIN document_tags dt ON t.id = dt.tag_id 
                GROUP BY t.name ORDER BY c DESC LIMIT 5
            """).fetchall()
            tags_str = ", ".join([f"{t['name']} ({t['c']})" for t in top_tags]) if top_tags else "No tags yet"

            briefing_parts.append(f"[AUTO-DETECTED]: Total Documents: {total_docs}. {date_str}. Top Tags: {tags_str}.")
        except Exception as e:
            briefing_parts.append("[AUTO-DETECTED]: System briefing unavailable (Database error).")

        # 2. Manual Context (Appended if present)
        if self.manual_context:
            briefing_parts.append(f"\n[USER OVERRIDE]: {self.manual_context}")

        return "\n".join(briefing_parts)

    # ===================================================================
    # --- MODIFIED: AGENTIC RECURSIVE STUDY IMPLEMENTATION ---
    # ===================================================================
    def _handle_study_command(self, user_input: str) -> Optional[str]:
        """Executes a deep, multi-document study workflow with Broad-to-Narrow filtering."""
        study_match = re.search(r'^study:\s*(.+)', user_input, re.IGNORECASE)
        if not study_match: return None
        
        goal = study_match.group(1).strip()
        
        # 1. GET POOL CONTEXT
        system_stats = self._get_global_context()
        
        print(f"\n{Style.MAGENTA}{Style.BOLD}=== 🕵️ Starting Autonomous Study on: '{goal}' ==={Style.END}")
        print(f"{Style.CYAN}System Briefing:{Style.END}\n{system_stats}")
        print(f"{Style.CYAN}Investigator Persona:{Style.END} {self.persona}\n")
        
        accumulated_notes = "No notes yet."
        read_history: Set[str] = set()
        max_steps = 8
        
        for step in range(1, max_steps + 1):
            print(f"{Style.BOLD}[Step {step}/{max_steps}]{Style.END}", end=" ")
            
            # 2. DECIDE NEXT MOVE
            # Inject Persona and System Stats into the prompt
            prompt_content = RECURSIVE_STUDY_PROMPT.format(
                goal=goal, 
                current_notes=accumulated_notes, 
                system_stats=system_stats,
                persona=self.persona
            )
            
            decision_messages = [{"role": "system", "content": prompt_content}]
            decision_json = self.get_assistant_response(decision_messages, use_json=True)
            
            try:
                decision = json.loads(decision_json)
                action = decision.get("action", "finish")
                thought = decision.get("thought", "No thought provided.")
                comment = decision.get("comment", "") # Capture new AI comment
                query = decision.get("query", "")
            except json.JSONDecodeError:
                print(f"{Style.RED}[Error] JSON Decode failed. Stopping.{Style.END}")
                break

            # 3. PRINT AI SCRATCHPAD COMMENT
            if comment:
                print(f"{Style.MAGENTA}AI: \"{comment}\"{Style.END}")
            print(f"{Style.YELLOW}Thought: {thought}{Style.END}")

            if action == "finish":
                print(f"{Style.GREEN}--> Decision: Finish and Write Report.{Style.END}")
                break
            
            if action == "search" and query:
                print(f"{Style.CYAN}--> Action: Searching for '{query}'...{Style.END}")
                
                # 4. SCAN (Hybrid Search) - Get top 50 candidates
                fts_hits = _internal_fts_search(self.db, query, limit=50)
                sem_hits = _internal_semantic_search(self.db, query, limit=50)
                
                # Merge and deduplicate based on Doc ID + Page
                candidates = {}
                for hit in fts_hits + sem_hits:
                    key = f"{hit['doc_id']}:{hit['page_number']}"
                    if key not in read_history: 
                        candidates[key] = hit
                
                if not candidates:
                    print(f"{Style.YELLOW}    [No new unread documents found. Skipping.]{Style.END}")
                    accumulated_notes += f"\n[System Note]: Search for '{query}' yielded no new documents."
                    continue

                # Format candidates for the Selector Agent (Broad Filter)
                candidate_text = ""
                indexed_candidates = list(candidates.values())[:20]
                
                for i, cand in enumerate(indexed_candidates):
                    doc_path_row = self.db.execute("SELECT relative_path FROM documents WHERE id = ?", (cand['doc_id'],)).fetchone()
                    doc_name = Path(doc_path_row['relative_path']).name if doc_path_row else "Unknown"
                    candidate_text += f"ID {i}: [Doc {cand['doc_id']} - {doc_name} (Pg {cand['page_number']})]: {cand['snippet'][:150]}...\n"

                # 5. SELECT (Broad Context Filtering)
                # KEY CHANGE: We pass the GOAL + Query to the selector so it knows to filter out drift.
                print(f"{Style.CYAN}    Scanning {len(candidates)} candidates for relevance to '{goal}'...{Style.END}")
                broad_context_query = f"GOAL: {goal} (Specific Search: {query})"
                selector_messages = [{"role": "system", "content": SELECTOR_PROMPT.format(query=broad_context_query, candidates=candidate_text, k=3)}]
                selection_json = self.get_assistant_response(selector_messages, use_json=True)
                
                try:
                    selection_data = json.loads(selection_json)
                    selected_indices = selection_data.get("selected_ids", [])
                except:
                    selected_indices = [0, 1, 2]

                # 6. READ & EXTRACT (Narrow Filtering)
                docs_read_count = 0
                for idx in selected_indices:
                    if idx >= len(indexed_candidates): continue
                    target = indexed_candidates[idx]
                    key = f"{target['doc_id']}:{target['page_number']}"
                    if key in read_history: continue
                    read_history.add(key)
                    
                    # read_specific_pages now includes Curator Hints!
                    full_text = read_specific_pages(self.db, [{'doc_id': target['doc_id'], 'page_number': target['page_number']}])
                    print(f"{Style.GREEN}    Reading Doc {target['doc_id']} (Pg {target['page_number']})...{Style.END}")
                    
                    # 7. UPDATE NOTES (Inject Persona & Goal for Strict Filtering)
                    # KEY CHANGE: Passing 'goal' to Note Taker enforces the narrow filter.
                    extractor_messages = [{"role": "system", "content": NOTE_TAKER_PROMPT.format(
                        goal=goal, 
                        current_notes=accumulated_notes, 
                        new_content=full_text,
                        doc_id=target['doc_id'],
                        page_num=target['page_number'],
                        persona=self.persona
                    )}]
                    new_facts = self.get_assistant_response(extractor_messages)
                    
                    if "Nothing relevant" not in new_facts:
                        accumulated_notes += f"\n{new_facts}"
                        docs_read_count += 1
                
                if docs_read_count == 0:
                    accumulated_notes += f"\n[System Note]: Investigated documents for '{query}' but found no *new* relevant information."

        # 8. FINAL REPORT (Inject Persona here too)
        print(f"\n{Style.MAGENTA}{Style.BOLD}=== 📝 Synthesizing Final Report ==={Style.END}")
        
        writer_messages = [
            {"role": "system", "content": STUDY_REPORT_PROMPT.format(persona=self.persona)},
            {"role": "user", "content": f"TOPIC: {goal}\n\nRESEARCH NOTES AND EVIDENCE:\n{accumulated_notes}\n\n---\n\nINSTRUCTION: Write the final report now based on the notes above."}
        ]
        
        print(f"\n{Style.GREEN}", end='')
        final_report = self.get_assistant_response_stream(writer_messages)
        print(f"{Style.END}\n", end='')

        # 9. POST-PROCESSING: LINKS
        links_section = "\n\n🔗 **Reference Links:**\n"
        cited_ids = set(re.findall(r'\[Doc[:.]?\s*(\d+)\]', final_report, re.IGNORECASE))
        
        if not cited_ids:
             cited_ids = set(re.findall(r'\[Doc[:.]?\s*(\d+)\]', accumulated_notes, re.IGNORECASE))
             links_section += "(Documents Consulted)\n"

        if cited_ids:
            placeholders = ','.join('?' for _ in cited_ids)
            rows = self.db.execute(f"SELECT id, relative_path FROM documents WHERE id IN ({placeholders})", list(cited_ids)).fetchall()
            
            for row in rows:
                url = f"{REDLEAF_BASE_URL}/document/{row['id']}"
                print(f"  • {Style.CYAN}[Doc {row['id']}]{Style.END}: {row['relative_path']}")
                print(f"    {Style.BLUE}{url}{Style.END}")
                links_section += f"- [Doc {row['id']}]({url}): {row['relative_path']}\n"
        else:
            print(f"  {Style.YELLOW}(No specific documents were found or cited){Style.END}")
            links_section += "(No specific documents were cited)\n"
        
        return final_report + links_section

    def _handle_research_command(self, user_input: str, active_doc: Dict) -> str:
        # (Legacy simple research command - retained for backward compatibility)
        research_match = re.search(r'^research:\s*(.+)', user_input, re.IGNORECASE)
        if not research_match: return None
        user_instruction = research_match.group(1).strip()
        print(f"{Style.MAGENTA}[INFO] Executing simple lookup for: '{user_instruction}'{Style.END}")
        return self.available_tools['research_across_all_documents']([user_instruction])

    # ===================================================================
    # --- STANDARD SEARCH & INTERACTION (Unchanged logic) ---
    # ===================================================================
    def _handle_search_command(self, user_input: str) -> Tuple[Union[str, None], Union[Tuple[str, str], None]]:
        search_match = re.search(r'^search:\s*(.+)', user_input, re.IGNORECASE)
        if not search_match: return None, None

        full_query = search_match.group(1).strip()
        all_parts = [p.strip() for p in full_query.split(' + ')]
        search_query = all_parts[0]
        limit = self.max_global_search_results
        instruction_parts = []
        for part in all_parts[1:]:
            results_match = re.match(r'^results:\s*(\d+)', part, re.IGNORECASE)
            if results_match:
                try: limit = int(results_match.group(1))
                except ValueError: pass
            else: instruction_parts.append(part)
        instruction_string = " + ".join(instruction_parts)

        print(f"{Style.CYAN}[INFO] Performing {self.search_strategy} search for: '{search_query}'{Style.END}")
        
        fetch_limit = limit * 2 
        fts_sources = _internal_fts_search(self.db, search_query, limit=fetch_limit)
        sem_sources = _internal_semantic_search(self.db, search_query, limit=fetch_limit)

        rrf_scores = defaultdict(float)
        k = 60
        for rank, source in enumerate(fts_sources): rrf_scores[(source['doc_id'], source['page_number'])] += 1 / (k + rank + 1)
        for rank, source in enumerate(sem_sources): rrf_scores[(source['doc_id'], source['page_number'])] += 1 / (k + rank + 1)
        
        sorted_sources = sorted(rrf_scores.keys(), key=lambda s: rrf_scores[s], reverse=True)
        final_sources = [{'doc_id': d, 'page_number': p} for d, p in sorted_sources[:limit]]
        
        if not final_sources: return f"{Style.YELLOW}[INFO] No documents found.{Style.END}", None

        combined_context = read_specific_pages(self.db, sources=final_sources)
        
        writer_instruction = instruction_string if instruction_string else f"Summarize findings on '{search_query}'."
        print(f"{Style.CYAN}[Thinking...]{Style.END}\n", flush=True)
        messages = [{"role": "system", "content": self.writer_prompt}, {"role": "user", "content": writer_instruction}, {"role": "assistant", "content": f"Context:\n\n{combined_context}"}]
        print(f"{Style.GREEN}", end=''); final_answer = self.get_assistant_response_stream(messages); print(f"{Style.END}", end='')
        return final_answer, (search_query, combined_context)

    def _handle_instruct_command(self, user_input: str) -> Union[str, None]:
        doc_page_match = re.search(r'^id:\s*(\d+)\s*\+\s*page:\s*(\d+(?:-\d+)?)\s*\+\s*(.+)', user_input, re.IGNORECASE)
        if not doc_page_match: return None
        doc_id, page_range, instr = doc_page_match.groups()
        try:
            d_id = int(doc_id)
            start, end = (map(int, page_range.split('-'))) if '-' in page_range else (int(page_range), int(page_range))
        except: return f"{Style.RED}Invalid format.{Style.END}"
        
        doc = self.db.execute("SELECT relative_path, file_type FROM documents WHERE id = ?", (d_id,)).fetchone()
        if not doc: return f"{Style.RED}Doc not found.{Style.END}"
        
        # --- FIX: Resolve the path ---
        resolved_path = resolve_document_path(doc['relative_path'])
        
        # --- FIX: Pass doc_id to extractor ---
        text = extract_text_for_copying(resolved_path, doc['file_type'], start_page=start, end_page=end, doc_id=d_id)
        
        # --- FIX: Inject Metadata Header for Instruct Command ---
        doc_url = f"{REDLEAF_BASE_URL}/document/{d_id}"
        header = f"--- CONTEXT from Document #{d_id} ({doc['relative_path']}) URL: {doc_url}, Pages {page_range} ---\n"
        full_context = header + text
        
        msg = [{"role": "system", "content": self.writer_prompt}, {"role": "user", "content": instr}, {"role": "assistant", "content": f"Context:\n{full_context}"}]
        print(f"{Style.GREEN}", end=''); ans = self.get_assistant_response_stream(msg); print(f"{Style.END}", end='')
        return ans

    def _handle_for_each_page_command(self, user_input: str, active_doc: Dict) -> Tuple[Union[str, None], Union[str, None]]:
        page_match = re.search(self.for_each_regex, user_input, re.IGNORECASE)
        if not page_match: return None, None
        if not active_doc: return f"{Style.RED}Load a doc first.{Style.END}", None
        
        page_range_str, instruction = page_match.groups()
        doc_id = active_doc['id']
        total_page_count = active_doc.get('page_count')
        start_page, end_page = 1, total_page_count
        if page_range_str:
            try:
                if '-' in page_range_str: start_page, end_page = map(int, page_range_str.split('-'))
                else: start_page = end_page = int(page_range_str)
                if start_page > end_page or start_page < 1 or (total_page_count and end_page > total_page_count):
                    return f"{Style.RED}[ERROR] Invalid page range.{Style.END}", None
            except ValueError:
                return f"{Style.RED}[ERROR] Invalid page number format.{Style.END}", None
        
        if not total_page_count or total_page_count < 1:
            return f"{Style.YELLOW}[INFO] This document has no pages to process.{Style.END}", None

        pages_to_process = range(start_page, end_page + 1)
        full_batch_output, full_document_text = [], []
        print(f"{Style.CYAN}[INFO] Processing pages {start_page}-{end_page} in Doc #{doc_id}.{Style.END}")
        full_batch_output.append(f"[INFO] Processing pages {start_page}-{end_page} in Doc #{doc_id}.")

        for page_num in pages_to_process:
            print(f"\n{Style.BOLD}--- Analyzing Page {page_num}/{end_page} ---{Style.END}")
            full_batch_output.append(f"\n--- Analyzing Page {page_num}/{end_page} ---")
            
            page_text, doc_info = get_page_content(self.db, doc_id=doc_id, page_number=page_num)
            if not page_text or not doc_info:
                print(f"{Style.YELLOW}[SKIP] Page {page_num} has no text.{Style.END}")
                continue
            
            full_document_text.append(page_text)
            doc_url = f"{REDLEAF_BASE_URL}/document/{doc_id}"
            final_context = f"--- CONTEXT from Document #{doc_id} ({doc_info['relative_path']}) URL: {doc_url}, Page {page_num} ---\n" + page_text
            
            print(f"{Style.CYAN}[Thinking...]{Style.END}")
            messages = [{"role": "system", "content": self.writer_prompt}, {"role": "user", "content": instruction}, {"role": "assistant", "content": f"Context:\n\n{final_context}"}]
            
            print(f"{Style.GREEN}", end='')
            final_answer = self.get_assistant_response_stream(messages); print(f"{Style.END}", end='')
            full_batch_output.append(final_answer)

        final_summary_header = f"\n{Style.BOLD}--- Batch processing complete. ---{Style.END}"
        print(final_summary_header)
        full_batch_output.append(final_summary_header)
        
        full_doc_context = "\n\n".join(full_document_text)
        return "\n".join(full_batch_output), full_doc_context

    def run(self):
        if not self._login(): return
        
        active_doc, conversation_history = None, []
        last_global_context, last_global_query = "", ""
        last_doc_context = None

        while True:
            doc_prompt = f"#{active_doc['id']}" if active_doc else "All Docs"
            try: user_input = input(f"{Style.BOLD}({doc_prompt}){Style.END} > ").strip()
            except (KeyboardInterrupt, EOFError): print("\nGoodbye!"); break
            if not user_input: continue

            # --- INTERCEPT STUDY COMMAND ---
            if user_input.lower().startswith("study:"):
                report_content = self._handle_study_command(user_input)
                if report_content:
                    conversation_history.append({"role": "assistant", "content": report_content})
                continue
            # -------------------------------

            if user_input.startswith('/'):
                command = user_input.lstrip('/').split(maxsplit=1)[0].lower()
                if command == 'exit': print("Goodbye!"); break
                elif command == 'help': self._print_help()
                elif command == 'clear': active_doc = None; print("[OK] Cleared.")
                elif command == 'print': self._export_chat_to_html(conversation_history, active_doc)
                continue
            
            conversation_history.append({"role": "user", "content": user_input})
            
            # Handle other commands (search, load, etc)
            if user_input.lower().startswith('search:'):
                ans, ctx = self._handle_search_command(user_input)
                if ans:
                    conversation_history.append({"role": "assistant", "content": ans})
                    if ctx: last_global_context, last_global_query = ctx
                continue

            # 4. Instruct (id:X + page:Y)
            ans = self._handle_instruct_command(user_input)
            if ans:
                conversation_history.append({"role": "assistant", "content": ans})
                continue

            # 5. For Each
            ans, ctx = self._handle_for_each_page_command(user_input, active_doc)
            if ans:
                if ctx:
                    last_doc_context = ctx
                    last_global_context, last_global_query = "", ""
                conversation_history.append({"role": "assistant", "content": ans})
                continue

            # 6. Load/Find (Regex check)
            find_match = re.search(r'^(?:find|search for)\s+(.+)', user_input, re.IGNORECASE)
            load_match = re.search(r'^(?:load|open|get)\s+(?:doc|document)?\s*(?:#|id:?)?\s*(\S+)', user_input, re.IGNORECASE)

            if find_match or load_match:
                # Remove the user command from history if it was just a system op
                conversation_history.pop()
                
                if find_match:
                    q = find_match.group(1).strip()
                    res = self.db.execute("SELECT id, relative_path FROM documents WHERE relative_path LIKE ?", (f"%{q}%",)).fetchall()
                    if not res: print(f"{Style.YELLOW}[INFO] No docs found.{Style.END}")
                    else: [print(f"  {Style.BOLD}#{r['id']}:{Style.END} {r['relative_path']}") for r in res]
                
                if load_match:
                    ident = load_match.group(1).strip()
                    rec = None
                    try: 
                        d_id = int(ident)
                        rec = self.db.execute("SELECT * FROM documents WHERE id = ?", (d_id,)).fetchone()
                    except:
                        rec = self.db.execute("SELECT * FROM documents WHERE relative_path LIKE ?", (f"%{ident}%",)).fetchone()
                    
                    if not rec: print(f"{Style.RED}[FAIL] Doc not found.{Style.END}")
                    else:
                        active_doc = dict(rec)
                        conversation_history = [] # Reset history on new doc load
                        print(f"{Style.GREEN}[OK] Loaded Doc ID {active_doc['id']}: '{active_doc['relative_path']}'{Style.END}")
                continue
            
            # 7. Default Chat (Conversation)
            context_to_use = last_doc_context if (active_doc and last_doc_context) else last_global_context
            
            if context_to_use:
                print(f"{Style.CYAN}[Thinking... Context Aware]{Style.END}")
                msgs = [
                    {"role": "system", "content": CONVERSATION_PROMPT}, 
                    {"role": "user", "content": f"Context:\n{context_to_use}"},
                    {"role": "assistant", "content": "I have the context. What is your question?"},
                    {"role": "user", "content": user_input}
                ]
            else:
                # Fallback to simple chat if no context loaded
                print(f"{Style.CYAN}[Thinking... General Chat]{Style.END}")
                msgs = [{"role": "system", "content": CONVERSATION_PROMPT}, *conversation_history[-5:]]

            print(f"{Style.GREEN}", end=''); ans = self.get_assistant_response_stream(msgs); print(f"{Style.END}", end='')
            conversation_history.append({"role": "assistant", "content": ans})