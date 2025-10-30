# --- File: ./project/assistant_core.py (DEFINITIVELY FIXED - FINAL) ---
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

WRITER_PROMPT = """You are a factual reporting agent. Your purpose is to synthesize a helpful answer to the user's instruction using ONLY the provided "Tool Output / Context".

Your response MUST follow this two-part structure precisely:
1.  **Summary:** Write a concise, high-level abstract summary that directly addresses the user's instruction.
    - Base your summary *exclusively* on the text in the context block.
    - **Inline Citations:**
        - If context is from `--- CONTEXT from Document #XX (...), Page YY ---`, you MUST cite with the short format `[Doc. XX/p. YY]`.
        - If the document information is not available, cite with `[p. XX]`.
2.  **Bibliography:** After the summary, add a separator (`---`), a heading `Sources Cited:`, and then list every unique document you cited.
    - For each document, you MUST provide its ID, full filename, and the URL exactly as provided in the context.
    - Format: `[Doc. XX]: filename.pdf (URL)`

**CRITICAL:** Do NOT add any information not present in the context. If the context is empty or irrelevant, your ONLY response is: "The provided text excerpts do not contain information to answer that question."
"""

CONVERSATION_PROMPT = """You are a helpful and conversational AI assistant for the Redleaf project. Your goal is to answer the user's questions based on the document context provided. Synthesize information in your own words and explain the key takeaways. Address the user directly and be engaging."""

BATCH_CONVERSATION_PROMPT = """You are a helpful and conversational AI assistant. You have just completed a "for each page" batch operation, analyzing and reporting on several pages individually for the user.

Your task is to now provide a brief, high-level, and conversational summary of the key themes or topics you observed across all the pages you just processed. Conclude by inviting the user to ask follow-up questions about the content.

Keep your summary concise (2-4 sentences) and your tone helpful and engaging. Address the user directly.
"""

# --- Core Search & Data Retrieval Functions ---
def _internal_fts_search(db, query: str, limit: int = 20) -> List[Dict]:
    sanitized = query.replace('"', '""'); fts_query = f'"{sanitized}"'
    sql = "SELECT ci.doc_id, ci.page_number FROM content_index ci WHERE ci.content_index MATCH ? ORDER BY rank LIMIT ?"
    return [dict(r) for r in db.execute(sql, [fts_query, limit]).fetchall()]

def _internal_semantic_search(db, query: str, limit: int = 20) -> List[Dict]:
    try:
        print(f"{Style.CYAN}[INFO] Generating embedding for query... (This may take a moment on first run){Style.END}")
        query_embedding_response = ollama.embeddings(model=EMBEDDING_MODEL, prompt=query)
        query_vector = np.array(query_embedding_response['embedding'], dtype=np.float32)
    except Exception as e:
        print(f"{Style.RED}[ERROR] Could not generate query embedding: {e}{Style.END}")
        return []

    print(f"{Style.CYAN}[INFO] Performing vector similarity search...{Style.END}")
    all_chunks = db.execute("SELECT doc_id, page_number, embedding FROM embedding_chunks").fetchall()
    if not all_chunks:
        print(f"{Style.YELLOW}[WARN] No embeddings found in the database to search against.{Style.END}")
        return []

    chunk_embeddings = np.array([np.frombuffer(chunk['embedding'], dtype=np.float32) for chunk in all_chunks])
    
    norm_chunk_embeddings = chunk_embeddings / np.linalg.norm(chunk_embeddings, axis=1, keepdims=True)
    norm_query_vector = query_vector / np.linalg.norm(query_vector)
    similarities = np.dot(norm_chunk_embeddings, norm_query_vector)
    
    k = min(limit, len(similarities))
    if k <= 0: return []
    top_k_indices = np.argpartition(similarities, -k)[-k:]
    sorted_top_k_indices = top_k_indices[np.argsort(similarities[top_k_indices])][::-1]

    unique_sources = set()
    top_results = []
    for index in sorted_top_k_indices:
        chunk = all_chunks[index]
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

