# --- File: ./curator_pipeline_v2.py (FIXED FOR SRT EXTRACTION) ---
import duckdb
import spacy
import ollama
import numpy as np
import fitz
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from tqdm import tqdm
import os
import sys
import re
from pathlib import Path
import logging
import math
import pickle
import shutil
import pandas as pd

import email
from email.header import decode_header
from email.utils import parsedate_to_datetime
from itertools import combinations

# Add project directory to allow imports from project config
project_dir = Path(__file__).resolve().parent
sys.path.append(str(project_dir))
from project.config import EMBEDDING_MODEL, DOCUMENTS_DIR

DUCKDB_FILE = project_dir / "curator_workspace.duckdb"
SPACY_MODEL = "en_core_web_lg"
NLP_MODEL = None
TEMP_DIR = project_dir / "temp_nlp_results"

LOG_FILE = project_dir / "curator_pipeline.log"
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.ERROR,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# --- DATABASE AND MODEL LOADING ---
def get_db_conn():
    return duckdb.connect(database=str(DUCKDB_FILE), read_only=False)
def create_staging_tables(conn):
    print("[INFO] Clearing and resetting all staging tables...")
    conn.execute("DROP TABLE IF EXISTS _staging_raw_text CASCADE;"); conn.execute("DROP TABLE IF EXISTS _staging_chunks_to_embed CASCADE;"); conn.execute("DROP TABLE IF EXISTS _staging_entities CASCADE;"); conn.execute("DROP TABLE IF EXISTS _staging_relationships CASCADE;"); conn.execute("DROP SEQUENCE IF EXISTS seq_staging_chunks_id;")
    conn.execute("CREATE TABLE _staging_raw_text (doc_id BIGINT, page_number INTEGER, page_content VARCHAR, PRIMARY KEY(doc_id, page_number));")
    conn.execute("CREATE TABLE _staging_chunks_to_embed (id BIGINT PRIMARY KEY, doc_id BIGINT, page_number INTEGER, entity_text VARCHAR, entity_label VARCHAR, chunk_text VARCHAR);")
    conn.execute("CREATE TABLE _staging_entities (doc_id BIGINT, page_number INTEGER, entity_text VARCHAR, entity_label VARCHAR);")
    conn.execute("CREATE TABLE _staging_relationships (doc_id BIGINT, page_number INTEGER, subj_text VARCHAR, subj_label VARCHAR, obj_text VARCHAR, obj_label VARCHAR, phrase VARCHAR);")
    conn.execute("CREATE SEQUENCE seq_staging_chunks_id START 1;"); print("[OK]   Staging tables are ready.")
def cleanup_staging_tables():
    print("--- Cleaning up temporary staging tables... ---")
    if TEMP_DIR.exists(): shutil.rmtree(TEMP_DIR)
    print("[OK] Cleanup complete.")
def load_spacy_model(use_gpu=False):
    global NLP_MODEL
    if NLP_MODEL is None:
        if use_gpu:
            try: spacy.require_gpu()
            except Exception: pass
        try:
            NLP_MODEL = spacy.load(SPACY_MODEL, disable=["lemmatizer"])
            NLP_MODEL.max_length = 2000000
        except Exception as e:
            logging.error(f"Failed to load spaCy model {SPACY_MODEL}: {e}", exc_info=True)
            raise
    return NLP_MODEL

# --- PARSING HELPERS ---
def _decode_header_text(h): return "".join(p.decode(c or 'utf-8', 'ignore') if isinstance(p, bytes) else p for p, c in decode_header(h or ""))
def _parse_eml_content(b):
    m = email.message_from_bytes(b); d = {'from_address': _decode_header_text(m.get('From')),'to_addresses': _decode_header_text(m.get('To')),'cc_addresses': _decode_header_text(m.get('Cc')),'subject': _decode_header_text(m.get('Subject')),'sent_at': None}
    if ds := m.get('Date'):
        try: d['sent_at'] = parsedate_to_datetime(ds)
        except (ValueError, TypeError): pass
    bp, bh = "", ""
    if m.is_multipart():
        for p in m.walk():
            ct = p.get_content_type(); cs = p.get_content_charset() or 'utf-8'
            if ct == 'text/plain' and not bp:
                try: bp = p.get_payload(decode=True).decode(cs, 'ignore')
                except (LookupError, AttributeError): pass
            elif ct == 'text/html' and not bh:
                try: bh = p.get_payload(decode=True).decode(cs, 'ignore')
                except (LookupError, AttributeError): pass
    else:
        try: bp = m.get_payload(decode=True).decode(m.get_content_charset() or 'utf-8', 'ignore')
        except (LookupError, AttributeError): pass
    fb = bp.strip()
    if not fb and bh: fb = _extract_text_with_block_separation(bh)
    return {'metadata': d, 'body': fb}
