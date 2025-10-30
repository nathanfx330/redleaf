# --- File: ./project/background.py ---
import os
import sys
import queue
import time
import threading
import traceback
import functools
import sqlite3
from concurrent.futures import ProcessPoolExecutor, CancelledError
from flask import current_app

try:
    from concurrent.futures import BrokenProcessPool
except ImportError:
    class BrokenProcessPool(Exception):
        pass

import processing_pipeline
import spacy 

task_queue = queue.Queue()
active_tasks = {}
active_tasks_lock = threading.Lock()
executor = None
restart_executor_event = threading.Event()

def get_system_settings():
    """Reads all settings from the database and returns them as a dict."""
    defaults = {'max_workers': 2, 'use_gpu': False, 'html_parsing_mode': 'generic'}
    settings = defaults.copy()
    conn = None
    try:
        conn = sqlite3.connect(current_app.config['DATABASE_FILE'])
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
        db_settings = {row[0]: row[1] for row in rows}
        if db_settings.get('max_workers', '').isdigit():
            settings['max_workers'] = int(db_settings['max_workers'])
        settings['use_gpu'] = db_settings.get('use_gpu') == 'true'
        if db_settings.get('html_parsing_mode') in ['generic', 'pipermail']:
            settings['html_parsing_mode'] = db_settings['html_parsing_mode']
    except Exception as e:
        print(f"Could not read app_settings from DB, using defaults. Error: {e}")
    finally:
        if conn: conn.close()
    return settings

def init_worker(use_gpu=False):
    """Initializes a worker process when it's spawned by the ProcessPoolExecutor."""
    print(f"Initializing worker process: {os.getpid()}...")
    if use_gpu:
        try:
            spacy.require_gpu()
            print(f"--- SUCCESS: Worker {os.getpid()} has acquired GPU access. ---")
        except Exception as e:
            print(f"!!! WARNING: Worker {os.getpid()} failed to acquire GPU, falling back to CPU. Error: {e} !!!")
    try:
        processing_pipeline.load_spacy_model()
        print(f"Worker {os.getpid()} successfully initialized spaCy model.")
    except Exception as e:
        print(f"FATAL ERROR initializing spaCy in worker {os.getpid()}: {e}")
        raise

def manager_thread_loop():
    """The main loop for the background task manager thread."""
    global executor, active_tasks
    print("--- Task Manager Thread Started ---")
    current_settings = get_system_settings()

    while True:
        try:
            if restart_executor_event.is_set() and not active_tasks:
                print("--- Restarting Process Pool Executor... ---")
                if executor:
                    executor.shutdown(wait=True)
                executor = None
                restart_executor_event.clear()
                current_settings = get_system_settings()

            if executor is None:
                print(f"Manager: Creating new ProcessPoolExecutor with {current_settings['max_workers']} workers. GPU: {current_settings['use_gpu']}")
                initializer_func = functools.partial(init_worker, use_gpu=current_settings['use_gpu'])
                executor = ProcessPoolExecutor(
                    max_workers=current_settings['max_workers'],
                    initializer=initializer_func
                )
                with active_tasks_lock:
                    active_tasks.clear()

            with active_tasks_lock:
                active_process_count = sum(1 for v in active_tasks.values() if v[0] == 'process')

            try:
                task_type, item_id = task_queue.get_nowait()
                if task_type == 'process':
                    if active_process_count < current_settings['max_workers']:
                        print(f"Manager: Queuing Doc ID {item_id} for processing.")
                        # The worker will now set its own 'Queued' and 'Indexing' status
                        future = executor.submit(processing_pipeline.process_document, item_id)
                        with active_tasks_lock:
                            active_tasks[future] = (task_type, item_id)
                    else:
                        task_queue.put((task_type, item_id)) # Put it back if no free workers
                elif task_type in ['discover', 'cache']:
                    target_func = {'discover': processing_pipeline.discover_and_register_documents, 'cache': processing_pipeline.update_browse_cache}.get(task_type)
                    if target_func:
                        print(f"Manager: Starting lightweight task '{task_type}' in a new thread.")
                        thread = threading.Thread(target=target_func)
                        with active_tasks_lock:
                            active_tasks[thread] = (task_type, item_id)
                        thread.start()
            except queue.Empty:
                pass

            with active_tasks_lock:
                if not active_tasks:
                    time.sleep(1)
                    continue
                done_tasks = [task for task in active_tasks if (isinstance(task, threading.Thread) and not task.is_alive()) or (not isinstance(task, threading.Thread) and task.done())]

            finished_tasks_info = []
            for task in done_tasks:
                with active_tasks_lock:
                    if task in active_tasks:
                        task_info = active_tasks.pop(task)
                        finished_tasks_info.append(task_info)
                if not isinstance(task, threading.Thread):
                    try:
                        task.result() # Worker handles its own DB writes, we just check for errors
                        print(f"Manager: Process task '{task_info[0]}' for item '{task_info[1]}' completed successfully.")
                    except Exception as e:
                        print(f"!!! MANAGER DETECTED A WORKER FAILURE for task '{task_info[0]}' on item '{task_info[1]}': {type(e).__name__} !!!")
                        if not isinstance(e, BrokenProcessPool):
                            print(traceback.format_exc())
                        raise e
                else:
                    print(f"Manager: Thread task '{task_info[0]}' completed.")

            if any(info[0] == 'process' for info in finished_tasks_info):
                with active_tasks_lock:
                    is_cache_task_pending = any(v[0] == 'cache' for v in active_tasks.values()) or any(item[0] == 'cache' for item in list(task_queue.queue))
                if not is_cache_task_pending:
                    print("Manager: Document processing finished. Automatically queueing browse cache update.")
                    task_queue.put(('cache', None))

            time.sleep(1)
            
        except (BrokenProcessPool, Exception) as e:
            print(f"!!! MANAGER THREAD ENCOUNTERED AN ERROR: {e} !!!")

            with active_tasks_lock:
                tasks_to_requeue = [info for future, info in active_tasks.items() if isinstance(future, object) and not future.done()]
                if tasks_to_requeue:
                    print("Manager: Re-queueing tasks that were active during the crash.")
                    for task_type, item_id in reversed(tasks_to_requeue):
                        print(f"Manager: Re-queueing item {item_id} for processing.")
                        task_queue.queue.appendleft((task_type, item_id))
                active_tasks.clear()
            
            if executor:
                executor.shutdown(wait=False, cancel_futures=True)
            executor = None
            
            print("Manager: Pool marked for recreation. Restarting in 5 seconds...")
            time.sleep(5)

def start_manager_thread(app):
    """Initializes and starts the background manager thread."""
    if hasattr(app, 'manager_thread_started') and app.manager_thread_started:
        return
        
    manager_target = lambda: app.app_context().push() or manager_thread_loop()
    manager = threading.Thread(target=manager_target, daemon=True)
    manager.start()
    
    app.manager_thread_started = True