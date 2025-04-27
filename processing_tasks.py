# --- File: ./processing_tasks.py --- (Refactored - Multi-Type Support)

import fitz  # PyMuPDF
import spacy
import sqlite3
import os
import re
import time
import gc
from pathlib import Path
import traceback # For detailed error logging
from bs4 import BeautifulSoup # <<<--- ADDED IMPORT

# --- Configuration (Should match app.py) ---
DATABASE = './redleaf_data.db'
INPUT_DIR = Path("./documents").resolve() # <<<--- RENAMED (Matches app.py)
PDF_TEXT_OUTPUT_DIR = Path("./output_text_direct").resolve() # <<<--- RENAMED (Matches app.py) - ONLY for PDF output
SPACY_MODEL = "en_core_web_lg"
ENTITY_TYPES_TO_INDEX = ["PERSON", "GPE", "DATE", "ORG", "LOC"]

# --- Database Helper ---
def get_db_conn():
    """Gets a fresh DB connection configured for worker process use."""
    db_path = Path(DATABASE).resolve()
    if not db_path.is_file():
        raise FileNotFoundError(f"Database file not found at {db_path}. Ensure Flask app has run setup.")
    try:
        conn = sqlite3.connect(db_path, timeout=30) # Increased timeout
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn
    except sqlite3.Error as e:
        print(f"FATAL: Failed to connect to database {db_path}: {e}")
        raise