def _extract_text_with_block_separation(h):
    s = BeautifulSoup(h, 'lxml'); [e.decompose() for e in s(["script", "style"])]; b = [f"Title: {t.get_text(strip=True)}" for t in [s.find('title')] if t and t.get_text(strip=True)]; b.extend(t.get_text(separator=' ', strip=True) for t in s.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'li', 'blockquote', 'pre', 'div']) if t.get_text(strip=True)); return "\n\n".join(b)
def _extract_text_from_pdf_doc(d): return {i: t.strip() for i, p in enumerate(d, 1) if (t := p.get_text("text", sort=True))}

# --- START OF MODIFICATION ---
def _get_srt_duration(srt_content: str):
    timestamps = re.findall(r'\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})', srt_content)
    if not timestamps: return None
    try:
        h, m, s, ms = map(int, re.split('[:,]', timestamps[-1]))
        return h * 3600 + m * 60 + s + ms / 1000
    except (ValueError, IndexError):
        return None

def _parse_srt_for_db(srt_content: str) -> list[dict]:
    cues = []
    # This pattern is more robust for variations in SRT files.
    cue_pattern = re.compile(r'(\d+)\s*\n(\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3})\s*\n(.*?)(?=\n\n|\Z)', re.DOTALL)
    for match in cue_pattern.finditer(srt_content):
        sequence, timestamp, dialogue_block = match.groups()
        clean_dialogue = ' '.join(dialogue_block.strip().splitlines())
        cues.append({'sequence': int(sequence), 'timestamp': timestamp.strip(), 'dialogue': clean_dialogue})
    return cues

def _extract_text_from_srt(srt_cues: list[dict]) -> str:
    return "\n".join(cue['dialogue'] for cue in srt_cues)
# --- END OF MODIFICATION ---

