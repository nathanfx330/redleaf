Redleaf Engine v2.0
<p align="center">
<strong>A local-first, full-stack application to index, search, and explore your personal document collection.</strong>
</p>

<p align="center">
<img src="https://img.shields.io/badge/python-3.7+-blue.svg" alt="Python 3.7+">
<img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License: MIT">
</p>


Redleaf transforms a directory of PDFs, HTML, and text files into a searchable, interconnected knowledge graph that runs entirely on your local machine. It uses a robust Python Flask backend for heavy NLP tasks and a lightweight JavaScript frontend for a fast, responsive user experience.

Table of Contents

Key Features

Getting Started

Prerequisites

Installation

Usage

Configuration

Secret Key

In-App Settings

Management Script

Technology Stack

About the Developer

License

Key Features

📄 Document Indexing: Ingests and processes .pdf, .html, and .txt files.

🔍 Full-Text Search: Powered by SQLite FTS5 for fast and relevant content search.

🤖 Automatic Entity Extraction: Uses spaCy to automatically identify and link entities (People, Organizations, Locations, etc.).

🕸️ Relationship Discovery: Infers and displays relationships between entities based on co-occurrence.

✍️ Document Curation: Add custom tags, organize documents into "Catalogs," and leave comments or private notes.

👥 Multi-User Support: Full authentication system with admin/user roles and invitation-based registration.

⚡ Concurrent Processing: A background task manager processes documents without blocking the web UI.

🚀 Optional GPU Acceleration: Supports NVIDIA GPUs (CUDA) to accelerate NLP tasks.

Getting Started

Follow these steps to get the Redleaf Engine running on your local machine.

1. Prerequisites

Python 3.7 or newer

pip for installing Python packages

2. Installation

First, clone the repository to your local machine:

Generated bash
git clone <your-repository-url>
cd <repository-directory>


Next, it is highly recommended to create and activate a Python virtual environment:

On macOS / Linux:

Generated bash
python3 -m venv venv
source venv/bin/activate
IGNORE_WHEN_COPYING_START
content_copy
download
Use code with caution.
Bash
IGNORE_WHEN_COPYING_END

On Windows:

Generated bash
python -m venv venv
.\venv\Scripts\activate
IGNORE_WHEN_COPYING_START
content_copy
download
Use code with caution.
Bash
IGNORE_WHEN_COPYING_END

Install the required Python packages from requirements.txt:

Generated bash
pip install -r requirements.txt
IGNORE_WHEN_COPYING_START
content_copy
download
Use code with caution.
Bash
IGNORE_WHEN_COPYING_END

Finally, download the necessary spaCy NLP model:

Generated bash
python -m spacy download en_core_web_lg
IGNORE_WHEN_COPYING_START
content_copy
download
Use code with caution.
Bash
IGNORE_WHEN_COPYING_END
3. Usage

Add Documents: Create a documents folder in the project's root directory. Place all the files you want to index inside this folder. Subdirectories are also supported.

Run the Application: Start the Flask development server:

Generated bash
python app.py
IGNORE_WHEN_COPYING_START
content_copy
download
Use code with caution.
Bash
IGNORE_WHEN_COPYING_END

First-Time Setup: Open your web browser and go to http://127.0.0.1:5000. You will be guided through creating your primary admin account.

Log In and Explore:

Click "1. Discover Docs" to find and register your files.

Click "2. Process All 'New'" to start indexing.

Configuration
Secret Key

For security, you must set a private secret key. The application will warn you if you are using the insecure default key.

Set it as an environment variable before running the app:

On macOS / Linux:

Generated bash
export FLASK_SECRET_KEY='your-very-long-and-random-secret-key'
IGNORE_WHEN_COPYING_START
content_copy
download
Use code with caution.
Bash
IGNORE_WHEN_COPYING_END

On Windows (PowerShell):

Generated powershell
$env:FLASK_SECRET_KEY="your-very-long-and-random-secret-key"
IGNORE_WHEN_COPYING_START
content_copy
download
Use code with caution.
Powershell
IGNORE_WHEN_COPYING_END
In-App Settings

Settings like the number of worker processes, GPU usage, and parsing strategies can be configured directly from the Settings page within the app (admin access required).

Management Script

The manage.py script is included for command-line administrative tasks.

Example: Reset a user's password

Generated bash
python manage.py reset-password <username>
IGNORE_WHEN_COPYING_START
content_copy
download
Use code with caution.
Bash
IGNORE_WHEN_COPYING_END

You will be securely prompted to enter a new password.

Technology Stack

Backend: Python, Flask

Database: SQLite

NLP: spaCy

Document Parsing: PyMuPDF, BeautifulSoup4

Frontend: HTML5, CSS3, Vanilla JavaScript (ES6+)

CSRF Protection: Flask-WTF

About the Developer

This project was created by Nathaniel Westveer as a personal tool for knowledge exploration. It is open source and free to use, distribute, and modify.

License

This project is licensed under the MIT License. See the LICENSE file for details.

Copyright (c) 2025 Nathaniel Westveer

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
