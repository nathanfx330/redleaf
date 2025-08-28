# --- File: ./processing_pipeline.py ---
import sqlite3
import hashlib
import traceback
import os
import datetime
import re
from pathlib import Path
from itertools import combinations
from typing import Union

import fitz
import spacy
from bs4 import BeautifulSoup

# --- Configuration ---
DATABASE_FILE = "knowledge_base.db"
DOCUMENTS_DIR = Path("./documents").resolve()
SPACY_MODEL = "en_core_web_lg"

NLP_MODEL = None

def load_spacy_model():
    """Loads the spaCy model into the global variable if not already loaded."""
    global NLP_MODEL
    if NLP_MODEL is None:
        print(f"Loading spaCy model '{SPACY_MODEL}' in process {os.getpid()}...")
        try:
            disabled_pipes = ["lemmatizer"]
            NLP_MODEL = spacy.load(SPACY_MODEL, disable=disabled_pipes)
            print(f"spaCy model loaded successfully in process {os.getpid()}.")
        except OSError:
            print(f"FATAL: spaCy model '{SPACY_MODEL}' not found.")
            print(f"Please run: python -m spacy download {SPACY_MODEL}")
            raise
    return NLP_MODEL

def get_db_conn():
    """Gets a fresh, process-safe database connection."""
    conn = sqlite3.connect(DATABASE_FILE, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn

def update_document_status(db_conn, doc_id, status, message=""):
    """
    Centralized function for quick, non-worker status updates (e.g., 'Queued').
    Manages its own transaction to not interfere with worker processes.
    """
    try:
        cursor = db_conn.cursor()
        cursor.execute("BEGIN")
        cursor.execute(
            "UPDATE documents SET status = ?, status_message = ?, processed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, message, doc_id)
        )
        cursor.execute("COMMIT")
    except Exception as e:
        print(f"CRITICAL: Failed to update status for doc_id {doc_id} to '{status}'. Error: {e}")
        try:
            db_conn.execute("ROLLBACK")
        except Exception:
            pass # Ignore rollback errors

def _paginate_text(text, words_per_page=300):
    """Splits a string of text into pages of roughly N words."""
    words = text.split()
    page_content_map = {}
    for i in range(0, len(words), words_per_page):
        page_number = (i // words_per_page) + 1
        page_content_map[page_number] = " ".join(words[i:i + words_per_page])
    # Handle empty text case
    if not page_content_map:
        page_content_map[1] = ""
    return page_content_map

def extract_text_for_copying(doc_path: Path, file_type: str, start_page=None, end_page=None) -> str:
    """
    Extracts text for copying. For PDFs, it reads the file. For TXT, HTML, and SRT files, it reads
    the paginated content from the database for consistency with the viewer.
    """
    if file_type in ['TXT', 'HTML', 'SRT']:
        conn = None
        try:
            conn = get_db_conn()
            rel_path_str = str(doc_path.relative_to(DOCUMENTS_DIR).as_posix())
            doc_id_row = conn.execute("SELECT id FROM documents WHERE relative_path = ?", (rel_path_str,)).fetchone()
            if not doc_id_row:
                return f"Error: Document not found in database for path {rel_path_str}"
            
            doc_id = doc_id_row['id']
            # For HTML/SRT, we now ignore page numbers as it's all one block
            if file_type in ['HTML', 'SRT']:
                start_page = None
                end_page = None

            query = "SELECT page_content FROM content_index WHERE doc_id = ?"
            params = [doc_id]
            
            if start_page is not None and end_page is not None and start_page <= end_page:
                query += " AND page_number BETWEEN ? AND ?"
                params.extend([start_page, end_page])
            elif start_page is not None:
                query += " AND page_number = ?"
                params.append(start_page)
            
            query += " ORDER BY page_number ASC"
            
            pages = [row['page_content'] for row in conn.execute(query, params).fetchall()]
            return "\n\n".join(pages)

        except Exception as e:
            print(f"Error extracting text from DB for {file_type} file {doc_path}: {e}")
            return f"Error extracting text from database: {e}"
        finally:
            if conn:
                conn.close()

    elif file_type == 'PDF':
        full_text = []
        try:
            doc = fitz.open(doc_path)
            first_page_index = max(0, start_page - 1) if start_page is not None else 0
            last_page_index = doc.page_count - 1
            if end_page is not None:
                last_page_index = min(doc.page_count - 1, end_page - 1)
            elif start_page is not None:
                last_page_index = first_page_index
            if first_page_index > last_page_index:
                return ""
            for page_num in range(first_page_index, last_page_index + 1):
                page = doc.load_page(page_num)
                text = page.get_text("text", sort=True)
                if text:
                    full_text.append(text.strip())
            doc.close()
        except Exception as e:
            print(f"Error extracting text from PDF {doc_path}: {e}")
            return f"Error extracting text: {e}"
        return "\n\n".join(full_text)
    else:
        return f"Cannot extract text from unsupported file type: {file_type}"

def _extract_text_from_pipermail(html_content: str) -> str:
    """Extracts content from a Pipermail HTML page with vertical separation."""
    soup = BeautifulSoup(html_content, 'lxml')
    
    title = soup.find('h1')
    subject = soup.find('b')
    author = soup.find('i')
    body = soup.find('pre')

    parts = []
    if title:
        parts.append(f"List: {title.get_text(strip=True)}")
    if subject:
        parts.append(f"Subject: {subject.get_text(strip=True)}")
    if author:
        parts.append(f"Author: {author.get_text(strip=True)}")
    if body:
        parts.append(f"--- Message Body ---\n{body.get_text()}") # Keep single newline inside body

    return "\n\n".join(parts)

def _extract_text_with_block_separation(html_content: str) -> str:
    """
    Extracts text from generic HTML, preserving block-level separation with double newlines.
    """
    soup = BeautifulSoup(html_content, 'lxml')
    
    for script_or_style in soup(["script", "style"]):
        script_or_style.decompose()

    block_tags = ['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'blockquote', 'pre', 'tr', 'div']
    
    text_blocks = []
    title_tag = soup.find('title')
    if title_tag and title_tag.get_text(strip=True):
        text_blocks.append(f"Title: {title_tag.get_text(strip=True)}")

    for tag in soup.find_all(block_tags):
        text = tag.get_text(separator=' ', strip=True)
        if text:
            text_blocks.append(text)
            
    return "\n\n".join(text_blocks)

def _extract_text_from_srt(srt_content: str) -> str:
    """
    Parses the content of an SRT file, extracting only the dialogue.
    - Ignores sequence numbers and timestamps.
    - Joins multi-line subtitles.
    - Returns a single string with each subtitle on a new line.
    """
    lines = []
    # Split the file into subtitle blocks, which are separated by double newlines.
    for block in srt_content.strip().split('\n\n'):
        parts = block.strip().split('\n')
        if len(parts) >= 3:
            # The first line is the sequence number, the second is the timestamp.
            # All subsequent lines are the dialogue.
            dialogue = " ".join(parts[2:])
            lines.append(dialogue)
    return "\n".join(lines)

def _parse_srt_for_db(srt_content: str) -> list[dict]:
    """Parses SRT content into a list of dicts for database insertion."""
    cues = []
    # This regex is robust against different newline formats and optional formatting tags
    cue_pattern = re.compile(
        r'(\d+)\s*(\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3})\s*(.*?)\n\n',
        re.DOTALL
    )
    for match in cue_pattern.finditer(srt_content + '\n\n'): # Append newlines to catch last cue
        sequence, timestamp, dialogue_block = match.groups()
        # Clean the dialogue: remove HTML-like tags and join lines
        clean_dialogue = re.sub(r'<[^>]+>', '', dialogue_block).replace('\n', ' ').strip()
        cues.append({
            'sequence': int(sequence),
            'timestamp': timestamp.strip(),
            'dialogue': clean_dialogue
        })
    return cues

def _get_srt_duration(srt_content: str) -> Union[int, None]:
    """Calculates the total duration in seconds from the last timestamp in an SRT file."""
    # Find all timestamps, then focus on the last one's end time.
    timestamps = re.findall(r'\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})', srt_content)
    if not timestamps:
        return None
    
    last_timestamp = timestamps[-1]
    try:
        h, m, s, ms = map(int, re.split('[:,]', last_timestamp))
        return h * 3600 + m * 60 + s + ms / 1000
    except (ValueError, IndexError):
        return None

def _extract_text_from_pdf_doc(doc: fitz.Document) -> dict:
    page_content_map = {}
    try:
        for page_num, page in enumerate(doc, 1):
            text = page.get_text("text", sort=True)
            if text:
                page_content_map[page_num] = text.strip()
    except Exception as e:
        print(f"Error extracting text from PDF: {e}")
    return page_content_map

def _extract_data_from_pages(page_content_map: dict) -> dict:
    nlp = load_spacy_model()
    data_to_store = {
        "entities": set(),
        "appearances": set(),
        "relationships": [],
        "content": []
    }
    for page_num, page_text in page_content_map.items():
        data_to_store["content"].append((page_num, page_text))
        if not page_text or len(page_text) > nlp.max_length:
            continue
        doc_nlp = nlp(page_text)
        for ent in doc_nlp.ents:
            entity_tuple = (ent.text.strip(), ent.label_)
            if entity_tuple[0]:
                data_to_store["entities"].add(entity_tuple)
                data_to_store["appearances"].add((entity_tuple, page_num))
        for sent in doc_nlp.sents:
            unique_ents = list(dict.fromkeys(sent.ents))
            if len(unique_ents) < 2:
                continue
            for ent1, ent2 in combinations(unique_ents, 2):
                start = min(ent1.end_char, ent2.end_char)
                end = max(ent1.start_char, ent2.start_char)
                if end > start and (end - start) < 75:
                    relationship_phrase = ' '.join(page_text[start:end].strip().split())
                    if relationship_phrase:
                        subject_tuple = (ent1.text.strip(), ent1.label_) if ent1.start_char < ent2.start_char else (ent2.text.strip(), ent2.label_)
                        object_tuple = (ent2.text.strip(), ent2.label_) if ent1.start_char < ent2.start_char else (ent1.text.strip(), ent1.label_)
                        if subject_tuple[0] and object_tuple[0]:
                             data_to_store["relationships"].append((subject_tuple, object_tuple, relationship_phrase, page_num))
    return data_to_store

def process_document(doc_id):
    """Worker function using the 'collect-then-write' pattern."""
    conn = None
    try:
        conn = get_db_conn()
        cursor = conn.cursor()

        cursor.execute("BEGIN")
        cursor.execute("UPDATE documents SET status = ?, status_message = ? WHERE id = ?", ('Indexing', 'Worker process started...', doc_id))
        cursor.execute("COMMIT")

        cursor.execute("SELECT relative_path, file_type FROM documents WHERE id = ?", (doc_id,))
        doc_info = cursor.fetchone()
        if not doc_info:
            raise ValueError(f"No document found with ID: {doc_id}")

        print(f"--- Worker {os.getpid()} processing Doc ID: {doc_id} (Type: {doc_info['file_type']}) ---")
        full_path = DOCUMENTS_DIR / doc_info['relative_path']
        
        page_content_map, page_count, duration_seconds = {}, 0, None
        extracted_data = {}

        if doc_info['file_type'] == 'SRT':
            try:
                content = full_path.read_text(encoding='utf-8', errors='ignore')
                parsed_cues = _parse_srt_for_db(content)
                duration_seconds = _get_srt_duration(content)
                page_count = len(parsed_cues)
                
                nlp = load_spacy_model()
                extracted_data = {"entities": set(), "appearances": set(), "relationships": [], "content": []}
                full_dialogue_parts = []

                # Pass 1: Extract entities and appearances from each individual cue for accuracy.
                for cue in parsed_cues:
                    cue_text = cue['dialogue']
                    full_dialogue_parts.append(cue_text)
                    if not cue_text or len(cue_text) > nlp.max_length:
                        continue
                    
                    doc_nlp_cue = nlp(cue_text)
                    for ent in doc_nlp_cue.ents:
                        entity_tuple = (ent.text.strip(), ent.label_)
                        if entity_tuple[0]:
                            extracted_data["entities"].add(entity_tuple)
                            extracted_data["appearances"].add((entity_tuple, cue['sequence']))
                
                # For FTS, create one single document with all dialogue. This is correct.
                full_dialogue_text = " ".join(full_dialogue_parts)
                page_content_map = {1: full_dialogue_text}
                extracted_data["content"].append((1, full_dialogue_text))

                # === START OF THE FIX ===
                # Pass 2: Process the full dialogue text to find relationships that span across cues,
                # but correctly attribute them to their source cue.
                if full_dialogue_text and len(full_dialogue_text) <= nlp.max_length:
                    print(f"SRT Worker {os.getpid()}: Analyzing full transcript for relationships...")

                    # Pre-calculate the character start/end boundaries for each cue in the full text.
                    cue_char_boundaries = []
                    current_pos = 0
                    for cue in parsed_cues:
                        dialogue_len = len(cue['dialogue'])
                        cue_char_boundaries.append((current_pos, current_pos + dialogue_len))
                        current_pos += dialogue_len + 1 # Account for the space joiner.

                    doc_nlp_full = nlp(full_dialogue_text)
                    for sent in doc_nlp_full.sents:
                        unique_ents = list(dict.fromkeys(sent.ents))
                        if len(unique_ents) < 2:
                            continue
                        for ent1, ent2 in combinations(unique_ents, 2):
                            start = min(ent1.end_char, ent2.end_char)
                            end = max(ent1.start_char, ent2.start_char)
                            if end > start and (end - start) < 75: # Proximity threshold
                                relationship_phrase = ' '.join(full_dialogue_text[start:end].strip().split())
                                if relationship_phrase:
                                    subject_tuple = (ent1.text.strip(), ent1.label_) if ent1.start_char < ent2.start_char else (ent2.text.strip(), ent2.label_)
                                    object_tuple = (ent2.text.strip(), ent2.label_) if ent1.start_char < ent2.start_char else (ent1.text.strip(), ent1.label_)
                                    
                                    if subject_tuple[0] and object_tuple[0]:
                                        # Find the correct cue number by checking where the relationship starts.
                                        relationship_start_char = min(ent1.start_char, ent2.start_char)
                                        relationship_cue_num = 1 # Default, just in case.
                                        for i, (cue_start, cue_end) in enumerate(cue_char_boundaries):
                                            if relationship_start_char >= cue_start and relationship_start_char < cue_end:
                                                # Use the actual sequence number from the parsed data, not the loop index.
                                                relationship_cue_num = parsed_cues[i]['sequence']
                                                break
                                        
                                        extracted_data["relationships"].append((subject_tuple, object_tuple, relationship_phrase, relationship_cue_num))
                # === END OF THE FIX ===

            except Exception as e:
                cursor.execute("BEGIN; UPDATE documents SET status = ?, status_message = ? WHERE id = ?; COMMIT;", ('Error', f"Could not read/parse SRT file: {e}"[:1000], doc_id))
                return "ERROR_OPENING_FILE"
        else:
            # --- EXISTING LOGIC FOR OTHER FILE TYPES ---
            if doc_info['file_type'] == 'PDF':
                try:
                    with fitz.open(full_path) as pdf_doc:
                        page_count = pdf_doc.page_count
                        page_content_map = _extract_text_from_pdf_doc(pdf_doc)
                except Exception as e:
                    cursor.execute("BEGIN; UPDATE documents SET status = ?, status_message = ? WHERE id = ?; COMMIT;", ('Error', f"Could not open/read PDF: {e}"[:1000], doc_id))
                    return "ERROR_OPENING_FILE"
            elif doc_info['file_type'] == 'TXT':
                try:
                    content = full_path.read_text(encoding='utf-8', errors='ignore')
                    page_content_map = _paginate_text(content.strip())
                    page_count = len(page_content_map)
                except Exception as e:
                    cursor.execute("BEGIN; UPDATE documents SET status = ?, status_message = ? WHERE id = ?; COMMIT;", ('Error', f"Could not read TXT file: {e}"[:1000], doc_id))
                    return "ERROR_OPENING_FILE"
            elif doc_info['file_type'] == 'HTML':
                try:
                    mode_row = cursor.execute("SELECT value FROM app_settings WHERE key = 'html_parsing_mode'").fetchone()
                    parsing_mode = mode_row[0] if mode_row else 'generic'
                    content = full_path.read_text(encoding='utf-8', errors='ignore')
                    
                    extracted_text = ""
                    if parsing_mode == 'pipermail': extracted_text = _extract_text_from_pipermail(content)
                    else: extracted_text = _extract_text_with_block_separation(content)
                    
                    if extracted_text.strip(): page_content_map = {1: extracted_text.strip()}; page_count = 1
                    else: page_content_map = {1: ""}; page_count = 0
                except Exception as e:
                    cursor.execute("BEGIN; UPDATE documents SET status = ?, status_message = ? WHERE id = ?; COMMIT;", ('Error', f"Could not read/parse HTML: {e}"[:1000], doc_id))
                    return "ERROR_OPENING_FILE"
            else:
                cursor.execute("BEGIN; UPDATE documents SET status = ?, status_message = ? WHERE id = ?; COMMIT;", ('Error', f"Unsupported file type: {doc_info['file_type']}", doc_id))
                return "ERROR_UNSUPPORTED_TYPE"
            
            extracted_data = _extract_data_from_pages(page_content_map)

        # --- DATABASE WRITING (Now common for all types) ---
        cursor.execute("BEGIN")
        # Clear old data
        cursor.execute("DELETE FROM srt_cues WHERE doc_id = ?", (doc_id,))
        cursor.execute("UPDATE documents SET page_count = ?, duration_seconds = ? WHERE id = ?", (page_count, duration_seconds, doc_id))
        cursor.execute("DELETE FROM content_index WHERE doc_id = ?", (doc_id,))
        cursor.execute("DELETE FROM entity_appearances WHERE doc_id = ?", (doc_id,))
        cursor.execute("DELETE FROM entity_relationships WHERE doc_id = ?", (doc_id,))

        # Insert new data
        if doc_info['file_type'] == 'SRT' and 'parsed_cues' in locals() and parsed_cues:
            cues_to_insert = [(doc_id, cue['sequence'], cue['timestamp'], cue['dialogue']) for cue in parsed_cues]
            cursor.executemany("INSERT INTO srt_cues (doc_id, sequence, timestamp, dialogue) VALUES (?, ?, ?, ?)", cues_to_insert)

        if extracted_data.get("content"):
            cursor.executemany("INSERT INTO content_index (doc_id, page_number, page_content) VALUES (?, ?, ?)", [(doc_id, pn, pt) for pn, pt in extracted_data["content"]])
        
        if extracted_data.get("entities"):
            entities_list = list(extracted_data["entities"])
            cursor.executemany("INSERT OR IGNORE INTO entities (text, label) VALUES (?, ?)", entities_list)
            
            entity_id_map = {}
            for text, label in entities_list:
                res = cursor.execute("SELECT id FROM entities WHERE text = ? AND label = ?", (text, label)).fetchone()
                if res: entity_id_map[(text, label)] = res['id']

            appearances_to_insert = [(doc_id, entity_id_map[ent_tuple], page_num) for ent_tuple, page_num in extracted_data["appearances"] if ent_tuple in entity_id_map]
            if appearances_to_insert:
                cursor.executemany("INSERT OR IGNORE INTO entity_appearances (doc_id, entity_id, page_number) VALUES (?, ?, ?)", appearances_to_insert)

            relationships_to_insert = [(entity_id_map[subj], entity_id_map[obj], phrase, doc_id, page_num) for subj, obj, phrase, page_num in extracted_data.get("relationships", []) if subj in entity_id_map and obj in entity_id_map]
            if relationships_to_insert:
                cursor.executemany("INSERT INTO entity_relationships (subject_entity_id, object_entity_id, relationship_phrase, doc_id, page_number) VALUES (?, ?, ?, ?, ?)", relationships_to_insert)
            
        cursor.execute("UPDATE documents SET status = ?, status_message = ?, processed_at = CURRENT_TIMESTAMP WHERE id = ?", ('Indexed', 'Processing complete.', doc_id))
        cursor.execute("COMMIT")
        print(f"--- Worker {os.getpid()} finished Doc ID: {doc_id} ---")
        return "SUCCESS"

    except Exception as e:
        print(f"!!! WORKER {os.getpid()} ERROR on Doc ID: {doc_id} !!!")
        print(traceback.format_exc())
        if conn:
            try:
                cursor.execute("ROLLBACK")
                cursor.execute("BEGIN")
                error_message = f"Worker Error: {type(e).__name__}: {e}"
                cursor.execute("UPDATE documents SET status = ?, status_message = ? WHERE id = ?", ('Error', error_message[:1000], doc_id))
                cursor.execute("COMMIT")
            except Exception as rollback_e:
                print(f"CRITICAL: Failed to rollback or update error status for doc {doc_id}. Error: {rollback_e}")
        raise
    finally:
        if conn:
            conn.close()

def discover_and_register_documents():
    """Scans the source directory and registers new or modified files."""
    print(f"--- Scanning for documents in {DOCUMENTS_DIR} ---")
    conn = get_db_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT relative_path, file_hash FROM documents")
        db_files = {row['relative_path']: row['file_hash'] for row in cursor.fetchall()}
        
        registered_count = 0
        supported_patterns = ["*.pdf", "*.txt", "*.html", "*.srt"]
        all_files = []
        for pattern in supported_patterns:
            all_files.extend(DOCUMENTS_DIR.rglob(pattern))
            
        for file_path in all_files:
            if not file_path.is_file(): continue
            
            rel_path_str = str(file_path.relative_to(DOCUMENTS_DIR).as_posix())
            stats = file_path.stat()
            current_size = stats.st_size
            current_mtime = datetime.datetime.fromtimestamp(stats.st_mtime)
            file_type = file_path.suffix[1:].upper()

            hasher = hashlib.md5()
            with open(file_path, 'rb') as f:
                buf = f.read(65536)
                while len(buf) > 0:
                    hasher.update(buf)
                    buf = f.read(65536)
            current_hash_str = hasher.hexdigest()

            if rel_path_str not in db_files or db_files.get(rel_path_str) != current_hash_str:
                print(f"Registering new/modified file: {rel_path_str} (Type: {file_type})")
                
                cursor.execute("BEGIN")
                cursor.execute(
                    """
                    INSERT INTO documents (relative_path, file_hash, file_type, status, status_message, file_size_bytes, file_modified_at, processed_at) 
                    VALUES (?, ?, ?, 'New', 'Ready for processing', ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(relative_path) 
                    DO UPDATE SET file_hash=excluded.file_hash, 
                                  file_type=excluded.file_type,
                                  status='New', 
                                  status_message='File modified, ready for re-processing', 
                                  file_size_bytes=excluded.file_size_bytes,
                                  file_modified_at=excluded.file_modified_at,
                                  processed_at=excluded.processed_at;
                    """, 
                    (rel_path_str, current_hash_str, file_type, current_size, current_mtime)
                )
                cursor.execute("COMMIT")
                registered_count += 1

        print(f"--- Discovery complete. Registered {registered_count} new/modified documents. ---")
        return "SUCCESS"
    except Exception as e:
        print(f"!!! ERROR during document discovery: {e} !!!")
        print(traceback.format_exc())
        try: cursor.execute("ROLLBACK")
        except sqlite3.Error: pass
    finally:
        if conn: conn.close()

def update_browse_cache():
    """Recomputes the aggregated entity data for the Discovery View."""
    print("--- Starting update of Aggregated View Cache ---")
    conn = get_db_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN")
        print("Executing aggregation query...")
        query = """
            SELECT 
                e.id as entity_id, 
                e.text as entity_text, 
                e.label as entity_label, 
                COUNT(DISTINCT ea.doc_id) as document_count,
                COUNT(ea.doc_id) as appearance_count
            FROM entities e 
            JOIN entity_appearances ea ON e.id = ea.entity_id 
            GROUP BY e.id, e.text, e.label
        """
        cursor.execute(query)
        aggregated_data = cursor.fetchall()
        print(f"Found {len(aggregated_data)} unique entities with document counts.")
        print("Updating browse_cache table...")
        
        print("Clearing old cache...")
        cursor.execute("DELETE FROM browse_cache")
        if aggregated_data:
            print(f"Inserting {len(aggregated_data)} rows into the new cache...")
            insert_query = """
                INSERT INTO browse_cache (entity_id, entity_text, entity_label, document_count, appearance_count) 
                VALUES (?, ?, ?, ?, ?)
            """
            cursor.executemany(insert_query, [
                (row['entity_id'], row['entity_text'], row['entity_label'], row['document_count'], row['appearance_count']) 
                for row in aggregated_data
            ])
        cursor.execute("COMMIT")
        print("--- Aggregated View Cache update finished successfully. ---")
    except Exception as e:
        print(f"!!! ERROR updating browse cache: {e} !!!")
        print(traceback.format_exc())
        cursor.execute("ROLLBACK")
    finally:
        if conn: conn.close()