# --- PHASE 1: TEXT EXTRACTION ---
def _extract_worker(d):
    i, r, t = d; p, c, e, m, dur, srt_cues = {}, 0, None, None, None, []
    try:
        fp = DOCUMENTS_DIR / r
        if t == 'PDF':
            with fitz.open(fp) as f: p = _extract_text_from_pdf_doc(f); c = f.page_count
        elif t == 'TXT':
            x = fp.read_text('utf-8', 'ignore'); w = x.split(); c = (len(w) + 299) // 300 or 1
            for j in range(0, len(w), 300): p[(j // 300) + 1] = " ".join(w[j:j + 300])
        elif t == 'HTML':
            x = _extract_text_with_block_separation(fp.read_text('utf-8', 'ignore')); p = {1: x}; c = 1
        elif t == 'EML':
            y = _parse_eml_content(fp.read_bytes()); p = {1: y['body']}; c = 1; e = y['metadata']
        # --- START OF MODIFICATION ---
        elif t == 'SRT':
            content = fp.read_text('utf-8', 'ignore')
            dur = _get_srt_duration(content)
            srt_cues = _parse_srt_for_db(content)
            # The "main content" for NLP is still the full text block.
            body = _extract_text_from_srt(srt_cues)
            p = {1: body} 
            c = len(srt_cues) # Page count is the number of cues
        # --- END OF MODIFICATION ---

        return i, c, list(p.items()), e, dur, None, srt_cues
    except Exception as ex:
        m = f"EXTRACT FAILED:{ex}"; logging.error(m, exc_info=True)
        return i, 0, [], None, None, m, []

def phase_extract_text(workers):
    print("\n--- PHASE 1: Extracting Text from Documents ---");
    if LOG_FILE.exists():
        try: LOG_FILE.unlink()
        except Exception: logging.exception("Failed to delete existing log file.")
    c = get_db_conn(); create_staging_tables(c)
    try: 
        c.execute("DELETE FROM email_metadata;")
        c.execute("DELETE FROM srt_cues;") # <-- ADDED
    except Exception: logging.info("email_metadata or srt_cues table not present yet — skipping delete.")
    td = c.execute("SELECT COUNT(*) FROM documents WHERE status = 'New'").fetchone()[0]
    if td == 0: print("[INFO] No new documents to extract."); c.close(); return
    dbf, dbw, ap, am, ec, ac = 10000, 5000, [], [], 0, [] # <-- ADDED ac for SRT cues
    with ThreadPoolExecutor(max_workers=workers) as ex, tqdm(total=td, desc="Extracting Text", mininterval=1.0) as pb:
        for o in range(0, td, dbf):
            dtp = c.execute(f"SELECT id,relative_path,file_type FROM documents WHERE status='New' LIMIT {dbf} OFFSET {o}").fetchall()
            if not dtp: break
            fs = {ex.submit(_extract_worker, d): d for d in dtp}
            for f in as_completed(fs):
                # --- START OF MODIFICATION: Unpack all 7 items from the worker result ---
                i, pc, pi, em, dur, er, srt_cues = f.result()
                # --- END OF MODIFICATION ---
                if er: ec += 1; tqdm.write(f"[FAIL] Doc {i}:{er[:150]}..."); c.execute("UPDATE documents SET status='Error',status_message=? WHERE id=?", (er[:1000], i))
                else:
                    if pi: ap.extend([(i, pn, pt) for pn, pt in pi])
                    if dur is not None:
                        c.execute("UPDATE documents SET page_count=?, duration_seconds=? WHERE id=?", (pc, dur, i))
                    else:
                        c.execute("UPDATE documents SET page_count=? WHERE id=?", (pc, i))
                    if em: am.append((i, em.get('from_address'), em.get('to_addresses'), em.get('cc_addresses'), em.get('subject'), em.get('sent_at')))
                    # --- START OF MODIFICATION: Handle SRT cue data ---
                    if srt_cues:
                        ac.extend([(c.execute("SELECT nextval('seq_srt_cues_id')").fetchone()[0], i, s['sequence'], s['timestamp'], s['dialogue']) for s in srt_cues])
                    # --- END OF MODIFICATION ---
                if len(ap) > dbw: pb.set_description("Writing text..."); c.executemany("INSERT INTO _staging_raw_text VALUES(?,?,?)", ap); ap = []
                if len(am) > dbw: pb.set_description("Writing meta..."); c.executemany("INSERT INTO email_metadata VALUES(?,?,?,?,?,?)", am); am = []
                if len(ac) > dbw: pb.set_description("Writing cues..."); c.executemany("INSERT INTO srt_cues VALUES (?,?,?,?,?)", ac); ac = [] # <-- ADDED
                pb.set_description("Extracting Text"); pb.update(1)
    if ap: print(f"[INFO] Staging final {len(ap)} pages..."); c.executemany("INSERT INTO _staging_raw_text VALUES(?,?,?)", ap)
    if am: print(f"[INFO] Staging final {len(am)} records..."); c.executemany("INSERT INTO email_metadata VALUES(?,?,?,?,?,?)", am)
    if ac: print(f"[INFO] Staging final {len(ac)} SRT cues..."); c.executemany("INSERT INTO srt_cues VALUES (?,?,?,?,?)", ac) # <-- ADDED
    c.close(); print(f"[OK] Extraction complete. Errors:{ec}.");
    if ec > 0: print(f"[ACTION] Review '{LOG_FILE.name}'.")

# --- PHASE 2: NLP ANALYSIS (REVERTED TO FILE-BASED WORKERS) ---
def _nlp_worker_process(pages_batch, temp_file_path):
    nlp = load_spacy_model()
    results = {'entities': [], 'chunks': [], 'relationships': []}
    for doc_id, page_number, page_text in pages_batch:
        if not page_text or len(page_text) > nlp.max_length: continue
        try:
            doc_nlp = nlp(page_text)
            for ent in doc_nlp.ents:
                if ent_text := ent.text.strip():
                    results['entities'].append((doc_id, page_number, ent_text, ent.label_))
                    verb = ent.root.head
                    if verb.pos_ in ("VERB", "AUX"):
                        action_phrase = " ".join(token.text for token in verb.subtree).strip().replace('\n', ' ')
                        if action_phrase and len(action_phrase) < 2048:
                            chunk_text = f"{ent.text} ({ent.label_}): {action_phrase}"
                            results['chunks'].append((doc_id, page_number, ent_text, ent.label_, chunk_text))
            for sent in doc_nlp.sents:
                unique_ents = list(dict.fromkeys(sent.ents))
                if len(unique_ents) < 2: continue
                for ent1, ent2 in combinations(unique_ents, 2):
                    start, end = min(ent1.end_char, ent2.end_char), max(ent1.start_char, ent2.start_char)
                    if end > start and (end - start) < 75:
                        phrase = ' '.join(page_text[start:end].strip().split())
                        if phrase:
                            subj = (ent1.text.strip(), ent1.label_) if ent1.start_char < ent2.start_char else (ent2.text.strip(), ent2.label_)
                            obj = (ent2.text.strip(), ent2.label_) if ent1.start_char < ent2.start_char else (ent1.text.strip(), ent1.label_)
                            if subj[0] and obj[0]: results['relationships'].append((doc_id, page_number, subj[0], subj[1], obj[0], obj[1], phrase))
        except Exception as e:
            logging.error(f"NLP FAILED for Doc ID {doc_id} Page {page_number}: {e}", exc_info=True)
    with open(temp_file_path, 'wb') as f:
        pickle.dump(results, f)

def phase_nlp_analysis(workers):
    print("\n--- PHASE 2: Performing NLP Analysis ---")
    conn = get_db_conn()
    print("[INFO] Counting total pages for NLP analysis...")
    total_pages = conn.execute("SELECT COUNT(*) FROM _staging_raw_text").fetchone()[0]
    print(f"[OK]   Found {total_pages} pages to process.")
    if total_pages == 0: print("[INFO] No text to analyze. Skipping."); conn.close(); return
    
    if TEMP_DIR.exists(): shutil.rmtree(TEMP_DIR)
    TEMP_DIR.mkdir(exist_ok=True)
    
    print("[INFO] Fetching all page data from database...")
    all_pages = conn.execute("SELECT doc_id, page_number, page_content FROM _staging_raw_text").fetchall()
    conn.close()
    
    chunk_size = math.ceil(total_pages / workers) if workers > 0 else total_pages
    page_batches = [all_pages[i:i + chunk_size] for i in range(0, total_pages, chunk_size)]

    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_nlp_worker_process, batch, TEMP_DIR / f"worker_{i}.pkl") for i, batch in enumerate(page_batches)]
        with tqdm(total=len(futures), desc="Processing Batches") as pbar:
            for future in as_completed(futures):
                try: future.result()
                except Exception as e: pbar.write(f"[FATAL] A worker process failed: {e}")
                pbar.update(1)

    print("\n[INFO] Consolidating results from all workers...")
    conn = get_db_conn()
    all_results = {'entities': [], 'chunks': [], 'relationships': []}
    temp_files = sorted(list(TEMP_DIR.glob('*.pkl')))
    for temp_file in tqdm(temp_files, desc="Loading result files"):
        with open(temp_file, 'rb') as f:
            results = pickle.load(f)
            all_results['entities'].extend(results['entities'])
            all_results['chunks'].extend(results['chunks'])
            all_results['relationships'].extend(results['relationships'])
            
    DB_WRITE_BATCH_SIZE = 100000

    if all_results['entities']:
        total_entities = len(all_results['entities'])
        with tqdm(total=total_entities, desc="Inserting Entities", unit="entity") as pbar:
            for i in range(0, total_entities, DB_WRITE_BATCH_SIZE):
                batch = all_results['entities'][i:i + DB_WRITE_BATCH_SIZE]
                if batch: conn.executemany("INSERT INTO _staging_entities VALUES (?, ?, ?, ?)", batch); pbar.update(len(batch))

    if all_results['relationships']:
        total_relationships = len(all_results['relationships'])
        with tqdm(total=total_relationships, desc="Inserting Relationships", unit="rel") as pbar:
            for i in range(0, total_relationships, DB_WRITE_BATCH_SIZE):
                batch = all_results['relationships'][i:i + DB_WRITE_BATCH_SIZE]
                if batch: conn.executemany("INSERT INTO _staging_relationships VALUES (?, ?, ?, ?, ?, ?, ?)", batch); pbar.update(len(batch))
            
    if all_results['chunks']:
        with tqdm(total=len(all_results['chunks']), desc="Inserting Chunks") as pbar:
            for i in range(0, len(all_results['chunks']), 5000):
                batch = all_results['chunks'][i:i+5000]
                chunk_data = [(conn.execute("SELECT nextval('seq_staging_chunks_id')").fetchone()[0], c[0], c[1], c[2], c[3], c[4]) for c in batch]
                if chunk_data: conn.executemany("INSERT INTO _staging_chunks_to_embed VALUES (?, ?, ?, ?, ?, ?)", chunk_data); pbar.update(len(batch))

    conn.close()
    print("\n[OK] Phase 2: NLP Analysis Complete.")