def get_page_content(doc_id: int, page_number: int) -> Tuple[str, Dict]:
    db = get_db()
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
                self.user = user
                print(f"\n{Style.GREEN}Welcome, {self.user['username']}!{Style.END}")
                print(f"  Reasoning Model: {Style.BLUE}{self.reasoning_model}{Style.END}")
                print(f"  Embedding Model: {Style.BLUE}{self.embedding_model}{Style.END}")
                self._print_help()
                return True
            else:
                print(f"{Style.RED}[FAIL] Invalid credentials.{Style.END}\n")
        return False

    def _print_help(self):
        print(f"\n{Style.BOLD}Redleaf AI Assistant Commands:{Style.END}")
        print(f"  {Style.GREEN}find [query]{Style.END}            - Find documents by path without loading them.")
        print(f"  {Style.GREEN}load [doc_id|path]{Style.END}   - Load a specific document for focused questions.")
        print(f"  {Style.GREEN}search: [q]{Style.END} - Perform {self.search_strategy} search and summarize top results.")
        print(f"  {Style.GREEN}id:[id] + page:[p-p] + [instr]{Style.END} - Run instruction on a specific page range.")
        print(f"    ↳ e.g., `id:1 + page:1-2 + summarize`")
        print(f"  {Style.GREEN}for each page in [range] + [instr]{Style.END} - Run instruction on each page in a range.")
        print(f"    ↳ e.g., `for each page in 5-7 + list names`")
        print(f"  {Style.GREEN}for each page + [instr]{Style.END} - Run instruction on all pages of the loaded doc.")
        print(f"  {Style.GREEN}search: [q] + [instr] + results:[N]{Style.END} - Override the number of search results.")
        print(f"    ↳ e.g., `search: example + for each + summarize + results:30`")
        print(f"  {Style.GREEN}/print{Style.END}               - Export the current chat session to an HTML file.")
        print(f"  {Style.GREEN}/help{Style.END}                - Show this help message.")
        print(f"  {Style.GREEN}/clear{Style.END}               - Unload doc and return to the global 'All Docs' view.")
        print(f"  {Style.GREEN}/exit{Style.END}                - Exit the assistant.")
    
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
                if token:
                    print(token, end='', flush=True)
                    full_response += token
        except Exception as e:
            error_message = f"Error contacting Ollama: {e}"
            print(error_message)
            return error_message
        
        print()
        return full_response

    def _ansi_to_html(self, text: str) -> str:
        escaped_text = html.escape(text)
        replacements = {
            Style.BLUE: '<span class="blue">', Style.GREEN: '<span class="green">',
            Style.YELLOW: '<span class="yellow">', Style.RED: '<span class="red">',
            Style.BOLD: '<span class="bold">', Style.MAGENTA: '<span class="magenta">',
            Style.CYAN: '<span class="cyan">', Style.END: '</span>',
        }
        for ansi, html_tag in replacements.items():
            escaped_text = escaped_text.replace(html.escape(ansi), html_tag)
        
        url_pattern = re.compile(r'(https?://[^\s<>()"\'`]+)')
        final_html = url_pattern.sub(r'<a href="\1" target="_blank">\1</a>', escaped_text)
        
        return final_html

    def _generate_html_from_history(self, conversation_history, active_doc):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        doc_info = f"Doc #{active_doc['id']}: {active_doc['relative_path']}" if active_doc else "All Docs"
        chat_body_html = ""
        for entry in conversation_history:
            if entry['role'] == 'user':
                chat_body_html += f'<div class="entry user-entry"><pre><strong>({html.escape(doc_info)}) ></strong> {html.escape(entry["content"])}</pre></div>\n'
            elif entry['role'] == 'assistant':
                formatted_content = self._ansi_to_html(entry['content'])
                chat_body_html += f'<div class="entry assistant-entry"><pre>{formatted_content}</pre></div>\n'
        
        html_template = f"""
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>Redleaf Chat Export - {timestamp}</title><style>body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.6; margin: 0; padding: 20px; background-color: #121212; color: #E8E8EA; }} a {{ color: #82aaff; text-decoration: none; }} a:hover {{ text-decoration: underline; }} .container {{ max-width: 1000px; margin: auto; background-color: #1E1E24; border: 1px solid #383842; border-radius: 8px; padding: 25px; }} h1 {{ border-bottom: 1px solid #383842; padding-bottom: 10px; }} .meta-info {{ color: #828290; }} .entry {{ margin-bottom: 1rem; }} pre {{ white-space: pre-wrap; word-wrap: break-word; margin: 0; font-family: Consolas, monospace; }} .assistant-entry pre {{ color: #92cc94; }} .bold {{ font-weight: bold; }} .blue {{ color: #82aaff; }} .green {{ color: #92cc94; }} .yellow {{ color: #fdea9b; }} .red {{ color: #ff8282; }} .magenta {{ color: #c58af9; }} .cyan {{ color: #7fdbca; }}</style></head>
<body><div class="container"><h1>Redleaf Assistant Chat Log</h1><p class="meta-info">Exported on: {timestamp}<br>User: {self.user['username']}</p><hr style="border-color: #383842;">{chat_body_html}</div></body></html>"""
        return html_template

    def _export_chat_to_html(self, conversation_history, active_doc):
        if not conversation_history:
            print(f"{Style.YELLOW}[WARN] Chat history is empty. Nothing to print.{Style.END}")
            return
        chats_dir = project_dir / "chats"
        chats_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"chat_export_{timestamp}.html"
        filepath = chats_dir / filename
        print(f"{Style.CYAN}[INFO] Generating HTML for chat export...{Style.END}")
        html_content = self._generate_html_from_history(conversation_history, active_doc)
        try:
            with open(filepath, 'w', encoding='utf-8') as f: f.write(html_content)
            print(f"{Style.GREEN}[SUCCESS] Chat history exported to: {filepath.resolve()}{Style.END}")
        except IOError as e:
            print(f"{Style.RED}[ERROR] Could not write to file: {e}{Style.END}")

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
        final_sources = [{'doc_id': doc_id, 'page_number': page_num} for doc_id, page_num in sorted_sources]
        
        sources = final_sources[:limit]
        if not sources:
            return f"{Style.YELLOW}[INFO] No documents found for '{search_query}'.{Style.END}", None

        combined_context = read_specific_pages(self.db, sources=sources)
        
        # ===================================================================
        # === START: THE DEFINITIVE FIX FOR 'SEARCH + FOR EACH' =============
        # ===================================================================
        if "for each" in instruction_string.lower():
            match = re.search(r'for each\s*\+?\s*(.+)', instruction_string, re.IGNORECASE)
            if match:
                for_each_instruction = match.group(1).strip()
                grouped_sources = defaultdict(list)
                for source in sources: grouped_sources[source['doc_id']].append(source['page_number'])
                
                full_batch_output = []
                info_header = f"{Style.CYAN}[INFO] Found results in {len(grouped_sources)} documents. Processing each document's relevant pages...{Style.END}"
                print(info_header)
                full_batch_output.append(info_header)

                # This is the CORRECT loop: it iterates through DOCUMENTS
                for doc_id, page_numbers in grouped_sources.items():
                    doc_info = self.db.execute("SELECT relative_path FROM documents WHERE id = ?", (doc_id,)).fetchone()
                    
                    doc_header = f"\n{Style.BOLD}--- Analyzing Document #{doc_id} ({doc_info['relative_path']}) ---{Style.END}"
                    print(doc_header, flush=True)
                    full_batch_output.append(doc_header)

                    # This is CORRECT: it reads all relevant pages for THIS document at once
                    loop_context = read_specific_pages(self.db, sources=[{'doc_id': doc_id, 'page_number': p} for p in page_numbers])
                    
                    if not loop_context.strip() or "[ERROR]" in loop_context:
                         no_text_msg = f"{Style.YELLOW}[SKIP] Could not read relevant pages for this document.{Style.END}"
                         print(no_text_msg, flush=True)
                         full_batch_output.append(no_text_msg)
                         continue

                    print(f"{Style.CYAN}[Thinking... Fulfilling: '{for_each_instruction}' for Doc #{doc_id}]{Style.END}", flush=True)
                    messages = [{"role": "system", "content": self.writer_prompt}, {"role": "user", "content": for_each_instruction}, {"role": "assistant", "content": f"Context:\n\n{loop_context}"}]
                    
                    print(f"\n{Style.GREEN}", end='')
                    final_answer = self.get_assistant_response_stream(messages)
                    print(f"{Style.END}", end='')
                    
                    full_batch_output.append(final_answer)

                final_summary = f"\n{Style.BOLD}--- Batch processing complete. ---{Style.END}\n"
                final_summary += f"{Style.MAGENTA}{Style.BOLD}[Session Active] The combined content from all {len(grouped_sources)} documents is now in memory.{Style.END}"
                print(final_summary)
                full_batch_output.append(final_summary)
                
                return "\n".join(full_batch_output), (search_query, combined_context)
            else:
                return f"{Style.RED}[ERROR] Malformed 'for each' command.{Style.END}", None
        # ===================================================================
        # === END: THE DEFINITIVE FIX =====================================
        # ===================================================================
        else:
            writer_instruction = instruction_string if instruction_string else f"Give a high-level abstract summary about '{search_query}'."
            print(f"{Style.CYAN}[Thinking... Fulfilling: '{writer_instruction}']{Style.END}\n", flush=True)
            messages = [{"role": "system", "content": self.writer_prompt}, {"role": "user", "content": writer_instruction}, {"role": "assistant", "content": f"Context:\n\n{combined_context}"}]
            print(f"{Style.GREEN}", end='')
            final_answer = self.get_assistant_response_stream(messages)
            print(f"{Style.END}", end='')
            return final_answer, (search_query, combined_context)

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

        print(f"{Style.CYAN}[INFO] Fetching content from Doc ID {doc_id} (Pages: {page_range_str})...{Style.END}\n", flush=True)
        context_text = extract_text_for_copying(DOCUMENTS_DIR / doc['relative_path'], doc['file_type'], start_page=start_page, end_page=end_page)
        if not context_text.strip(): return f"{Style.YELLOW}[WARN] No text content found on specified pages.{Style.END}"
        
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
            return f"{Style.RED}[ERROR] A document must be loaded to use this command. Use `load [doc_id]` first.{Style.END}", None

        page_range_str, instruction = page_match.groups()
        doc_id = active_doc['id']
        total_page_count = active_doc.get('page_count')

        start_page, end_page = 1, total_page_count

        if page_range_str:
            try:
                if '-' in page_range_str:
                    start_page, end_page = map(int, page_range_str.split('-'))
                else:
                    start_page = end_page = int(page_range_str)
                
                if start_page > end_page or start_page < 1 or (total_page_count and end_page > total_page_count):
                    return f"{Style.RED}[ERROR] Invalid page range. Please provide a range within 1-{total_page_count}.{Style.END}", None
            except ValueError:
                return f"{Style.RED}[ERROR] Invalid page number format in range.{Style.END}", None
        
        if not total_page_count or total_page_count < 1:
            return f"{Style.YELLOW}[INFO] The loaded document has no pages to process.{Style.END}", None

        pages_to_process = range(start_page, end_page + 1)
        full_batch_output = []
        full_document_text = []
        info_header = f"{Style.CYAN}[INFO] Processing pages {start_page}-{end_page} in Document #{doc_id}.{Style.END}"
        print(info_header, flush=True)
        full_batch_output.append(info_header)

        for page_num in pages_to_process:
            page_header = f"\n{Style.BOLD}--- Analyzing Page {page_num}/{end_page} ---{Style.END}"
            print(page_header, flush=True)
            full_batch_output.append(page_header)

            page_text, doc_info = get_page_content(doc_id=doc_id, page_number=page_num)
            
            if not page_text or not doc_info:
                no_text_msg = f"{Style.YELLOW}[SKIP] Page {page_num} contains no text.{Style.END}"
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

        final_summary_header = f"\n{Style.BOLD}--- Batch processing for pages {start_page}-{end_page} complete. ---{Style.END}"
        print(final_summary_header, flush=True)
        full_batch_output.append(final_summary_header)

        doc_url = f"{REDLEAF_BASE_URL}/document/{doc_id}"
        full_doc_context = f"--- CONTEXT from Document #{doc_id} ({active_doc['relative_path']}) URL: {doc_url} ---\n" + "\n\n".join(full_document_text)

        conversation_prompt = f"{Style.MAGENTA}{Style.BOLD}[Session Active] The content from pages {start_page}-{end_page} of Document #{doc_id} is now in memory. You can ask follow-up questions.{Style.END}"
        print(conversation_prompt)
        full_batch_output.append(conversation_prompt)
        
        return "\n".join(full_batch_output), full_doc_context

    def run(self):
        if not self._login(): return
        
        active_doc, conversation_history, seen_pages_for_doc = None, [], set()
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
                elif command == 'clear': active_doc, conversation_history, seen_pages_for_doc, last_global_context, last_global_query, last_doc_context = None, [], set(), "", "", None; print("[OK] Document context cleared.")
                elif command == 'print': self._export_chat_to_html(conversation_history, active_doc)
                else: print(f"{Style.RED}Unknown command: /{command}.{Style.END}")
                continue
            
            conversation_history.append({"role": "user", "content": user_input})
            
            output_text, context_info = self._handle_search_command(user_input)
            if output_text is not None:
                if context_info: last_global_query, last_global_context = context_info; last_doc_context = None
                conversation_history.append({"role": "assistant", "content": output_text})
                continue

            output_text, doc_context = self._handle_for_each_page_command(user_input, active_doc)
            if output_text is not None:
                if doc_context: last_doc_context = doc_context; last_global_context, last_global_query = "", ""
                if output_text.startswith(Style.RED) or output_text.startswith(Style.YELLOW):
                    print(output_text)
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
                    if not results: print(f"{Style.YELLOW}[INFO] No documents found matching '{query}'.{Style.END}")
                    else: print(f"{Style.GREEN}[INFO] Found {len(results)} doc(s):{Style.END}"); [print(f"  {Style.BOLD}#{doc['id']}:{Style.END} {doc['relative_path']}") for doc in results]
                
                if load_match:
                    identifier = load_match.group(1).strip()
                    new_doc_record = None
                    try: 
                        doc_id = int(identifier)
                        new_doc_record = self.db.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
                    except (ValueError, TypeError): 
                        new_doc_record = self.db.execute("SELECT * FROM documents WHERE relative_path LIKE ?", (f"%{identifier}%",)).fetchone()
                    
                    if not new_doc_record: 
                        print(f"{Style.RED}[FAIL] Document '{identifier}' not found.{Style.END}")
                    else: 
                        active_doc = dict(new_doc_record)
                        conversation_history, seen_pages_for_doc, last_doc_context = [], set(), None
                        print(f"{Style.GREEN}[OK] Loaded Doc ID {active_doc['id']}: '{active_doc['relative_path']}'{Style.END}")
                
                continue
            
            context_to_use = None
            if active_doc and last_doc_context:
                print(f"{Style.CYAN}[INFO] Answering based on the context of Document #{active_doc['id']}.{Style.END}", flush=True)
                context_to_use = last_doc_context
            elif not active_doc and last_global_context:
                print(f"{Style.CYAN}[INFO] Answering based on last search for '{last_global_query}'.{Style.END}", flush=True)
                context_to_use = last_global_context
            
            if context_to_use:
                last_user_q = conversation_history[-1]['content']
                
                print(f"{Style.CYAN}[Thinking... Synthesizing answer from loaded context]{Style.END}")
                
                messages = [
                    {"role": "system", "content": self.conversation_prompt},
                    {"role": "user", "content": f"Here is the context from the document(s) we are discussing:\n\n{context_to_use}"},
                    {"role": "assistant", "content": "I have read the context. What is your question?"},
                    {"role": "user", "content": last_user_q}
                ]
                
                print(f"{Style.GREEN}", end='')
                final_answer = self.get_assistant_response_stream(messages)
                print(f"{Style.END}", end='')
                
                conversation_history.append({"role": "assistant", "content": final_answer})
                continue
            
            if not active_doc:
                print(f"{Style.YELLOW}Please load a document (e.g., `load 1`) or use a `search:` command.{Style.END}")
                conversation_history.pop()
                continue

            print(f"{Style.CYAN}[Thinking... Deciding which tool to use]{Style.END}")
            short_history = conversation_history[-5:]
            router_messages = [{"role": "system", "content": self.router_prompt.format(doc_id=active_doc['id'], doc_path=active_doc['relative_path'], seen_pages=sorted(list(seen_pages_for_doc)))}, *short_history]
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
                
                print(f"\n{Style.GREEN}", end='')
                final_answer = self.get_assistant_response_stream(writer_messages)
                print(f"{Style.END}", end='')

                conversation_history.append({"role": "assistant", "content": final_answer})
            except (json.JSONDecodeError, TypeError, ValueError, KeyError) as e:
                print(f"{Style.RED}[Error] Could not execute AI action. Raw response: {tool_choice_response}{Style.END}\nDetails: {e}")
                conversation_history.pop()