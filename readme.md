# Redleaf

**Redleaf is a private, local-first knowledge engine.**
It transforms a directory of documents (PDFs, text, emails, HTML, and transcripts) into a searchable, interconnected knowledge graph, complete with an integrated AI assistant—all running **entirely on your local machine**.

Built for researchers, archivists, and knowledge workers, Redleaf makes it easy to find meaningful connections, synthesize findings, and chat with your documents while protecting your privacy.

**Redleaf Engine 2.0 Blog Post:**
[https://nathanfx330.github.io/blog/posts/redleaf-engine-update/](https://nathanfx330.github.io/blog/posts/redleaf-engine-update/)

![Dashboard Screenshot](https://nathanfx330.github.io/blog/posts/redleaf-engine-update/dashboard.jpg)

---

## 📚 Table of Contents

* [Why Redleaf?](#-why-redleaf)
* [Key Features](#-key-features)
* [Workflows: Building Your Knowledge Base](#-workflows-building-your-knowledge-base)

  * [A. The Dashboard Workflow (Simple & Interactive)](#a-the-dashboard-workflow-simple--interactive)
  * [B. The DuckDB Pipeline (Advanced & High-Throughput)](#b-the-duckdb-pipeline-advanced--high-throughput)
* [Distributing Knowledge: The Precomputed Model](#-distributing-knowledge-the-precomputed-model)
* [Technology Stack](#-technology-stack)
* [Getting Started](#-getting-started)
* [Advanced Features](#-advanced-features)
* [Management Scripts](#-management-scripts)
* [License](#-license)
* [About the Developer](#about-the-developer)

---

## 💡 Why Redleaf?

Modern researchers often face hundreds of scattered PDFs, transcripts, and notes. Recalling where a piece of information came from is difficult, and time is wasted re-reading documents instead of making new connections.

**Redleaf solves this** by creating a searchable, structured knowledge graph on your own computer. It’s **local-first**, **privacy-respecting**, and designed to let you focus on analysis rather than file management.

---

## 🚀 Key Features

* 🧠 **AI Semantic Assistant** – Chat with your documents using local LLMs via Ollama.
* ⚙️ **Dual Processing Pipelines** – Simple dashboard workflow or high-throughput DuckDB batch pipeline.
* 📦 **Precomputed & Distributable** – Package and share the entire finished knowledge base.
* 📄 **Multi-Format Indexing** – `.pdf`, `.html`, `.txt`, `.srt`, `.eml`.
* ✍️ **Synthesis Environment** – Dual-pane writing studio with automatic citations.
* 🎙️ **Transcript & Media Sync** – Auto-scrolls `.srt` transcripts while playing media.
* ☁️ **Cloud Media Linking** – Seamlessly connect transcripts to media on Archive.org.
* 🔍 **Hybrid Search** – FTS5 + semantic search + embedding vectors.
* 🕸️ **Knowledge Graph** – Automatic entity + relationship extraction using spaCy.
* ⚡ **GPU Acceleration Optional** – CUDA for embeddings & NLP.
* 👥 **Multi-User Support** – Admin/User roles + invitation flow.

![Entity & Relationship Extraction](https://nathanfx330.github.io/blog/posts/redleaf-engine-update/relationship.jpg)

---

## ⚙️ Workflows: Building Your Knowledge Base

Redleaf offers two workflows:

---

### A. The Dashboard Workflow (Simple & Interactive)

1. **Add Files** – Put documents into `documents/`
2. **Discover** – Click **"1. Discover Docs"**
3. **Process** – Click **"2. Process All 'New'"**

A multi-process job queue keeps the UI responsive.

---

### B. The DuckDB Pipeline (Advanced & High-Throughput)

For very large datasets or full rebuilds:

```bash
python curator_reset.py
python curator_cli.py process-docs run-all
python curator_cli.py bake-sqlite
```

See: **DUCKDB_PIPELINE_GUIDE.md**

---

## 📦 Distributing Knowledge: The Precomputed Model

A Redleaf **Precomputed Knowledge Base** is a ready-to-explore, fully processed dataset.

**Curator:**

* Processes documents
* Runs:

  ```bash
  python bulk_manage.py export-precomputed-state
  ```
* Publishes the package

**Explorer:**

* Clones the repository
* Runs:

  ```bash
  python run.py
  ```
* Redleaf auto-builds their local copy

---

## 🧪 Technology Stack

| Layer       | Technology                       |
| ----------- | -------------------------------- |
| Backend     | Python (Flask)                   |
| Web DB      | SQLite + FTS5                    |
| Pipeline DB | DuckDB                           |
| AI / NLP    | Ollama, spaCy (`en_core_web_lg`) |
| Parsing     | PyMuPDF, BeautifulSoup4          |
| Frontend    | HTML5, CSS, JS, Tiptap           |
| Async       | ProcessPoolExecutor              |

---

## 🛠️ Getting Started

### 1. Prerequisites

* Python **3.9+**
* Conda recommended
* Ollama installed & running

### 2. Required Models

```bash
ollama pull gemma3:12b
ollama pull embeddinggemma:latest
```

### 3. Application Installation

```bash
git clone https://github.com/nathanfx330/redleaf.git
cd redleaf
conda env create -f environment.yml
conda activate redleaf-env
python -m spacy download en_core_web_lg
```

---

### 💡 Alternative (venv + pip)

<details>
<summary>Click to expand</summary>

```bash
python3 -m venv venv
source venv/bin/activate   # Linux/macOS
.\venv\Scripts\activate    # Windows
pip install -r requirements.txt
python -m spacy download en_core_web_lg
```

</details>

---

### 4. Run the Application

```bash
python run.py
```

Then go to:

**[http://127.0.0.1:5000](http://127.0.0.1:5000)**

---

## 🌟 Advanced Features

* **Semantic Assistant:**

  ```bash
  python semantic_assistant.py
  ```

* **Collaborative Annotations:**
  Users export contributions to the curator.

* **Synthesis Environment:**
  Dual-pane writing + automatic bibliography.

* **GPU Acceleration:**

  ```bash
  pip install cupy-cuda12x
  ```

  Then enable via **Settings → System & Processing**

---

## 🔧 Management Scripts

* `manage.py` – User admin
* `bulk_manage.py` – System-wide tools
* `curator_cli.py` – DuckDB pipeline entrypoint

---

## 📄 License

**MIT License**

---

## About the Developer

Created by **Nathaniel Westveer**.
Free to use, distribute, and modify.
