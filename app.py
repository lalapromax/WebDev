import os
import uuid
from datetime import date, timezone
from decimal import Decimal, InvalidOperation

import requests
import stripe

from dotenv import load_dotenv
from flask import Flask, g, render_template, request, redirect, url_for, flash, session, jsonify
from pymongo import MongoClient
from pymongo.server_api import ServerApi
from datetime import datetime, timezone

#For Forget Password OTP
from flask_mail import Mail, Message
import pyotp

import atexit
import certifi
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from authlib.integrations.flask_client import OAuth

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

#HitPay service configuration
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", None)
stripe.api_version = "2026-08-26.dahlia; custom_checkout_payment_form_preview=v1"
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY",None)
STRIPE_WEBHOOK_SECRET = os.environ["STRIPE_WEBHOOK_SECRET"]




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
            "role" : "donor",
            "provider":"local",

            "user_details" : {
                "DOB" : None,
                "state": None,
                "membership_streak" : date.today(),
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

@app.route("/register-ngo", methods=["POST"])
def register_ngo():

    if session.get('user'):
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
            "provider": "local",

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

#-----Create Campaign-----
@app.route("/campaign-create", methods=["GET", "POST"])
def campaign_create():

    if not session.get("user"):
        return redirect(url_for("login", next=request.path))

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        youtube_url = request.form.get("youtubeLink", "").strip()
        custom_url = request.form.get("customUrl", "").strip().lower()
        image = request.files.get("coverPhoto")
        has_image = bool(image and image.filename)

        try:
            amount = Decimal(request.form.get("fundraiserAmount", ""))
            if (
                not amount.is_finite()
                or amount <= 0
                or amount * 100 != (amount * 100).to_integral_value()
            ):
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            return render_template(
                "campaign-create.html",
                error="Enter a valid amount with up to two decimal places.",
            )

        if not title or has_image == bool(youtube_url):
            return render_template(
                "campaign-create.html",
                error="Enter a title and choose either a cover photo or a YouTube link.",
            )

        if custom_url and g.db.campaigns.find_one({"custom_url": custom_url}):
            return render_template(
                "campaign-create.html",
                error="That custom URL is already in use.",
            )

        image_url = None
        if has_image:
            extension = os.path.splitext(image.filename)[1].lower()
            if extension not in {".jpg", ".jpeg", ".png", ".webp"}:
                return render_template(
                    "campaign-create.html",
                    error="Use a JPG, PNG, or WebP cover photo.",
                )

            filename = secure_filename(f"{uuid.uuid4().hex}{extension}")
            upload_folder = os.path.join(app.root_path, "static", "uploads")
            os.makedirs(upload_folder, exist_ok=True)
            image.save(os.path.join(upload_folder, filename))
            image_url = url_for("static", filename=f"uploads/{filename}")

        campaigns = g.db.campaigns
        users = g.db.users

        campaigns.insert_one({
            "title": title,
            "description": description,
            "amount_cents": int(amount*100),
            "youtube_url": youtube_url or None,
            "image_url": image_url,
            "custom_url": custom_url or None,
            "owner_name": session["user"]["name"],
            "owner_email": session["user"]["email"],
            "status": "active",
            "created_at": datetime.now(timezone.utc),
        })
        user = users.find_one({'email' : session['user']['email']})

        user.update_one({
            "user_campaigns" : {'campaign_id' : campaigns['_id'], 'title' : title}
        })

        g.db.audit_logs.insert_one({
            "user_id": user['_id'],
            "name" : user['email'],
            "role" : user['ngo_name'],
            "action": "create",
            "changes": f"{user['name']} created campaign {title}",
            "timestamp" : datetime.now()
        })

        flash("Campaign details saved.")
        return redirect(url_for("index"))

    return render_template("campaign-create.html")

#User donates to a campaign


@app.route("/campaign-donate/<int:_id>", methods=["POST"])
def campaign_donate(_id):

    if not session.get("user"):
        return redirect(url_for("login", next=request.path))

    campaign = g.db.campaigns.find_one({"_id": _id})
    if not campaign:
        flash("No campaign found.")
        return redirect(url_for("index"))

    try:
        # amount = Decimal(request.form.get("amount", ""))
        amount = Decimal(500.00)
        if (
            not amount.is_finite()
            or amount <= 0
            or amount * 100 != (amount * 100).to_integral_value()
        ):
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        flash("Enter a valid donation amount with up to two decimal places.")
        return redirect(url_for("campaign_detail", _id=_id))

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
        # comes back to you in the webhook event below — so nothing here
        # needs to be trusted from the browser again after this point.
        metadata={
            "campaign_id": str(_id),
            "donor_name": "" if anonymous else session["user"]["name"],
            "donor_email": session["user"]["email"],
            "message": message,
            "anonymous": "true" if anonymous else "false",
        },
        success_url=url_for("donation_success", _external=True) + "?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=url_for("campaign_detail", _id=_id, _external=True),
    )

    user = g.db.users.find_one({"email": session['user']['email']})



    return redirect(checkout_session.url, code=303)


