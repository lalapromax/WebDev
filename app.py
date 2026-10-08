import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import requests
import stripe

from dotenv import load_dotenv
from flask import Flask, g, render_template, request, redirect, url_for, flash, session, jsonify, abort
from pymongo import MongoClient
from pymongo.server_api import ServerApi
from bson import ObjectId
from requests.exceptions import RequestException

from urllib.parse import parse_qs, urlparse

#For Forget Password OTP
from flask_mail import Mail, Message
import pyotp

import atexit
import certifi
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from authlib.integrations.flask_client import OAuth

ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
IMAGE_FIELDS = ("profile-pic", "background-pic")

MALAYSIA_STATES = [
    "Johor", "Kedah", "Kelantan", "Kuala Lumpur", "Labuan", "Malacca",
    "Negeri Sembilan", "Pahang", "Penang", "Perak", "Perlis",
    "Putrajaya", "Sabah", "Sarawak", "Selangor", "Terengganu",
]

#STANDARDISE DNS TO 8.8.8.8 FOR DEBUGGING!! REMOVE LATER
import dns.resolver
dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
dns.resolver.default_resolver.nameservers = ['8.8.8.8', '1.1.1.1']


app = Flask(__name__)
app.secret_key = os.environ.get('MED_SECRET', 'dev-secret-key')
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

oauth = OAuth(app)

oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

#loads content of env file
load_dotenv()

#Server Information
MONGODB_URI = os.environ.get("MONGODB_URI")
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "app_db")

#Google Auth Information
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", None)
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", None)
GOOGLE_DISCOVERY_URL = (
    "https://accounts.google.com/.well-known/openid-configuration"
)
cid = os.environ.get("GOOGLE_CLIENT_ID", "")
secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
print("CLIENT ID length:", len(cid), "| stripped matches original:", cid == cid.strip())
print("SECRET length:", len(secret), "| stripped matches original:", secret == secret.strip())
print("ID ends correctly:", cid.endswith(".apps.googleusercontent.com"))
print("Secret starts with GOCSPX-:", secret.startswith("GOCSPX-"))

#HitPay service configuration
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", None)
stripe.api_version = "2026-08-26.dahlia; custom_checkout_payment_form_preview=v1"
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY",None)
STRIPE_WEBHOOK_SECRET = os.environ["STRIPE_WEBHOOK_SECRET"]

client = stripe.StripeClient(stripe.api_key)


#Mailing Service Information
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USE_SSL'] = False
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')

mail = Mail(app)

ca = certifi.where() #Secure connection

# Create the client ONCE, at startup — not per request
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

atexit.register(app.mongo_client.close)

def seed_admin():
    #Insert default admin user if not present.

    admin_exists = g.db.users.find_one({"name": "admin"})

    if not admin_exists:
        hashed = generate_password_hash('admin123')

        g.db.users.insert_one({
            "email" : "admin@gmail.com",
            "name" : "admin",
            "password" : hashed,
            "address": None,
            "phone-no": None,
            "profile-pic": None,
            "role": "admin",
            "provider":"local",
        })


@app.before_request
def attach_db():
    g.db = app.mongo_client["medonate"]


# --- routes ---
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/contact")
def contact():
    return render_template("contact.html")


@app.route("/register", methods =["GET", "POST"])
def register():

    if session.get('user'):
        return redirect(url_for('index'))

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        name = request.form.get("name")
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        #Validation
        if not email or not name or not password:
            flash("Name, email, and password are required.")
            return url_for("register")

        if password != confirm_password:
            flash("Passwords do not match.")
            return url_for("register")

        users = g.db.users

        #Check if existing user
        existing_user = users.find_one({"email": email})
        if existing_user:
            flash("An account with this email already exists.")
            return redirect(url_for("register"))

        users.insert_one({
            "email" : email,
            "name" : name,
            "password" : generate_password_hash(password),
            "address": None,
            "phone-no": None,
            "profile-pic": None,
            "role": "donor",
            "provider":"local",
            "profile_completed": False,

            "donor_details" : {
                "DOB" : None,
                "state": None,
                "membership_streak" : datetime.now(),
                "tier": "Bronze"

            }

        })

        user = users.find_one({"email" : email})

        g.db.audit_logs.insert_one({
            "user_id": user['_id'],
            "name" : user['email'],
            "role" : user['role'],
            "action": "create",
            "changes": f"{user['name']} account created.",
            "timestamp" : datetime.now()
        })



        flash("Registration successful! Please log in.")
        return redirect(url_for("index"))

    return render_template("register.html")

