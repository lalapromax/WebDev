import os
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

#Twitter OAuth Config
oauth.register(
    name='twitter',
    client_id=os.getenv("TWITTER_CLIENT_ID"),
    client_secret=os.getenv("TWITTER_CLIENT_SECRET"),
    request_token_url='https://api.twitter.com/oauth/request_token',
    request_token_params=None,
    access_token_url='https://api.twitter.com/oauth/access_token',
    access_token_params=None,
    authorize_url='https://api.twitter.com/oauth/authenticate',
    authorize_params=None,
    api_base_url='https://api.twitter.com/1.1/',
    client_kwargs=None,
)

# Facebook OAuth Config
FACEBOOK_CLIENT_ID = os.environ.get('FACEBOOK_CLIENT_ID')
FACEBOOK_CLIENT_SECRET = os.environ.get('FACEBOOK_CLIENT_SECRET')
oauth.register(
    name='facebook',
    client_id=FACEBOOK_CLIENT_ID,
    client_secret=FACEBOOK_CLIENT_SECRET,
    access_token_url='https://graph.facebook.com/oauth/access_token',
    access_token_params=None,
    authorize_url='https://www.facebook.com/dialog/oauth',
    authorize_params=None,
    api_base_url='https://graph.facebook.com/',
    client_kwargs={'scope': 'email'},
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
# Basic pages
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


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
        {"confirmed": {"$ne": False}},
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
        owner = owners.get(owner_email, {})
        organizer = owner.get("name") or owner_email or "Fundraiser organizer"
        target_cents = max(0, int(campaign.get("amount_cents", 0) or 0))
        raised_cents = max(0, int(campaign.get("current_amount", 0) or 0))  # after
        progress_percent = raised_cents / target_cents * 100 if target_cents else 0
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
            "status" : campaign.get('status'),
            "created_at": campaign.get("created_at"),
            "tags": _fundraiser_tags(progress_percent, campaign.get("created_at")),
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
        recent_fundraisers=fundraisers[:5],
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

        if password != confirm_password:
            flash("Passwords do not match.", "popup")
            return url_for("register")

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


@app.route("/register-ngo", methods=["GET", "POST"])
def register_ngo():
    # Role is stored inside session['user'], not at the top level of the session
    if (session.get('user') or {}).get('role') != 'admin':
        return redirect(url_for('index'))

    if request.method == "POST":

        email = request.form.get("email", "").strip()
        name = request.form.get("name")
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        ngo_name = request.form.get("ngo_name")
        instagram = request.form.get("instagram")
        facebook = request.form.get('facebook')
        tiktok = request.form.get('tiktok')
        description = request.form.get('description')

        # Validation
        if not email or not name or not password:
            flash("Name, email, and password are required.", "popup")
            return redirect(url_for("register_ngo"))

        if password != confirm_password:
            flash("Passwords do not match.", "popup")
            return redirect(url_for("register_ngo"))

        users = g.db.users

        # Check if existing user
        existing_user = users.find_one({"email": email})
        if existing_user:
            flash("An account with this email already exists.", "popup")
            return redirect(url_for("register_ngo"))

        users.insert_one({
            "email": email,
            "name": name,
            "password": generate_password_hash(password),
            "role": "ngo",
            "address": None,
            "phone_no": None,
            "profile-pic": None,
            "provider": "local",
            "profile_completed": False,

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



    return render_template('register-ngo.html')

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
    """needed_items as [{"name", "quantity"}] rows, for the edit form and the campaign pages.

    Campaigns saved before quantities existed hold plain names, which read as quantity 1.
    """
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

    retained_image_urls = [] if existing_campaign and replace_all_images else existing_image_urls

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

    for image in images:
        extension = os.path.splitext(image.filename)[1].lower()
        if extension not in ALLOWED_IMAGE_EXT:
            return render_form("Use JPG, PNG, or WebP cover photos.")

    image_urls = []
    for image in images:
        image_urls.append(_save_uploaded_image(image))

    user = g.db.users.find_one({"email": session['user']['email']})
    campaign_data = {
        "ngo_id" : user['_id'],
        "title": title,
        "description": description,
        "amount_cents": int(amount * 100),
        "youtube_url": youtube_url or None,
        "image_url": (retained_image_urls + image_urls)[0] if retained_image_urls or image_urls else None,
        "image_urls": retained_image_urls + image_urls,
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
        if replace_all_images:
            for image_url in existing_image_urls:
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
    owner_name = owner.get("name") or campaign.get("owner_email", "Fundraiser organizer")
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
        is_campaign_owner=is_campaign_owner,
        can_confirm_campaign=can_confirm_campaign,
        _campaign_id = _campaign_id,
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

    campaign = g.db.campaigns.find_one({
        "_id": ObjectId(campaign_id),
        "confirmed": {"$ne": False},
    })
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



    return redirect(url_for("campaign"))


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

COMMENT_MAX_LENGTH = 500
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
        }},
        {"$group": {"_id": None, "total": {"$sum": "$quantity"}}},
    ])), None)
    return result["total"] if result else 0


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

    user = g.db.users.find_one({"email": campaign["owner_email"]}) or {}

    ngo = g.db.users.find_one({"_id": campaign['ngo_id']})
    del_address = user.get('address')

    needed_items = campaign.get("needed_items") or []

    # Distinct item names, in their original order, for the dropdown.
    item_choices = list(dict.fromkeys(_item_name(e) for e in needed_items if _item_name(e)))

    if request.method == "POST":
        action = request.form.get("action")
    #
    # #-----------DONATE FUNDS-------------#
    #     if action == "funds":
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

        # # -----------DONATE EQUIPMENT-------------#
        # elif action == "equipment":
        #     item_name = (request.form.get("item") or "").strip()
        #
        #     try:
        #         quantity = int(request.form.get("equipment_amount"))
        #         if quantity <= 0:
        #             raise ValueError
        #     except (TypeError, ValueError):
        #         flash("Enter a valid quantity.", "popup")
        #         return redirect(url_for("campaign_donate", campaign_id=campaign_id))
        #
        #     # 1. The item has to be one this campaign actually asked for.
        #     if item_name not in item_choices:
        #         flash("Select one of the items this campaign actually needs.", "popup")
        #         return redirect(url_for("campaign_donate", campaign_id=campaign_id))
        #
        #     # 2. Can't give more of it than is still needed.
        #     needed_amount = _item_needed_amount(needed_items, item_name)
        #     already_given = _item_already_given(campaign_id, item_name)
        #
        #     if already_given + quantity > needed_amount:
        #         remaining = max(0, needed_amount - already_given)
        #         flash(
        #             f"Only {remaining} more \"{item_name}\" needed -- lower the quantity."
        #             if remaining else
        #             f"\"{item_name}\" has already been fully donated -- thank you!",
        #             "popup",
        #         )
        #         return redirect(url_for("campaign_donate", campaign_id=campaign_id))
        #
        #     donor = g.db.users.find_one({"email": session["user"]["email"]})
        #     g.db.donations.insert_one({
        #         "user_id": donor["_id"],
        #         "campaign_id": ObjectId(campaign_id),
        #         "donation_type": "equipment",
        #         "item": item_name,
        #         "quantity": quantity,
        #         "date_posted": datetime.now(timezone.utc),
        #     })
        #
        #     flash(f"Thank you for donating {quantity} x {item_name}!", "popup")
        #     return redirect(url_for("campaign_detail", campaign_id=campaign_id))

    image_urls = _campaign_image_urls(campaign)
    target_cents = max(0, int(campaign.get("amount_cents", 0) or 0))
    raised_cents = max(0, int(campaign.get("current_amount", 0) or 0))
    progress_percent = min(100, raised_cents / target_cents * 100) if target_cents else 0

    return render_template("campaign_donate.html",
                           stripe_publishable_key=STRIPE_PUBLISHABLE_KEY,
                           del_address=del_address,
                           item_choices=item_choices,
                           campaign=campaign,
                           cover_image=image_urls[0] if image_urls else None,
                           owner_name=user.get("name") or campaign.get("owner_email"),
                           owner_profile=_user_profile_image(user) if user.get("_id") else None,
                           raised_amount=f"{raised_cents / 100:,.2f}",
                           target_amount=f"{target_cents / 100:,.2f}",
                           progress_percent=progress_percent)


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
                        "payment_method" : 'stripe',
                        "checkout_session_id": checkout_session.id,
                        "payout_status": "unpaid"
                    })

                    session.pop("campaign_details", None)
                    return redirect(url_for("campaign_detail", campaign_id=campaign_id) + "#donor-messages")

                else:
                    flash("Payment received -- we're confirming it now.", "popup")

        except stripe.error.StripeError:
            flash("Error Occurred.", "popup")

    return redirect(url_for("index"))