@app.route("/donate/success")
def donation_success():

    #Return donation success message.
    session_id = request.args.get("session_id")
    if session_id:
        try:
            checkout_session = stripe.checkout.Session.retrieve(session_id)
            if checkout_session.payment_status == "paid":
                flash("Thank you for your donation!")
            else:
                flash("Payment received — we're confirming it now.")
        except stripe.error.StripeError:
            flash("Thanks! We're confirming your payment.")
    return redirect(url_for("index"))


@app.route("/stripe-webhook", methods=["POST"])
def stripe_webhook():
    # Called by Stripe's servers directly, not by a logged-in user —
    # no session check here, and exempt this route from CSRF protection
    # if you have Flask-WTF's CSRFProtect enabled globally.
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        return "", 400  # bad payload or bad signature — reject it

    if event["type"] == "checkout.session.completed":
        checkout_session = event["data"]["object"]
        metadata = checkout_session.get("metadata", {})
        campaign_id = int(metadata.get("campaign_id", 0))
        amount_cents = checkout_session["amount_total"]  # what Stripe actually charged
        anonymous = metadata.get("anonymous") == "true"

        # Stripe retries webhooks that don't get a fast 200 response, so this
        # write has to be idempotent — otherwise a retry double-counts a donation.
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
            # Only the first time we see this session — a retry will match
            # the existing doc and skip this, so totals stay correct.
            g.db.campaigns.update_one(
                {"_id": campaign_id},
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

    user = g.db.users.find_one({"email": session['user']['email']})
    g.db.audit_logs.insert_one({
        "user_id": user['_id'],
        "name": user['email'],
        "role": user['ngo_name'],
        "action": "donate",
        "changes": f"{user['name']} donated RM{amount} to {campaign['title']}",
        "timestamp": datetime.now()
    })

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
    picture = user_info.get('picture')

    #Find an existing user with that email
    users = g.db.users
    existing_user = users.find_one({"email": email})

    if not existing_user:
        users.insert_one({

            "email" : email,
            "name" : name,
            "password": None, #Google doesn't provide password
            "role": "donor",
            "provider": "google"

        })

    session['user'] = {
        "name" : name,
        "email" : email
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

        elif existing_user and check_password_hash(existing_user["password"], password):
            session['user'] = {
                "name": existing_user.get("name", existing_user["email"]),
                "email": email
            }
            next_url = request.args.get("next", "")
            if next_url.startswith("/") and not next_url.startswith("//") and "\\" not in next_url:
                return redirect(next_url)
            return redirect(url_for('index'))
        else:
            flash("Invalid credentials")

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

    if request.method == "POST":

        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")

        if not password or not confirm_password:
            flash("Please enter password and password confirmation.")
            return redirect(url_for('reset-password'))

        if password != confirm_password:
            flash("Passwords do not match.")
            return redirect(url_for('reset-password'))

        password_hash = generate_password_hash(password)
        users = g.db.users

        user = users.find_one({"email": session['user']['email']})

        users.update_one(
            {"_id": user['_id']},
            {"password": password_hash},
            upsert=False
        )

    return render_template("reset_password.html")

@app.route("/profile")
def profile():
    return render_template("profile.html")

@app.route("/edit-profile")
def edit_profile():
    return render_template("edit-profile.html")


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

@app.route("/api/create-checkout-session", methods=["POST"])
def create_checkout_session():
    orders = g.db.orders

    order = {
        **SAMPLE_ORDER,
        "status": "pending",
        "created_at": datetime.now(timezone.utc),
    }
    order_id = orders.insert_one(order).inserted_id

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            ui_mode="embedded_page",   # "embedded" if your API version is older
            line_items=[{
                "price_data": {
                    "currency": SAMPLE_ORDER["currency"],
                    "product_data": {"name": SAMPLE_ORDER["fundraiser"]},
                    "unit_amount": SAMPLE_ORDER["amount"],
                },
                "quantity": 1,
            }],
            customer_email=SAMPLE_ORDER["email"],
            client_reference_id=str(order_id),
            metadata={"reference": SAMPLE_ORDER["reference"]},
            return_url="http://127.0.0.1:5000/payment-success?session_id={CHECKOUT_SESSION_ID}",
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    orders.update_one(
        {"_id": order_id},
        {"$set": {"stripe_session_id": session.id}},
    )
    return jsonify({"client_secret": session.client_secret})

@app.route("/payment-success")
def payment_success():

    orders = g.db.orders

    session_id = request.args.get("session_id")
    current_order = orders.find_one({"stripe_session_id": session_id}) if session_id else None
    if not current_order:
        return render_template("payment-success.html", status="unknown")

    # Ask Stripe directly instead of trusting the URL
    checkout = stripe.checkout.Session.retrieve(session_id)

    if checkout.payment_status == "paid" and current_order["status"] != "paid":
        orders.update_one(
            {"_id": current_order["_id"], "status": {"$ne": "paid"}},
            {"$set": {
                "status": "paid",
                "paid_at": datetime.now(timezone.utc),
                "payment_intent": checkout.payment_intent,
            }},
        )
    elif checkout.status == "expired":
        orders.update_one({"_id": current_order["_id"], "status": "pending"},
                             {"$set": {"status": "expired"}})

    return render_template("payment-success.html", status=checkout.payment_status)


@app.route("/logout")
def logout():

    session.pop('user', None)
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=True)