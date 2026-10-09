import os
import uuid
import atexit
from io import BytesIO
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlparse



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
app.secret_key = os.environ.get('MED_SECRET', 'dev-secret-key')
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

# ---------------------------------------------------------------------------
# Google OAuth
# ---------------------------------------------------------------------------
oauth = OAuth(app)

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
                "phone-no": '0123456789',
                "profile-pic": DEFAULT_PROFILE_PIC,
                "role": "admin",
                "provider": "local",

            })
            print("Default admin created")

        print("Admin account exists")

    seed_admin()

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


@app.route("/contact")
def contact():
    return render_template("contact.html")


@app.route("/campaign")
def campaign():
    campaigns = list(g.db.campaigns.find(
        {"confirmed": {"$ne": False}},
        {
            "_id": 1,
            "title": 1,
            "amount_cents": 1,
            "raised_cents": 1,
            "owner_email": 1,
            "image_urls": 1,
            "image_url": 1,
            "keywords": 1,
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
        raised_cents = max(0, int(campaign.get("raised_cents", 0) or 0))
        fundraisers.append({
            "id": str(campaign["_id"]),
            "title": campaign.get("title") or "Untitled fundraiser",
            "organizer": organizer,
            "organizer_initial": organizer[:1].upper(),
            "organizer_image": owner.get("profile-pic"),
            "goal": target_cents / 100,
            "progress_percent": raised_cents / target_cents * 100 if target_cents else 0,
            "image_url": image_urls[0] if image_urls else None,
            "keywords": campaign.get("keywords") or [],
            "created_at": campaign.get("created_at"),
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
            flash("Name, email, and password are required.")
            return redirect(url_for("register"))

        if password != confirm_password:
            flash("Passwords do not match.")
            return redirect(url_for("register"))

        if password != confirm_password:
            flash("Passwords do not match.")
            return url_for("register")

        users = g.db.users

        # Check if existing user
        existing_user = users.find_one({"email": email})
        if existing_user:
            flash("An account with this email already exists.")
            return redirect(url_for("register"))

        users.insert_one({
            "email": email,
            "name": name,
            "password": generate_password_hash(password),
            "address": None,
            "phone-no": None,
            "profile-pic": None,
            "role": "donor",
            "provider": "local",
            "profile_completed": False,

            "donor_details": {
                "DOB": None,
                "state": None,
                "membership_streak": datetime.now(),
                "tier": "Bronze"
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

        flash("Registration successful! Please log in.")
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
            flash("Name, email, and password are required.")
            return redirect(url_for("register_ngo"))

        if password != confirm_password:
            flash("Passwords do not match.")
            return redirect(url_for("register_ngo"))

        users = g.db.users

        # Check if existing user
        existing_user = users.find_one({"email": email})
        if existing_user:
            flash("An account with this email already exists.")
            return redirect(url_for("register_ngo"))

        users.insert_one({
            "email": email,
            "name": name,
            "password": generate_password_hash(password),
            "role": "ngo",
            "address": None,
            "phone-no": None,
            "profile-pic": None,
            "provider": "local",
            "profile_completed": False,

            'ngo_details': {
                "ngo_name": ngo_name,
                "instagram": instagram,
                "facebook": facebook,
                "tiktok": tiktok,
                "description": description
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

        flash("Registration successful! Please log in.")
        return redirect(url_for("index"))

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
                form_needed_items="",
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
            form_needed_items="\n".join(existing_campaign.get("needed_items") or []),
            form_needs_equipment=bool(existing_campaign.get("needed_items")),
            form_keywords=", ".join(existing_campaign.get("keywords") or []),
        )

    if not session.get("user"):
        return redirect(url_for("login", next=request.path))

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
            form_needed_items=request.form.get("neededItems", ""),
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
    raw_needed_items = request.form.get("neededItems", "") if needs_equipment else ""
    raw_keywords = request.form.get("keywords", "").strip()
    keywords = []
    seen_keywords = set()
    for keyword in raw_keywords.split(","):
        keyword = keyword.strip()
        normalized_keyword = keyword.casefold()
        if keyword and normalized_keyword not in seen_keywords:
            seen_keywords.add(normalized_keyword)
            keywords.append(keyword)
    needed_items = list(dict.fromkeys(
        item.strip()
        for item in raw_needed_items.splitlines()
        if item.strip()
    ))
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

    if (
        len(raw_needed_items) > 2000
        or len(needed_items) > 20
        or any(len(item) > 100 for item in needed_items)
    ):
        return render_form("List up to 20 equipment items, with no more than 100 characters per item.")

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

    campaign_data = {
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
                "$unset": {"confirmed_at": ""},
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

    flash("Campaign details saved.")
    return redirect(url_for("campaign_preview", campaign_id=str(saved_campaign_id)))


def _render_campaign_page(campaign, preview_mode, _campaign_id):
    image_urls = _campaign_image_urls(campaign)
    target_cents = max(0, int(campaign.get("amount_cents", 0) or 0))
    raised_cents = (
        round(target_cents / 2)
        if preview_mode
        else max(0, int(campaign.get("raised_cents", 0) or 0))
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
        needed_items=campaign.get("needed_items") or [],
        is_campaign_owner=is_campaign_owner,
        can_confirm_campaign=can_confirm_campaign,
        _campaign_id = _campaign_id
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

    return _render_campaign_page(campaign, preview_mode=True)


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
        flash("Fundraiser created successfully.")

    return redirect(url_for("campaign"))


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

def create_comment(campaign_id, comment, amount_cents):
    if not ObjectId.is_valid(campaign_id):
        abort(404)

    campaign = g.db.campaigns.find_one({"_id": ObjectId(campaign_id)})
    if campaign is None:
        abort(404)

    if comment is not None:
        user = g.db.users.find_one({"email": session['user']['email']})

        g.db.comments.insert_one({
            "campaign_id": campaign_id,
            "name": user['name'],
            "profile-pic": user.get('profile-pic'),
            "donation_amount": amount_cents,
            "date_posted": datetime.now()
        })

# ---------------------------------------------------------------------------
# Donations / Stripe
# ---------------------------------------------------------------------------
@app.route("/campaign-donate/<campaign_id>", methods=["GET","POST"])
def campaign_donate(campaign_id):
    if not session.get("user"):
        return redirect(url_for("login", next=request.path))

    if not ObjectId.is_valid(campaign_id):
        abort(404)

    if request.method == "POST":
        campaign = g.db.campaigns.find_one({"_id": ObjectId(campaign_id)})
        if not campaign:
            flash("No campaign found.")
            return redirect(url_for("index"))

        try:
            amount = Decimal(request.form.get("amount"))
            comment = request.form.get("comment")
            if (
                    not amount.is_finite()
                    or amount <= 0
                    or amount * 100 != (amount * 100).to_integral_value()
            ):
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            flash("Enter a valid donation amount with up to two decimal places.")
            return redirect(url_for("campaign_detail", campaign_id=campaign_id))

        amount_cents = int(amount * 100)
        create_comment(campaign_id, comment, amount_cents)


        checkout_session = stripe.checkout.Session.create(

            ui_mode="embedded_page",
            customer_email=session["user"]["email"],
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


            # Whatever you put in metadata survives the redirect to Stripe and
            # comes back to you in the webhook event below -- so nothing here
            # needs to be trusted from the browser again after this point.
            metadata={
                "campaign_id": campaign_id,
                "donor_name": session["user"]["name"],
                "donor_email": session["user"]["email"],
                "comment" : comment
            },
            return_url=url_for("donation_success", _external=True) + "?session_id={CHECKOUT_SESSION_ID}",

        )

        return jsonify(clientSecret=checkout_session.client_secret)

    return render_template("campaign_donate.html", stripe_publishable_key=STRIPE_PUBLISHABLE_KEY)


@app.route("/donate/success")
def donation_success():
    # Return donation success message.
    session_id = request.args.get("session_id")
    if session_id:
        try:
            checkout_session = stripe.checkout.Session.retrieve(session_id)
            if checkout_session.payment_status == "paid":
                flash("Thank you for your donation!")
            else:
                flash("Payment received -- we're confirming it now.")
        except stripe.error.StripeError:
            flash("Error Occured.")
    return redirect(url_for("index"))


@app.route("/stripe-webhook", methods=["POST"])
def stripe_webhook():
    # Called by Stripe's servers directly, not by a logged-in user --
    # no session check here, and exempt this route from CSRF protection
    # if you have Flask-WTF's CSRFProtect enabled globally.
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        return "", 400  # bad payload or bad signature -- reject it

    if event["type"] == "checkout.session.completed":
        checkout_session = event["data"]["object"]
        metadata = checkout_session.get("metadata", {})
        campaign_id = metadata.get("campaign_id", "")
        if not ObjectId.is_valid(campaign_id):
            return "", 200  # nothing we can attribute this to
        amount_cents = checkout_session["amount_total"]  # what Stripe actually charged
        anonymous = metadata.get("anonymous") == "true"

        # Stripe retries webhooks that don't get a fast 200 response, so this
        # write has to be idempotent -- otherwise a retry double-counts a donation.
        result = g.db.donations.update_one(
            {"stripe_session_id": checkout_session["id"]},
            {
                "$setOnInsert": {
                    "campaign_id": campaign_id,
                    "stripe_session_id": checkout_session["id"],
                    "stripe_payment_intent": checkout_session.get("payment_intent"),
                    "donor_name": None if anonymous else metadata.get("donor_name"),
                    "donor_email": metadata.get("donor_email"),
                    "amount_cents": amount_cents,
                    "message": metadata.get("message") or None,
                    "anonymous": anonymous,
                    "created_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )

        if result.upserted_id is not None:
            # Only the first time we see this session -- a retry will match
            # the existing doc and skip this, so totals stay correct.
            g.db.campaigns.update_one(
                {"_id": ObjectId(campaign_id)},
                {"$inc": {"raised_cents": amount_cents, "donor_count": 1}},
            )

    return "", 200


def get_top_donors(campaign_id, limit=3):
    return list(g.db.donations.aggregate([
        {"$match": {"campaign_id": campaign_id}},
        {"$sort": {"created_at": 1}},
        {
            "$group": {
                "_id": "$donor_email",  # group by account, not display name
                "total_cents": {"$sum": "$amount_cents"},
                "donor_name": {"$last": "$donor_name"},
                "anonymous": {"$last": "$anonymous"},
            }
        },
        {"$sort": {"total_cents": -1}},
        {"$limit": limit},
    ]))


# --- Embedded checkout experiment (from the newer version) ---
@app.route("/payment")
def payment():
    return render_template(
        "payment.html",
        stripe_publishable_key=STRIPE_PUBLISHABLE_KEY,
    )


SAMPLE_ORDER = {
    "reference": "ORD-1234",
    "email": "abc@gmail.com",
    "fundraiser": "John Fundraiser's Funds for Funding Funds",
    "amount": 30000,  # in sen: RM300.00
    "currency": "myr",
}


@app.route('/checkout', methods=["POST", "GET"])
def checkout():
    return render_template('checkout.html')


@app.route("/create-checkout-session", methods=["POST"])
def create_checkout_session():
    try:
        # Named checkout_session so it doesn't shadow Flask's `session`
        checkout_session = client.v1.checkout.sessions.create(params={
            'ui_mode': 'embedded_page',
            'line_items': [
                {
                    # Provide the exact Price ID (for example, price_1234) of the product you want to sell
                    'price': 'price_1UNkxJRgUauqJgQUyqNOTOF9',
                    'quantity': 1,
                },
            ],
            'mode': 'payment',
            'return_url': DOMAIN + '/return.html?session_id={CHECKOUT_SESSION_ID}',
        })
    except Exception as e:
        return str(e)

    return jsonify(clientSecret=checkout_session.client_secret)


@app.route('/session-status', methods=['GET'])
def session_status():
    checkout_session = client.v1.checkout.sessions.retrieve(request.args.get('session_id'))

    return jsonify(status=checkout_session.status, customer_email=checkout_session.customer_details.email)


# ---------------------------------------------------------------------------
# Auth: Google sign-in, login, password reset
# ---------------------------------------------------------------------------
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
        flash("Google login failed.")
        return redirect(url_for('login'))

    name = user_info.get('name')
    email = user_info.get('email')
    picture_url = user_info.get('picture')

    # Find an existing user with that email
    users = g.db.users
    existing_user = users.find_one({"email": email})

    if not existing_user:

        users.insert_one({
            "email": email,
            "name": name,
            "password": None,  # Google doesn't provide password
            "address": None,
            "phone-no": None,
            "profile-pic": picture_url,
            "role": "donor",
            "provider": "google",
            "profile_completed": False,

            "donor_details": {
                "DOB": None,
                "state": None,
                "membership_streak": datetime.now(),
                "tier": "Bronze",
            }
        })
        session['user'] = {
            "name": name,
            "email": email,
            "role": "donor"
        }

        return redirect('/')

    session['user'] = {
        "name": existing_user.get("name", existing_user["email"]),
        "email": email,
        "role": existing_user.get("role")
    }

    return redirect('/')


@app.route("/login", methods=["POST", "GET"])
def login():
    if session.get('user'):
        next_url = request.args.get("next", "")
        if next_url.startswith("/") and not next_url.startswith("//") and "\\" not in next_url:
            return redirect(next_url)
        return redirect(url_for('index'))

    if request.method == "POST":
        email = request.form.get('email')
        password = request.form.get('password')

        if not email or not password:
            flash("Email and password required.")
            return redirect(url_for('login'))

        users = g.db.users
        existing_user = users.find_one({"email": email})

        if existing_user and not existing_user.get("password"):
            flash("This account uses Google sign-in. Please log in with Google.")
            return redirect(url_for('index'))

        elif existing_user and check_password_hash(existing_user["password"], password):
            session['user'] = {
                "name": existing_user.get("name", existing_user["email"]),
                "email": email,
                "role": existing_user.get("role")
            }

            user = g.db.users.find_one({"email": session['user']['email']})
            g.db.audit_logs.insert_one({
                "user_id": user['_id'],
                "name": user['email'],
                "role": user['role'],
                "action": "sign-in",
                "changes": f"{user['name']} signed in.",
                "timestamp": datetime.now()
            })

            next_url = request.args.get("next", "")
            if next_url.startswith("/") and not next_url.startswith("//") and "\\" not in next_url:
                return redirect(next_url)

            return redirect(url_for('index'))
        else:
            flash("Invalid credentials")
            return redirect(url_for('index'))

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
                flash("Account with that email does not exist.")
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

                flash("OTP has been sent to your email.")

            except Exception as e:
                print("Email error:", e)
                flash("Failed to send OTP.")

        # ----------Verify OTP-----------#
        elif action == "verify_otp":

            entered_otp = request.form.get("otp")

            otp_secret = session.get("reset_otp_secret")

            if not otp_secret:
                flash("Please request an OTP first.")
                return redirect(url_for("forget_password"))

            totp = pyotp.TOTP(otp_secret, interval=60)

            if totp.verify(entered_otp):
                # Correct OTP
                session["otp_verified"] = True
                flash("OTP verified successfully.")

                return redirect(url_for("reset_password"))

            else:
                flash("Invalid or expired OTP.")

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
        flash("Access not allowed.")
        return redirect(url_for("forget_password"))

    if request.method == "POST":

        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")

        if not password or not confirm_password:
            flash("Please enter password and password confirmation.")
            return redirect(url_for('reset_password'))

        if password != confirm_password:
            flash("Passwords do not match.")
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

        flash("Password reset successful. Please log in.")
        return redirect(url_for("login"))

    return render_template("reset_password.html")


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------
@app.route("/profile")
def profile():
    if not session.get("user"):
        return redirect(url_for("login", next=request.path))

    user = g.db.users.find_one({"email": session['user']['email']})
    if user:
        _user_profile_image(user)
    streak_days = (datetime.now() - user['donor_details']['membership_streak']).days + 1

    return render_template("profile.html",
                           user=user,
                           streak=streak_days)


@app.route("/edit-profile", methods=["GET", "POST"])
def edit_profile():
    if not session.get("user"):
        return redirect(url_for("login", next=request.path))

    user = g.db.users.find_one({"email": session['user']['email']})
    if user:
        _user_profile_image(user)

    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        phone_no = request.form.get('phone-no')
        DOB = request.form.get("DOB")
        state = request.form.get('state')
        address = request.form.get('address')

        if state not in MALAYSIA_STATES:
            flash("Please input a valid state.")
            return redirect(url_for("edit_profile"))

        # Handle the uploaded profile picture (if one was chosen)
        new_pic_url = None
        pic = request.files.get("profile-pic")
        if pic and pic.filename:
            extension = os.path.splitext(pic.filename)[1].lower()
            if extension not in ALLOWED_IMAGE_EXT:
                flash("Use a JPG, PNG, or WebP profile picture.")
                return redirect(url_for("edit_profile"))

            new_pic_url = _save_uploaded_image(pic)

        update_fields = {
            "name": name,
            "email": email,
            "phone-no": phone_no,
            "address": address,
            "donor_details.DOB": DOB,
            "donor_details.state": state
        }

        # Only touch profile-pic if a new one was uploaded
        if new_pic_url:
            update_fields["profile-pic"] = new_pic_url

        # Check that all fields are filled in, then mark the profile as complete
        if all([phone_no, DOB, state, address]):
            update_fields['profile_completed'] = True

        g.db.users.update_one({"_id": user["_id"]}, {"$set": update_fields})

        if new_pic_url:
            old_pic = user.get("profile-pic") or ""
            _delete_stored_image(old_pic)

        session["user"] = {**session["user"], "name": name, "email": email}

        flash("Profile updated.")
        return redirect(url_for("profile"))

    return render_template("edit-profile.html",
                           user=user,
                           states=MALAYSIA_STATES)


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
    if user and not user.get("profile_completed", False):
        flash("Please complete your profile to continue.")
        return redirect(url_for("edit_profile"))


@app.route("/logout")
def logout():
    session.pop('user', None)
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True)