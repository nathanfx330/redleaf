# Redleaf

**Redleaf is a private, local-first knowledge engine.**  
It transforms a directory of documents (PDFs, text, emails, HTML, and transcripts) into a searchable, interconnected knowledge graph, complete with an integrated AI assistant—all running **entirely on your local machine**.

Built for researchers, archivists, and knowledge workers, Redleaf makes it easy to find meaningful connections, synthesize findings, and chat with your documents while protecting your privacy.

Redleaf Engine 2.0 Blog Post: [https://nathanfx330.github.io/blog/posts/redleaf-engine-update/](https://nathanfx330.github.io/blog/posts/redleaf-engine-update/)

![Dashboard Screenshot](https://nathanfx330.github.io/blog/posts/redleaf-engine-update/dashboard.jpg)

---

## 📚 Table of Contents

- [Why Redleaf?](#-why-redleaf)
- [Key Features](#-key-features)
- [Workflows: Building Your Knowledge Base](#-workflows-building-your-knowledge-base)
  - [A. The Dashboard Workflow (Simple & Interactive)](#a-the-dashboard-workflow-simple--interactive)
  - [B. The DuckDB Pipeline (Advanced & High-Throughput)](#b-the-duckdb-pipeline-advanced--high-throughput)
- [Distributing Knowledge: The Precomputed Model](#-distributing-knowledge-the-precomputed-model)
- [Technology Stack](#-technology-stack)
- [Getting Started](#-getting-started)
- [Advanced Features](#-advanced-features)
- [Management Scripts](#-management-scripts)
- [License](#-license)

---

## 💡 Why Redleaf?

Modern researchers often face hundreds of scattered PDFs, transcripts, and notes. Recalling where a piece of information came from is difficult, and time is wasted re-reading documents instead of making new connections.

**Redleaf solves this** by creating a searchable, structured knowledge graph on your own computer. It’s **local-first**, **privacy-respecting**, and designed to let you focus on analysis rather than file management.

---

## 🚀 Key Features

-   🧠 **AI Semantic Assistant**: Chat with your documents, perform semantic searches, and run complex research queries using local LLMs via Ollama.
-   ⚙️ **Dual Processing Pipelines**: Choose between a simple, web-based workflow for casual use and a high-throughput DuckDB-based pipeline for large-scale builds.
-   📦 **Precomputed & Distributable**: Package and share your entire knowledge base as a single, explorable unit.
-   📄 **Multi-Format Document Indexing**: `.pdf`, `.html`, `.txt`, `.srt`, and `.eml`.
-   ✍️ **Synthesis Environment**: A dual-pane writing studio with automatic citations.
-   🎙️ **Transcript & Media Sync**: Auto-scrolls `.srt` transcripts in sync with local or cloud-based audio/video.
-   ☁️ **Cloud Media Linking**: Connect local transcripts to media hosted on Archive.org.
-   🔍 **Hybrid Search**: Combines lightning-fast full-text search (SQLite FTS5) with AI-powered semantic vector search.
-   🕸️ **Knowledge Graph**: Automatically extracts entities (people, places, orgs) and the relationships between them using spaCy.
-   ⚡ **Optional GPU Acceleration**: CUDA support for NLP and embedding generation.
-   👥 **Multi-User Support**: Admin/user roles with a secure invitation system.

![Entity & Relationship Extraction](https://nathanfx330.github.io/blog/posts/redleaf-engine-update/relationship.jpg)

---

## ⚙️ Workflows: Building Your Knowledge Base

Redleaf offers two distinct workflows for processing your documents.

### A. The Dashboard Workflow (Simple & Interactive)

This is the standard, easy-to-use method for processing documents directly from the web interface. It's ideal for adding a few documents at a time or for users who prefer a graphical interface.

1.  **Add Files:** Place your documents in the `documents/` folder.
2.  **Discover:** Click the **"1. Discover Docs"** button on the dashboard to register the new files.
3.  **Process:** Click the **"2. Process All 'New'"** button to start the background processing job.

This workflow uses a multi-process job queue to avoid blocking the web UI.

### B. The DuckDB Pipeline (Advanced & High-Throughput)

For building or rebuilding a very large knowledge base from scratch, this powerful CLI-only pipeline provides maximum speed and efficiency. It uses DuckDB for massive parallel processing.

1.  **Reset & Discover:** Run `python curator_reset.py` to prepare a clean workspace.
2.  **Process:** Execute the full pipeline with `python curator_cli.py process-docs run-all`.
3.  **Bake:** Finalize the process by transferring the data to the live database with `python curator_cli.py bake-sqlite`.

*(For a detailed guide, see `DUCKDB_PIPELINE_GUIDE.md`)*

---

## 📦 Distributing Knowledge: The Precomputed Model

Redleaf can be used not just as a personal tool, but as a way to distribute a fully analyzed dataset. A **Precomputed Knowledge Base** is a Redleaf repository where all heavy processing has been completed, allowing users to start exploring immediately.

-   **The Curator:** Gathers and processes documents, enriches the data, and runs `python bulk_manage.py export-precomputed-state` to package the public data for distribution.
-   **The Explorer:** Clones the Curator's repository and runs `python run.py`. Redleaf automatically builds the knowledge base from the included files, allowing the Explorer to create a local account and begin their work instantly.

*(For full details, see the "Distributing Knowledge" section in the `readme.md`)*

---

## 🧪 Technology Stack

| Layer                | Technology                                                                                                    |
| :------------------- | :------------------------------------------------------------------------------------------------------------ |
| **Backend**          | Python (Flask)                                                                                                |
| **Web DB**           | SQLite + FTS5                                                                                                 |
| **Pipeline DB**      | DuckDB                                                                                                        |
| **AI / NLP**         | Ollama, spaCy (`en_core_web_lg`)                                                                              |
| **Parsing**          | PyMuPDF (PDFs), BeautifulSoup4 (HTML)                                                                         |
| **Frontend**         | HTML5, CSS3, JS (ES6+, [Tiptap.js](https://tiptap.dev/))                                                      |
| **Async Tasks**      | `concurrent.futures.ProcessPoolExecutor`                                                                      |

---

## 🛠️ Getting Started

### 1. Prerequisites

-   Python **3.9+**
-   [Anaconda](https://www.anaconda.com/download) or [Miniconda](https://docs.conda.io/projects/miniconda/en/latest/) (recommended for easy environment management)
-   **Ollama**: You must have [Ollama](https://ollama.com/) installed and running for the AI assistant and semantic search features.

### 2. Model Installation

After installing Ollama, pull the required models from the command line:

```bash
ollama pull gemma3:12b
ollama pull embeddinggemma:latest
```

### 3. Application Installation

Clone the repository and create the Conda environment.

```bash
git clone https://github.com/nathanfx330/redleaf.git
cd redleaf
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
python -m spacy download en_core_web_lg```

</details>

### 4. Running the Application

Start the Flask web server:

```bash
python run.py
```

Then open your browser to [http://127.0.0.1:5000](http://127.0.0.1:5000).

---

## 🌟 Advanced Features

-   **Semantic Assistant**: Launch a powerful CLI-based chat and research agent with `python semantic_assistant.py`.
-   **Collaborative Annotations**: "Explorers" can export their local tags and comments for a "Curator" to review and merge into the main knowledge base.
-   **Synthesis Environment**: A dual-pane view to write on the left and view/cite documents on the right. Export your work to `.odt` with an auto-generated bibliography.
-   **GPU Acceleration**: If you have an NVIDIA GPU with CUDA, install `cupy` (`pip install cupy-cuda12x`) and enable the GPU in **Settings → System & Processing**.

---

## 🔧 Management Scripts

Redleaf includes several powerful command-line tools for data management.

-   **`manage.py`**: Basic user administration (e.g., `python manage.py reset-password <username>`).
-   **`bulk_manage.py`**: System-wide data tools for linking media, exporting contributions, and creating distributable packages. Run with `-h` for all options.
-   **`curator_cli.py`**: The entry point for the high-throughput DuckDB processing pipeline.

---

## 📄 License

Licensed under the **MIT License**

## About the Developer

Created by Nathaniel Westveer as a personal tool for knowledge exploration.
Free to use, distribute, and modify.

