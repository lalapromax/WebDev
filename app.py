import os
from dotenv import load_dotenv
from flask import Flask, g, render_template
from pymongo import MongoClient
from pymongo.server_api import ServerApi
import atexit
import certifi

load_dotenv() #loads mongodb cloud server location


MONGODB_URI = os.environ["MONGODB_URI"]          # raises KeyError if missing — good, fail loudly
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "app_db")


ca = certifi.where() #Secure connection



app = Flask(__name__)

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



if __name__ == "__main__":
    app.run(debug=True)