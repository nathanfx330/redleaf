# --- File: ./run_task.py ---
# This script is now ONLY used by the single-file processing route in app.py
# Handles dispatching tasks based on file type.

import sys
import os
import time
import traceback
from pathlib import Path

# Ensure the main project directory is in the Python path
project_dir = Path(__file__).parent.resolve()
if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python run_task.py <task_name> <relative_path_str>")
        sys.exit(2)

    task_name = sys.argv[1]
    relative_path_str = sys.argv[2] # Expecting relative path

    print(f"--- run_task.py starting SINGLE task '{task_name}' for '{relative_path_str}' at {time.strftime('%Y-%m-%d %H:%M:%S')} ---", flush=True)
    start_time = time.time()

    try:
        # Import tasks module
        try:
            import processing_tasks
        except ImportError as e:
            print(f"FATAL ERROR: Could not import 'processing_tasks': {e}", flush=True)
            traceback.print_exc() # Show traceback for import errors
            sys.exit(1)

        # Determine file type for dispatching
        try:
            file_extension = Path(relative_path_str).suffix.lower()
        except Exception as path_err:
            print(f"FATAL ERROR: Could not process relative path '{relative_path_str}': {path_err}", flush=True)
            sys.exit(1)

        # Dispatch based on task name and file type
        if task_name == "extract_text":
            if file_extension == ".pdf":
                print(f"Dispatching PDF extraction for: {relative_path_str}", flush=True)
                processing_tasks.extract_text_for_pdf(relative_path_str)
            elif file_extension == ".html":
                # Check if the corresponding function exists before calling
                if hasattr(processing_tasks, 'extract_text_for_html'):
                    print(f"Dispatching HTML extraction for: {relative_path_str}", flush=True)
                    processing_tasks.extract_text_for_html(relative_path_str)
                else:
                    print(f"ERROR: HTML extraction function 'extract_text_for_html' not found in processing_tasks.py for {relative_path_str}", flush=True)
                    # Note: The calling function in app.py should ideally prevent this
                    # by checking status, but we add a fallback error here.
                    # Consider updating DB status if possible from here, though it adds complexity.
                    sys.exit(1) # Exit with error if function missing
            else:
                # This case should ideally be prevented by app.py logic
                print(f"ERROR: 'extract_text' task called for unsupported file type '{file_extension}' on file: {relative_path_str}", flush=True)
                # Consider updating DB status to an error state
                sys.exit(1)

        elif task_name == "index_entities":
             # Check if the corresponding function exists before calling
            if hasattr(processing_tasks, 'index_entities_for_source'):
                print(f"Dispatching indexing for source: {relative_path_str}", flush=True)
                # This single function should handle PDF/HTML/TXT internally based on path
                processing_tasks.index_entities_for_source(relative_path_str)
            else:
                 print(f"ERROR: Indexing function 'index_entities_for_source' not found in processing_tasks.py for {relative_path_str}", flush=True)
                 sys.exit(1) # Exit with error if function missing

        else:
            print(f"Error: Unknown task name '{task_name}' provided.", flush=True)
            sys.exit(1)

        end_time = time.time()
        print(f"--- run_task.py finished task '{task_name}' for '{relative_path_str}' in {end_time - start_time:.2f} seconds ---", flush=True)
        sys.exit(0) # Success

    except Exception as e:
        end_time = time.time()
        print(f"--- run_task.py FAILED task '{task_name}' for '{relative_path_str}' after {end_time - start_time:.2f} seconds ---", flush=True)
        print(f"Error Type: {type(e).__name__}", flush=True)
        print(f"Error Details: {e}", flush=True)
        traceback.print_exc() # Print full traceback
        # Note: The called processing_task function *should* attempt to update
        # the DB status to an error state itself. This script signals failure via exit code.
        sys.exit(1) # Failure