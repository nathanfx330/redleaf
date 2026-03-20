# --- File: ./curator_chat.py ---
import sys
import json
import argparse
import sqlite3
import re
import html
from datetime import datetime
from pathlib import Path
from collections import defaultdict
import ollama

# --- 1. Project Path Setup ---
project_dir = Path(__file__).resolve().parent
sys.path.append(str(project_dir))

# --- 2. Redleaf Imports ---
from project import create_app
from project.database import get_db
from project.config import REASONING_MODEL, REDLEAF_BASE_URL
from project.assistant_core import (
    _internal_fts_search,
    _internal_semantic_search,
    read_specific_pages,
    Style
)

# --- 3. PROMPTS ---

THINKING_SYSTEM_PROMPT = """You are the cognitive layer of 'Curator'.

INPUTS:
1. USER INPUT
2. LAST ASSISTANT MESSAGE
3. TERRITORY MAP

DECISION LOGIC:
1. **Definition Check:** If the Last Message asked "What is this collection?", the User Input is the definition. Intent: "Clarify". Search: "None".
2. **Selection Check:** If user says "1", "2", or "The first one", map it to the options in Last Message. Intent: "Retrieve". Search: Specific keyword for that option.
3. **Search Check:** Only search if the user asks for a specific person, date, or event.

Return JSON:
{
    "intent": "Clarify" OR "Retrieve",
    "search": "None" OR "keyword query"
}
"""

RESPONDER_SYSTEM_PROMPT = """You are Curator, an executive research partner.

**Protocol:**
1. **The First Turn:** If the user defines the collection, acknowledge it and immediately offer **3 numbered paths** based on the Territory Map.
2. **Summarize:** Synthesize findings into a conversational summary. No block quotes.
3. **MANDATORY CITATIONS:** You MUST end sentences with citations like `[Doc 12]`. 
   - **Crucial:** These tags are used by the system to generate links.
   - **Voice Rule:** Do not explicitly say "Document 12". Just attach the tag `[Doc 12]` at the end of the statement so the system can parse it.

**Tone:** Professional, structured, insightful.
"""

def get_system_briefing(db) -> str:
    """Generates the Territory Map."""
    print(f"{Style.CYAN}[System] Mapping territory...{Style.END}")
    try:
        total_docs = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        
        top_people = db.execute("""
            SELECT entity_text FROM browse_cache 
            WHERE entity_label = 'PERSON' 
            ORDER BY appearance_count DESC LIMIT 5
        """).fetchall()
        
        top_orgs = db.execute("""
            SELECT entity_text FROM browse_cache 
            WHERE entity_label = 'ORG' 
            ORDER BY appearance_count DESC LIMIT 5
        """).fetchall()

        briefing = (
            f"TERRITORY MAP (ENTITIES IN DB):\n"
            f"- Total Documents: {total_docs}\n"
            f"- Prominent People: {', '.join([r['entity_text'] for r in top_people])}\n"
            f"- Prominent Groups: {', '.join([r['entity_text'] for r in top_orgs])}\n"
        )
        return briefing
    except Exception:
        return "TERRITORY MAP: Connection established. No high-level metadata available."

def perform_hybrid_search(db, query: str, limit: int = 5) -> list:
    print(f"{Style.CYAN}   [Exploring] Searching for: '{query}'...{Style.END}")
    fts_hits = _internal_fts_search(db, query, limit=limit * 2)
    sem_hits = _internal_semantic_search(db, query, limit=limit * 2)

    rrf_scores = defaultdict(float)
    k = 60
    for rank, source in enumerate(fts_hits): rrf_scores[(source['doc_id'], source['page_number'])] += 1 / (k + rank + 1)
    for rank, source in enumerate(sem_hits): rrf_scores[(source['doc_id'], source['page_number'])] += 1 / (k + rank + 1)
    
    sorted_keys = sorted(rrf_scores.keys(), key=lambda s: rrf_scores[s], reverse=True)
    return [{'doc_id': d, 'page_number': p} for d, p in sorted_keys[:limit]]

# --- 4. The Link Generator & Parser ---

