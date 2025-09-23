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
    *   [Becoming a Curator (Unlocking Processing Features)](#becoming-a-curator-unlocking-processing-features)
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

1.  **Revert to Curator Mode:** To make changes, you must first switch your local instance back to the fully-functional mode. Navigate to the `instance/` directory and **delete the `precomputed.marker` file**. This will re-enable all processing features the next time you run the app.

2.  **Make Revisions:** Run the application (`python run.py`) and use it normally. Add/remove documents, re-run the processing workflow from the dashboard, update tags, create catalogs, etc.

3.  **Export the State:** Once you are satisfied with the changes, stop the server and run the export command:
    ```bash
    python bulk_manage.py export-precomputed-state
    ```
    The script will prompt you for your name and create the necessary files (`initial_state.sql`, `manifest.json`, and `precomputed.marker`).

4.  **Commit to Git:** Commit all the generated files along with your source documents to your Git repository. This captures the new, updated state of your knowledge base.

### Workflow for Explorers (Using)

The experience for an end-user is designed to be as simple as possible.

1.  Clone the Curator's repository.
2.  Follow the standard installation steps.
3.  Run `python run.py`. The application will automatically build its database from the included data files.
4.  When you open the browser, you will be greeted by a special welcome screen. Create your personal user account and log in.

Once you are on the dashboard, you will notice that the data processing buttons (**Discover Docs**, **Process All**, etc.) are **greyed out and disabled**. This is intentional. It keeps the focus on discovery and ensures that you can explore the curated data immediately without any complex setup or processing.

### Becoming a Curator (Unlocking Processing Features)

If you receive a precomputed knowledge base and later decide you want to add your *own* documents and take over the role of curator, you can easily switch the application to its fully functional Standard Mode.

**To unlock all processing features:**
1.  Stop the application.
2.  Navigate to the `instance/` directory inside your project folder.
3.  **Delete the `precomputed.marker` file.**

The next time you start the application with `python run.py`, all processing buttons on the dashboard and system options in the Settings page will be fully enabled.

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

In Precomputed Mode, these features are disabled to keep the focus on discovery, but can be unlocked by following the steps in the "Becoming a Curator" section.

