# --- File: ./project/config.py ---
import os
import sys
import secrets
from pathlib import Path

# --- Base Directory ---
# This makes the paths in your project robust and independent of where you run the script from.
BASE_DIR = Path(__file__).resolve().parent.parent

# --- Key Directories ---
# Using the standard Flask convention for the instance folder.
INSTANCE_DIR = BASE_DIR / "instance"
INSTANCE_DIR.mkdir(exist_ok=True)

# Location for user-provided documents.
DOCUMENTS_DIR = BASE_DIR / "documents"
DOCUMENTS_DIR.mkdir(exist_ok=True)

# --- Database Configuration ---
DATABASE_FILE = BASE_DIR / "knowledge_base.db"

# --- Application-Specific Settings ---
# Labels to be displayed in the "Discovery" view.
ENTITY_LABELS_TO_DISPLAY = ['PERSON', 'GPE', 'LOC', 'ORG', 'DATE']

# --- Security: Secret Key Handling ---
SECRET_KEY_FILE = INSTANCE_DIR / "secret.key"

def get_or_create_secret_key():
    """
    Looks for a secret key file. If it exists, reads the key.
    If not, it creates a new secure, random key and saves it.
    This ensures each Redleaf installation is automatically secure from the start.
    """
    if SECRET_KEY_FILE.exists():
        # Key already exists, load it.
        return SECRET_KEY_FILE.read_text().strip()
    else:
        # First-time setup: Generate and save a new key.
        print("\n!!! First time setup: Generating a new, unique secret key. !!!")
        print(f"!!! This will be stored at: {SECRET_KEY_FILE} !!!\n")
        new_key = secrets.token_hex(24)
        try:
            # Save the key to the file.
            SECRET_KEY_FILE.write_text(new_key)
            # On POSIX systems (Linux/macOS), set restrictive permissions (read/write for owner only).
            if os.name == 'posix':
                os.chmod(SECRET_KEY_FILE, 0o600)
        except Exception as e:
            # This is a critical failure, as the app cannot run securely without the key.
            print(f"FATAL: Could not write secret key to file! Error: {e}")
            sys.exit("Application cannot start without a secret key.")
        return new_key

# Generate or load the secret key to be used by the Flask app.
SECRET_KEY = get_or_create_secret_key()