def parse_for_mobile(raw_response):
    """
    Splits the AI's raw output into 'Voice Text' (cleaned) and 'Data Links'.
    This simulates what your future Flask app will send to the phone.
    """
    # 1. Extract IDs using Regex (Matches [Doc 12], [Doc: 12], (Doc 12))
    ids = set(re.findall(r'(?:Doc|Document)[:.]?\s*(\d+)', raw_response, re.IGNORECASE))
    
    # 2. Create Voice-Clean Text
    # Removes the tags like [Doc 12] so the TTS doesn't read them out.
    clean_text = re.sub(r'\[(?:Doc|Document)[:.]?\s*\d+\]', '', raw_response, flags=re.IGNORECASE)
    # Cleanup extra spaces left behind
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
    
    return clean_text, ids

def generate_clickable_links(db, doc_ids_set):
    """Prints clickable URLs for the CLI user."""
    if not doc_ids_set: return

    ids_list = list(doc_ids_set)
    placeholders = ','.join('?' for _ in ids_list)
    rows = db.execute(f"SELECT id, relative_path FROM documents WHERE id IN ({placeholders})", ids_list).fetchall()
    
    print(f"\n{Style.CYAN}   🔗 RELEVANT DOCUMENTS:{Style.END}")
    for row in rows:
        url = f"{REDLEAF_BASE_URL}/document/{row['id']}"
        print(f"   {Style.BOLD}[Doc {row['id']}]{Style.END} {row['relative_path']}")
        print(f"   {Style.BLUE}{url}{Style.END}")
    print()

def handle_print_command(chat_history):
    if not chat_history:
        print(f"{Style.YELLOW}Curator:{Style.END} Log is empty.")
        return

    chats_dir = project_dir / "chats"
    chats_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filepath = chats_dir / f"curator_session_{timestamp}.html"

    html_body = ""
    for entry in chat_history:
        role_class = "user-entry" if entry['role'] == 'user' else "curator-entry"
        role_name = "USER" if entry['role'] == 'user' else "CURATOR"
        content = html.escape(entry['content']).replace('\n', '<br>')
        html_body += f'<div class="entry {role_class}"><div class="header">{role_name}</div><div class="content">{content}</div></div>\n'

    full_html = f"""<!DOCTYPE html><html><head><title>Curator Session {timestamp}</title><style>body{{background-color:#121212;color:#E8E8EA;font-family:sans-serif;padding:20px;max-width:900px;margin:auto}}.entry{{margin-bottom:20px;padding-left:15px;border-left:3px solid #333}}.user-entry{{border-color:#828290}}.curator-entry{{border-color:#4caf50}}.header{{font-size:0.8em;color:#888;font-weight:bold;margin-bottom:5px}}.content{{line-height:1.6;white-space:pre-wrap}}a{{color:#82aaff}}</style></head><body><h1>Curator Session Log</h1><p style="color:#888">{timestamp}</p><hr style="border-color:#333">{html_body}</body></html>"""
    
    with open(filepath, 'w', encoding='utf-8') as f: f.write(full_html)
    print(f"{Style.GREEN}Curator:{Style.END} Session exported to: {filepath}")

def handle_help_command():
    print(f"\n{Style.BOLD}Available Commands:{Style.END}")
    print(f"  {Style.CYAN}/print{Style.END}      - Export chat history to HTML.")
    print(f"  {Style.CYAN}/scratchpad{Style.END} - View internal memory.")
    print(f"  {Style.CYAN}/quit{Style.END}       - Exit.")
    print()

# --- 5. Main Loop ---

