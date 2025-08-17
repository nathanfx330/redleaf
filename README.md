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

##  Quickstart

### 1. Clone the Repository

```bash
git clone <your-repository-url>
cd <repository-directory>
```

### 2. Set Up Environment

#### Using `venv`:

```bash
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate       # Linux/macOS
# OR
.\venv\Scripts\activate        # Windows
```

#### Or using `conda`:

```bash
conda env create -f environment.yml
conda activate <env-name>
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
python -m spacy download en_core_web_lg
```

### 4. Run the Application

```bash
python run.py
```

Then open your browser to:
[http://127.0.0.1:5000](http://127.0.0.1:5000)

---

## 🛠️ Getting Started

### Prerequisites

* **Python** 3.9+
* **C Compiler**

  * **Windows**: Microsoft C++ Build Tools
  * **macOS**: Xcode Command Line Tools
  * **Linux**: `build-essential`

---

## ▶️ Running the Application

1. Place documents inside the `documents/` directory.

2. Start the app:

   ```bash
   python run.py
   ```

3. On first run, create an admin account.

4. Log in with your new credentials.

---

## 🔄 Core Workflow

1. **Discover Docs**: Scans the `documents/` folder for new or updated files.
2. **Process All "New"**: Queues new files for background NLP parsing.
3. **Update Browse Cache**: Builds entity index for faster navigation.

---

## ✨ Advanced Features

### 🧠 Synthesis Environment

* Dual-pane: write on the left, browse on the right.
* Highlighting text auto-generates formatted citations.
* Export to `.odt` with a complete bibliography.

### 🎧 Transcript & Media Sync

* Matches `.srt` files with `.mp3`/`.mp4` using filename prefixes.
* Transcript auto-scrolls with playback.
* Click any line to jump to that timestamp.
* Add timestamped comments.

### ⚡ GPU Acceleration

1. Install CuPy:

   ```bash
   pip install cupy-cuda11x
   ```

2. Run the check:

   ```bash
   python check_gpu.py
   ```

3. If GPU is available, enable it in:
   `Settings → System & Processing` (admin only)

---

## ⚙️ Configuration

* **Secret Key**: Auto-generated on first run, stored in `instance/` (not tracked by Git).
* **In-App Settings**:

  * Worker count
  * GPU usage
  * Parsing strategies

---

## 🔧 Management Script

Use `manage.py` for admin tasks.

Example: Reset a user's password:

```bash
python manage.py reset-password <username>
```

---

## 📄 License

This project is open source under the **MIT License**.

---

## 👤 About the Developer

Created by **Nathaniel Westveer** as a personal knowledge exploration tool.
Free to use, distribute, and modify.

