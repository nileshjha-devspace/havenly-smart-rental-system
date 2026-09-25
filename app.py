from flask import Flask, render_template, request, redirect, url_for, session
import os
import uuid
import re
import mimetypes
from urllib.parse import urlparse

from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from supabase import create_client
from database.db import get_db_connection


app = Flask(__name__)
app.secret_key = "havenly-development-secret-key"

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_BUCKET = "property-images"

supabase = None

if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    supabase = create_client(
        SUPABASE_URL,
        SUPABASE_SERVICE_ROLE_KEY
    )




# =========================================================
# IMAGE UPLOAD CONFIGURATION
# =========================================================

UPLOAD_FOLDER = "static/uploads"

ALLOWED_IMAGE_EXTENSIONS = {
    "png",
    "jpg",
    "jpeg",
    "webp"
}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER


def allowed_image(filename):
    if "." not in filename:
        return False

    extension = filename.rsplit(".", 1)[1].lower()

    return extension in ALLOWED_IMAGE_EXTENSIONS


def upload_image_to_supabase(image, property_id):
    """
    Upload one Flask FileStorage image to Supabase Storage
    and return its public URL.
    """
    if supabase is None:
        raise RuntimeError(
            "Supabase Storage is not configured. "
            "Please check SUPABASE_URL and "
            "SUPABASE_SERVICE_ROLE_KEY in .env."
        )

    extension = image.filename.rsplit(".", 1)[1].lower()
    unique_filename = f"{uuid.uuid4().hex}.{extension}"
    storage_path = f"properties/{property_id}/{unique_filename}"

    content_type = (
        mimetypes.guess_type(image.filename)[0]
        or "application/octet-stream"
    )

    image_data = image.read()

    supabase.storage.from_(SUPABASE_BUCKET).upload(
        storage_path,
        image_data,
        {
            "content-type": content_type,
            "upsert": "true"
        }
    )

    public_url = (
        supabase.storage
        .from_(SUPABASE_BUCKET)
        .get_public_url(storage_path)
    )

    return public_url


def delete_supabase_image(image_url):
    """
    Delete a Havenly image from Supabase Storage.
    External URLs are ignored safely.
    """
    if not image_url or supabase is None:
        return

    marker = (
        f"/storage/v1/object/public/"
        f"{SUPABASE_BUCKET}/"
    )

    if marker not in image_url:
        return

    storage_path = image_url.split(marker, 1)[1]

    if not storage_path:
        return

    try:
        supabase.storage.from_(SUPABASE_BUCKET).remove(
            [storage_path]
        )
    except Exception:
        # Image deletion should never break property deletion.
        pass


os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# =========================================================
# APPROVED OWNER SECURITY CHECK
# =========================================================

def require_approved_owner():
    """
    Allow owner-only pages/actions only when the logged-in
    account exists, has the owner role, and is approved by admin.
    """

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("user_role") != "owner":
        return redirect(url_for("login"))

    connection = get_db_connection()

    owner = connection.execute(
        """
        SELECT id, status
        FROM users
        WHERE id = ?
        AND role = 'owner'
        """,
        (session["user_id"],)
    ).fetchone()

    connection.close()

    if owner is None:
        session.clear()
        return redirect(url_for("login"))

    if owner["status"] != "approved":
        session.clear()
        return redirect(
            url_for(
                "login",
                owner_status=owner["status"]
            )
        )

    return None


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():
    return render_template("index.html")


# =========================================================
# PROPERTIES
# =========================================================

@app.route("/properties")
def properties():

    location = request.args.get("location", "").strip()
    property_type = request.args.get("type", "").strip()
    budget = request.args.get("budget", "").strip()

    connection = get_db_connection()

    query = """
        SELECT *
        FROM properties
        WHERE available = 1
    """

    parameters = []

    if location:
        query += """
            AND location LIKE ?
        """
        parameters.append(f"%{location}%")

    if property_type and property_type != "All Types":
        query += """
            AND property_type = ?
        """
        parameters.append(property_type)

    if budget:
        try:
            query += """
                AND rent <= ?
            """
            parameters.append(int(budget))
        except ValueError:
            pass

    query += """
        ORDER BY rent ASC
    """

    properties = connection.execute(
        query,
        parameters
    ).fetchall()

    connection.close()

    return render_template(
        "properties.html",
        properties=properties
    )


# =========================================================
# PROPERTY DETAILS
# =========================================================

@app.route("/property/<int:property_id>")
def property_details(property_id):

    connection = get_db_connection()

    property_data = connection.execute(
        """
        SELECT *
        FROM properties
        WHERE id = ?
        """,
        (property_id,)
    ).fetchone()

    images = connection.execute(
        """
        SELECT image_url
        FROM property_images
        WHERE property_id = ?
        ORDER BY id
        """,
        (property_id,)
    ).fetchall()

    connection.close()

    if property_data is None:
        return "Property not found", 404

    requested = request.args.get("requested")

    return render_template(
        "property_details.html",
        property=property_data,
        images=images,
        requested=requested
    )


# =========================================================
# REQUEST TO RENT
# =========================================================

@app.route(
    "/request-rent/<int:property_id>",
    methods=["POST"]
)
def request_rent(property_id):

    if "user_id" not in session:
        return redirect(
            url_for(
                "login",
                next=url_for(
                    "property_details",
                    property_id=property_id
                )
            )
        )

    if session.get("user_role") == "owner":
        return redirect(url_for("owner_dashboard"))

    tenant_id = session["user_id"]

    connection = get_db_connection()

    property_data = connection.execute(
        """
        SELECT *
        FROM properties
        WHERE id = ?
        """,
        (property_id,)
    ).fetchone()

    if property_data is None:
        connection.close()
        return "Property not found", 404

    if property_data["available"] != 1:

        connection.close()

        return redirect(
            url_for(
                "property_details",
                property_id=property_id,
                requested="unavailable"
            )
        )

    existing_request = connection.execute(
        """
        SELECT id, status
        FROM rental_requests
        WHERE property_id = ?
        AND tenant_id = ?
        AND status IN ('Pending', 'Approved')
        """,
        (
            property_id,
            tenant_id
        )
    ).fetchone()

    if existing_request:

        connection.close()

        if existing_request["status"] == "Approved":

            return redirect(
                url_for(
                    "property_details",
                    property_id=property_id,
                    requested="already_approved"
                )
            )

        return redirect(
            url_for(
                "property_details",
                property_id=property_id,
                requested="already_requested"
            )
        )

    connection.execute(
        """
        INSERT INTO rental_requests
        (
            property_id,
            tenant_id,
            status
        )
        VALUES (?, ?, ?)
        """,
        (
            property_id,
            tenant_id,
            "Pending"
        )
    )

    connection.commit()
    connection.close()

    return redirect(
        url_for(
            "property_details",
            property_id=property_id,
            requested="success"
        )
    )


# =========================================================
# SMART MATCH / RECOMMENDATIONS
# =========================================================