# --- PHASE 3: EMBEDDING GENERATION ---
def _batch_generator(items, batch_size):
    for i in range(0, len(items), batch_size): yield items[i:i + batch_size]
def _embed_worker(batch):
    results = []
    try:
        for item_id, prompt_text in batch:
            response = ollama.embeddings(model=EMBEDDING_MODEL, prompt=prompt_text); embedding_bytes = np.array(response['embedding'], dtype=np.float32).tobytes(); results.append((item_id, embedding_bytes))
        return results
    except Exception as e:
        tqdm.write(f"[WARN] An embedding generation failed: {e}"); return []
def phase_generate_embeddings(workers, batch_size, use_gpu):
    print("\n--- PHASE 3: Generating AI Embeddings ---")
    if use_gpu: print("[INFO] GPU acceleration is enabled for Ollama (ensure Ollama server is configured for GPU).")
    conn = get_db_conn()
    try: conn.execute("DELETE FROM super_embedding_chunks;")
    except Exception: logging.info("super_embedding_chunks table may not exist yet; continuing.")
    chunks_to_process = conn.execute("SELECT id, chunk_text FROM _staging_chunks_to_embed").fetchall()
    if not chunks_to_process: print("[INFO] No text chunks to embed. Skipping."); conn.close(); return
    all_embeddings = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        batches = list(_batch_generator(chunks_to_process, batch_size)); futures = {executor.submit(_embed_worker, batch): i for i, batch in enumerate(batches)}
        pbar = tqdm(as_completed(futures), total=len(batches), desc="Generating Embeddings")
        for future in pbar:
            try:
                result = future.result()
                if result: all_embeddings.extend(result)
            except Exception as e:
                tqdm.write(f"[WARN] Embedding worker failed: {e}")
    if all_embeddings:
        print(f"[INFO] Writing {len(all_embeddings)} embeddings to the database...")
        conn.execute("CREATE TEMP TABLE _tmp_embeddings (chunk_id BIGINT, embedding BLOB);")
        conn.executemany("INSERT INTO _tmp_embeddings VALUES (?, ?)", all_embeddings)
        conn.execute("""
            INSERT INTO super_embedding_chunks (id, doc_id, page_number, entity_id, chunk_text, embedding)
            SELECT s.id, s.doc_id, s.page_number, e.id, s.chunk_text, t.embedding
            FROM _staging_chunks_to_embed s 
            JOIN _tmp_embeddings t ON s.id = t.chunk_id
            JOIN entities e ON s.entity_text = e.text AND s.entity_label = e.label;
        """)
        conn.execute("DROP TABLE _tmp_embeddings;")
    conn.close(); print("[OK] Phase 3: Embedding Generation Complete.")

