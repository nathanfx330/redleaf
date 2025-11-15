import sqlite3
from project.config import DATABASE_FILE

def add_boosted_relationships_table():
    """Safely adds the new table to an existing database."""
    print(f"--- Connecting to your database at: {DATABASE_FILE} ---")
    
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()

        print("Checking for and creating the 'boosted_relationships' table...")

        # The "IF NOT EXISTS" makes this script safe to run multiple times.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS boosted_relationships (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                source_entity_id INTEGER NOT NULL,
                target_entity_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, source_entity_id, target_entity_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (source_entity_id) REFERENCES entities(id) ON DELETE CASCADE,
                FOREIGN KEY (target_entity_id) REFERENCES entities(id) ON DELETE CASCADE
            );
        """)
        
        conn.commit()
        conn.close()
        
        print("\n[SUCCESS] Your database schema is now up to date.")
        print("You do not need to run this script again.")

    except Exception as e:
        print(f"\n[ERROR] An error occurred: {e}")
        print("Please ensure your DATABASE_FILE path in project/config.py is correct.")

if __name__ == '__main__':
    add_boosted_relationships_table()