# ---------------------------------------------------------------------------
# NGO analytics
# ---------------------------------------------------------------------------
def _rm(cents):
    return f"{cents / 100:,.2f}"


def _percent(part, whole):
    return min(100, round(part / whole * 100)) if whole else 0


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
    months = [_month_start(now, offset) for offset in range(-5, 1)]  # last 6 months
    slot_of = {(m.year, m.month): i for i, m in enumerate(months)}

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

    # ---- one pass over this NGO's donations (projection keeps it light) ----
    monthly_cents = [0] * len(months)
    new_donors = [0] * len(months)
    returning_donors = [0] * len(months)
    month_donors = [set() for _ in months]
    donors = {}                 # user_id -> [number of gifts, total cents]
    items_by_campaign = {}      # campaign_id -> items received
    raised_by_campaign = {}

    for donation in g.db.donations.find(
        {"campaign_id": {"$in": [c["_id"] for c in campaigns]}},
        {"user_id": 1, "campaign_id": 1, "amount_cents": 1,
         "donation_type": 1, "quantity": 1, "date_posted": 1},
    ).sort("_id", 1):
        when = _donation_time(donation)
        slot = slot_of.get((when.year, when.month))
        campaign_id = donation.get("campaign_id")

        if donation.get("donation_type") == "equipment":
            items_by_campaign[campaign_id] = (
                items_by_campaign.get(campaign_id, 0) + int(donation.get("quantity") or 1)
            )
            cents = 0
        else:
            cents = int(donation.get("amount_cents") or 0)
            if slot is not None:
                monthly_cents[slot] += cents

            raised_by_campaign[campaign_id] = raised_by_campaign.get(campaign_id, 0) + cents


        stats = donors.setdefault(donation.get("user_id"), [0, 0])
        is_new_donor = stats[0] == 0
        stats[0] += 1
        stats[1] += cents

        # Count each donor once per month: "new" the month they first gave,
        # "returning" every later month they gave again.
        if slot is not None and donation.get("user_id") not in month_donors[slot]:
            month_donors[slot].add(donation.get("user_id"))
            (new_donors if is_new_donor else returning_donors)[slot] += 1

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

    donor_count = len(donors)
    repeat_donors = sum(1 for gifts, _ in donors.values() if gifts > 1)
    remaining_cents = max(0, goal_cents - raised_cents)

    return {
        "raised": _rm(raised_cents),
        "goal": _rm(goal_cents),
        "remaining": _rm(remaining_cents),
        "goal_percent": _percent(raised_cents, goal_cents),
        "has_goal": goal_cents > 0,
        "has_funds": any(monthly_cents),
        "has_donors": donor_count > 0,
        "donor_count": donor_count,
        "new_this_month": new_donors[-1],
        "repeat_percent": _percent(repeat_donors, donor_count),
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
            "months": [m.strftime("%b %y") for m in months],
            "funds": [round(c / 100, 2) for c in monthly_cents],
            "new_donors": new_donors,
            "returning_donors": returning_donors,
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
             "donation_type": 1, "quantity": 1, "item": 1, "date_posted": 1},
    ).sort("_id", 1):
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

