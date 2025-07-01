# --- File: ./manage.py ---
import argparse
import sqlite3
from pathlib import Path
from getpass import getpass
from werkzeug.security import generate_password_hash

# --- Configuration (should match app.py) ---
DATABASE_FILE = "knowledge_base.db"

def get_db_conn():
    """Gets a direct database connection."""
    db_path = Path(DATABASE_FILE)
    if not db_path.exists():
        print(f"Error: Database file not found at '{db_path.resolve()}'")
        print("Please ensure you are running this script from the correct directory.")
        return None
        
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def reset_user_password(username, password):
    """Finds a user by username and resets their password."""
    conn = get_db_conn()
    if not conn:
        return

    try:
        cursor = conn.cursor()
        
        # Check if the user exists
        user = cursor.execute("SELECT id, username FROM users WHERE username = ?", (username,)).fetchone()
        if not user:
            print(f"Error: User '{username}' not found.")
            return

        # Hash the new password and update the database
        hashed_password = generate_password_hash(password)
        cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hashed_password, user['id']))
        conn.commit()
        
        print(f"Success! The password for user '{username}' has been reset.")

    except sqlite3.Error as e:
        print(f"A database error occurred: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Redleaf Engine Admin Management Tool.")
    
    # Create subparsers for different commands
    subparsers = parser.add_subparsers(dest='command', required=True, help='Available commands')

    # Create the parser for the "reset-password" command
    parser_reset = subparsers.add_parser('reset-password', help="Reset a user's password.")
    parser_reset.add_argument('username', type=str, help="The username of the account to reset.")
    
    args = parser.parse_args()

    if args.command == 'reset-password':
        print(f"Attempting to reset password for user: {args.username}")
        # Use getpass to securely prompt for the password without showing it on screen
        new_password = getpass("Enter new password: ")
        if not new_password or len(new_password) < 8:
            print("Password cannot be empty and must be at least 8 characters long. Aborting.")
        else:
            new_password_confirm = getpass("Confirm new password: ")
            if new_password == new_password_confirm:
                reset_user_password(args.username, new_password)
            else:
                print("Passwords do not match. Aborting.")