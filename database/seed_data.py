import sqlite3

connection = sqlite3.connect("database/database.db")
cursor = connection.cursor()

properties = [
    (
        "Cozy Student PG",
        "PG",
        "Thane",
        9500,
        "Comfortable PG suitable for students with basic amenities and easy transport access.",
        "https://images.unsplash.com/photo-1555854877-bab0e564b8d5?auto=format&fit=crop&w=900&q=80",
        1
    ),
    (
        "Modern City Apartment",
        "Apartment",
        "Andheri, Mumbai",
        22000,
        "Modern apartment located close to offices, restaurants and public transport.",
        "https://images.unsplash.com/photo-1522708323590-d24dbb6b0267?auto=format&fit=crop&w=900&q=80",
        1
    ),
    (
        "Premium Private Room",
        "Room",
        "Powai, Mumbai",
        16000,
        "Fully furnished private room in a well-connected residential area.",
        "https://images.unsplash.com/photo-1505693416388-ac5ce068fe85?auto=format&fit=crop&w=900&q=80",
        1
    ),
    (
        "Affordable Girls PG",
        "PG",
        "Vashi, Navi Mumbai",
        8500,
        "Affordable PG with furnished rooms and essential facilities.",
        "https://images.unsplash.com/photo-1560185008-b033106af5c3?auto=format&fit=crop&w=900&q=80",
        1
    ),
    (
        "Luxury 1BHK Apartment",
        "Apartment",
        "Bandra, Mumbai",
        30000,
        "Spacious 1BHK apartment with modern interiors and premium facilities.",
        "https://images.unsplash.com/photo-1493809842364-78817add7ffb?auto=format&fit=crop&w=900&q=80",
        1
    ),
    (
        "Budget Friendly Room",
        "Room",
        "Borivali, Mumbai",
        12000,
        "Clean and affordable private room with convenient access to local transport.",
        "https://images.unsplash.com/photo-1560448204-e02f11c3d0e2?auto=format&fit=crop&w=900&q=80",
        1
    )
]

cursor.executemany("""
    INSERT INTO properties
    (title, property_type, location, rent, description, image_url, available)
    VALUES (?, ?, ?, ?, ?, ?, ?)
""", properties)

connection.commit()
connection.close()

print("Sample properties added successfully!")