@app.route("/recommendations")
def recommendations():

    location = request.args.get(
        "location",
        ""
    ).strip()

    budget_text = request.args.get(
        "budget",
        ""
    ).strip()

    property_type = request.args.get(
        "type",
        "All Types"
    ).strip()

    budget = None

    if budget_text:
        try:
            budget = int(budget_text)
        except ValueError:
            budget = None

    connection = get_db_connection()

    properties = connection.execute(
        """
        SELECT *
        FROM properties
        WHERE available = 1
        """
    ).fetchall()

    connection.close()

    recommendations_list = []

    for property_data in properties:

        score = 0
        reasons = []

        # LOCATION
        if location:

            property_location = (
                property_data["location"] or ""
            ).lower()

            preferred_location = location.lower()

            if preferred_location in property_location:

                score += 40

                reasons.append(
                    "Location matches"
                )

            else:

                location_words = [
                    word.strip()
                    for word in preferred_location.split()
                    if word.strip()
                ]

                partial_match = False

                for word in location_words:

                    if (
                        len(word) >= 3
                        and word in property_location
                    ):
                        partial_match = True
                        break

                if partial_match:

                    score += 20

                    reasons.append(
                        "Nearby location"
                    )

        else:
            score += 40

        # BUDGET
        rent = property_data["rent"]

        if budget:

            if rent <= budget:

                score += 35

                reasons.append(
                    "Within your budget"
                )

            else:

                difference = rent - budget

                if difference <= budget * 0.10:

                    score += 20

                    reasons.append(
                        "Slightly above budget"
                    )

                elif difference <= budget * 0.20:

                    score += 10

                    reasons.append(
                        "Close to your budget"
                    )

        else:
            score += 35

        # PROPERTY TYPE
        if (
            property_type
            and property_type != "All Types"
        ):

            if (
                property_data["property_type"]
                == property_type
            ):

                score += 25

                reasons.append(
                    "Property type matches"
                )

        else:
            score += 25

        property_item = dict(property_data)

        property_item["match_score"] = score
        property_item["reasons"] = reasons

        recommendations_list.append(
            property_item
        )

    recommendations_list.sort(
        key=lambda item: item["match_score"],
        reverse=True
    )

    return render_template(
        "recommendations.html",
        recommendations=recommendations_list,
        selected_location=location,
        selected_budget=budget_text,
        selected_type=property_type
    )


# =========================================================
# AI PROPERTY ASSISTANT
# =========================================================

@app.route("/ai-assistant")
def ai_assistant():

    user_query = request.args.get(
        "query",
        ""
    ).strip()

    response = ""

    if user_query:

        query_lower = user_query.lower()

        # -------------------------------------------------
        # LOAD AVAILABLE PROPERTIES
        # -------------------------------------------------

        connection = get_db_connection()

        available_properties = connection.execute(
            """
            SELECT *
            FROM properties
            WHERE available = 1
            """
        ).fetchall()

        connection.close()

        # -------------------------------------------------
        # EXTRACT BUDGET
        # -------------------------------------------------

        budget = None

        budget_patterns = [

            # under 10000
            r"(?:under|below|within|upto|up to|less than|max|maximum)\s*[₹rs\.]*\s*([0-9,]+)",

            # ₹10000 / Rs 10000
            r"[₹rs\.]+\s*([0-9,]+)",

            # 10000 rupees / 10000 rs
            r"([0-9,]+)\s*(?:rupees|rs)\b",

            # 10k
            r"([0-9]+(?:\.[0-9]+)?)\s*k\b",

            # 10 thousand
            r"([0-9]+(?:\.[0-9]+)?)\s*(?:thousand|k)\b"
        ]

        for pattern in budget_patterns:

            match = re.search(
                pattern,
                user_query,
                re.IGNORECASE
            )

            if match:

                try:

                    raw_value = match.group(1).replace(
                        ",",
                        ""
                    )

                    numeric_value = float(raw_value)

                    matched_text = match.group(0).lower()

                    if (
                        "k" in matched_text
                        or "thousand" in matched_text
                    ):
                        numeric_value *= 1000

                    budget = int(numeric_value)

                    break

                except ValueError:
                    pass

        # -------------------------------------------------
        # EXTRACT PROPERTY TYPE
        # -------------------------------------------------

        property_type = None

        pg_keywords = [
            "pg",
            "p.g",
            "paying guest",
            "paying-guest",
            "hostel",
            "hostels"
        ]

        apartment_keywords = [
            "apartment",
            "apartments",
            "flat",
            "flats",
            "1bhk",
            "2bhk",
            "3bhk",
            "4bhk",
            "bhk"
        ]

        room_keywords = [
            "room",
            "rooms",
            "private room",
            "private rooms"
        ]

        if any(
            keyword in query_lower
            for keyword in pg_keywords
        ):

            property_type = "PG"

        elif any(
            keyword in query_lower
            for keyword in apartment_keywords
        ):

            property_type = "Apartment"

        elif any(
            keyword in query_lower
            for keyword in room_keywords
        ):

            property_type = "Room"

        # -------------------------------------------------
        # EXTRACT LOCATION
        # -------------------------------------------------

        location = None

        # Known locations from database
        for property_data in available_properties:

            property_location = (
                property_data["location"] or ""
            )

            location_parts = [
                part.strip()
                for part in property_location.split(",")
            ]

            for location_part in location_parts:

                location_part_lower = (
                    location_part.lower()
                )

                if (
                    len(location_part_lower) >= 3
                    and location_part_lower in query_lower
                ):

                    location = location_part

                    break

            if location:
                break

        # -------------------------------------------------
        # LOCATION ALIASES
        # -------------------------------------------------

        location_aliases = {

            "mumbai": [
                "mumbai",
                "bombay"
            ],

            "thane": [
                "thane"
            ],

            "andheri": [
                "andheri"
            ],

            "powai": [
                "powai"
            ],

            "borivali": [
                "borivali",
                "borivli"
            ],

            "vashi": [
                "vashi"
            ],

            "navi mumbai": [
                "navi mumbai",
                "new mumbai"
            ],

            "bandra": [
                "bandra"
            ]
        }

        if not location:

            for standard_location, aliases in location_aliases.items():

                for alias in aliases:

                    if re.search(
                        rf"\b{re.escape(alias)}\b",
                        query_lower
                    ):

                        location = standard_location.title()

                        break

                if location:
                    break

        # -------------------------------------------------
        # UNDERSTAND NATURAL LANGUAGE
        # -------------------------------------------------

        affordability_keywords = [
            "cheap",
            "cheapest",
            "affordable",
            "budget",
            "low budget",
            "inexpensive",
            "economical"
        ]

        if any(
            keyword in query_lower
            for keyword in affordability_keywords
        ) and budget is None:

            # If the user says "cheap" without a number,
            # rank by the lowest rent instead of inventing
            # a budget limit.
            budget_mode = "affordable"

        else:
            budget_mode = None

        # -------------------------------------------------
        # HANDLE GENERAL PROPERTY REQUESTS
        # -------------------------------------------------

        wants_property = any(
            keyword in query_lower
            for keyword in [
                "find",
                "show",
                "search",
                "looking",
                "need",
                "want",
                "place",
                "property",
                "stay",
                "rent",
                "available"
            ]
        )

        # -------------------------------------------------
        # FILTER PROPERTIES
        # -------------------------------------------------

        matching_properties = []

        for property_data in available_properties:

            property_location = (
                property_data["location"] or ""
            )

            property_location_lower = (
                property_location.lower()
            )

            rent = property_data["rent"]

            current_type = (
                property_data["property_type"]
            )

            # LOCATION FILTER
            if location:

                location_match = (
                    location.lower()
                    in property_location_lower
                )

                # Mumbai can also appear inside
                # "Andheri, Mumbai", "Bandra, Mumbai", etc.
                if (
                    not location_match
                    and location.lower() == "mumbai"
                    and "mumbai" in property_location_lower
                ):
                    location_match = True

                if not location_match:
                    continue

            # BUDGET FILTER
            if budget is not None:

                if rent > budget:
                    continue

            # PROPERTY TYPE FILTER
            if property_type:

                if current_type != property_type:
                    continue

            matching_properties.append(
                dict(property_data)
            )

        # -------------------------------------------------
        # SORT RESULTS
        # -------------------------------------------------

        if budget_mode == "affordable":

            matching_properties.sort(
                key=lambda item: item["rent"]
            )

        else:

            matching_properties.sort(
                key=lambda item: item["rent"]
            )

        # -------------------------------------------------
        # GENERATE RESPONSE
        # -------------------------------------------------

        if not matching_properties:

            response = (
                "<div class='response-intro'>"
                "I couldn't find an exact match for "
                "your requirements."
                "</div>"
            )

            if location:

                response += (
                    "<div class='preference-summary'>"
                    "<strong>📍 Location:</strong> "
                    f"{location}"
                    "</div>"
                )

            if budget:

                response += (
                    "<div class='preference-summary'>"
                    "<strong>💰 Maximum budget:</strong> "
                    f"₹{budget:,}"
                    "</div>"
                )

            if property_type:

                response += (
                    "<div class='preference-summary'>"
                    "<strong>🏠 Property type:</strong> "
                    f"{property_type}"
                    "</div>"
                )

            if budget_mode == "affordable":

                response += (
                    "<div class='preference-summary'>"
                    "<strong>💡 Preference:</strong> "
                    "Affordable options"
                    "</div>"
                )

            response += (
                "<div class='smart-match-note'>"
                "Try increasing your budget or "
                "changing the location or property type."
                "</div>"
            )

        else:

            top_matches = matching_properties[:5]

            response = (
                "<div class='response-intro'>"
                "Great! I found properties matching "
                "your requirements."
                "</div>"
            )

            # -------------------------------------------------
            # PREFERENCE SUMMARY
            # -------------------------------------------------

            response += (
                "<div class='preference-summary'>"
            )

            summary_parts = []

            if location:

                summary_parts.append(
                    f"<strong>📍 Location:</strong> {location}"
                )

            if budget:

                summary_parts.append(
                    f"<strong>💰 Budget:</strong> ₹{budget:,}"
                )

            if property_type:

                summary_parts.append(
                    f"<strong>🏠 Type:</strong> {property_type}"
                )

            if budget_mode == "affordable":

                summary_parts.append(
                    "<strong>💡 Preference:</strong> Affordable"
                )

            if summary_parts:

                response += " &nbsp; ".join(
                    summary_parts
                )

            else:

                response += (
                    "<strong>✨ Preference:</strong> "
                    "Available properties"
                )

            response += "</div>"

            # -------------------------------------------------
            # PROPERTY RESULTS
            # -------------------------------------------------

            for index, property_item in enumerate(
                top_matches,
                start=1
            ):

                response += (
                    "<div class='property-result'>"

                    f"<div class='property-result-title'>"
                    f"{index}. "
                    f"{property_item['title']}"
                    f"</div>"

                    "<div class='property-result-info'>"

                    f"📍 {property_item['location']}"
                    "<br>"

                    f"🏠 {property_item['property_type']}"

                    f"<div class='property-result-rent'>"
                    f"₹{property_item['rent']:,}/month"
                    f"</div>"

                    f"<a "
                    f"href='/property/{property_item['id']}' "
                    f"class='view-property-btn'>"
                    f"View Property →"
                    f"</a>"

                    "</div>"

                    "</div>"
                )

            response += (
                "<div class='smart-match-note'>"
                "💡 These results are based on your "
                "location, budget and property preferences. "
                "Use <strong>Smart Match</strong> for a "
                "more detailed comparison."
                "</div>"
            )

    return render_template(
        "ai_assistant.html",
        user_query=user_query,
        response=response
    )


