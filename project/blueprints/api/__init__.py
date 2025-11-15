# --- File: ./project/blueprints/api/__init__.py ---
from flask import Blueprint

# 1. Create the Blueprint instance. The name 'api' is kept so that all
#    url_for() calls (e.g., url_for('api.get_document_entities')) will continue
#    to work without changes in your templates or JavaScript.
api_bp = Blueprint('api', __name__)

# 2. Import the new, separated route modules.
#    This is crucial for registering the endpoints defined in those files
#    with the main 'api_bp' blueprint. These imports must come *after*
#    the Blueprint object is created to avoid circular dependencies.
from . import admin
from . import curation
from . import discovery
from . import documents
from . import media