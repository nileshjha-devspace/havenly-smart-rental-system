import sqlite3

connection = sqlite3.connect("database/database.db")

cursor = connection.cursor()


# --------------------------------------------------
# USERS TABLE
# --------------------------------------------------

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'tenant'
)
""")


# --------------------------------------------------
# PROPERTIES TABLE
# --------------------------------------------------

cursor.execute("""
CREATE TABLE IF NOT EXISTS properties (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    property_type TEXT NOT NULL,
    location TEXT NOT NULL,
    rent INTEGER NOT NULL,
    description TEXT,
    image_url TEXT,
    available INTEGER DEFAULT 1
)
""")


# --------------------------------------------------
# PROPERTY IMAGES TABLE
# One property can have multiple images
# --------------------------------------------------

cursor.execute("""
CREATE TABLE IF NOT EXISTS property_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id INTEGER NOT NULL,
    image_url TEXT NOT NULL,

    FOREIGN KEY (property_id)
        REFERENCES properties(id)
)
""")


# --------------------------------------------------
# RENTAL REQUESTS TABLE
# --------------------------------------------------

cursor.execute("""
CREATE TABLE IF NOT EXISTS rental_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id INTEGER NOT NULL,
    tenant_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'Pending',
    request_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (property_id)
        REFERENCES properties(id),

    FOREIGN KEY (tenant_id)
        REFERENCES users(id)
)
""")


connection.commit()
connection.close()

print("Database tables created successfully!")