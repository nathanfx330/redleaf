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
- [Distributing Knowledge: The Precomputed Model](#-distributing-knowledge-the-precomputed-model)  
  - [The Two Roles](#the-two-roles)  
  - [Workflow for Curators (Creating & Revising)](#workflow-for-curators-creating--revising)  
  - [Workflow for Explorers (Using)](#workflow-for-explorers-using)  
  - [Becoming a Curator (Removing the Bracing)](#becoming-a-curator-removing-the-bracing)  
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


## ✨ From Text to Knowledge Graph: Automatic Relationship Detection


Redleaf’s most powerful feature is its ability to understand the *context* that connects the entities it finds. It doesn't just list names and places; it reads the sentences where they appear and intelligently infers the relationship between them.

> For example, imagine you are researching a historical figure by reading their digitized correspondence. A letter might contain the sentence:
>
> "After the telegram arrived from **London**, a meeting was arranged between **Mr. Alistair Finch** and the **ambassador** at the **Blackwood Estate**."
>
> Redleaf instantly pieces together the narrative, creating a map of explorable connections:
> *   `[Mr. Alistair Finch]` → `and the` → `[ambassador]`
> *   `[ambassador]` → `at the` → `[Blackwood Estate]`

Now, you can click on the **Blackwood Estate** and instantly see every other document in your collection that mentions it, who else was seen there, and what other events took place on the property.

This transforms your research from a linear reading process into an interactive exploration. You can follow a web of interconnected evidence, discovering patterns and links across your entire collection that would be impossible to find with simple keyword search.
![Entity & Relationship Extraction](https://nathanfx330.github.io/blog/posts/redleaf-engine-update/relationship.jpg)  



![PDF viewer and Entity Browser](https://nathanfx330.github.io/blog/posts/redleaf-engine-update/side_pannel.jpg)  
![Synthesis Environment](https://nathanfx330.github.io/blog/posts/redleaf-engine-update/pdf_writeup.jpg)  

---

## 📦 Distributing Knowledge: The Precomputed Model

Redleaf can be used not just as a personal tool, but as a way to distribute a fully analyzed dataset—like a museum exhibit in a box. A **Precomputed Knowledge Base** is a Redleaf repository where all heavy NLP and indexing work has already been done, allowing users to start exploring immediately.

### The Two Roles

*   **The Curator:** Gathers and processes documents, enriches the data, and exports the final "exhibit" for distribution.
*   **The Explorer:** Clones the precomputed repository to immediately search, analyze, and add their own local annotations to the data.

### Workflow for Curators (Creating & Revising)

1.  **Work Locally:** Use Redleaf in its standard, fully-featured mode. Add documents, run the processing workflow from the dashboard, update tags, and import contributions from your team.
2.  **Export the State:** When you are ready to publish, stop the server and run the export command. **This is a destructive action for your local instance**—it will wipe all user accounts to ensure the distributed package is clean.
    ```bash
    python bulk_manage.py export-precomputed-state
    ```
    This generates `instance/initial_state.sql` (the public data) and `instance/precomputed.marker`.
3.  **Commit to Git:** Add the exported files and your documents to your Git repository. This captures the new state of your knowledge base.

### Workflow for Explorers (Using)

1.  Clone the Curator's repository.
2.  Follow the standard installation steps.
3.  Run `python run.py`. Redleaf automatically detects the precomputed state and builds the database from the included files.
4.  When you open your browser, you will be greeted by a special welcome screen. Create your personal, local user account and log in.
5.  Begin exploring immediately. The data processing buttons on the dashboard and advanced system settings will be disabled ("braced for transit") to keep the focus on discovery.

### Becoming a Curator (Removing the Bracing)

If you receive a precomputed knowledge base and later decide you want to add your *own* documents and take over as the curator, you can easily unlock the application's full features.

1.  Stop the application.
2.  Navigate to the `instance/` directory.
3.  **Delete the `precomputed.marker` file.**

The next time you start the application, all processing features will be fully enabled.

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

### 🤝 Collaborative Annotations

*   **Export Contributions:** An Explorer can export their personal tags and comments into a lightweight `.json` file.
*   **Review & Merge:** A Curator can upload this file to a special review panel, where they can approve or reject each suggestion before merging it into the main knowledge base.

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

#### Export for Distribution (Curator):
```bash
# Wipes local users, then exports public data for distribution.
python bulk_manage.py export-precomputed-state
```

#### Export Annotations (Explorer):
```bash
# Exports your local tags/comments to a shareable .json file.
python bulk_manage.py export-contributions <your-username>
```

#### Podcast & Media Linking:
```bash
# Link podcast XML metadata to SRTs.
python bulk_manage.py link-podcast-metadata

# Link local .mp3/.mp4 files to SRTs by filename.
python bulk_manage.py link-local-audio

# Link SRTs to media on Archive.org.
python bulk_manage.py link-archive-org <archive-id>

# [DESTRUCTIVE] Remove all podcast-related links from SRTs.
python bulk_manage.py unpodcast
```

---

## 📄 License

Licensed under the **MIT License**.

---

## 👤 About the Developer

Created by **Nathaniel Westveer** as a personal tool for knowledge exploration.  
Free to use, distribute, and modify.