# --- PHASE 4: FINALIZE DATA ---
def phase_finalize_data():
    print("\n--- PHASE 4: Finalizing Data ---")
    with get_db_conn() as conn:
        print("[INFO] Clearing old data from final tables...")
        try:
            conn.execute("DELETE FROM browse_cache;"); conn.execute("DELETE FROM entity_relationships;"); conn.execute("DELETE FROM entity_appearances;"); conn.execute("DELETE FROM entities;"); conn.execute("DELETE FROM content_index;")
        except Exception: logging.info("Some final tables may not exist yet; continuing.")
        print("[INFO] Committing final entities..."); conn.execute("INSERT INTO entities (id, text, label) SELECT nextval('seq_entities_id'), text, label FROM (SELECT DISTINCT entity_text as text, entity_label as label FROM _staging_entities) t;")
        print("[INFO] Committing final entity appearances..."); conn.execute("""INSERT INTO entity_appearances (doc_id, entity_id, page_number) SELECT DISTINCT s.doc_id, e.id, s.page_number FROM _staging_entities s JOIN entities e ON s.entity_text = e.text AND s.entity_label = e.label;""")
        print("[INFO] Committing final relationships..."); conn.execute("""INSERT INTO entity_relationships (id, doc_id, page_number, subject_entity_id, object_entity_id, relationship_phrase) SELECT nextval('seq_entity_relationships_id'), s.doc_id, s.page_number, subj.id, obj.id, s.phrase FROM (SELECT DISTINCT * FROM _staging_relationships) s JOIN entities subj ON s.subj_text = subj.text AND s.subj_label = subj.label JOIN entities obj ON s.obj_text = obj.text AND s.obj_label = obj.label;""")
        print("[INFO] Committing final content index..."); conn.execute("INSERT INTO content_index (doc_id, page_number, page_content) SELECT doc_id, page_number, page_content FROM _staging_raw_text;")
        print("[INFO] Updating document statuses..."); conn.execute("UPDATE documents SET status='Indexed', processed_at=CURRENT_TIMESTAMP WHERE status = 'New';")
        print("[INFO] Updating browse cache..."); conn.execute("CREATE OR REPLACE TABLE browse_cache AS SELECT e.id as entity_id, e.text as entity_text, e.label as entity_label, COUNT(DISTINCT ea.doc_id) as document_count, COUNT(ea.doc_id) as appearance_count FROM entities e JOIN entity_appearances ea ON e.id = ea.entity_id GROUP BY e.id, e.text, e.label;")
    print("[OK] Phase 4: Finalization Complete.")

