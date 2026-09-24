import sqlite3


DATABASE_PATH = "database/database.db"


connection = sqlite3.connect(DATABASE_PATH)

cursor = connection.cursor()


# Check whether owner_id already exists

columns = cursor.execute(
    "PRAGMA table_info(properties)"
).fetchall()


column_names = [
    column[1]
    for column in columns
]


if "owner_id" not in column_names:

    cursor.execute(
        """
        ALTER TABLE properties
        ADD COLUMN owner_id INTEGER
        """
    )

    connection.commit()

    print("owner_id column added successfully.")

else:

    print("owner_id column already exists.")


connection.close()