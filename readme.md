# Redleaf Engine

Redleaf is a self-hosted, personal document analysis tool built with Python and Flask. It's designed to run entirely on your local machine, allowing you to build a private, searchable knowledge base for yourself or a small team on a local network.

Redleaf goes beyond simple search by **automatically discovering and mapping relationships** between the people, places, and organizations within your texts, helping you uncover connections you never knew existed.

## Key Features

*   **Relationship Discovery Engine:** This is the core of Redleaf. It automatically identifies and visualizes connections between entities. Uncover hidden patterns like `[Person] founded [Company]` or `[Company] acquired [Startup]` that repeat across your entire document collection. You can then drill down to see the exact context for each occurrence.
*   **LAN-Ready & Multi-User:** Runs on your local network, allowing multiple users to access, search, and contribute to the same knowledge base from their own computers.
*   **Secure Authentication:** Features a full user authentication system with admin roles and **secure, invitation-only registration**.
*   **Automated NLP Pipeline:** Uses spaCy to automatically extract named entities (people, places, organizations) that power the relationship discovery.
*   **Robust Background Processing:** A sophisticated manager handles long-running, CPU-intensive NLP tasks in parallel processes without blocking the web interface.
*   **GPU Acceleration:** Optional support for CUDA-enabled NVIDIA GPUs to dramatically speed up processing.
*   **Rich Curation & Collaboration Tools:**
    *   **Catalogs:** Group related documents into custom collections.
    *   **Tags:** Apply multiple tags to documents for fine-grained organization.
    *   **Notes & Comments:** Add private notes (user-specific) or public comments to any document.
*   **Full-Text Search:** Powerful search across all ingested documents, powered by SQLite FTS5 with highlighted snippets.

## Technology Stack

*   **Backend:** Python 3, Flask, spaCy
*   **Database:** SQLite
*   **Document Parsers:** PyMuPDF (Fitz), BeautifulSoup4
*   **Frontend:** Vanilla JavaScript (ES6+), HTML5, CSS3

## Setup & Installation

Using **Conda** is strongly recommended to manage the environment, as it simplifies the installation of complex scientific and data processing packages.

### Option 1: Using Conda (Recommended)

This is the easiest and most reliable way to get started.

**1. Install Miniconda**
If you don't have Conda, install [Miniconda](https://docs.conda.io/en/latest/miniconda.html) for your operating system. It's a minimal installer for the Conda package manager.

**2. Clone the Repository**
```bash
git clone https://github.com/nathanfx330/redleaf.git
cd redleaf
```

**3. Create the Conda Environment**
This single command uses the `environment.yml` file to create a new, isolated environment named `redleaf-env` with all the necessary packages.
```bash
conda env create -f environment.yml
```

**4. Activate the New Environment**
You must activate the environment every time you want to run the application.
```bash
conda activate redleaf-env
```

**5. Download the NLP Model**
Redleaf requires a spaCy language model for its processing pipeline.
```bash
python -m spacy download en_core_web_lg
```

Proceed to the **"Running the Application"** step below.

### Option 2: Using Pip and Venv

If you prefer not to use Conda, you can use a standard Python virtual environment.

**1. Clone the Repository**
```bash
git clone https://github.com/nathanfx330/redleaf.git
cd redleaf
```

**2. Create and Activate a Virtual Environment**
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

Proceed to the **"Running the Application"** step below.

---

## Running the Application

### 1. Start the Server

Make sure your environment (`redleaf-env` or `venv`) is activated and you are in the project's root directory. Then run:
```bash
python app.py
```
The server is now running and listening for connections.

### 2. Access the Web UI

The application is now accessible to anyone on your local network.

*   **For Yourself (on the same machine):**
    Open your web browser and go to **`http://localhost:5000`**

*   **For Other Users on Your Network:**
    You need to find the local IP address of the computer running the server.
    *   On **Windows**, open Command Prompt and type `ipconfig`. Look for the "IPv4 Address".
    *   On **macOS** or **Linux**, open a terminal and type `ip a` or `ifconfig`. Look for the "inet" address.

    It will look something like `192.168.1.15` or `10.0.0.52`. Other users can then access Redleaf by navigating to that IP address followed by the port number in their browser.
    
    Example: **`http://192.168.1.15:5000`**

> **Important Security Note:** Running the server on `0.0.0.0` makes it accessible to any device on your network. Only do this on trusted networks (like your home or a private office). Your operating system's firewall may ask for permission to allow incoming connections; you must allow it for others to connect.

## (Optional) GPU Acceleration Setup

If you have a compatible NVIDIA GPU and have installed the CUDA drivers, you can install CuPy to enable GPU acceleration.

Activate your environment (either Conda or venv) and run the appropriate `pip` command. You can find the correct command for your CUDA version on the [CuPy installation guide](https://cupy.dev/install).

For example, for CUDA 11.x:
```bash
pip install cupy-cuda11x
```
For CUDA 12.x:
```bash
pip install cupy-cuda12x
```
Once installed, go to the Redleaf settings page in the UI and toggle on "Enable GPU Acceleration".

## Getting Started: First-Time Workflow

### A. Initial Server Setup

1.  **Create Admin Account:** The very first time you visit the application, you will be directed to a setup page to create the primary administrator account.

2.  **Add Other Users (Optional):** To add more users, the administrator **must** go to the **Settings** page and generate an invitation token. This unique token can then be given to a new user, who will use it on the registration page to create their account. **New users cannot register without a valid token.**

### B. Document Processing

3.  **Discover Documents:** Copy your documents into the project's `documents` folder, then go to the Dashboard and click **"1. Discover Docs"** to have Redleaf find and register the files.

4.  **Process Documents:** Click **"2. Process All 'New'"** to begin indexing and NLP extraction. You can monitor the progress on the dashboard.

5.  **Update Cache:** Once processing is complete, click **"3. Update Browse Cache"** to aggregate the new data for the Discovery page.

6.  **Explore!** Navigate to the **Discovery** tab to search for keywords or browse entities. **Be sure to check the "Explore Relationships" tab to see the most common connections automatically found in your collection.**

## Directory Structure

*   `./documents/`: **Place your source documents here.** Subdirectories are supported.
*   `./instance/`: **Holds non-public files**, like the database and secret key. This folder is critical and should *not* be committed to version control.
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
