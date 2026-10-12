
from datetime import datetime, timezone

import os
import stripe
from bson import ObjectId
from flask import Blueprint, flash, g, redirect, request, session, url_for

from dotenv import load_dotenv

ngo_payouts = Blueprint("ngo_payouts", __name__)

# ---- adjust these two to match your app -----------------------------------
LOGIN_ENDPOINT = "login"
NGO_DASHBOARD_ENDPOINT = "profile"
# -----------------------------------------------------------------------------

# Must match the currency your Checkout Sessions charge donors in.
PAYOUT_CURRENCY = "myr"
CURRENCY_DISPLAY = "RM"  # matches the "RM {{ ... }}" convention used across ngo_dashboard.html

# Set >0 if you want to hold back tiny balances (e.g. skip payouts under $1
# to avoid Stripe transfer fees eating a disproportionate chunk).
MIN_PAYOUT_CENTS = 0

load_dotenv()

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", None)
client = stripe.StripeClient(stripe.api_key)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _current_user():

    if "user" not in session:
        return None
    return g.db.users.find_one({"email": session["user"]["email"]})


def _ngo_campaign_ids(user):
    return [c["_id"] for c in g.db.campaigns.find(
        {"owner_email": user["email"]}, {"_id": 1}
    )]


def _unpaid_claim_filter(campaign_ids):
    # donation_type != "equipment" -- item donations aren't cash and
    # shouldn't be swept into a cash payout even if payout_status is set.
    return {
        "campaign_id": {"$in": campaign_ids},
        "payout_status": "unpaid",
        "donation_type": {"$ne": "equipment"},
    }


def _recipient_transfers_active(account_id):

    account = client.v2.core.accounts.retrieve(
        account_id, {"include": ["configuration.recipient"]}
    )
    config = getattr(account, "configuration", None) or {}
    recipient = getattr(config, "recipient", None) or {}
    capabilities = getattr(recipient, "capabilities", None) or {}
    stripe_balance = getattr(capabilities, "stripe_balance", None) or {}
    transfers = getattr(stripe_balance, "stripe_transfers", None) or {}

    return getattr(transfers, "status", None) == "active"


# ---------------------------------------------------------------------------
# Stripe Connect onboarding -- an NGO needs this done once before any payout
# ---------------------------------------------------------------------------

@ngo_payouts.route("/connect-onboard", methods=["POST"])
def connect_onboard():
    user = _current_user()
    if not user:
        return redirect(url_for(LOGIN_ENDPOINT))

    try:
        # Reuse the existing connected account if one was already created.
        account_id = user.get("stripe_account_id")

        if not account_id:
            account = client.v2.core.accounts.create({
                "contact_email": user["email"],
                "display_name": user.get("name") or user["email"],
                "dashboard": "full",
                "identity": {
                    "country": "my",

                    "entity_type": "individual",
                },
                "configuration": {
                    "recipient": {
                        "capabilities": {
                            "stripe_balance": {
                                "stripe_transfers": {"requested": True},

                        }
                    }
                },
                "merchant": {
                    "capabilities": {
                        "card_payments": {"requested": True},
                        }
                    }
                },
                "defaults": {
                    "currency": "myr",
                    "responsibilities" : {"fees_collector": "stripe", "losses_collector" : "stripe"},
                    "locales": ["en"],
                },
                "metadata": {"ngo_user_id": str(user["_id"])},
                "include": ["configuration.recipient", "configuration.merchant", "identity", "requirements"],
            })

            account_id = account.id
            g.db.users.update_one(
                {"_id": user["_id"]},
                {"$set": {"stripe_account_id": account_id}},
            )



        account_link = client.v2.core.account_links.create({
            "account": account_id,
            "use_case": {
                "type": "account_onboarding",
                "account_onboarding": {
                    "configurations":  ["recipient", "merchant"],
                    "return_url": f"http://localhost:5000/ngo/connect/return",
                    "refresh_url": f"http://localhost:5000/ngo/connect/return",
                },
            },
        })
        acct_id = user.get('stripe_account_id')
        account = stripe.Account.retrieve(acct_id)
        print(account.capabilities)

        return redirect(account_link.url)

    except stripe.StripeError as exc:
        print("Stripe error:", type(exc).__name__, str(exc))
        flash("Couldn't start payout setup. Check the server log.", "popup")
        return redirect(url_for(NGO_DASHBOARD_ENDPOINT))


@ngo_payouts.route("/ngo/connect/return")
def connect_return():
    """Stripe sends the NGO back here after onboarding (finished or not)."""
    user = _current_user()
    if not user or not user.get("stripe_account_id"):
        return redirect(url_for(NGO_DASHBOARD_ENDPOINT))

    try:
        ready = _recipient_transfers_active(user["stripe_account_id"])
    except stripe.StripeError as exc:
        print("Stripe error:", type(exc).__name__, str(exc))
        return redirect(url_for(NGO_DASHBOARD_ENDPOINT))

    g.db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {"stripe_payouts_ready": ready}},
    )

    acct_id = user.get('stripe_account_id')
    account = stripe.Account.retrieve(acct_id)
    print(account.capabilities)
    flash(
        "Payout account connected -- you can now claim funds."
        if ready else
        "Almost there -- Stripe still needs a bit more info before you can claim funds.",
        "popup",
    )
    return redirect(url_for(NGO_DASHBOARD_ENDPOINT))


