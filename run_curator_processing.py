# --- File: ./run_curator_processing.py ---
import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import multiprocessing
import threading
from tqdm import tqdm
import spacy # NEW IMPORT

# Add project directory to allow imports
project_dir = Path(__file__).resolve().parent
sys.path.append(str(project_dir))

import curator_pipeline
from curator_pipeline import get_db_conn, load_spacy_model

def main():
    parser = argparse.ArgumentParser(description="Run the Redleaf Curator Processing Pipeline.")
    parser.add_argument('-w', '--workers', type=int, default=2, help="Number of parallel worker threads.")
    parser.add_argument('--retry-errors', action='store_true', help="Include documents in 'Error' state.")
    # ================= START OF FIX =================
    parser.add_argument('--gpu', action='store_true', help="Enable GPU acceleration for spaCy.")
    # ================= END OF FIX =================
    args = parser.parse_args()

    max_workers = args.workers
    if max_workers < 1: print("[ERROR] Workers must be >= 1."), sys.exit(1)
        
    print(f"--- Starting Curator Processing with {max_workers} worker thread(s) ---")
    
    # ================= START OF FIX =================
    if args.gpu:
        print("[Main Thread] Attempting to enable GPU acceleration...")
        try:
            spacy.require_gpu()
            print("[Main Thread] SUCCESS: GPU is available and spaCy can use it!")
        except Exception as e:
            print("\n[FATAL] GPU ACCELERATION FAILED. spaCy could not access the GPU.")
            print("         Please ensure you have a compatible NVIDIA GPU, CUDA drivers, and 'cupy' installed.")
            print(f"         (e.g., 'pip install cupy-cuda11x' or 'cupy-cuda12x'). Error: {e}")
            sys.exit(1)
    # ================= END OF FIX =================
    
    db_conn = get_db_conn()
    print("[INFO] Performing startup cleanup...")
    try:
        stale_docs = db_conn.execute("UPDATE documents SET status = 'New', status_message = 'Reset on startup' WHERE status IN ('Queued', 'Indexing') RETURNING id").fetchall()
        if stale_docs: print(f"[OK]   Found and reset {len(stale_docs)} stale document(s).")
        else: print("[OK]   System state is clean.")
    except Exception as e:
        print(f"[WARN] Could not perform cleanup task: {e}")
    
    try:
        print("[Main Thread] Loading spaCy model...")
        load_spacy_model()
        print("[Main Thread] spaCy model loaded successfully.")
    except Exception as e:
        print(f"[FATAL] Could not load spaCy model: {e}"), db_conn.close(), sys.exit(1)

    query = "SELECT id FROM documents WHERE status = 'New'"
    if args.retry_errors:
        print("[INFO] --retry-errors flag set. Including failed documents.")
        query = "SELECT id FROM documents WHERE status IN ('New', 'Error')"
    
    docs_to_process = db_conn.execute(query).fetchall()
    if not docs_to_process:
        print("[INFO] No documents found to process."); db_conn.close(); return

    doc_ids_to_process = [row[0] for row in docs_to_process]
    print(f"[INFO] Found {len(doc_ids_to_process)} document(s) to process.")

    db_lock = threading.Lock()
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        with db_lock:
            db_conn.execute("BEGIN;")
            for doc_id in doc_ids_to_process:
                db_conn.execute("UPDATE documents SET status='Queued', status_message='Pending assignment' WHERE id=?", (doc_id,))
            db_conn.execute("COMMIT;")
        
        future_to_doc_id = {executor.submit(curator_pipeline.process_document, doc_id, db_lock): doc_id for doc_id in doc_ids_to_process}
        
        total_count = len(doc_ids_to_process)
        
        print("\n--- Processing Documents ---")
        pbar = tqdm(as_completed(future_to_doc_id), total=total_count, unit="doc", desc="Processing")
        
        for future in pbar:
            doc_id = future_to_doc_id[future]
            try:
                future.result()
            except Exception as exc:
                tqdm.write(f"[ERROR] Doc ID {doc_id} failed: {exc}")

    print("\n--- All processing tasks complete. ---")

    try:
        curator_pipeline.update_browse_cache()
    except Exception as e:
        print(f"[ERROR] Failed to update browse cache: {e}")
    
    db_conn.close()

if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()