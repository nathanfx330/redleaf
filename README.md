# Redleaf: Search and Catalog documents with SpaCy!

Redleaf is a self-contained, personal document analysis tool built with Python and Flask. It's designed for individual use and portability, operating entirely within its own folder structure. Simply place your documents (PDF, HTML, TXT, SRT) in the designated folder, run the application locally, and use the web interface to process, index, search, browse, and organize them.

It uses SpaCy for Named Entity Recognition (NER) and stores all data in an embedded SQLite database, making the entire system easy to move and manage.

## Key Features

*   **Multi-Format Support:** Processes `.pdf`, `.html`, `.txt`, and `.srt` files.
*   **Text Extraction:** Extracts text from PDFs (using PyMuPDF) and HTML (using BeautifulSoup). Copies SRT/TXT content.
*   **Named Entity Recognition:** Uses SpaCy (`en_core_web_lg` model by default) to identify and index entities (Person, Org, Date, Location, GPE).
*   **Local Database:** Stores all indexed data and metadata in a local SQLite database (`redleaf_data.db`).
*   **Web Dashboard:** Monitor file processing status (New, Processing, Indexed, Error, etc.) and trigger processing tasks.
*   **Background Processing:** Uses `subprocess` and `concurrent.futures.ProcessPoolExecutor` for non-blocking text extraction and indexing.
*   **Entity Browsing:** Explore extracted entities grouped by type (Person, Org, etc.).
*   **Keyword Search:** Search the full text content of processed documents with highlighted snippets.
*   **Integrated Viewer:** View original PDF, HTML, TXT, and SRT files directly within the application using iframes.
*   **Organization Tools:**
    *   **Favorites:** Mark important documents.
    *   **Notes:** Add persistent notes to specific documents.
    *   **Catalogs:** Group related documents into custom "playlists".
*   **Portable:** Designed to run locally within its own folder. Move the folder, and the application and its data move with it.

## Technology Stack

*   Python 3.x
*   Flask
*   spaCy (specifically `en_core_web_lg` model)
*   PyMuPDF (Fitz)
*   BeautifulSoup4
*   SQLite

## Setup and Running

1.  **Clone the Repository:**
    ```bash
    git clone <your-repository-url>
    cd redleaf
    ```

2.  **Create a Virtual Environment (Recommended):**
    ```bash
    python -m venv venv
    # On Windows:
    .\venv\Scripts\activate
    # On macOS/Linux:
    source venv/bin/activate
    ```

3.  **Install Dependencies:**
    *   **Important:** Make sure you have a `requirements.txt` file listing the necessary packages (Flask, spacy, PyMuPDF, beautifulsoup4). If you don't have one, you can create it based on your imports or install manually:
        ```bash
        # If you have requirements.txt:
        pip install -r requirements.txt

        # Or install manually (adjust versions as needed):
        # pip install Flask spacy PyMuPDF beautifulsoup4
        ```

4.  **Download SpaCy Model:**
    ```bash
    python -m spacy download en_core_web_lg
    ```
    *(This model is relatively large, ensure you have sufficient disk space and internet connection)*

5.  **Run the Application:**
    ```bash
    python app.py
    ```
    The application will perform initial setup (creating directories and the database if they don't exist) and then start the Flask server.

6.  **Access:** Open your web browser and navigate to `http://127.0.0.1:5000` (or `http://localhost:5000`).

## Usage

1.  **Add Documents:** Place your `.pdf`, `.html`, `.txt`, and `.srt` files into the `./documents/` subfolder within the project directory. You can create subdirectories within `./documents/` as needed.
2.  **Refresh Dashboard:** Go to the Dashboard page (`/`) in the web interface. New files should appear with the status 'New' (or 'Text Extracted'/'Text Ready' if corresponding text files were found from previous runs).
3.  **Process Files:**
    *   Use the "Extract/Copy Text" buttons (single or bulk) for PDF/HTML/SRT files with 'New' status.
    *   Use the "Index Entities" buttons (single or bulk) for files with 'Text Extracted' or 'Text Ready' status.
    *   Monitor progress on the Dashboard (you may need to refresh or wait for polling).
4.  **Explore:**
    *   Use the "Browse & Search" tab to find entities or search keywords.
    *   Click on file paths to open them in the integrated viewer.
5.  **Organize:**
    *   Use the ⭐ (Favorite), 📝 (Note), and "Add to Catalog" features within the viewer's interaction bar.
    *   Manage catalogs and view documents with notes on the "Catalog & Notes" tab.

## Directory Structure

*   `./documents/`: **Place your source documents here.** Subdirectories are supported.
*   `./output_text_direct/`: Contains extracted text from PDFs and verbatim copies of SRTs (as `.txt` files). *Do not manually place files here.*
*   `./logs/`: Contains log files for background processing tasks.
*   `./redleaf_data.db`: The SQLite database containing all indexed data, statuses, notes, favorites, and catalogs.
*   `app.py`: The main Flask application script.
*   `processing_tasks.py`: Contains the core logic for text extraction and entity indexing.
*   `run_task.py`: Helper script for running single background tasks.
*   `./static/`: Contains CSS and potentially future JS files.
*   `./templates/`: Contains HTML templates for the web interface.

## Configuration

Key configuration options (like directories, spaCy model, entity types, worker limits) can be adjusted directly near the top of the `app.py` script.

## Portability

The application is designed to be self-contained. As long as the Python environment and dependencies are met, you can move the entire project folder (including `documents`, `output_text_direct`, `logs`, and `redleaf_data.db`) to another location or machine, and it should function identically with the data intact.

## License

*(Choose a license, e.g., MIT, Apache 2.0, and add it here. For example:)*
This project is licensed under the MIT License - see the LICENSE.md file for details.