# --- Update Status ---
def update_pdf_status(relative_path_str, status, conn=None):
    """Updates the processing status in the database. Manages its own connection if needed."""
    # (This function remains the same as your previous version)
    close_conn = False
    if conn is None:
        try:
            conn = get_db_conn()
            close_conn = True
        except Exception as e:
            print(f"  CRITICAL: Cannot update status for '{relative_path_str}' to '{status}'. DB connection failed: {e}")
            return

    pid = os.getpid()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO pdf_status (relative_path, status, last_updated) VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(relative_path) DO UPDATE SET
                status=excluded.status,
                last_updated=excluded.last_updated;
        """, (relative_path_str, status))
        conn.commit()
        print(f"  DB Status Updated: '{relative_path_str}' -> '{status}' (PID: {pid})")
    except sqlite3.Error as e:
        print(f"  DB Status Update Error for '{relative_path_str}' -> '{status}': {e} (PID: {pid})")
        if conn and conn.in_transaction and close_conn:
             try: conn.rollback()
             except Exception as rb_e: print(f"Rollback error: {rb_e}")
    except Exception as e:
        print(f"  Unexpected error updating status for '{relative_path_str}' -> '{status}': {e} (PID: {pid})")
        traceback.print_exc()
    finally:
        if close_conn and conn:
            conn.close()

# --- Text Extraction Task (PDF) ---
def extract_text_for_pdf(relative_path_str):
    """Extracts text from a PDF file and saves it to the PDF text output directory."""
    pdf_rel_path = Path(relative_path_str)
    pdf_abs_path = INPUT_DIR / pdf_rel_path # <<<--- Use INPUT_DIR
    txt_abs_path = PDF_TEXT_OUTPUT_DIR / pdf_rel_path.with_suffix(".txt") # <<<--- Use PDF_TEXT_OUTPUT_DIR

    # Ensure output directory exists
    try:
        txt_abs_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"Error creating output directory {txt_abs_path.parent} for PDF {relative_path_str}: {e}")
        update_pdf_status(relative_path_str, f"Error: Cannot Create PDF Output Dir ({e})")
        return

    pid = os.getpid()
    print(f"Starting PDF text extraction for: {relative_path_str} (PID: {pid})")
    conn = None; doc = None
    final_status = "Error: Text Extract Failed (Unknown)"

    try:
        conn = get_db_conn()
        update_pdf_status(relative_path_str, 'Text Extract In Progress', conn)

        if not pdf_abs_path.is_file():
            final_status = "Error: PDF Not Found (Extractor)"
            raise FileNotFoundError(f"PDF not found at {pdf_abs_path}")

        # Open PDF
        try:
            doc = fitz.open(pdf_abs_path)
        except fitz.fitz.FileNotFoundError: final_status = "Error: PDF Not Found (Fitz)"; raise
        except fitz.fitz.UnusualFitInputError as fitz_err: final_status = f"Error: Invalid PDF ({fitz_err})"; raise
        except Exception as open_err: final_status = f"Error: Failed to Open PDF ({open_err})"; raise

        # Handle encryption
        if doc.is_encrypted:
            if not doc.authenticate(''):
                 final_status = "Error: PDF Encrypted (Password Required)"
                 raise ValueError("PDF is encrypted and requires a password")
            else: print(f"  Authenticated encrypted PDF {relative_path_str} with empty password.")

        # Extract text page by page
        full_text_pages = []
        num_pages = doc.page_count
        print(f"  Extracting text from {num_pages} pages...")
        for page_num in range(num_pages):
            page_index = page_num + 1
            try:
                page = doc.load_page(page_num)
                page_text = page.get_text("text", sort=True) # Use recommended 'text' format
                full_text_pages.append(page_text if page_text else "")
            except Exception as page_err:
                 print(f"  Error extracting text from page {page_index} of {relative_path_str}: {page_err}")
                 full_text_pages.append(f"\n--- Error extracting page {page_index} ---\n")

        # Format with page separators
        final_text_parts = []
        if full_text_pages:
            final_text_parts.append(full_text_pages[0].strip()) # Page 1
            for i in range(1, len(full_text_pages)):
                final_text_parts.append(f"\n\n--- Page {i + 1} ---\n\n{full_text_pages[i].strip()}")
        final_text = "".join(final_text_parts)

        # Write output file
        try:
            with open(txt_abs_path, "w", encoding="utf-8") as f_out: f_out.write(final_text)
            print(f"  Successfully wrote PDF text output to {txt_abs_path}")
            final_status = 'Text Extracted' # Success status for PDF
        except OSError as write_err:
            final_status = f"Error: Cannot Write Text File ({write_err})"
            raise

    except (FileNotFoundError, ValueError, fitz.fitz.FitzError) as specific_e:
        print(f"Error during PDF text extraction for {relative_path_str}: {specific_e} (PID: {pid})")
        # final_status should have been set before raising
    except Exception as e:
        final_status = f"Error: PDF Text Extract Unexpected ({type(e).__name__})" # More specific error
        print(f"Unexpected error during PDF text extraction for {relative_path_str}: {e} (PID: {pid})")
        traceback.print_exc()
    finally:
        if doc:
            try: doc.close()
            except Exception: pass
        # Update final status in DB reliably
        update_pdf_status(relative_path_str, final_status, conn=conn if 'conn' in locals() and conn else None)
        if conn: conn.close()
        gc.collect()
        print(f"Finished PDF text extraction attempt for {relative_path_str}. Final Status: '{final_status}'")


# --- Text Extraction Task (HTML) --- # <<<--- NEW FUNCTION ---
def extract_text_for_html(relative_path_str):
    """Extracts relevant text content from an HTML (Pipermail) file."""
    html_rel_path = Path(relative_path_str)
    html_abs_path = INPUT_DIR / html_rel_path # Source HTML file
    # Output text file path (next to source HTML)
    txt_abs_path = INPUT_DIR / html_rel_path.with_suffix(".txt")

    # Ensure parent directory exists (might be redundant if source exists, but safe)
    try:
        txt_abs_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"Error ensuring output directory {txt_abs_path.parent} for HTML {relative_path_str}: {e}")
        update_pdf_status(relative_path_str, f"Error: Cannot Create HTML Output Dir ({e})")
        return

    pid = os.getpid()
    print(f"Starting HTML text extraction for: {relative_path_str} (PID: {pid})")
    conn = None
    final_status = "Error: HTML Extract Failed (Unknown)" # Default status

    try:
        conn = get_db_conn()
        update_pdf_status(relative_path_str, 'Text Extract In Progress', conn)

        if not html_abs_path.is_file():
            final_status = "Error: HTML Not Found (Extractor)"
            raise FileNotFoundError(f"HTML file not found at {html_abs_path}")

        # Read HTML file content - try UTF-8 first, fallback might be needed
        html_content = None
        try:
            with open(html_abs_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
        except UnicodeDecodeError:
            try:
                 print(f"  Warning: UTF-8 decode failed for {relative_path_str}. Trying latin-1.")
                 with open(html_abs_path, 'r', encoding='latin-1') as f:
                     html_content = f.read()
            except Exception as read_err:
                 final_status = f"Error: Failed Read HTML (Fallback: {read_err})"
                 raise read_err # Re-raise inner exception
        except Exception as read_err:
            final_status = f"Error: Failed Read HTML ({read_err})"
            raise read_err

        if html_content is None:
             final_status = "Error: HTML Content is None"
             raise ValueError("HTML content could not be read.")

        # Parse with BeautifulSoup
        soup = BeautifulSoup(html_content, 'html.parser')

        # Extract Metadata (with error checking)
        subject = soup.find('h1').get_text(strip=True) if soup.find('h1') else "No Subject"
        sender_name = soup.body.find('b').get_text(strip=True) if soup.body and soup.body.find('b') else "Unknown Sender"
        sender_email_tag = soup.body.find('a', href=lambda href: href and href.startswith('mailto:'))
        sender_email = sender_email_tag.get_text(strip=True) if sender_email_tag else "Unknown Email"
        date_str = soup.body.find('i').get_text(strip=True) if soup.body and soup.body.find('i') else "Unknown Date"

        # Extract Primary Content from <PRE>
        pre_tag = soup.find('pre')
        body_text = ""
        if pre_tag:
            full_pre_text = pre_tag.get_text()
            # Remove scrubbed attachment sections more reliably
            body_text = re.split(r'\n-{10,}\s*(next|previous)\s+part\s*-{10,}', full_pre_text, flags=re.IGNORECASE)[0]
            body_text = body_text.strip() # Remove leading/trailing whitespace from body
        else:
            # Fallback: try getting text directly from body if no <pre>
            body_text = soup.body.get_text(separator='\n', strip=True) if soup.body else ""
            print(f"  Warning: No <pre> tag found in {relative_path_str}. Using soup.body.get_text().")


        # Combine for final output .txt file
        final_text_parts = [
            f"Subject: {subject}",
            f"From: {sender_name} <{sender_email}>",
            f"Date: {date_str}",
            "\n--- Body ---", # Clear separator
            body_text if body_text else "(No body content extracted)"
        ]
        final_text = "\n".join(final_text_parts)

        # Write the extracted text
        try:
            with open(txt_abs_path, "w", encoding="utf-8") as f_out:
                f_out.write(final_text)
            print(f"  Successfully wrote extracted HTML text to {txt_abs_path}")
            final_status = 'Text Ready' # Success status for HTML parsing
        except OSError as write_err:
            final_status = f"Error: Cannot Write HTML Text File ({write_err})"
            raise

    except FileNotFoundError as e:
        print(f"Error during HTML extraction for {relative_path_str}: {e} (PID: {pid})")
        # final_status already set
    except Exception as e:
        final_status = f"Error: HTML Extract Unexpected ({type(e).__name__})"
        print(f"Unexpected error during HTML extraction for {relative_path_str}: {e} (PID: {pid})")
        traceback.print_exc()
    finally:
        # Update final status in DB reliably
        update_pdf_status(relative_path_str, final_status, conn=conn if 'conn' in locals() and conn else None)
        if conn: conn.close()
        gc.collect()
        print(f"Finished HTML extraction attempt for {relative_path_str}. Final Status: '{final_status}'")


# --- NER & Indexing Task ---

# Global variable to hold the loaded spaCy model in a worker process
_nlp_model = None

def load_spacy_model():
    """Loads the spaCy model, attempting GPU preference. Raises error if fails."""
    # (This function remains the same as your previous version)
    global _nlp_model
    if _nlp_model is None:
        pid = os.getpid()
        try:
            use_gpu = spacy.prefer_gpu()
            print(f"Attempting to load spaCy model '{SPACY_MODEL}' (GPU Preference: {use_gpu})... (PID: {pid})")
            _nlp_model = spacy.load(SPACY_MODEL, disable=["tagger", "parser", "lemmatizer", "attribute_ruler"])
            gpu_active = spacy.require_gpu()
            print(f"spaCy model '{SPACY_MODEL}' loaded successfully. Using GPU: {gpu_active} (PID: {pid})")
        except ImportError as ie:
             if "cupy" in str(ie).lower():
                  print(f"INFO: spaCy GPU requires CuPy. Falling back to CPU. (PID: {pid})")
                  spacy.require_cpu()
                  print(f"Retrying spaCy model load on CPU...")
                  _nlp_model = spacy.load(SPACY_MODEL, disable=["tagger", "parser", "lemmatizer", "attribute_ruler"])
                  print(f"spaCy model '{SPACY_MODEL}' loaded successfully ON CPU. (PID: {pid})")
             else: print(f"FATAL: ImportError loading spaCy model: {ie} (PID: {pid})"); traceback.print_exc(); raise
        except OSError as e:
             if "Can't find model" in str(e): print(f"FATAL: spaCy model '{SPACY_MODEL}' not found. Run: python -m spacy download {SPACY_MODEL}")
             else: print(f"FATAL: OSError loading spaCy model '{SPACY_MODEL}': {e}"); traceback.print_exc()
             raise
        except Exception as e: print(f"FATAL: Unexpected error loading spaCy model '{SPACY_MODEL}': {e}"); traceback.print_exc(); raise
    return _nlp_model

def _insert_entity(cursor, entity_text, entity_label):
    """Inserts or ignores an entity and returns its database ID. Normalizes whitespace."""
     # (This function remains the same as your previous version)
    clean_entity_text = re.sub(r'\s+', ' ', entity_text).strip()
    if not clean_entity_text: return None
    try:
        cursor.execute("INSERT OR IGNORE INTO entities (entity_text, entity_label) VALUES (?, ?)", (clean_entity_text, entity_label))
        cursor.execute("SELECT id FROM entities WHERE entity_text = ? AND entity_label = ?", (clean_entity_text, entity_label))
        result = cursor.fetchone()
        return result['id'] if result else None
    except sqlite3.Error as e:
        print(f"DB Error inserting/fetching entity '{clean_entity_text}' ({entity_label}): {e} (PID: {os.getpid()})")
        return None

def _delete_existing_doc_entries(conn, relative_path_str): # Renamed for clarity
    """Deletes all previous indexing data (document pages and links) for a given source relative path."""
    # (This function remains the same functionally as your previous _delete_existing_pdf_entries)
    cursor = conn.cursor(); deleted_links = 0; deleted_docs = 0
    try:
        cursor.execute("SELECT id FROM documents WHERE relative_path = ?", (relative_path_str,))
        doc_ids = [row['id'] for row in cursor.fetchall()]
        if doc_ids:
            print(f"  Found {len(doc_ids)} existing entries for {relative_path_str}. Deleting...")
            placeholders = ','.join('?' * len(doc_ids))
            # Delete links first due to foreign key constraints
            cursor.execute(f"DELETE FROM document_entities WHERE document_id IN ({placeholders})", doc_ids); deleted_links = cursor.rowcount
            cursor.execute("DELETE FROM documents WHERE relative_path = ?", (relative_path_str,)); deleted_docs = cursor.rowcount
            conn.commit() # Commit deletion separately before re-indexing
            print(f"  Deleted {deleted_docs} doc entries and {deleted_links} entity links for {relative_path_str} (PID: {os.getpid()})")
        else:
            print(f"  No existing indexed data found for {relative_path_str}.")
    except sqlite3.Error as e:
        print(f"Error deleting existing entries for {relative_path_str}: {e} (PID: {os.getpid()})")
        if conn.in_transaction: conn.rollback()
        raise # Propagate the error to stop the indexing process


# --- Unified Indexing Function --- # <<<--- RENAMED and MODIFIED ---
def index_entities_for_source(relative_path_str):
    """Performs NER using spaCy's nlp.pipe() and indexes entities from PDF, HTML, or TXT sources."""
    source_rel_path = Path(relative_path_str)
    source_ext = source_rel_path.suffix.lower()
    text_abs_path = None # Path to the text file to be processed
    is_pdf_source = False # Flag to handle page splitting

    pid = os.getpid()
    print(f"Starting entity indexing for: {relative_path_str} (Type: {source_ext}, PID: {pid})")
    conn = None; nlp = None
    final_status = "Error: Indexing Failed (Unknown)"

    try:
        # --- Determine path to the text file based on source type ---
        if source_ext == ".pdf":
            text_abs_path = (PDF_TEXT_OUTPUT_DIR / source_rel_path.with_suffix(".txt")).resolve()
            is_pdf_source = True
        elif source_ext == ".html":
            # Text file should be next to the source HTML in INPUT_DIR
            text_abs_path = (INPUT_DIR / source_rel_path.with_suffix(".txt")).resolve()
        elif source_ext == ".txt":
            # Source TXT file *is* the text file, located in INPUT_DIR
            text_abs_path = (INPUT_DIR / source_rel_path).resolve()
            # Safety check: ensure it's not in PDF_TEXT_OUTPUT_DIR
            if text_abs_path.is_relative_to(PDF_TEXT_OUTPUT_DIR.resolve()):
                 final_status = "Error: Indexing File from PDF Output Dir"
                 raise ValueError(f"Attempting to index a file from PDF_TEXT_OUTPUT_DIR: {relative_path_str}")
        else:
            final_status = f"Error: Indexing Unsupported Type ({source_ext})"
            raise ValueError(f"Unsupported source type for indexing: {relative_path_str}")

        # This is the path that will be stored in the database
        abs_filepath_str = str(text_abs_path)
        # --- End Text Path Determination ---

        conn = get_db_conn()
        update_pdf_status(relative_path_str, 'Indexing In Progress', conn)

        if not text_abs_path.is_file():
            final_status = "Error: Indexing Text File Not Found"
            raise FileNotFoundError(f"Required text file not found at {text_abs_path}")

        # Load spaCy model
        try: nlp = load_spacy_model()
        except Exception as model_load_err: final_status = f"Error: SpaCy Model Load Failed ({model_load_err})"; raise

        # Delete previous entries for this source file
        try: _delete_existing_doc_entries(conn, relative_path_str) # Use renamed function
        except Exception as delete_err: final_status = f"Error: Failed to Delete Old Entries ({delete_err})"; raise

        # Read the appropriate text file
        try: full_doc_text = text_abs_path.read_text(encoding='utf-8')
        except Exception as read_err: final_status = f"Error: Failed to Read Text File ({read_err})"; raise
        if not full_doc_text.strip():
             print(f"Skipping empty text file: {text_abs_path.name} (PID: {pid})")
             final_status = 'Indexed (Empty Text)'; raise StopIteration("Empty file")

        # --- Adapt Page Parsing based on source type ---
        pages_data = [] # List of tuples: (page_number, page_text_content)
        if is_pdf_source:
            # Use existing PDF page splitting logic
            page1_match = re.match(r'(.*?)(?=\n\n--- Page \d+ ---|\Z)', full_doc_text, re.DOTALL)
            if page1_match:
                 page1_content = page1_match.group(1).strip()
                 if page1_content: pages_data.append((1, page1_content))
            page_marker_pattern = re.compile(r'\n\n--- Page (\d+) ---\n\n(.*?)(?=\n\n--- Page \d+ ---|\Z)', re.DOTALL)
            for match in page_marker_pattern.finditer(full_doc_text):
                 try:
                     page_num = int(match.group(1)); page_text = match.group(2).strip()
                     if page_text: pages_data.append((page_num, page_text))
                 except ValueError: print(f"Warning: Invalid page number marker in PDF text {relative_path_str}")
                 except Exception as parse_err: print(f"Warning: Error parsing page near marker in PDF text {relative_path_str}: {parse_err}")
        else: # HTML or TXT source - treat as single page/document
            content_to_index = full_doc_text
            if source_ext == ".html":
                 # Find the body content after the marker
                 body_match = re.search(r'\n--- Body ---\n(.*)', full_doc_text, re.DOTALL)
                 if body_match:
                     content_to_index = body_match.group(1).strip()
                 else:
                     print(f"Warning: '--- Body ---' marker not found in HTML text file {text_abs_path.name}. Indexing full file content.")
                     # Keep full_doc_text as content_to_index
            # Add the single "page"
            if content_to_index.strip(): # Ensure there's actual content
                 pages_data.append((1, content_to_index.strip())) # Page number is always 1
        # --- End Page Parsing Adaptation ---

        if not pages_data:
             final_status = "Error: Indexing Failed (No Content Parsed)"
             raise ValueError("Failed to parse any indexable content from the text file.")

        pages_data.sort(key=lambda item: item[0]) # Still sort, relevant for multi-page PDFs
        page_texts = [text for num, text in pages_data]
        page_numbers = [num for num, text in pages_data]

        # --- Process all pages/content chunks using nlp.pipe() ---
        total_entities_linked = 0; processed_chunk_count = 0
        transaction_active = False
        cursor = conn.cursor()

        try:
            print(f"  Starting transaction and processing {len(page_texts)} text chunks with nlp.pipe()...")
            cursor.execute("BEGIN")
            transaction_active = True

            processed_docs_stream = nlp.pipe(page_texts) # Batch processing

            for i, doc_ner in enumerate(processed_docs_stream):
                page_num = page_numbers[i] # Page number (1 for HTML/TXT)
                page_text_len = len(page_texts[i])
                processed_chunk_count += 1
                document_db_id = None # Changed variable name

                # Log very long chunks (more likely for HTML/TXT)
                if page_text_len > nlp.max_length * 1.5: # Heuristic check
                    print(f"Warning: Text chunk (Page {page_num}) for {relative_path_str} is very long ({page_text_len} chars). NER results might be affected.")

                # 1. Insert document record (associating with the source path and the text file used)
                try:
                    cursor.execute("INSERT INTO documents (relative_path, page_number, text_filepath) VALUES (?, ?, ?)",
                                   (relative_path_str, page_num, abs_filepath_str)) # Use correct text path
                    document_db_id = cursor.lastrowid
                    if not document_db_id: raise sqlite3.Error(f"Failed to get last row ID for document chunk (Page {page_num})")
                except sqlite3.Error as doc_db_err:
                    print(f"DB Error inserting document record (Page {page_num}) for {relative_path_str}: {doc_db_err}")
                    final_status = f"Error: Indexing DB Error (Doc Insert)"; raise

                # 2. Link entities found in this chunk
                page_entities_linked_ids = set()
                try:
                    for ent in doc_ner.ents:
                        if ent.label_ in ENTITY_TYPES_TO_INDEX:
                            entity_id = _insert_entity(cursor, ent.text, ent.label_)
                            if entity_id and entity_id not in page_entities_linked_ids:
                                try:
                                    # Link entity to the document record ID
                                    cursor.execute("INSERT OR IGNORE INTO document_entities (document_id, entity_id) VALUES (?, ?)",
                                                   (document_db_id, entity_id))
                                    if cursor.rowcount > 0: total_entities_linked += 1
                                    page_entities_linked_ids.add(entity_id)
                                except sqlite3.Error as link_err:
                                     print(f"DB Error linking entity ID {entity_id} to doc ID {document_db_id} (Page {page_num}): {link_err}") # Log and continue if possible
                except Exception as ent_link_err:
                    print(f"Error processing entities/links for chunk (Page {page_num}), {relative_path_str}: {ent_link_err}"); traceback.print_exc()
                    final_status = f"Error: Indexing Entity/Link Processing (Page {page_num})"; raise # Raise to rollback

            # Commit transaction only if all chunks processed without raising errors
            conn.commit()
            transaction_active = False
            final_status = 'Indexed' # Set success status
            print(f"Successfully indexed entities for {relative_path_str}. Linked {total_entities_linked} new unique entity occurrences across {processed_chunk_count} pages/chunks.")

        except Exception as inner_e:
             print(f"Error during chunk processing loop or commit for {relative_path_str}: {inner_e}")
             if transaction_active and conn and conn.in_transaction:
                 print(f"Rolling back transaction for {relative_path_str}."); conn.rollback()
             # final_status should have been set where error was raised
             raise # Re-raise to outer handler

    except StopIteration as si: # Catch the early exit for empty files
        print(f"Indexing stopped early: {si}") # final_status already set
    except (FileNotFoundError, ValueError, OSError, sqlite3.Error) as specific_e:
        print(f"Setup/Pre-processing error during indexing for {relative_path_str}: {specific_e}")
        if conn and conn.in_transaction and 'transaction_active' in locals() and transaction_active: conn.rollback()
    except Exception as e:
        final_status = f"Error: Indexing Unexpected ({type(e).__name__})"
        print(f"Unexpected error during indexing for {relative_path_str}: {e}"); traceback.print_exc()
        if conn and conn.in_transaction and 'transaction_active' in locals() and transaction_active: conn.rollback()
    finally:
        update_pdf_status(relative_path_str, final_status, conn=conn if 'conn' in locals() and conn else None)
        if conn: conn.close()
        gc.collect()
        print(f"Finished entity indexing attempt for {relative_path_str}. Final Status: '{final_status}'")


