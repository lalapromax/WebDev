# MEDonate

MEDonate is a medical crowdfunding platform built with Flask and MongoDB Atlas. It connects donors with NGOs running medical campaigns, and gives admins the tools to verify accounts and keep the platform trustworthy.

Web Development Project, August 2026 Semester.

## Who uses it

| Role | What they can do |
|------|------------------|
| **Donor** | Browse campaigns, donate money (Stripe) or medical equipment, comment, view donation history, earn and claim rewards, appear on the donor ranking |
| **NGO** | Register, create and preview campaigns, review equipment donations, post updates to donors, receive payouts through Stripe |
| **Admin** | Verify NGOs and users, view analytics, review the audit log, manage rewards and claims, remove accounts |

## Features

- Email/password login, Google sign-in, and password reset by email
- One-time-password (OTP) support via PyOTP
- Card donations through Stripe Checkout, with NGO payouts (`ngo_payouts.py`)
- Equipment donations (beds, oxygen, wheelchairs, etc.) that NGOs accept or reject
- Campaign comments, update feed and unread notifications
- Rewards/perks system and donor leaderboard
- Admin dashboard, analytics and audit trail

## Tech stack

- **Backend:** Python, Flask, Flask-PyMongo, Authlib, Flask-Mail, Stripe
- **Database:** MongoDB Atlas
- **Frontend:** Jinja2 templates, HTML, CSS, JavaScript
- **Deployment:** Gunicorn

## Project structure

```
WebDev/
├── app.py              # Main Flask app: routes and logic
├── ngo_payouts.py      # Blueprint for NGO Stripe payouts
├── seed_demo.py        # Fills the database with demo NGO data
├── requirements.txt    # Python dependencies
├── templates/          # Jinja2 HTML pages
├── static/             # CSS, JavaScript, images, uploads
├── docs/diagrams/      # Use case, class, sequence and ER diagrams
└── transfer.md         # Project handover notes
```

## Getting started

### 1. Prerequisites

- Python 3.10 or newer
- A MongoDB Atlas cluster
- A Stripe account (test mode is fine)
- A Google OAuth client and a Gmail app password (for login and email)

### 2. Install

```bash
git clone https://github.com/lalapromax/WebDev.git
cd WebDev
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux
pip install -r requirements.txt
```

### 3. Configure

Create a `.env` file in the project root. **Never commit this file.**

```env
MONGODB_URI=
MONGO_DB_NAME=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
MAIL_USERNAME=
MAIL_PASSWORD=
STRIPE_SECRET_KEY=
STRIPE_PUBLISHABLE_KEY=
STRIPE_WEBHOOK_SECRET=
```

### 4. Run

```bash
python app.py
```

Then open http://127.0.0.1:5000.

For production:

```bash
gunicorn app:app
```

### 5. Optional: load demo data

```bash
python seed_demo.py                 # first NGO account found
python seed_demo.py ngo@email.com   # a specific NGO
python seed_demo.py --clear         # remove all demo data
```

Demo records are tagged, so real data is never touched.

## Documentation

System diagrams are in `docs/diagrams/` (PNG), with editable sources in `docs/diagrams/source/`. See `transfer.md` for the full project handover summary.

## Team

Beldon, Lakshna, Ming Rae
