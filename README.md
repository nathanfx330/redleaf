# Redleaf Engine

**Redleaf is a private, local-first knowledge engine.**  
It transforms a directory of documents (PDFs, text, HTML, and transcripts) into a searchable, interconnected knowledge graph — all running **entirely on your local machine**.

Built for researchers, archivists, and knowledge workers, Redleaf makes it easy to find meaningful connections across large collections of documents while protecting your privacy.

---

## Table of Contents

- [Why Redleaf?](#why-redleaf)
- [Key Features](#key-features)
- [Technology Stack](#technology-stack)
- [Quickstart](#quickstart)
- [Getting Started](#getting-started)
  - [Prerequisites](#1-prerequisites)
  - [Installation](#2-installation)
  - [Running the Application](#3-running-the-application)
- [Core Workflow](#core-workflow)
- [Advanced Features](#advanced-features)
  - [Synthesis Environment](#synthesis-environment)
  - [Transcript & Media Sync](#transcript--media-sync)
  - [GPU Acceleration](#gpu-acceleration)
- [Configuration](#configuration)
- [Management Script](#management-script)
- [License](#license)
- [About the Developer](#about-the-developer)

---

## Why Redleaf?

Modern researchers often face:
- Hundreds of scattered PDFs, transcripts, and notes.
- Difficulty recalling where a piece of information came from.
- Time wasted re-reading documents instead of making connections.

**Redleaf solves this** by creating a searchable, structured knowledge graph on your own computer.  
It’s **local-first**, **privacy-respecting**, and designed to let you focus on analysis rather than file management.

---

## Key Features

- **Multi-Format Document Indexing**: Supports `.pdf`, `.html`, `.txt`, and `.srt` files.
- **Synthesis Environment**: Dual-pane workspace for writing reports while citing sources directly.
- **Bibliographic Tools**: Structured metadata, in-text citations, and automatic bibliography generation.
- **Transcript & Media Sync**: Link `.srt` transcripts to `.mp3`/`.mp4` files for synchronized playback and timestamped comments.
- **Full-Text Search**: Powered by SQLite FTS5 for lightning-fast queries across all indexed documents.
- **Entity & Relationship Extraction**: Uses **spaCy** to auto-detect people, places, and organizations and link them together.
- **Deep Document Curation**:
  - Custom tags & color-coding
  - Collections for grouping
  - Public comments or private notes
- **Multi-User Support**: Authentication with admin/user roles and invite-based registration.
- **Concurrent Background Processing**: Multi-core processing of new documents without blocking the UI.
- **Optional GPU Acceleration**: Speed up NLP tasks with CUDA-enabled NVIDIA GPUs.

---

## Technology Stack

- **Backend**: Python (Flask)
- **Database**: SQLite (with FTS5 enabled)
- **NLP**: spaCy (`en_core_web_lg`)
- **Parsing**: PyMuPDF (PDFs), BeautifulSoup4 (HTML)
- **Frontend**: HTML5, CSS3, modern JavaScript (ES6+ Modules, [Tiptap.js](https://tiptap.dev/) editor)
- **Task Management**: `concurrent.futures.ProcessPoolExecutor`
- **Citations**: `citeproc-py` (CSL support)

---

## Quickstart

```bash
git clone <your-repository-url>
cd <repository-directory>

# Create environment
python3 -m venv venv
source venv/bin/activate   # (Linux/macOS)
# OR
.\venv\Scripts\activate    # (Windows)

# Install dependencies
pip install -r requirements.txt

# Download NLP model
python -m spacy download en_core_web_lg

# Run
python run.py

Then open: http://127.0.0.1:5000
Getting Started
1. Prerequisites

    Python 3.9+

    A C compiler

        Windows: Microsoft C++ Build Tools

        macOS: Xcode Command Line Tools

        Linux: build-essential

2. Installation

git clone <your-repository-url>
cd <repository-directory>
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m spacy download en_core_web_lg

(Or use conda env create -f environment.yml if preferred.)
3. Running the Application

    Place documents in the documents/ directory.

    Start the app:

    python run.py

    First run will ask you to create an admin account.

    Then log in with your credentials.

Core Workflow

    Discover Docs → Scan documents/ for new/updated files.

    Process All "New" → Queue new files for background NLP + parsing.

    Update Browse Cache → Build entity index for faster browsing.

Advanced Features
Synthesis Environment

    Dual-pane view: write on the left, browse documents on the right.

    Highlight text in documents → automatically creates a formatted citation in your report.

    Export to .odt with bibliography included.

Transcript & Media Sync

    Automatically pairs .srt with matching .mp3/.mp4 (same filename prefix).

    Transcript scrolls with playback.

    Click any line to jump to that timestamp.

    Add comments tied to exact moments in the media.

GPU Acceleration

    Install CUDA-compatible CuPy (pip install cupy-cuda11x).

    Run check:

    python check_gpu.py

    If available, enable in Settings → System & Processing (admin only).

Configuration

    Secret Key: Generated on first run, stored in instance/ (not committed to git).

    In-App Settings: Control worker count, GPU acceleration, parsing strategy.

Management Script

manage.py provides admin tasks.

Example: Reset a user’s password

python manage.py reset-password <username>

License

This project is open source under the MIT License.
About the Developer

Created by Nathaniel Westveer as a personal tool for knowledge exploration.
It is free to use, distribute, and modify.
