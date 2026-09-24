import sqlite3


connection = sqlite3.connect("database/database.db")

connection.row_factory = sqlite3.Row

cursor = connection.cursor()


requests = cursor.execute("""
    SELECT
        rental_requests.id,
        properties.title,
        properties.location,
        rental_requests.tenant_id,
        rental_requests.status,
        rental_requests.request_date
    FROM rental_requests
    JOIN properties
        ON rental_requests.property_id = properties.id
    ORDER BY rental_requests.id DESC
""").fetchall()


print("\n========================================")
print("       HAVENLY RENTAL REQUESTS")
print("========================================\n")


if not requests:

    print("No rental requests found.")

else:

    for rental_request in requests:

        print("----------------------------------------")

        print(
            f"Request ID   : {rental_request['id']}"
        )

        print(
            f"Property     : {rental_request['title']}"
        )

        print(
            f"Location     : {rental_request['location']}"
        )

        print(
            f"Tenant ID    : {rental_request['tenant_id']}"
        )

        print(
            f"Status       : {rental_request['status']}"
        )

        print(
            f"Request Date : {rental_request['request_date']}"
        )

    print("----------------------------------------")


connection.close()