#Twitter
@app.route('/login/twitter')
def twitter_login():

    redirect_uri = url_for('twitter_auth', _external=True)
    return oauth.twitter.authorize_redirect(redirect_uri)

@app.route('/twitter/auth/')
def twitter_auth():
    token = oauth.twitter.authorize_access_token()
    resp = oauth.twitter.get('account/verify_credentials.json')
    user_info = resp.json()

    if not user_info:
        flash("Twitter login failed.", "popup")
        return redirect(url_for('login'))

    name = user_info.get('name')
    email = user_info.get('email')
    picture_url = user_info.get('profile_image_url_https')

    if not email:
        flash("Email sharing not activated on twitter's authentication.", "popup")
        return redirect(url_for("login"))

    login_or_register_oauth_user("twitter", name, email, picture_url)
    return redirect('/')


@app.route('/login/facebook/')
def facebook_login():

    redirect_uri = url_for('facebook_auth', _external=True)
    return oauth.facebook.authorize_redirect(redirect_uri)

@app.route('/facebook/auth/')
def facebook_auth():

    token = oauth.facebook.authorize_access_token()
    resp = oauth.facebook.get(
        'https://graph.facebook.com/me?fields=id,name,email,picture{url}')
    profile = resp.json()

    if not profile:
        flash("Facebook login failed.", "popup")
        return redirect(url_for('login'))

    name = profile.get('name')
    email = profile.get('email')
    picture_url = (profile.get('picture') or {}).get('data', {}).get('url')

    if not email:
        flash("Facebook didn't share an email with us -- try a different sign-in method.", "popup")
        return redirect(url_for('login'))

    login_or_register_oauth_user("facebook", name, email, picture_url)
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