# --- FULL PIPELINE RUNNER (CORRECTED SEQUENCE) ---
def run_full_pipeline(workers, batch_size, use_gpu):
    conn = get_db_conn()
    new_docs_count = conn.execute("SELECT COUNT(*) FROM documents WHERE status = 'New'").fetchone()[0]
    if new_docs_count > 0:
        phase_extract_text(workers)
    else:
        print("\n--- PHASE 1: Skipping Text Extraction (No new documents to process) ---")

    try:
        staged_text_count = conn.execute("SELECT COUNT(*) FROM _staging_raw_text").fetchone()[0]
    except (duckdb.CatalogException, IndexError):
        staged_text_count = 0
    
    conn.close()

    if staged_text_count > 0:
        phase_nlp_analysis(workers)
        
        # This is the critical fix: Re-introducing the finalize step in the correct order.
        phase_finalize_data()
        
        phase_generate_embeddings(workers, batch_size, use_gpu)
    else:
        print("\n--- SKIPPING PHASES 2, 3 & 4: No text was staged in Phase 1 ---")
        
    cleanup_staging_tables()
    print("\n--- ✅ All processing phases completed successfully! ---")

# --- CLI / entrypoint guard for safe multiprocessing ---
if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    import argparse
    parser = argparse.ArgumentParser(description="Run curator pipeline.")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 1), help="Number of parallel workers")
    parser.add_argument("--batch-size", type=int, default=16, help="Embedding batch size")
    parser.add_argument("--use-gpu", action="store_true", help="Enable GPU for NLP/embedding generation (if supported)")
    args = parser.parse_args()
    run_full_pipeline(workers=args.workers, batch_size=args.batch_size, use_gpu=args.use_gpu)