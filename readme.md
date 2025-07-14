# Redleaf Engine 2.0

<p align="center"><strong>A local-first, full-stack application to index, search, and synthesize your personal document collection.</strong></p>

<p align="center">
<img src="https://img.shields.io/badge/python-3.9-blue.svg" alt="Python 3.9">
<img src="https://img.shields.io/badge/spaCy-v3.6-brightgreen.svg" alt="spaCy">
<img src="https://img.shields.io/badge/Flask-v2.3-lightgrey.svg" alt="Flask">
<a href="https://github.com/nathanfx330/redleaf/blob/main/LICENSE">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License: MIT">
</a>
</p>

---

Redleaf is a private knowledge management system that transforms a directory of `.pdf`, `.html`, `.txt`, and `.srt` files into a **searchable, interconnected knowledge graph**—all running on your local machine.

Beyond simple search, Redleaf includes a built-in **Synthesis Studio**, where you can write reports, cite sources with CSL-based formatting (Chicago-style), and export your work with ease.

---

## ✨ Key Features

### 🗂️ Broad Document Support
- Supports `.pdf`, `.html`, `.txt`, and `.srt` (subtitle) files.

### 🔌 Multiple HTML Parsing Modes
- Choose between a generic content scraper or a specialized **Pipermail archive parser**.

### 🤖 Automatic Knowledge Extraction
- Uses **spaCy** to extract People, Organizations, Locations, and Dates.
- Visualizes relationships based on co-occurrence.

### 🔍 Powerful Search & Discovery
- Fast full-text search with **SQLite FTS5**.
- Browse by extracted entities, tags, and user-defined catalogs.

### ✍️ Synthesis Studio
- Rich-text editor for drafting documents.
- Click to cite text directly from your library.
- Auto-generate in-text citations and bibliographies (Chicago Author-Date).
- Export to `.odt` (OpenDocument Text) format.

### 🎛️ Rich Curation Tools
- Organize files into **Catalogs**.
- Add color-coded **Tags** and comments (public or private).
  
### ⚙️ Robust Backend
- Multi-process NLP pipeline using `ProcessPoolExecutor`.
- Optional **GPU acceleration via CUDA**.
- In-app controls for workers and hardware usage.

### 👥 Multi-User Support
- Built-in authentication system with **admin/user roles**.
- Invitation-only registration flow.

---

## 🚀 Getting Started

### Prerequisites
- [Miniconda or Anaconda](https://docs.conda.io/en/latest/)
- Python 3.9

### Clone the Repository

```bash
git clone https://github.com/nathanfx330/redleaf.git
cd redleaf
```

### Create Conda Environment

```bash
conda env create -f environment.yml
```

### Activate Environment

```bash
conda activate redleaf-env
```

### Download spaCy Language Model

```bash
python -m spacy download en_core_web_lg
```

---

## 🏁 Running the Application

1. **Add Documents**  
   Create a `documents/` folder in the project root. Add your files there—subfolders are supported.

2. **Start the App**

```bash
python run.py
```

3. **First-Time Setup**  
   Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your browser and follow the prompt to create an admin account.

4. **Index Your Library**
   - Click **1. Discover Docs** to load files.
   - Click **2. Process All ‘New’** to start indexing.
   - Once done, explore through the Discovery interface.

---

## ⚙️ Configuration

All core settings can be configured via the in-app **Settings** page (admin-only).

- **Worker Count**: Control how many parallel processes run during indexing.
- **GPU Toggle**: Enable or disable CUDA acceleration.
- **HTML Parsing Mode**: Switch between generic or Pipermail mode.

A secure secret key is automatically generated and stored in the `instance/` folder on first run—no manual setup needed.

---

## 🛠️ Command-Line Tools

### 🔐 Reset a User's Password

```bash
python manage.py reset-password <username>
```

You’ll be securely prompted to enter and confirm the new password.

### ⚡ Check GPU Availability

```bash
python check_gpu.py
```

This utility confirms whether spaCy can access your NVIDIA GPU for acceleration.

---

## 💻 Tech Stack

| Category          | Technology                            |
|-------------------|----------------------------------------|
| **Backend**       | Python, Flask                          |
| **Database**      | SQLite + FTS5                          |
| **NLP Engine**    | spaCy                                  |
| **Document Parsing** | PyMuPDF, BeautifulSoup4, ODFPy     |
| **Frontend**      | HTML5, CSS3, Vanilla JavaScript (ES6+) |
| **Text Editor**   | TipTap (Rich Text)                     |
| **Citations**     | citeproc-py, CSL: Chicago Author-Date  |
| **Tasks**         | `ProcessPoolExecutor` (multi-process)  |
| **Deployment**    | Local machine only                     |

---

MIT License

Copyright (c) 2025 Nathaniel Westveer

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights  
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell      
copies of the Software, and to permit persons to whom the Software is          
furnished to do so, subject to the following conditions:                       

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.                                

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR     
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,       
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE    
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER        
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, 
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE 
SOFTWARE.
