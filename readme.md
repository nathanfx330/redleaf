Redleaf Engine v2.0
<p align="center">
<br>
<strong>A local-first, full-stack application to index, search, and explore your personal document collection.</strong>
</p>

<p align="center">
<a href="https://www.python.org/downloads/release/python-370/"><img src="https://img.shields.io/badge/python-3.7+-blue.svg" alt="Python 3.7+"></a>
<a href="https://github.com/<your-username>/<your-repo>/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License: MIT"></a>
<!-- Optional: Add a build status badge -->
<!-- <a href="#"><img src="https://img.shields.io/github/actions/workflow/status/<your-username>/<your-repo>/main.yml" alt="Build Status"></a> -->
</p>


<!-- <p align="center">
<img src="path/to/your/screenshot.gif" alt="Redleaf Engine Demo">
</p> -->

✨ Key Features

📄 Document Indexing: Ingests .pdf, .html, and .txt files from a local directory.

🔍 Full-Text Search: Powered by SQLite FTS5 for fast, relevant content search.

🤖 Automatic Entity Extraction: Uses spaCy to automatically find and link People, Organizations, Locations, and more.

🕸️ Relationship Discovery: Infers and visualizes relationships between entities.

✍️ Curation Tools: Add custom tags, organize into "Catalogs," and leave comments or private notes.

👥 Multi-User Support: Full authentication with admin/user roles and an invitation-based system.

⚡ Concurrent Processing: A background task manager processes new documents without blocking the UI.

🚀 GPU Acceleration: Optionally leverages an NVIDIA GPU (with CUDA) to speed up NLP tasks.

🚀 Getting Started

Follow these steps to get the Redleaf Engine running on your local machine.

1. Prerequisites

Python 3.7 or newer

pip for installing Python packages

2. Installation

First, clone the repository:

Generated bash
git clone <your-repository-url>
cd <repository-directory>

<br>


[!NOTE]
It is highly recommended to use a Python virtual environment to avoid conflicts with other projects.

<details>
<summary><strong>Click to view commands for creating a virtual environment</strong></summary>


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
</details>

<br>


Install the required packages and the NLP model:

Generated bash
# Install Python dependencies
pip install -r requirements.txt

# Download the spaCy English model
python -m spacy download en_core_web_lg
IGNORE_WHEN_COPYING_START
content_copy
download
Use code with caution.
Bash
IGNORE_WHEN_COPYING_END
3. Usage

Add Your Documents: Create a documents folder in the project's root directory. Place all the files you want to index here.

Run the Application:

Generated bash
python app.py
IGNORE_WHEN_COPYING_START
content_copy
download
Use code with caution.
Bash
IGNORE_WHEN_COPYING_END

First-Time Setup: Open your browser to http://127.0.0.1:5000. You will be redirected to a setup page to create your admin account.

Log In and Index:

Click "1. Discover Docs" to register your files.

Click "2. Process All 'New'" to begin indexing.

⚙️ Configuration
Secret Key

[!WARNING]
For production use, you must set a secure, private FLASK_SECRET_KEY. The application will warn you if the default key is being used.

<details>
<summary><strong>Click to view commands for setting the Secret Key</strong></summary>


On macOS / Linux:

Generated bash
export FLASK_SECRET_KEY='your-very-long-and-random-secret-key'
IGNORE_WHEN_COPYING_START
content_copy
download
Use code with caution.
Bash
IGNORE_WHEN_COPYING_END

On Windows (Command Prompt):

Generated bash
set FLASK_SECRET_KEY="your-very-long-and-random-secret-key"
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
</details>

In-App Settings

Other settings (worker processes, GPU acceleration, etc.) can be configured from the Settings page within the application after logging in as an admin.

💻 Technology Stack
Category	Technology
Backend	Python, Flask
Database	SQLite (with FTS5 for search)
NLP	spaCy
Document Parsing	PyMuPDF (for PDFs), BeautifulSoup4
Frontend	HTML5, CSS3, Vanilla JavaScript (ES6+)
Security	Flask-WTF (CSRF Protection)
🛠️ Management Script

A command-line tool, manage.py, is included for admin tasks.

Example: Reset a user's password

Generated bash
python manage.py reset-password <username>
IGNORE_WHEN_COPYING_START
content_copy
download
Use code with caution.
Bash
IGNORE_WHEN_COPYING_END
👨‍💻 About the Developer

This project was created by Nathaniel Westveer as a personal tool for knowledge exploration. It is open source and free to use, distribute, and modify.

📜 License

This project is licensed under the MIT License.

<details>
<summary><strong>Click to read the full license text</strong></summary>


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

</details>
