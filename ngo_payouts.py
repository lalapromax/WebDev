from datetime import datetime, timezone

import stripe
from bson import ObjectId
from flask import Blueprint, flash, g, redirect, request, session, url_for

ngo_payouts = Blueprint("ngo_payouts", __name__)

# ---- adjust these two to match your app -----------------------------------
LOGIN_ENDPOINT = "login"
NGO_DASHBOARD_ENDPOINT = "profile"
# -----------------------------------------------------------------------------

# Must match the currency your Checkout Sessions charge donors in.
PAYOUT_CURRENCY = "myr"

# Set >0 if you want to hold back tiny balances (e.g. skip payouts under $1
# to avoid Stripe transfer fees eating a disproportionate chunk).
MIN_PAYOUT_CENTS = 0

client = stripe.StripeClient(stripe.api_key)
# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _current_user():
    """Mirrors the session['user']['email'] pattern from your donation route.
    Mount this blueprint behind whatever check already guards your NGO
    dashboard (role check etc.) -- this only confirms someone is logged in.
    """
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


# ---------------------------------------------------------------------------
# Stripe Connect onboarding -- an NGO needs this done once before any payout
# ---------------------------------------------------------------------------


@ngo_payouts.route("/connect-onboard")
def connect_onboard():
    user = _current_user()

    if not user:
        return redirect(url_for(LOGIN_ENDPOINT))

    try:
        # Reuse the existing connected account if one was created.
        account_id = user.get("stripe_account_id")

        if not account_id:
            account = client.v2.core.accounts.create({
                "contact_email": user["email"],
                "display_name": user.get("name") or user["email"],
                "identity": {
                    "country": "my",
                    "entity_type": "individual",
                },
                "configuration": {
                    "recipient": {
                        "capabilities": {
                            "stripe_balance": {
                                "stripe_transfers": {
                                    "requested": True
                                }
                            }
                        }
                    }
                },
                "defaults": {
                    "currency": "myr",
                    "locales": ["en-MY"],
                },
                "metadata": {
                    "ngo_user_id": str(user["_id"]),
                },
                "include": [
                    "configuration.recipient",
                    "identity",
                    "requirements",
                ],
            })

            account_id = account.id

            # Save the v2 account ID in your existing user record.
            g.db.users.update_one(
                {"_id": user["_id"]},
                {"$set": {"stripe_account_id": account_id}},
            )

        # Account Links are still a v1 endpoint.
        # Stripe supports using a v2 Account ID here.
        link = stripe.AccountLink.create(
            account=account_id,
            refresh_url=url_for(
                "ngo_payouts.connect_onboard",
                _external=True,
            ),
            return_url=url_for(
                "ngo_payouts.connect_return",
                _external=True,
            ),
            type="account_onboarding",
        )

        return redirect(link.url)

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
        account = stripe.Account.retrieve(user["stripe_account_id"])
    except stripe.error.StripeError:
        return redirect(url_for(NGO_DASHBOARD_ENDPOINT))

    ready = account.capabilities.get("transfers") == "active"
    g.db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {"stripe_payouts_ready": ready}},
    )

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

    # Live check rather than trusting the cached flag -- Stripe can restrict
    # an account after the fact (risk review, missing docs, etc.).
    try:
        account = stripe.Account.retrieve(account_id)
    except stripe.error.StripeError:
        flash("Couldn't verify your payout account with Stripe. Try again shortly.", "popup")
        return redirect(url_for(NGO_DASHBOARD_ENDPOINT))

    if account.capabilities.get("transfers") != "active":
        flash("Your payout account isn't fully set up yet.", "popup")
        return redirect(url_for(NGO_DASHBOARD_ENDPOINT))

    campaign_ids = _ngo_campaign_ids(user)
    if not campaign_ids:
        flash("No campaigns to pay out.", "popup")
        return redirect(url_for(NGO_DASHBOARD_ENDPOINT))

    # Fast-path guard against double-clicks / concurrent requests. Not the
    # only safety net -- see note below -- but avoids kicking off redundant
    # work in the common case.
    already_processing = g.db.donations.count_documents({
        "campaign_id": {"$in": campaign_ids},
        "payout_status": "processing",
    })
    if already_processing:
        flash("A payout is already in progress -- hang tight.", "popup")
        return redirect(url_for(NGO_DASHBOARD_ENDPOINT))

    # Claim: flip unpaid -> processing. Safe under concurrent claims because
    # MongoDB evaluates the filter per document at write time, so a document
    # can only ever end up tagged with one batch id, even if two requests
    # race -- there's no way for the same donation to be summed twice.
    batch_id = ObjectId()
    g.db.donations.update_many(
        _unpaid_claim_filter(campaign_ids),
        {"$set": {"payout_status": "processing", "payout_batch_id": batch_id}},
    )

    claimed = list(g.db.donations.find(
        {"payout_batch_id": batch_id}, {"amount_cents": 1}
    ))
    total_cents = sum(int(d.get("amount_cents") or 0) for d in claimed)

    if not claimed or total_cents <= MIN_PAYOUT_CENTS:
        g.db.donations.update_many(
            {"payout_batch_id": batch_id},
            {"$set": {"payout_status": "unpaid"}, "$unset": {"payout_batch_id": ""}},
        )
        flash("Nothing to pay out right now.", "popup")
        return redirect(url_for(NGO_DASHBOARD_ENDPOINT))

    g.db.payouts.insert_one({
        "_id": batch_id,
        "ngo_email": user["email"],
        "stripe_account_id": account_id,
        "amount_cents": total_cents,
        "currency": PAYOUT_CURRENCY,
        "donation_ids": [d["_id"] for d in claimed],
        "status": "processing",
        "created_at": datetime.now(timezone.utc),
    })

    try:
        transfer = stripe.Transfer.create(
            amount=total_cents,
            currency=PAYOUT_CURRENCY,
            destination=account_id,
            transfer_group=str(batch_id),
            metadata={"payout_batch_id": str(batch_id), "ngo_email": user["email"]},
            idempotency_key=f"payout-{batch_id}",
        )
    except stripe.error.StripeError as exc:
        # Release the claim so the next attempt can pick these donations
        # back up -- nothing is lost or double-counted.
        g.db.donations.update_many(
            {"payout_batch_id": batch_id},
            {"$set": {"payout_status": "unpaid"}, "$unset": {"payout_batch_id": ""}},
        )
        g.db.payouts.update_one(
            {"_id": batch_id}, {"$set": {"status": "failed", "error": str(exc)}}
        )
        flash("Payout failed -- please try again.", "popup")
        return redirect(url_for(NGO_DASHBOARD_ENDPOINT))

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

    flash(f"{total_cents / 100:,.2f} {PAYOUT_CURRENCY.upper()} is on its way!", "popup")
    return redirect(url_for(NGO_DASHBOARD_ENDPOINT))