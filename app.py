import os

from dotenv import load_dotenv
from flask import Flask, g, render_template, request, redirect, url_for, flash, session, jsonify
from pymongo import MongoClient
from pymongo.server_api import ServerApi

#For Forget Password OTP
from flask_mail import Mail, Message
import pyotp

import atexit
import certifi
from werkzeug.security import generate_password_hash, check_password_hash

from authlib.integrations.flask_client import OAuth

#STANDARDISE DNS TO 8.8.8.8 FOR DEBUGGING!! REMOVE LATER
import dns.resolver
dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
dns.resolver.default_resolver.nameservers = ['8.8.8.8', '1.1.1.1']


app = Flask(__name__)
app.secret_key = os.environ.get('MED_SECRET', 'dev-secret-key')


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

        #Validation
        if not email or not name or not password:
            flash("Name, email, and password are required.")
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
            "provider":"local"

        })

        flash("Registration successful! Please log in.")
        return redirect(url_for("index"))

    return render_template("register.html")


#-----Login Routes------

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

    return render_template('forgetpassword.html')

@app.route("/passwordOTP")
def password_otp():




    return render_template("forgetpassword.html")

@app.route("/logout")
def logout():

    session.pop('user', None)
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=True)