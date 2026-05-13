# Redleaf Engine API Documentation

The Redleaf Engine exposes a robust REST-like API designed to power its web frontend, as well as external power-user clients like [Node Leaf](https://github.com/nathanfx330/node-leaf) and autonomous AI agents.

Because Redleaf is a **local-first** application, the API is served locally (default: `http://127.0.0.1:5000`) and relies on standard session cookies and CSRF tokens for security.

---

## 🔐 Authentication Flow

To programmatically interact with the Redleaf API, your client must establish a Flask session and capture the CSRF token.

1. **GET `/login`**: Fetch the page, extract the `session=` cookie, and parse the HTML to find the `<input name="csrf_token" value="...">`.
2. **POST `/login`**: Send a URL-encoded form with `username`, `password`, and the `csrf_token`. Include the `session=` cookie in the header.
3. **Save Cookies**: If successful, Flask will return a `302 Found` redirect and a new `session=` cookie.
4. **Subsequent API Calls**: Attach the following headers to all `POST`, `PUT`, and `DELETE` requests:
   - `Cookie: session=<your_session_token>`
   - `X-CSRFToken: <your_csrf_token>`
   - `Content-Type: application/json`

---

## 🗺️ System & Context 

### `GET /api/system/info`
Returns global identifying information about the database instance.
**Response:**
```json
{
  "project_name": "Redleaf",
  "instance_id": "uuid-string-here",
  "base_dir": "/absolute/path/to/redleaf"
}
```

### `GET /api/system/briefing`
Returns a high-level statistical summary (Total Docs, Top Tags, Top Entities) formatted for LLM context injection.
**Response:**
```json
{
  "briefing": "- Total Documents: 1540\n- Years covered: 1980 to 2012..."
}
```

---

## 🔍 Search & Retrieval

### `POST /api/search/advanced`
The primary endpoint for autonomous agents. Performs a highly optimized, hybrid Reciprocal Rank Fusion (RRF) search utilizing SQLite FTS5 and `sqlite-vec` cosine distance.

**Request Body:**
```json
{
  "q": "quarterly revenue forecast",
  "limit": 10,
  "threshold": 0.80,
  "file_types": ["PDF", "TXT"],
  "entities": [
    { "text": "Acme Corp", "label": "ORG", "mode": "doc" },
    { "text": "Globex", "label": "ORG", "mode": "exclude" },
    { "text": "Q3 Earnings Call", "label": "EVENT", "mode": "page" }
  ]
}
```
*Note on Modes: `doc` requires the entity anywhere in the document. `page` requires the entity on the exact same page. `exclude` strictly removes documents containing the entity.*

**Response:** List of snippets and metadata.
```json
[
  {
    "doc_id": 142,
    "relative_path": "financials/q3_report.pdf",
    "file_type": "PDF",
    "page_number": 12,
    "snippet": "...the quarterly revenue forecast issued by <mark>Acme Corp</mark>...",
    "metadata_str": "\"Q3 Financials\" by Jane Doe (2023)"
  }
]
```

### `GET /api/search`
A simpler global hybrid search.
**Parameters:**
- `q`: Search query string.
- `limit`: Number of results (default: 15).
- `mode`: `hybrid` or `fts` (default: `hybrid`).

### `GET /api/search/intersection`
Finds raw text pages where ALL requested topics co-occur.
**Parameters:**
- `topic`: Append multiple times (e.g., `?topic=Acme Corp&topic=Revenue`).
**Response:** 
```json
{ "context": "--- CONTEXT from Document #12... \nRaw page text here." }
```

---

## 🕸️ Knowledge Graph

### `GET /api/search/entities_autocomplete`
Fast autocomplete lookup for entities in the system.
**Parameters:**
- `q`: Partial entity name.
- `label`: (Optional) Filter by spaCy label (e.g., `PERSON`).

### `GET /api/entity/<entity_id>/relationships`
Returns structured graph triplets (Subject -> Phrase -> Object) connected to the given entity.
**Response:**
```json
[
  {
    "role": "subject",
    "relationship_phrase": "acquired",
    "other_entity_id": 84,
    "other_entity_text": "Global Logistics",
    "other_entity_label": "ORG",
    "count": 12
  }
]
```

### `GET /api/entity/<entity_id>/co-mentions`
Fetches paginated occurrences where the primary entity and a secondary entity appear on the exact same page.
**Parameters:**
- `page`: Page number (default: 1).
- `filter_entity_id`: The ID of the secondary entity.

---

## 📄 Documents & Curation

### `GET /api/document/<doc_id>/text`
Extracts the raw text content of a document.
**Parameters:**
- `start_page`: (Optional) 
- `end_page`: (Optional) 
**Response:**
```json
{
  "success": true,
  "text": "Raw extracted text goes here...",
  "metadata_str": "\"Book Title\" by Author (Year)"
}
```

### `GET /api/document/<doc_id>/entities`
Returns a grouped list of all unique entities extracted from a specific document.

### `GET /api/document/<doc_id>/curation`
Returns user-specific curation data for a document, including private notes, catalog memberships, and public comments.

### `POST /api/document/<doc_id>/comments`
Adds a public comment to the document.
**Request Body:** `{ "comment_text": "This is a notable finding." }`

### `POST /api/document/<doc_id>/tags`
Overwrites the tags for a document.
**Request Body:** `{ "tags": ["q3", "financials"] }`

---

## 🎙️ Media & Podcasts

### `GET /api/document/<doc_id>/media_status`
Checks if an SRT transcript has linked audio/video media.
**Response:**
```json
{
  "linked": true,
  "path": "/serve_doc/media/audio.mp3",
  "type": "audio",
  "source": "local",
  "position": 142.5,
  "offset": 0.0
}
```

### `POST /api/document/<doc_id>/find_audio`
Triggers a backend scan to find and link an `.mp3` file with a matching filename.
**Request Body:** 
```json
{ 
  "use_fuzzy": true, 
  "fuzzy_threshold": 0.85 
}
```

### `GET /api/podcasts/collections`
Returns a list of all auto-generated Podcast collections (derived from CSL-JSON metadata).

---

## ✍️ Synthesis Environment

The Synthesis environment uses a separate Blueprint prefix: `/api/synthesis`.

### `GET /api/synthesis/reports`
Lists all synthesis reports owned by the authenticated user.

### `POST /api/synthesis/reports`
Creates a new blank report.
**Request Body:** `{ "title": "My New Report" }`

### `GET /api/synthesis/<report_id>/content`
Retrieves the raw Tiptap JSON tree for the rich-text editor.

### `POST /api/synthesis/<report_id>/content`
Saves the Tiptap JSON tree.

### `POST /api/synthesis/<report_id>/citations`
Registers a new inline citation and returns a UUID and formatted Chicago-style string.
**Request Body:**
```json
{
  "source_doc_id": 45,
  "page_number": 12,
  "quoted_text": "The actual quote...",
  "prefix": "see also",
  "suffix": "emphasis added",
  "suppress_author": false
}
```
**Response:**
```json
{
  "success": true,
  "citation_instance_uuid": "...",
  "in_text_label": "(see also Smith, 1999, p. 12, emphasis added)"
}