# Redleaf Engine

**Redleaf is a private, local-first knowledge engine.**  
It transforms a directory of documents (PDFs, text, HTML, and transcripts) into a searchable, interconnected knowledge graph — all running **entirely on your local machine**.

Built for researchers, archivists, and knowledge workers, Redleaf makes it easy to find meaningful connections across large collections of documents while protecting your privacy.

---

## Table of Contents

- [Why Redleaf?](#why-redleaf)
- [Key Features](#key-features)
- [Technology Stack](#technology-stack)
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

## Getting Started

### 1. Prerequisites

- **Python 3.9+**
- **Conda** (Recommended, via [Anaconda](https://www.anaconda.com/download) or [Miniconda](https://docs.conda.io/projects/miniconda/en/latest/))

### 2. Installation

The recommended setup uses the provided `environment.yml` file, which creates a self-contained Conda environment with all necessary dependencies.

#### **Step 1: Clone the Repository**
```bash
git clone <your-repository-url>
cd <repository-directory>
Step 2: Create and Activate the Conda Environment

This single command creates the redleaf-env environment and installs all required packages from the environment.yml file.

code
Bash
download
content_copy
expand_less
IGNORE_WHEN_COPYING_START
IGNORE_WHEN_COPYING_END
conda env create -f environment.yml

Then, activate it:

code
Bash
download
content_copy
expand_less
IGNORE_WHEN_COPYING_START
IGNORE_WHEN_COPYING_END
conda activate redleaf-env
Step 3: Download the NLP Model

Finally, you must download the spaCy language model that Redleaf uses for entity extraction.

code
Bash
download
content_copy
expand_less
IGNORE_WHEN_COPYING_START
IGNORE_WHEN_COPYING_END
python -m spacy download en_core_web_lg
<details>
<summary><b>Alternative Installation (using venv and pip)</b></summary>


If you prefer not to use Conda, you can use a standard Python virtual environment. You may need to manually install a C++ compiler for your system if packages fail to build.

Create and activate a virtual environment:

code
Bash
download
content_copy
expand_less
IGNORE_WHEN_COPYING_START
IGNORE_WHEN_COPYING_END
# Create the environment
python3 -m venv venv

# Activate it (Linux/macOS)
source venv/bin/activate

# Activate it (Windows)
.\venv\Scripts\activate

Install dependencies from requirements.txt:

code
Bash
download
content_copy
expand_less
IGNORE_WHEN_COPYING_START
IGNORE_WHEN_COPYING_END
pip install -r requirements.txt

Download the NLP model:

code
Bash
download
content_copy
expand_less
IGNORE_WHEN_COPYING_START
IGNORE_WHEN_COPYING_END
python -m spacy download en_core_web_lg
</details>

3. Running the Application

Add your files: Place the documents you want to analyze into the documents/ directory.

Start the server:

code
Bash
download
content_copy
expand_less
IGNORE_WHEN_COPYING_START
IGNORE_WHEN_COPYING_END
python run.py

Open in browser: Navigate to http://127.0.0.1:5000

First-time setup: The application will prompt you to create your administrator account.

Core Workflow

The main workflow buttons are on the dashboard:

Discover Docs: Scans the documents/ directory to find new or modified files and adds them to the registry.

Process All "New": Queues all unregistered documents for background processing (text extraction, NLP, etc.).

Update Browse Cache: After processing, this aggregates all extracted entities to make the Discovery page fast and responsive.

Advanced Features
Synthesis Environment

A dual-pane workspace: write your report on the left while viewing and citing your source documents on the right.

Highlight text in a source document to automatically create a properly formatted in-text citation in your report.

Export your final report to .odt (OpenDocument Text) format, with a complete bibliography automatically generated.

Transcript & Media Sync

Automatically pairs .srt transcript files with matching .mp3 or .mp4 files (based on the same filename).

The transcript automatically scrolls along with audio/video playback.

Click any line in the transcript to jump the media to that exact timestamp.

Add comments that are tied to specific timestamps in the media.

GPU Acceleration

If you have an NVIDIA GPU with CUDA drivers, you can install the GPU-enabled version of CuPy. For CUDA 11.x:

code
Bash
download
content_copy
expand_less
IGNORE_WHEN_COPYING_START
IGNORE_WHEN_COPYING_END
pip install cupy-cuda11x

Run the included script to verify that spaCy can access your GPU:

code
Bash
download
content_copy
expand_less
IGNORE_WHEN_COPYING_START
IGNORE_WHEN_COPYING_END
python check_gpu.py

If the check is successful, enable the feature in Settings → System & Processing.

Configuration

Secret Key: A unique secret key is automatically generated on the first run and stored in the instance/ folder. This file is not tracked by Git for security.

In-App Settings: The administrator can control the number of background worker processes, GPU acceleration, and HTML parsing strategy directly from the Settings page.

Management Script

The project includes manage.py for command-line administrative tasks.

Example: Reset a user's password.

code
Bash
download
content_copy
expand_less
IGNORE_WHEN_COPYING_START
IGNORE_WHEN_COPYING_END
python manage.py reset-password <username>
License

This project is open source under the MIT License.

About the Developer

Created by Nathaniel Westveer as a personal tool for knowledge exploration.
It is free to use, distribute, and modify.

