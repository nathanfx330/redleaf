# Redleaf Engine

Redleaf is a self-hosted, personal document analysis tool built with Python and Flask. It's designed to run entirely on your local machine, allowing you to build a private, searchable knowledge base. Simply place your documents (PDF, HTML, TXT) in the designated folder, and use the web interface to process, index, search, browse, and organize them.

It uses **spaCy** for advanced Natural Language Processing and stores all data in a local **SQLite** database, making the entire system secure and self-contained.


## Key Features

*   **Multi-Format Ingestion:** Process `.pdf`, `.html`, and `.txt` files.
*   **Automated NLP Pipeline:** Uses spaCy to automatically extract named entities (people, places, organizations) and infer relationships between them.
*   **Full-Text Search:** Powerful search across all ingested documents, powered by SQLite FTS5 with highlighted snippets.
*   **Robust Background Processing:** A sophisticated manager handles long-running, CPU-intensive NLP tasks in parallel processes without blocking the web interface.
*   **GPU Acceleration:** Optional support for CUDA-enabled NVIDIA GPUs to dramatically speed up processing.
*   **Interactive UI:** A fast, API-driven frontend to explore entities, relationships, and search results.
*   **Integrated Document Viewer:** View original documents directly within the application, with a custom paginated viewer for text and extracted HTML content.
*   **Rich Curation Tools:**
    *   **Catalogs:** Group related documents into custom collections (e.g., "Project X," "Research Papers").
    *   **Tags:** Apply multiple tags to documents for fine-grained organization.
    *   **Notes & Comments:** Add private notes or public comments to any document.
*   **Secure & Multi-User:** Features a full user authentication system with admin roles and invitation-based registration.

## Technology Stack

*   **Backend:** Python 3, Flask, spaCy
*   **Database:** SQLite
*   **Document Parsers:** PyMuPDF (Fitz), BeautifulSoup4
*   **Frontend:** Vanilla JavaScript (ES6+), HTML5, CSS3

## Setup & Installation

Follow these steps to get Redleaf running on your local machine.

**Prerequisites:** Python 3.7+ and Git.

**1. Clone the Repository**
```bash
git clone https://github.com/your-username/redleaf-engine.git
cd redleaf-engine
```

**2. Create and Activate a Virtual Environment (Recommended)**
```bash
# On macOS/Linux:
python3 -m venv venv
source venv/bin/activate

# On Windows:
python -m venv venv
.\venv\Scripts\activate
```

**3. Install Dependencies**
Install all required Python packages from the `requirements.txt` file.
```bash
pip install -r requirements.txt
```

**4. Download the NLP Model**
Redleaf requires a spaCy language model for its processing pipeline.
```bash
python -m spacy download en_core_web_lg
```

**5. Add Your Documents**
Create a `documents` folder in the project root if it doesn't exist. Place any PDF, TXT, or HTML files you want to process inside this folder.
```bash
mkdir documents
```
*(Note: The `documents/` folder is ignored by Git, so you will need to add your own files.)*

**6. Run the Application**
```bash
python app.py
```
The application will start, perform a one-time setup (creating the database and a secret key), and will then be accessible at **`http://localhost:5000`**.

## Getting Started

1.  **Create Admin Account:** When you first visit `http://localhost:5000`, you will be prompted to create the primary administrator account.
2.  **Discover Documents:** After logging in, you will be on the Dashboard. Click **"1. Discover Docs"** to have Redleaf find and register the files in your `documents` folder.
3.  **Process Documents:** Click **"2. Process All 'New'"** to begin indexing and NLP extraction. You can monitor the progress on the dashboard.
4.  **Update Cache:** Once processing is complete, click **"3. Update Browse Cache"** to aggregate the new data for the Discovery page.
5.  **Explore!** Navigate to the Discovery tab to search and browse your newly created knowledge base.

## Directory Structure

*   `./documents/`: **Place your source documents here.** Subdirectories are supported.
*   `./instance/`: **Holds non-public files**, like the database and secret key. This folder is critical and should *not* be committed to version control.
*   `knowledge_base.db`: The SQLite database containing all indexed data, users, and curation metadata.
*   `app.py`: The main Flask application, handling web routes and the UI.
*   `processing_pipeline.py`: The core logic for document processing and NLP.
*   `storage_setup.py`: Defines the database schema and handles initial creation.
*   `manage.py`: A command-line tool for administrative tasks (e.g., password resets).
*   `./static/`: Contains CSS and other static assets.
*   `./templates/`: Contains all Jinja2 HTML templates.

## License

MIT License

Copyright (c) 2025 Nathaniel Westveer

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.