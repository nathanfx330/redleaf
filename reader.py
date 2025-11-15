"reader.py"

import os
import html
from flask import Flask, render_template, abort, request, current_app

app = Flask(__name__)

TARGET_DIRECTORY = os.path.dirname(os.path.abspath(__file__))
VIEWABLE_EXTENSIONS = {'.as','.py', '.html', '.css', '.js', ".csl", ".yml",".md",".txt"}
SCRIPT_PATH = os.path.abspath(__file__)

def build_directory_tree(paths):
    """Builds a nested dictionary tree from a list of directory paths."""
    tree = {}
    for path in paths:
        parts = path.split('/')
        node = tree
        for part in parts:
            node = node.setdefault(part, {})
    return tree

def get_all_viewable_files(base_dir):
    viewable_files = []
    viewable_directories = set()

    if not os.path.isdir(base_dir):
        current_app.logger.error(f"Target directory not found: {base_dir}")
        return [], set()

    abs_base_dir = os.path.abspath(base_dir)

    for root, dirs, files in os.walk(base_dir, topdown=True):
        dirs[:] = [d for d in dirs if d not in ['.venv', '__pycache__', '.git', 'node_modules']]
        abs_root = os.path.abspath(root)
        if not abs_root.startswith(abs_base_dir):
            continue

        for filename in files:
            _, ext = os.path.splitext(filename)
            if ext.lower() in VIEWABLE_EXTENSIONS:
                full_path = os.path.join(root, filename)
                abs_full_path = os.path.abspath(full_path)
                if abs_full_path == SCRIPT_PATH:
                    continue

                relative_path = os.path.relpath(full_path, base_dir)
                display_path = os.path.join('.', relative_path).replace(os.sep, '/')

                directory_name = os.path.dirname(relative_path).replace(os.sep, '/')
                if directory_name == '.':
                    directory_name = ''
                
                # Add all parent directories of the current file's directory
                if directory_name:
                    parts = directory_name.split('/')
                    for i in range(1, len(parts) + 1):
                        viewable_directories.add('/'.join(parts[:i]))


                safe_item_id = relative_path.replace('/', '__').replace('\\', '__').replace('.', '_')

                file_info = {
                    'name': filename,
                    'path': display_path,
                    'id': safe_item_id,
                    'type': 'other',
                    'content': None,
                    'error': False,
                    'directory': directory_name
                }

                if ext == '.py': file_info['type'] = 'python'
                elif ext == '.html': file_info['type'] = 'html'
                elif ext == '.css': file_info['type'] = 'css'

                try:
                    try:
                        with open(full_path, 'r', encoding='utf-8') as f: file_info['content'] = f.read()
                    except UnicodeDecodeError:
                        with open(full_path, 'r', encoding='latin-1') as f: file_info['content'] = f.read()
                        current_app.logger.warning(f"File {display_path} read with latin-1.")
                except Exception as e:
                    current_app.logger.error(f"Error reading {display_path}: {e}")
                    file_info['content'] = f"Error: Could not read file.\n{e}"
                    file_info['error'] = True

                viewable_files.append(file_info)

    viewable_files.sort(key=lambda x: x['path'])
    return viewable_files, sorted(list(viewable_directories))

@app.route('/')
def show_all_files():
    all_files, all_directories = get_all_viewable_files(TARGET_DIRECTORY)
    directory_tree = build_directory_tree(all_directories)
    return render_template(
        'reader.html',
        base_directory=TARGET_DIRECTORY,
        files=all_files,
        directory_tree=directory_tree
    )

# ... (Error handlers) ...

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