@app.route("/register-ngo", methods=["GET", "POST"])
def register_ngo():

    if not session.get('role') == 'admin':
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

        #Validation
        if not email or not name or not password:
            flash("Name, email, and password are required.")
            return url_for("register")

        if password != confirm_password:
            flash("Passwords do not match.")
            return url_for("register")



        users = g.db.users

        #Check if existing user
        existing_user = users.find_one({"email": email})
        if existing_user:
            flash("An account with this email already exists.")
            return redirect(url_for("register"))

        users.insert_one({
            "email" : email,
            "name" : name,
            "password" : generate_password_hash(password),
            "role" : "ngo",
            "address": None,
            "phone-no": None,
            "profile-pic": None,
            "provider": "local",
            "profile_completed": False,

            'ngo_details': {
            "ngo_name" : ngo_name,
            "instagram" : instagram,
            "facebook" : facebook,
            "tiktok" : tiktok,
            "description" : description
            }
        })

        user = users.find_one({"email" : email})

        g.db.audit_logs.insert_one({
            "user_id": user['_id'],
            "name" : user['email'],
            "role" : user['role'],
            "action": "create",
            "changes": f"{user['name']} account created.",
            "timestamp" : datetime.now()
        })

        flash("Registration successful! Please log in.")
        return redirect(url_for("index"))

    return render_template('register-ngo.html')


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

        existing_image_urls = existing_campaign.get("image_urls") or (
            [existing_campaign["image_url"]] if existing_campaign.get("image_url") else []
        )
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
        existing_image_urls = existing_campaign.get("image_urls") or (
            [existing_campaign["image_url"]] if existing_campaign.get("image_url") else []
        )
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
            error=error,
        )

    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    youtube_url = request.form.get("youtubeLink", "").strip()
    custom_url = request.form.get("customUrl", "").strip().lower()
    needs_equipment = request.form.get("needsEquipment") == "on"
    raw_needed_items = request.form.get("neededItems", "") if needs_equipment else ""
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
        extension = os.path.splitext(image.filename)[1].lower()
        filename = secure_filename(f"{uuid.uuid4().hex}{extension}")
        upload_folder = os.path.join(app.root_path, "static", "uploads")
        os.makedirs(upload_folder, exist_ok=True)
        image.save(os.path.join(upload_folder, filename))
        image_urls.append(url_for("static", filename=f"uploads/{filename}"))

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
                if image_url.startswith("/static/uploads/"):
                    filename = os.path.basename(image_url)
                    upload_path = os.path.join(app.root_path, "static", "uploads", filename)
                    if os.path.isfile(upload_path):
                        os.remove(upload_path)
        saved_campaign_id = existing_campaign["_id"]
    else:
        campaign_data["created_at"] = datetime.now(timezone.utc)
        campaign_data["confirmed"] = False
        result = g.db.campaigns.insert_one(campaign_data)
        saved_campaign_id = result.inserted_id

    flash("Campaign details saved.")
    return redirect(url_for("campaign_preview", campaign_id=str(saved_campaign_id)))