# --- Standalone Execution Block ---
if __name__ == '__main__':
     import sys
     if len(sys.argv) < 3:
         print("Usage:")
         print("  python processing_tasks.py extract <relative_path>")
         print("  python processing_tasks.py index <relative_path>")
         sys.exit(1)

     task_type = sys.argv[1].lower()
     relative_path = sys.argv[2]
     file_ext = Path(relative_path).suffix.lower()

     print(f"--- Running Task from Command Line ---")
     print(f"Task Type: {task_type}")
     print(f"Relative Path: {relative_path}")
     print(f"------------------------------------")

     if task_type == 'extract':
          if file_ext == '.pdf':
               print(f"Calling extract_text_for_pdf...")
               extract_text_for_pdf(relative_path)
          elif file_ext == '.html':
                print(f"Calling extract_text_for_html...")
                extract_text_for_html(relative_path)
          else:
               print(f"Error: Cannot run 'extract' task for file type {file_ext}")
               sys.exit(1)
          print(f"--- Extraction Task Finished ---")
     elif task_type == 'index':
          print(f"Calling index_entities_for_source...")
          index_entities_for_source(relative_path) # Call the unified function
          print(f"--- Indexing Task Finished ---")
     else:
         print(f"Error: Unknown task type '{task_type}'. Use 'extract' or 'index'.")
         sys.exit(1)

     sys.exit(0)