import os
import json
import uuid
import secrets
import hashlib
import struct
import logging
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, request, redirect, url_for, session, flash, render_template_string, jsonify, abort

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
import pyodbc

try:
    import requests
except ImportError:
    requests = None


# ============================================================
# HOME EATS
# Food made from home.
#
# This single file is designed for Azure App Service.
#
# Azure services used by the app:
#   - Azure App Service / App Service Plan: hosts Flask
#   - Azure SQL: users, food, orders, ratings, forum, events
#   - Azure Blob Storage: food photos + event archive
#   - VNet / Private Endpoints: network path to private Azure services
#   - Event Grid: optional event publishing
#
# VM / Bastion / NSG / RBAC are infrastructure around the app.
# They are not application databases/APIs, so the Flask app does
# not need to call them on every request.
#
# IMPORTANT:
# This prototype does NOT process real card payments. The order
# flow records an order/reservation. A real payment provider such
# as Stripe should be added before accepting real money.
#
# Secrets/Key Vault integration has not been implemented yet.
# ============================================================

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", secrets.token_hex(32))

app.config.update(
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("FLASK_SESSION_SECURE", "false").lower() == "true",
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("home-eats")

# Azure settings
STORAGE_ACCOUNT = os.getenv("AZURE_STORAGE_ACCOUNT", "pystorageacc441")
STORAGE_CONTAINER = os.getenv("AZURE_STORAGE_CONTAINER", "home-eats")
STORAGE_CONNECTION = os.getenv("AZURE_STORAGE_CONNECTION_STRING")

SQL_SERVER = os.getenv("AZURE_SQL_SERVER", "sqlserv441.database.windows.net")
SQL_DATABASE = os.getenv("AZURE_SQL_DATABASE", "sqldb441")
SQL_DRIVER = os.getenv("AZURE_SQL_DRIVER", "{ODBC Driver 18 for SQL Server}")

EVENT_GRID_ENDPOINT = os.getenv("EVENT_GRID_ENDPOINT")
EVENT_GRID_KEY = os.getenv("EVENT_GRID_KEY")

credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
blob_container = None


# ============================================================
# Azure clients
# ============================================================

def get_blob_container():
    global blob_container

    if blob_container:
        return blob_container

    if STORAGE_CONNECTION:
        service = BlobServiceClient.from_connection_string(STORAGE_CONNECTION)
    else:
        service = BlobServiceClient(
            account_url=f"https://{STORAGE_ACCOUNT}.blob.core.windows.net",
            credential=credential,
        )

    blob_container = service.get_container_client(STORAGE_CONTAINER)

    try:
        if not blob_container.exists():
            blob_container.create_container()
    except Exception:
        log.exception("Blob container could not be initialised.")
        raise

    return blob_container


def sql_connection():
    """Azure SQL using an Entra access token from Managed Identity."""
    token = credential.get_token("https://database.windows.net/.default")

    token_bytes = token.token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s>", len(token_bytes), token_bytes)

    connection_string = (
        f"DRIVER={SQL_DRIVER};"
        f"SERVER=tcp:{SQL_SERVER},1433;"
        f"DATABASE={SQL_DATABASE};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "Connection Timeout=30;"
    )

    # 1256 = SQL_COPT_SS_ACCESS_TOKEN
    return pyodbc.connect(
        connection_string,
        attrs_before={1256: token_struct},
    )


# ============================================================
# Database
# ============================================================

def init_database():
    conn = sql_connection()
    cur = conn.cursor()

    tables = [
        """
        IF OBJECT_ID('users', 'U') IS NULL
        CREATE TABLE users (
            id INT IDENTITY PRIMARY KEY,
            name NVARCHAR(120) NOT NULL,
            email NVARCHAR(255) NOT NULL UNIQUE,
            password_hash NVARCHAR(255) NOT NULL,
            created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
        )
        """,
        """
        IF OBJECT_ID('food_listings', 'U') IS NULL
        CREATE TABLE food_listings (
            id INT IDENTITY PRIMARY KEY,
            seller_id INT NOT NULL,
            title NVARCHAR(160) NOT NULL,
            description NVARCHAR(MAX) NOT NULL,
            category NVARCHAR(80) NOT NULL,
            price DECIMAL(10,2) NOT NULL,
            servings INT NOT NULL,
            collection_area NVARCHAR(200) NOT NULL,
            collection_address NVARCHAR(500) NOT NULL,
            image_url NVARCHAR(1000) NULL,
            available BIT NOT NULL DEFAULT 1,
            created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
            FOREIGN KEY (seller_id) REFERENCES users(id)
        )
        """,
        """
        IF OBJECT_ID('orders', 'U') IS NULL
        CREATE TABLE orders (
            id INT IDENTITY PRIMARY KEY,
            buyer_id INT NOT NULL,
            listing_id INT NOT NULL,
            quantity INT NOT NULL,
            total DECIMAL(10,2) NOT NULL,
            status NVARCHAR(40) NOT NULL DEFAULT 'reserved',
            collection_code NVARCHAR(80) NOT NULL UNIQUE,
            created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
            FOREIGN KEY (buyer_id) REFERENCES users(id),
            FOREIGN KEY (listing_id) REFERENCES food_listings(id)
        )
        """,
        """
        IF OBJECT_ID('ratings', 'U') IS NULL
        CREATE TABLE ratings (
            id INT IDENTITY PRIMARY KEY,
            order_id INT NOT NULL UNIQUE,
            buyer_id INT NOT NULL,
            listing_id INT NOT NULL,
            stars INT NOT NULL,
            review NVARCHAR(MAX),
            created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
            FOREIGN KEY (order_id) REFERENCES orders(id),
            FOREIGN KEY (buyer_id) REFERENCES users(id),
            FOREIGN KEY (listing_id) REFERENCES food_listings(id)
        )
        """,
        """
        IF OBJECT_ID('forum_posts', 'U') IS NULL
        CREATE TABLE forum_posts (
            id INT IDENTITY PRIMARY KEY,
            author_id INT NOT NULL,
            title NVARCHAR(200) NOT NULL,
            body NVARCHAR(MAX) NOT NULL,
            category NVARCHAR(80) NOT NULL DEFAULT 'General',
            created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
            FOREIGN KEY (author_id) REFERENCES users(id)
        )
        """,
        """
        IF OBJECT_ID('forum_comments', 'U') IS NULL
        CREATE TABLE forum_comments (
            id INT IDENTITY PRIMARY KEY,
            post_id INT NOT NULL,
            author_id INT NOT NULL,
            body NVARCHAR(MAX) NOT NULL,
            created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
            FOREIGN KEY (post_id) REFERENCES forum_posts(id),
            FOREIGN KEY (author_id) REFERENCES users(id)
        )
        """,
        """
        IF OBJECT_ID('application_events', 'U') IS NULL
        CREATE TABLE application_events (
            id INT IDENTITY PRIMARY KEY,
            event_id NVARCHAR(80) NOT NULL,
            event_type NVARCHAR(120) NOT NULL,
            actor_id INT NULL,
            entity_type NVARCHAR(80) NULL,
            entity_id NVARCHAR(100) NULL,
            payload NVARCHAR(MAX) NULL,
            created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
        )
        """,
    ]

    for table in tables:
        cur.execute(table)

    conn.commit()
    conn.close()


# ============================================================
# Events
# ============================================================

def emit_event(event_type, actor_id=None, entity_type=None, entity_id=None, data=None):
    """
    One application event can:
      1. appear in App Service logs,
      2. be stored in Azure SQL,
      3. be archived in Blob Storage,
      4. be sent to Event Grid if configured.

    This is deliberately separate from Azure Activity Log:
    Activity Log records Azure resource/control-plane operations,
    while these are Home Eats application events.
    """
    event_id = str(uuid.uuid4())
    event = {
        "id": event_id,
        "eventType": event_type,
        "subject": f"/home-eats/{entity_type or 'app'}/{entity_id or 'event'}",
        "eventTime": datetime.now(timezone.utc).isoformat(),
        "dataVersion": "1.0",
        "data": data or {},
    }

    log.info("HOME_EATS_EVENT %s", json.dumps(event, default=str))

    try:
        conn = sql_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO application_events
            (event_id, event_type, actor_id, entity_type, entity_id, payload)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            event_id,
            event_type,
            actor_id,
            entity_type,
            str(entity_id) if entity_id is not None else None,
            json.dumps(data or {}, default=str),
        )
        conn.commit()
        conn.close()
    except Exception:
        log.exception("Could not save application event to SQL.")

    try:
        container = get_blob_container()
        blob_name = f"events/{datetime.now(timezone.utc):%Y/%m/%d}/{event_id}.json"
        container.upload_blob(
            blob_name,
            json.dumps(event, indent=2),
            overwrite=False,
            content_settings=ContentSettings(content_type="application/json"),
        )
    except Exception:
        log.exception("Could not archive event to Blob Storage.")

    if EVENT_GRID_ENDPOINT and EVENT_GRID_KEY and requests:
        try:
            response = requests.post(
                EVENT_GRID_ENDPOINT,
                headers={
                    "aeg-sas-key": EVENT_GRID_KEY,
                    "Content-Type": "application/json",
                },
                json=[event],
                timeout=10,
            )
            response.raise_for_status()
        except Exception:
            log.exception("Event Grid publishing failed.")


