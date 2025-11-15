# --- File: ./project/assistant_core.py ---
import sys
import getpass
import re
import json
import ollama
import html
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Union, List, Tuple
from collections import defaultdict
import numpy as np

# --- Add project to Python path to access its modules ---
project_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(project_dir))

# --- Import from your existing Redleaf project ---
from project.database import get_db
from werkzeug.security import check_password_hash
from processing_pipeline import extract_text_for_copying
from project.config import DOCUMENTS_DIR, REDLEAF_BASE_URL, EMBEDDING_MODEL

# --- Import prompts from the new prompts file ---
from project.prompts import (
    WRITER_PROMPT, 
    CONVERSATION_PROMPT, 
    BATCH_CONVERSATION_PROMPT, 
    RESEARCHER_AGENT_PROMPT,
    ROUTER_PROMPT
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

# --- Core Search & Data Retrieval Functions (Unchanged) ---
def _internal_fts_search(db, query: str, limit: int = 20) -> List[Dict]:
    sanitized = query.replace('"', '""'); fts_query = f'"{sanitized}"'
    sql = "SELECT ci.doc_id, ci.page_number FROM content_index ci WHERE ci.content_index MATCH ? ORDER BY rank LIMIT ?"
    return [dict(r) for r in db.execute(sql, [fts_query, limit]).fetchall()]

def _internal_semantic_search(db, query: str, limit: int = 20) -> List[Dict]:
    try:
        print(f"{Style.CYAN}[INFO] Generating embedding for query...{Style.END}")
        query_embedding_response = ollama.embeddings(model=EMBEDDING_MODEL, prompt=query)
        query_vector = np.array(query_embedding_response['embedding'], dtype=np.float32)
    except Exception as e:
        print(f"{Style.RED}[ERROR] Could not generate query embedding: {e}{Style.END}")
        return []

    normal_chunks = db.execute("SELECT doc_id, page_number, embedding FROM embedding_chunks").fetchall()
    super_chunks = db.execute("SELECT doc_id, page_number, embedding FROM super_embedding_chunks").fetchall()
    
    all_chunks = normal_chunks + super_chunks
    if not all_chunks: return []
    
    chunk_embeddings = np.array([np.frombuffer(chunk['embedding'], dtype=np.float32) for chunk in all_chunks])
    norm_chunk_embeddings = chunk_embeddings / np.linalg.norm(chunk_embeddings, axis=1, keepdims=True)
    norm_query_vector = query_vector / np.linalg.norm(query_vector)
    
    similarities = np.dot(norm_chunk_embeddings, norm_query_vector)
    
    k = min(limit, len(similarities));
    if k <= 0: return []
        
    top_k_indices = np.argpartition(similarities, -k)[-k:]
    sorted_top_k_indices = top_k_indices[np.argsort(similarities[top_k_indices])][::-1]
    
    unique_sources, top_results = set(), []
    for index in sorted_top_k_indices:
        chunk = all_chunks[index]; 
        source_tuple = (chunk['doc_id'], chunk['page_number'])
        if source_tuple not in unique_sources:
            unique_sources.add(source_tuple)
            top_results.append({'doc_id': chunk['doc_id'], 'page_number': chunk['page_number']})
            if len(top_results) >= limit: break
            
    return top_results

def read_specific_pages(db, sources: List[Dict[str, int]], MAX_CONTEXT_CHARS=32000) -> str:
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

def get_page_content(db, doc_id: int, page_number: int) -> Tuple[str, Dict]:
    doc = db.execute("SELECT id, relative_path, file_type, page_count FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc: return f"Error: No document found with ID {doc_id}.", None
    if doc['page_count'] and (page_number < 1 or page_number > doc['page_count']): return f"Error: Invalid page number.", None
    page_text = extract_text_for_copying(DOCUMENTS_DIR / doc['relative_path'], doc['file_type'], start_page=page_number, end_page=page_number)
    if not page_text.strip(): return f"Page {page_number} was found but contains no text.", None
    return page_text, dict(doc)


class BaseAssistant:
    def __init__(self, reasoning_model, available_tools, router_prompt, search_strategy="hybrid", max_global_search_results=15):
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
        print(f"  {Style.GREEN}research: [goal]{Style.END} - Start autonomous research on a topic.")
        print(f"  {Style.GREEN}find [query]{Style.END}       - Find documents by path without loading them.")
        print(f"  {Style.GREEN}load [doc_id|path]{Style.END}- Load a specific document for focused questions.")
        print(f"  {Style.GREEN}search: [q]{Style.END}      - Perform {self.search_strategy} search and summarize top results.")
        print(f"  {Style.GREEN}id:[id] + page:[p-p] + [instr]{Style.END} - Run instruction on a specific page range.")
        print(f"  {Style.GREEN}for each page in [range] + [instr]{Style.END} - Run instruction on each page in a range.")
        print(f"  {Style.GREEN}search: [q] + [instr] + results:[N]{Style.END} - Override the number of search results.")
        print(f"  {Style.GREEN}/print{Style.END}            - Export the current chat session to an HTML file.")
        print(f"  {Style.GREEN}/help{Style.END}             - Show this help message.")
        print(f"  {Style.GREEN}/clear{Style.END}            - Unload doc and return to the global 'All Docs' view.")
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
        replacements = { Style.BLUE: '<span class="blue">', Style.GREEN: '<span class="green">', Style.YELLOW: '<span class="yellow">', Style.RED: '<span class="red">', Style.BOLD: '<span class="bold">', Style.MAGENTA: '<span class="magenta">', Style.CYAN: '<span class="cyan">', Style.END: '</span>', }
        for ansi, html_tag in replacements.items():
            escaped_text = escaped_text.replace(html.escape(ansi), html_tag)
        url_pattern = re.compile(r'(https?://[^\s<>()"\'`]+)')
        return url_pattern.sub(r'<a href="\1" target="_blank">\1</a>', escaped_text)

    def _generate_html_from_history(self, conversation_history, active_doc):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        doc_info = f"Doc #{active_doc['id']}: {active_doc['relative_path']}" if active_doc else "All Docs"
        chat_body_html = ""
        for entry in conversation_history:
            if entry['role'] == 'user':
                chat_body_html += f'<div class="entry user-entry"><pre><strong>({html.escape(doc_info)}) ></strong> {html.escape(entry["content"])}</pre></div>\n'
            elif entry['role'] == 'assistant':
                chat_body_html += f'<div class="entry assistant-entry"><pre>{self._ansi_to_html(entry["content"])}</pre></div>\n'
        
        return f"""
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>Redleaf Chat Export - {timestamp}</title><style>body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.6; margin: 0; padding: 20px; background-color: #121212; color: #E8E8EA; }} a {{ color: #82aaff; text-decoration: none; }} a:hover {{ text-decoration: underline; }} .container {{ max-width: 1000px; margin: auto; background-color: #1E1E24; border: 1px solid #383842; border-radius: 8px; padding: 25px; }} h1 {{ border-bottom: 1px solid #383842; padding-bottom: 10px; }} .meta-info {{ color: #828290; }} .entry {{ margin-bottom: 1rem; }} pre {{ white-space: pre-wrap; word-wrap: break-word; margin: 0; font-family: Consolas, monospace; }} .assistant-entry pre {{ color: #92cc94; }} .bold {{ font-weight: bold; }} .blue {{ color: #82aaff; }} .green {{ color: #92cc94; }} .yellow {{ color: #fdea9b; }} .red {{ color: #ff8282; }} .magenta {{ color: #c58af9; }} .cyan {{ color: #7fdbca; }}</style></head>
<body><div class="container"><h1>Redleaf Assistant Chat Log</h1><p class="meta-info">Exported on: {timestamp}<br>User: {self.user['username']}</p><hr style="border-color: #383842;">{chat_body_html}</div></body></html>"""

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

    def _handle_research_command(self, user_input: str, active_doc: Dict) -> str:
        research_match = re.search(r'^research:\s*(.+)', user_input, re.IGNORECASE)
        if not research_match:
            return None

        user_instruction = research_match.group(1).strip()
        print(f"{Style.MAGENTA}[INFO] Starting automated research for: '{user_instruction}'{Style.END}")
        
        research_context = f"Focused on Document ID {active_doc['id']}" if active_doc else "Global Search Mode (across all documents)"
        print(f"{Style.CYAN}[CONTEXT] {research_context}{Style.END}")
        
        prompt = self.researcher_prompt.format(
            user_instruction=user_instruction,
            research_context=research_context,
            scratchpad_content="No actions taken yet."
        )
        messages = [{"role": "system", "content": prompt}]

        print(f"{Style.CYAN}[Agent Planning...]{Style.END}")
        response_str = self.get_assistant_response(messages, use_json=True)
        
        try:
            response_json = json.loads(response_str)
            thought = response_json.get("thought")
            action = response_json.get("action")
            
            tool_name, tool_args = None, {}
            if isinstance(action, dict):
                for key in ["name", "tool_name", "tool"]:
                    if key in action: tool_name = action.pop(key); break
                tool_args = action.get("arguments", action)
            
            if not thought or not tool_name:
                raise ValueError("Agent failed to produce a valid thought or tool name.")

            print(f"{Style.YELLOW}[Thought] {thought}{Style.END}")

            if tool_name in self.available_tools:
                if active_doc and 'doc_id' in self.available_tools[tool_name].__code__.co_varnames and 'doc_id' not in tool_args:
                    tool_args['doc_id'] = active_doc['id']

                print(f"{Style.CYAN}[Action] Executing: {tool_name}({', '.join(f'{k}={v}' for k, v in tool_args.items())}){Style.END}")
                tool_output = self.available_tools[tool_name](**tool_args)
                print(f"{Style.MAGENTA}[Observation] {str(tool_output)[:1000]}...{Style.END}")
                return tool_output
            else:
                return f"Error: Agent tried to call a non-existent tool '{tool_name}'."
        except (json.JSONDecodeError, AttributeError, ValueError) as e:
            print(f"{Style.RED}[FATAL] Agent failed to produce a valid action. Details: {e}{Style.END}\nRaw Response: {response_str}")
            return "Agent failed to generate a valid research plan."
            
    # ===================================================================
    # --- MODIFIED FUNCTION: _handle_search_command ---
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
                try:
                    parsed_limit = int(results_match.group(1)); limit = max(1, min(100, parsed_limit))
                    print(f"{Style.CYAN}[INFO] Search result limit overridden to {limit}.{Style.END}")
                except ValueError: print(f"{Style.YELLOW}[WARN] Invalid results limit, using default.{Style.END}")
            else: instruction_parts.append(part)
        instruction_string = " + ".join(instruction_parts)

        print(f"{Style.CYAN}[INFO] Performing {self.search_strategy} search for: '{search_query}'{Style.END}")
        
        fetch_limit = limit * 2 
        fts_sources, semantic_sources = [], []
        if self.search_strategy in ["hybrid", "keyword"]: fts_sources = _internal_fts_search(self.db, search_query, limit=fetch_limit)
        if self.search_strategy in ["hybrid", "semantic"]: semantic_sources = _internal_semantic_search(self.db, search_query, limit=fetch_limit)

        rrf_scores = defaultdict(float)
        k = 60
        for rank, source in enumerate(fts_sources): rrf_scores[(source['doc_id'], source['page_number'])] += 1 / (k + rank + 1)
        for rank, source in enumerate(semantic_sources): rrf_scores[(source['doc_id'], source['page_number'])] += 1 / (k + rank + 1)
        
        sorted_sources = sorted(rrf_scores.keys(), key=lambda s: rrf_scores[s], reverse=True)

        # --- START OF NEW RE-RANKING LOGIC ---
        try:
            # 1. Check if the user's query contains a known entity
            main_entity = self.db.execute("SELECT id FROM entities WHERE ? LIKE '%' || text || '%'", (search_query,)).fetchone()
            
            if main_entity:
                # 2. If it does, find its boosted "friends" for the current user
                boosted_friends = self.db.execute(
                    "SELECT be.text FROM boosted_relationships br JOIN entities be ON br.target_entity_id = be.id WHERE br.user_id = ? AND br.source_entity_id = ?",
                    (self.user['id'], main_entity['id'])
                ).fetchall()
                
                if boosted_friends:
                    boosted_names = {friend['text'] for friend in boosted_friends}
                    print(f"{Style.MAGENTA}[INFO] Applying boost for relationships: {', '.join(boosted_names)}{Style.END}")
                    
                    # 3. For each search result, check if it also contains a "friend"
                    for source_tuple in sorted_sources:
                        doc_id, page_num = source_tuple
                        page_content, _ = get_page_content(self.db, doc_id, page_num)
                        
                        if page_content and any(friend_name.lower() in page_content.lower() for friend_name in boosted_names):
                            # 4. Apply a significant boost to its RRF score!
                            rrf_scores[source_tuple] *= 1.5  # A 50% score boost

                    # 5. Re-sort the sources based on the newly boosted scores
                    sorted_sources = sorted(rrf_scores.keys(), key=lambda s: rrf_scores[s], reverse=True)

        except Exception as e:
            print(f"{Style.RED}[WARN] Could not apply relational boost: {e}{Style.END}")
        # --- END OF NEW RE-RANKING LOGIC ---

        final_sources = [{'doc_id': doc_id, 'page_number': page_num} for doc_id, page_num in sorted_sources]
        
        sources = final_sources[:limit]
        if not sources:
            return f"{Style.YELLOW}[INFO] No documents found for '{search_query}'.{Style.END}", None

        combined_context = read_specific_pages(self.db, sources=sources)
        
        if "for each" in instruction_string.lower():
            match = re.search(r'for each\s*\+?\s*(.+)', instruction_string, re.IGNORECASE)
            if match:
                for_each_instruction = match.group(1).strip()
                grouped_sources = defaultdict(list)
                for source in sources: grouped_sources[source['doc_id']].append(source['page_number'])
                
                full_batch_output = []
                info_header = f"{Style.CYAN}[INFO] Found results in {len(grouped_sources)} documents. Processing each...{Style.END}"
                print(info_header)
                full_batch_output.append(info_header)

                doc_ids = list(grouped_sources.keys())
                placeholders = ','.join('?' for _ in doc_ids)
                doc_info_map = { row['id']: row for row in self.db.execute(f"SELECT id, relative_path FROM documents WHERE id IN ({placeholders})", doc_ids) }

                for doc_id, page_numbers in grouped_sources.items():
                    doc_info = doc_info_map.get(doc_id)
                    if not doc_info: continue
                    
                    doc_header = f"\n{Style.BOLD}--- Analyzing Doc #{doc_id} ({doc_info['relative_path']}) ---{Style.END}"
                    print(doc_header, flush=True)
                    full_batch_output.append(doc_header)

                    loop_context = read_specific_pages(self.db, sources=[{'doc_id': doc_id, 'page_number': p} for p in page_numbers])
                    
                    if not loop_context.strip() or "[ERROR]" in loop_context:
                         print(f"{Style.YELLOW}[SKIP] Could not read relevant pages.{Style.END}", flush=True)
                         full_batch_output.append("[SKIP] Could not read relevant pages.")
                         continue

                    print(f"{Style.CYAN}[Thinking... Fulfilling: '{for_each_instruction}']{Style.END}", flush=True)
                    messages = [{"role": "system", "content": self.writer_prompt}, {"role": "user", "content": for_each_instruction}, {"role": "assistant", "content": f"Context:\n\n{loop_context}"}]
                    
                    print(f"\n{Style.GREEN}", end='')
                    final_answer = self.get_assistant_response_stream(messages)
                    print(f"{Style.END}", end='')
                    full_batch_output.append(final_answer)

                final_summary = f"\n{Style.BOLD}--- Batch processing complete. ---{Style.END}\n"
                final_summary += f"{Style.MAGENTA}{Style.BOLD}[Session Active] The combined content from all {len(grouped_sources)} docs is now in memory.{Style.END}"
                print(final_summary)
                full_batch_output.append(final_summary)
                
                return "\n".join(full_batch_output), (search_query, combined_context)
            else:
                return f"{Style.RED}[ERROR] Malformed 'for each' command.{Style.END}", None
        else:
            writer_instruction = instruction_string if instruction_string else f"Give a high-level abstract summary about '{search_query}'."
            print(f"{Style.CYAN}[Thinking... Fulfilling: '{writer_instruction}']{Style.END}\n", flush=True)
            messages = [{"role": "system", "content": self.writer_prompt}, {"role": "user", "content": writer_instruction}, {"role": "assistant", "content": f"Context:\n\n{combined_context}"}]
            print(f"{Style.GREEN}", end='')
            final_answer = self.get_assistant_response_stream(messages)
            print(f"{Style.END}", end='')
            return final_answer, (search_query, combined_context)
    # ===================================================================

    def _handle_instruct_command(self, user_input: str) -> Union[str, None]:
        doc_page_match = re.search(r'^id:\s*(\d+)\s*\+\s*page:\s*(\d+(?:-\d+)?)\s*\+\s*(.+)', user_input, re.IGNORECASE)
        if not doc_page_match: return None

        doc_id_str, page_range_str, instruction = doc_page_match.groups()
        try:
            doc_id = int(doc_id_str)
            if '-' in page_range_str: start_page, end_page = map(int, page_range_str.split('-'))
            else: start_page = end_page = int(page_range_str)
            if start_page > end_page: return f"{Style.RED}[ERROR] Invalid page range.{Style.END}"
        except ValueError: return f"{Style.RED}[ERROR] Invalid document ID or page format.{Style.END}"

        doc = self.db.execute("SELECT relative_path, file_type FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if not doc: return f"{Style.RED}[ERROR] Document with ID {doc_id} not found.{Style.END}"

        print(f"{Style.CYAN}[INFO] Fetching content from Doc ID {doc_id}, Pages: {page_range_str}...{Style.END}\n", flush=True)
        context_text = extract_text_for_copying(DOCUMENTS_DIR / doc['relative_path'], doc['file_type'], start_page=start_page, end_page=end_page)
        if not context_text.strip(): return f"{Style.YELLOW}[WARN] No text content found.{Style.END}"
        
        print(f"{Style.CYAN}[Thinking... Fulfilling: '{instruction}']{Style.END}\n", flush=True)
        doc_url = f"{REDLEAF_BASE_URL}/document/{doc_id}"
        context_header = f"--- CONTEXT from Document #{doc_id} ({doc['relative_path']}) URL: {doc_url}, Pages {page_range_str} ---\n"
        final_context = context_header + context_text
        
        messages = [{"role": "system", "content": self.writer_prompt}, {"role": "user", "content": instruction}, {"role": "assistant", "content": f"Context:\n\n{final_context}"}]
        print(f"{Style.GREEN}", end='')
        final_answer = self.get_assistant_response_stream(messages)
        print(f"{Style.END}", end='')
        return final_answer

    def _handle_for_each_page_command(self, user_input: str, active_doc: Dict) -> Tuple[Union[str, None], Union[str, None]]:
        page_match = re.search(self.for_each_regex, user_input, re.IGNORECASE)
        if not page_match:
            return None, None

        if not active_doc:
            return f"{Style.RED}[ERROR] A document must be loaded to use this command.{Style.END}", None

        page_range_str, instruction = page_match.groups()
        doc_id = active_doc['id']
        total_page_count = active_doc.get('page_count')
        start_page, end_page = 1, total_page_count
        if page_range_str:
            try:
                if '-' in page_range_str: start_page, end_page = map(int, page_range_str.split('-'))
                else: start_page = end_page = int(page_range_str)
                if start_page > end_page or start_page < 1 or (total_page_count and end_page > total_page_count):
                    return f"{Style.RED}[ERROR] Invalid page range (1-{total_page_count}).{Style.END}", None
            except ValueError:
                return f"{Style.RED}[ERROR] Invalid page number format.{Style.END}", None
        
        if not total_page_count or total_page_count < 1:
            return f"{Style.YELLOW}[INFO] This document has no pages to process.{Style.END}", None

        pages_to_process = range(start_page, end_page + 1)
        full_batch_output, full_document_text = [], []
        info_header = f"{Style.CYAN}[INFO] Processing pages {start_page}-{end_page} in Doc #{doc_id}.{Style.END}"
        print(info_header, flush=True)
        full_batch_output.append(info_header)

        for page_num in pages_to_process:
            page_header = f"\n{Style.BOLD}--- Analyzing Page {page_num}/{end_page} ---{Style.END}"
            print(page_header, flush=True)
            full_batch_output.append(page_header)
            page_text, doc_info = get_page_content(self.db, doc_id=doc_id, page_number=page_num)
            
            if not page_text or not doc_info:
                no_text_msg = f"{Style.YELLOW}[SKIP] Page {page_num} has no text.{Style.END}"
                print(no_text_msg, flush=True)
                full_batch_output.append(no_text_msg)
                continue
            
            full_document_text.append(page_text)
            thinking_msg = f"{Style.CYAN}[Thinking... Fulfilling: '{instruction}' for page {page_num}]{Style.END}"
            print(thinking_msg, flush=True)
            
            doc_url = f"{REDLEAF_BASE_URL}/document/{doc_id}"
            context_header = f"--- CONTEXT from Document #{doc_id} ({doc_info['relative_path']}) URL: {doc_url}, Page {page_num} ---\n"
            final_context = context_header + page_text
            messages = [{"role": "system", "content": self.writer_prompt}, {"role": "user", "content": instruction}, {"role": "assistant", "content": f"Context:\n\n{final_context}"}]
            
            print(f"{Style.GREEN}", end='')
            final_answer = self.get_assistant_response_stream(messages)
            print(f"{Style.END}", end='')
            full_batch_output.append(thinking_msg)
            full_batch_output.append(final_answer)

        final_summary_header = f"\n{Style.BOLD}--- Batch processing complete for pages {start_page}-{end_page}. ---{Style.END}"
        print(final_summary_header, flush=True)
        full_batch_output.append(final_summary_header)

        doc_url = f"{REDLEAF_BASE_URL}/document/{doc_id}"
        full_doc_context = f"--- CONTEXT from Doc #{doc_id} ({active_doc['relative_path']}) URL: {doc_url} ---\n" + "\n\n".join(full_document_text)
        conversation_prompt = f"{Style.MAGENTA}{Style.BOLD}[Session Active] Content from pages {start_page}-{end_page} is now in memory.{Style.END}"
        print(conversation_prompt)
        full_batch_output.append(conversation_prompt)
        
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

            if user_input.startswith('/'):
                command = user_input.lstrip('/').split(maxsplit=1)[0].lower()
                if command == 'exit': print("Goodbye!"); break
                elif command == 'help': self._print_help()
                elif command == 'clear': active_doc, conversation_history, last_global_context, last_global_query, last_doc_context = None, [], "", "", None; print("[OK] Document context cleared.")
                elif command == 'print': self._export_chat_to_html(conversation_history, active_doc)
                else: print(f"{Style.RED}Unknown command: /{command}.{Style.END}")
                continue
            
            conversation_history.append({"role": "user", "content": user_input})
            
            if user_input.lower().startswith('research:'):
                gathered_context = self._handle_research_command(user_input, active_doc)
                
                if not gathered_context or gathered_context.strip().startswith("Error:") or "not found" in gathered_context:
                    print(f"\n{Style.YELLOW}[INFO] Research concluded with no direct results or an error.{Style.END}")
                    print(f"{Style.YELLOW}Observation: {gathered_context}{Style.END}\n")
                    conversation_history.append({"role": "assistant", "content": gathered_context})
                    continue

                print(f"\n{Style.CYAN}[Writer] Synthesizing final answer from gathered context...{Style.END}")
                writer_messages = [
                    {"role": "system", "content": self.writer_prompt},
                    {"role": "user", "content": f"Based on the text I found, please answer my original question: '{user_input}'"},
                    {"role": "assistant", "content": f"Context:\n\n{gathered_context}"}
                ]
                print(f"\n{Style.GREEN}", end=''); final_answer = self.get_assistant_response_stream(writer_messages); print(f"{Style.END}\n", end='')
                conversation_history.append({"role": "assistant", "content": final_answer})
                continue
            
            output_text, context_info = self._handle_search_command(user_input)
            if output_text is not None:
                if context_info: 
                    last_global_query, last_global_context = context_info
                    last_doc_context = None
                conversation_history.append({"role": "assistant", "content": output_text})
                continue

            output_text, doc_context = self._handle_for_each_page_command(user_input, active_doc)
            if output_text is not None:
                if doc_context: 
                    last_doc_context = doc_context
                    last_global_context, last_global_query = "", ""
                if output_text.startswith(Style.RED) or output_text.startswith(Style.YELLOW): print(output_text)
                conversation_history.append({"role": "assistant", "content": output_text})
                continue
            
            output_text = self._handle_instruct_command(user_input)
            if output_text is not None:
                conversation_history.append({"role": "assistant", "content": output_text})
                continue
            
            find_match = re.search(r'^(?:find|search for)\s+(.+)', user_input, re.IGNORECASE)
            load_match = re.search(r'^(?:load|open|get)\s+(?:doc|document)?\s*(?:#|id:?)?\s*(\S+)', user_input, re.IGNORECASE)

            if find_match or load_match:
                conversation_history.pop()
                if find_match:
                    query = find_match.group(1).strip()
                    results = self.db.execute("SELECT id, relative_path FROM documents WHERE relative_path LIKE ?", (f"%{query}%",)).fetchall()
                    if not results: print(f"{Style.YELLOW}[INFO] No docs found matching '{query}'.{Style.END}")
                    else: print(f"{Style.GREEN}[INFO] Found {len(results)} doc(s):{Style.END}"); [print(f"  {Style.BOLD}#{doc['id']}:{Style.END} {doc['relative_path']}") for doc in results]
                
                if load_match:
                    identifier = load_match.group(1).strip()
                    new_doc_record = None
                    try: doc_id = int(identifier); new_doc_record = self.db.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
                    except (ValueError, TypeError): new_doc_record = self.db.execute("SELECT * FROM documents WHERE relative_path LIKE ?", (f"%{identifier}%",)).fetchone()
                    
                    if not new_doc_record: print(f"{Style.RED}[FAIL] Document '{identifier}' not found.{Style.END}")
                    else: 
                        active_doc = dict(new_doc_record)
                        conversation_history = []
                        print(f"{Style.GREEN}[OK] Loaded Doc ID {active_doc['id']}: '{active_doc['relative_path']}'{Style.END}")
                continue
            
            context_to_use = None
            if active_doc and last_doc_context:
                print(f"{Style.CYAN}[INFO] Answering based on Doc #{active_doc['id']} context.{Style.END}", flush=True)
                context_to_use = last_doc_context
            elif not active_doc and last_global_context:
                print(f"{Style.CYAN}[INFO] Answering based on last search for '{last_global_query}'.{Style.END}", flush=True)
                context_to_use = last_global_context
            
            if context_to_use:
                print(f"{Style.CYAN}[Thinking... Answering your follow-up]{Style.END}")
                messages = [ {"role": "system", "content": self.conversation_prompt}, {"role": "user", "content": f"Context:\n\n{context_to_use}"}, {"role": "assistant", "content": "I have the context. What is your question?"}, {"role": "user", "content": user_input} ]
                print(f"{Style.GREEN}", end=''); final_answer = self.get_assistant_response_stream(messages); print(f"{Style.END}", end='')
                conversation_history.append({"role": "assistant", "content": final_answer})
                continue

            if not active_doc:
                print(f"{Style.YELLOW}Please load a document (e.g., `load 1`) or use a `research:` command.{Style.END}")
                conversation_history.pop()
                continue
            
            print(f"{Style.CYAN}[Thinking... Deciding which tool to use]{Style.END}")
            router_messages = [{"role": "system", "content": self.router_prompt.format(doc_id=active_doc['id'], doc_path=active_doc['relative_path'], seen_pages=[])}, *conversation_history[-5:]]
            tool_choice_response = self.get_assistant_response(router_messages, use_json=True)
            try:
                tool_call_data = json.loads(tool_choice_response)
                tool_name, tool_args = None, {}
                if isinstance(tool_call_data, dict):
                    if "tool_call" in tool_call_data: tool_call_data = tool_call_data["tool_call"]
                    for k in ["tool_name", "name", "tool"]:
                        if k in tool_call_data: tool_name = tool_call_data.pop(k); break
                    tool_args = tool_call_data.get("arguments", tool_call_data)
                
                if tool_name not in self.available_tools or not isinstance(tool_args, dict): raise ValueError("Parsed JSON is not a valid tool call.")
                
                tool_func = self.available_tools[tool_name]
                if 'doc_id' in tool_func.__code__.co_varnames: tool_args['doc_id'] = active_doc['id']
                
                print(f"{Style.YELLOW}[Using Tool] {tool_name}({', '.join(f'{k}={v}' for k, v in tool_args.items())}){Style.END}")
                tool_output_raw = tool_func(**tool_args)
                print(f"{Style.MAGENTA}[Tool Output Received]{Style.END}")
                
                print(f"{Style.CYAN}[Thinking... Generating final answer]{Style.END}")
                doc_url = f"{REDLEAF_BASE_URL}/document/{active_doc['id']}"
                doc_info_header = f"[Active Document Information]\n[Doc. {active_doc['id']}]: {active_doc['relative_path']}\nURL: {doc_url}\n---\n\n"
                final_context = doc_info_header + tool_output_raw
                writer_messages = [{"role": "system", "content": self.writer_prompt}, {"role": "user", "content": f"My question was: '{user_input}'"}, {"role": "assistant", "content": f"Context:\n\n{final_context}"}]
                
                print(f"\n{Style.GREEN}", end=''); final_answer = self.get_assistant_response_stream(writer_messages); print(f"{Style.END}", end='')
                conversation_history.append({"role": "assistant", "content": final_answer})

            except (json.JSONDecodeError, TypeError, ValueError, KeyError) as e:
                print(f"{Style.RED}[Error] Could not execute AI action. Raw response: {tool_choice_response}{Style.END}\nDetails: {e}")
                conversation_history.pop()