def main():
    parser = argparse.ArgumentParser(description="Redleaf Curator Chat")
    parser.add_argument('--model', type=str, help="Override the AI model")
    args = parser.parse_args()

    app = create_app(start_background_thread=False)
    
    with app.app_context():
        model_to_use = args.model if args.model else REASONING_MODEL
        db = get_db()

        print(f"{Style.RED}{Style.BOLD}=== Redleaf Curator ==={Style.END}")
        print(f"{Style.MAGENTA}Model: {model_to_use}{Style.END}")
        
        # Initialize Memory
        scratchpad = get_system_briefing(db)
        chat_history = [] 
        
        # --- THE FORCED OPENING ---
        last_assistant_message = "I have mapped the territory. Before we begin, how would you describe this collection? What is the subject matter?"

        print(f"\n{last_assistant_message} (Type '/help' for commands)\n")

        while True:
            try:
                user_input = input(f"{Style.BOLD}You > {Style.END}").strip()
                if not user_input: continue
                
                # --- COMMAND HANDLING ---
                if user_input.lower() in ['/quit', 'exit']: break
                if user_input.lower() == '/help': handle_help_command(); continue
                if user_input.lower() == '/print': handle_print_command(chat_history); continue
                if user_input.lower() == '/scratchpad': print(f"\n{Style.YELLOW}--- Context ---{Style.END}\n{scratchpad}\n{Style.YELLOW}---------------{Style.END}\n"); continue

                # 1. THINK
                print(f"{Style.BLUE}[Curator is thinking...]{Style.END}", end='\r')
                
                think_context = (
                    f"TERRITORY MAP/MEMORY:\n{scratchpad}\n\n"
                    f"LAST ASSISTANT MESSAGE:\n\"{last_assistant_message}\"\n\n"
                    f"USER INPUT: \"{user_input}\""
                )
                
                think_msg = [
                    {"role": "system", "content": THINKING_SYSTEM_PROMPT},
                    {"role": "user", "content": think_context}
                ]
                try:
                    resp = ollama.chat(model=model_to_use, messages=think_msg, format='json')
                    decision = json.loads(resp['message']['content'])
                except:
                    decision = {"intent": "Clarify", "search": "None"}

                search_query = decision.get('search', 'None')
                print(f"                                      \r", end='')

                # 2. SEARCH
                new_knowledge = ""
                if search_query and search_query.lower() != "none":
                    results = perform_hybrid_search(db, search_query)
                    if results:
                        new_knowledge = read_specific_pages(db, results)
                        print(f"{Style.GREEN}   -> Extracted evidence from {len(results)} docs.{Style.END}")
                    else:
                        print(f"{Style.YELLOW}   -> No specific documents found for '{search_query}'.{Style.END}")

                # 3. LEARN
                if new_knowledge:
                    learn_msg = [
                        {"role": "system", "content": "You are a Research Analyst. Update the Scratchpad with specific facts from the new evidence. PRESERVE [Doc ID] citations."},
                        {"role": "user", "content": f"OLD SCRATCHPAD:\n{scratchpad}\n\nNEW EVIDENCE:\n{new_knowledge}"}
                    ]
                    learn_resp = ollama.chat(model=model_to_use, messages=learn_msg)
                    scratchpad = learn_resp['message']['content']

                # 4. RESPOND
                respond_msg = [
                    {"role": "system", "content": RESPONDER_SYSTEM_PROMPT},
                    {"role": "system", "content": f"CONTEXT/MEMORY:\n{scratchpad}"}
                ]
                respond_msg.extend(chat_history[-4:])
                respond_msg.append({"role": "user", "content": user_input})

                print(f"{Style.GREEN}Curator > {Style.END}", end="", flush=True)
                stream = ollama.chat(model=model_to_use, messages=respond_msg, stream=True)
                
                full_resp = ""
                for chunk in stream:
                    content = chunk['message']['content']
                    print(content, end="", flush=True)
                    full_resp += content
                print("\n")

                # --- 5. MOBILE APP SIMULATION (PARSING) ---
                voice_text, cited_ids = parse_for_mobile(full_resp)
                
                if cited_ids:
                    # In CLI mode, we show links. In App mode, these are buttons.
                    generate_clickable_links(db, cited_ids)
                    
                    # --- DEBUG PREVIEW FOR YOU (Future API Proof) ---
                    # print(f"\n{Style.MAGENTA}[📱 Mobile App Payload Preview]{Style.END}")
                    # print(f"Audio Output: \"{voice_text}\"")
                    # print(f"Data Payload: {list(cited_ids)}")
                    # -----------------------------------------------

                # Update State
                chat_history.append({"role": "user", "content": user_input})
                chat_history.append({"role": "assistant", "content": full_resp})
                last_assistant_message = full_resp

            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"\n{Style.RED}Error: {e}{Style.END}")

if __name__ == "__main__":
    main()