# =========================================================
# TENANT DASHBOARD
# =========================================================

@app.route("/tenant-dashboard")
def tenant_dashboard():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("user_role") == "owner":
        return redirect(url_for("owner_dashboard"))

    tenant_id = session["user_id"]

    connection = get_db_connection()

    requests = connection.execute(
        """
        SELECT
            rental_requests.id,
            rental_requests.status,
            rental_requests.request_date,
            properties.title,
            properties.location,
            properties.rent
        FROM rental_requests
        JOIN properties
            ON rental_requests.property_id = properties.id
        WHERE rental_requests.tenant_id = ?
        ORDER BY rental_requests.id DESC
        """,
        (tenant_id,)
    ).fetchall()

    user = connection.execute(
        """
        SELECT
            id,
            name,
            email
        FROM users
        WHERE id = ?
        """,
        (tenant_id,)
    ).fetchone()

    connection.close()

    return render_template(
        "tenant_dashboard.html",
        requests=requests,
        user=user
    )


# =========================================================
# TENANT REGISTRATION
# =========================================================

@app.route(
    "/register",
    methods=["GET", "POST"]
)
def register():

    if request.method == "GET":
        return render_template("register.html")

    name = request.form.get(
        "name",
        ""
    ).strip()

    email = request.form.get(
        "email",
        ""
    ).strip().lower()

    password = request.form.get(
        "password",
        ""
    )

    if not name or not email or not password:

        return render_template(
            "register.html",
            error="All fields are required."
        )

    if len(password) < 6:

        return render_template(
            "register.html",
            error="Password must contain at least 6 characters."
        )

    connection = get_db_connection()

    existing_user = connection.execute(
        """
        SELECT id
        FROM users
        WHERE email = ?
        """,
        (email,)
    ).fetchone()

    if existing_user:

        connection.close()

        return render_template(
            "register.html",
            error="An account with this email already exists."
        )

    password_hash = generate_password_hash(password)

    connection.execute(
        """
        INSERT INTO users
        (
            name,
            email,
            password,
            role
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            name,
            email,
            password_hash,
            "tenant"
        )
    )

    connection.commit()
    connection.close()

    return redirect(
        url_for(
            "login",
            registered="success"
        )
    )


# =========================================================
# OWNER REGISTRATION
# =========================================================

@app.route(
    "/owner-register",
    methods=["GET", "POST"]
)
def owner_register():

    if request.method == "GET":
        return render_template("owner_register.html")

    name = request.form.get(
        "name",
        ""
    ).strip()

    email = request.form.get(
        "email",
        ""
    ).strip().lower()

    password = request.form.get(
        "password",
        ""
    )

    if not name or not email or not password:

        return render_template(
            "owner_register.html",
            error="All fields are required."
        )

    if len(password) < 6:

        return render_template(
            "owner_register.html",
            error="Password must contain at least 6 characters."
        )

    connection = get_db_connection()

    existing_user = connection.execute(
        """
        SELECT id
        FROM users
        WHERE email = ?
        """,
        (email,)
    ).fetchone()

    if existing_user:

        connection.close()

        return render_template(
            "owner_register.html",
            error="An account with this email already exists."
        )

    password_hash = generate_password_hash(password)

    connection.execute(
        """
        INSERT INTO users
        (
            name,
            email,
            password,
            role,
            status
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            name,
            email,
            password_hash,
            "owner",
            "pending"
        )
    )

    connection.commit()
    connection.close()

    return redirect(
        url_for(
            "login",
            registered="owner"
        )
    )


