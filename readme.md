**Redleaf is a private, local-first knowledge engine.**
It transforms a directory of documents (PDFs, text, HTML, and transcripts) into a searchable, interconnected knowledge graph — all running **entirely on your local machine**.

Built for researchers, archivists, and knowledge workers, Redleaf makes it easy to find meaningful connections across large collections of documents while protecting your privacy.

Redleaf Engine 2.0: https://nathanfx330.github.io/blog/posts/redleaf-engine-update/

![Dashboard Screenshot](https://nathanfx330.github.io/blog/posts/redleaf-engine-update/dashboard.jpg)
---

## 📚 Table of Contents

*   [Why Redleaf?](#why-redleaf)
*   [Key Features](#key-features)
*   [Technology Stack](#technology-stack)
*   [Getting Started](#getting-started)
    *   [1. Prerequisites](#1-prerequisites)
    *   [2. Installation](#2-installation)
    *   [3. Running the Application](#3-running-the-application)
*   [Core Workflow](#core-workflow)
*   [Advanced Features](#advanced-features)
    *   [Synthesis Environment](#synthesis-environment)
    *   [Transcript & Media Sync](#transcript--media-sync)
    *   [GPU Acceleration](#gpu-acceleration)
*   [Configuration](#configuration)
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
![Synthesis Environment](https://nathanfx330.github.io/blog/posts/redleaf-engine-update/pdf_writeup.jpg)

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

### 3. Running the Application

1.  Add your documents to the `documents/` directory.
2.  Start the local server:

```bash
python run.py
```

3.  Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your browser.
4.  Follow the prompt to create your administrator account.

---

## ⚙️ Core Workflow

From the dashboard, you can:

*   **Discover Docs**: Scan and detect new/modified files.
*   **Process All "New"**: Extract text and metadata via background tasks.
*   **Update Browse Cache**: Precompute relationships for fast navigation.

---

## 🌟 Advanced Features

### ✍️ Synthesis Environment

*   Dual-pane view: write on the left, cite on the right.
*   Highlight text to create inline citations.
*   Export to `.odt` with auto-generated bibliography.

---

### 🎧 Transcript & Media Sync

*   Auto-pairs `.srt` with local `.mp3` or `.mp4` files based on filename.
*   Scrolls transcript in sync with media playback.
*   Click any line in the transcript to jump to that timestamp in the media.
*   Add timestamped comments and quotes.

#### Cloud-Based Media Re-linking

For large collections, storing media locally is often impractical. Redleaf can link your local `.srt` transcripts to audio/video files hosted on **Archive.org**. This saves disk space while keeping your media fully integrated. This is done via the `bulk_manage.py` script (see Management Scripts section below).

---

### ⚡ GPU Acceleration

If you have a compatible NVIDIA GPU:

```bash
pip install cupy-cuda11x
```

Verify GPU access:

```bash
python check_gpu.py
```

Enable in: **Settings → System & Processing**

---

## ⚙️ Configuration

*   **Secret Key**: Auto-generated on first run, stored in `instance/` (ignored by Git).
*   **Admin Settings**: Configure background workers, HTML parser, and GPU use in-app.

---

## 🔧 Management Scripts

Redleaf includes powerful command-line scripts for administration and bulk data operations.

### `manage.py` (User Admin)

Use this for simple, single-user administrative tasks.

**Example: Reset a user's password**
```bash
python manage.py reset-password <username>
```

### `bulk_manage.py` (Data & Content)

Use this for powerful, system-wide data linking and management. Run `python bulk_manage.py -h` to see all commands.

**Key Commands:**

*   **Link Podcast Metadata & Media:** Scans local `.xml` podcast feeds to automatically link bibliographic metadata (title, author, date) and the original web media URL to your unprocessed `.srt` files. This is the recommended first step before linking local or archived media.
    ```bash
    python bulk_manage.py link-podcast-metadata
    ```

*   **Link Local Audio:** Scans for `.mp3` files in your `documents` directory that match unlinked `.srt` files.
    ```bash
    python bulk_manage.py link-local-audio
    ```

*   **Link from Archive.org:** Scans an Archive.org item and links all matching `.srt` files to the hosted audio. This will **overwrite** existing links.
    ```bash
    # Usage: python bulk_manage.py link-archive-org <archive-id>
    # Example for archive.org/details/example-podcast-archive
    python bulk_manage.py link-archive-org example-podcast-archive
    ```

*   **Reset Transcripts:** A destructive operation to remove all metadata and media links from every `.srt` file.
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
