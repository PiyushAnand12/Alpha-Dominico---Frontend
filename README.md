# ALPHAdominico — Compliance Package
## Razorpay-Ready Structure

```
alphadominico/
│
├── frontend/                        ← Static HTML site
│   ├── index.html                   ← Landing page (compliance-patched)
│   ├── about.html                   ← About Us page
│   ├── terms.html                   ← Terms & Conditions
│   ├── privacy.html                 ← Privacy Policy
│   ├── refund.html                  ← Refund & Cancellation Policy
│   ├── contact.html                 ← Contact Us
│   ├── checkout.html                ← Payment / subscription page (Razorpay)
│   └── welcome.html                 ← Post-payment success page (create yourself)
│
├── backend/
│   ├── main.py                      ← FastAPI app (all endpoints)
│   ├── requirements.txt             ← pip dependencies
│   ├── .env.example                 ← Copy to .env, fill in keys
│   ├── .env                         ← Your secrets (NEVER commit)
│   ├── alphadominico.db             ← SQLite database (auto-created)
│   └── backend.log                  ← Application log (auto-created)
│
└── screener/
    ├── screener.py                  ← Your existing screener
    └── ...
```

---

## Razorpay Setup Checklist

### 1. Create an account
- Go to https://dashboard.razorpay.com
- Sign up as **Individual / Sole Proprietor**
- Upload KYC: PAN card + bank account + address proof
- Website URL: your domain with all compliance pages live

### 2. Create Subscription Plans
Dashboard → Subscriptions → Plans → + New Plan

| Plan     | Interval | Amount   | Plan ID to save in .env           |
|----------|----------|----------|-----------------------------------|
| Standard | Monthly  | ₹399     | `RAZORPAY_STANDARD_PLAN_ID`       |
| Pro      | Monthly  | ₹999     | `RAZORPAY_PRO_PLAN_ID`            |

### 3. Register Webhook
Dashboard → Settings → Webhooks → + Add New Webhook

- URL: `https://yourdomain.com/api/webhook/razorpay`
- Events to subscribe:
  - `subscription.activated`
  - `subscription.charged`
  - `subscription.cancelled`
  - `subscription.completed`
  - `payment.failed`
- Copy the webhook secret → `RAZORPAY_WEBHOOK_SECRET` in .env

### 4. Get API Keys
Dashboard → Settings → API Keys → Generate Key

- Key ID → `RAZORPAY_KEY_ID`
- Key Secret → `RAZORPAY_KEY_SECRET`

### 5. Fill checkout.html
In checkout.html, the `plan_id` values passed to the backend are validated
server-side against `RAZORPAY_PLAN_IDS` in main.py. The frontend never
controls the actual plan price — the server always uses its own plan ID map.

---

## Backend — Run Instructions

```bash
cd backend/
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your real keys
uvicorn main:app --host 0.0.0.0 --port 8000
```

For production, use a process manager:
```bash
gunicorn main:app -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

---

## Database Schema Summary

### subscribers
| Column              | Type    | Description                                      |
|---------------------|---------|--------------------------------------------------|
| email               | TEXT    | Primary identifier                               |
| name                | TEXT    | Full name                                        |
| phone               | TEXT    | Phone (required by Razorpay)                     |
| plan_key            | TEXT    | "standard" or "pro"                              |
| status              | TEXT    | trial / active / cancelled / expired / payment_failed |
| razorpay_sub_id     | TEXT    | Razorpay subscription ID                         |
| trial_start_at      | TEXT    | ISO8601 UTC timestamp                            |
| activated_at        | TEXT    | When first payment charged                       |
| cancelled_at        | TEXT    | When cancelled                                   |
| cancel_at_period_end| INTEGER | 1 if scheduled to cancel at next renewal         |

### payment_log
| Column              | Type    | Description                                      |
|---------------------|---------|--------------------------------------------------|
| email               | TEXT    | Subscriber email                                 |
| phone               | TEXT    | Phone at time of payment                         |
| razorpay_payment_id | TEXT    | Unique per charge — UNIQUE constraint            |
| razorpay_sub_id     | TEXT    | Subscription ID                                  |
| amount_paise        | INTEGER | Amount in paise (₹399 = 39900)                   |
| event_type          | TEXT    | subscription.charged / manual_success / etc.     |
| webhook_payload     | TEXT    | Raw JSON for audit                               |
| logged_at           | TEXT    | ISO8601 UTC timestamp                            |

---

## API Endpoints

| Method | Path                        | Description                          |
|--------|-----------------------------|--------------------------------------|
| POST   | /api/create-subscription    | Creates Razorpay subscription        |
| POST   | /api/payment-success        | Confirms payment + verifies signature|
| POST   | /api/webhook/razorpay       | Webhook handler (primary truth source)|
| POST   | /api/cancel-subscription    | Cancel at end of period              |
| GET    | /api/subscription-status    | Check if user is active subscriber   |
| GET    | /api/health                 | Health check                         |

---

## Compliance Pages Summary (Razorpay Review Checklist)

| Requirement                              | Page               | Status |
|------------------------------------------|--------------------|--------|
| Terms & Conditions                       | terms.html         | ✓      |
| Privacy Policy                           | privacy.html       | ✓      |
| Refund & Cancellation Policy             | refund.html        | ✓      |
| Contact Us with email + address          | contact.html       | ✓      |
| About Us with founder info               | about.html         | ✓      |
| Not SEBI investment advice disclaimer    | All pages          | ✓      |
| No profit guarantees                     | All pages          | ✓      |
| Market risk disclosure                   | All pages          | ✓      |
| Auto-renewal disclosed                   | terms + checkout   | ✓      |
| Refund eligibility clearly stated        | refund.html        | ✓      |
| Subscription prices clearly shown        | checkout.html      | ✓      |
| Billing frequency shown                  | checkout.html      | ✓      |
| Refund/Terms links near pay button       | checkout.html      | ✓      |
| Founder name + business type             | about + footer     | ✓      |
| Support email (domain-based)             | contact + footer   | ✓      |
| No misleading urgency tactics            | All pages          | ✓      |

---

## Before Going Live — Replace These Placeholders

Search all files for and replace:

- `[Your Full Name]` → Your actual name (as per PAN / KYC)
- `support@alphadominico.com` → Your actual domain email
- `YOUR_RAZORPAY_STANDARD_PLAN_ID` → From Razorpay dashboard
- `YOUR_RAZORPAY_PRO_PLAN_ID` → From Razorpay dashboard
- `01 January 2025` in legal pages → Actual launch date

---

*This compliance package is provided as a practical implementation guide.
It is not legal advice. Consult a qualified legal professional if needed.*