def _render_campaign_page(campaign, preview_mode):
    image_urls = campaign.get("image_urls") or (
        [campaign["image_url"]] if campaign.get("image_url") else []
    )
    target_cents = max(0, int(campaign.get("amount_cents", 0) or 0))
    raised_cents = (
        round(target_cents / 2)
        if preview_mode
        else max(0, int(campaign.get("raised_cents", 0) or 0))
    )
    progress_percent = min(100, raised_cents / target_cents * 100) if target_cents else 0
    owner = g.db.users.find_one({"email": campaign.get("owner_email")}) or {}
    owner_name = owner.get("name") or campaign.get("owner_email", "Fundraiser organizer")
    current_user = session.get("user") or {}
    is_campaign_owner = current_user.get("email") == campaign.get("owner_email")
    can_confirm_campaign = (
        preview_mode and is_campaign_owner and campaign.get("confirmed") is not True
    )

    return render_template(
        "campaign-preview.html",
        campaign=campaign,
        preview_mode=preview_mode,
        image_urls=image_urls,
        owner_name=owner_name,
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

    return _render_campaign_page(campaign, preview_mode=False)


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

    return redirect(url_for("campaign_preview", campaign_id=campaign_id))




# ---------------------------------------------------------------------------
# Donations / Stripe
# ---------------------------------------------------------------------------
# User donates to a campaign
@app.route("/campaign-donate/<campaign_id>", methods=["POST"])
def campaign_donate(campaign_id):
    if not session.get("user"):
        return redirect(url_for("login", next=request.path))

    if not ObjectId.is_valid(campaign_id):
        abort(404)

    campaign = g.db.campaigns.find_one({"_id": ObjectId(campaign_id)})
    if not campaign:
        flash("No campaign found.")
        return redirect(url_for("index"))

    try:
        # amount = Decimal(request.form.get("amount", ""))
        amount = Decimal("500.00")  # TODO: placeholder, read from the form
        if (
                not amount.is_finite()
                or amount <= 0
                or amount * 100 != (amount * 100).to_integral_value()
        ):
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        flash("Enter a valid donation amount with up to two decimal places.")
        return redirect(url_for("campaign_detail", campaign_id=campaign_id))

    message = request.form.get("message", "").strip()[:500]
    anonymous = request.form.get("anonymous") == "on"
    amount_cents = int(amount * 100)

    checkout_session = stripe.checkout.Session.create(
        mode="payment",
        payment_method_types=["card"],
        customer_email=session["user"]["email"],
        line_items=[{
            "price_data": {
                "currency": "myr",
                "unit_amount": amount_cents,
                "product_data": {
                    "name": f"Donation to {campaign['title']}",
                },
            },
            "quantity": 1,
        }],
        # Whatever you put in metadata survives the redirect to Stripe and
        # comes back to you in the webhook event below -- so nothing here
        # needs to be trusted from the browser again after this point.
        metadata={
            "campaign_id": campaign_id,
            "donor_name": "" if anonymous else session["user"]["name"],
            "donor_email": session["user"]["email"],
            "message": message,
            "anonymous": "true" if anonymous else "false",
        },
        success_url=url_for("donation_success", _external=True) + "?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=url_for("campaign_detail", campaign_id=campaign_id, _external=True),
    )

    return redirect(checkout_session.url, code=303)


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
            flash("Thanks! We're confirming your payment.")
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
                "_id": "$donor_email",          # group by account, not display name
                "total_cents": {"$sum": "$amount_cents"},
                "donor_name": {"$last": "$donor_name"},
                "anonymous": {"$last": "$anonymous"},
            }
        },
        {"$sort": {"total_cents": -1}},
        {"$limit": limit},
    ]))



# Google sign-in authentication
@app.route("/login/google")
def google_login():
    redirect_uri = url_for('authorize', _external=True)
    print("REDIRECT URI:", redirect_uri)  # debug

    return oauth.google.authorize_redirect(redirect_uri, prompt='select_account')


