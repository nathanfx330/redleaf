# Redleaf Engine

Redleaf is a full-stack web application designed to help you index, search, and explore a personal collection of documents. It turns a directory of PDFs, HTML, and text files into a searchable, interconnected knowledge graph that runs entirely on your local machine.

The application uses a robust Python Flask backend with a multi-process task manager to handle heavy NLP tasks, and a lightweight, responsive JavaScript frontend for a fast user experience.

## Key Features

- **Document Indexing**: Ingests and processes `.pdf`, `.html`, and `.txt` files from a local directory.
- **Full-Text Search**: Powered by SQLite FTS5 for fast and relevant content search across all documents.
- **Automatic Entity Extraction**: Uses spaCy to automatically identify and link entities like People, Organizations, Locations, and Dates.
- **Relationship Discovery**: Infers and displays relationships between entities based on their proximity within the text.
- **Document Curation**:
    - Add custom tags to documents.
    - Organize documents into user-defined "Catalogs".
    - Leave public comments or private notes on any document.
- **Multi-User Support**: Includes a full authentication system with admin/user roles and an invitation-based registration process.
- **Concurrent Processing**: A background task manager processes new documents without blocking the web interface.
- **Optional GPU Acceleration**: Can leverage a compatible NVIDIA GPU (with CUDA) to speed up NLP tasks.

## Getting Started

Follow these steps to set up and run the Redleaf Engine on your local machine.

### 1. Prerequisites

- Python 3.7 or newer
- `pip` for installing packages

### 2. Installation

First, clone the repository to your local machine:
```bash
git clone <your-repository-url>
cd <repository-directory>

    

IGNORE_WHEN_COPYING_START
Use code with caution. Markdown
IGNORE_WHEN_COPYING_END

Next, it is highly recommended to create and activate a Python virtual environment:
Generated bash

      
# On macOS / Linux
python3 -m venv venv
source venv/bin/activate

# On Windows
python -m venv venv
.\venv\Scripts\activate

    

IGNORE_WHEN_COPYING_START
Use code with caution. Bash
IGNORE_WHEN_COPYING_END

Install the required Python packages using the requirements.txt file:
Generated bash

      
pip install -r requirements.txt

    

IGNORE_WHEN_COPYING_START
Use code with caution. Bash
IGNORE_WHEN_COPYING_END

Finally, download the necessary spaCy NLP model:
Generated bash

      
python -m spacy download en_core_web_lg

    

IGNORE_WHEN_COPYING_START
Use code with caution. Bash
IGNORE_WHEN_COPYING_END
3. Usage

    Place Your Documents: Create a directory named documents in the root of the project folder. Place all the .pdf, .html, and .txt files you want to index inside this directory (you can also use subdirectories).

    Run the Application: Start the Flask development server with the following command:
    Generated bash

          
    python app.py

        

    IGNORE_WHEN_COPYING_START

    Use code with caution. Bash
    IGNORE_WHEN_COPYING_END

    First-Time Setup: Open your web browser and navigate to http://127.0.0.1:5000. You will be automatically redirected to a setup page to create your primary admin account.

    Log In and Explore: After creating your admin account, you will be taken to the login page. Log in with your new credentials to access the dashboard and begin using the application.

        Click "1. Discover Docs" to have Redleaf find and register the files in your documents directory.

        Click "2. Process All 'New'" to begin indexing the newly discovered documents.

Configuration
Secret Key

For production use, it is critical to set a secure, private secret key. The application will warn you if you are using the insecure default key.

Set the key as an environment variable before running the app:
Generated bash

      
# On macOS / Linux
export FLASK_SECRET_KEY='your-very-long-and-random-secret-key'

# On Windows (Command Prompt)
set FLASK_SECRET_KEY="your-very-long-and-random-secret-key"

# On Windows (PowerShell)
$env:FLASK_SECRET_KEY="your-very-long-and-random-secret-key"

    

IGNORE_WHEN_COPYING_START
Use code with caution. Bash
IGNORE_WHEN_COPYING_END
In-App Settings

Other settings, such as the number of worker processes, GPU acceleration, and HTML parsing strategy, can be configured directly from the Settings page within the application after logging in as an administrator.
Management Script

A command-line tool, manage.py, is included for administrative tasks. Currently, it supports resetting a user's password.

Example: Reset a password
Generated bash

      
python manage.py reset-password <username>

    

IGNORE_WHEN_COPYING_START
Use code with caution. Bash
IGNORE_WHEN_COPYING_END

You will be securely prompted to enter and confirm the new password.
Technology Stack

    Backend: Python, Flask

    Database: SQLite

    NLP: spaCy

    Document Parsing: PyMuPDF (for PDFs), BeautifulSoup4

    Frontend: HTML5, CSS3, vanilla JavaScript (ES6+)

    CSRF Protection: Flask-WTF

About the Developer

This project was created by Nathaniel Westveer as a personal tool for knowledge exploration. It is open source and free to use, distribute, and modify.