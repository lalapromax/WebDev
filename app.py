import os
import re
import uuid
import atexit
from io import BytesIO
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlparse
from ngo_payouts import ngo_payouts


import certifi
import pyotp
import stripe
from authlib.integrations.flask_client import OAuth
from bson import ObjectId
from dotenv import load_dotenv
from gridfs import GridFS, NoFile
from flask import (
    Flask, g, render_template, request, redirect, url_for,
    flash, session, jsonify, abort, send_file,
)
from flask_mail import Mail, Message
from pymongo import MongoClient
from pymongo.server_api import ServerApi
from requests.exceptions import RequestException
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# Load .env FIRST so every os.environ / os.getenv below actually sees it
load_dotenv()

# STANDARDISE DNS TO 8.8.8.8 FOR DEBUGGING!! REMOVE LATER
import dns.resolver

dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
dns.resolver.default_resolver.nameservers = ['8.8.8.8', '1.1.1.1']


ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
DEFAULT_PROFILE_PIC = "/static/uploads/sample_profile.jpg"

# Proof files (parcel receipts, account-removal evidence)
PROOF_EXT = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}
PROOF_MAX_BYTES = 5 * 1024 * 1024

# Equipment donations: who carried the parcel, and where it is in its journey.
HAND_DELIVERY = "Hand delivery"
COURIERS = [
    "Pos Laju", "J&T Express", "DHL eCommerce", "Ninja Van", "Flash Express",
    "GDEX", "City-Link Express", "Other courier", HAND_DELIVERY,
]

MALAYSIA_STATES = [
    "Johor", "Kedah", "Kelantan", "Kuala Lumpur", "Labuan", "Malacca",
    "Negeri Sembilan", "Pahang", "Penang", "Perak", "Perlis",
    "Putrajaya", "Sabah", "Sarawak", "Selangor", "Terengganu",
]

app = Flask(__name__)
app.register_blueprint(ngo_payouts)
app.secret_key = os.environ.get('MED_SECRET', 'dev-secret-key')
app.config['SERVER_NAME'] = 'localhost:5000'
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

oauth = OAuth(app)

#Google OAuth Config
oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

# Server Information
MONGODB_URI = os.environ.get("MONGODB_URI")
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "app_db")

# Google Auth Information
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", None)
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", None)
GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"

# ---------------------------------------------------------------------------
# Stripe
# ---------------------------------------------------------------------------
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", None)
stripe.api_version = "2026-08-26.dahlia; custom_checkout_payment_form_preview=v1"
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", None)
STRIPE_WEBHOOK_SECRET = os.environ["STRIPE_WEBHOOK_SECRET"]

client = stripe.StripeClient(stripe.api_key)

DOMAIN = 'http://localhost:5000'

# ---------------------------------------------------------------------------
# Mail
# ---------------------------------------------------------------------------
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USE_SSL'] = False
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')

mail = Mail(app)

# ---------------------------------------------------------------------------
# MongoDB
# ---------------------------------------------------------------------------
ca = certifi.where()  # Secure connection

# Create the client ONCE, at startup -- not per request
app.mongo_client = MongoClient(
    MONGODB_URI,
    server_api=ServerApi(version="1", strict=True, deprecation_errors=True)
)

# Verify connection at startup
try:
    app.mongo_client.admin.command("ping")
    print("Connected to MongoDB successfully")
except Exception as e:
    raise Exception("Failed to connect to MongoDB:", e)

with app.app_context():
    def seed_admin():
        g.db = app.mongo_client["medonate"]
        # Insert default admin user if not present.
        admin_exists = g.db.users.find_one({"name": "admin"})

        if not admin_exists:
            hashed = generate_password_hash('admin123')

            g.db.users.insert_one({
                "email": "admin@gmail.com",
                "name": "admin",
                "password": hashed,
                "address": '123 Admin Lane',
                "phone_no": '0123456789',
                "profile-pic": DEFAULT_PROFILE_PIC,
                "role": "admin",
                "provider": "local",
                "profile_completed" : True

            })
            print("Default admin created")

        print("Admin account exists")

    seed_admin()

    app.mongo_client["medonate"].donations.create_index("campaign_id")
    app.mongo_client["medonate"].comments.create_index([("campaign_id", 1), ("date_posted", -1)])
    # Updates: who can see a notice, what a sender has sent, and the automatic clean-up of old events
    app.mongo_client["medonate"].updates.create_index("recipients")
    app.mongo_client["medonate"].updates.create_index([("audience.type", 1), ("created_at", -1)])
    app.mongo_client["medonate"].updates.create_index([("sender_id", 1), ("created_at", -1)])
    app.mongo_client["medonate"].updates.create_index("expires_at", expireAfterSeconds=0)

atexit.register(app.mongo_client.close)


@app.before_request
def attach_db():
    g.db = app.mongo_client["medonate"]

# @app.teardown_appcontext
# def teardown_db(exception):
#     db = g.pop('db', None)
#
#     if db is not None:
#         db.close()



def _image_content_type(filename):
    extension = os.path.splitext(filename)[1].lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(extension, "application/octet-stream")


def _save_uploaded_image(image):
    extension = os.path.splitext(secure_filename(image.filename))[1].lower()
    filename = f"{uuid.uuid4().hex}{extension}"
    image_id = GridFS(g.db, collection="media").put(
        image.stream,
        filename=filename,
        content_type=_image_content_type(filename),
    )
    return url_for("stored_image", file_id=str(image_id))


def _shared_image_url(image_url):
    if not image_url:
        return image_url

    parsed_url = urlparse(image_url)
    if not parsed_url.path.startswith("/static/uploads/"):
        return image_url

    filename = os.path.basename(parsed_url.path)
    upload_folder = os.path.realpath(os.path.join(app.root_path, "static", "uploads"))
    local_path = os.path.realpath(os.path.join(upload_folder, filename))
    if (
        not filename
        or os.path.commonpath((upload_folder, local_path)) != upload_folder
        or not os.path.isfile(local_path)
    ):
        return image_url

    with open(local_path, "rb") as image_file:
        image_id = GridFS(g.db, collection="media").put(
            image_file,
            filename=filename,
            content_type=_image_content_type(filename),
        )
    return url_for("stored_image", file_id=str(image_id))


def _campaign_image_urls(campaign):
    image_urls = campaign.get("image_urls") or (
        [campaign["image_url"]] if campaign.get("image_url") else []
    )
    shared_image_urls = [_shared_image_url(image_url) for image_url in image_urls]
    if shared_image_urls != image_urls:
        g.db.campaigns.update_one(
            {"_id": campaign["_id"]},
            {"$set": {
                "image_urls": shared_image_urls,
                "image_url": shared_image_urls[0] if shared_image_urls else None,
            }},
        )
        campaign["image_urls"] = shared_image_urls
        campaign["image_url"] = shared_image_urls[0] if shared_image_urls else None
    return shared_image_urls


def _user_profile_image(user):
    profile_image = _shared_image_url(user.get("profile-pic"))
    if profile_image != user.get("profile-pic"):
        g.db.users.update_one(
            {"_id": user["_id"]},
            {"$set": {"profile-pic": profile_image}},
        )
        user["profile-pic"] = profile_image
    return profile_image


def _delete_stored_image(image_url):
    if not image_url:
        return

    parsed_url = urlparse(image_url)
    if parsed_url.path.startswith("/media/"):
        file_id = parsed_url.path.rsplit("/", 1)[-1]
        if ObjectId.is_valid(file_id):
            GridFS(g.db, collection="media").delete(ObjectId(file_id))
    elif parsed_url.path.startswith("/static/uploads/"):
        filename = os.path.basename(parsed_url.path)
        upload_path = os.path.join(app.root_path, "static", "uploads", filename)
        if os.path.isfile(upload_path):
            os.remove(upload_path)