# ============================================================
# Authentication
# ============================================================

def password_hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310000)
    return f"{salt.hex()}${digest.hex()}"


def password_matches(password, stored):
    try:
        salt, expected = stored.split("$", 1)
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            bytes.fromhex(salt),
            310000,
        ).hex()
        return secrets.compare_digest(actual, expected)
    except Exception:
        return False


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None

    conn = sql_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name, email FROM users WHERE id = ?", user_id)
    row = cur.fetchone()
    conn.close()

    if not row:
        session.clear()
        return None

    return {"id": row[0], "name": row[1], "email": row[2]}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            flash("Please log in first.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


# ============================================================
# UI
# ============================================================

BASE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }} · Home Eats</title>
<style>
:root{
 --cream:#fff8ee;--cream2:#f4e5d1;--brown:#43291c;--brown2:#70472f;
 --orange:#d96d2c;--orange2:#f4ae70;--green:#5d704c;--white:#fffdf9;
 --line:#ead7c0;--shadow:0 18px 45px rgba(67,41,28,.12)
}
*{box-sizing:border-box}
body{margin:0;background:var(--cream);color:var(--brown);
font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
a{text-decoration:none;color:inherit}
nav{position:sticky;top:0;z-index:20;background:rgba(255,253,249,.94);
backdrop-filter:blur(15px);border-bottom:1px solid var(--line)}
.nav{max-width:1200px;margin:auto;padding:15px 22px;display:flex;align-items:center;gap:22px}
.brand{font-size:27px;font-weight:950;letter-spacing:-1.5px;margin-right:auto}
.brand span{color:var(--orange)}
.links{display:flex;align-items:center;gap:17px;font-weight:750}
.links a:hover{color:var(--orange)}
.wrap{max-width:1200px;margin:auto;padding:28px 22px 60px}
.hero{min-height:500px;border-radius:32px;padding:58px;display:flex;align-items:center;color:#fff;
background:linear-gradient(90deg,rgba(39,22,13,.92),rgba(39,22,13,.42)),
radial-gradient(circle at 78% 28%,#f5b477,#914c2d 48%,#352016);box-shadow:var(--shadow)}
.hero h1{font-size:clamp(48px,7vw,88px);line-height:.9;letter-spacing:-5px;margin:8px 0 20px}
.hero p{max-width:670px;font-size:19px;line-height:1.65}
.slogan{font-size:22px;font-weight:900;color:#ffd4ad}
.btn{display:inline-block;border:0;border-radius:999px;padding:13px 20px;background:var(--orange);
color:white;font-weight:850;cursor:pointer;box-shadow:0 8px 22px rgba(217,109,44,.22)}
.btn:hover{background:#bd531d}
.btn.dark{background:var(--brown)}
.btn.light{background:#fff;color:var(--brown)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:20px}
.card{background:var(--white);border:1px solid var(--line);border-radius:22px;padding:22px;box-shadow:var(--shadow)}
.food{padding:0;overflow:hidden}
.food img,.food-img{width:100%;height:215px;object-fit:cover;background:linear-gradient(135deg,#f4c28d,#844a2d)}
.food-body{padding:20px}
.price{font-size:23px;font-weight:950;color:var(--orange)}
.muted{color:#846c5b}
.tag{display:inline-block;background:#f6e4cf;border-radius:999px;padding:6px 10px;
font-size:12px;font-weight:850}
input,textarea,select{width:100%;padding:13px;border:1px solid #dfc9b2;border-radius:13px;
background:#fff;color:var(--brown);font:inherit}
textarea{min-height:130px;resize:vertical}
form{display:grid;gap:12px}
label{font-size:14px;font-weight:850}
.alert{padding:14px 16px;border-radius:14px;border:1px solid var(--line);background:#fff;margin-bottom:14px}
.alert.success{background:#eff7e9;border-color:#b9d4a8}
.alert.warning{background:#fff7e4;border-color:#e7c984}
.alert.error{background:#fff0ed;border-color:#e2aea4}
.title{display:flex;justify-content:space-between;align-items:end;gap:20px;margin:42px 0 18px}
.title h2{margin:0;font-size:32px}
.rating{color:#c8681d;font-weight:900}
.post{border-left:4px solid var(--orange);margin-bottom:14px}
footer{text-align:center;padding:38px;color:#846c5b}
@media(max-width:750px){.nav{flex-wrap:wrap}.links{width:100%;overflow:auto}.hero{padding:32px}.hero h1{font-size:54px}}
</style>
</head>
<body>
<nav><div class="nav">
<a class="brand" href="{{url_for('home')}}">Home <span>Eats</span></a>
<div class="links">
<a href="{{url_for('browse')}}">Find Food</a>
<a href="{{url_for('forum')}}">Food Forum</a>
{% if user %}
<a href="{{url_for('dashboard')}}">My Home</a>
<a href="{{url_for('sell')}}">Sell Food</a>
<a href="{{url_for('logout')}}">Log out</a>
{% else %}
<a href="{{url_for('login')}}">Log in</a>
<a class="btn" href="{{url_for('register')}}">Join</a>
{% endif %}
</div>
</div></nav>
<div class="wrap">
{% with messages=get_flashed_messages(with_categories=true) %}
{% for category,message in messages %}<div class="alert {{category}}">{{message}}</div>{% endfor %}
{% endwith %}
{{body|safe}}
</div>
<footer><strong>Home Eats</strong> · Food made from home 🏡🍲<br>
Made for local cooks, food lovers and real community.</footer>
</body>
</html>
"""


def page(title, template, **context):
    return render_template_string(
        BASE,
        title=title,
        body=render_template_string(template, **context),
        user=current_user(),
    )


# ============================================================
# Home / food marketplace
# ============================================================

@app.route("/")
def home():
    conn = sql_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT TOP 9 f.id,f.title,f.description,f.category,f.price,f.servings,
               f.collection_area,f.image_url,u.name
        FROM food_listings f
        JOIN users u ON u.id=f.seller_id
        WHERE f.available=1
        ORDER BY f.created_at DESC
    """)
    foods = cur.fetchall()
    conn.close()

    return page("Food made from home", """
<section class="hero">
<div>
<div class="slogan">Food made from home.</div>
<h1>Good food.<br>Real homes.<br>Real people.</h1>
<p>Discover food cooked by people in your community. Order from a real home kitchen,
then collect your meal and share your experience with the community.</p>
<a class="btn light" href="{{url_for('browse')}}">Find something delicious →</a>
</div>
</section>

<div class="title"><div><h2>Fresh from home kitchens 🍛</h2>
<div class="muted">No big chains. Just people making food they love.</div></div></div>

<div class="grid">
{% for f in foods %}
<article class="card food">
{% if f[7] %}<img src="{{f[7]}}" alt="{{f[1]}}">{% else %}<div class="food-img"></div>{% endif %}
<div class="food-body">
<span class="tag">{{f[3]}}</span><h3>{{f[1]}}</h3>
<p class="muted">{{f[2][:140]}}</p>
<div class="price">€{{"%.2f"|format(f[4])}}</div>
<p class="muted">Cooked by {{f[8]}} · {{f[5]}} portions · 📍 {{f[6]}}</p>
<a class="btn" href="{{url_for('listing',listing_id=f[0])}}">See the plate →</a>
</div></article>
{% else %}
<div class="card"><h3>The kitchen is warming up 👨‍🍳</h3>
<p>Be the first person to list a home-cooked dish.</p></div>
{% endfor %}
</div>
""", foods=foods)


@app.route("/browse")
def browse():
    category = request.args.get("category", "").strip()
    conn = sql_connection()
    cur = conn.cursor()

    if category:
        cur.execute("""
            SELECT f.id,f.title,f.description,f.category,f.price,f.servings,
                   f.collection_area,f.image_url,u.name
            FROM food_listings f JOIN users u ON u.id=f.seller_id
            WHERE f.available=1 AND f.category=?
            ORDER BY f.created_at DESC
        """, category)
    else:
        cur.execute("""
            SELECT f.id,f.title,f.description,f.category,f.price,f.servings,
                   f.collection_area,f.image_url,u.name
            FROM food_listings f JOIN users u ON u.id=f.seller_id
            WHERE f.available=1 ORDER BY f.created_at DESC
        """)

    foods = cur.fetchall()
    conn.close()

    return page("Find food", """
<div class="title"><div><h2>Find your next home-cooked meal 🍽️</h2>
<div class="muted">Food from local kitchens, not factory kitchens.</div></div></div>
<div class="grid">
{% for f in foods %}
<article class="card food">
{% if f[7] %}<img src="{{f[7]}}" alt="{{f[1]}}">{% else %}<div class="food-img"></div>{% endif %}
<div class="food-body"><span class="tag">{{f[3]}}</span>
<h3>{{f[1]}}</h3><p>{{f[2]}}</p><div class="price">€{{"%.2f"|format(f[4])}}</div>
<p class="muted">Cooked by {{f[8]}} · 📍 {{f[6]}}</p>
<a class="btn" href="{{url_for('listing',listing_id=f[0])}}">View meal</a>
</div></article>
{% else %}<div class="card"><h3>No meals yet.</h3><p>Try again later or become a home cook.</p></div>{% endfor %}
</div>
""", foods=foods)


@app.route("/food/<int:listing_id>")
def listing(listing_id):
    conn = sql_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT f.id,f.title,f.description,f.category,f.price,f.servings,
               f.collection_area,f.image_url,f.seller_id,u.name
        FROM food_listings f JOIN users u ON u.id=f.seller_id WHERE f.id=?
    """, listing_id)
    food = cur.fetchone()

    if not food:
        conn.close()
        abort(404)

    cur.execute("""
        SELECT AVG(CAST(stars AS FLOAT)),COUNT(*) FROM ratings WHERE listing_id=?
    """, listing_id)
    rating = cur.fetchone()
    conn.close()

    return page(food[1], """
<div class="card">
{% if food[7] %}<img class="food-img" src="{{food[7]}}" alt="{{food[1]}}">{% endif %}
<span class="tag">{{food[3]}}</span>
<h1>{{food[1]}}</h1>
<p style="font-size:18px;line-height:1.7">{{food[2]}}</p>
<div class="price">€{{"%.2f"|format(food[4])}}</div>
<p>🍽️ {{food[5]}} portions · 👨‍🍳 {{food[9]}} · 📍 {{food[6]}}</p>
<p class="muted"><strong>Safety:</strong> the exact collection address is only revealed after
a reservation is confirmed. Never publish private access details publicly.</p>
{% if rating[1] %}<p class="rating">★ {{ "%.1f"|format(rating[0]) }} · {{rating[1]}} rating(s)</p>
{% else %}<p class="muted">No ratings yet.</p>{% endif %}
{% if user %}
<form method="post" action="{{url_for('buy',listing_id=food[0])}}" style="max-width:430px">
<label>Portions</label><input type="number" name="quantity" min="1" max="{{food[5]}}" value="1" required>
<button class="btn" type="submit">Reserve this meal 🍲</button>
</form>
{% else %}<a class="btn" href="{{url_for('login')}}">Log in to order</a>{% endif %}
</div>
""", food=food, rating=rating)


# ============================================================
# Auth
# ============================================================

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        name=request.form.get("name","").strip()
        email=request.form.get("email","").strip().lower()
        password=request.form.get("password","")

        if not name or not email or len(password)<8:
            flash("Use your name, a valid email and a password of at least 8 characters.","warning")
            return redirect(url_for("register"))

        try:
            conn=sql_connection(); cur=conn.cursor()
            cur.execute(
                "INSERT INTO users(name,email,password_hash) VALUES(?,?,?)",
                name,email,password_hash(password)
            )
            conn.commit()
            cur.execute("SELECT id FROM users WHERE email=?",email)
            user_id=cur.fetchone()[0]
            conn.close()

            session["user_id"]=user_id
            emit_event("homeeats.user.registered",user_id,"user",user_id,{"email":email})
            flash("Welcome to Home Eats! 🏡🍲","success")
            return redirect(url_for("dashboard"))
        except Exception:
            log.exception("Registration failed.")
            flash("That email may already exist or Azure SQL is unavailable.","error")

    return page("Join Home Eats", """
<div class="card" style="max-width:560px;margin:30px auto">
<h1>Welcome to Home Eats 🏡</h1><p class="muted">Food made from home.</p>
<form method="post">
<label>Name</label><input name="name" required>
<label>Email</label><input type="email" name="email" required>
<label>Password</label><input type="password" name="password" minlength="8" required>
<button class="btn">Create account</button>
</form></div>
""")


@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        email=request.form.get("email","").strip().lower()
        password=request.form.get("password","")

        try:
            conn=sql_connection(); cur=conn.cursor()
            cur.execute("SELECT id,password_hash FROM users WHERE email=?",email)
            row=cur.fetchone(); conn.close()

            if row and password_matches(password,row[1]):
                session["user_id"]=row[0]
                emit_event("homeeats.user.logged_in",row[0],"user",row[0],{})
                return redirect(request.args.get("next") or url_for("dashboard"))
        except Exception:
            log.exception("Login failed.")

        flash("Email or password was incorrect.","error")

    return page("Log in", """
<div class="card" style="max-width:520px;margin:30px auto">
<h1>Welcome back 👋</h1><p class="muted">Your next home-cooked meal is waiting.</p>
<form method="post">
<label>Email</label><input type="email" name="email" required>
<label>Password</label><input type="password" name="password" required>
<button class="btn">Log in</button>
</form></div>
""")


@app.route("/logout")
def logout():
    user=current_user()
    if user:
        emit_event("homeeats.user.logged_out",user["id"],"user",user["id"],{})
    session.clear()
    return redirect(url_for("home"))


# ============================================================
# Sell food + Blob Storage
# ============================================================

@app.route("/sell", methods=["GET","POST"])
@login_required
def sell():
    user=current_user()

    if request.method=="POST":
        title=request.form.get("title","").strip()
        description=request.form.get("description","").strip()
        category=request.form.get("category","Other").strip()
        area=request.form.get("area","").strip()
        address=request.form.get("address","").strip()

        try:
            price=float(request.form.get("price","0"))
            servings=int(request.form.get("servings","0"))
        except ValueError:
            price=0; servings=0

        if not title or not description or not area or not address or price<=0 or servings<=0:
            flash("Complete all meal details.","warning")
            return redirect(url_for("sell"))

        image_url=None
        image=request.files.get("image")

        if image and image.filename:
            blob_name=f"food/{user['id']}/{uuid.uuid4()}-{image.filename.rsplit('/',1)[-1]}"
            try:
                container=get_blob_container()
                blob=container.get_blob_client(blob_name)
                blob.upload_blob(
                    image,
                    overwrite=False,
                    content_settings=ContentSettings(
                        content_type=image.content_type or "image/jpeg"
                    )
                )
                image_url=blob.url
            except Exception:
                log.exception("Food image upload failed.")
                flash("Image upload failed.","error")
                return redirect(url_for("sell"))

        conn=sql_connection(); cur=conn.cursor()
        cur.execute("""
            INSERT INTO food_listings
            (seller_id,title,description,category,price,servings,
             collection_area,collection_address,image_url)
            VALUES(?,?,?,?,?,?,?,?,?)
        """,user["id"],title,description,category,price,servings,area,address,image_url)
        conn.commit()
        cur.execute("""
            SELECT TOP 1 id FROM food_listings
            WHERE seller_id=? ORDER BY id DESC
        """,user["id"])
        listing_id=cur.fetchone()[0]
        conn.close()

        emit_event(
            "homeeats.food.listed",user["id"],"food_listing",listing_id,
            {"title":title,"category":category,"price":price}
        )
        flash("Your dish is live on Home Eats! 🍲","success")
        return redirect(url_for("listing",listing_id=listing_id))

    return page("Sell food", """
<div class="card" style="max-width:760px;margin:auto">
<h1>Put your home cooking on the table 🏡</h1>
<p class="muted">Show people the food, while keeping your private address hidden until an order is confirmed.</p>
<form method="post" enctype="multipart/form-data">
<label>Dish name</label><input name="title" placeholder="Nigerian Jollof & Chicken" required>
<label>Description</label><textarea name="description" placeholder="Tell people what makes it special..." required></textarea>
<label>Category</label><select name="category">
<option>Nigerian</option><option>Caribbean</option><option>Indian</option>
<option>Italian</option><option>Asian</option><option>Bakery</option>
<option>Dessert</option><option>Other</option></select>
<label>Collection area</label><input name="area" placeholder="e.g. Galway City" required>
<label>Private collection address</label><input name="address" required>
<label>Price per portion (€)</label><input type="number" name="price" min="0.50" step="0.01" required>
<label>Available portions</label><input type="number" name="servings" min="1" required>
<label>Food photo</label><input type="file" name="image" accept="image/*">
<button class="btn">Publish my dish 🍛</button>
</form></div>
""")


# ============================================================
# Orders / ratings
# ============================================================

@app.route("/buy/<int:listing_id>", methods=["POST"])
@login_required
def buy(listing_id):
    user=current_user()

    try:
        quantity=int(request.form.get("quantity","1"))
    except ValueError:
        quantity=1

    conn=sql_connection(); cur=conn.cursor()
    cur.execute("""
        SELECT id,seller_id,title,price,servings,collection_address
        FROM food_listings WHERE id=? AND available=1
    """,listing_id)
    food=cur.fetchone()

    if not food:
        conn.close(); abort(404)

    if food[1]==user["id"] or quantity<1 or quantity>food[4]:
        conn.close()
        flash("That order cannot be completed.","warning")
        return redirect(url_for("listing",listing_id=listing_id))

    total=float(food[3])*quantity
    code=secrets.token_urlsafe(10)

    # Prototype reservation. Real payments need a payment provider/webhook.
    cur.execute("""
        INSERT INTO orders
        (buyer_id,listing_id,quantity,total,status,collection_code)
        VALUES(?,?,?,?,'reserved',?)
    """,user["id"],listing_id,quantity,total,code)

    cur.execute(
        "UPDATE food_listings SET servings=servings-? WHERE id=?",
        quantity,listing_id
    )
    conn.commit()

    cur.execute("SELECT TOP 1 id FROM orders WHERE buyer_id=? ORDER BY id DESC",user["id"])
    order_id=cur.fetchone()[0]
    conn.close()

    emit_event(
        "homeeats.order.reserved",user["id"],"order",order_id,
        {"listing_id":listing_id,"quantity":quantity,"total":total}
    )

    flash(
        "Reservation created. For a production version, connect this step to a real payment provider before treating it as paid.",
        "success"
    )
    return redirect(url_for("dashboard"))


@app.route("/order/<int:order_id>/collected", methods=["POST"])
@login_required
def collected(order_id):
    user=current_user()
    conn=sql_connection(); cur=conn.cursor()
    cur.execute("""
        SELECT id,buyer_id,listing_id,status FROM orders WHERE id=?
    """,order_id)
    order=cur.fetchone()

    if not order or order[1]!=user["id"]:
        conn.close(); abort(403)

    cur.execute("UPDATE orders SET status='collected' WHERE id=?",order_id)
    conn.commit(); conn.close()

    emit_event("homeeats.order.collected",user["id"],"order",order_id,{"listing_id":order[2]})
    flash("Enjoy your food! 🍽️","success")
    return redirect(url_for("dashboard"))


@app.route("/order/<int:order_id>/rate", methods=["POST"])
@login_required
def rate(order_id):
    user=current_user()
    stars=max(1,min(5,int(request.form.get("stars","5"))))
    review=request.form.get("review","").strip()

    conn=sql_connection(); cur=conn.cursor()
    cur.execute("""
        SELECT id,buyer_id,listing_id,status FROM orders WHERE id=?
    """,order_id)
    order=cur.fetchone()

    if not order or order[1]!=user["id"] or order[3]!="collected":
        conn.close(); abort(403)

    try:
        cur.execute("""
            INSERT INTO ratings(order_id,buyer_id,listing_id,stars,review)
            VALUES(?,?,?,?,?)
        """,order_id,user["id"],order[2],stars,review)
        conn.commit()
    except Exception:
        conn.rollback(); conn.close()
        flash("You may have already rated this meal.","warning")
        return redirect(url_for("dashboard"))

    conn.close()
    emit_event(
        "homeeats.food.rated",user["id"],"order",order_id,
        {"listing_id":order[2],"stars":stars}
    )
    flash("Thanks for rating your meal! ⭐","success")
    return redirect(url_for("dashboard"))


# ============================================================
# Dashboard
# ============================================================

@app.route("/dashboard")
@login_required
def dashboard():
    user=current_user()
    conn=sql_connection(); cur=conn.cursor()

    cur.execute("""
        SELECT o.id,f.title,o.quantity,o.total,o.status,o.collection_code,
               f.collection_address
        FROM orders o JOIN food_listings f ON f.id=o.listing_id
        WHERE o.buyer_id=? ORDER BY o.created_at DESC
    """,user["id"])
    orders=cur.fetchall()

    cur.execute("""
        SELECT id,title,category,price,servings,available
        FROM food_listings WHERE seller_id=? ORDER BY created_at DESC
    """,user["id"])
    listings=cur.fetchall()
    conn.close()

    return page("My Home", """
<div class="title"><div><h2>Welcome home, {{user.name}} 🏡</h2>
<div class="muted">Your orders and home kitchen.</div></div></div>

<div class="grid">
<div class="card"><h2>{{orders|length}}</h2><div class="muted">Orders</div></div>
<div class="card"><h2>{{listings|length}}</h2><div class="muted">Your dishes</div></div>
<div class="card"><h2>🍲</h2><div class="muted">Food made from home</div></div>
</div>

<div class="title"><h2>Your orders</h2></div>
{% for o in orders %}
<div class="card" style="margin-bottom:14px">
<h3>{{o[1]}}</h3><p>{{o[2]}} portion(s) · €{{"%.2f"|format(o[3])}}</p>
<span class="tag">{{o[4]}}</span>
{% if o[4] == "reserved" %}
<p><strong>Collection address:</strong> {{o[6]}}</p>
<p>Collection code: <strong>{{o[5]}}</strong></p>
<p class="muted">Only show this address/code to the buyer. Meet safely and follow local food-safety rules.</p>
<form method="post" action="{{url_for('collected',order_id=o[0])}}">
<button class="btn">I've collected the food 🍽️</button></form>
{% elif o[4] == "collected" %}
<form method="post" action="{{url_for('rate',order_id=o[0])}}">
<label>Rate your meal</label>
<select name="stars"><option value="5">★★★★★ 5</option><option value="4">★★★★ 4</option>
<option value="3">★★★ 3</option><option value="2">★★ 2</option><option value="1">★ 1</option></select>
<textarea name="review" placeholder="Tell the cook what you thought..."></textarea>
<button class="btn">Post rating</button></form>
{% endif %}
</div>
{% else %}
<div class="card"><p>No orders yet. Hungry?</p><a class="btn" href="{{url_for('browse')}}">Find food</a></div>
{% endfor %}

<div class="title"><h2>Your kitchen</h2><a class="btn" href="{{url_for('sell')}}">+ List food</a></div>
{% for f in listings %}
<div class="card" style="margin-bottom:12px"><h3>{{f[1]}}</h3>
<span class="tag">{{f[2]}}</span><p>€{{"%.2f"|format(f[3])}} · {{f[4]}} portions left</p></div>
{% else %}
<div class="card"><p>You haven't listed anything yet.</p></div>
{% endfor %}
""",user=user,orders=orders,listings=listings)


# ============================================================
# Reddit-style food forum
# ============================================================

@app.route("/forum")
def forum():
    conn=sql_connection(); cur=conn.cursor()
    cur.execute("""
        SELECT p.id,p.title,p.body,p.category,p.created_at,u.name,
               (SELECT COUNT(*) FROM forum_comments c WHERE c.post_id=p.id)
        FROM forum_posts p JOIN users u ON u.id=p.author_id
        ORDER BY p.created_at DESC
    """)
    posts=cur.fetchall(); conn.close()

    return page("Food Forum", """
<div class="title"><div><h2>Food Forum 💬</h2>
<div class="muted">Talk food. Rate food. Share recipes. Find cooks.</div></div>
{% if user %}<a class="btn" href="{{url_for('new_post')}}">Start a discussion</a>{% endif %}</div>

{% for p in posts %}
<article class="card post"><span class="tag">{{p[3]}}</span>
<h2><a href="{{url_for('post',post_id=p[0])}}">{{p[1]}}</a></h2>
<p>{{p[2][:260]}}</p><div class="muted">Posted by {{p[5]}} · {{p[6]}} comments</div>
</article>
{% else %}<div class="card"><h3>The table is quiet...</h3>
<p>Start the first food conversation.</p></div>{% endfor %}
""",posts=posts)


@app.route("/forum/new",methods=["GET","POST"])
@login_required
def new_post():
    user=current_user()

    if request.method=="POST":
        title=request.form.get("title","").strip()
        body=request.form.get("body","").strip()
        category=request.form.get("category","General").strip()

        if not title or not body:
            flash("Add a title and message.","warning")
            return redirect(url_for("new_post"))

        conn=sql_connection(); cur=conn.cursor()
        cur.execute("""
            INSERT INTO forum_posts(author_id,title,body,category)
            VALUES(?,?,?,?)
        """,user["id"],title,body,category)
        conn.commit()
        cur.execute("SELECT TOP 1 id FROM forum_posts WHERE author_id=? ORDER BY id DESC",user["id"])
        post_id=cur.fetchone()[0]
        conn.close()

        emit_event("homeeats.forum.post_created",user["id"],"forum_post",post_id,{"category":category})
        return redirect(url_for("post",post_id=post_id))

    return page("New discussion", """
<div class="card" style="max-width:760px;margin:auto">
<h1>Start a food conversation 🍴</h1>
<form method="post"><label>Title</label><input name="title" required>
<label>Category</label><select name="category"><option>General</option><option>Recipes</option>
<option>Reviews</option><option>Home cooks</option><option>Food safety</option>
<option>Recommendations</option></select>
<label>Post</label><textarea name="body" required></textarea>
<button class="btn">Post to the table</button></form></div>
""")


@app.route("/forum/<int:post_id>")
def post(post_id):
    conn=sql_connection(); cur=conn.cursor()
    cur.execute("""
        SELECT p.id,p.title,p.body,p.category,p.created_at,u.name
        FROM forum_posts p JOIN users u ON u.id=p.author_id WHERE p.id=?
    """,post_id)
    p=cur.fetchone()

    if not p:
        conn.close(); abort(404)

    cur.execute("""
        SELECT c.body,c.created_at,u.name FROM forum_comments c
        JOIN users u ON u.id=c.author_id WHERE c.post_id=? ORDER BY c.created_at
    """,post_id)
    comments=cur.fetchall(); conn.close()

    return page(p[1], """
<article class="card"><span class="tag">{{p[3]}}</span><h1>{{p[1]}}</h1>
<p style="white-space:pre-wrap;font-size:18px;line-height:1.7">{{p[2]}}</p>
<div class="muted">Posted by {{p[5]}}</div></article>

<div class="title"><h2>Comments</h2></div>
{% for c in comments %}<div class="card" style="margin-bottom:12px">
<p style="white-space:pre-wrap">{{c[0]}}</p><div class="muted">{{c[2]}}</div></div>
{% else %}<div class="card">No comments yet.</div>{% endfor %}

{% if user %}<div class="card" style="margin-top:20px"><form method="post"
action="{{url_for('comment',post_id=p[0])}}"><label>Add your voice</label>
<textarea name="body" required></textarea><button class="btn">Comment</button></form></div>{% endif %}
""",p=p,comments=comments)


@app.route("/forum/<int:post_id>/comment",methods=["POST"])
@login_required
def comment(post_id):
    user=current_user()
    body=request.form.get("body","").strip()

    if not body:
        return redirect(url_for("post",post_id=post_id))

    conn=sql_connection(); cur=conn.cursor()
    cur.execute("INSERT INTO forum_comments(post_id,author_id,body) VALUES(?,?,?)",
                post_id,user["id"],body)
    conn.commit(); conn.close()

    emit_event("homeeats.forum.comment_created",user["id"],"forum_post",post_id,{})
    return redirect(url_for("post",post_id=post_id))


# ============================================================
# Health / Azure observability
# ============================================================

@app.route("/health")
def health():
    result={
        "application":"Home Eats",
        "slogan":"Food made from home",
        "status":"ok",
        "timestamp":datetime.now(timezone.utc).isoformat(),
        "azure_app_service":bool(os.getenv("WEBSITE_SITE_NAME")),
        "storage_account":STORAGE_ACCOUNT,
        "sql_database":SQL_DATABASE,
        "event_grid_configured":bool(EVENT_GRID_ENDPOINT),
    }

    try:
        conn=sql_connection(); conn.close()
        result["sql"]="connected"
    except Exception:
        result["sql"]="unavailable"
        result["status"]="degraded"

    return jsonify(result)


@app.route("/events")
@login_required
def my_events():
    user=current_user()
    conn=sql_connection(); cur=conn.cursor()
    cur.execute("""
        SELECT TOP 100 event_type,entity_type,entity_id,payload,created_at
        FROM application_events WHERE actor_id=? ORDER BY created_at DESC
    """,user["id"])
    rows=cur.fetchall(); conn.close()

    return page("My events", """
<div class="title"><h2>Home Eats event trail ⚡</h2></div>
<div class="card">
{% for e in rows %}
<div style="padding:14px 0;border-bottom:1px solid #ead7c0">
<strong>{{e[0]}}</strong> <span class="tag">{{e[1]}} {{e[2] or ""}}</span>
<pre style="white-space:pre-wrap">{{e[3]}}</pre>
<div class="muted">{{e[4]}}</div>
</div>
{% else %}<p>No events yet.</p>{% endfor %}
</div>
""",rows=rows)


# ============================================================
# Initialise database on first request
# ============================================================

@app.before_request
def initialise_once():
    if not getattr(app,"db_ready",False):
        try:
            init_database()
            app.db_ready=True
            log.info("Home Eats database is ready.")
        except Exception:
            log.exception("Azure SQL initialisation failed.")
            # Do not hide the real Azure error from App Service logs.


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT","8000")),
        debug=os.getenv("FLASK_DEBUG","false").lower()=="true",
    )