@app.route("/forget-password", methods=["GET", "POST"])
def forget_password():
    if request.method == "POST":

        action = request.form.get("action")

        # ----------Send OTP-----------#
        if action == "send_otp":

            email = request.form.get("email")
            users = g.db.users

            recipient = users.find_one({"email": email})

            if not recipient:
                flash("Account with that email does not exist.", "popup")
                return redirect(url_for('forget_password'))

            # Generate OTP
            otp = pyotp.random_base32()
            totp = pyotp.TOTP(otp, interval=60)

            generated_otp = totp.now()
            print(generated_otp)  # debug -- REMOVE LATER

            # Store information in session
            session["reset_email"] = email
            session["reset_otp_secret"] = otp

            print(session['reset_email'])
            # Send email
            subject = "MEDonate - Forget Password OTP"
            body = (
                f"""
            Hello {recipient['name']},

            This is your one-time-password to login to your MEDonate account.

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

            except Exception as e:
                print("Email error:", e)
                flash("Failed to send OTP.", "popup")

        # ----------Verify OTP-----------#
        elif action == "verify_otp":

            entered_otp = request.form.get("otp")

            otp_secret = session.get("reset_otp_secret")

            if not otp_secret:
                flash("Please request an OTP first.", "popup")
                return redirect(url_for("forget_password"))

            totp = pyotp.TOTP(otp_secret, interval=60)

            if totp.verify(entered_otp):
                # Correct OTP
                session["otp_verified"] = True
                flash("OTP verified successfully.", "popup")

                return redirect(url_for("reset_password"))

            else:
                flash("Invalid or expired OTP.", "popup")

                return redirect(url_for("forget_password"))

    return render_template('forget_password.html')



@app.route("/reset-password", methods=['POST', 'GET'])
def reset_password():
    # NOTE: this condition is inverted from what you probably intend
    # (see notes) -- left exactly as in your originals.
    if not session.get("otp_verified") and session.get("reset_email"):
        target_email = session["reset_email"]
    elif session.get("user"):
        target_email = session["user"]["email"]
    else:
        flash("Access not allowed.", "popup")
        return redirect(url_for("forget_password"))

    if request.method == "POST":

        user = g.db.users.find_one({"email": target_email})

        old_password = request.form.get("old_password")
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")

        if not password or not confirm_password:
            flash("Please enter password and password confirmation.", "popup")
            return redirect(url_for('reset_password'))

        if password != confirm_password:
            flash("Passwords do not match.", "popup")
            return redirect(url_for('reset_password'))

        if password != confirm_password:
            flash("Passwords do not match.", "popup")
            return redirect(url_for('reset_password'))


        password_hash = generate_password_hash(password)
        users = g.db.users

        users.update_one(
            {"email": target_email},
            {'$set': {"password": password_hash}},
        )

        session.pop("reset_email", None)
        session.pop("reset_otp_secret", None)
        session.pop("otp_verified", None)

        flash("Password reset successful. Please log in.", "popup")
        return redirect(url_for("login"))

    return render_template("reset_password.html")


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

    return render_template("profile.html",
                           user=user,
                           streak=streak_days)


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

            profile_completed = all([
                ngo_name, _id, phone_no, email, description, address
            ])

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
                "ngo_details.profile_completed": profile_completed
            }

            g.db.users.update_one({"_id": user["_id"]}, {"$set": update_fields})

            updated_user = g.db.users.find_one({"_id": user["_id"]})

            user_profile_complete = updated_user.get("profile_completed", False)
            ngo_profile_complete = updated_user.get("ngo_details", {}).get("profile_completed", False)  # was "ngo.details" — never matched

            if user_profile_complete and ngo_profile_complete:

                flash("Both profiles are complete!", "popup")
                return redirect(url_for("profile"))

            flash("NGO profile updated.", "popup")
            return redirect(url_for("edit_profile"))

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

    if request.endpoint in PROFILE_EXEMPT_ENDPOINTS:
        return

    user = g.db.users.find_one({"email": user_session["email"]})

    if not user:
        session.clear()
        return redirect(url_for("login"))

    # if user.get("role") == "donor":
    #      if user.get("profile_completed", False):
    #          flash("Please complete your profile.", "popup")
    #          return redirect(url_for("edit_profile"))

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
    session.pop('user', None)
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True)