# =========================================================
# LOGIN
# =========================================================

@app.route(
    "/login",
    methods=["GET", "POST"]
)
def login():

    if request.method == "GET":

        registered = request.args.get("registered")
        next_page = request.args.get("next")
        owner_status = request.args.get("owner_status")

        owner_status_message = None

        if owner_status == "pending":
            owner_status_message = (
                "Your owner account is awaiting admin approval. "
                "You will be able to access the Owner Dashboard "
                "once your account is approved."
            )

        elif owner_status == "rejected":
            owner_status_message = (
                "Your owner account has been rejected by the admin "
                "and cannot access owner features."
            )

        return render_template(
            "login.html",
            registered=registered,
            next_page=next_page,
            error=owner_status_message
        )

    email = request.form.get(
        "email",
        ""
    ).strip().lower()

    password = request.form.get(
        "password",
        ""
    )

    next_page = request.form.get(
        "next_page"
    )

    if not email or not password:

        return render_template(
            "login.html",
            error="Please enter your email and password."
        )

    connection = get_db_connection()

    user = connection.execute(
        """
        SELECT *
        FROM users
        WHERE email = ?
        """,
        (email,)
    ).fetchone()

    if user is None:

        connection.close()

        return render_template(
            "login.html",
            error="Invalid email or password."
        )

    stored_password = user["password"]

    password_valid = False

    try:

        password_valid = check_password_hash(
            stored_password,
            password
        )

    except ValueError:

        password_valid = False

    # Support old plaintext accounts
    if (
        not password_valid
        and stored_password == password
    ):

        password_valid = True

        new_password_hash = generate_password_hash(
            password
        )

        connection.execute(
            """
            UPDATE users
            SET password = ?
            WHERE id = ?
            """,
            (
                new_password_hash,
                user["id"]
            )
        )

        connection.commit()

    if not password_valid:

        connection.close()

        return render_template(
            "login.html",
            error="Invalid email or password."
        )

    # ---------------------------------------------------------
    # BLOCK PENDING OWNER ACCOUNTS
    # ---------------------------------------------------------

    if (
        user["role"] == "owner"
        and user["status"] == "pending"
    ):

        connection.close()

        return render_template(
            "login.html",
            error=(
                "Your owner account is awaiting admin approval. "
                "You will be able to access the Owner Dashboard "
                "once your account is approved."
            )
        )

    # ---------------------------------------------------------
    # BLOCK REJECTED OWNER ACCOUNTS
    # ---------------------------------------------------------

    if (
        user["role"] == "owner"
        and user["status"] == "rejected"
    ):

        connection.close()

        return render_template(
            "login.html",
            error=(
                "Your owner account has been rejected by the admin "
                "and cannot access owner features."
            )
        )

    session["user_id"] = user["id"]
    session["user_name"] = user["name"]
    session["user_email"] = user["email"]
    session["user_role"] = user["role"]

    connection.close()

    if next_page:
        return redirect(next_page)

    if user["role"] == "owner":

        return redirect(
            url_for("owner_dashboard")
        )

    return redirect(
        url_for("tenant_dashboard")
    )


# =========================================================
# ADMIN LOGIN
# =========================================================

@app.route(
    "/admin-login",
    methods=["GET", "POST"]
)
def admin_login():

    if request.method == "GET":

        return render_template(
            "admin_login.html"
        )

    email = request.form.get(
        "email",
        ""
    ).strip().lower()

    password = request.form.get(
        "password",
        ""
    )

    if not email or not password:

        return render_template(
            "admin_login.html",
            error="Please enter your admin email and password."
        )

    connection = get_db_connection()

    user = connection.execute(
        """
        SELECT *
        FROM users
        WHERE email = ?
        AND role = 'admin'
        """,
        (email,)
    ).fetchone()

    if user is None:

        connection.close()

        return render_template(
            "admin_login.html",
            error="Invalid admin email or password."
        )

    password_valid = False

    try:

        password_valid = check_password_hash(
            user["password"],
            password
        )

    except ValueError:

        password_valid = False

    if not password_valid:

        connection.close()

        return render_template(
            "admin_login.html",
            error="Invalid admin email or password."
        )

    session["user_id"] = user["id"]
    session["user_name"] = user["name"]
    session["user_email"] = user["email"]
    session["user_role"] = "admin"

    connection.close()

    return redirect(
        url_for("admin_dashboard")
    )


# =========================================================
# ADMIN DASHBOARD
# =========================================================

@app.route("/admin-dashboard")
def admin_dashboard():

    if "user_id" not in session:
        return redirect(url_for("admin_login"))

    if session.get("user_role") != "admin":
        return redirect(url_for("login"))

    connection = get_db_connection()

    total_users = connection.execute(
        "SELECT COUNT(*) FROM users"
    ).fetchone()[0]

    total_properties = connection.execute(
        "SELECT COUNT(*) FROM properties"
    ).fetchone()[0]

    pending_owners = connection.execute(
        """
        SELECT COUNT(*)
        FROM users
        WHERE role = 'owner'
        AND status = 'pending'
        """
    ).fetchone()[0]

    approved_owners = connection.execute(
        """
        SELECT COUNT(*)
        FROM users
        WHERE role = 'owner'
        AND status = 'approved'
        """
    ).fetchone()[0]

    pending_owner_list = connection.execute(
        """
        SELECT id, name, email, status
        FROM users
        WHERE role = 'owner'
        AND status = 'pending'
        ORDER BY id DESC
        """
    ).fetchall()

    connection.close()

    return render_template(
        "admin_dashboard.html",
        total_users=total_users,
        total_properties=total_properties,
        pending_owners=pending_owners,
        approved_owners=approved_owners,
        pending_owner_list=pending_owner_list
    )


# =========================================================
# ADMIN USER MANAGEMENT
# =========================================================

@app.route("/admin-users")
def admin_users():

    if "user_id" not in session:
        return redirect(url_for("admin_login"))

    if session.get("user_role") != "admin":
        return redirect(url_for("login"))

    connection = get_db_connection()

    users = connection.execute(
        """
        SELECT
            id,
            name,
            email,
            role,
            status
        FROM users
        ORDER BY id DESC
        """
    ).fetchall()

    connection.close()

    return render_template(
        "admin_users.html",
        users=users
    )



# =========================================================
# ADMIN USER DELETE
# =========================================================

