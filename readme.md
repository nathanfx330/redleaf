# Redleaf

**Redleaf is a private, local-first knowledge engine.**  
It transforms a directory of documents (PDFs, text, HTML, and transcripts) into a searchable, interconnected knowledge graph — all running **entirely on your local machine**.

Built for researchers, archivists, and knowledge workers, Redleaf makes it easy to find meaningful connections across large collections of documents while protecting your privacy.

Redleaf Engine 2.0: [https://nathanfx330.github.io/blog/posts/redleaf-engine-update/](https://nathanfx330.github.io/blog/posts/redleaf-engine-update/)

![Dashboard Screenshot](https://nathanfx330.github.io/blog/posts/redleaf-engine-update/dashboard.jpg)

---

## 📚 Table of Contents

- [Why Redleaf?](#-why-redleaf)  
- [Key Features](#-key-features)  
- [Distributing Knowledge: Precomputed Mode](#-distributing-knowledge-precomputed-mode)  
  - [The Two Roles](#the-two-roles)  
  - [Workflow for Curators (Creating & Revising)](#workflow-for-curators-creating--revising)  
  - [Workflow for Explorers (Using)](#workflow-for-explorers-using)  
  - [Becoming a Curator (Unlocking Processing Features)](#becoming-a-curator-unlocking-processing-features)  
- [Technology Stack](#-technology-stack)  
- [Getting Started](#-getting-started)  
  - [1. Prerequisites](#1-prerequisites)  
  - [2. Installation](#2-installation)  
  - [3. Two Modes of Operation](#3-two-modes-of-operation)  
  - [4. Running the Application](#4-running-the-application)  
- [Core Workflow (Standard Mode)](#-core-workflow-standard-mode)  
- [Advanced Features](#-advanced-features)  
- [Management Scripts](#-management-scripts)  
- [License](#-license)  
- [About the Developer](#-about-the-developer)  

---

## 💡 Why Redleaf?

Modern researchers often face:

- Hundreds of scattered PDFs, transcripts, and notes.  
- Difficulty recalling where a piece of information came from.  
- Time wasted re-reading documents instead of making connections.  

**Redleaf solves this** by creating a searchable, structured knowledge graph on your own computer.  
It’s **local-first**, **privacy-respecting**, and designed to let you focus on analysis rather than file management.  

---

## 🚀 Key Features

- 📦 **Precomputed & Distributable**: Package and share your entire knowledge base for others to explore.  
- 📄 **Multi-Format Document Indexing**: `.pdf`, `.html`, `.txt`, `.srt`  
- ✍️ **Synthesis Environment**: Dual-pane writing and citation  
- 📚 **Bibliographic Tools**: In-text citations and auto-generated bibliography  
- 🎙️ **Transcript & Media Sync**: Auto-scroll with local or cloud audio/video  
- ☁️ **Cloud Media Linking**: Connect local transcripts (`.srt`) to audio/video hosted on Archive.org  
- 🔍 **Full-Text Search**: Lightning-fast SQLite FTS5 queries  
- 🧠 **Entity & Relationship Extraction**: spaCy-powered NLP  
- 🗂️ **Deep Document Curation**: Tags, colors, notes, and collections  
- 👥 **Multi-User Support**: Admin/user roles with invites  
- ⚙️ **Concurrent Processing**: Multi-core, non-blocking workflows  
- ⚡ **Optional GPU Acceleration**: CUDA support for NLP  

![Entity & Relationship Extraction](https://nathanfx330.github.io/blog/posts/redleaf-engine-update/relationship.jpg)  
![PDF viewer and Entity Browser](https://nathanfx330.github.io/blog/posts/redleaf-engine-update/side_pannel.jpg)  
![Synthesis Environment](https://nathanfx330.github.io/blog/posts/redleaf-engine-update/pdf_writeup.jpg)  

---

## 📦 Distributing Knowledge: Precomputed Mode

Redleaf can be used not just as a personal tool, but as a way to distribute a fully analyzed dataset. A **Precomputed Knowledge Base** is a Redleaf repository that includes all processed data, allowing users to start exploring immediately without running heavy NLP and indexing steps.  

### The Two Roles

- **Curator:** Gathers documents, processes them, and exports the final state of the knowledge base.  
- **Explorer:** Clones the precomputed repository to search and analyze the data immediately.  

### Workflow for Curators (Creating & Revising)

1. **Revert to Curator Mode:** Delete the `precomputed.marker` file in the `instance/` directory. This re-enables all processing features on the next run.  
2. **Make Revisions:** Run `python run.py` normally. Add/remove documents, re-run processing, update tags, or build catalogs.  
3. **Export the State:** When satisfied, stop the server and run:  
   ```bash
   python bulk_manage.py export-precomputed-state
````

This generates `initial_state.sql`, `manifest.json`, and `precomputed.marker`.
4. **Commit to Git:** Add the exported files and your documents to version control:

```bash
git add documents/
git add project/precomputed_data/ instance/precomputed.marker
git commit -m "Update knowledge base with new research"
git push
```

### Workflow for Explorers (Using)

1. Clone the curator’s repository.
2. Follow the standard installation steps.
3. Run `python run.py`. Redleaf detects the precomputed state and builds the database automatically.
4. Create a personal account on the welcome screen.
5. Begin exploring immediately.

> **Note:** Data processing buttons (like **Discover Docs** and **Process All**) are intentionally disabled in Precomputed Mode.

### Becoming a Curator (Unlocking Processing Features)

If you want to add your *own* documents:

1. Stop the application.
2. Delete the `precomputed.marker` file in the `instance/` folder.
3. Restart with `python run.py`.

Processing features will now be fully enabled.

---

## 🧪 Technology Stack

| Layer                 | Technology                                               |
| :-------------------- | :------------------------------------------------------- |
| **Backend**           | Python (Flask)                                           |
| **Database**          | SQLite + FTS5                                            |
| **NLP**               | spaCy (`en_core_web_lg`)                                 |
| **Parsing**           | PyMuPDF (PDFs), BeautifulSoup4 (HTML)                    |
| **Frontend**          | HTML5, CSS3, JS (ES6+, [Tiptap.js](https://tiptap.dev/)) |
| **Async Tasks**       | `concurrent.futures.ProcessPoolExecutor`                 |
| **Citations**         | `citeproc-py`                                            |
| **External Requests** | `requests`                                               |

---

## 🛠️ Getting Started

### 1. Prerequisites

* Python **3.9+**
* [Anaconda](https://www.anaconda.com/download) or [Miniconda](https://docs.conda.io/projects/miniconda/en/latest/) (recommended)

### 2. Installation

```bash
git clone <your-repository-url>
cd <repository-directory>
conda env create -f environment.yml
conda activate redleaf-env
python -m spacy download en_core_web_lg
```

<details>
<summary>💡 Alternative (venv + pip)</summary>

```bash
python3 -m venv venv
source venv/bin/activate   # Linux/macOS
.\venv\Scripts\activate    # Windows
pip install -r requirements.txt
python -m spacy download en_core_web_lg
```

</details>

### 3. Two Modes of Operation

* **Standard Mode:** Start fresh, add and process your own documents.
* **Precomputed Mode:** Use preprocessed repositories for instant exploration.

### 4. Running the Application

```bash
python run.py
```

Then open [http://127.0.0.1:5000](http://127.0.0.1:5000).

---

## ⚙️ Core Workflow (Standard Mode)

From the dashboard you can:

* **Discover Docs**: Scan for new/modified files.
* **Process All "New"**: Extract text and metadata in the background.
* **Update Browse Cache**: Precompute relationships for fast navigation.

In Precomputed Mode, these are disabled.

---

## 🌟 Advanced Features

### ✍️ Synthesis Environment

* Dual-pane view: write on the left, cite on the right.
* Highlight text to create inline citations.
* Export to `.odt` with auto-generated bibliography.

### 🎧 Transcript & Media Sync

* Auto-pairs `.srt` with local `.mp3`/`.mp4` by filename.
* Scrolls transcript in sync with playback.
* Click any line to jump to that timestamp.
* Add timestamped comments and quotes.

#### Cloud-Based Media Linking

* Link `.srt` transcripts to audio/video hosted on **Archive.org**.

### ⚡ GPU Acceleration

If you have an NVIDIA GPU:

```bash
pip install cupy-cuda11x
python check_gpu.py
```

Enable in **Settings → System & Processing**.

---

## 🔧 Management Scripts

### `manage.py` (User Admin)

Simple admin tasks.
**Example: Reset a user password**

```bash
python manage.py reset-password <username>
```

### `bulk_manage.py` (Data & Content)

System-wide data tools. Run `python bulk_manage.py -h` for all options.

**Key Commands:**

* Export precomputed state:

  ```bash
  python bulk_manage.py export-precomputed-state
  ```
* Link local audio:

  ```bash
  python bulk_manage.py link-local-audio
  ```
* Link from Archive.org:

  ```bash
  python bulk_manage.py link-archive-org <archive-id>
  ```
* Reset transcripts (remove media links):

  ```bash
  python bulk_manage.py unpodcast
  ```

---

## 📄 License

Licensed under the **MIT License**.

---

## 👤 About the Developer

Created by **Nathaniel Westveer** as a personal tool for knowledge exploration.
Free to use, distribute, and modify.
