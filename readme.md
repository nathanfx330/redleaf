# Redleaf

**Redleaf is a private, local-first knowledge engine.**  
It transforms a directory of documents (PDFs, text, HTML, and transcripts) into a searchable, interconnected knowledge graph — all running **entirely on your local machine**.

Built for researchers, archivists, and knowledge workers, Redleaf makes it easy to find meaningful connections across large collections of documents while protecting your privacy.

Redleaf Engine 2.0: [https://nathanfx330.github.io/blog/posts/redleaf-engine-update/](https://nathanfx330.github.io/blog/posts/redleaf-engine-update/)

![Dashboard Screenshot](https://nathanfx330.github.io/blog/posts/redleaf-engine-update/dashboard.jpg)

---

## 📚 Table of Contents

*   [Why Redleaf?](#why-redleaf)
*   [Key Features](#key-features)
*   [Distributing Knowledge: Precomputed Mode](#distributing-knowledge-precomputed-mode)
    *   [The Two Roles](#the-two-roles)
    *   [Workflow for Curators (Creating & Revising)](#workflow-for-curators-creating--revising)
    *   [Workflow for Explorers (Using)](#workflow-for-explorers-using)
*   [Technology Stack](#technology-stack)
*   [Getting Started](#getting-started)
    *   [1. Prerequisites](#1-prerequisites)
    *   [2. Installation](#2-installation)
    *   [3. Two Modes of Operation](#3-two-modes-of-operation)
    *   [4. Running the Application](#4-running-the-application)
*   [Core Workflow (Standard Mode)](#core-workflow-standard-mode)
*   [Advanced Features](#advanced-features)
*   [Management Scripts](#management-scripts)
*   [License](#license)
*   [About the Developer](#about-the-developer)

---

## 💡 Why Redleaf?

Modern researchers often face:

*   Hundreds of scattered PDFs, transcripts, and notes.
*   Difficulty recalling where a piece of information came from.
*   Time wasted re-reading documents instead of making connections.

**Redleaf solves this** by creating a searchable, structured knowledge graph on your own computer.  
It’s **local-first**, **privacy-respecting**, and designed to let you focus on analysis rather than file management.

---

## 🚀 Key Features

*   📦 **Precomputed & Distributable**: Package and share your entire knowledge base for others to explore.
*   📄 **Multi-Format Document Indexing**: `.pdf`, `.html`, `.txt`, `.srt`
*   ✍️ **Synthesis Environment**: Dual-pane writing and citation
*   📚 **Bibliographic Tools**: In-text citations and auto-generated bibliography
*   🎙️ **Transcript & Media Sync**: Auto-scroll with local or cloud audio/video
*   ☁️ **Cloud Media Linking**: Connect local transcripts (`.srt`) to audio/video hosted on Archive.org
*   🔍 **Full-Text Search**: Lightning-fast SQLite FTS5 queries
*   🧠 **Entity & Relationship Extraction**: spaCy-powered NLP
*   🗂️ **Deep Document Curation**: Tags, colors, notes, and collections
*   👥 **Multi-User Support**: Admin/user roles with invites
*   ⚙️ **Concurrent Processing**: Multi-core, non-blocking workflows
*   ⚡ **Optional GPU Acceleration**: CUDA support for NLP

![Entity & Relationship Extraction](https://nathanfx330.github.io/blog/posts/redleaf-engine-update/relationship.jpg)
![PDF viewer and Entity Broswer](https://nathanfx330.github.io/blog/posts/redleaf-engine-update/side_pannel.jpg)
![Synthesis Environment](https://nathanfx330.github.io/blog/posts/redleaf-engine-update/pdf_writeup.jpg)

---

## 📦 Distributing Knowledge: Precomputed Mode

Redleaf can be used not just as a personal tool, but as a way to distribute a fully analyzed dataset. A **Precomputed Knowledge Base** is a Redleaf repository that includes all the processed data, allowing users to start exploring immediately without needing to perform the time-consuming NLP and indexing steps.

### The Two Roles

*   **The Curator (You):** The person who gathers documents, processes them, and exports the final state of the knowledge base for distribution.
*   **The Explorer (End-User):** The person who clones the precomputed repository to explore, search, and analyze the data.

### Workflow for Curators (Creating & Revising)

You can update and re-export your knowledge base at any time.

1.  **Revert to Curator Mode:** To make changes, delete the `precomputed.marker` file in the `instance/` directory. This will re-enable all processing features on next run.
2.  **Make Revisions:** Run the application (`python run.py`) and use it normally. Add/remove documents, re-run the processing workflow, update tags, create catalogs, etc.
3.  **Export the State:** Stop the server and run:
    ```bash
    python bulk_manage.py export-precomputed-state
    ```
    This generates `initial_state.sql`, `manifest.json`, and `precomputed.marker`.
4.  **Commit to Git:** Add the exported files and documents to your repository:
    ```bash
    git add documents/
    git add project/precomputed_data/ instance/precomputed.marker
    git commit -m "Update knowledge base with new research"
    git push
    ```

### Workflow for Explorers (Using)

1.  Clone the Curator's repository.
2.  Follow the standard installation steps (create environment, download spaCy model).
3.  Run `python run.py`.
4.  Redleaf detects the precomputed state and builds the local database.
5.  Create a personal account on the welcome screen.
6.  Begin exploring the fully indexed knowledge base immediately.

---

## 🧪 Technology Stack

| Layer | Technology |
| :--- | :--- |
| **Backend** | Python (Flask) |
| **Database** | SQLite + FTS5 |
| **NLP** | spaCy (`en_core_web_lg`) |
| **Parsing** | PyMuPDF (PDFs), BeautifulSoup4 (HTML) |
| **Frontend** | HTML5, CSS3, JS (ES6+, [Tiptap.js](https://tiptap.dev/)) |
| **Async Tasks** | `concurrent.futures.ProcessPoolExecutor` |
| **Citations** | `citeproc-py` |
| **External Requests**| `requests` |

---

## 🛠️ Getting Started

### 1. Prerequisites

*   Python **3.9+**
*   [Anaconda](https://www.anaconda.com/download) or [Miniconda](https://docs.conda.io/projects/miniconda/en/latest/) (recommended)

---

### 2. Installation

#### Clone the Repository

```bash
git clone <your-repository-url>
cd <repository-directory>
```

#### Create and Activate the Conda Environment

```bash
conda env create -f environment.yml
conda activate redleaf-env
```

#### Download the NLP Model

```bash
python -m spacy download en_core_web_lg
```

<details>
<summary><strong>💡 Alternative Installation (venv + pip)</strong></summary>

Create a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate        # Linux/macOS
# OR
.\venv\Scripts\activate         # Windows
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Download the spaCy model:

```bash
python -m spacy download en_core_web_lg
```

</details>

---

### 3. Two Modes of Operation

Redleaf can run in:

*   **Standard Mode:** For new projects. Creates an empty database. You add and process documents yourself.
*   **Precomputed Mode:** For cloned repositories with preprocessed data. Redleaf builds the local database from included files, letting you explore immediately.

---

### 4. Running the Application

1.  In Standard Mode, add documents to `documents/`.
2.  Start the local server:

```bash
python run.py
```

3.  Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your browser.
4.  Follow the on-screen prompts to create your account.

---

## ⚙️ Core Workflow (Standard Mode)

When running in Standard Mode, you can use the dashboard to:

*   **Discover Docs**: Scan and detect new/modified files.
*   **Process All "New"**: Extract text and metadata via background tasks.
*   **Update Browse Cache**: Precompute relationships for fast navigation.

> In Precomputed Mode, these features are disabled, as the work has already been done by the curator.

---

## 🌟 Advanced Features

### ✍️ Synthesis Environment

*   Dual-pane view: write on the left, cite on the right.
*   Highlight text to create inline citations.
*   Export to `.odt` with auto-generated bibliography.

### 🎧 Transcript & Media Sync

*   Auto-pairs `.srt` with local `.mp3` or `.mp4` files based on filename.
*   Scrolls transcript in sync with media playback.
*   Click any line in the transcript to jump to that timestamp in the media.
*   Add timestamped comments and quotes.

#### Cloud-Based Media Re-linking

Redleaf can link local `.srt` transcripts to audio/video files hosted on **Archive.org**. This saves disk space while keeping your media fully integrated.

### ⚡ GPU Acceleration

If you have a compatible NVIDIA GPU:

```bash
pip install cupy-cuda11x
python check_gpu.py
```

Enable in: **Settings → System & Processing**

---

## 🔧 Management Scripts

### `manage.py` (User Admin)

Simple, single-user administrative tasks.

**Example: Reset a user's password**

```bash
python manage.py reset-password <username>
```

### `bulk_manage.py` (Data & Content)

Use this for system-wide data linking and management. Run `python bulk_manage.py -h` to see all commands.

**Key Commands:**

*   **Export Precomputed State:** Package the public state of the database for distribution.
    ```bash
    python bulk_manage.py export-precomputed-state
    ```

*   **Link Local Audio:** Match `.mp3` files to unlinked `.srt` files.
    ```bash
    python bulk_manage.py link-local-audio
    ```

*   **Link from Archive.org:** Match `.srt` files to hosted audio. Overwrites existing links.
    ```bash
    python bulk_manage.py link-archive-org <archive-id>
    ```

*   **Reset Transcripts:** Remove all metadata and media links from `.srt` files.
    ```bash
    python bulk_manage.py unpodcast
    ```

---

## 📄 License

This project is licensed under the **MIT License**.

---

## 👤 About the Developer

Created by **Nathaniel Westveer** as a personal tool for knowledge exploration.  
It is free to use, distribute, and modify.