@app.route(
    "/admin-user/<int:user_id>/delete",
    methods=["POST"]
)
def admin_delete_user(user_id):

    if "user_id" not in session:
        return redirect(url_for("admin_login"))

    if session.get("user_role") != "admin":
        return redirect(url_for("login"))

    connection = get_db_connection()

    user = connection.execute(
        """
        SELECT id, name, email, role
        FROM users
        WHERE id = ?
        """,
        (user_id,)
    ).fetchone()

    if user is None:
        connection.close()
        return redirect(url_for("admin_users"))

    # Protect the primary Havenly admin account.
    if user["email"].strip().lower() == "admin@havenly.com":
        connection.close()
        return redirect(url_for("admin_users"))

    # Never allow the currently logged-in admin to delete itself.
    if user["id"] == session.get("user_id"):
        connection.close()
        return redirect(url_for("admin_users"))

    # Remove rental requests connected to this user's activity.
    connection.execute(
        """
        DELETE FROM rental_requests
        WHERE tenant_id = ?
        """,
        (user_id,)
    )

    # If the user is an owner, remove their property-related records first.
    if user["role"] == "owner":

        owner_properties = connection.execute(
            """
            SELECT id
            FROM properties
            WHERE owner_id = ?
            """,
            (user_id,)
        ).fetchall()

        for property_row in owner_properties:

            property_id = property_row["id"]

            connection.execute(
                """
                DELETE FROM rental_requests
                WHERE property_id = ?
                """,
                (property_id,)
            )

            connection.execute(
                """
                DELETE FROM property_images
                WHERE property_id = ?
                """,
                (property_id,)
            )

        connection.execute(
            """
            DELETE FROM properties
            WHERE owner_id = ?
            """,
            (user_id,)
        )

    connection.execute(
        """
        DELETE FROM users
        WHERE id = ?
        """,
        (user_id,)
    )

    connection.commit()
    connection.close()

    return redirect(url_for("admin_users"))



# =========================================================
# ADMIN PROPERTY OVERSIGHT
# =========================================================

@app.route("/admin-properties")
def admin_properties():

    if "user_id" not in session:
        return redirect(url_for("admin_login"))

    if session.get("user_role") != "admin":
        return redirect(url_for("login"))

    connection = get_db_connection()

    properties = connection.execute(
        """
        SELECT
            properties.id,
            properties.title,
            properties.property_type,
            properties.location,
            properties.rent,
            properties.available,
            users.name AS owner_name,
            users.email AS owner_email
        FROM properties
        LEFT JOIN users
            ON properties.owner_id = users.id
        ORDER BY properties.id DESC
        """
    ).fetchall()

    connection.close()

    return render_template(
        "admin_properties.html",
        properties=properties
    )


# =========================================================
# ADMIN PLATFORM SAFETY
# =========================================================

@app.route("/admin-safety")
def admin_safety():

    if "user_id" not in session:
        return redirect(url_for("admin_login"))

    if session.get("user_role") != "admin":
        return redirect(url_for("login"))

    connection = get_db_connection()

    pending_owner_list = connection.execute(
        """
        SELECT
            id,
            name,
            email,
            status
        FROM users
        WHERE role = 'owner'
        AND status = 'pending'
        ORDER BY id DESC
        """
    ).fetchall()

    rejected_owners = connection.execute(
        """
        SELECT
            id,
            name,
            email,
            status
        FROM users
        WHERE role = 'owner'
        AND status = 'rejected'
        ORDER BY id DESC
        """
    ).fetchall()

    approved_owners = connection.execute(
        """
        SELECT
            id,
            name,
            email,
            status
        FROM users
        WHERE role = 'owner'
        AND status = 'approved'
        ORDER BY id DESC
        """
    ).fetchall()

    connection.close()

    return render_template(
        "admin_safety.html",
        pending_owner_list=pending_owner_list,
        rejected_owners=rejected_owners,
        approved_owners=approved_owners
    )


# =========================================================
# ADMIN APPROVE OWNER
# =========================================================

@app.route(
    "/admin-owner/<int:owner_id>/approve",
    methods=["POST"]
)
def admin_approve_owner(owner_id):

    if "user_id" not in session:
        return redirect(url_for("admin_login"))

    if session.get("user_role") != "admin":
        return redirect(url_for("login"))

    connection = get_db_connection()

    connection.execute(
        """
        UPDATE users
        SET status = 'approved'
        WHERE id = ?
        AND role = 'owner'
        AND status = 'pending'
        """,
        (owner_id,)
    )

    connection.commit()
    connection.close()

    return redirect(url_for("admin_dashboard"))


# =========================================================
# ADMIN REJECT OWNER
# =========================================================

@app.route(
    "/admin-owner/<int:owner_id>/reject",
    methods=["POST"]
)
def admin_reject_owner(owner_id):

    if "user_id" not in session:
        return redirect(url_for("admin_login"))

    if session.get("user_role") != "admin":
        return redirect(url_for("login"))

    connection = get_db_connection()

    connection.execute(
        """
        UPDATE users
        SET status = 'rejected'
        WHERE id = ?
        AND role = 'owner'
        AND status = 'pending'
        """,
        (owner_id,)
    )

    connection.commit()
    connection.close()

    return redirect(url_for("admin_dashboard"))


# =========================================================
# OWNER REQUESTS
# =========================================================

@app.route("/owner-requests")
def owner_requests():

    owner_access = require_approved_owner()

    if owner_access:
        return owner_access

    owner_id = session["user_id"]

    connection = get_db_connection()

    rental_requests = connection.execute(
        """
        SELECT
            rental_requests.id,
            rental_requests.status,
            rental_requests.request_date,
            users.name AS tenant_name,
            users.email AS tenant_email,
            properties.title AS property_title,
            properties.location,
            properties.rent
        FROM rental_requests
        JOIN users
            ON rental_requests.tenant_id = users.id
        JOIN properties
            ON rental_requests.property_id = properties.id
        WHERE properties.owner_id = ?
        ORDER BY rental_requests.id DESC
        """,
        (owner_id,)
    ).fetchall()

    total_requests = connection.execute(
        """
        SELECT COUNT(*)
        FROM rental_requests
        JOIN properties
            ON rental_requests.property_id = properties.id
        WHERE properties.owner_id = ?
        """,
        (owner_id,)
    ).fetchone()[0]

    pending_requests = connection.execute(
        """
        SELECT COUNT(*)
        FROM rental_requests
        JOIN properties
            ON rental_requests.property_id = properties.id
        WHERE properties.owner_id = ?
        AND rental_requests.status = 'Pending'
        """,
        (owner_id,)
    ).fetchone()[0]

    approved_requests = connection.execute(
        """
        SELECT COUNT(*)
        FROM rental_requests
        JOIN properties
            ON rental_requests.property_id = properties.id
        WHERE properties.owner_id = ?
        AND rental_requests.status = 'Approved'
        """,
        (owner_id,)
    ).fetchone()[0]

    connection.close()

    return render_template(
        "owner_requests.html",
        rental_requests=rental_requests,
        total_requests=total_requests,
        pending_requests=pending_requests,
        approved_requests=approved_requests
    )


# =========================================================
# APPROVE RENTAL REQUEST
# =========================================================