# ---------------------------------------------------------------------------
# The actual "claim money" button
# ---------------------------------------------------------------------------

@ngo_payouts.route("/ngo/payouts/claim", methods=["POST"])
def claim_payout():
    user = _current_user()
    if not user:
        return redirect(url_for(LOGIN_ENDPOINT))

    account_id = user.get("stripe_account_id")
    if not account_id:
        flash("Connect a payout account first.", "popup")
        return redirect(url_for(NGO_DASHBOARD_ENDPOINT))

    try:
        ready = _recipient_transfers_active(account_id)
    except stripe.error.StripeError as exc:  # Updated to modern exceptions namespace
        print("Stripe verification error:", type(exc).__name__, str(exc))
        flash("Couldn't verify your payout account with Stripe. Try again shortly.", "popup")
        return redirect(url_for(NGO_DASHBOARD_ENDPOINT))

    if not ready:
        flash("Your payout account isn't fully set up yet.", "popup")
        return redirect(url_for(NGO_DASHBOARD_ENDPOINT))

    campaign_ids = _ngo_campaign_ids(user)
    if not campaign_ids:
        flash("No campaigns to pay out.", "popup")
        return redirect(url_for(NGO_DASHBOARD_ENDPOINT))

    # Optimistic concurrency check: Ensure no other batch is currently processing
    already_processing = g.db.donations.count_documents({
        "campaign_id": {"$in": campaign_ids},
        "payout_status": "processing",
    })
    if already_processing:
        flash("A payout is already in progress -- hang tight.", "popup")
        return redirect(url_for(NGO_DASHBOARD_ENDPOINT))

    batch_id = ObjectId()

    # Secure the batch block in the database first
    g.db.donations.update_many(
        _unpaid_claim_filter(campaign_ids),
        {"$set": {"payout_status": "processing", "payout_batch_id": batch_id}},
    )

    claimed = list(g.db.donations.find(
        {"payout_batch_id": batch_id}, {"amount_cents": 1}
    ))
    total_cents = sum(int(d.get("amount_cents") or 0) for d in claimed)

    # Validate against minimum payout limits
    if not claimed or total_cents <= MIN_PAYOUT_CENTS:
        g.db.donations.update_many(
            {"payout_batch_id": batch_id},
            {"$set": {"payout_status": "unpaid"}, "$unset": {"payout_batch_id": ""}},
        )
        flash("Nothing to pay out right now.", "popup")
        return redirect(url_for(NGO_DASHBOARD_ENDPOINT))

    # Insert a tracking state record
    g.db.payouts.insert_one({
        "_id": batch_id,
        "ngo_email": user["email"],
        "stripe_account_id": account_id,
        "amount_cents": total_cents,
        "currency": PAYOUT_CURRENCY.lower(),  # Stripe expects lowercase codes (e.g. 'myr')
        "donation_ids": [d["_id"] for d in claimed],
        "status": "processing",
        "created_at": datetime.now(timezone.utc),
    })

    try:
        # EXECUTE MANIPULATION VIA THE UNIFIED CLIENT INSTANCE
        # (Assuming 'client' is your initialized Stripe client from your onboarding route)
        transfer = client.transfers.create(
            params={
                "amount": total_cents,
                "currency": PAYOUT_CURRENCY.lower(),
                "destination": account_id,
                "transfer_group": str(batch_id),
                "metadata": {
                    "payout_batch_id": str(batch_id),
                    "ngo_email": user["email"]
                }
            },
            options={
                "idempotency_key": f"payout-{str(batch_id)}"
            }
        )
    except stripe.error.StripeError as exc:
        print("Stripe Transfer Failure:", type(exc).__name__, str(exc))

        # Rollback database changes cleanly if Stripe rejects the transfer
        g.db.donations.update_many(
            {"payout_batch_id": batch_id},
            {"$set": {"payout_status": "unpaid"}, "$unset": {"payout_batch_id": ""}},
        )
        g.db.payouts.update_one(
            {"_id": batch_id},
            {"$set": {"status": "failed", "error": str(exc)}}
        )
        flash(f"Payout failed: {exc.user_message if hasattr(exc, 'user_message') else 'Please try again.'}", "popup")
        return redirect(url_for(NGO_DASHBOARD_ENDPOINT))

    # Mark as completely settled on success
    g.db.donations.update_many(
        {"payout_batch_id": batch_id},
        {"$set": {"payout_status": "paid", "payout_id": batch_id}},
    )
    g.db.payouts.update_one(
        {"_id": batch_id},
        {"$set": {
            "status": "completed",
            "stripe_transfer_id": transfer.id,
            "completed_at": datetime.now(timezone.utc),
        }},
    )

    flash(f"{CURRENCY_DISPLAY} {total_cents / 100:,.2f} is on its way!", "popup")
    return redirect(url_for(NGO_DASHBOARD_ENDPOINT))