#Check
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

    #Find an existing user with that email
    users = g.db.users
    existing_user = users.find_one({"email": email})

    if not existing_user:

        users.insert_one({

            "email" : email,
            "name" : name,
            "password": None, #Google doesn't provide password
            "address": None,
            "phone-no": None,
            "profile-pic": picture_url,
            "role": "donor",
            "provider": "google",
            "profile_completed" : False,

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

    elif existing_user:
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
        existing_user = users.find_one({"email" : email})

        if existing_user and not existing_user.get("password"):
            flash("This account uses Google sign-in. Please log in with Google.")
            return redirect(url_for('index'))

        elif existing_user and check_password_hash(existing_user["password"], password):
            session['user'] = {
                "name": existing_user.get("name", existing_user["email"]),
                "email": email,
                "role" : existing_user.get("role")
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

            #Generate OTP
            otp = pyotp.random_base32()
            totp = pyotp.TOTP(otp, interval=60)

            generated_otp = totp.now()
            print(generated_otp)

            # Store information in session
            session["reset_email"] = email
            session["reset_otp_secret"] = otp

            print(session['reset_email'])
            #Send email
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

#----------Verify OTP-----------#
        elif action == "verify_otp":

            entered_otp = request.form.get("otp")

            secret = session.get("reset_otp_secret")

            if not secret:
                flash("Please request an OTP first.")
                return redirect(url_for("forget_password"))

            totp = pyotp.TOTP(secret, interval=60)

            if totp.verify(entered_otp):

                email = session.get("reset_email")

            #Correct OTP
                session["otp_verified"] = True
                flash("OTP verified successfully.")

                return redirect(url_for("reset_password"))

            else:

                flash("Invalid or expired OTP.")

                return redirect(url_for("forget_password"))

    return render_template('forget_password.html')

@app.route("/reset-password", methods=['POST', 'GET'])
def reset_password():

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
            {'$set' : {"password": password_hash}},
        )

        session.pop("reset_email", None)
        session.pop("reset_otp_secret", None)
        session.pop("otp_verified", None)

        flash("Password reset successful. Please log in.")
        return redirect(url_for("login"))

    return render_template("reset_password.html")

@app.route("/profile")
def profile():

    if not session.get("user"):
        return redirect(url_for("login", next=request.path))

    user = g.db.users.find_one({"email": session['user']['email']})
    streak_days = (datetime.now() - user['donor_details']['membership_streak']).days + 1
    print(streak_days)

    return render_template("profile.html",
                           user= user,
                           streak = streak_days)

def save_image(file):
    """Save an uploaded image to static/uploads and return its URL."""
    extension = os.path.splitext(file.filename)[1].lower()
    filename = secure_filename(f"{uuid.uuid4().hex}{extension}")
    upload_folder = os.path.join(app.root_path, "static", "uploads")
    os.makedirs(upload_folder, exist_ok=True)
    file.save(os.path.join(upload_folder, filename))
    return url_for("static", filename=f"uploads/{filename}")


def delete_old_upload(url):
    """Delete a previously uploaded image. External URLs (e.g. Google photos) are skipped."""
    if url and url.startswith("/static/uploads/"):
        path = os.path.join(app.root_path, "static", "uploads", os.path.basename(url))
        if os.path.isfile(path):
            os.remove(path)


@app.route("/edit-profile", methods = ["GET", "POST"])
def edit_profile():

    if not session.get("user"):
        return redirect(url_for("login", next=request.path))

    user = g.db.users.find_one({"email": session['user']['email']})

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

        # ADDED: handle the uploaded profile picture (if one was chosen)
        new_pic_url = None
        pic = request.files.get("profile-pic")
        if pic and pic.filename:
            extension = os.path.splitext(pic.filename)[1].lower()
            if extension not in ALLOWED_IMAGE_EXT:
                flash("Use a JPG, PNG, or WebP profile picture.")
                return redirect(url_for("edit_profile"))

            filename = secure_filename(f"{uuid.uuid4().hex}{extension}")
            upload_folder = os.path.join(app.root_path, "static", "uploads")
            os.makedirs(upload_folder, exist_ok=True)
            pic.save(os.path.join(upload_folder, filename))
            new_pic_url = url_for("static", filename=f"uploads/{filename}")

        update_fields = {
            "name" : name,
            "email" : email,
            "phone-no" : phone_no,
            "address": address,
            "donor_details.DOB" : DOB,
            "donor_details.state" : state
        }

        # ADDED: only touch profile-pic if a new one was uploaded
        if new_pic_url:
            update_fields["profile-pic"] = new_pic_url

        #Check that all fields are filled in, then mark the profile as complete
        if all([phone_no, DOB, state, address]):
            update_fields['profile_completed'] = True

        g.db.users.update_one({"_id": user["_id"]}, {"$set": update_fields})

        # ADDED: delete the old uploaded picture so files don't pile up.
        # Google profile pictures are external URLs, so they're skipped.
        if new_pic_url:
            old_pic = user.get("profile-pic") or ""
            if old_pic.startswith("/static/uploads/"):
                old_path = os.path.join(app.root_path, "static", "uploads", os.path.basename(old_pic))
                if os.path.isfile(old_path):
                    os.remove(old_path)

        session["user"] = {**session["user"], "name": name, "email": email}

        flash("Profile updated.")
        return redirect(url_for("profile"))

    return render_template("edit-profile.html",
                           user=user,
                           states=MALAYSIA_STATES)

@app.route('/create_comment/<campaign_id>', methods = ["GET", "POST"])
def create_comment(campaign_id):

    if not session.get("user"):
        flash("Please log in to leave a comment.")
        return redirect(url_for("login"))

    if not ObjectId.is_valid(campaign_id):
        abort(404)

    campaign = g.db.campaigns.find_one({"_id": ObjectId(campaign_id)})
    if campaign is None:
        abort(404)

    if request.method == "POST":
        comment = request.form.get('comment')

        user = g.db.users.find_one({"email": session['user']['email']})
        comments = g.db.comments

        comments.insert_one({
            "campaign_id": campaign_id,
            "name": user['name'],
            "profile-pic": user['profile-pic'],
            "donation_amount": 1000,
            "date_posted": datetime.now(),
            "comment": comment
        })

    return redirect(url_for("campaign_detail", campaign_id=campaign_id))


# Force new users to complete their profile before they may continue.
#Pages exempt from forced redirection to edit profile
PROFILE_EXEMPT_ENDPOINTS = {
    "edit_profile", "logout", "static",
    "login", "google_login", "authorize",
    "reset_password"
}
@app.before_request
def require_complete_profile():
    user_session = session.get("user")
    if not user_session:
        return  # not logged in — your per-route checks handle this

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