@app.route(
    "/owner-request/<int:request_id>/approve",
    methods=["POST"]
)
def approve_rental_request(request_id):

    owner_access = require_approved_owner()

    if owner_access:
        return owner_access

    owner_id = session["user_id"]

    connection = get_db_connection()

    rental_request = connection.execute(
        """
        SELECT
            rental_requests.id,
            rental_requests.property_id,
            rental_requests.tenant_id,
            rental_requests.status
        FROM rental_requests
        JOIN properties
            ON rental_requests.property_id = properties.id
        WHERE rental_requests.id = ?
        AND properties.owner_id = ?
        """,
        (
            request_id,
            owner_id
        )
    ).fetchone()

    if rental_request is None:

        connection.close()

        return redirect(
            url_for("owner_requests")
        )

    if rental_request["status"] != "Pending":

        connection.close()

        return redirect(
            url_for("owner_requests")
        )

    property_id = rental_request["property_id"]

    connection.execute(
        """
        UPDATE rental_requests
        SET status = 'Approved'
        WHERE id = ?
        """,
        (request_id,)
    )

    connection.execute(
        """
        UPDATE properties
        SET available = 0
        WHERE id = ?
        AND owner_id = ?
        """,
        (
            property_id,
            owner_id
        )
    )

    connection.execute(
        """
        UPDATE rental_requests
        SET status = 'Rejected'
        WHERE property_id = ?
        AND status = 'Pending'
        AND id != ?
        """,
        (
            property_id,
            request_id
        )
    )

    connection.commit()
    connection.close()

    return redirect(
        url_for("owner_requests")
    )


# =========================================================
# REJECT RENTAL REQUEST
# =========================================================

@app.route(
    "/owner-request/<int:request_id>/reject",
    methods=["POST"]
)
def reject_rental_request(request_id):

    owner_access = require_approved_owner()

    if owner_access:
        return owner_access

    owner_id = session["user_id"]

    connection = get_db_connection()

    rental_request = connection.execute(
        """
        SELECT
            rental_requests.id,
            rental_requests.status
        FROM rental_requests
        JOIN properties
            ON rental_requests.property_id = properties.id
        WHERE rental_requests.id = ?
        AND properties.owner_id = ?
        """,
        (
            request_id,
            owner_id
        )
    ).fetchone()

    if rental_request is None:

        connection.close()

        return redirect(
            url_for("owner_requests")
        )

    if rental_request["status"] != "Pending":

        connection.close()

        return redirect(
            url_for("owner_requests")
        )

    connection.execute(
        """
        UPDATE rental_requests
        SET status = 'Rejected'
        WHERE id = ?
        """,
        (request_id,)
    )

    connection.commit()
    connection.close()

    return redirect(
        url_for("owner_requests")
    )


# =========================================================
# OWNER DASHBOARD
# =========================================================

@app.route("/owner-dashboard")
def owner_dashboard():

    owner_access = require_approved_owner()

    if owner_access:
        return owner_access

    owner_id = session["user_id"]

    connection = get_db_connection()

    owner_properties = connection.execute(
        """
        SELECT *
        FROM properties
        WHERE owner_id = ?
        ORDER BY id DESC
        """,
        (owner_id,)
    ).fetchall()

    total_properties = connection.execute(
        """
        SELECT COUNT(*)
        FROM properties
        WHERE owner_id = ?
        """,
        (owner_id,)
    ).fetchone()[0]

    active_properties = connection.execute(
        """
        SELECT COUNT(*)
        FROM properties
        WHERE owner_id = ?
        AND available = 1
        """,
        (owner_id,)
    ).fetchone()[0]

    total_requests = connection.execute(
        """
        SELECT COUNT(*)
        FROM rental_requests
        JOIN properties
            ON rental_requests.property_id = properties.id
        WHERE properties.owner_id = ?
        """,
        (owner_id,)
    ).fetchone()[0]

    pending_requests = connection.execute(
        """
        SELECT COUNT(*)
        FROM rental_requests
        JOIN properties
            ON rental_requests.property_id = properties.id
        WHERE properties.owner_id = ?
        AND rental_requests.status = 'Pending'
        """,
        (owner_id,)
    ).fetchone()[0]

    connection.close()

    return render_template(
        "owner_dashboard.html",
        owner_properties=owner_properties,
        total_properties=total_properties,
        active_properties=active_properties,
        total_requests=total_requests,
        pending_requests=pending_requests
    )


# =========================================================
# ADD PROPERTY
# =========================================================

