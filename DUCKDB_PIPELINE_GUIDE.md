# Guide: The DuckDB Data Processing Pipeline

This document explains how to use the high-throughput, CLI-only data processing pipeline. This pipeline is designed for performing a fast, local, large-scale build of the Redleaf knowledge base from raw document files.

It uses an intermediate **DuckDB** database (`curator_workspace.duckdb`) for all heavy processing and then "bakes" the final, clean data into the application's **SQLite** database (`knowledge_base.db`).

### System Requirements

*   All standard Redleaf dependencies (`environment.yml` or `requirements.txt`).
*   A complete `documents/` directory containing all source files.
*   The spaCy model `en_core_web_lg` must be downloaded via `python -m spacy download en_core_web_lg`.

### The Complete Workflow

The entire process is a sequence of commands run from your terminal.

#### Step 1: Initialize or Reset the Workspace

To start from a completely clean slate, use the dedicated reset script. This is the **recommended** way to begin a full build. It will delete any old workspace, create a new one with the correct schema, and automatically discover all your documents.

```bash
python curator_reset.py
```

#### Step 2: Process Documents (The High-Throughput Pipeline)

This is the core of the workflow, where all documents are parsed, analyzed, and indexed into the DuckDB workspace. For the first run, `run-all` is recommended.

*   You can adjust `--workers` based on your CPU cores.
*   Add the `--gpu` flag if you have a compatible NVIDIA GPU and have installed CuPy.

```bash
# For CPU processing
python curator_cli.py process-docs run-all --workers 8 --batch-size 128

# For GPU-accelerated processing
python curator_cli.py process-docs run-all --workers 8 --batch-size 128 --gpu
```

#### Step 3: Bake Data to SQLite

After processing is complete, the data exists in the temporary DuckDB workspace. This final step "bakes" the clean, processed data into the final SQLite database (`knowledge_base.db`) that the web application uses.

```bash
python curator_cli.py bake-sqlite
```

#### Step 4: Run the Web Application

Your knowledge base is now ready. Start the Flask server to explore your newly built instance.

```bash
python run.py
```

### Full Workflow at a Glance

For a complete rebuild, the sequence of commands is:

```bash
# 1. Clean the workspace and find all documents
python curator_reset.py

# 2. Run the entire parallel processing pipeline
python curator_cli.py process-docs run-all --workers 8

# 3. Transfer the processed data to the live database
python curator_cli.py bake-sqlite

# 4. Start the web server to see your results
python run.py
```

### Advanced: Running Individual Pipeline Phases

You can also run each phase of the processing pipeline individually. This is useful for debugging or re-running a specific part of the process.

```bash
# Phase 1: Extracts clean text from all new documents.
python curator_cli.py process-docs extract --workers 8

# Phase 2: Performs spaCy NLP analysis on extracted text.
python curator_cli.py process-docs nlp --workers 8

# Phase 3: Commits staged data (entities, relationships) to final DuckDB tables.
python curator_cli.py process-docs finalize

# Phase 4: Generates AI embeddings for all chunks.
python curator_cli.py process-docs embed --workers 8 --batch-size 128