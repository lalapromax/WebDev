"""
Fills the MEDonate database with demo data for ONE NGO account so the new
ngo_profile dashboard has something to show.

Put this file next to app.py (same folder as .env) and run it once:

    python seed_demo.py                  # uses the first NGO account it finds
    python seed_demo.py ngo@email.com    # a specific NGO account
    python seed_demo.py --clear          # remove ALL demo data again
    python seed_demo.py ngo@email.com --clear   # remove demo data for one NGO

Every demo document carries a "demo_owner" field, so real data is never touched
and running the script again simply replaces the old demo data.
"""
import os
import random
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from pymongo import MongoClient

DB_NAME = "medonate"

DONORS = [
    "Aisyah Rahman", "Wei Jie Tan", "Priya Nair", "Daniel Lim",
    "Siti Aminah", "Arjun Menon", "Mei Ling Chong", "Hafiz Ismail",
]
# Earlier donors give more often, so "returning donors" shows up in the chart.
DONOR_WEIGHTS = [5, 4, 3, 3, 2, 2, 1, 1]
GIFT_SIZES_CENTS = [1000, 2000, 5000, 5000, 10000, 10000, 20000, 50000]

# age = days since the campaign was created, fill = share of the goal raised
CAMPAIGNS = [
    {"title": "Dialysis machines for the community clinic", "goal": 6000, "age": 175, "fill": 0.62,
     "status": "active", "confirmed": True, "equipment": [1, 1],
     "items": ["Dialysis machine", "Reclining chair", "Water filter", "Blood pressure monitor"]},
    {"title": "School health screening kits", "goal": 3000, "age": 120, "fill": 0.35,
     "status": "active", "confirmed": True, "equipment": [2, 1],
     "items": ["Weighing scale", "Height chart", "Vision chart", "Thermometer", "Stethoscope", "First-aid kit"]},
    {"title": "Wheelchairs for elderly residents", "goal": 2500, "age": 160, "fill": 1.0,
     "status": "completed", "confirmed": True, "equipment": [], "items": []},
    {"title": "Mobile clinic van (draft)", "goal": 30000, "age": 5, "fill": 0,
     "status": "active", "confirmed": False, "equipment": [], "items": ["Van", "Folding beds"]},
]


def clear(db, ngo_email=None):
    """Delete demo data for one NGO (or all NGOs when ngo_email is None)."""
    query = {"demo_owner": ngo_email} if ngo_email else {"demo_owner": {"$exists": True}}
    removed = 0
    for collection in (db.donations, db.campaigns, db.users):
        removed += collection.delete_many(query).deleted_count
    return removed


def seed(db, ngo, rng=None):
    rng = rng or random.Random(7)  # fixed seed: same demo data every run
    now = datetime.now(timezone.utc)
    owner = ngo["email"]
    slug = owner.split("@")[0]

    clear(db, owner)

    donor_ids = db.users.insert_many([
        {
            "email": f"demo.donor{i + 1}.{slug}@example.com",
            "name": name,
            "password": None,
            "address": None,
            "phone-no": None,
            "profile-pic": None,
            "role": "donor",
            "provider": "local",
            "profile_completed": False,
            "donor_details": {"DOB": None, "state": None, "membership_streak": now,
                              "tier": "Bronze", "total_cents": 0},
            "demo_owner": owner,
        }
        for i, name in enumerate(DONORS)
    ]).inserted_ids

    donations = []
    for spec in CAMPAIGNS:
        goal_cents = spec["goal"] * 100
        campaign_id = db.campaigns.insert_one({
            "ngo_id": ngo["_id"],
            "title": spec["title"],
            "description": "Demo campaign created by seed_demo.py.",
            "amount_cents": goal_cents,
            "youtube_url": None,
            "image_url": None,
            "image_urls": [],
            "custom_url": None,
            "owner_email": owner,
            "needed_items": spec["items"],
            "keywords": ["demo"],
            "current_amount": 0,
            "status": spec["status"],
            "confirmed": spec["confirmed"],
            "created_at": now - timedelta(days=spec["age"]),
            "demo_owner": owner,
        }).inserted_id

        # Fund donations: keep adding gifts until the target share of the goal.
        # Squaring the random number skews gifts toward recent months (growth).
        raised = 0
        while raised < goal_cents * spec["fill"]:
            cents = rng.choice(GIFT_SIZES_CENTS)
            days_ago = max(1, int(spec["age"] * rng.random() ** 1.4))
            donations.append({
                "user_id": rng.choices(donor_ids, weights=DONOR_WEIGHTS)[0],
                "campaign_id": campaign_id,
                "amount_cents": cents,
                "donation_type": "funds",
                "payment_method": None,
                "date_posted": now - timedelta(days=days_ago, hours=rng.randint(0, 23)),
                "demo_owner": owner,
            })
            raised += cents

        # Equipment donations (the dashboard counts "quantity" per campaign)
        for quantity in spec["equipment"]:
            donations.append({
                "user_id": rng.choices(donor_ids, weights=DONOR_WEIGHTS)[0],
                "campaign_id": campaign_id,
                "donation_type": "equipment",
                "quantity": quantity,
                "date_posted": now - timedelta(days=rng.randint(1, spec["age"] - 1),
                                               hours=rng.randint(0, 23)),
                "demo_owner": owner,
            })

        update = {"current_amount": raised}
        db.campaigns.update_one({"_id": campaign_id}, {"$set": update})

    # Insert oldest-first so _id order matches date order (the dashboard relies on it)
    donations.sort(key=lambda d: d["date_posted"])
    db.donations.insert_many(donations)

    return {"campaigns": len(CAMPAIGNS), "donations": len(donations), "donors": len(donor_ids)}


def main():
    load_dotenv()

    # Same DNS workaround app.py uses (needed for mongodb+srv:// on some networks)
    import dns.resolver
    dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
    dns.resolver.default_resolver.nameservers = ["8.8.8.8", "1.1.1.1"]

    emails = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    db = MongoClient(os.environ["MONGODB_URI"])[DB_NAME]

    if "--clear" in sys.argv:
        print(f"Removed {clear(db, emails[0] if emails else None)} demo documents.")
        return

    ngo = db.users.find_one({"email": emails[0], "role": "ngo"} if emails else {"role": "ngo"})
    if not ngo:
        sys.exit("No NGO account found. Create one first (admin > register NGO).")

    summary = seed(db, ngo)
    print(f"Demo data added for {ngo['email']}: {summary['campaigns']} campaigns, "
          f"{summary['donations']} donations from {summary['donors']} donors.")
    print("Log in as that NGO and open /profile. Undo with: python seed_demo.py --clear")


if __name__ == "__main__":
    main()