@app.route(
    "/add-property",
    methods=["GET", "POST"]
)
def add_property():

    owner_access = require_approved_owner()

    if owner_access:
        return owner_access

    if request.method == "GET":
        return render_template("add_property.html")

    title = request.form.get(
        "title",
        ""
    ).strip()

    property_type = request.form.get(
        "property_type",
        ""
    ).strip()

    location = request.form.get(
        "location",
        ""
    ).strip()

    rent = request.form.get(
        "rent",
        ""
    ).strip()

    description = request.form.get(
        "description",
        ""
    ).strip()

    available = (
        1
        if request.form.get("available") == "1"
        else 0
    )

    if (
        not title
        or not property_type
        or not location
        or not rent
    ):

        return render_template(
            "add_property.html",
            error="Please fill all required fields."
        )

    try:

        rent = int(rent)

    except ValueError:

        return render_template(
            "add_property.html",
            error="Please enter a valid rent amount."
        )

    if rent <= 0:

        return render_template(
            "add_property.html",
            error="Rent must be greater than 0."
        )

    allowed_types = [
        "PG",
        "Apartment",
        "Room"
    ]

    if property_type not in allowed_types:

        return render_template(
            "add_property.html",
            error="Invalid property type."
        )

    owner_id = session["user_id"]

    uploaded_images = request.files.getlist(
        "property_images"
    )

    valid_images = []

    for image in uploaded_images:

        if (
            image
            and image.filename
            and allowed_image(image.filename)
        ):

            valid_images.append(image)

    connection = get_db_connection()

    cursor = connection.execute(
        """
        INSERT INTO properties
        (
            title,
            property_type,
            location,
            rent,
            description,
            image_url,
            available,
            owner_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            title,
            property_type,
            location,
            rent,
            description,
            "",
            available,
            owner_id
        )
    )

    property_id = cursor.lastrowid

    first_image_url = ""
    uploaded_image_urls = []

    try:

        for image in valid_images:

            image_url = upload_image_to_supabase(
                image,
                property_id
            )

            uploaded_image_urls.append(image_url)

            connection.execute(
                """
                INSERT INTO property_images
                (
                    property_id,
                    image_url
                )
                VALUES (?, ?)
                """,
                (
                    property_id,
                    image_url
                )
            )

            if not first_image_url:
                first_image_url = image_url

        if first_image_url:

            connection.execute(
                """
                UPDATE properties
                SET image_url = ?
                WHERE id = ?
                """,
                (
                    first_image_url,
                    property_id
                )
            )

        connection.commit()
        connection.close()

    except Exception as error:

        connection.rollback()
        connection.close()

        for uploaded_url in uploaded_image_urls:
            delete_supabase_image(uploaded_url)

        return render_template(
            "add_property.html",
            error=(
                "Property could not be saved. "
                f"Image upload failed: {error}"
            )
        )

    return redirect(
        url_for(
            "owner_dashboard",
            added="success"
        )
    )


# =========================================================
# EDIT PROPERTY
# =========================================================

@app.route(
    "/edit-property/<int:property_id>",
    methods=["GET", "POST"]
)
def edit_property(property_id):

    owner_access = require_approved_owner()

    if owner_access:
        return owner_access

    owner_id = session["user_id"]

    connection = get_db_connection()

    property_data = connection.execute(
        """
        SELECT *
        FROM properties
        WHERE id = ?
        AND owner_id = ?
        """,
        (
            property_id,
            owner_id
        )
    ).fetchone()

    if property_data is None:

        connection.close()

        return (
            "Property not found or "
            "you do not have permission "
            "to edit it.",
            404
        )

    property_images = connection.execute(
        """
        SELECT
            id,
            image_url
        FROM property_images
        WHERE property_id = ?
        ORDER BY id
        """,
        (property_id,)
    ).fetchall()

    if request.method == "GET":

        connection.close()

        return render_template(
            "edit_property.html",
            property=property_data,
            property_images=property_images
        )

    title = request.form.get(
        "title",
        ""
    ).strip()

    property_type = request.form.get(
        "property_type",
        ""
    ).strip()

    location = request.form.get(
        "location",
        ""
    ).strip()

    rent = request.form.get(
        "rent",
        ""
    ).strip()

    description = request.form.get(
        "description",
        ""
    ).strip()

    available = (
        1
        if request.form.get("available") == "1"
        else 0
    )

    if (
        not title
        or not property_type
        or not location
        or not rent
    ):

        connection.close()

        return render_template(
            "edit_property.html",
            property=property_data,
            property_images=property_images,
            error="Please fill all required fields."
        )

    try:

        rent = int(rent)

    except ValueError:

        connection.close()

        return render_template(
            "edit_property.html",
            property=property_data,
            property_images=property_images,
            error="Please enter a valid rent amount."
        )

    if rent <= 0:

        connection.close()

        return render_template(
            "edit_property.html",
            property=property_data,
            property_images=property_images,
            error="Rent must be greater than 0."
        )

    allowed_types = [
        "PG",
        "Apartment",
        "Room"
    ]

    if property_type not in allowed_types:

        connection.close()

        return render_template(
            "edit_property.html",
            property=property_data,
            property_images=property_images,
            error="Invalid property type."
        )

    # UPDATE PROPERTY DETAILS

    connection.execute(
        """
        UPDATE properties
        SET
            title = ?,
            property_type = ?,
            location = ?,
            rent = ?,
            description = ?,
            available = ?
        WHERE id = ?
        AND owner_id = ?
        """,
        (
            title,
            property_type,
            location,
            rent,
            description,
            available,
            property_id,
            owner_id
        )
    )

    # HANDLE NEW IMAGES

    uploaded_images = request.files.getlist(
        "property_images"
    )

    valid_images = []

    for image in uploaded_images:

        if (
            image
            and image.filename
            and allowed_image(image.filename)
        ):

            valid_images.append(image)

    first_new_image_url = ""
    uploaded_image_urls = []

    try:

        for image in valid_images:

            image_url = upload_image_to_supabase(
                image,
                property_id
            )

            uploaded_image_urls.append(image_url)

            connection.execute(
                """
                INSERT INTO property_images
                (
                    property_id,
                    image_url
                )
                VALUES (?, ?)
                """,
                (
                    property_id,
                    image_url
                )
            )

            if not first_new_image_url:
                first_new_image_url = image_url

        # Set main image if there wasn't one

        if (
            not property_data["image_url"]
            and first_new_image_url
        ):

            connection.execute(
                """
                UPDATE properties
                SET image_url = ?
                WHERE id = ?
                AND owner_id = ?
                """,
                (
                    first_new_image_url,
                    property_id,
                    owner_id
                )
            )

        connection.commit()
        connection.close()

    except Exception as error:

        connection.rollback()
        connection.close()

        for uploaded_url in uploaded_image_urls:
            delete_supabase_image(uploaded_url)

        return render_template(
            "edit_property.html",
            property=property_data,
            property_images=property_images,
            error=(
                "Property could not be updated. "
                f"Image upload failed: {error}"
            )
        )

    return redirect(
        url_for(
            "owner_dashboard",
            updated="success"
        )
    )


# =========================================================
# DELETE PROPERTY
# =========================================================

@app.route(
    "/delete-property/<int:property_id>",
    methods=["POST"]
)
def delete_property(property_id):

    owner_access = require_approved_owner()

    if owner_access:
        return owner_access

    owner_id = session["user_id"]

    connection = get_db_connection()

    property_data = connection.execute(
        """
        SELECT
            id,
            image_url
        FROM properties
        WHERE id = ?
        AND owner_id = ?
        """,
        (
            property_id,
            owner_id
        )
    ).fetchone()

    if property_data is None:

        connection.close()

        return (
            "Property not found or "
            "you do not have permission "
            "to delete it.",
            404
        )

    property_images = connection.execute(
        """
        SELECT image_url
        FROM property_images
        WHERE property_id = ?
        """,
        (property_id,)
    ).fetchall()

    connection.execute(
        """
        DELETE FROM rental_requests
        WHERE property_id = ?
        """,
        (property_id,)
    )

    connection.execute(
        """
        DELETE FROM property_images
        WHERE property_id = ?
        """,
        (property_id,)
    )

    connection.execute(
        """
        DELETE FROM properties
        WHERE id = ?
        AND owner_id = ?
        """,
        (
            property_id,
            owner_id
        )
    )

    connection.commit()
    connection.close()

    image_urls = []

    if property_data["image_url"]:
        image_urls.append(
            property_data["image_url"]
        )

    for image in property_images:

        if image["image_url"]:

            image_urls.append(
                image["image_url"]
            )

    image_urls = list(
        set(image_urls)
    )

    for image_url in image_urls:

        if image_url.startswith(
            "/static/uploads/"
        ):

            filename = image_url.replace(
                "/static/uploads/",
                "",
                1
            )

            file_path = os.path.join(
                app.config["UPLOAD_FOLDER"],
                filename
            )

            try:

                if os.path.exists(file_path):
                    os.remove(file_path)

            except OSError:
                pass

        elif "/storage/v1/object/public/property-images/" in image_url:
            delete_supabase_image(image_url)

    return redirect(
        url_for(
            "owner_dashboard",
            deleted="success"
        )
    )

# =========================================================
# OWNER AI INSIGHTS
# =========================================================

@app.route(
    "/owner-ai-insights",
    methods=["GET"]
)
def owner_ai_insights():

    owner_access = require_approved_owner()

    if owner_access:
        return owner_access

    owner_id = session["user_id"]

    question = request.args.get(
        "question",
        ""
    ).strip()

    response = ""

    connection = get_db_connection()


    # =====================================================
    # OWNER PROPERTY DATA
    # =====================================================

    owner_properties = connection.execute(
        """
        SELECT
            *
        FROM properties
        WHERE owner_id = ?
        ORDER BY id DESC
        """,
        (owner_id,)
    ).fetchall()


    # =====================================================
    # PROPERTY COUNTS
    # =====================================================

    total_properties = connection.execute(
        """
        SELECT COUNT(*)
        FROM properties
        WHERE owner_id = ?
        """,
        (owner_id,)
    ).fetchone()[0]


    available_properties = connection.execute(
        """
        SELECT COUNT(*)
        FROM properties
        WHERE owner_id = ?
        AND available = 1
        """,
        (owner_id,)
    ).fetchone()[0]


    rented_properties = connection.execute(
        """
        SELECT COUNT(*)
        FROM properties
        WHERE owner_id = ?
        AND available = 0
        """,
        (owner_id,)
    ).fetchone()[0]


    # =====================================================
    # RENTAL REQUEST COUNTS
    # =====================================================

    total_requests = connection.execute(
        """
        SELECT COUNT(*)
        FROM rental_requests
        JOIN properties
            ON rental_requests.property_id = properties.id
        WHERE properties.owner_id = ?
        """,
        (owner_id,)
    ).fetchone()[0]


    pending_requests = connection.execute(
        """
        SELECT COUNT(*)
        FROM rental_requests
        JOIN properties
            ON rental_requests.property_id = properties.id
        WHERE properties.owner_id = ?
        AND rental_requests.status = 'Pending'
        """,
        (owner_id,)
    ).fetchone()[0]


    approved_requests = connection.execute(
        """
        SELECT COUNT(*)
        FROM rental_requests
        JOIN properties
            ON rental_requests.property_id = properties.id
        WHERE properties.owner_id = ?
        AND rental_requests.status = 'Approved'
        """,
        (owner_id,)
    ).fetchone()[0]


    rejected_requests = connection.execute(
        """
        SELECT COUNT(*)
        FROM rental_requests
        JOIN properties
            ON rental_requests.property_id = properties.id
        WHERE properties.owner_id = ?
        AND rental_requests.status = 'Rejected'
        """,
        (owner_id,)
    ).fetchone()[0]


    # =====================================================
    # MOST REQUESTED PROPERTY
    # =====================================================

    most_requested = connection.execute(
        """
        SELECT
            properties.title,
            properties.location,
            COUNT(rental_requests.id) AS request_count

        FROM properties

        LEFT JOIN rental_requests
            ON rental_requests.property_id = properties.id

        WHERE properties.owner_id = ?

        GROUP BY properties.id

        ORDER BY request_count DESC

        LIMIT 1
        """,
        (owner_id,)
    ).fetchone()


    connection.close()


    # =====================================================
    # AI QUESTION PROCESSING
    # =====================================================

    normalized_question = question.lower()


    if not question:

        response = (
            "👋 Hi! I’m Havenly AI, your property assistant. "
            "Ask me about your properties, rental requests, "
            "availability or property performance."
        )


    elif (
        "how many properties" in normalized_question
        or "total properties" in normalized_question
        or "number of properties" in normalized_question
    ):

        response = (
            f"🏠 You currently have "
            f"<strong>{total_properties}</strong> "
            f"properties listed on Havenly."
            f"<br><br>"
            f"• Available: <strong>{available_properties}</strong>"
            f"<br>"
            f"• Rented: <strong>{rented_properties}</strong>"
        )


    elif (
        "pending" in normalized_question
        and "request" in normalized_question
    ):

        response = (
            f"📩 You currently have "
            f"<strong>{pending_requests}</strong> "
            f"pending rental request"
            f"{'s' if pending_requests != 1 else ''}."
            f"<br><br>"
            f"Total rental requests: "
            f"<strong>{total_requests}</strong>"
        )


    elif (
        "available" in normalized_question
        and (
            "property" in normalized_question
            or "properties" in normalized_question
        )
    ):

        if available_properties == 0:

            response = (
                "🏠 You currently don't have any "
                "available properties."
            )

        else:

            response = (
                f"🏠 You have "
                f"<strong>{available_properties}</strong> "
                f"available propert"
                f"{'y' if available_properties == 1 else 'ies'}."
                f"<br><br>"
            )

            available_list = [
                property_item
                for property_item in owner_properties
                if property_item["available"] == 1
            ]


            for property_item in available_list:

                response += (
                    f"• <strong>"
                    f"{property_item['title']}"
                    f"</strong>"
                    f" — {property_item['location']}"
                    f" — ₹"
                    f"{property_item['rent']:,}"
                    f"/month"
                    f"<br>"
                )


    elif (
        "most" in normalized_question
        and (
            "request" in normalized_question
            or "requested" in normalized_question
        )
    ):

        if (
            most_requested is None
            or most_requested["request_count"] == 0
        ):

            response = (
                "📊 None of your properties have received "
                "a rental request yet."
            )

        else:

            response = (
                "📊 Your most requested property is "
                f"<strong>"
                f"{most_requested['title']}"
                f"</strong>."
                f"<br><br>"
                f"📍 {most_requested['location']}"
                f"<br>"
                f"📩 "
                f"<strong>"
                f"{most_requested['request_count']}"
                f"</strong> rental request"
                f"{'s' if most_requested['request_count'] != 1 else ''}"
            )


    elif (
        "rental request" in normalized_question
        or "rental requests" in normalized_question
    ):

        response = (
            "📩 Here is your rental request summary:"
            f"<br><br>"
            f"• Total: "
            f"<strong>{total_requests}</strong>"
            f"<br>"
            f"• Pending: "
            f"<strong>{pending_requests}</strong>"
            f"<br>"
            f"• Approved: "
            f"<strong>{approved_requests}</strong>"
            f"<br>"
            f"• Rejected: "
            f"<strong>{rejected_requests}</strong>"
        )


    elif (
        "available properties" in normalized_question
        or "availability" in normalized_question
    ):

        response = (
            "📊 Your current availability:"
            f"<br><br>"
            f"• Total properties: "
            f"<strong>{total_properties}</strong>"
            f"<br>"
            f"• Available: "
            f"<strong>{available_properties}</strong>"
            f"<br>"
            f"• Rented: "
            f"<strong>{rented_properties}</strong>"
        )


    elif (
        "performance" in normalized_question
        or "overview" in normalized_question
        or "summary" in normalized_question
    ):

        response = (
            "📊 <strong>Your Havenly Property Overview</strong>"
            f"<br><br>"
            f"🏠 Total properties: "
            f"<strong>{total_properties}</strong>"
            f"<br>"
            f"🟢 Available: "
            f"<strong>{available_properties}</strong>"
            f"<br>"
            f"🔴 Rented: "
            f"<strong>{rented_properties}</strong>"
            f"<br>"
            f"📩 Total requests: "
            f"<strong>{total_requests}</strong>"
            f"<br>"
            f"⏳ Pending requests: "
            f"<strong>{pending_requests}</strong>"
            f"<br>"
            f"✅ Approved requests: "
            f"<strong>{approved_requests}</strong>"
        )


    else:

        response = (
            "🤖 I can help you with your Havenly "
            "property data."
            f"<br><br>"
            "Try asking:"
            f"<br>"
            "• How many properties do I have?"
            f"<br>"
            "• How many pending requests do I have?"
            f"<br>"
            "• Which of my properties are available?"
            f"<br>"
            "• Which property has the most requests?"
            f"<br>"
            "• Show me my rental requests"
        )


    return render_template(
        "owner_ai_insights.html",
        question=question,
        response=response
    )

# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("home")
    )


# =========================================================
# RUN APPLICATION
# =========================================================

if __name__ == "__main__":

    app.run(
        debug=True
    )