@app.route("/media/<file_id>")
def stored_image(file_id):
    if not ObjectId.is_valid(file_id):
        abort(404)

    try:
        image = GridFS(g.db, collection="media").get(ObjectId(file_id))
    except NoFile:
        abort(404)

    response = send_file(
        BytesIO(image.read()),
        mimetype=image.content_type or _image_content_type(image.filename),
        download_name=image.filename,
        max_age=86400,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response



# ---------------------------------------------------------------------------
# Proof files: parcel receipts and account-removal evidence.
# They live in their own GridFS bucket ("proofs") and are never served by the
# public /media route -- only by /proof/<id>, which checks who is asking.
# ---------------------------------------------------------------------------
def _sniff_proof_type(head):
    """Real file type from the first bytes, so a renamed .exe can't pass as a .jpg."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def _save_proof(upload, metadata):
    """Validate and store an uploaded proof. Returns (file_id, error_message)."""
    if not upload or not upload.filename:
        return None, "Please attach a proof file."

    extension = os.path.splitext(secure_filename(upload.filename))[1].lower()
    if extension not in PROOF_EXT:
        return None, "Proof must be a JPG, PNG, WebP or PDF file."

    data = upload.stream.read(PROOF_MAX_BYTES + 1)
    if len(data) > PROOF_MAX_BYTES:
        return None, "That proof file is too large. Keep it under 5 MB."

    content_type = _sniff_proof_type(data[:16])
    if content_type is None:
        return None, "That file doesn't look like a real image or PDF."

    file_id = GridFS(g.db, collection="proofs").put(
        data,
        filename=f"{uuid.uuid4().hex}{extension}",
        content_type=content_type,
        metadata=metadata,
    )
    return file_id, None


@app.route("/proof/<file_id>")
def stored_proof(file_id):
    viewer = session.get("user") or {}
    if not viewer or not ObjectId.is_valid(file_id):
        abort(404)

    try:
        proof = GridFS(g.db, collection="proofs").get(ObjectId(file_id))
    except NoFile:
        abort(404)

    meta = proof.metadata or {}
    allowed = viewer.get("role") == "admin"
    if not allowed and meta.get("kind") == "equipment":
        # the donor who sent the parcel, and the NGO that is waiting for it
        allowed = viewer.get("email") in (meta.get("donor_email"), meta.get("owner_email"))
    if not allowed:
        abort(404)  # 404, not 403: don't confirm that the file exists

    response = send_file(
        BytesIO(proof.read()),
        mimetype=proof.content_type,
        download_name=proof.filename,
        max_age=0,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.context_processor
def inject_base_template():
    """Which layout a page extends, by who is looking.
    Pages shared between roles use {% extends base_template %}."""
    viewer = session.get("user")
    if not viewer:
        name = "general_base.html"
    else:
        name = {"admin": "admin_base.html", "ngo": "ngo_base.html"}.get(viewer.get("role"), "base.html")
    return {"base_template": name}


def _account_removed(user):
    return bool(user) and user.get("account_status") == "removed"


REMOVED_ACCOUNT_MESSAGE = ("This account has been removed by an administrator. "
                           "Contact the MEDonate team if you think this is a mistake.")

# ---------------------------------------------------------------------------
# Basic pages
# ---------------------------------------------------------------------------
@app.route("/")
def index():

    bday = None
    if session.get("user"):

        user = g.db.users.find_one({"email": session['user']['email']})

        if user and user.get('profile_completed') is True:
            DOB = user['donor_details']['DOB']

            if DOB and datetime.now().strftime("%Y-%m-%d") == DOB:
                if not session.get("birthday_shown"):
                    flash("Happy Birthday!", "popup")
                    session['birthday_shown'] = True
                    bday = True

    return render_template("index.html", bday=bday)


@app.route("/contact", methods=["GET",'POST'])
def contact():

    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        phone_no = request.form.get("phone_no")
        ngo = request.form.get("ngo")

        g.db.contact.insert_one({
            "full_name" : name,
            "email" : email,
            "phone_no": phone_no,
            "ngo_name" : ngo
        })
        flash("Thank you for contacting us. We will reply soon.", "popup")
        return redirect(url_for("contact"))

    return render_template("contact.html")

@app.route("/admin_analytics")
def admin_analytics():
    return render_template("admin_analytics.html")

def _campaign_is_completed(campaign):
    """Done once it is marked completed, or the goal has been reached."""
    goal = int(campaign.get("amount_cents") or 0)
    raised = int(campaign.get("current_amount") or 0)
    return campaign.get("status") == "completed" or (goal > 0 and raised >= goal)


def _fundraiser_tags(progress_percent, created_at):
    """Small coloured labels on a fundraiser card, at most two."""
    tags = []
    if created_at:
        created = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - created <= timedelta(days=7):
            tags.append({"label": "New", "kind": "new"})
    if progress_percent >= 80:
        tags.append({"label": "Almost there", "kind": "almost"})
    elif progress_percent >= 50:
        tags.append({"label": "Halfway", "kind": "halfway"})
    return tags

@app.route("/campaign")
def campaign():
    campaigns = list(g.db.campaigns.find(
        {"confirmed": {"$ne": False}, "owner_removed": {"$ne": True}},
        {
            "_id": 1,
            "title": 1,
            "amount_cents": 1,
            "current_amount": 1,
            "owner_email": 1,
            "image_urls": 1,
            "image_url": 1,
            "keywords": 1,
            "status": 1,
            "created_at": 1,
        },
    ).sort("created_at", -1))
    owner_emails = {
        campaign.get("owner_email")
        for campaign in campaigns
        if campaign.get("owner_email")
    }
    owners = {
        owner["email"]: owner
        for owner in g.db.users.find(
            {"email": {"$in": list(owner_emails)}},
            {"email": 1, "name": 1, "profile-pic": 1},
        )
    }
    for owner in owners.values():
        _user_profile_image(owner)

    fundraisers = []
    for campaign in campaigns:
        image_urls = _campaign_image_urls(campaign)
        owner_email = campaign.get("owner_email", "")
        owner = g.db.users.find_one({"email": owner_email})
        organizer = owner['ngo_details']['ngo_name']
        target_cents = max(0, int(campaign.get("amount_cents", 0) or 0))
        raised_cents = max(0, int(campaign.get("current_amount", 0) or 0))  # after
        progress_percent = raised_cents / target_cents * 100 if target_cents else 0
        is_completed = _campaign_is_completed(campaign)
        fundraisers.append({
            "id": str(campaign["_id"]),
            "title": campaign.get("title") or "Untitled fundraiser",
            "organizer": organizer,
            "organizer_initial": organizer[:1].upper(),
            "organizer_image": owner.get("profile-pic"),
            "goal": target_cents / 100,
            "progress_percent": progress_percent,
            "image_url": image_urls[0] if image_urls else None,
            "keywords": campaign.get("keywords") or [],
            "status": "completed" if is_completed else campaign.get("status"),
            "created_at": campaign.get("created_at"),
            "tags": (
                [{"label": "Completed", "kind": "completed"}] if is_completed
                else _fundraiser_tags(progress_percent, campaign.get("created_at"))
            ),
        })

    keyword_stats = {}
    for fundraiser in fundraisers:
        for keyword in fundraiser["keywords"]:
            normalized_keyword = keyword.strip().casefold()
            if not normalized_keyword:
                continue
            keyword_stat = keyword_stats.setdefault(
                normalized_keyword,
                {"label": keyword.strip(), "count": 0},
            )
            keyword_stat["count"] += 1
    popular_keywords = sorted(
        (
            {"label": stat["label"], "value": normalized_keyword}
            for normalized_keyword, stat in keyword_stats.items()
        ),
        key=lambda keyword: (
            -keyword_stats[keyword["value"]]["count"],
            keyword["label"].casefold(),
        ),
    )[:8]

    return render_template(
        "campaign.html",
        listing_mode=True,
        fundraisers=fundraisers,
        active_count=sum(1 for f in fundraisers if f['status'] == 'active'),
        recent_fundraisers=[f for f in fundraisers if f["status"] != "completed"][:5],
        popular_keywords=popular_keywords,
    )

@app.route("/our_partners")
def legacy_our_partners():
    return redirect(url_for("campaign"))


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if session.get('user'):
        return redirect(url_for('index'))

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        name = request.form.get("name")
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        # Validation
        if not email or not name or not password:
            flash("Name, email, and password are required.", "popup")
            return redirect(url_for("register"))

        if password != confirm_password:
            flash("Passwords do not match.", "popup")
            return redirect(url_for("register"))

        weak = _password_problem(password)
        if weak:
            flash(weak, "popup")
            return redirect(url_for("register"))

        users = g.db.users

        # Check if existing user
        existing_user = users.find_one({"email": email})
        if existing_user:
            flash("An account with this email already exists.", "popup")
            return redirect(url_for("register"))

        users.insert_one({
            "email": email,
            "name": name,
            "password": generate_password_hash(password),
            "address": None,
            "phone_no": None,
            "profile-pic": None,
            "role": "donor",
            "provider": "local",
            "profile_completed": False,

            "donor_details": {
                "DOB": None,
                "state": None,
                "membership_streak": datetime.now(),
                "tier": "Bronze",
                "total_cents": 0
            }
        })

        user = users.find_one({"email": email})

        g.db.audit_logs.insert_one({
            "user_id": user['_id'],
            "name": user['email'],
            "role": user['role'],
            "action": "create",
            "changes": f"{user['name']} account created.",
            "timestamp": datetime.now()
        })

        user = users.find_one({"email": email})

        g.db.audit_logs.insert_one({
            "user_id": user['_id'],
            "name": user['email'],
            "role": user['role'],
            "action": "create",
            "changes": f"{user['name']} account created.",
            "timestamp": datetime.now()
        })

        flash("Registration successful! Please log in.", "popup")
        return redirect(url_for("index"))

    return render_template("register.html")

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def _password_problem(password):
    """Why this password is too weak, or None when it is fine."""
    if len(password) < 8:
        return "Use at least 8 characters for the password."
    if not any(ch.isalpha() for ch in password) or all(ch.isalpha() for ch in password):
        return "Mix letters with at least one number or symbol."
    return None


@app.route("/register-ngo", methods=["GET", "POST"])
def register_ngo():
    # Role is stored inside session['user'], not at the top level of the session
    if (session.get('user') or {}).get('role') != 'admin':
        return redirect(url_for('index'))

    def show_form(message=None):
        # Keep what the admin typed (never the passwords) so one typo doesn't wipe the form
        if message:
            flash(message, "popup")
        return render_template("register-ngo.html", form=request.form)

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        ngo_name = request.form.get("ngo_name", "").strip()
        instagram = request.form.get("instagram", "").strip() or None
        facebook = request.form.get("facebook", "").strip() or None
        tiktok = request.form.get("tiktok", "").strip() or None
        description = request.form.get("description", "").strip() or None

        # Validation
        if not email or not name or not password or not ngo_name:
            return show_form("Contact name, NGO name, email and password are required.")

        if not EMAIL_PATTERN.match(email):
            return show_form("Enter a valid email address.")

        if password != confirm_password:
            return show_form("Passwords do not match.")

        weak = _password_problem(password)
        if weak:
            return show_form(weak)

        users = g.db.users

        # Check if existing user (emails are compared ignoring case here)
        existing_user = users.find_one({
            "email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}
        })
        if existing_user:
            return show_form("An account with this email already exists.")

        admin_user = users.find_one({"email": session["user"]["email"]})

        created = users.insert_one({
            "email": email,
            "name": name,
            "password": generate_password_hash(password),
            "role": "ngo",
            "address": None,
            "phone_no": None,
            "profile-pic": None,
            "provider": "local",
            "profile_completed": False,
            "account_status": "active",
            "created_at": datetime.now(),
            "created_by": session["user"]["email"],

            'ngo_details': {
                "ngo_name": ngo_name,
                "instagram": instagram,
                "facebook": facebook,
                "tiktok": tiktok,
                "description": description,
                "address": None,
                "email": None,
                "phone_no" : None,
                "registration_no": None,
                "profile_completed": False
            }
        })

        g.db.audit_logs.insert_one({
            "user_id": admin_user["_id"] if admin_user else None,
            "name": session["user"]["email"],
            "role": "admin",
            "action": "register-ngo",
            "changes": f"Registered the NGO account {ngo_name} ({email}).",
            "target_user_id": created.inserted_id,
            "target_email": email,
            "timestamp": datetime.now()
        })

        recipient = g.db.users.find_one({"email": email})

        # Send email
        subject = "MEDonate - Successful NGO Registration"
        body = (
            f"""
Hello {recipient['name']},

Your NGO has been successfully registered into the MEDonate system. Here are your default credentials:

Email: {email}
Password: {password}

Please reset your password upon login.

MEDonate
                """)

        try:
            msg = Message(
                subject=subject,
                sender=app.config['MAIL_USERNAME'],
                recipients=[recipient['email']]
            )

            msg.body = body

            mail.send(msg)

        except Exception as e:
            print("Email error:", e)
            flash(f"Unable to register NGO account.", "popup")
            return redirect(url_for("register_ngo"))

        flash(f"{ngo_name} is successfully registered.", "popup")
        return redirect(url_for("admin_dashboard"))

    return render_template('register-ngo.html', form={})

# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------
def _youtube_embed_url(youtube_url):
    if not youtube_url:
        return None

    parsed_url = urlparse(youtube_url)
    hostname = (parsed_url.hostname or "").lower()
    video_id = ""

    if hostname == "youtu.be":
        video_id = parsed_url.path.strip("/").split("/")[0]
    elif hostname in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        video_id = parse_qs(parsed_url.query).get("v", [""])[0]
        if not video_id:
            path_parts = parsed_url.path.strip("/").split("/")
            if len(path_parts) > 1 and path_parts[0] in {"embed", "shorts", "live"}:
                video_id = path_parts[1]

    if len(video_id) != 11 or not all(char.isalnum() or char in "-_" for char in video_id):
        return None

    return f"https://www.youtube-nocookie.com/embed/{video_id}"

# -----Create Campaign-----
MAX_EQUIPMENT_ITEMS = 20
MAX_ITEM_NAME_LENGTH = 100
MAX_ITEM_QUANTITY = 500
MAX_CAMPAIGN_UPLOAD_BYTES = 30 * 1024 * 1024

def _parse_equipment_items(form):
    """Returns (items, error). items = [{"name": str, "quantity": int}, ...]"""
    names = form.getlist("neededItemName")
    quantities = form.getlist("neededItemQuantity")

    items = []
    seen = set()
    for index, raw_name in enumerate(names):
        name = " ".join(raw_name.split())
        if not name:
            continue

        if len(name) > MAX_ITEM_NAME_LENGTH:
            return [], f"Item names can be up to {MAX_ITEM_NAME_LENGTH} characters."

        raw_quantity = quantities[index].strip() if index < len(quantities) else ""
        if not (raw_quantity.isascii() and raw_quantity.isdigit()):
            return [], f'Enter a whole-number quantity for "{name}".'
        quantity = int(raw_quantity)
        if not 1 <= quantity <= MAX_ITEM_QUANTITY:
            return [], f'The quantity for "{name}" must be between 1 and {MAX_ITEM_QUANTITY}.'

        key = name.casefold()
        if key in seen:
            return [], f'"{name}" is listed more than once. Combine it into one item.'
        seen.add(key)
        items.append({"name": name, "quantity": quantity})

    if len(items) > MAX_EQUIPMENT_ITEMS:
        return [], f"List up to {MAX_EQUIPMENT_ITEMS} equipment items."
    return items, None

def _equipment_rows(campaign):
    return [
        {"name": _item_name(entry), "quantity": _item_quantity(entry)}
        for entry in campaign.get("needed_items") or []
        if _item_name(entry)
    ]


def _equipment_rows_from_form(form):
    """The rows the user typed, as typed, for re-showing the form after an error."""
    names = form.getlist("neededItemName")
    quantities = form.getlist("neededItemQuantity")
    return [
        {"name": name.strip(), "quantity": quantities[index].strip() if index < len(quantities) else ""}
        for index, name in enumerate(names)
        if name.strip()
    ]

@app.errorhandler(413)
def upload_too_large(error):
    # campaign-create.html shows whatever [role="alert"] text comes back, instead of "HTTP 413"
    if request.path == url_for("campaign_create"):
        limit_mb = MAX_CAMPAIGN_UPLOAD_BYTES // (1024 * 1024)
        return f'<p role="alert">Those photos are too large. Keep them under {limit_mb} MB in total.</p>', 413
    return error

@app.route("/campaign-create", methods=["GET", "POST"])
def campaign_create():
    existing_campaign = None
    existing_image_urls = []
    existing_image_filenames = []
    removed_image_indices = set()

    if request.method == "GET":
        campaign_id = request.args.get("campaign_id", "").strip()
        if not campaign_id:
            return render_template(
                "campaign-create.html",
                campaign=None,
                existing_image_urls=existing_image_urls,
                existing_image_filenames=existing_image_filenames,
                removed_image_indices=removed_image_indices,
                form_amount="",
                form_needed_items=[],
                form_needs_equipment=False,
                form_keywords="",
            )

        if not session.get("user"):
            return redirect(url_for("login", next=request.full_path))
        if not ObjectId.is_valid(campaign_id):
            abort(404)

        existing_campaign = g.db.campaigns.find_one({
            "_id": ObjectId(campaign_id),
            "owner_email": session["user"]["email"],
        })
        if existing_campaign is None:
            abort(404)

        existing_image_urls = _campaign_image_urls(existing_campaign)
        existing_image_filenames = [
            os.path.basename(urlparse(image_url).path) or f"Photo {index + 1}"
            for index, image_url in enumerate(existing_image_urls)
        ]
        return render_template(
            "campaign-create.html",
            campaign=existing_campaign,
            existing_image_urls=existing_image_urls,
            existing_image_filenames=existing_image_filenames,
            removed_image_indices=removed_image_indices,
            form_amount=f"{existing_campaign.get('amount_cents', 0) / 100:.2f}",
            form_needed_items=_equipment_rows(existing_campaign),
            form_needs_equipment=bool(existing_campaign.get("needed_items")),
            form_keywords=", ".join(existing_campaign.get("keywords") or []),
        )

    if not session.get("user"):
        return redirect(url_for("login", next=request.path))

    # The app-wide limit is 5 MB. This form can carry five photos, so it gets its own limit.
    # It has to be set before request.form / request.files are first read.
    request.max_content_length = MAX_CAMPAIGN_UPLOAD_BYTES

    campaign_id = request.form.get("campaignId", "").strip()
    replace_all_images = request.form.get("replaceAllImages") == "1"
    if campaign_id:
        if not ObjectId.is_valid(campaign_id):
            abort(404)
        existing_campaign = g.db.campaigns.find_one({
            "_id": ObjectId(campaign_id),
            "owner_email": session["user"]["email"],
        })
        if existing_campaign is None:
            abort(404)
        existing_image_urls = _campaign_image_urls(existing_campaign)
        existing_image_filenames = [
            os.path.basename(urlparse(image_url).path) or f"Photo {index + 1}"
            for index, image_url in enumerate(existing_image_urls)
        ]

    removed_image_indices = {
        index
        for index in range(len(existing_image_urls))
        if replace_all_images or request.form.get(f"removePhoto-{index}") == "1"
    }
    retained_image_urls = [
        image_url
        for index, image_url in enumerate(existing_image_urls)
        if index not in removed_image_indices
    ]

    def render_form(error):
        return render_template(
            "campaign-create.html",
            campaign=existing_campaign,
            existing_image_urls=existing_image_urls,
            existing_image_filenames=existing_image_filenames,
            removed_image_indices=removed_image_indices,
            form_amount=(
                f"{existing_campaign.get('amount_cents', 0) / 100:.2f}"
                if existing_campaign
                else ""
            ),
            form_needed_items=_equipment_rows_from_form(request.form),
            form_needs_equipment=request.form.get("needsEquipment") == "on",
            form_keywords=request.form.get(
                "keywords",
                ", ".join(existing_campaign.get("keywords") or []) if existing_campaign else "",
            ),
            error=error,
        )

    submitted_image_order = request.form.getlist("imageOrder")
    if submitted_image_order:
        try:
            image_order = [int(index) for index in submitted_image_order]
        except ValueError:
            image_order = []
        if sorted(image_order) != list(range(len(existing_image_urls))):
            return render_form("The cover photo order is invalid. Please reorder the photos and try again.")
    else:
        image_order = list(range(len(existing_image_urls)))

    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    youtube_url = request.form.get("youtubeLink", "").strip()
    custom_url = request.form.get("customUrl", "").strip().lower()
    needs_equipment = request.form.get("needsEquipment") == "on"
    needed_items, equipment_error = (
        _parse_equipment_items(request.form) if needs_equipment else ([], None)
    )
    raw_keywords = request.form.get("keywords", "").strip()
    keywords = []
    seen_keywords = set()
    for keyword in raw_keywords.split(","):
        keyword = keyword.strip()
        normalized_keyword = keyword.casefold()
        if keyword and normalized_keyword not in seen_keywords:
            seen_keywords.add(normalized_keyword)
            keywords.append(keyword)
    images = [image for image in request.files.getlist("coverPhotos") if image.filename]
    # A file chosen next to a saved photo replaces that photo (unless the photo was removed).
    replacement_files = {
        index: image
        for index in range(len(existing_image_urls))
        if index not in removed_image_indices
        if (image := request.files.get(f"replacePhoto-{index}")) and image.filename
    }
    has_images = bool(images)

    try:
        amount = Decimal(request.form.get("fundraiserAmount", ""))
        if (
                not amount.is_finite()
                or amount <= 0
                or amount * 100 != (amount * 100).to_integral_value()
        ):
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        return render_form("Enter a valid amount with up to two decimal places.")

    if not title or not (retained_image_urls or has_images or youtube_url):
        return render_form("Enter a title and add at least one cover image or a YouTube link.")

    if equipment_error:
        return render_form(equipment_error)

    if (
        len(raw_keywords) > 1000
        or len(keywords) > 20
        or any(len(keyword) > 50 for keyword in keywords)
    ):
        return render_form("Add up to 20 keywords, with no more than 50 characters each.")

    if len(retained_image_urls) + len(images) > 5:
        return render_form("Upload no more than five cover photos in total.")

    if custom_url:
        custom_url_query = {"custom_url": custom_url}
        if existing_campaign:
            custom_url_query["_id"] = {"$ne": existing_campaign["_id"]}
        if g.db.campaigns.find_one(custom_url_query):
            return render_form("That custom URL is already in use.")

    for image in [*images, *replacement_files.values()]:
        extension = os.path.splitext(image.filename)[1].lower()
        if extension not in ALLOWED_IMAGE_EXT:
            return render_form("Use JPG, PNG, or WebP cover photos.")

    # Final photo list: saved photos in the chosen order (skipping removed ones, swapping replaced ones), then new uploads.
    image_urls = []
    stale_image_urls = [
        image_url
        for index, image_url in enumerate(existing_image_urls)
        if index in removed_image_indices
    ]
    for index in image_order:
        if index in removed_image_indices:
            continue
        replacement = replacement_files.get(index)
        if replacement:
            stale_image_urls.append(existing_image_urls[index])
            image_urls.append(_save_uploaded_image(replacement))
        else:
            image_urls.append(existing_image_urls[index])
    for image in images:
        image_urls.append(_save_uploaded_image(image))

    user = g.db.users.find_one({"email": session['user']['email']})
    campaign_data = {
        "ngo_id" : user['_id'],
        "title": title,
        "description": description,
        "amount_cents": int(amount * 100),
        "youtube_url": youtube_url or None,
        "image_url": image_urls[0] if image_urls else None,
        "image_urls": image_urls,
        "custom_url": custom_url or None,
        "owner_email": session["user"]["email"],
        "needed_items": needed_items,
        "keywords": keywords,
        "current_amount": 0,
        "status": "active"
    }

    if existing_campaign:
        g.db.campaigns.update_one(
            {"_id": existing_campaign["_id"]},
            {
                "$set": {
                    **campaign_data,
                    "updated_at": datetime.now(timezone.utc),
                    "confirmed": False,
                },
                "$unset": {"confirmed_at": "", "equipment_needs": ""},
            },
        )
        for image_url in set(stale_image_urls):
            if image_url not in image_urls:
                _delete_stored_image(image_url)
        saved_campaign_id = existing_campaign["_id"]
    else:
        campaign_data["created_at"] = datetime.now(timezone.utc)
        campaign_data["confirmed"] = False
        result = g.db.campaigns.insert_one(campaign_data)
        saved_campaign_id = result.inserted_id

    flash("Campaign details saved.", "popup")
    return redirect(url_for("campaign_preview", campaign_id=str(saved_campaign_id)))


def _render_campaign_page(campaign, preview_mode, _campaign_id):
    image_urls = _campaign_image_urls(campaign)
    target_cents = max(0, int(campaign.get("amount_cents", 0) or 0))
    raised_cents = (
        round(target_cents / 2)
        if preview_mode
        else max(0, int(campaign.get("current_amount", 0) or 0))
    )
    progress_percent = min(100, raised_cents / target_cents * 100) if target_cents else 0
    owner = g.db.users.find_one({"email": campaign.get("owner_email")}) or {}
    owner_profile = _user_profile_image(owner) if owner.get("_id") else None
    owner_name = owner['ngo_details']['ngo_name'] or campaign.get("owner_email", "Fundraiser organizer")
    current_user = session.get("user") or {}
    is_campaign_owner = current_user.get("email") == campaign.get("owner_email")
    can_confirm_campaign = (
        preview_mode and is_campaign_owner and campaign.get("confirmed") is not True
    )

    comments, comment_total = _campaign_comments(campaign["_id"])
    has_donated, can_comment = (
        (False, False) if preview_mode
        else _donor_comment_state(campaign["_id"], current_user.get("email"))
    )

    equipment_progress = _equipment_progress(campaign)

    template_name = "campaign-preview.html" if preview_mode else "campaign.html"
    return render_template(
        template_name,
        campaign=campaign,
        preview_mode=preview_mode,
        image_urls=image_urls,
        owner_name=owner_name,
        owner_profile=owner_profile,
        owner_initial=owner_name[:1].upper(),
        target_amount=f"{target_cents / 100:,.2f}",
        raised_amount=f"{raised_cents / 100:,.2f}",
        donor_count=max(0, int(campaign.get("donor_count", 0) or 0)),
        progress_percent=progress_percent,
        youtube_embed_url=_youtube_embed_url(campaign.get("youtube_url")),
        media_count=len(image_urls) + int(bool(campaign.get("youtube_url"))),
        needed_items=[_item_name(entry) for entry in campaign.get("needed_items") or [] if _item_name(entry)],
        equipment_items=_equipment_rows(campaign),
        equipment_progress=equipment_progress,
        equipment_left=sum(row["left"] for row in equipment_progress),
        is_campaign_owner=is_campaign_owner,
        can_confirm_campaign=can_confirm_campaign,
        _campaign_id = _campaign_id,
        is_completed=not preview_mode and _campaign_is_completed(campaign),
        comments=comments,
        comment_total=comment_total,
        has_donated=has_donated,
        can_comment=can_comment,
        comment_max=COMMENT_MAX_LENGTH,
    )


@app.route("/campaign/<campaign_id>")
def campaign_detail(campaign_id):
    if not ObjectId.is_valid(campaign_id):
        abort(404)

    visible = {
        "_id": ObjectId(campaign_id),
        "confirmed": {"$ne": False},
    }
    if (session.get("user") or {}).get("role") != "admin":
        visible["owner_removed"] = {"$ne": True}   # admins can still open a removed NGO's page
    campaign = g.db.campaigns.find_one(visible)
    if campaign is None:
        abort(404)

    return _render_campaign_page(campaign, preview_mode=False, _campaign_id=campaign_id)


@app.route("/campaign-preview/<campaign_id>")
def campaign_preview(campaign_id):
    if not ObjectId.is_valid(campaign_id):
        abort(404)

    campaign = g.db.campaigns.find_one({"_id": ObjectId(campaign_id)})
    if campaign is None:
        abort(404)

    return _render_campaign_page(campaign, preview_mode=True, _campaign_id=campaign_id)


@app.route("/campaign-confirm/<campaign_id>", methods=["POST"])
def campaign_confirm(campaign_id):
    if not session.get("user"):
        return redirect(url_for(
            "login",
            next=url_for("campaign_preview", campaign_id=campaign_id),
        ))
    if not ObjectId.is_valid(campaign_id):
        abort(404)

    campaign = g.db.campaigns.find_one({
        "_id": ObjectId(campaign_id),
        "owner_email": session["user"]["email"],
    })
    if campaign is None:
        abort(404)

    if campaign.get("confirmed") is not True:
        g.db.campaigns.update_one(
            {"_id": campaign["_id"]},
            {"$set": {
                "confirmed": True,
                "confirmed_at": datetime.now(timezone.utc),
            }},
        )

        g.db.users.update_one(
            {"email": session['user']['email']},
            {
                "$push": {
                    "campaigns": {
                        "campaign_id":campaign_id,
                        "campaign_title": campaign['title']
                    }
                }
            }
        )

        flash("Fundraiser created successfully.", "popup")
        _notify_campaign_live(campaign)

    return redirect(url_for("profile"))


@app.route("/campaign-delete/<campaign_id>", methods=["GET", "POST"])
def campaign_delete(campaign_id):
    if not session.get("user"):
        return redirect(url_for("login", next=request.path))

    if not ObjectId.is_valid(campaign_id):
        abort(404)

    campaign = g.db.campaigns.find_one({"_id": ObjectId(campaign_id)})
    if campaign is None:
        abort(404)

    is_admin = session["user"]["role"] == "admin"
    is_owner = session["user"]["email"] == campaign.get("owner_email")
    if not (is_admin or is_owner):
        abort(403)

    g.db.donations.delete_many({"campaign_id": ObjectId(campaign_id)})
    g.db.campaigns.delete_one({"_id": ObjectId(campaign_id)})

    flash("Campaign deleted.", "popup")
    return redirect(url_for("profile"))


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

COMMENT_MAX_LENGTH = 200
COMMENTS_SHOWN = 30


def _campaign_comments(campaign_id):
    """Newest donor messages for a campaign, plus how many there are in total."""
    query = {"campaign_id": campaign_id}
    latest = g.db.comments.find(query).sort("date_posted", -1).limit(COMMENTS_SHOWN)
    return list(latest), g.db.comments.count_documents(query)


def _donor_comment_state(campaign_id, email):
    """(has_donated, can_comment) for this user on this campaign.

    A donor may leave one message per donation, so a second donation unlocks a second message.
    """
    user = g.db.users.find_one({"email": email}, {"_id": 1}) if email else None
    if user is None:
        return False, False
    mine = {"campaign_id": campaign_id, "user_id": user["_id"]}
    donations = g.db.donations.count_documents(mine)
    messages = g.db.comments.count_documents(mine)
    return donations > 0, messages < donations


@app.route("/campaign-comment/<campaign_id>", methods=["POST"])
def campaign_comment(campaign_id):
    if not ObjectId.is_valid(campaign_id):
        abort(404)

    campaign_url = url_for("campaign_detail", campaign_id=campaign_id)
    if not session.get("user"):
        return redirect(url_for("login", next=campaign_url))

    campaign = g.db.campaigns.find_one({"_id": ObjectId(campaign_id), "confirmed": {"$ne": False}})
    if campaign is None:
        abort(404)

    back = campaign_url + "#donor-messages"
    text = (request.form.get("comment") or "").strip()
    if not text:
        flash("Write a message first.", "popup")
        return redirect(back)
    if len(text) > COMMENT_MAX_LENGTH:
        flash(f"Messages can be up to {COMMENT_MAX_LENGTH} characters.", "popup")
        return redirect(back)

    has_donated, can_comment = _donor_comment_state(campaign["_id"], session["user"]["email"])
    if not has_donated:
        flash("Donate to this fundraiser first, then you can leave a message.", "popup")
        return redirect(back)
    if not can_comment:
        flash("You've already left a message for your donation. Donate again to add another.", "popup")
        return redirect(back)

    user = g.db.users.find_one({"email": session["user"]["email"]})
    given = next(iter(g.db.donations.aggregate([
        {"$match": {"campaign_id": campaign["_id"], "user_id": user["_id"], "amount_cents": {"$exists": True}}},
        {"$group": {"_id": None, "total": {"$sum": "$amount_cents"}}},
    ])), None)

    g.db.comments.insert_one({
        "campaign_id": campaign["_id"],
        "user_id": user["_id"],
        "name": user.get("name") or session["user"]["email"],
        "profile-pic": user.get("profile-pic"),
        "donation_amount": f"{(given['total'] if given else 0) / 100:,.2f}",
        "comment": text,
        "date_posted": datetime.now(),
    })
    flash("Thank you! Your message is now on the fundraiser.", "popup")
    return redirect(back)

def create_comment(campaign_id, comment, amount_cents):
    if not ObjectId.is_valid(campaign_id):
        abort(404)

    campaign = g.db.campaigns.find_one({"_id": ObjectId(campaign_id)})
    if campaign is None:
        abort(404)

    comment = (comment or "").strip()[:COMMENT_MAX_LENGTH]
    if comment:
        user = g.db.users.find_one({"email": session['user']['email']})

        if user is None:
            return

        g.db.comments.insert_one({
            "campaign_id": ObjectId(campaign_id),
            "user_id": user["_id"],
            "name": user['name'],
            "profile-pic": user.get('profile-pic'),
            "donation_amount": f"{amount_cents / 100:,.2f}",
            "comment": comment,
            "date_posted": datetime.now()
        })


def _item_name(entry):
    if isinstance(entry, str):
        return entry
    return (entry or {}).get("name") or (entry or {}).get("item") or ""

def _item_quantity(entry):
    """Units asked for. Entries saved before quantities existed are plain names: one each."""
    if isinstance(entry, dict):
        try:
            return max(1, int(entry.get("quantity") or 1))
        except (TypeError, ValueError):
            return 1
    return 1

def _item_needed_amount(needed_items, item_name):
    return sum(_item_quantity(entry) for entry in needed_items if _item_name(entry) == item_name)

def _item_already_given(campaign_id, item_name):
    """Units of item_name already donated to this campaign so far."""
    result = next(iter(g.db.donations.aggregate([
        {"$match": {
            "campaign_id": ObjectId(campaign_id),
            "donation_type": "equipment",
            "item": item_name,
            "status": {"$ne": "rejected"},   # a parcel the NGO never received frees its quantity again
        }},
        {"$group": {"_id": None, "total": {"$sum": "$quantity"}}},
    ])), None)
    return result["total"] if result else 0


def _equipment_status(donation):
    return donation.get("status") or "received"

def _equipment_tallies(campaign_ids):
    tallies = {}
    for row in g.db.donations.aggregate([
        {"$match": {"campaign_id": {"$in": list(campaign_ids)}, "donation_type": "equipment"}},
        {"$group": {
            "_id": {"c": "$campaign_id", "i": "$item", "s": "$status"},
            "units": {"$sum": "$quantity"},
        }},
    ]):
        key = row["_id"]
        status = key.get("s") or "received"
        slot = tallies.setdefault(key["c"], {}).setdefault(key.get("i"), {})
        slot[status] = slot.get(status, 0) + int(row.get("units") or 0)
    return tallies


def _equipment_progress(campaign, tallies=None):
    """Per item the campaign asked for: how many are wanted, received, still on the way,
    and still needed. Rejected parcels don't count toward anything."""
    if tallies is None:
        tallies = _equipment_tallies([campaign["_id"]])
    counted = tallies.get(campaign["_id"], {})

    wanted = {}
    for entry in campaign.get("needed_items") or []:
        name = _item_name(entry)
        if name:
            wanted[name] = wanted.get(name, 0) + _item_quantity(entry)

    rows = []
    for name, needed in wanted.items():
        got = counted.get(name, {}).get("received", 0)
        coming = counted.get(name, {}).get("shipped", 0)
        rows.append({
            "name": name,
            "needed": needed,
            "received": got,
            "on_the_way": coming,
            "left": max(0, needed - got - coming),
            "received_percent": _percent(min(got, needed), needed),
            "covered_percent": _percent(min(got + coming, needed), needed),
        })
    return rows


# ---------------------------------------------------------------------------
# Donations / Stripe
# ---------------------------------------------------------------------------
@app.route("/campaign-donate/<campaign_id>", methods=["GET","POST"])
def campaign_donate(campaign_id):
    if not session.get("user"):
        return redirect(url_for("login", next=request.path))

    if not ObjectId.is_valid(campaign_id):
        abort(404)

    campaign = g.db.campaigns.find_one({"_id": ObjectId(campaign_id)})
    if not campaign:
        flash("No campaign found.", "popup")
        return redirect(url_for("index"))

    if campaign.get("owner_removed"):
        abort(404)

    if _campaign_is_completed(campaign):
        if request.method == "POST":
            return jsonify(error="This fundraiser has reached its goal. Thank you for your support!"), 400
        flash("This fundraiser has reached its goal. Thank you for all your support!", "popup")
        return redirect(url_for("campaign_detail", campaign_id=campaign_id))

    user = g.db.users.find_one({"email": campaign["owner_email"]}) or {}

    ngo = g.db.users.find_one({"_id": campaign['ngo_id']})

    if request.method == "POST":
        try:
            amount = Decimal(request.form.get("amount"))
            comment = request.form.get("message")
            if (
                    not amount.is_finite()
                    or amount <= 0
                    or amount * 100 != (amount * 100).to_integral_value()
            ):
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            flash("Enter a valid donation amount with up to two decimal places.", "popup")
            return redirect(url_for("campaign_detail", campaign_id=campaign_id))

        amount_cents = int(amount * 100)

        print(ngo.get("stripe_account_id"))
        checkout_session = stripe.checkout.Session.create(

            ui_mode="embedded_page",
            customer_email=session["user"]["email"],
            payment_method_types=["fpx","card", "alipay", "grabpay"],
            line_items=[
                {
                    'price_data': {
                        'currency': 'myr',
                        'product_data': {
                            'name': f'Donation to {campaign.get("title", "this campaign")}',
                        },
                        'unit_amount': amount_cents,
                    },
                    'quantity': 1,
                }
            ],
                mode="payment",
                integration_identifier="custom_embedded_web_0001",
                billing_address_collection="auto",
                submit_type="auto",

            metadata={
                "campaign_id": campaign_id,
                "donor_name": session["user"]["name"],
                "donor_email": session["user"]["email"],
                "comment" : comment
            },

            return_url=url_for("donation_success", _external=True) + "?session_id={CHECKOUT_SESSION_ID}",


        )

        session["campaign_details"] = {
            "campaign_id" : campaign_id,
            "amount_cents" : amount_cents,
            "comment" : comment
        }

        return jsonify(clientSecret=checkout_session.client_secret)

    image_urls = _campaign_image_urls(campaign)
    target_cents = max(0, int(campaign.get("amount_cents", 0) or 0))
    raised_cents = max(0, int(campaign.get("current_amount", 0) or 0))
    progress_percent = min(100, raised_cents / target_cents * 100) if target_cents else 0

    return render_template("campaign_donate.html",
                           stripe_publishable_key=STRIPE_PUBLISHABLE_KEY,
                           campaign=campaign,
                           cover_image=image_urls[0] if image_urls else None,
                           owner_name=user.get("name") or campaign.get("owner_email"),
                           owner_profile=_user_profile_image(user) if user.get("_id") else None,
                           raised_amount=f"{raised_cents / 100:,.2f}",
                           target_amount=f"{target_cents / 100:,.2f}",
                           progress_percent=progress_percent)

@app.route("/campaign-donate/equipment/<campaign_id>", methods=["GET", "POST"])
def campaign_donate_equipment(campaign_id):
    if not session.get('user'):
        return redirect(url_for('login', next=request.path))

    if not ObjectId.is_valid(campaign_id):
        abort(404)


    campaign = g.db.campaigns.find_one({'_id': ObjectId(campaign_id)})

    if not campaign or campaign.get("confirmed") is False or campaign.get("owner_removed"):
        flash('No campaign found.', 'popup')
        return redirect(url_for('index'))

    if _campaign_is_completed(campaign):
        flash("This fundraiser is complete. Thank you for all your support!", "popup")
        return redirect(url_for("campaign_detail", campaign_id=campaign_id))

    rows = _equipment_progress(campaign)
    by_name = {row["name"]: row for row in rows}

    owner = g.db.users.find_one({'email': campaign.get('owner_email')}) or {}
    ngo = owner.get("ngo_details") or {}
    image_urls = _campaign_image_urls(campaign)

    def show_form(message=None):
        # Re-draw the form with what was typed (a file can't be kept, so the proof is re-attached)
        if message:
            flash(message, "popup")
        return render_template(
            'campaign_donate_equipment.html',
            campaign=campaign,
            rows=rows,
            left_total=sum(row["left"] for row in rows),
            item_choices=[row["name"] for row in rows],
            couriers=COURIERS,
            hand_delivery=HAND_DELIVERY,
            del_name=ngo.get("ngo_name") or owner.get("name"),
            del_address=ngo.get("address") or owner.get("address"),
            del_phone=ngo.get("phone_no") or owner.get("phone_no"),
            cover_image=image_urls[0] if image_urls else None,
            owner_name=owner.get('name') or campaign.get('owner_email'),
            owner_profile=_user_profile_image(owner) if owner.get("_id") else None,
            form=request.form,
            selected_item=request.args.get("item", ""),
        ), (400 if message else 200)

    if request.method == "POST":
        item_name = (request.form.get('item') or '').strip()
        row = by_name.get(item_name)
        if row is None:
            return show_form('Select one of the items this campaign actually needs.')

        try:
            quantity = int(request.form.get('equipment_amount', 0))
            if quantity <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return show_form('Enter a valid quantity.')

        if quantity > row["left"]:
            if row["left"] > 0:
                return show_form(f"Only {row['left']} more '{item_name}' needed. Please lower the quantity.")
            return show_form(f"'{item_name}' is already fully covered. Thank you for your support!")

        courier = (request.form.get("courier") or "").strip()
        if courier not in COURIERS:
            return show_form("Choose the courier that is carrying your parcel.")

        tracking_no = re.sub(r"\s+", "", request.form.get("tracking_no") or "").upper()
        if courier == HAND_DELIVERY:
            tracking_no = ""
        elif not re.fullmatch(r"[A-Z0-9\-]{6,40}", tracking_no):
            return show_form("Enter the tracking number from your courier receipt "
                             "(6 to 40 letters, numbers or dashes).")
        elif g.db.donations.find_one({"donation_type": "equipment", "courier": courier,
                                      "tracking_no": tracking_no}):
            return show_form("That tracking number has already been submitted.")

        note = (request.form.get("note") or "").strip()[:200]

        user = g.db.users.find_one({'email': session['user']['email']})
        proof_file = request.files.get("proof")
        proof_id = None
        # Hand delivery has no courier receipt, so proof is optional. A file that is attached is still checked.
        if courier != HAND_DELIVERY or (proof_file and proof_file.filename):
            proof_id, proof_error = _save_proof(proof_file, {
                "kind": "equipment",
                "donor_email": user["email"],
                "owner_email": campaign.get("owner_email"),
                "campaign_id": campaign_id,
            })
            if proof_error:
                return show_form(proof_error)

        # Save the donation. It stays "shipped" until the NGO confirms the parcel arrived.
        g.db.donations.insert_one({
            'user_id': user['_id'],
            'campaign_id': ObjectId(campaign_id),
            'donation_type': 'equipment',
            'item': item_name,
            'quantity': quantity,
            'courier': courier,
            'tracking_no': tracking_no,
            'note': note,
            'proof_id': proof_id,
            'status': 'shipped',
            'date_posted': datetime.now(timezone.utc),
        })

        _notify_parcel_sent(user, campaign, item_name, quantity)

        flash(f"Thank you! {quantity} x {item_name} is on its way. "
              "You can follow it under My donations once the NGO confirms it arrived.", 'popup')
        return redirect(url_for('donation_history', type='equipment'))

    return show_form()


EQUIPMENT_TABS = ("shipped", "received", "rejected", "all")

@app.route("/ngo-equipment/<campaign_id>", methods=["GET"])
def ngo_equipment(campaign_id):
    viewer = session.get("user")
    if not viewer:
        return redirect(url_for("login", next=request.path))

    role = viewer.get("role")
    if role not in ("ngo", "admin"):
        return redirect(url_for("index"))

    if not ObjectId.is_valid(campaign_id):
        abort(404)

    campaign = g.db.campaigns.find_one({"_id": ObjectId(campaign_id)})
    if not campaign:
        abort(404)

    # If user is an NGO, make sure they own the campaign
    if role == "ngo" and campaign.get("owner_email") != viewer["email"]:
        abort(404)

    tab = request.args.get("status", "shipped")
    if tab not in EQUIPMENT_TABS:
        tab = "shipped"

    # Query equipment donations specifically for this campaign
    query = {"campaign_id": ObjectId(campaign_id), "donation_type": "equipment"}
    if tab == "shipped":
        query["status"] = "shipped"
    elif tab == "rejected":
        query["status"] = "rejected"
    elif tab == "received":
        query["status"] = {"$nin": ["shipped", "rejected"]}

    donations = list(g.db.donations.find(query).sort("_id", -1).limit(100))

    donors = {
        u["_id"]: u for u in g.db.users.find(
            {"_id": {"$in": [d.get("user_id") for d in donations]}}, {"name": 1, "email": 1}
        )
    }

    def local(moment):
        return (moment.replace(tzinfo=None) + MALAYSIA_OFFSET).strftime("%d %b %Y") if moment else ""

    rows = []
    for d in donations:
        status = _equipment_status(d)
        donor = donors.get(d.get("user_id")) or {}
        rows.append({
            "id": str(d["_id"]),
            "item": d.get("item") or "Item",
            "quantity": int(d.get("quantity") or 1),
            "campaign_id": str(campaign_id),
            "campaign_title": campaign.get("title") or "Fundraiser",
            "donor": donor.get("name") or "Anonymous donor",
            "donor_email": donor.get("email") or "",
            "courier": d.get("courier"),
            "tracking_no": d.get("tracking_no"),
            "note": d.get("note"),
            "proof_url": url_for("stored_proof", file_id=str(d["proof_id"])) if d.get("proof_id") else None,
            "status": status,
            "sent": local(_donation_time(d)),
            "handled": local(d.get("status_updated_at")),
            "can_act": role == "ngo" and status == "shipped",
        })

    # Tallies for summary stats for this campaign only
    parcels = {"shipped": 0, "received": 0, "rejected": 0}
    units = {"shipped": 0, "received": 0, "rejected": 0}
    for row in g.db.donations.aggregate([
        {"$match": {"campaign_id": ObjectId(campaign_id), "donation_type": "equipment"}},
        {"$group": {"_id": "$status", "parcels": {"$sum": 1}, "units": {"$sum": "$quantity"}}},
    ]):
        status = row["_id"] if row["_id"] in ("shipped", "rejected") else "received"
        parcels[status] += row["parcels"]
        units[status] += int(row["units"] or 0)

    # Get cover image URL
    image_urls = _campaign_image_urls(campaign)

    if image_urls:
        cover_image = image_urls[0]
    else:
        cover_image = DEFAULT_PROFILE_PIC

    return render_template(
        "ngo_equipment.html",
        campaign=campaign,
        campaign_id=campaign_id,
        cover_image=cover_image,
        rows=rows,
        tab=tab,
        parcels=parcels,
        units=units,
        read_only=(role != "ngo"),
    )


@app.route("/ngo-equipment/<donation_id>/<action>", methods=["POST"])
def ngo_equipment_update(donation_id, action):
    viewer = session.get("user")
    if not viewer:
        return redirect(url_for("login", next=url_for("index")))
    if viewer.get("role") != "ngo" or action not in ("received", "rejected"):
        abort(404)
    if not ObjectId.is_valid(donation_id):
        abort(404)

    donation = g.db.donations.find_one({"_id": ObjectId(donation_id), "donation_type": "equipment"})
    if donation is None:
        abort(404)
    campaign = g.db.campaigns.find_one({"_id": donation.get("campaign_id"), "owner_email": viewer["email"]})
    if campaign is None:
        abort(404)

    campaign_id_str = str(donation.get("campaign_id"))
    tab = request.form.get("status", "shipped")

    # Explicitly build URL pointing to the new /ngo-equipment/<campaign_id> route with status query string
    back = url_for("ngo_equipment", campaign_id=campaign_id_str, status=tab if tab in EQUIPMENT_TABS else "shipped")

    changed = g.db.donations.update_one(
        {"_id": donation["_id"], "status": "shipped"},
        {"$set": {
            "status": action,
            "status_updated_at": datetime.now(timezone.utc),
            "status_updated_by": viewer["email"],
        }},
    )
    if changed.modified_count == 0:
        flash("That parcel has already been handled.", "popup")
        return redirect(back)

    ngo = g.db.users.find_one({"email": viewer["email"]}) or {}
    donor = g.db.users.find_one({"_id": donation.get("user_id")}) or {}
    quantity = int(donation.get("quantity") or 1)
    item = donation.get("item") or "item"
    verb = "received" if action == "received" else "did not receive"
    g.db.audit_logs.insert_one({
        "user_id": ngo.get("_id"),
        "name": viewer["email"],
        "role": "ngo",
        "action": "equipment-received" if action == "received" else "equipment-declined",
        "changes": f"{(ngo.get('ngo_details') or {}).get('ngo_name') or ngo.get('name')} {verb} "
                   f"{quantity} x {item} from {donor.get('name') or 'a donor'}.",
        "timestamp": datetime.now(),
    })

    _notify_parcel_result(donor, campaign, item, quantity, action == "received")

    if action == "received" and donor.get("role") == "donor":
        _sync_donor_tier(donor)

    if action == "received":
        flash(f"Marked {quantity} x {item} as received. The donor can now see it.", "popup")
    else:
        flash(f"Marked {quantity} x {item} as not received. It counts as still needed again.", "popup")

    return redirect(back)

HISTORY_PAGE_SIZE = 15


@app.route("/donation_history")
def donation_history():
    viewer = session.get("user")
    if not viewer:
        return redirect(url_for("login", next=request.path))
    if viewer.get("role") != "donor":
        return redirect(url_for("profile"))

    user = g.db.users.find_one({"email": viewer["email"]})
    kind = request.args.get("type", "all")
    if kind not in ("funds", "equipment"):
        kind = "all"

    # Totals over everything this person has given
    total_cents = gifts = parcels = on_the_way = 0
    for d in g.db.donations.find(
        {"user_id": user["_id"]},
        {"amount_cents": 1, "donation_type": 1, "status": 1},
    ):
        if d.get("donation_type") == "equipment":
            status = _equipment_status(d)
            if status != "rejected":
                parcels += 1
            if status == "shipped":
                on_the_way += 1
        else:
            gifts += 1
            total_cents += int(d.get("amount_cents") or 0)

    query = {"user_id": user["_id"]}
    if kind == "funds":
        query["donation_type"] = {"$ne": "equipment"}
    elif kind == "equipment":
        query["donation_type"] = "equipment"
    shown = g.db.donations.count_documents(query)
    pages = max(1, -(-shown // HISTORY_PAGE_SIZE))
    page = min(max(1, request.args.get("page", 1, type=int)), pages)
    donations = list(
        g.db.donations.find(query).sort("_id", -1)
        .skip((page - 1) * HISTORY_PAGE_SIZE).limit(HISTORY_PAGE_SIZE)
    )

    titles = {
        c["_id"]: c.get("title") or "Untitled fundraiser"
        for c in g.db.campaigns.find(
            {"_id": {"$in": [d.get("campaign_id") for d in donations]}}, {"title": 1}
        )
    }

    def local(moment):
        return (moment.replace(tzinfo=None) + MALAYSIA_OFFSET).strftime("%d %b %Y") if moment else ""

    rows = []
    for d in donations:
        base = {
            "campaign_id": str(d.get("campaign_id")),
            "campaign_title": titles.get(d.get("campaign_id"), "A fundraiser that is no longer listed"),
            "when": local(_donation_time(d)),
        }
        if d.get("donation_type") == "equipment":
            rows.append({
                **base,
                "kind": "equipment",
                "item": d.get("item") or "Item",
                "quantity": int(d.get("quantity") or 1),
                "courier": d.get("courier"),
                "tracking_no": d.get("tracking_no"),
                "note": d.get("note"),
                "proof_url": url_for("stored_proof", file_id=str(d["proof_id"])) if d.get("proof_id") else None,
                "status": _equipment_status(d),
                "handled": local(d.get("status_updated_at")),
            })
        else:
            rows.append({
                **base,
                "kind": "funds",
                "amount": _rm(int(d.get("amount_cents") or 0)),
            })

    return render_template(
        "donation_history.html",
        rows=rows,
        kind=kind, page=page, pages=pages,
        total=_rm(total_cents),
        gifts=gifts,
        parcels=parcels,
        on_the_way=on_the_way,
    )

@app.route("/donate/success")
def donation_success():
    # Return donation success message.
    session_id = request.args.get("session_id")
    if session_id:
        try:
                checkout_session = stripe.checkout.Session.retrieve(session_id)
                if checkout_session.payment_status == "paid":
                    flash("Thank you for your donation!", "popup")

                    campaigns = g.db.campaigns

                    campaign_id = session['campaign_details']['campaign_id']
                    amount_cents = session['campaign_details']['amount_cents']
                    comment = session['campaign_details']['comment']

                    campaigns.update_one(
                        {"_id": ObjectId(campaign_id)},
                        {"$inc" : { 'current_amount' : amount_cents }
                    })


                    create_comment(campaign_id, comment, amount_cents)
                    print("COMMENT RECEIVED:", repr(comment))

                    current_campaign = g.db.campaigns.find_one({"_id": ObjectId(campaign_id)})

                    if current_campaign['current_amount'] >= current_campaign['amount_cents']:
                        campaigns.update_one(
                            {"_id": ObjectId(campaign_id)},
                            {"$set": {'status': 'completed' }}
                        )

                    g.db.users.update_one(
                        {"email": session['user']['email']},
                        {"$inc": {"donor_details.total_cents": amount_cents}}
                    )

                    user = g.db.users.find_one({"email": session['user']['email']})

                    g.db.donations.insert_one({
                        "user_id" : user["_id"],
                        "campaign_id" : ObjectId(campaign_id),
                        "amount_cents": amount_cents,
                        "donation_type": "funds",
                        "date_posted": datetime.now(timezone.utc),
                        "checkout_session_id": checkout_session.id,
                        "payout_status": "unpaid"
                    })

                    _sync_donor_tier(user)

                    _notify_funds_received(user, current_campaign, amount_cents)

                    session.pop("campaign_details", None)
                    return redirect(url_for("campaign_detail", campaign_id=campaign_id) + "#donor-messages")

                else:
                    flash("Payment Failed", "popup")

        except stripe.error.StripeError:
            flash("Error Occurred.", "popup")

    return redirect(url_for("index"))

# ---------------------------------------------------------------------------
# Rewards: three tiers, perks per tier, claims that an admin fulfils
# ---------------------------------------------------------------------------
# How it works
#   * A donor's standing is what they have given: every funds donation, plus each
#     equipment item the NGO confirmed as received, valued at a fixed credit per item.
#   * Tiers are lifetime, so a donor never drops down. Perks stack (Gold includes Silver).
#   * "badge" perks are automatic. Every other type is claimed by the donor, then an admin
#     fulfils the claim (posts the mention, sends the voucher code, ...) and leaves a note
#     the donor can read on their profile.
#   * Everything an admin can change lives in one document: reward_settings/config.
PERK_TYPES = {
    "badge":    {"label": "Profile badge",     "claim": False, "icon": "fa-solid fa-award"},
    "mention":  {"label": "Instagram mention", "claim": True,  "icon": "fa-brands fa-instagram"},
    "voucher":  {"label": "Voucher",           "claim": True,  "icon": "fa-solid fa-ticket"},
    "cashback": {"label": "Cashback",          "claim": True,  "icon": "fa-solid fa-coins"},
    "gift":     {"label": "Gift",              "claim": True,  "icon": "fa-solid fa-gift"},
    "birthday": {"label": "Birthday treat",    "claim": True,  "icon": "fa-solid fa-cake-candles"},
}
PERK_LIMITS = {"once": "Once", "yearly": "Once a year"}
TIER_NAME_MAX = 20
TIER_BLURB_MAX = 90
PERK_TITLE_MAX = 60
PERK_DETAIL_MAX = 200
CLAIM_NOTE_MAX = 200
CREDIT_MAX_CENTS = 1_000_000          # RM 10,000 per item is already absurd
INSTAGRAM_HANDLE = re.compile(r"^@?[A-Za-z0-9._]{1,30}$")
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
CLAIM_STATUSES = ("pending", "fulfilled", "declined")
CLAIMS_PAGE_SIZE = 15


def _default_rewards():
    """What the donor rewards look like until an admin changes them."""
    return {
        "equipment_credit_cents": 2500,
        "tiers": [
            {"key": "bronze", "name": "Bronze", "min_cents": 0,
             "blurb": "Every donor starts here."},
            {"key": "silver", "name": "Silver", "min_cents": 20000,
             "blurb": "For donors who keep coming back."},
            {"key": "gold", "name": "Gold", "min_cents": 100000,
             "blurb": "Our most generous supporters."},
        ],
        "perks": [
            {"id": "starter-bronze-badge", "tier": "bronze", "type": "badge", "limit": "once", "active": True,
             "title": "Supporter badge", "detail": "A badge on your profile."},
            {"id": "starter-silver-story", "tier": "silver", "type": "mention", "limit": "once", "active": True,
             "title": "Instagram story mention", "detail": "We thank you in our Instagram story."},
            {"id": "starter-silver-voucher", "tier": "silver", "type": "voucher", "limit": "yearly", "active": True,
             "title": "RM 5 partner voucher", "detail": "A voucher from one of our partners, once a year."},
            {"id": "starter-gold-post", "tier": "gold", "type": "mention", "limit": "once", "active": True,
             "title": "Instagram feed feature", "detail": "A post on our feed that names and thanks you."},
            {"id": "starter-gold-voucher", "tier": "gold", "type": "voucher", "limit": "yearly", "active": True,
             "title": "RM 20 partner voucher", "detail": "A bigger voucher from our partners, once a year."},
            {"id": "starter-gold-cashback", "tier": "gold", "type": "cashback", "limit": "yearly", "active": True,
             "title": "RM 10 cashback", "detail": "We send RM 10 back to you once a year."},
            {"id": "starter-gold-birthday", "tier": "gold", "type": "birthday", "limit": "yearly", "active": True,
             "title": "Birthday gift", "detail": "A small gift in your birthday month."},
        ],
    }


def _reward_config():
    """The saved rewards, or the defaults when an admin has never saved any."""
    config = _default_rewards()
    saved = g.db.reward_settings.find_one({"_id": "config"})
    if saved:
        if saved.get("tiers"):
            config["tiers"] = sorted(saved["tiers"], key=lambda t: t.get("min_cents", 0))
        if "perks" in saved:
            config["perks"] = saved["perks"]
        if saved.get("equipment_credit_cents") is not None:
            config["equipment_credit_cents"] = int(saved["equipment_credit_cents"])
    return config


def _now_myt():
    return datetime.now(timezone.utc).replace(tzinfo=None) + MALAYSIA_OFFSET


def _rm_to_cents(text):
    """'12.5' -> 1250, or None when it is not a sensible non-negative amount."""
    try:
        amount = Decimal(str(text).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return int((amount * 100).quantize(Decimal("1")))


def _donor_points(user_ids, credit_cents):
    """{user_id: {"funds": cents, "items": units, "points": cents}}.
    user_ids=None means every donor. Two plain aggregates, no distinct (strict Stable API)."""
    who = {"user_id": {"$in": list(user_ids)}} if user_ids is not None else {"user_id": {"$ne": None}}
    totals = {}
    for row in g.db.donations.aggregate([
        {"$match": {**who, "donation_type": {"$ne": "equipment"}}},
        {"$group": {"_id": "$user_id", "cents": {"$sum": "$amount_cents"}}},
    ]):
        totals.setdefault(row["_id"], {"funds": 0, "items": 0})["funds"] = int(row["cents"] or 0)
    for row in g.db.donations.aggregate([
        # a parcel only counts once the NGO has it (older rows without a status count as received)
        {"$match": {**who, "donation_type": "equipment", "status": {"$nin": ["shipped", "rejected"]}}},
        {"$group": {"_id": "$user_id", "units": {"$sum": "$quantity"}}},
    ]):
        totals.setdefault(row["_id"], {"funds": 0, "items": 0})["items"] = int(row["units"] or 0)
    for entry in totals.values():
        entry["points"] = entry["funds"] + entry["items"] * credit_cents
    return totals


def _tier_index(points, tiers):
    index = 0
    for i, tier in enumerate(tiers):
        if points >= tier["min_cents"]:
            index = i
    return index


def _donor_standing(user, config):
    """Where this donor is on the ladder, and how far from the next step."""
    tiers = config["tiers"]
    mine = _donor_points([user["_id"]], config["equipment_credit_cents"]).get(user["_id"])
    mine = mine or {"funds": 0, "items": 0, "points": 0}
    index = _tier_index(mine["points"], tiers)
    upcoming = tiers[index + 1] if index + 1 < len(tiers) else None
    floor = tiers[index]["min_cents"]
    if upcoming:
        span = max(1, upcoming["min_cents"] - floor)
        percent = max(0, min(100, int((mine["points"] - floor) * 100 / span)))
        to_next = upcoming["min_cents"] - mine["points"]
    else:
        percent, to_next = 100, 0
    return {
        **mine,
        "tier_index": index,
        "tier": tiers[index],
        "next_tier": upcoming,
        "to_next_cents": to_next,
        "percent": percent,
    }


def _sync_donor_tier(user, config=None):
    """Keep donor_details.tier current (the audit page reads it) and note a promotion."""
    if not user or user.get("role") != "donor":
        return None
    config = config or _reward_config()
    standing = _donor_standing(user, config)
    tier = standing["tier"]
    details = user.get("donor_details") or {}
    if details.get("tier") != tier["name"] or details.get("tier_key") != tier["key"]:
        keys = [t["key"] for t in config["tiers"]]
        old_key = details.get("tier_key") or next(
            (t["key"] for t in config["tiers"] if t["name"] == details.get("tier")), keys[0])
        old_index = keys.index(old_key) if old_key in keys else 0
        g.db.users.update_one({"_id": user["_id"]}, {"$set": {
            "donor_details.tier": tier["name"],
            "donor_details.tier_key": tier["key"],
        }})
        if standing["tier_index"] > old_index:
            g.db.audit_logs.insert_one({
                "user_id": user["_id"],
                "name": user["email"],
                "role": "donor",
                "action": "tier-up",
                "changes": f"{user.get('name') or user['email']} reached {tier['name']} "
                           f"(RM {_rm(standing['points'])} counted).",
                "timestamp": datetime.now(),
            })
            _notify_tier_up(user, tier)
    return standing


def _birth_month(user):
    raw = (user.get("donor_details") or {}).get("DOB")
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").month
    except ValueError:
        return None


def _claim_period(perk):
    return "all" if perk.get("limit") == "once" else str(_now_myt().year)


def _rewards_view(user):
    """Everything profile.html needs: the ladder, the donor's standing and each perk's state."""
    config = _reward_config()
    standing = _sync_donor_tier(user, config)
    tiers = config["tiers"]
    claims = list(g.db.reward_claims.find({"user_id": user["_id"]}).sort("_id", -1))
    month_now = _now_myt().month
    birth = _birth_month(user)

    columns = []
    ready = 0
    for index, tier in enumerate(tiers):
        unlocked = index <= standing["tier_index"]
        perks = []
        for perk in config["perks"]:
            if perk.get("tier") != tier["key"] or not perk.get("active", True):
                continue
            meta = PERK_TYPES.get(perk.get("type"), PERK_TYPES["badge"])
            period = _claim_period(perk)
            mine = [c for c in claims if c.get("perk_id") == perk["id"] and c.get("period") == period]
            live = next((c for c in mine if c["status"] in ("pending", "fulfilled")), None)
            declined = next((c for c in mine if c["status"] == "declined"), None)

            if not unlocked:
                state, hint = "locked", f"Unlocks at {tier['name']}"
            elif not meta["claim"]:
                state, hint = "auto", "Yours automatically"
            elif live:
                state, hint = live["status"], ""
            elif perk["type"] == "birthday" and birth is None:
                state, hint = "needs_birthday", "Add your birthday in Edit Personal Details"
            elif perk["type"] == "birthday" and birth != month_now:
                state, hint = "off_month", f"Opens in {MONTHS[birth - 1]}"
            else:
                state, hint = "ready", ""
                ready += 1
            perks.append({
                "id": perk["id"],
                "type": perk["type"],
                "label": meta["label"],
                "icon": meta["icon"],
                "title": perk["title"],
                "detail": perk.get("detail", ""),
                "limit": PERK_LIMITS.get(perk.get("limit"), "Once"),
                "state": state,
                "hint": hint,
                "claimed_on": live["claimed_at"].strftime("%d %b %Y") if live else "",
                "note": (live or {}).get("note") if live and live["status"] == "fulfilled" else "",
                "declined_note": (declined or {}).get("note") if declined and not live else "",
            })
        columns.append({
            "key": tier["key"],
            "name": tier["name"],
            "blurb": tier.get("blurb", ""),
            "min": _rm(tier["min_cents"]) if tier["min_cents"] % 100 else f"{tier['min_cents'] // 100:,}",
            "unlocked": unlocked,
            "current": index == standing["tier_index"],
            "perks": perks,
        })

    return {
        "columns": columns,
        "tier": standing["tier"]["name"],
        "tier_key": standing["tier"]["key"],
        "next_name": standing["next_tier"]["name"] if standing["next_tier"] else None,
        "to_next": _rm(standing["to_next_cents"]),
        "percent": standing["percent"],
        "counted": _rm(standing["points"]),
        "funds": _rm(standing["funds"]),
        "item_count": standing["items"],
        "credit": _rm(config["equipment_credit_cents"]),
        "ready": ready,
        "fill": min(100, round((standing["tier_index"] + standing["percent"] / 100)
                               / max(1, len(tiers) - 1) * 100)),
    }


@app.route("/rewards/claim/<perk_id>", methods=["POST"])
def reward_claim(perk_id):
    viewer = session.get("user")
    if not viewer:
        return redirect(url_for("login", next=url_for("profile")))
    if viewer.get("role") != "donor":
        abort(403)

    back = url_for("profile") + "#rewards"
    user = g.db.users.find_one({"email": viewer["email"]})
    config = _reward_config()
    standing = _sync_donor_tier(user, config)

    perk = next((p for p in config["perks"] if p["id"] == perk_id and p.get("active", True)), None)
    if perk is None or not PERK_TYPES.get(perk.get("type"), {}).get("claim"):
        abort(404)
    keys = [t["key"] for t in config["tiers"]]
    if perk.get("tier") not in keys or keys.index(perk["tier"]) > standing["tier_index"]:
        flash("That reward isn't unlocked yet.", "popup")
        return redirect(back)

    extra = {}
    if perk["type"] == "mention":
        handle = request.form.get("handle", "").strip()
        if not INSTAGRAM_HANDLE.match(handle):
            flash("Enter your Instagram handle, for example @yourname.", "popup")
            return redirect(back)
        extra["instagram"] = "@" + handle.lstrip("@")
    if perk["type"] in ("gift", "birthday"):
        address = (user.get("address") or "").strip()
        if not address:
            flash("Add your address in Edit Personal Details first, so we know where to send it.", "popup")
            return redirect(back)
        extra["address"] = address
        extra["phone_no"] = user.get("phone_no")
    if perk["type"] == "birthday":
        birth = _birth_month(user)
        if birth is None:
            flash("Add your birthday in Edit Personal Details to claim this.", "popup")
            return redirect(back)
        if birth != _now_myt().month:
            flash(f"You can claim this in {MONTHS[birth - 1]}, your birthday month.", "popup")
            return redirect(back)

    now = datetime.now()
    result = g.db.reward_claims.update_one(
        {"user_id": user["_id"], "perk_id": perk["id"], "period": _claim_period(perk),
         "status": {"$in": ["pending", "fulfilled"]}},
        {"$setOnInsert": {
            "user_email": user["email"],
            "user_name": user.get("name"),
            "perk_title": perk["title"],
            "perk_type": perk["type"],
            "tier_key": perk["tier"],
            "tier_name": next(t["name"] for t in config["tiers"] if t["key"] == perk["tier"]),
            "status": "pending",
            "claimed_at": now,
            **extra,
        }},
        upsert=True,
    )
    if result.upserted_id is None:
        flash("You have already claimed this reward.", "popup")
        return redirect(back)

    g.db.audit_logs.insert_one({
        "user_id": user["_id"],
        "name": user["email"],
        "role": "donor",
        "action": "reward-claim",
        "changes": f"{user.get('name') or user['email']} claimed \"{perk['title']}\".",
        "timestamp": now,
    })
    _notify_admins("reward", "New reward claim",
                   f"{user.get('name') or user['email']} claimed \"{perk['title']}\".",
                   link=url_for("admin_rewards", tab="claims"), cta="Review claim")
    flash(f"Claimed: {perk['title']}. We'll update this card when it's on its way.", "popup")
    return redirect(back)


def _admin_only():
    """None for an admin, otherwise the redirect to send everyone else to."""
    viewer = session.get("user")
    if not viewer:
        return redirect(url_for("login", next=request.path))
    if viewer.get("role") != "admin":
        return redirect(url_for("index"))
    return None


def _claim_row(claim):
    return {
        "id": str(claim["_id"]),
        "donor": claim.get("user_name") or claim.get("user_email"),
        "email": claim.get("user_email"),
        "perk": claim.get("perk_title"),
        "type": claim.get("perk_type"),
        "label": PERK_TYPES.get(claim.get("perk_type"), {}).get("label", "Reward"),
        "icon": PERK_TYPES.get(claim.get("perk_type"), {}).get("icon", "fa-solid fa-gift"),
        "tier": claim.get("tier_name"),
        "status": claim.get("status"),
        "when": _stamp(claim.get("claimed_at")),
        "handled": _stamp(claim.get("handled_at")),
        "handled_by": claim.get("handled_by"),
        "note": claim.get("note"),
        "facts": [(label, claim.get(key)) for label, key in (
            ("Instagram", "instagram"), ("Send to", "address"), ("Phone", "phone_no")) if claim.get(key)],
    }


@app.route("/admin-rewards")
def admin_rewards():
    blocked = _admin_only()
    if blocked:
        return blocked

    tab = request.args.get("tab", "tiers")
    if tab not in ("tiers", "claims"):
        tab = "tiers"
    status = request.args.get("status", "pending")
    if status not in CLAIM_STATUSES + ("all",):
        status = "pending"
    page = max(1, request.args.get("page", 1, type=int))

    config = _reward_config()
    tiers = config["tiers"]

    # how many donors sit on each step (donors with no gifts yet are on the first)
    donor_ids = [u["_id"] for u in g.db.users.find(
        {"role": "donor", "account_status": {"$ne": "removed"}}, {"_id": 1})]
    points = _donor_points(donor_ids, config["equipment_credit_cents"]) if donor_ids else {}
    counts = {t["key"]: 0 for t in tiers}
    for uid in donor_ids:
        counts[tiers[_tier_index((points.get(uid) or {}).get("points", 0), tiers)]["key"]] += 1

    claimed_by_perk = {
        r["_id"]: r["n"] for r in g.db.reward_claims.aggregate([
            {"$match": {"status": {"$ne": "declined"}}},
            {"$group": {"_id": "$perk_id", "n": {"$sum": 1}}},
        ])
    }

    tier_blocks = []
    for index, tier in enumerate(tiers):
        tier_blocks.append({
            **tier,
            "index": index,
            "min_rm": f"{tier['min_cents'] / 100:.2f}".rstrip("0").rstrip("."),
            "donors": counts.get(tier["key"], 0),
            "perks": [
                {**p, "meta": PERK_TYPES.get(p.get("type"), PERK_TYPES["badge"]),
                 "claims": claimed_by_perk.get(p["id"], 0)}
                for p in config["perks"] if p.get("tier") == tier["key"]
            ],
        })

    pending = g.db.reward_claims.count_documents({"status": "pending"})
    claim_rows, claim_total = [], 0
    if tab == "claims":
        query = {} if status == "all" else {"status": status}
        claim_total = g.db.reward_claims.count_documents(query)
        claim_rows = [
            _claim_row(c) for c in g.db.reward_claims.find(query).sort("claimed_at", -1)
            .skip((page - 1) * CLAIMS_PAGE_SIZE).limit(CLAIMS_PAGE_SIZE)
        ]

    me = g.db.users.find_one({"email": session["user"]["email"]}) or {}
    return render_template(
        "admin_rewards.html",
        user=me, tab=tab, status=status, page=page,
        pages=max(1, -(-claim_total // CLAIMS_PAGE_SIZE)),
        tiers=tier_blocks, donors=sum(counts.values()), pending=pending,
        claims=claim_rows, claim_total=claim_total,
        credit_rm=f"{config['equipment_credit_cents'] / 100:.2f}".rstrip("0").rstrip("."),
        perk_types=PERK_TYPES, perk_limits=PERK_LIMITS,
        limits=dict(title=PERK_TITLE_MAX, detail=PERK_DETAIL_MAX, name=TIER_NAME_MAX, blurb=TIER_BLURB_MAX,
                    note=CLAIM_NOTE_MAX),
    )


@app.route("/admin/rewards/save", methods=["POST"])
def admin_rewards_save():
    blocked = _admin_only()
    if blocked:
        return blocked

    action = request.form.get("action", "")
    tier_key = request.form.get("tier", "")
    back = url_for("admin_rewards", tab="tiers") + (f"#tier-{tier_key}" if tier_key else "")
    config = _reward_config()
    tiers = config["tiers"]
    keys = [t["key"] for t in tiers]
    summary = None

    def clean(name, limit):
        return " ".join(request.form.get(name, "").split())[:limit]

    def perk_fields():
        """Validated perk fields from the form, or (None, why)."""
        title = clean("title", PERK_TITLE_MAX + 1)
        detail = clean("detail", PERK_DETAIL_MAX + 1)
        kind = request.form.get("type", "")
        limit = request.form.get("limit", "once")
        if not title or len(title) > PERK_TITLE_MAX:
            return None, f"Give the reward a title of up to {PERK_TITLE_MAX} characters."
        if len(detail) > PERK_DETAIL_MAX:
            return None, f"Keep the description under {PERK_DETAIL_MAX} characters."
        if kind not in PERK_TYPES:
            return None, "Choose what kind of reward this is."
        if limit not in PERK_LIMITS:
            limit = "once"
        if kind == "badge":
            limit = "once"
        return {"title": title, "detail": detail, "type": kind, "limit": limit}, None

    if action == "save_tier":
        if tier_key not in keys:
            abort(404)
        tier = tiers[keys.index(tier_key)]
        name = clean("name", TIER_NAME_MAX + 1)
        blurb = clean("blurb", TIER_BLURB_MAX + 1)
        if not name or len(name) > TIER_NAME_MAX:
            flash(f"Give the tier a name of up to {TIER_NAME_MAX} characters.", "popup")
            return redirect(back)
        if len(blurb) > TIER_BLURB_MAX:
            flash(f"Keep the tagline under {TIER_BLURB_MAX} characters.", "popup")
            return redirect(back)
        if any(t["name"].lower() == name.lower() and t["key"] != tier_key for t in tiers):
            flash("Another tier already has that name.", "popup")
            return redirect(back)
        minimum = tier["min_cents"]
        if keys.index(tier_key) > 0:
            minimum = _rm_to_cents(request.form.get("min_rm", ""))
            if minimum is None:
                flash("Enter the amount a donor needs as a number, like 200.", "popup")
                return redirect(back)
            i = keys.index(tier_key)
            if minimum <= tiers[i - 1]["min_cents"]:
                flash(f"{name} must need more than {tiers[i - 1]['name']} (RM {_rm(tiers[i - 1]['min_cents'])}).", "popup")
                return redirect(back)
            if i + 1 < len(tiers) and minimum >= tiers[i + 1]["min_cents"]:
                flash(f"{name} must need less than {tiers[i + 1]['name']} (RM {_rm(tiers[i + 1]['min_cents'])}).", "popup")
                return redirect(back)
        tier.update({"name": name, "blurb": blurb, "min_cents": minimum})
        summary = f"Saved the {name} tier: starts at RM {_rm(minimum)}."

    elif action == "save_credit":
        credit = _rm_to_cents(request.form.get("credit_rm", ""))
        if credit is None or credit > CREDIT_MAX_CENTS:
            flash("Enter what one received item is worth, like 25.", "popup")
            return redirect(url_for("admin_rewards", tab="tiers") + "#credit")
        config["equipment_credit_cents"] = credit
        back = url_for("admin_rewards", tab="tiers") + "#credit"
        summary = f"Each received equipment item now counts as RM {_rm(credit)}."

    elif action == "add_perk":
        if tier_key not in keys:
            abort(404)
        fields, problem = perk_fields()
        if problem:
            flash(problem, "popup")
            return redirect(back)
        config["perks"].append({"id": uuid.uuid4().hex[:10], "tier": tier_key, "active": True, **fields})
        summary = f"Added \"{fields['title']}\" to {tiers[keys.index(tier_key)]['name']}."

    elif action in ("save_perk", "remove_perk"):
        perk = next((p for p in config["perks"] if p["id"] == request.form.get("perk_id")), None)
        if perk is None:
            abort(404)
        back = url_for("admin_rewards", tab="tiers") + f"#tier-{perk['tier']}"
        if action == "remove_perk":
            config["perks"].remove(perk)
            summary = f"Removed \"{perk['title']}\". Claims already made are kept."
        else:
            fields, problem = perk_fields()
            if problem:
                flash(problem, "popup")
                return redirect(back)
            perk.update(fields)
            perk["active"] = request.form.get("active") == "on"
            summary = f"Updated \"{perk['title']}\"" + ("" if perk["active"] else " (hidden from donors).")
    else:
        abort(400)

    admin_user = g.db.users.find_one({"email": session["user"]["email"]}) or {}
    g.db.reward_settings.update_one(
        {"_id": "config"},
        {"$set": {
            "tiers": tiers,
            "perks": config["perks"],
            "equipment_credit_cents": config["equipment_credit_cents"],
            "updated_at": datetime.now(),
            "updated_by": session["user"]["email"],
        }},
        upsert=True,
    )
    g.db.audit_logs.insert_one({
        "user_id": admin_user.get("_id"),
        "name": session["user"]["email"],
        "role": "admin",
        "action": "rewards-update",
        "changes": summary,
        "timestamp": datetime.now(),
    })
    flash(summary, "popup")
    return redirect(back)


@app.route("/admin/rewards/claims/<claim_id>", methods=["POST"])
def admin_reward_claim_update(claim_id):
    blocked = _admin_only()
    if blocked:
        return blocked
    if not ObjectId.is_valid(claim_id):
        abort(404)

    action = request.form.get("action", "")
    if action not in ("fulfil", "decline"):
        abort(400)
    back = url_for("admin_rewards", tab="claims", status=request.form.get("status") or "pending")
    note = " ".join(request.form.get("note", "").split())
    if not note or len(note) > CLAIM_NOTE_MAX:
        flash(("Add the voucher code, post link or delivery details the donor should see"
               if action == "fulfil" else "Say why you are declining, so the donor knows")
              + f" (up to {CLAIM_NOTE_MAX} characters).", "popup")
        return redirect(back)

    now = datetime.now()
    claim = g.db.reward_claims.find_one({"_id": ObjectId(claim_id)})
    if claim is None:
        abort(404)
    changed = g.db.reward_claims.update_one(
        {"_id": claim["_id"], "status": "pending"},
        {"$set": {
            "status": "fulfilled" if action == "fulfil" else "declined",
            "note": note,
            "handled_at": now,
            "handled_by": session["user"]["email"],
        }},
    )
    if changed.modified_count == 0:
        flash("That claim was already handled.", "popup")
        return redirect(back)

    admin_user = g.db.users.find_one({"email": session["user"]["email"]}) or {}
    verb = "Fulfilled" if action == "fulfil" else "Declined"
    g.db.audit_logs.insert_one({
        "user_id": admin_user.get("_id"),
        "name": session["user"]["email"],
        "role": "admin",
        "action": "reward-fulfilled" if action == "fulfil" else "reward-declined",
        "changes": f"{verb} \"{claim.get('perk_title')}\" for {claim.get('user_name') or claim.get('user_email')}. {note}",
        "timestamp": now,
    })
    _notify_claim_handled(claim, action == "fulfil", note)
    flash(f"{verb}. {claim.get('user_name') or claim.get('user_email')} will see it on their profile.", "popup")
    return redirect(back)

# ---------------------------------------------------------------------------
# NGO analytics
# ---------------------------------------------------------------------------
def _rm(cents):
    return f"{cents / 100:,.2f}"


def _percent(part, whole):
    return min(100, round(part / whole * 100)) if whole else 0


MALAYSIA_OFFSET = timedelta(hours=8)    # donations are stored in UTC; days are counted in Malaysia time
AGE_CUTOFFS = (18, 25, 35, 45, 55)
AGE_LABELS = ["Under 18", "18\u201324", "25\u201334", "35\u201344", "45\u201354", "55+"]


def _age_from_dob(dob, today):
    """Whole years old on `today`, or None when the date of birth is missing or unreadable."""
    if isinstance(dob, datetime):
        born = dob.date()
    else:
        try:
            born = datetime.strptime(str(dob)[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return age if 0 <= age < 120 else None

def _month_start(moment, offset=0):
    """First day of the month `offset` months away from `moment`."""
    index = moment.year * 12 + moment.month - 1 + offset
    return datetime(index // 12, index % 12 + 1, 1)

def _donation_time(donation):
    """Old donations have no date_posted, so use when the row was created."""
    posted = donation.get("date_posted") or donation["_id"].generation_time
    return posted.replace(tzinfo=None)

def _ngo_analytics(user):
    """Everything the NGO profile dashboard shows, computed in one pass."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    campaigns = list(g.db.campaigns.find(
        {"owner_email": user["email"]},
        {"title": 1, "amount_cents": 1, "current_amount": 1, "needed_items": 1,
         "image_urls": 1, "image_url": 1, "status": 1, "confirmed": 1},
    ).sort("created_at", -1))

    ngo_campaigns = list(g.db.campaigns.find(
        {"owner_email": session["user"]["email"]},
        {"_id": 1}
    ))

    ngo_campaign_ids = [
        campaign["_id"] for campaign in ngo_campaigns
    ]
    unpaid = list(g.db.donations.aggregate([
        {
            "$match": {
                "campaign_id": {"$in": ngo_campaign_ids},
                "payout_status": "unpaid",
                "donation_type": {"$ne": "equipment"},
            }
        },
        {
            "$group": {
                "_id": None,
                "total_cents": {"$sum": "$amount_cents"}
            }
        }
    ]))

    total_cents = unpaid[0]["total_cents"] if unpaid else 0

    daily_cents = {}
    donors = {}
    items_by_campaign = {}
    raised_by_campaign = {}

    for donation in g.db.donations.find(
        {"campaign_id": {"$in": [c["_id"] for c in campaigns]}},
        {"user_id": 1, "campaign_id": 1, "amount_cents": 1,
         "donation_type": 1, "quantity": 1, "date_posted": 1, "status": 1},
    ).sort("_id", 1):
        when = _donation_time(donation)
        campaign_id = donation.get("campaign_id")

        if donation.get("donation_type") == "equipment":
            if _equipment_status(donation) == "rejected":
                continue    # never arrived: not a gift
            if _equipment_status(donation) == "received":
                items_by_campaign[campaign_id] = (
                    items_by_campaign.get(campaign_id, 0) + int(donation.get("quantity") or 1)
                )
            cents = 0
        else:
            cents = int(donation.get("amount_cents") or 0)
            day = (when + MALAYSIA_OFFSET).date().isoformat()
            daily_cents[day] = daily_cents.get(day, 0) + cents

            raised_by_campaign[campaign_id] = raised_by_campaign.get(campaign_id, 0) + cents


        stats = donors.setdefault(donation.get("user_id"), [0, 0])
        stats[0] += 1
        stats[1] += cents

    # ---- per-campaign rows + totals (drafts are not live, so not counted) ----
    goal_cents = raised_cents = items_needed = items_received = active_count = 0
    rows = []
    for c in campaigns:
        goal = max(0, int(c.get("amount_cents") or 0))
        raised = max(0, raised_by_campaign.get(c["_id"], 0))
        needed = sum(_item_quantity(entry) for entry in c.get("needed_items") or [])
        received = min(needed, items_by_campaign.get(c["_id"], 0))
        is_draft = c.get("confirmed") is False
        status = "draft" if is_draft else "completed" if c.get("status") == "completed" else "active"

        if not is_draft:
            goal_cents += goal
            raised_cents += raised
            items_needed += needed
            items_received += received
        if status == "active":
            active_count += 1

        images = c.get("image_urls") or ([c["image_url"]] if c.get("image_url") else [])
        rows.append({
            "id": str(c["_id"]),
            "title": c.get("title") or "Untitled fundraiser",
            "image": images[0] if images else None,
            "status": status,
            "raised": _rm(raised),
            "goal": _rm(goal),
            "percent": _percent(raised, goal),
            "items_needed": needed,
            "items_received": received,
            "items_percent": _percent(received, needed),
        })

    item_rows = sorted(
        (r for r in rows if r["items_needed"] and r["status"] != "draft"),
        key=lambda r: r["items_needed"] - r["items_received"],
        reverse=True,
    )[:4]

    # ---- top donors ----
    top = sorted(donors.items(), key=lambda kv: (kv[1][1], kv[1][0]), reverse=True)[:5]
    donor_docs = {
        d["_id"]: d for d in g.db.users.find(
            {"_id": {"$in": [donor_id for donor_id, _ in top]}},
            {"name": 1, "profile-pic": 1},
        )
    }
    top_donors = []
    for donor_id, (gifts, cents) in top:
        doc = donor_docs.get(donor_id, {})
        name = doc.get("name") or "Anonymous donor"
        top_donors.append({
            "name": name,
            "initial": name[:1].upper(),
            "image": doc.get("profile-pic"),
            "gifts": gifts,
            "amount": _rm(cents),
        })

    # ---- who the donors are: age group and state (profile fields are optional) ----
    today = (now + MALAYSIA_OFFSET).date()
    age_counts = [0] * len(AGE_LABELS)
    state_counts = {}
    for doc in g.db.users.find(
        {"_id": {"$in": list(donors)}},
        {"donor_details.DOB": 1, "donor_details.state": 1},
    ):
        details = doc.get("donor_details") or {}
        age = _age_from_dob(details.get("DOB"), today)
        if age is not None:
            age_counts[sum(age >= cutoff for cutoff in AGE_CUTOFFS)] += 1
        if details.get("state"):
            state_counts[details["state"]] = state_counts.get(details["state"], 0) + 1

    ranked_states = sorted(state_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    shown_states = ranked_states[:8]
    if len(ranked_states) > 8:
        shown_states.append(("Other states", sum(count for _, count in ranked_states[8:])))

    donor_count = len(donors)
    remaining_cents = max(0, goal_cents - raised_cents)

    return {
        "raised": _rm(raised_cents),
        "goal": _rm(goal_cents),
        "remaining": _rm(remaining_cents),
        "goal_percent": _percent(raised_cents, goal_cents),
        "has_goal": goal_cents > 0,
        "has_funds": bool(daily_cents),
        "donor_count": donor_count,
        "total_users": g.db.users.count_documents({"role": "donor"}),
        "age_total": sum(age_counts),
        "age_unknown": donor_count - sum(age_counts),
        "state_total": sum(state_counts.values()),
        "state_unknown": donor_count - sum(state_counts.values()),
        "active_count": active_count,
        "campaign_count": len(rows),
        "campaigns": rows[:6],
        "unpaid_cents": total_cents,
        "unpaid": _rm(total_cents),
        "item_progress": {
            "needed": items_needed,
            "received": items_received,
            "percent": _percent(items_received, items_needed),
            "left_percent": 100 - _percent(items_received, items_needed),
            "rows": item_rows,
        },
        "top_donors": top_donors,
        # Only plain numbers/strings here -- this is what Chart.js receives.
        "chart": {
            "daily": [[day, round(cents / 100, 2)] for day, cents in sorted(daily_cents.items())],
            "today": today.isoformat(),
            "age": {"labels": AGE_LABELS, "values": age_counts},
            "states": {
                "labels": [name for name, _ in shown_states],
                "values": [count for _, count in shown_states],
            },
            "raised": round(raised_cents / 100, 2),
            "remaining": round(remaining_cents / 100, 2),
        },
    }

def _ngo_analytics(user):
    """Everything the NGO profile dashboard shows, computed in one pass."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    campaigns = list(g.db.campaigns.find(
        {"owner_email": user["email"]},
        {"title": 1, "amount_cents": 1, "current_amount": 1, "needed_items": 1,
         "image_urls": 1, "image_url": 1, "status": 1, "confirmed": 1},
    ).sort("created_at", -1))

    ngo_campaigns = list(g.db.campaigns.find(
        {"owner_email": session["user"]["email"]},
        {"_id": 1}
    ))

    ngo_campaign_ids = [
        campaign["_id"] for campaign in ngo_campaigns
    ]
    unpaid = list(g.db.donations.aggregate([
        {
            "$match": {
                "campaign_id": {"$in": ngo_campaign_ids},
                "payout_status": "unpaid",
                "donation_type": {"$ne": "equipment"},
            }
        },
        {
            "$group": {
                "_id": None,
                "total_cents": {"$sum": "$amount_cents"}
            }
        }
    ]))

    total_cents = unpaid[0]["total_cents"] if unpaid else 0

    daily_cents = {}
    donors = {}
    received_by_item = {}       # (campaign id, equipment name) -> units the NGO confirmed
    raised_by_campaign = {}

    for donation in g.db.donations.find(
        {"campaign_id": {"$in": [c["_id"] for c in campaigns]}},
        {"user_id": 1, "campaign_id": 1, "amount_cents": 1,
         "donation_type": 1, "item": 1, "quantity": 1, "date_posted": 1, "status": 1},
    ).sort("_id", 1):
        when = _donation_time(donation)
        campaign_id = donation.get("campaign_id")

        if donation.get("donation_type") == "equipment":
            if _equipment_status(donation) == "rejected":
                continue    # never arrived: not a gift
            if _equipment_status(donation) == "received":
                slot = (campaign_id, donation.get("item"))
                received_by_item[slot] = received_by_item.get(slot, 0) + int(donation.get("quantity") or 1)
            cents = 0
        else:
            cents = int(donation.get("amount_cents") or 0)
            day = (when + MALAYSIA_OFFSET).date().isoformat()
            daily_cents[day] = daily_cents.get(day, 0) + cents

            raised_by_campaign[campaign_id] = raised_by_campaign.get(campaign_id, 0) + cents


        stats = donors.setdefault(donation.get("user_id"), [0, 0])
        stats[0] += 1
        stats[1] += cents

    # ---- per-campaign rows + totals (drafts are not live, so not counted) ----
    goal_cents = raised_cents = items_needed = items_received = active_count = 0
    rows = []
    equipment = {}      # one entry per piece of equipment, added up over the NGO's live campaigns
    for c in campaigns:
        goal = max(0, int(c.get("amount_cents") or 0))
        raised = max(0, raised_by_campaign.get(c["_id"], 0))
        wanted = {}
        for entry in c.get("needed_items") or []:
            name = _item_name(entry)
            if name:
                wanted[name] = wanted.get(name, 0) + _item_quantity(entry)
        # a surplus doesn't count: 5 wheelchairs sent for 3 asked for is 3 of 3
        got = {name: min(units, received_by_item.get((c["_id"], name), 0)) for name, units in wanted.items()}
        needed = sum(wanted.values())
        received = sum(got.values())
        is_draft = c.get("confirmed") is False
        status = "draft" if is_draft else "completed" if c.get("status") == "completed" else "active"

        if not is_draft:
            goal_cents += goal
            raised_cents += raised
            items_needed += needed
            items_received += received
            for name, units in wanted.items():
                total = equipment.setdefault(name.strip().lower(), {"name": name.strip(), "needed": 0, "received": 0})
                total["needed"] += units
                total["received"] += got[name]
        if status == "active":
            active_count += 1

        images = c.get("image_urls") or ([c["image_url"]] if c.get("image_url") else [])
        rows.append({
            "id": str(c["_id"]),
            "title": c.get("title") or "Untitled fundraiser",
            "image": images[0] if images else None,
            "status": status,
            "raised": _rm(raised),
            "goal": _rm(goal),
            "percent": _percent(raised, goal),
            "items_needed": needed,
            "items_received": received,
            "items_percent": _percent(received, needed),
        })

    # most units still missing first; the rest are summed up as "+ N more"
    ranked_items = sorted(equipment.values(), key=lambda e: (e["received"] - e["needed"], e["name"].lower()))
    item_rows = [
        {"name": e["name"], "needed": e["needed"], "received": e["received"],
         "percent": _percent(e["received"], e["needed"])}
        for e in ranked_items[:5]
    ]

    # ---- top donors ----
    top = sorted(donors.items(), key=lambda kv: (kv[1][1], kv[1][0]), reverse=True)[:5]
    donor_docs = {
        d["_id"]: d for d in g.db.users.find(
            {"_id": {"$in": [donor_id for donor_id, _ in top]}},
            {"name": 1, "profile-pic": 1},
        )
    }
    top_donors = []
    for donor_id, (gifts, cents) in top:
        doc = donor_docs.get(donor_id, {})
        name = doc.get("name") or "Anonymous donor"
        top_donors.append({
            "name": name,
            "initial": name[:1].upper(),
            "image": doc.get("profile-pic"),
            "gifts": gifts,
            "amount": _rm(cents),
        })

    # ---- who the donors are: age group and state (profile fields are optional) ----
    today = (now + MALAYSIA_OFFSET).date()
    age_counts = [0] * len(AGE_LABELS)
    state_counts = {}
    for doc in g.db.users.find(
        {"_id": {"$in": list(donors)}},
        {"donor_details.DOB": 1, "donor_details.state": 1},
    ):
        details = doc.get("donor_details") or {}
        age = _age_from_dob(details.get("DOB"), today)
        if age is not None:
            age_counts[sum(age >= cutoff for cutoff in AGE_CUTOFFS)] += 1
        if details.get("state"):
            state_counts[details["state"]] = state_counts.get(details["state"], 0) + 1

    ranked_states = sorted(state_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    shown_states = ranked_states[:8]
    if len(ranked_states) > 8:
        shown_states.append(("Other states", sum(count for _, count in ranked_states[8:])))

    donor_count = len(donors)
    remaining_cents = max(0, goal_cents - raised_cents)

    return {
        "raised": _rm(raised_cents),
        "goal": _rm(goal_cents),
        "remaining": _rm(remaining_cents),
        "goal_percent": _percent(raised_cents, goal_cents),
        "has_goal": goal_cents > 0,
        "has_funds": bool(daily_cents),
        "donor_count": donor_count,
        "total_users": g.db.users.count_documents({"role": "donor"}),
        "age_total": sum(age_counts),
        "age_unknown": donor_count - sum(age_counts),
        "state_total": sum(state_counts.values()),
        "state_unknown": donor_count - sum(state_counts.values()),
        "active_count": active_count,
        "campaign_count": len(rows),
        "campaigns": rows[:6],
        "unpaid_cents": total_cents,
        "unpaid": _rm(total_cents),
        "item_progress": {
            "needed": items_needed,
            "received": items_received,
            "percent": _percent(items_received, items_needed),
            "left_percent": 100 - _percent(items_received, items_needed),
            "rows": item_rows,
            "more": max(0, len(ranked_items) - len(item_rows)),
        },
        "top_donors": top_donors,
        # Only plain numbers/strings here -- this is what Chart.js receives.
        "chart": {
            "daily": [[day, round(cents / 100, 2)] for day, cents in sorted(daily_cents.items())],
            "today": today.isoformat(),
            "age": {"labels": AGE_LABELS, "values": age_counts},
            "states": {
                "labels": [name for name, _ in shown_states],
                "values": [count for _, count in shown_states],
            },
            "raised": round(raised_cents / 100, 2),
            "remaining": round(remaining_cents / 100, 2),
        },
    }

def _admin_analytics(selected_year):
    """Everything the admin dashboard shows, computed in one pass over
    every donation, for the given year's monthly charts."""
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    campaigns = list(g.db.campaigns.find(
        {},
        {"title": 1, "amount_cents": 1, "image_urls": 1, "image_url": 1,
         "status": 1, "confirmed": 1, "owner_email": 1, "created_at": 1},
    ).sort("created_at", -1))

    owner_emails = {c.get("owner_email") for c in campaigns if c.get("owner_email")}
    owners = {
        o["email"]: o for o in g.db.users.find(
            {"email": {"$in": list(owner_emails)}},
            {"email": 1, "name": 1, "ngo_details.ngo_name": 1, "profile-pic": 1},
        )
    }
    for owner in owners.values():
        _user_profile_image(owner)

    campaign_ids = [c["_id"] for c in campaigns]
    owner_by_campaign = {c["_id"]: c.get("owner_email") for c in campaigns}
    campaign_titles = {c["_id"]: c.get("title") or "Untitled fundraiser" for c in campaigns}

    year_start = datetime(selected_year, 1, 1)
    year_end = datetime(selected_year + 1, 1, 1)

    monthly_funds_cents = [0] * 12
    monthly_equipment_units = [0] * 12

    donors = {}  # user_id -> [gifts, total cents]
    ngo_raised_cents = {}  # owner_email -> cents
    item_totals = {}  # item name -> units
    raised_by_campaign = {}  # campaign_id -> cents raised

    total_funds_cents = 0
    total_equipment_units = 0
    donor_ids = set()

    for donation in g.db.donations.find(
            {"campaign_id": {"$in": campaign_ids}},
            {"user_id": 1, "campaign_id": 1, "amount_cents": 1,
             "donation_type": 1, "quantity": 1, "item": 1, "date_posted": 1, "status": 1},
    ).sort("_id", 1):
        if donation.get("donation_type") == "equipment" and _equipment_status(donation) == "rejected":
            continue    # never arrived: not a gift
        when = _donation_time(donation)
        campaign_id = donation.get("campaign_id")
        user_id = donation.get("user_id")
        donor_ids.add(user_id)

        slot = when.month - 1 if year_start <= when < year_end else None

        if donation.get("donation_type") == "equipment":
            quantity = int(donation.get("quantity") or 1)
            total_equipment_units += quantity
            item_name = donation.get("item") or "Unlabelled item"
            item_totals[item_name] = item_totals.get(item_name, 0) + quantity
            if slot is not None:
                monthly_equipment_units[slot] += quantity
            cents = 0
        else:
            cents = int(donation.get("amount_cents") or 0)
            total_funds_cents += cents
            raised_by_campaign[campaign_id] = raised_by_campaign.get(campaign_id, 0) + cents
            if slot is not None:
                monthly_funds_cents[slot] += cents
            owner_email = owner_by_campaign.get(campaign_id)
            if owner_email:
                ngo_raised_cents[owner_email] = ngo_raised_cents.get(owner_email, 0) + cents

        stats = donors.setdefault(user_id, [0, 0])
        stats[0] += 1
        stats[1] += cents

    # ---- campaign rows, for the admin management list ----
    status_filter = request.args.get("status", "all")
    rows = []
    active_count = completed_count = draft_count = 0
    for c in campaigns:
        goal = max(0, int(c.get("amount_cents") or 0))
        raised = max(0, raised_by_campaign.get(c["_id"], 0))
        is_draft = c.get("confirmed") is False
        status = "draft" if is_draft else ("completed" if c.get("status") == "completed" else "active")

        if status == "active":
            active_count += 1
        elif status == "completed":
            completed_count += 1
        else:
            draft_count += 1

        owner = owners.get(c.get("owner_email"), {})
        owner_name = (owner.get("ngo_details") or {}).get("ngo_name") or owner.get("name") or c.get(
            "owner_email") or "Unknown NGO"
        images = c.get("image_urls") or ([c["image_url"]] if c.get("image_url") else [])

        rows.append({
            "id": str(c["_id"]),
            "title": c.get("title") or "Untitled fundraiser",
            "image": images[0] if images else None,
            "status": status,
            "owner_name": owner_name,
            "raised": _rm(raised),
            "goal": _rm(goal),
            "percent": _percent(raised, goal),
        })

    if status_filter in {"active", "completed", "draft"}:
        visible_rows = [r for r in rows if r["status"] == status_filter]
    else:
        status_filter = "all"
        visible_rows = rows

    # ---- rankings ----
    top_donor_pairs = sorted(donors.items(), key=lambda kv: (kv[1][1], kv[1][0] or 0), reverse=True)[:5]
    donor_docs = {
        d["_id"]: d for d in g.db.users.find(
            {"_id": {"$in": [uid for uid, _ in top_donor_pairs if uid]}},
            {"name": 1, "profile-pic": 1},
        )
    }
    top_donors = []
    for user_id, (gifts, cents) in top_donor_pairs:
        doc = donor_docs.get(user_id, {})
        name = doc.get("name") or "Anonymous donor"
        top_donors.append({
            "name": name,
            "initial": name[:1].upper(),
            "image": doc.get("profile-pic"),
            "gifts": gifts,
            "amount": _rm(cents),
        })

    top_ngo_pairs = sorted(ngo_raised_cents.items(), key=lambda kv: kv[1], reverse=True)[:5]
    top_ngos = []
    for email, cents in top_ngo_pairs:
        owner = owners.get(email, {})
        name = (owner.get("ngo_details") or {}).get("ngo_name") or owner.get("name") or email
        top_ngos.append({
            "name": name,
            "initial": name[:1].upper(),
            "image": owner.get("profile-pic"),
            "amount": _rm(cents),
        })

    top_items = sorted(item_totals.items(), key=lambda kv: kv[1], reverse=True)[:5]
    max_item_units = max((units for _, units in top_items), default=0)
    item_rows = [
        {"name": name, "units": units, "percent": _percent(units, max_item_units)}
        for name, units in top_items
    ]

    # ---- recent activity feed ----
    recent = list(g.db.donations.find(
        {"campaign_id": {"$in": campaign_ids}},
        {"user_id": 1, "campaign_id": 1, "amount_cents": 1, "donation_type": 1,
         "quantity": 1, "item": 1, "date_posted": 1},
    ).sort("_id", -1).limit(6))
    recent_user_ids = [r.get("user_id") for r in recent if r.get("user_id")]
    recent_donors = {
        d["_id"]: d.get("name") for d in g.db.users.find(
            {"_id": {"$in": recent_user_ids}}, {"name": 1},
        )
    }
    recent_feed = []
    for r in recent:
        when = _donation_time(r)
        if r.get("donation_type") == "equipment":
            detail = f"{int(r.get('quantity') or 1)} x {r.get('item') or 'item'}"
        else:
            detail = f"RM {_rm(int(r.get('amount_cents') or 0))}"
        recent_feed.append({
            "donor": recent_donors.get(r.get("user_id")) or "Anonymous donor",
            "campaign": campaign_titles.get(r.get("campaign_id"), "a campaign"),
            "detail": detail,
            "when": when.strftime("%d %b, %H:%M"),
        })

    donor_count = len(donor_ids)
    repeat_donors = sum(1 for gifts, _ in donors.values() if gifts > 1)
    ngo_count = g.db.users.count_documents({"role": "ngo"})

    pending_rows = list(g.db.donations.aggregate([
        {"$match": {"payout_status": "unpaid", "donation_type": {"$ne": "equipment"}}},
        {"$group": {"_id": None, "total_cents": {"$sum": "$amount_cents"}}},
    ]))
    pending_payout_cents = pending_rows[0]["total_cents"] if pending_rows else 0

    return {
        "year": selected_year,
        "status_filter": status_filter,
        "raised": _rm(total_funds_cents),
        "equipment_units": total_equipment_units,
        "donor_count": donor_count,
        "repeat_percent": _percent(repeat_donors, donor_count),
        "campaign_count": len(rows),
        "active_count": active_count,
        "completed_count": completed_count,
        "draft_count": draft_count,
        "ngo_count": ngo_count,
        "pending_payout": _rm(pending_payout_cents),
        "campaigns": visible_rows,
        "top_donors": top_donors,
        "top_ngos": top_ngos,
        "item_rows": item_rows,
        "recent_feed": recent_feed,
        "has_funds": any(monthly_funds_cents),
        "has_equipment": any(monthly_equipment_units),
        "chart": {
            "months": month_names,
            "funds": [round(c / 100, 2) for c in monthly_funds_cents],
            "equipment": monthly_equipment_units,
            "status_counts": [active_count, completed_count, draft_count],
        },
    }


# ---------------------------------------------------------------------------
# Auth: Google, Twitter & Facebook sign-in, login, password reset
# ---------------------------------------------------------------------------
def login_or_register_oauth_user(provider, name, email, picture_url):
    #Create account and start session. OR Login if account exists.
    #All providers have the same behavior.

    users = g.db.users
    existing_user = users.find_one({"email": email})

    if not existing_user:
        users.insert_one({
            "email": email,
            "name": name,
            "password": None,  # social login doesn't provide a password
            "address": None,
            "phone_no": None,
            "profile-pic": picture_url,
            "role": "donor",
            "provider": provider,
            "profile_completed": False,
            "account_status": "active",

            "donor_details": {
                "DOB": None,
                "state": None,
                "membership_streak": datetime.now(),
                "tier": "Bronze",
                "total_cents": 0
            }
        })
        session['user'] = {"name": name, "email": email, "role": "donor"}
        return

    if _account_removed(existing_user):
        flash(REMOVED_ACCOUNT_MESSAGE, "popup")
        return

    session['user'] = {
        "name": existing_user.get("name", existing_user["email"]),
        "email": email,
        "role": existing_user.get("role")
    }

#Google
@app.route("/login/google")
def google_login():

    redirect_uri = url_for('authorize', _external=True)
    print("REDIRECT URI:", redirect_uri)  # debug

    return oauth.google.authorize_redirect(redirect_uri, prompt='select_account')


@app.route('/authorize')
def authorize():
    # Retrieve google account information
    token = oauth.google.authorize_access_token()
    user_info = token.get('userinfo')

    if not user_info:
        flash("Google login failed.", "popup")
        return redirect(url_for('login'))

    name = user_info.get('name')
    email = user_info.get('email')
    picture_url = user_info.get('picture')

    login_or_register_oauth_user("google", name, email, picture_url)
    return redirect('/')

#################################
# TMR YA BETTER NOT DELETE THIS #
#################################
@app.route("/login", methods=["POST", "GET"])
def login():

    def redirect_by_role(role):
        if role == "donor":
            return redirect(url_for("index"))

        elif role == "ngo":
            return redirect(url_for("profile"))

        elif role == "admin":
            return redirect(url_for("admin_dashboard"))

        return redirect(url_for("index"))

    # If the user is already logged in
    if session.get("user"):
        return redirect_by_role(session["user"].get("role"))

    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        if not email or not password:
            flash("Email and password required.", "popup")
            return redirect(url_for("login"))

        users = g.db.users
        existing_user = users.find_one({"email": email})

        if existing_user and not existing_user.get("password"):
            flash(
                "This account uses Google sign-in. Please log in with Google.",
                "popup"
            )
            return redirect(url_for("index"))

        elif existing_user and check_password_hash(
            existing_user["password"], password
        ):
            if _account_removed(existing_user):
                flash(REMOVED_ACCOUNT_MESSAGE, "popup")
                return redirect(url_for("index"))

            session["user"] = {
                "name": existing_user.get("name", existing_user["email"]),
                "email": email,
                "role": existing_user.get("role")
            }

            user = g.db.users.find_one({"email": email})

            g.db.audit_logs.insert_one({
                "user_id": user["_id"],
                "name": user["email"],
                "role": user["role"],
                "action": "sign-in",
                "changes": f"{user['name']} signed in.",
                "timestamp": datetime.now()
            })

            # Redirect according to the user's role
            return redirect_by_role(session["user"].get("role"))

        else:
            flash("Invalid credentials", "popup")
            return redirect(url_for("index"))

    return render_template("login.html")

OTP_MAX_ATTEMPTS = 5


@app.route("/forget-password", methods=["GET", "POST"])
def forget_password():

    resets = g.db.password_resets

    def clear_reset():
        email = session.get("reset_email")
        if email:
            resets.delete_many({"email": email})
        for key in ("reset_email", "reset_otp_secret", "otp_verified"):
            session.pop(key, None)

    if request.method == "POST":

        action = request.form.get("action")

        # ----------Send (or re-send) OTP-----------#
        if action == "send_otp":

            email = (request.form.get("email") or "").strip()
            users = g.db.users

            recipient = users.find_one({"email": email})

            if not recipient or _account_removed(recipient):
                flash("Account with that email does not exist.", "popup")
                return redirect(url_for('forget_password'))

            # Generate OTP
            otp = pyotp.random_base32()
            totp = pyotp.TOTP(otp, interval=180)

            generated_otp = totp.now()

            # Remember it on the server; the session only says whose reset this is
            resets.update_one(
                {"email": email},
                {"$set": {"secret": otp, "attempts": 0, "verified": False,
                          "created_at": datetime.now()}},
                upsert=True,
            )
            session["reset_email"] = email
            session.pop("otp_verified", None)

            # Send email
            subject = "MEDonate - Forget Password OTP"
            body = (
                f"""
            Hello {recipient['name']},

            This is your one-time-password to reset your MEDonate password.

            OTP: {generated_otp}

            This OTP is valid for one minute.

            MEDonate
                    """)

            try:
                msg = Message(
                    subject=subject,
                    sender=app.config['MAIL_USERNAME'],
                    recipients=[recipient['email']]
                )

                msg.body = body

                mail.send(msg)

                flash("OTP has been sent to your email.", "popup")
                return redirect(url_for("forget_password", step=2))

            except Exception as e:
                print("Email error:", e)
                clear_reset()
                flash("Failed to send OTP.", "popup")
                return redirect(url_for("forget_password"))

        # ----------Verify OTP-----------#
        elif action == "verify_otp":

            email = session.get("reset_email")
            record = resets.find_one({"email": email}) if email else None

            if not record:
                flash("Please request an OTP first.", "popup")
                return redirect(url_for("forget_password"))

            if record.get("attempts", 0) >= OTP_MAX_ATTEMPTS:
                clear_reset()
                flash("Too many wrong codes. Request a new OTP.", "popup")
                return redirect(url_for("forget_password"))

            resets.update_one({"_id": record["_id"]}, {"$inc": {"attempts": 1}})
            entered_otp = (request.form.get("otp") or "").strip()

            # valid_window=1 also accepts the previous 60 s code, so "valid for one minute" is true
            if pyotp.TOTP(record["secret"], interval=60).verify(entered_otp, valid_window=1):
                resets.update_one({"_id": record["_id"]}, {"$set": {"verified": True}})
                session["otp_verified"] = True
                flash("OTP verified successfully.", "popup")
                return redirect(url_for("forget_password", step=3))

            flash("Invalid or expired OTP.", "popup")
            return redirect(url_for("forget_password", step=2))

        # ----------Choose the new password-----------#
        elif action == "reset":

            email = session.get("reset_email")
            record = resets.find_one({"email": email}) if email else None

            if not record or not record.get("verified") or not session.get("otp_verified"):
                flash("Please verify your OTP first.", "popup")
                return redirect(url_for("forget_password"))

            password = request.form.get("password") or ""
            confirm_password = request.form.get("confirm_password") or ""

            if not password or not confirm_password:
                flash("Please enter password and password confirmation.", "popup")
                return redirect(url_for("forget_password", step=3))

            if password != confirm_password:
                flash("Passwords do not match.", "popup")
                return redirect(url_for("forget_password", step=3))

            weak = _password_problem(password)
            if weak:
                return redirect(url_for("forget_password", step=3))

            account = g.db.users.find_one({"email": email})
            if not account or _account_removed(account):
                clear_reset()
                flash("Account with that email does not exist.", "popup")
                return redirect(url_for("forget_password"))

            g.db.users.update_one(
                {"_id": account["_id"]},
                {"$set": {"password": generate_password_hash(password)}},
            )
            g.db.audit_logs.insert_one({
                "user_id": account["_id"],
                "name": account["email"],
                "role": account.get("role"),
                "action": "password-reset",
                "changes": f"{account.get('name') or account['email']} reset their password with an email OTP.",
                "timestamp": datetime.now()
            })
            clear_reset()

            _notify_password_changed(account)
            flash("Your password was reset successfully. You can log in with it now.", "popup")
            return render_template("forget_password.html", step=4)

    # GET: only show a later step when the earlier one was really completed in this session
    if request.args.get("restart"):
        clear_reset()

    email = session.get("reset_email")
    record = resets.find_one({"email": email}) if email else None
    step = 1
    if record and request.args.get("step") == "2":
        step = 2
    elif record and record.get("verified") and session.get("otp_verified") and request.args.get("step") == "3":
        step = 3

    return render_template('forget_password.html',
                           step=step,
                           reset_email=email if step > 1 else None)

@app.route("/reset-password", methods=['POST', 'GET'])
def reset_password():
    # Changing your own password while logged in. Resetting a forgotten one by email
    # happens on /forget-password. The Reset Password pop-up on Edit Profile posts here with
    # fetch() and wants JSON back; the old stand-alone page posts a normal form and gets a redirect.
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if not session.get("user"):
        if wants_json:
            return jsonify(success=False, message="Please sign in again."), 401
        flash("Access not allowed.", "popup")
        return redirect(url_for("forget_password"))

    if request.method == "POST":
        def refuse(message, status=400):
            if wants_json:
                return jsonify(success=False, message=message), status
            flash(message, "popup")
            return redirect(url_for("reset_password"))

        user = g.db.users.find_one({"email": session["user"]["email"]})
        if not user or _account_removed(user):
            return refuse("Please sign in again.", 401)

        old_password = request.form.get("old_password") or ""
        # the pop-up names the field new_password, the old page called it password
        password = request.form.get("new_password") or request.form.get("password") or ""
        confirm_password = request.form.get("confirm_password") or ""

        if not user.get("password"):
            return refuse("This account signs in with Google, so it has no password to change.")
        if not old_password or not password or not confirm_password:
            return refuse("Please fill in all three password fields.")
        if not check_password_hash(user["password"], old_password):
            return refuse("Your current password is not correct.")
        if password != confirm_password:
            return refuse("The new passwords do not match.")
        weak = _password_problem(password)
        if weak:
            return refuse(weak)
        if check_password_hash(user["password"], password):
            return refuse("The new password must be different from your current one.")

        g.db.users.update_one(
            {"_id": user["_id"]},
            {"$set": {"password": generate_password_hash(password)}},
        )
        g.db.audit_logs.insert_one({
            "user_id": user["_id"],
            "name": user["email"],
            "role": user.get("role"),
            "action": "password-change",
            "changes": f"{user.get('name') or user['email']} changed their password.",
            "timestamp": datetime.now()
        })
        _notify_password_changed(user)

        if wants_json:
            return jsonify(success=True, message="Your password has been updated.")
        flash("Your password has been updated.", "popup")
        return redirect(url_for("index"))

    return render_template("reset_password.html")

# ---------------------------------------------------------------------------
# Donor leaderboard (the Ranking box on the donor profile)
# ---------------------------------------------------------------------------
LEADERBOARD_SIZE = 8
LEADERBOARD_PAGE = 50


def _public_name(user):
    """'Aisyah Rahman' -> 'Aisyah R.'  Other donors only ever see this, never a full name or an email."""
    parts = str(user.get("name") or "").split()
    if not parts:
        return "Donor"
    if len(parts) == 1:
        return parts[0][:24]
    return f"{parts[0][:24]} {parts[-1][0].upper()}."


def _rm_whole(cents):
    """RM 2,815 when it is a round number, RM 2,815.50 when it is not."""
    return f"{cents // 100:,}" if cents % 100 == 0 else _rm(cents)


def _short_rm(cents, unit_cents, places):
    """Cut (never round up) a total to `places` decimals of a unit, so a short figure is never larger than the real one."""
    whole, part = divmod(cents // (unit_cents // 10 ** places), 10 ** places)
    return f"{whole}.{part:0{places}d}".rstrip("0").rstrip(".")


def _rm_board(cents):
    """What a row prints: 2,815 / 2,815.50, and 1.23M once a total passes a million."""
    if cents >= 100_000_000:
        return _short_rm(cents, 100_000_000, 2) + "M"
    return _rm_whole(cents)


def _rm_podium(cents):
    """The podium cards are narrow, so they shorten earlier: 692.9K from RM 100,000 up."""
    if 10_000_000 <= cents < 100_000_000:
        return _short_rm(cents, 100_000, 1) + "K"
    return _rm_board(cents)


def _leaderboard(viewer):
    """Donors ranked by what they have given (the same total that sets their tier).

    Equal totals share a rank (1, 2, 2, 4). Removed accounts, NGOs and donors who have not given yet are left
    out. Strict Stable API safe: two aggregates in _donor_points plus one find, no distinct.
    """
    config = _reward_config()
    tiers = config["tiers"]
    points = _donor_points(None, config["equipment_credit_cents"])
    scored = {uid: p["points"] for uid, p in points.items() if p["points"] > 0}

    people = {}
    if scored:
        for person in g.db.users.find(
            {"_id": {"$in": list(scored)}, "role": "donor", "account_status": {"$ne": "removed"}},
            {"name": 1, "profile-pic": 1},
        ):
            people[person["_id"]] = person

    ordered = sorted(
        ((uid, cents) for uid, cents in scored.items() if uid in people),
        key=lambda row: (-row[1], _public_name(people[row[0]]).lower(), str(row[0])),
    )

    # the bars under the podium compare the rows with each other: the 4th place total is a full bar
    bar_top = ordered[3][1] if len(ordered) > 3 else 1
    ranked, rank, last = [], 0, None
    for place, (uid, cents) in enumerate(ordered, start=1):
        if cents != last:                       # a new total starts a new rank
            rank, last = place, cents
        tier = tiers[_tier_index(cents, tiers)]
        # rows lose their colour as they go down: 0 = full colour (4th place) ... 1 = plain grey (9th and below)
        fade = 1.0 if place > LEADERBOARD_SIZE else max(0.0, (place - 4) / (LEADERBOARD_SIZE - 3))
        ranked.append({
            "id": uid,
            "rank": rank,
            "cents": cents,
            "amount": _rm_board(cents),
            "amount_short": _rm_podium(cents),
            "amount_full": _rm_whole(cents),
            "share": min(100, max(4, round(cents * 100 / bar_top))),
            "fade": 0 if uid == viewer["_id"] else round(fade, 2),
            "name": _public_name(people[uid]),
            # other donors' pictures are used as stored; a legacy /static/uploads file is only migrated for the viewer
            "photo": people[uid].get("profile-pic") or "/static/assets/sample_profile.jpg",
            "tier": tier["name"],
            "tier_key": tier["key"],
            "you": uid == viewer["_id"],
        })

    mine_at = next((i for i, row in enumerate(ranked) if row["you"]), None)
    me = None
    if mine_at is not None:
        me = dict(ranked[mine_at])
        first_with_my_rank = next(i for i, row in enumerate(ranked) if row["rank"] == me["rank"])
        above = ranked[first_with_my_rank - 1] if first_with_my_rank else None
        ahead = sum(1 for row in ranked if row["cents"] < me["cents"])
        me.update({
            "ahead": ahead,
            "ahead_pct": ahead * 100 // len(ranked),        # "more than N% of donors": cut, never rounded up
            "tied_with": sum(1 for row in ranked if row["rank"] == me["rank"]) - 1,
            "gap": _rm_whole(above["cents"] - me["cents"]) if above else None,
            "gap_rank": above["rank"] if above else None,
        })

    return {
        "entries": ranked[:LEADERBOARD_SIZE],
        "all": ranked,
        "me": me,
        "me_outside": mine_at is not None and mine_at >= LEADERBOARD_SIZE,
        "total": len(ranked),
    }


@app.route("/ranking/rows")
def ranking_rows():
    """The next page of the full ranking as ready-made <li> rows. The "See full ranking" button asks for it."""
    session_user = session.get("user")
    if not session_user:
        return jsonify(error="Please sign in again."), 401
    viewer = g.db.users.find_one({"email": session_user["email"]})
    if not viewer or viewer.get("role") != "donor" or _account_removed(viewer):
        return jsonify(error="The ranking is for donors."), 403

    board = _leaderboard(viewer)
    start = max(LEADERBOARD_SIZE, request.args.get("offset", LEADERBOARD_SIZE, type=int))
    rows = board["all"][start:start + LEADERBOARD_PAGE]
    following = start + LEADERBOARD_PAGE
    return jsonify(
        html=render_template("ranking_rows.html", rows=rows, extra=True),
        next=following if following < board["total"] else None,
        total=board["total"],
        shown=start + len(rows),
        has_you=any(row["you"] for row in rows),
    )

# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------
@app.route("/profile")
def profile():
    if not session.get("user"):
        return redirect(url_for("login", next=request.path))

    if session['user']['role'] == "admin":
        return redirect(url_for("admin_dashboard"))

    user = g.db.users.find_one({"email": session['user']['email']})
    if user:
        _user_profile_image(user)
        if user and user.get("role") == "ngo":
            return render_template("ngo_profile.html",
                                   user=user,
                                   analytics=_ngo_analytics(user)
                                   )

    if user['role'] != "admin":
        streak_days = (datetime.now() - user['donor_details']['membership_streak']).days + 1
    else:
        streak_days = 0

    rewards = _rewards_view(user)
    return render_template("profile.html",
                           user=user,
                           streak=streak_days,
                           tier=rewards["tier"],
                           donated=rewards["funds"],
                           rewards=rewards,
                           ranking=_leaderboard(user))


@app.route("/edit-profile", methods=["GET", "POST"])
def edit_profile():
    if not session.get("user"):
        return redirect(url_for("login", next=request.path))

    if session['user']['role'] == "admin":
        return redirect(url_for("admin_dashboard"))

    user = g.db.users.find_one({"email": session['user']['email']})

    if user:
        _user_profile_image(user)


    if request.method == "POST":

        action = request.form.get("action")

        if action == "profile":
            name = request.form.get("name")
            email = request.form.get("email")
            address = request.form.get('address')
            phone_no = request.form.get('phone_no')

            DOB = ""
            state = ""

            if user['role'] == "donor":
                DOB = request.form.get("DOB")
                state = request.form.get('state')

                if state not in MALAYSIA_STATES:
                    flash("Please input a valid state.", "popup")
                    return redirect(url_for("edit_profile"))

            required_fields = [name, email, phone_no, address]

            if user['role'] == "donor":
                required_fields.extend([DOB, state])

            if not all(required_fields):
                flash("Please fill in all required fields.", "popup")
                return redirect(url_for("edit_profile"))

            profile_completed = all([name, email, phone_no, address])

            update_fields = {
                "name": name,
                "email": email,
                "phone_no": phone_no,
                "address": address,
                "profile_completed": profile_completed
            }
            if user["role"] == "donor":
                update_fields['donor_details.DOB'] = DOB
                update_fields['donor_details.state'] = state

            new_pic_url = None
            pic = request.files.get("profile-pic")
            if pic and pic.filename:
                extension = os.path.splitext(pic.filename)[1].lower()
                if extension not in ALLOWED_IMAGE_EXT:
                    flash("Use a JPG, PNG, or WebP profile picture.", "popup")
                    return redirect(url_for("edit_profile"))

                new_pic_url = _save_uploaded_image(pic)

            if new_pic_url:
                update_fields["profile-pic"] = new_pic_url


            new_bg_url = None
            pic = request.files.get("background-pic")
            if pic and pic.filename:
                extension = os.path.splitext(pic.filename)[1].lower()
                if extension not in ALLOWED_IMAGE_EXT:
                    flash("Use a JPG, PNG, or WebP for background image.", "popup")
                    return redirect(url_for("edit_profile"))

                new_bg_url = _save_uploaded_image(pic)

            if new_bg_url:
                update_fields["background-pic"] = new_bg_url

            g.db.users.update_one({"_id": user["_id"]}, {"$set": update_fields})

            if new_pic_url:
                old_pic = user.get("profile-pic") or ""
                _delete_stored_image(old_pic)

            if new_bg_url:
                old_pic = user.get("background-pic") or ""
                _delete_stored_image(old_pic)

            session["user"] = {**session["user"], "name": name, "email": email}

            flash("User profile completed", "popup")
            return redirect(url_for("profile"))

        elif action == "ngo":

            ngo_name = request.form.get("name", "").strip()
            _id = request.form.get("id", "").strip()
            phone_no = request.form.get("phone_no", "").strip()
            email = request.form.get("email", "").strip()
            instagram = request.form.get("instagram", "").strip()
            facebook = request.form.get("facebook", "").strip()
            tiktok = request.form.get("tiktok", "").strip()
            description = request.form.get("description", "").strip()
            address = request.form.get("address", "").strip()

            required_fields = [ngo_name, _id, email, phone_no, description, address]

            if not all(required_fields):
                flash("Please fill in all required NGO fields.", "popup")
                return redirect(url_for("edit_profile"))

            # The logo and cover photo are in this same form now (there is no separate admin card)
            logo = request.files.get("profile-pic")
            cover = request.files.get("background-pic")
            for upload, label in ((logo, "logo"), (cover, "cover photo")):
                if upload and upload.filename:
                    if os.path.splitext(upload.filename)[1].lower() not in ALLOWED_IMAGE_EXT:
                        flash(f"Use a JPG, PNG, or WebP for the {label}.", "popup")
                        return redirect(url_for("edit_profile"))

            update_fields = {
                "ngo_details.ngo_name": ngo_name,
                "ngo_details.registration_no": _id,
                "ngo_details.phone_no": phone_no,
                "ngo_details.email": email,
                "ngo_details.instagram": instagram,
                "ngo_details.facebook": facebook,
                "ngo_details.tiktok": tiktok,
                "ngo_details.description": description,
                "ngo_details.address": address,
                "ngo_details.profile_completed": True,
                # this one form is the whole profile now, and the sign-in check wants both flags
                "profile_completed": True
            }

            new_pic_url = _save_uploaded_image(logo) if logo and logo.filename else None
            new_bg_url = _save_uploaded_image(cover) if cover and cover.filename else None

            if new_pic_url:
                update_fields["profile-pic"] = new_pic_url
            if new_bg_url:
                update_fields["background-pic"] = new_bg_url

            g.db.users.update_one({"_id": user["_id"]}, {"$set": update_fields})

            if new_pic_url:
                _delete_stored_image(user.get("profile-pic") or "")
            if new_bg_url:
                _delete_stored_image(user.get("background-pic") or "")

            flash("NGO profile saved.", "popup")
            return redirect(url_for("profile"))

    return render_template("edit-profile.html",
                           user=user,
                           states=MALAYSIA_STATES)


@app.route("/admin-dashboard")
def admin_dashboard():
    if not session.get("user"):
        return redirect(url_for("login", next=request.path))

    if session['user']['role'] != "admin":
        return redirect(url_for("index"))

    available_years = sorted({
        r["_id"] for r in g.db.donations.aggregate([
            {"$match": {"date_posted": {"$type": "date"}}},
            {"$group": {"_id": {"$year": "$date_posted"}}},
        ])
    }, reverse=True)

    current_year = datetime.now().year
    if not available_years:
        available_years = [current_year]

    selected_year = request.args.get("year", type=int)
    if selected_year not in available_years:
        selected_year = available_years[0]

    user = g.db.users.find_one({"email": session["user"]["email"]})
    _user_profile_image(user)

    return render_template(
        "admin-dashboard.html",
        user=user,
        available_years=available_years,
        selected_year=selected_year,
        analytics=_admin_analytics(selected_year),
    )



# ---------------------------------------------------------------------------
# Admin: audit page and account removal
# ---------------------------------------------------------------------------
AUDIT_PAGE_SIZE = 20
AUDIT_VIEWS = ("accounts", "activity", "removed")
REMOVAL_REASON_MIN = 10
REMOVAL_REASON_MAX = 500


def _sign_in_method(user):
    provider = (user.get("provider") or "local").lower()
    return "Email and password" if provider == "local" else f"{provider.capitalize()} sign-in"


def _joined_at(user):
    """Accounts made before created_at existed fall back to when their row was created."""
    if user.get("created_at"):
        return user["created_at"]
    return user["_id"].generation_time.replace(tzinfo=None) + MALAYSIA_OFFSET


def _user_snapshot(user):
    """What we keep about an account when it is removed. Never the password hash."""
    donor = user.get("donor_details") or {}
    ngo = user.get("ngo_details") or {}
    return {
        "name": user.get("name"),
        "email": user.get("email"),
        "role": user.get("role"),
        "sign_in_method": _sign_in_method(user),
        "phone_no": user.get("phone_no"),
        "address": user.get("address"),
        "profile_completed": bool(user.get("profile_completed")),
        "joined": _joined_at(user).strftime("%d %b %Y"),
        "donor_state": donor.get("state"),
        "donor_tier": donor.get("tier"),
        "ngo_name": ngo.get("ngo_name"),
        "registration_no": ngo.get("registration_no"),
    }


def _stamp(moment):
    return moment.strftime("%d %b %Y, %H:%M") if moment else ""


def _day(moment):
    return moment.strftime("%d %b %Y") if moment else ""


@app.route("/admin-audit")
def admin_audit():
    viewer = session.get("user")
    if not viewer:
        return redirect(url_for("login", next=request.path))
    if viewer.get("role") != "admin":
        return redirect(url_for("index"))

    view = request.args.get("view", "accounts")
    if view not in AUDIT_VIEWS:
        view = "accounts"
    q = request.args.get("q", "").strip()[:80]
    role = request.args.get("role", "all")
    action = request.args.get("action", "all")
    page = max(1, request.args.get("page", 1, type=int))
    skip = (page - 1) * AUDIT_PAGE_SIZE
    pattern = {"$regex": re.escape(q), "$options": "i"} if q else None

    me = g.db.users.find_one({"email": viewer["email"]}) or {}

    # ---- account health, the numbers the analytics dashboard doesn't show ----
    live = {"account_status": {"$ne": "removed"}}
    by_role = {
        r["_id"]: r["n"] for r in g.db.users.aggregate([
            {"$match": live}, {"$group": {"_id": "$role", "n": {"$sum": 1}}},
        ])
    }
    kpis = {
        "accounts": sum(by_role.values()),
        "donors": by_role.get("donor", 0),
        "ngos": by_role.get("ngo", 0),
        "admins": by_role.get("admin", 0),
        # (aggregate, not distinct: the Atlas connection uses the strict Stable API, which has no distinct)
        "active_week": next(iter(g.db.audit_logs.aggregate([
            {"$match": {"action": "sign-in", "timestamp": {"$gte": datetime.now() - timedelta(days=7)}}},
            {"$group": {"_id": "$user_id"}},
            {"$group": {"_id": None, "n": {"$sum": 1}}},
        ])), {}).get("n", 0),
        "incomplete": g.db.users.count_documents({"$and": [
            live,
            {"role": {"$ne": "admin"}},
            {"$or": [
                {"profile_completed": {"$ne": True}},
                {"role": "ngo", "ngo_details.profile_completed": {"$ne": True}},
            ]},
        ]}),
        "removed": g.db.users.count_documents({"account_status": "removed"}),
    }

    rows, actions, total = [], [], 0

    if view == "accounts":
        query = {}
        if role in ("donor", "ngo", "admin"):
            query["role"] = role
        if pattern:
            query["$or"] = [{"name": pattern}, {"email": pattern}, {"ngo_details.ngo_name": pattern}]
        total = g.db.users.count_documents(query)
        users = list(g.db.users.find(query).sort("_id", -1).skip(skip).limit(AUDIT_PAGE_SIZE))

        logins = {
            r["_id"]: r for r in g.db.audit_logs.aggregate([
                {"$match": {"action": "sign-in", "user_id": {"$in": [u["_id"] for u in users]}}},
                {"$group": {"_id": "$user_id", "last": {"$max": "$timestamp"}, "count": {"$sum": 1}}},
            ])
        }

        # an NGO with money waiting to be paid out is worth knowing about before removing it
        unpaid_by_owner = {}
        ngo_emails = [u["email"] for u in users if u.get("role") == "ngo"]
        if ngo_emails:
            owner_of = {
                c["_id"]: c["owner_email"]
                for c in g.db.campaigns.find({"owner_email": {"$in": ngo_emails}}, {"owner_email": 1})
            }
            for r in g.db.donations.aggregate([
                {"$match": {"campaign_id": {"$in": list(owner_of)},
                            "payout_status": "unpaid", "donation_type": {"$ne": "equipment"}}},
                {"$group": {"_id": "$campaign_id", "cents": {"$sum": "$amount_cents"}}},
            ]):
                owner = owner_of.get(r["_id"])
                unpaid_by_owner[owner] = unpaid_by_owner.get(owner, 0) + int(r["cents"] or 0)

        for u in users:
            donor = u.get("donor_details") or {}
            ngo = u.get("ngo_details") or {}
            seen = logins.get(u["_id"]) or {}
            removed = _account_removed(u)
            rows.append({
                "id": str(u["_id"]),
                "name": u.get("name") or u.get("email"),
                "email": u.get("email"),
                "role": u.get("role"),
                "org": ngo.get("ngo_name"),
                "method": _sign_in_method(u),
                "has_password": bool(u.get("password")),
                "profile_completed": bool(u.get("profile_completed")) and (
                    u.get("role") != "ngo" or bool(ngo.get("profile_completed"))),
                "joined": _day(_joined_at(u)),
                "last_seen": _stamp(seen.get("last")),
                "sign_ins": seen.get("count", 0),
                "removed": removed,
                "can_remove": (not removed) and u.get("role") != "admin" and u["_id"] != me.get("_id"),
                "unpaid": _rm(unpaid_by_owner.get(u["email"], 0)) if u.get("role") == "ngo" else None,
                "details": [
                    ("Phone", u.get("phone_no")),
                    ("Address", u.get("address") or ngo.get("address")),
                    ("Date of birth", donor.get("DOB")),
                    ("State", donor.get("state")),
                    ("Membership tier", donor.get("tier")),
                    ("NGO registration no.", ngo.get("registration_no")),
                    ("NGO contact email", ngo.get("email")),
                    ("NGO phone", ngo.get("phone_no")),
                    ("Instagram", ngo.get("instagram")),
                    ("Facebook", ngo.get("facebook")),
                    ("TikTok", ngo.get("tiktok")),
                    ("Created by", u.get("created_by")),
                ],
            })

    else:
        query = {"action": "remove-user"} if view == "removed" else {}
        if view == "activity":
            actions = sorted(r["_id"] for r in g.db.audit_logs.aggregate([{"$group": {"_id": "$action"}}]) if r["_id"])
            if action != "all":
                query["action"] = action
        if pattern:
            query["$or"] = (
                [{"target_email": pattern}, {"target_name": pattern}, {"reason": pattern}]
                if view == "removed" else [{"name": pattern}, {"changes": pattern}]
            )
        total = g.db.audit_logs.count_documents(query)
        for log in g.db.audit_logs.find(query).sort("timestamp", -1).skip(skip).limit(AUDIT_PAGE_SIZE):
            rows.append({
                "when": _stamp(log.get("timestamp")),
                "actor": log.get("name"),
                "role": log.get("role"),
                "action": log.get("action"),
                "text": log.get("changes"),
                "proof_url": url_for("stored_proof", file_id=str(log["proof_id"])) if log.get("proof_id") else None,
                "target_name": log.get("target_name"),
                "target_email": log.get("target_email"),
                "target_role": log.get("target_role"),
                "reason": log.get("reason"),
                "hidden": log.get("campaigns_hidden", 0),
                "snapshot": [
                    (label, (log.get("snapshot") or {}).get(key))
                    for label, key in (
                        ("Sign-in method", "sign_in_method"), ("Joined", "joined"),
                        ("Phone", "phone_no"), ("Address", "address"),
                        ("State", "donor_state"), ("Tier", "donor_tier"),
                        ("NGO", "ngo_name"), ("Registration no.", "registration_no"),
                    )
                ],
            })

    return render_template(
        "admin_audit.html",
        user=me,
        view=view, q=q, role=role, action=action, actions=actions,
        rows=rows, total=total, kpis=kpis,
        page=page, pages=max(1, -(-total // AUDIT_PAGE_SIZE)),
        reason_min=REMOVAL_REASON_MIN, reason_max=REMOVAL_REASON_MAX,
    )


@app.route("/admin/users/<user_id>/remove", methods=["POST"])
def admin_remove_user(user_id):
    viewer = session.get("user")
    if not viewer:
        return redirect(url_for("login", next=url_for("admin_audit")))
    if viewer.get("role") != "admin":
        abort(403)

    return_to = request.form.get("return_to", "")
    back = return_to if return_to.startswith("/admin-audit") else url_for("admin_audit")

    if not ObjectId.is_valid(user_id):
        abort(404)
    target = g.db.users.find_one({"_id": ObjectId(user_id)})
    if target is None:
        abort(404)
    admin_user = g.db.users.find_one({"email": viewer["email"]})

    if admin_user and target["_id"] == admin_user["_id"]:
        flash("You can't remove your own account.", "popup")
        return redirect(back)
    if target.get("role") == "admin":
        flash("Admin accounts can't be removed here.", "popup")
        return redirect(back)
    if _account_removed(target):
        flash("That account has already been removed.", "popup")
        return redirect(back)

    reason = " ".join(request.form.get("reason", "").split())
    if len(reason) < REMOVAL_REASON_MIN:
        flash(f"Explain why you are removing this account (at least {REMOVAL_REASON_MIN} characters).", "popup")
        return redirect(back)
    if len(reason) > REMOVAL_REASON_MAX:
        flash(f"Keep the reason under {REMOVAL_REASON_MAX} characters.", "popup")
        return redirect(back)

    proof_id, proof_error = _save_proof(request.files.get("proof"), {
        "kind": "removal",
        "target_id": str(target["_id"]),
        "admin_email": viewer["email"],
    })
    if proof_error:
        flash(proof_error, "popup")
        return redirect(back)

    now = datetime.now()
    g.db.users.update_one({"_id": target["_id"]}, {"$set": {
        "account_status": "removed",
        "removed_at": now,
        "removed_by": viewer["email"],
        "removal_reason": reason,
        "removal_proof_id": proof_id,
    }})

    hidden = 0
    if target.get("role") == "ngo":
        # the NGO's fundraisers leave the public site with it (kept in the database)
        hidden = g.db.campaigns.update_many(
            {"owner_email": target["email"]}, {"$set": {"owner_removed": True}}
        ).modified_count

    g.db.audit_logs.insert_one({
        "user_id": admin_user["_id"] if admin_user else None,
        "name": viewer["email"],
        "role": "admin",
        "action": "remove-user",
        "changes": f"Removed {target.get('name') or target['email']} ({target['email']}, "
                   f"{target.get('role')}). Reason: {reason}",
        "target_user_id": target["_id"],
        "target_email": target["email"],
        "target_name": target.get("name"),
        "target_role": target.get("role"),
        "reason": reason,
        "proof_id": proof_id,
        "snapshot": _user_snapshot(target),
        "campaigns_hidden": hidden,
        "timestamp": now,
    })

    flash(f"{target.get('name') or target['email']} was removed and the action is in the audit log.", "popup")
    return redirect(url_for("admin_audit", view="removed"))

# ---------------------------------------------------------------------------
# Updates: one inbox per person
#
#   * An "event" is a personal notice: a donation arrived, a parcel was received, ...
#   * An "announcement" is written by an NGO or an admin for a group of people.
#
# Both live in the `updates` collection. Who may see one is decided by either
#   recipients      a list of user ids (every event, plus announcements to "my donors"
#                   and "one NGO" -- the list is fixed at the moment of sending), or
#   audience.type   everyone / donors / ngos, for an announcement aimed at a whole role.
# Whether *you* have read it is kept in `update_state`, one small document per person
# (a "read everything up to here" time plus a few exceptions), so sending to 5,000
# donors never writes 5,000 documents.
#
# Strict Stable API: only find / aggregate / count_documents / update_one / insert_one here.
# ---------------------------------------------------------------------------
import hmac

UPDATE_CATEGORIES = {
    "announcement": {"label": "Announcements", "icon": "fa-solid fa-bullhorn"},
    "donation":     {"label": "Donations",     "icon": "fa-solid fa-hand-holding-heart"},
    "parcel":       {"label": "Parcels",       "icon": "fa-solid fa-box-open"},
    "campaign":     {"label": "Campaigns",     "icon": "fa-solid fa-flag-checkered"},
    "reward":       {"label": "Rewards",       "icon": "fa-solid fa-award"},
    "account":      {"label": "Account",       "icon": "fa-solid fa-shield-halved"},
}
# The "Type" menu only lists what each role can actually receive.
UPDATE_TYPES_BY_ROLE = {
    "donor": ("announcement", "donation", "parcel", "campaign", "reward", "account"),
    "ngo":   ("announcement", "donation", "parcel", "campaign", "account"),
    "admin": ("announcement", "reward", "account"),
}
UPDATES_PAGE = 20                 # items per "Show older updates"
UPDATE_TITLE_MAX = 80
UPDATE_BODY_MAX = 1000
UPDATE_SEARCH_MAX = 60
UPDATE_IMAGE_MAX = 2 * 1024 * 1024
UPDATE_DAILY_LIMIT = {"ngo": 10, "admin": 30}    # announcements one account may send per 24 hours
UPDATE_KEEP_DAYS = 90             # personal events are deleted by MongoDB after this; announcements stay
UPDATE_EPOCH = datetime(1970, 1, 1)


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _updates_token():
    """Per-session token that every updates POST must send back (the app has no global CSRF check)."""
    if not session.get("upd_csrf"):
        session["upd_csrf"] = uuid.uuid4().hex
    return session["upd_csrf"]


def _updates_token_ok():
    sent = request.form.get("csrf") or request.headers.get("X-CSRF-Token") or ""
    return hmac.compare_digest(sent.encode(), (session.get("upd_csrf") or "-").encode())


def _updates_user():
    """The signed-in account (None when signed out or removed), looked up once per request."""
    cached = g.get("updates_user", False)
    if cached is not False:
        return cached
    viewer = session.get("user")
    user = g.db.users.find_one({"email": viewer["email"]}) if viewer else None
    if _account_removed(user):
        user = None
    g.updates_user = user
    return user


def _ngo_label(user):
    return (user.get("ngo_details") or {}).get("ngo_name") or user.get("name") or user.get("email")


def _plural(count, one, many):
    return f"{count} {one if count == 1 else many}"


# ---------- who can see what ----------
def _updates_scope(user):
    """Everything this person may see in their inbox (never what they sent themselves)."""
    kinds = {"donor": ["everyone", "donors"], "ngo": ["everyone", "ngos"]}.get(user.get("role"), [])
    # a broadcast to a whole role only reaches people who had already joined when it went out
    joined = user["_id"].generation_time.replace(tzinfo=None)
    return {
        "deleted": {"$ne": True},
        "sender_id": {"$ne": user["_id"]},
        "$or": [
            {"recipients": user["_id"]},
            {"audience.type": {"$in": kinds}, "created_at": {"$gte": joined}},
        ],
    }


def _updates_state(user):
    doc = g.db.update_state.find_one({"_id": user["_id"]}) or {}
    return {
        "upto": doc.get("read_upto") or UPDATE_EPOCH,
        "read": list(doc.get("read") or []),
        "unread": list(doc.get("unread") or []),
        "can_undo": bool(doc.get("prev")),
    }


def _unread_clause(state):
    # newer than the "read up to" time and not opened since, or older but marked unread again
    return {"$or": [
        {"created_at": {"$gt": state["upto"]}, "_id": {"$nin": state["read"]}},
        {"_id": {"$in": state["unread"]}},
    ]}


def _is_read(item, state):
    if item["_id"] in state["read"]:
        return True
    if item["_id"] in state["unread"]:
        return False
    return item["created_at"] <= state["upto"]


def _unread_count(user, state=None):
    state = state or _updates_state(user)
    return g.db.updates.count_documents({"$and": [_updates_scope(user), _unread_clause(state)]})


def _mark_update(user, update_id, read):
    item = g.db.updates.find_one({"_id": update_id}, {"created_at": 1})
    if item is None:
        return
    newer = item["created_at"] > _updates_state(user)["upto"]
    if read:
        change = {"$addToSet": {"read": update_id}} if newer else {"$pull": {"unread": update_id}}
    else:
        change = {"$pull": {"read": update_id}} if newer else {"$addToSet": {"unread": update_id}}
    g.db.update_state.update_one({"_id": user["_id"]}, change, upsert=True)


def _mark_all_updates(user):
    doc = g.db.update_state.find_one({"_id": user["_id"]}) or {}
    g.db.update_state.update_one({"_id": user["_id"]}, {"$set": {
        "read_upto": _utc_now(), "read": [], "unread": [],
        # kept so "Undo" can put everything back exactly as it was
        "prev": {"read_upto": doc.get("read_upto"), "read": doc.get("read") or [],
                 "unread": doc.get("unread") or []},
    }}, upsert=True)


def _undo_mark_all(user):
    doc = g.db.update_state.find_one({"_id": user["_id"]}) or {}
    prev = doc.get("prev")
    if not prev:
        return False
    g.db.update_state.update_one({"_id": user["_id"]}, {
        "$set": {"read_upto": prev.get("read_upto"), "read": prev.get("read") or [],
                 "unread": prev.get("unread") or []},
        "$unset": {"prev": ""},
    })
    return True


# ---------- writing personal events ----------
def _notify(user_ids, category, title, body="", link=None, cta=None):
    """Drop one personal notice into several inboxes at once. A failed notice must never break
    the donation, parcel or password change that caused it, so this swallows its own errors."""
    try:
        ids = [uid for uid in dict.fromkeys(user_ids or []) if uid]
        if not ids:
            return
        now = _utc_now()
        g.db.updates.insert_one({
            "kind": "event", "category": category,
            "title": title[:UPDATE_TITLE_MAX], "body": body[:UPDATE_BODY_MAX],
            "link": link, "cta": cta,
            "recipients": ids,
            "created_at": now,
            "expires_at": now + timedelta(days=UPDATE_KEEP_DAYS),
        })
    except Exception as error:
        print("Update notice failed:", error)


def _notify_admins(category, title, body="", link=None, cta=None):
    admins = g.db.users.find({"role": "admin", "account_status": {"$ne": "removed"}}, {"_id": 1})
    _notify([a["_id"] for a in admins], category, title, body, link, cta)


def _campaign_owner_id(campaign):
    if campaign.get("ngo_id"):
        return campaign["ngo_id"]
    owner = g.db.users.find_one({"email": campaign.get("owner_email")}, {"_id": 1})
    return owner["_id"] if owner else None


def _notify_funds_received(donor, campaign, amount_cents):
    """A payment went through: thank the donor, tell the NGO, and say so when it finishes the goal."""
    title = campaign.get("title") or "this fundraiser"
    amount = f"RM {amount_cents / 100:,.2f}"
    page = url_for("campaign_detail", campaign_id=str(campaign["_id"]))
    owner_id = _campaign_owner_id(campaign)
    _notify([donor["_id"]], "donation", "Donation received",
            f"Your {amount} gift to \"{title}\" went through. Thank you!",
            link=url_for("donation_history"), cta="See my donations")
    _notify([owner_id], "donation", f"{amount} received",
            f"{donor.get('name') or 'A donor'} gave {amount} to \"{title}\".",
            link=page, cta="See the fundraiser")
    # `campaign` was read before the "completed" status was written, so this is true exactly once
    if int(campaign.get("current_amount") or 0) >= int(campaign.get("amount_cents") or 0) > 0 \
            and campaign.get("status") != "completed":
        _notify(_donors_of([campaign["_id"]]), "campaign", "Fundraiser reached its goal",
                f"\"{title}\" has reached its goal. Thank you for helping it get there.",
                link=page, cta="See the fundraiser")
        _notify([owner_id], "campaign", "Your fundraiser reached its goal",
                f"\"{title}\" has reached its goal of RM {int(campaign.get('amount_cents') or 0) / 100:,.2f}.",
                link=page, cta="See the fundraiser")


def _notify_parcel_sent(donor, campaign, item, quantity):
    title = campaign.get("title") or "your campaign"
    _notify([donor["_id"]], "parcel", "Your parcel is on its way",
            f"{quantity} x {item} for \"{title}\". We will tell you when the NGO confirms it arrived.",
            link=url_for("donation_history", type="equipment"), cta="Follow my parcels")
    _notify([_campaign_owner_id(campaign)], "parcel", "A parcel is on its way",
            f"{donor.get('name') or 'A donor'} is sending {quantity} x {item} for \"{title}\". "
            "Mark it as received when it arrives.",
            link=url_for("ngo_equipment", campaign_id=str(campaign["_id"]), status="shipped"),
            cta="Open incoming parcels")


def _notify_parcel_result(donor, campaign, item, quantity, received):
    title = campaign.get("title") or "the campaign"
    ngo = g.db.users.find_one({"_id": _campaign_owner_id(campaign)}) or {}
    who = _ngo_label(ngo) if ngo else "The NGO"
    if received:
        _notify([donor["_id"]], "parcel", "Parcel received",
                f"{who} confirmed your {quantity} x {item} for \"{title}\" arrived. Thank you!",
                link=url_for("donation_history", type="equipment"), cta="See my parcels")
    else:
        _notify([donor["_id"]], "parcel", "Parcel not received",
                f"{who} marked your {quantity} x {item} for \"{title}\" as not received. "
                "The item counts as still needed.",
                link=url_for("donation_history", type="equipment"), cta="See my parcels")


def _notify_tier_up(user, tier):
    _notify([user["_id"]], "reward", f"You reached {tier['name']}",
            "New rewards are unlocked for you. Open Rewards on your profile to claim them.",
            link=url_for("profile") + "#rewards", cta="See my rewards")


def _notify_claim_handled(claim, fulfilled, note):
    title = claim.get("perk_title") or "your reward"
    if fulfilled:
        _notify([claim.get("user_id")], "reward", "Your reward is ready",
                f"\"{title}\": {note}", link=url_for("profile") + "#rewards", cta="See my rewards")
    else:
        _notify([claim.get("user_id")], "reward", "Reward claim declined",
                f"\"{title}\": {note}", link=url_for("profile") + "#rewards", cta="See my rewards")


def _notify_password_changed(user):
    _notify([user["_id"]], "account", "Your password was changed",
            "If this was not you, reset it from Forgot password and contact the MEDonate team.")


def _notify_campaign_live(campaign):
    _notify([_campaign_owner_id(campaign)], "campaign", "Your fundraiser is live",
            f"\"{campaign.get('title') or 'Your fundraiser'}\" is now public and can receive donations.",
            link=url_for("campaign_detail", campaign_id=str(campaign["_id"])), cta="See the fundraiser")


# ---------- who is in an audience ----------
def _donors_of(campaign_ids):
    """Donors who gave to any of these campaigns. A rejected parcel does not count as giving."""
    if not campaign_ids:
        return []
    given = [row["_id"] for row in g.db.donations.aggregate([
        {"$match": {"campaign_id": {"$in": list(campaign_ids)}, "status": {"$ne": "rejected"},
                    "user_id": {"$ne": None}}},
        {"$group": {"_id": "$user_id"}},
    ])]
    if not given:
        return []
    live = g.db.users.find({"_id": {"$in": given}, "role": "donor",
                            "account_status": {"$ne": "removed"}}, {"_id": 1})
    return [u["_id"] for u in live]


def _campaign_ids_of(ngo):
    return [c["_id"] for c in g.db.campaigns.find({"owner_email": ngo.get("email")}, {"_id": 1})]


def _role_count(roles):
    return g.db.users.count_documents({"role": {"$in": roles}, "account_status": {"$ne": "removed"}})


def _object_id(text):
    return ObjectId(text) if ObjectId.is_valid(text or "") else None


def _resolve_audience(user, source):
    """What the composer asked for -> (audience, recipients, error).
    `recipients` is a list of ids for a fixed group, or None when the whole role is meant.
    An NGO can only ever reach its own donors; the ids it sends are checked, never trusted."""
    kind = (source.get("audience") or "").strip()

    if user.get("role") == "ngo":
        if kind == "campaign":
            campaign = g.db.campaigns.find_one({"_id": _object_id(source.get("campaign_id")),
                                                "owner_email": user["email"]}, {"title": 1})
            if campaign is None:
                return None, None, "Choose one of your campaigns."
            ids = _donors_of([campaign["_id"]])
            audience = {"type": "ngo_donors", "ngo_id": user["_id"], "campaign_id": campaign["_id"],
                        "label": f"Donors to \"{campaign.get('title') or 'a campaign'}\""}
        elif kind in ("", "donors"):
            ids = _donors_of(_campaign_ids_of(user))
            audience = {"type": "ngo_donors", "ngo_id": user["_id"], "label": "All your donors"}
        else:
            return None, None, "Choose who should get this."
        audience["count"] = len(ids)
        return audience, ids, None

    if user.get("role") == "admin":
        if kind in ("everyone", "donors", "ngos"):
            roles = {"everyone": ["donor", "ngo"], "donors": ["donor"], "ngos": ["ngo"]}[kind]
            label = {"everyone": "Everyone (donors and NGOs)", "donors": "All donors", "ngos": "All NGOs"}[kind]
            return {"type": kind, "label": label, "count": _role_count(roles)}, None, None
        if kind in ("ngo_donors", "ngo"):
            ngo = g.db.users.find_one({"_id": _object_id(source.get("ngo_id")), "role": "ngo",
                                       "account_status": {"$ne": "removed"}})
            if ngo is None:
                return None, None, "Choose an NGO."
            if kind == "ngo":
                ids = [ngo["_id"]]
                label = f"{_ngo_label(ngo)} only"
            else:
                ids = _donors_of(_campaign_ids_of(ngo))
                label = f"Donors of {_ngo_label(ngo)}"
            return {"type": kind, "ngo_id": ngo["_id"], "label": label, "count": len(ids)}, ids, None
        return None, None, "Choose who should get this."

    return None, None, "Only NGOs and admins can send announcements."


def _compose_options(user):
    if user.get("role") == "ngo":
        campaigns = list(g.db.campaigns.find({"owner_email": user["email"], "confirmed": {"$ne": False}},
                                             {"title": 1}).sort("_id", -1))
        return {
            "campaigns": [{"id": str(c["_id"]), "title": c.get("title") or "Untitled"} for c in campaigns],
            "count": len(_donors_of(_campaign_ids_of(user))),
        }
    ngos = g.db.users.find({"role": "ngo", "account_status": {"$ne": "removed"}},
                           {"name": 1, "ngo_details.ngo_name": 1}).sort("name", 1)
    return {
        "ngos": [{"id": str(n["_id"]), "name": _ngo_label(n)} for n in ngos],
        "everyone": _role_count(["donor", "ngo"]),
    }


# ---------- reading the feed ----------
def _parse_day(text):
    try:
        day = datetime.strptime((text or "").strip(), "%Y-%m-%d")
    except ValueError:
        return None
    return day if 2000 <= day.year <= 2100 else None


def _filter_range(args):
    """From/To dates (Malaysia time, both included) -> a created_at filter in UTC, or None."""
    start, end = _parse_day(args.get("start")), _parse_day(args.get("end"))
    if start and end and start > end:
        start, end = end, start
    window = {}
    if start:
        window["$gte"] = start - MALAYSIA_OFFSET
    if end:
        window["$lt"] = end + timedelta(days=1) - MALAYSIA_OFFSET
    return window or None


def _updates_query(user, view, args, state):
    if view == "sent":
        parts = [{"sender_id": user["_id"], "kind": "announcement", "deleted": {"$ne": True}}]
    elif view == "all_sent":      # admins only: every announcement on the site, for moderation
        parts = [{"kind": "announcement", "deleted": {"$ne": True}}]
    else:
        parts = [_updates_scope(user)]
        if view == "unread":
            parts.append(_unread_clause(state))
        if args.get("cat") in UPDATE_TYPES_BY_ROLE.get(user.get("role"), ()):
            parts.append({"category": args["cat"]})
    q = (args.get("q") or "").strip()[:UPDATE_SEARCH_MAX]
    if q:
        pattern = {"$regex": re.escape(q), "$options": "i"}
        parts.append({"$or": [{"title": pattern}, {"body": pattern}, {"sender_name": pattern}]})
    window = _filter_range(args)
    if window:
        parts.append({"created_at": window})
    return {"$and": parts}


def _clock(moment):
    return f"{moment.hour % 12 or 12}:{moment.minute:02d} {'am' if moment.hour < 12 else 'pm'}"


def _day_label(day, today):
    gap = (today - day).days
    if gap == 0:
        return "Today"
    if gap == 1:
        return "Yesterday"
    if 1 < gap < 7:
        return day.strftime("%A")
    return f"{day.day} {day.strftime('%b')}" + ("" if day.year == today.year else f" {day.year}")


def _update_row(item, state, view, viewer, today):
    local = item["created_at"] + MALAYSIA_OFFSET
    category = item.get("category") if item.get("category") in UPDATE_CATEGORIES else "announcement"
    audience = item.get("audience") or {}
    sent_view = view in ("sent", "all_sent")
    sender_role = item.get("sender_role")
    row = {
        "id": str(item["_id"]),
        "kind": item.get("kind"),
        "category": category,
        "category_label": UPDATE_CATEGORIES[category]["label"],
        "icon": UPDATE_CATEGORIES[category]["icon"],
        "title": item.get("title") or "",
        "body": item.get("body") or "",
        "important": bool(item.get("important")),
        "image_url": item.get("image_url"),
        "go_url": url_for("update_go", update_id=str(item["_id"])) if item.get("link") else None,
        "cta": item.get("cta") or "Open",
        "time": _clock(local),
        "time_full": f"{local.day} {local.strftime('%b %Y')}, {_clock(local)}",
        "iso": local.strftime("%Y-%m-%dT%H:%M:00+08:00"),
        "day": local.strftime("%Y-%m-%d"),
        "day_label": _day_label(local.date(), today),
        "read": True if sent_view else _is_read(item, state),
        "sender_name": item.get("sender_name") or "MEDonate",
        "sender_role": sender_role,
        "official": sender_role == "admin",
        "sender_pic": item.get("sender_pic"),
        "sender_initial": (item.get("sender_name") or "M")[:1].upper(),
        "audience_label": audience.get("label"),
        "audience_count": audience.get("count"),
        "can_retract": item.get("kind") == "announcement" and (
            item.get("sender_id") == viewer["_id"] or viewer.get("role") == "admin"),
    }
    return row


def _updates_page(user, args):
    """Everything the page and the feed partial need. Same code for both, so they can't drift."""
    role = user.get("role")
    view = args.get("view", "all")
    allowed = ["all", "unread"] + (["sent"] if role in ("ngo", "admin") else []) + (["all_sent"] if role == "admin" else [])
    if view not in allowed:
        view = "all"
    page = min(500, max(1, args.get("page", 1, type=int)))

    state = _updates_state(user)
    query = _updates_query(user, view, args, state)
    docs = list(g.db.updates.find(query).sort([("created_at", -1), ("_id", -1)])
                .skip((page - 1) * UPDATES_PAGE).limit(UPDATES_PAGE + 1))
    more = len(docs) > UPDATES_PAGE
    today = (_utc_now() + MALAYSIA_OFFSET).date()
    rows = [_update_row(d, state, view, user, today) for d in docs[:UPDATES_PAGE]]

    groups = []
    after = args.get("after") if page > 1 else None      # the day the previous page ended on
    for row in rows:
        if groups and groups[-1]["day"] == row["day"]:
            groups[-1]["rows"].append(row)
        else:
            groups.append({"day": row["day"], "label": row["day_label"], "rows": [row],
                           "continued": not groups and row["day"] == after})

    keep = {k: args.get(k) for k in ("q", "start", "end", "cat") if args.get(k)}
    keep["view"] = view
    filtered = bool(keep.get("q") or keep.get("start") or keep.get("end") or keep.get("cat"))
    return {
        "view": view, "views": allowed, "groups": groups, "more": more,
        "more_url": url_for("updates_feed", page=page + 1, after=rows[-1]["day"] if rows else None, **keep) if more else None,
        "total": g.db.updates.count_documents(query) if page == 1 else None,
        "unread": _unread_count(user, state),
        "can_undo": state["can_undo"],
        "q": (args.get("q") or "").strip()[:UPDATE_SEARCH_MAX],
        "start": args.get("start") or "", "end": args.get("end") or "", "cat": args.get("cat") or "",
        "filtered": filtered,
        "types": [(key, UPDATE_CATEGORIES[key]["label"]) for key in UPDATE_TYPES_BY_ROLE.get(role, ())],
        "can_send": role in ("ngo", "admin"),
        "sent_view": view in ("sent", "all_sent"),
    }


@app.context_processor
def inject_updates():
    """The nav badge asks for the unread count itself, so pages that never show it never pay for it."""
    def updates_unread():
        try:
            user = _updates_user()
            return _unread_count(user) if user else 0
        except Exception:
            return 0
    return {"updates_unread": updates_unread, "updates_token": _updates_token}


def _updates_denied(json_reply, message="Please sign in again.", status=401):
    if json_reply:
        return jsonify(ok=False, message=message), status
    return redirect(url_for("login", next=request.path))


@app.route("/updates")
def updates():
    user = _updates_user()
    if not user:
        return _updates_denied(False)
    page = _updates_page(user, request.args)
    compose = _compose_options(user) if page["can_send"] else None
    return render_template("updates.html", p=page, compose=compose, role=user.get("role"),
                           limit=UPDATE_DAILY_LIMIT.get(user.get("role")),
                           title_max=UPDATE_TITLE_MAX, body_max=UPDATE_BODY_MAX)


@app.route("/updates/feed")
def updates_feed():
    user = _updates_user()
    if not user:
        return jsonify(ok=False, message="Please sign in again."), 401
    page = _updates_page(user, request.args)
    response = app.make_response(render_template("updates_feed.html", p=page))
    response.headers["X-Updates-Unread"] = str(page["unread"])
    response.headers["X-Updates-Total"] = str(page["total"] if page["total"] is not None else "")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/updates/unread")
def updates_unread_count():
    user = _updates_user()
    if not user:
        return jsonify(unread=0), 401
    response = jsonify(unread=_unread_count(user))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/updates/mark", methods=["POST"])
def updates_mark():
    user = _updates_user()
    if not user:
        return _updates_denied(True)
    if not _updates_token_ok():
        return jsonify(ok=False, message="This page is out of date. Reload it and try again."), 400
    to = request.form.get("to")
    if to == "all":
        _mark_all_updates(user)
    elif to == "undo":
        _undo_mark_all(user)
    elif to in ("read", "unread"):
        update_id = _object_id(request.form.get("id"))
        if update_id is None:
            return jsonify(ok=False, message="Unknown update."), 404
        _mark_update(user, update_id, to == "read")
    else:
        return jsonify(ok=False, message="Unknown request."), 400
    return jsonify(ok=True, unread=_unread_count(user))


@app.route("/updates/<update_id>/go")
def update_go(update_id):
    """Opening an update's link counts as reading it. Only site-internal links are followed."""
    user = _updates_user()
    if not user:
        return _updates_denied(False)
    oid = _object_id(update_id)
    item = g.db.updates.find_one({"$and": [_updates_scope(user), {"_id": oid}]}) if oid else None
    if item is None:
        abort(404)
    _mark_update(user, item["_id"], True)
    link = item.get("link") or ""
    if link.startswith("/") and not link.startswith("//"):
        return redirect(link)
    return redirect(url_for("updates"))


@app.route("/updates/audience")
def updates_audience():
    user = _updates_user()
    if not user:
        return _updates_denied(True)
    if user.get("role") not in ("ngo", "admin"):
        return jsonify(ok=False, message="Not allowed."), 403
    audience, _ids, error = _resolve_audience(user, request.args)
    if error:
        return jsonify(ok=False, message=error)
    return jsonify(ok=True, count=audience["count"], label=audience["label"])


@app.route("/updates/send", methods=["POST"])
def updates_send():
    user = _updates_user()
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if not user:
        return _updates_denied(wants_json)
    role = user.get("role")
    if role not in ("ngo", "admin"):
        abort(403)

    def refuse(message, status=400):
        if wants_json:
            return jsonify(ok=False, message=message), status
        flash(message, "popup")
        return redirect(url_for("updates"))

    if not _updates_token_ok():
        return refuse("This page is out of date. Reload it and try again.")

    headline = " ".join((request.form.get("subject") or "").split())
    message = _clean_body(request.form.get("message"))
    if not headline:
        return refuse("Add a title.")
    if len(headline) > UPDATE_TITLE_MAX:
        return refuse(f"Keep the title to {UPDATE_TITLE_MAX} characters.")
    if not message:
        return refuse("Write the message.")
    if len(message) > UPDATE_BODY_MAX:
        return refuse(f"Keep the message to {UPDATE_BODY_MAX} characters.")

    audience, recipients, error = _resolve_audience(user, request.form)
    if error:
        return refuse(error)
    if audience["count"] == 0:
        return refuse("No one is in that group yet." if role == "ngo"
                      else "No one matches that group yet.")

    limit = UPDATE_DAILY_LIMIT[role]
    since = _utc_now() - timedelta(days=1)
    if g.db.updates.count_documents({"sender_id": user["_id"], "kind": "announcement",
                                     "created_at": {"$gt": since}}) >= limit:
        return refuse(f"You have sent {limit} announcements in the last 24 hours. Try again tomorrow.")

    photo = request.files.get("photo")
    if photo and photo.filename:
        extension = os.path.splitext(secure_filename(photo.filename))[1].lower()
        head = photo.stream.read(16)
        photo.stream.seek(0, 2)
        size = photo.stream.tell()
        photo.stream.seek(0)
        if extension not in ALLOWED_IMAGE_EXT or _sniff_proof_type(head) not in (
                "image/jpeg", "image/png", "image/webp"):
            return refuse("The photo must be a JPG, PNG or WebP image.")
        if size > UPDATE_IMAGE_MAX:
            return refuse("The photo must be under 2 MB.")
        image_url = _save_uploaded_image(photo)
    else:
        image_url = None

    picture = user.get("profile-pic")
    now = _utc_now()
    doc = {
        "kind": "announcement", "category": "announcement",
        "title": headline, "body": message,
        "important": request.form.get("important") == "1",
        "image_url": image_url,
        "sender_id": user["_id"], "sender_role": role,
        "sender_name": "MEDonate team" if role == "admin" else _ngo_label(user),
        "sender_pic": picture if picture and picture != DEFAULT_PROFILE_PIC else None,
        "audience": audience, "created_at": now,
    }
    if recipients is not None:
        doc["recipients"] = recipients
    g.db.updates.insert_one(doc)

    count = audience["count"]
    g.db.audit_logs.insert_one({
        "user_id": user["_id"], "name": user["email"], "role": role,
        "action": "announcement-sent",
        "changes": f"{user.get('name') or user['email']} sent \"{headline}\" to "
                   f"{audience['label']} ({_plural(count, 'person', 'people')}).",
        "timestamp": datetime.now(),
    })
    flash(f"Sent to {_plural(count, 'person', 'people')}.", "popup")
    target = url_for("updates", view="sent")
    return jsonify(ok=True, redirect=target) if wants_json else redirect(target)


def _clean_body(text):
    lines = [line.rstrip() for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").strip().split("\n")]
    kept = []
    for line in lines:
        if not line and kept and not kept[-1]:
            continue            # never more than one blank line in a row
        kept.append(line)
    return "\n".join(kept)


@app.route("/updates/<update_id>/retract", methods=["POST"])
def updates_retract(update_id):
    user = _updates_user()
    if not user:
        return _updates_denied(True)
    if not _updates_token_ok():
        return jsonify(ok=False, message="This page is out of date. Reload it and try again."), 400
    oid = _object_id(update_id)
    item = g.db.updates.find_one({"_id": oid, "kind": "announcement", "deleted": {"$ne": True}}) if oid else None
    if item is None:
        return jsonify(ok=False, message="That announcement no longer exists."), 404
    if item.get("sender_id") != user["_id"] and user.get("role") != "admin":
        return jsonify(ok=False, message="You can only retract your own announcements."), 403

    g.db.updates.update_one({"_id": oid}, {"$set": {
        "deleted": True, "deleted_at": _utc_now(), "deleted_by": user["email"], "image_url": None,
    }})
    if item.get("image_url"):
        _delete_stored_image(item["image_url"])
    g.db.audit_logs.insert_one({
        "user_id": user["_id"], "name": user["email"], "role": user.get("role"),
        "action": "announcement-retracted",
        "changes": f"{user.get('name') or user['email']} retracted \"{item.get('title')}\" "
                   f"(sent by {item.get('sender_name')}).",
        "timestamp": datetime.now(),
    })
    return jsonify(ok=True)

# ---------------------------------------------------------------------------
# Force new users to complete their profile before they may continue.
# ---------------------------------------------------------------------------
# Pages exempt from forced redirection to edit profile
PROFILE_EXEMPT_ENDPOINTS = {
    "edit_profile", "logout", "static",
    "login", "google_login", "authorize",
    "reset_password"
}

@app.before_request
def require_complete_profile():
    user_session = session.get("user")
    if not user_session:
        return  # not logged in -- per-route checks handle this

    if request.endpoint in ("static", "logout"):
        return

    user = g.db.users.find_one({"email": user_session["email"]})

    if _account_removed(user):
        # an admin removed this account while they were signed in
        session.clear()
        flash(REMOVED_ACCOUNT_MESSAGE, "popup")
        return redirect(url_for("index"))

    if request.endpoint in PROFILE_EXEMPT_ENDPOINTS:
        return

    if not user:
        session.clear()
        return redirect(url_for("login"))

    elif user.get("role") == "ngo":
        ngo_details = user.get("ngo_details", {})

        if (
                not user.get("profile_completed", False)
                or not ngo_details.get("profile_completed", False)
        ):
            flash("Please complete both profile sections." , "popup")
            return redirect(url_for("edit_profile"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True)