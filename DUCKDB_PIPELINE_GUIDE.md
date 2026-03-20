# --- File: ./DUCKDB_PIPELINE_GUIDE.md ---
# Guide: The DuckDB Data Processing Pipeline V2

This document explains how to use the high-throughput, CLI-only data processing pipeline. This pipeline is specifically designed for performing fast, local, large-scale builds of the Redleaf knowledge base from raw document files (handling folders with 10k–100k+ files safely).

It uses an intermediate **DuckDB** database (`curator_workspace.duckdb`) for heavy processing, streaming data directly to disk to prevent RAM overloads, and then "bakes" the final, optimized data into the application's **SQLite** database (`knowledge_base.db`).

### System Requirements

*   All standard Redleaf dependencies (`environment.yml` or `requirements.txt`).
*   A complete `documents/` directory containing all source files.
*   The spaCy model `en_core_web_lg` must be downloaded via `python -m spacy download en_core_web_lg`.

---

### The Complete Workflow

The entire process is a sequence of commands run from your terminal.

#### Step 1: Initialize or Reset the Workspace

To start from a clean slate, use the dedicated reset script. This is the **required** way to begin a full build. It will delete any old workspace, clear out old batch states, create a new schema, and automatically discover all your documents.

```bash
python curator_reset.py
Step 2: Process Documents (Interactive Batch Manager)

This is the core of the workflow, where documents are extracted, analyzed via NLP, and embedded using AI.

Because massive datasets can consume excessive RAM or freeze the OS, running run-all will now launch the Interactive Batch Manager. It will ask you if you want to divide the processing into smaller chunks (e.g., 500 documents at a time).

If you select chunks, simply run this exact same command repeatedly. The system remembers where it left off until the entire folder is 100% complete.

Adjust --workers based on your CPU cores (Usually 4 to 8).

The --gpu Flag: Add this flag if you have a dedicated NVIDIA GPU and have installed CuPy (e.g., pip install cupy-cuda12x). This offloads the massive NLP entity extraction and embedding generation to the graphics card, speeding up processing by 5x-10x. Do not use this flag on Apple Silicon or integrated Intel graphics.

# Start the Batch Manager (CPU Only)
python curator_cli.py process-docs run-all --workers 6

# Start the Batch Manager (With NVIDIA GPU Acceleration)
python curator_cli.py process-docs run-all --workers 6 --gpu
Step 3: Link Media & Metadata (Optional)

If your dataset includes .srt transcripts and .xml podcast feeds, you should link them together before baking the database. This extracts the CSL-JSON bibliographic data and links the transcripts to their audio/video files.


python curator_cli.py link-media podcast-meta
Step 4: Bake & Optimize for SQLite

After processing and linking are complete, the data exists in the temporary DuckDB workspace. This step "bakes" the clean data into the final SQLite database (knowledge_base.db).

Note: This command now automatically builds all high-performance UI indexes and installs maintenance triggers. You do not need to run any separate optimization scripts.


python curator_cli.py bake-sqlite
Step 5: Run the Web Application

Your knowledge base is now ready, highly optimized, and indexed. Start the Flask server to explore your newly built instance.


python run.py
Full Workflow at a Glance

For a complete rebuild of a massive archive, the sequence of commands is:


# 1. Clean the workspace and find all documents
python curator_reset.py

# 2. Run the processing pipeline (Press UP and ENTER to run the next chunk!)
python curator_cli.py process-docs run-all --workers 6

# 3. Link XML metadata to SRT transcripts (Optional)
python curator_cli.py link-media podcast-meta

# 4. Transfer the processed data and optimize the live database
python curator_cli.py bake-sqlite

# 5. Start the web server to see your results
python run.py
Advanced: Running Individual Pipeline Phases

You can also run each phase of the processing pipeline individually. This is useful for debugging or re-running a specific part of the process.

# Phase 1: Extracts clean text from all new documents. (Supports --doc-limit)
python curator_cli.py process-docs extract --workers 6 --doc-limit 500

# Phase 2: Performs spaCy NLP analysis on extracted text.
python curator_cli.py process-docs nlp --workers 6

# Phase 3: Commits staged data (entities, relationships) to final DuckDB tables.
python curator_cli.py process-docs finalize

# Phase 4: Generates AI embeddings for all chunks.
python curator_cli.py process-docs embed --workers 6 --batch-size 128 --gpu
