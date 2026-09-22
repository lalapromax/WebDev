import os
from dotenv import load_dotenv
from flask import Flask, g, render_template, request, redirect, url_for, flash
from flask_pymongo import PyMongo
from pymongo import MongoClient
from pymongo.server_api import ServerApi
import atexit
import certifi
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('MED_SECRET', 'dev-secret-key')

load_dotenv() #loads mongodb cloud server location


MONGODB_URI = os.environ["MONGODB_URI"]          # raises KeyError if missing — good, fail loudly
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "app_db")

app.config["MONGO_URI"] = MONGODB_URI

ca = certifi.where() #Secure connection

mongo = PyMongo(app)

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
    # Reuse the same client, pick whichever database you need
    g.db = app.mongo_client["medoctor"]

# --- routes ---
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/contact")
def contact():
    return render_template("contact.html")

@app.route("/register", methods =["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        #Validation
        if not email or not password:
            flash("Username and password are required.")
            return url_for("register")

        users = mongo.db.users

        #Check if existing user
        existing_user = users.find_one({"email": email})
        if existing_user:
            flash("Username already exist")
            return render_template("register.html")

        users.insert_one({
            "email" : email,
            "password" : password

        })

        flash("Registration successful! Please log in.")
        return redirect(url_for("index"))

    return render_template("register.html")

@app.route("/login")
def login():
    return render_template("login.html")

if __name__ == "__main__":
    app.run(debug=True)