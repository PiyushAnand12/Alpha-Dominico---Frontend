"""
ALPHAdominico — Compliance-Ready Subscription Backend
======================================================
FastAPI + SQLite + Razorpay

Implements:
  - Subscription creation (create Razorpay subscription server-side)
  - Payment success confirmation + signature verification
  - Razorpay webhook handler with HMAC-SHA256 signature validation
  - Proper subscription status storage
  - Payment ID + email + phone logging
  - Subscription activation timestamps
  - Cancellation endpoint
  - Subscription status check endpoint

Run:
  pip install fastapi uvicorn razorpay python-dotenv
  uvicorn main:app --host 0.0.0.0 --port 8000

Env vars (.env):
  RAZORPAY_KEY_ID=rzp_live_...
  RAZORPAY_KEY_SECRET=...
  RAZORPAY_WEBHOOK_SECRET=...
  RAZORPAY_STANDARD_PLAN_ID=plan_...
  RAZORPAY_PRO_PLAN_ID=plan_...
  FRONTEND_URL=https://yourdomain.com
  ALLOWED_ORIGINS=https://yourdomain.com
"""

import os
import hmac
import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Optional

import razorpay
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr

load_dotenv()

# ── Configuration ─────────────────────────────────────────────────────────────
RAZORPAY_KEY_ID          = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET      = os.getenv("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET  = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
RAZORPAY_PLAN_IDS = {
    "standard": os.getenv("RAZORPAY_STANDARD_PLAN_ID", ""),
    "pro":      os.getenv("RAZORPAY_PRO_PLAN_ID", ""),
}
FRONTEND_URL   = os.getenv("FRONTEND_URL", "http://localhost:3000")
DB_PATH        = os.getenv("DB_PATH", "alphadominico.db")
ALLOWED_ORIGINS= os.getenv("ALLOWED_ORIGINS", FRONTEND_URL).split(",")

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("backend.log", encoding="utf-8"),
    ]
)
log = logging.getLogger("alphadominico")

# ── Razorpay Client ───────────────────────────────────────────────────────────
rz_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

# ── FastAPI App ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="ALPHAdominico Subscription API",
    description="Compliance-ready subscription management backend.",
    version="1.0.0",
    docs_url=None,   # disable public docs in production
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ══════════════════════════════════════════════════════════════════════════════
#  DATABASE
# ══════════════════════════════════════════════════════════════════════════════

INIT_SQL = """
CREATE TABLE IF NOT EXISTS subscribers (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    email                 TEXT NOT NULL UNIQUE,
    name                  TEXT,
    phone                 TEXT,
    plan_key              TEXT NOT NULL DEFAULT 'standard',
    status                TEXT NOT NULL DEFAULT 'trial',
    -- status values: trial | active | cancelled | expired | payment_failed
    razorpay_customer_id  TEXT,
    razorpay_sub_id       TEXT,
    created_at            TEXT NOT NULL,
    trial_start_at        TEXT,
    activated_at          TEXT,
    cancelled_at          TEXT,
    cancel_at_period_end  INTEGER NOT NULL DEFAULT 0,
    updated_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sub_email  ON subscribers(email);
CREATE INDEX IF NOT EXISTS idx_sub_rzid   ON subscribers(razorpay_sub_id);
CREATE INDEX IF NOT EXISTS idx_sub_status ON subscribers(status);

-- Immutable payment log — one row per payment event
CREATE TABLE IF NOT EXISTS payment_log (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    email                 TEXT NOT NULL,
    phone                 TEXT,
    plan_key              TEXT,
    razorpay_payment_id   TEXT NOT NULL UNIQUE,
    razorpay_sub_id       TEXT,
    razorpay_order_id     TEXT,
    amount_paise          INTEGER,
    currency              TEXT DEFAULT 'INR',
    event_type            TEXT,
    -- event_type: subscription.charged | subscription.activated |
    --             payment.captured | manual_success
    status                TEXT,
    webhook_payload       TEXT,
    logged_at             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pl_email ON payment_log(email);
CREATE INDEX IF NOT EXISTS idx_pl_rzpid ON payment_log(razorpay_payment_id);

-- Webhook event log for audit trail
CREATE TABLE IF NOT EXISTS webhook_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event         TEXT,
    entity_id     TEXT,
    payload       TEXT,
    signature_ok  INTEGER NOT NULL DEFAULT 0,
    received_at   TEXT NOT NULL
);
"""


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def db_conn():
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db_conn() as conn:
        conn.executescript(INIT_SQL)
    log.info(f"Database initialised at {DB_PATH}")


# ── Run on startup ────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    init_db()
    log.info("ALPHAdominico backend started")


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def verify_razorpay_signature(
    payment_id: str,
    subscription_id: str,
    signature: str,
) -> bool:
    """Verify Razorpay payment signature (HMAC-SHA256)."""
    message = f"{payment_id}|{subscription_id}"
    expected = hmac.new(
        RAZORPAY_KEY_SECRET.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(body: bytes, signature: str) -> bool:
    """Verify Razorpay webhook signature (HMAC-SHA256)."""
    expected = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def upsert_subscriber(
    conn: sqlite3.Connection,
    *,
    email: str,
    name: Optional[str] = None,
    phone: Optional[str] = None,
    plan_key: str = "standard",
    status: str = "trial",
    razorpay_customer_id: Optional[str] = None,
    razorpay_sub_id: Optional[str] = None,
    trial_start_at: Optional[str] = None,
    activated_at: Optional[str] = None,
    cancelled_at: Optional[str] = None,
    cancel_at_period_end: int = 0,
) -> None:
    now = utcnow()
    existing = conn.execute(
        "SELECT id FROM subscribers WHERE email = ?", (email,)
    ).fetchone()
    if existing:
        fields, vals = [], []
        for col, val in [
            ("name", name), ("phone", phone),
            ("plan_key", plan_key), ("status", status),
            ("razorpay_customer_id", razorpay_customer_id),
            ("razorpay_sub_id", razorpay_sub_id),
            ("trial_start_at", trial_start_at),
            ("activated_at", activated_at),
            ("cancelled_at", cancelled_at),
            ("cancel_at_period_end", cancel_at_period_end),
        ]:
            if val is not None:
                fields.append(f"{col} = ?")
                vals.append(val)
        if fields:
            fields.append("updated_at = ?")
            vals.append(now)
            vals.append(email)
            conn.execute(
                f"UPDATE subscribers SET {', '.join(fields)} WHERE email = ?",
                vals
            )
    else:
        conn.execute(
            """INSERT INTO subscribers
               (email, name, phone, plan_key, status,
                razorpay_customer_id, razorpay_sub_id,
                created_at, trial_start_at, activated_at,
                cancelled_at, cancel_at_period_end, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (email, name, phone, plan_key, status,
             razorpay_customer_id, razorpay_sub_id,
             now, trial_start_at, activated_at,
             cancelled_at, cancel_at_period_end, now)
        )


def log_payment(
    conn: sqlite3.Connection,
    *,
    email: str,
    phone: Optional[str],
    plan_key: str,
    payment_id: str,
    sub_id: Optional[str] = None,
    order_id: Optional[str] = None,
    amount_paise: Optional[int] = None,
    event_type: str = "payment.captured",
    status: str = "captured",
    webhook_payload: Optional[str] = None,
) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO payment_log
           (email, phone, plan_key, razorpay_payment_id, razorpay_sub_id,
            razorpay_order_id, amount_paise, event_type, status,
            webhook_payload, logged_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (email, phone, plan_key, payment_id, sub_id, order_id,
         amount_paise, event_type, status, webhook_payload, utcnow())
    )


# ══════════════════════════════════════════════════════════════════════════════
#  PYDANTIC MODELS
# ══════════════════════════════════════════════════════════════════════════════

class CreateSubscriptionRequest(BaseModel):
    email: str
    name: str
    phone: str
    plan_key: str   # "standard" | "pro"
    plan_id: str    # Razorpay plan ID passed from frontend (verified server-side)


class PaymentSuccessRequest(BaseModel):
    razorpay_payment_id: str
    razorpay_subscription_id: str
    razorpay_signature: str
    email: str
    name: str
    phone: str
    plan_key: str


class CancelRequest(BaseModel):
    email: str


class StatusRequest(BaseModel):
    email: str


# ══════════════════════════════════════════════════════════════════════════════
#  ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/create-subscription")
async def create_subscription(req: CreateSubscriptionRequest):
    """
    Server-side: create Razorpay subscription and return subscription_id + key.
    The plan_id from the request is validated against our server-side map
    so the client cannot tamper with the price.
    """
    # Validate plan key server-side (ignore plan_id from client)
    plan_key = req.plan_key.lower()
    if plan_key not in RAZORPAY_PLAN_IDS:
        raise HTTPException(status_code=400, detail="Invalid plan")
    server_plan_id = RAZORPAY_PLAN_IDS[plan_key]
    if not server_plan_id:
        raise HTTPException(status_code=503, detail="Plan not configured")

    try:
        subscription = rz_client.subscription.create({
            "plan_id":           server_plan_id,
            "total_count":       120,   # max renewal cycles (10 years)
            "quantity":          1,
            "customer_notify":   1,
            "notes": {
                "email":    req.email,
                "name":     req.name,
                "plan_key": plan_key,
            }
        })
        sub_id = subscription["id"]
        log.info(f"Subscription created: {sub_id} | {req.email} | {plan_key}")

        # Pre-register subscriber as 'trial' immediately
        with db_conn() as conn:
            upsert_subscriber(
                conn,
                email=req.email,
                name=req.name,
                phone=req.phone,
                plan_key=plan_key,
                status="trial",
                razorpay_sub_id=sub_id,
                trial_start_at=utcnow(),
            )

        return {"subscription_id": sub_id, "key_id": RAZORPAY_KEY_ID}

    except razorpay.errors.BadRequestError as e:
        log.error(f"Razorpay subscription create error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.exception(f"Unexpected error creating subscription for {req.email}")
        raise HTTPException(status_code=500, detail="Internal error")


@app.post("/api/payment-success")
async def payment_success(req: PaymentSuccessRequest):
    """
    Called from frontend after Razorpay handler fires.
    Verifies signature, marks subscriber as active.
    NOTE: This is a secondary confirmation path. Webhooks are the primary
    source of truth — do not rely solely on this endpoint.
    """
    if not verify_razorpay_signature(
        req.razorpay_payment_id,
        req.razorpay_subscription_id,
        req.razorpay_signature,
    ):
        log.warning(f"Invalid payment signature for payment {req.razorpay_payment_id}")
        raise HTTPException(status_code=400, detail="Invalid payment signature")

    with db_conn() as conn:
        upsert_subscriber(
            conn,
            email=req.email,
            name=req.name,
            phone=req.phone,
            plan_key=req.plan_key,
            status="active",
            razorpay_sub_id=req.razorpay_subscription_id,
            activated_at=utcnow(),
        )
        log_payment(
            conn,
            email=req.email,
            phone=req.phone,
            plan_key=req.plan_key,
            payment_id=req.razorpay_payment_id,
            sub_id=req.razorpay_subscription_id,
            event_type="manual_success",
            status="captured",
        )

    log.info(f"Payment success confirmed: {req.razorpay_payment_id} | {req.email}")
    return {"status": "ok"}


@app.post("/api/webhook/razorpay")
async def razorpay_webhook(request: Request):
    """
    Primary subscription lifecycle handler.
    Razorpay sends events here: subscription.activated, subscription.charged,
    subscription.cancelled, subscription.completed, payment.failed, etc.
    All events are signature-verified before processing.
    """
    body      = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    sig_ok = verify_webhook_signature(body, signature)

    import json
    try:
        payload = json.loads(body)
    except Exception:
        log.error("Webhook: invalid JSON body")
        raise HTTPException(status_code=400, detail="Invalid payload")

    event     = payload.get("event", "")
    entity    = payload.get("payload", {})
    payload_str = json.dumps(payload)

    # Log every webhook for audit trail (even failed ones)
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO webhook_log (event, entity_id, payload, signature_ok, received_at) VALUES (?,?,?,?,?)",
            (event, _extract_entity_id(payload), payload_str, int(sig_ok), utcnow())
        )

    if not sig_ok:
        log.warning(f"Webhook signature invalid for event: {event}")
        # Return 200 to Razorpay to prevent retries, but don't process
        return JSONResponse({"status": "signature_invalid"}, status_code=200)

    log.info(f"Webhook received: {event}")

    # ── subscription.activated ─────────────────────────────────────────────
    if event == "subscription.activated":
        sub_data  = entity.get("subscription", {}).get("entity", {})
        sub_id    = sub_data.get("id", "")
        notes     = sub_data.get("notes", {})
        email     = notes.get("email", "")
        plan_key  = notes.get("plan_key", "standard")
        if email:
            with db_conn() as conn:
                upsert_subscriber(conn, email=email, plan_key=plan_key,
                    status="active", razorpay_sub_id=sub_id,
                    activated_at=utcnow())
            log.info(f"Subscription activated: {sub_id} | {email}")

    # ── subscription.charged ──────────────────────────────────────────────
    elif event == "subscription.charged":
        payment_data = entity.get("payment",      {}).get("entity", {})
        sub_data     = entity.get("subscription", {}).get("entity", {})
        payment_id   = payment_data.get("id", "")
        sub_id       = sub_data.get("id", "")
        amount       = payment_data.get("amount", 0)      # in paise
        notes        = sub_data.get("notes", {})
        email        = notes.get("email", "")
        plan_key     = notes.get("plan_key", "standard")
        contact      = payment_data.get("contact", "")
        if email and payment_id:
            with db_conn() as conn:
                upsert_subscriber(conn, email=email, plan_key=plan_key,
                    status="active", razorpay_sub_id=sub_id)
                log_payment(conn, email=email, phone=contact,
                    plan_key=plan_key, payment_id=payment_id,
                    sub_id=sub_id, amount_paise=amount,
                    event_type="subscription.charged", status="captured",
                    webhook_payload=payload_str)
            log.info(f"Charged: {payment_id} | ₹{amount/100:.2f} | {email}")

    # ── subscription.cancelled ────────────────────────────────────────────
    elif event == "subscription.cancelled":
        sub_data = entity.get("subscription", {}).get("entity", {})
        sub_id   = sub_data.get("id", "")
        notes    = sub_data.get("notes", {})
        email    = notes.get("email", "")
        if email:
            with db_conn() as conn:
                upsert_subscriber(conn, email=email, status="cancelled",
                    razorpay_sub_id=sub_id, cancelled_at=utcnow())
            log.info(f"Subscription cancelled: {sub_id} | {email}")

    # ── subscription.completed ────────────────────────────────────────────
    elif event == "subscription.completed":
        sub_data = entity.get("subscription", {}).get("entity", {})
        sub_id   = sub_data.get("id", "")
        notes    = sub_data.get("notes", {})
        email    = notes.get("email", "")
        if email:
            with db_conn() as conn:
                upsert_subscriber(conn, email=email, status="expired",
                    razorpay_sub_id=sub_id)
            log.info(f"Subscription completed/expired: {sub_id} | {email}")

    # ── payment.failed ────────────────────────────────────────────────────
    elif event == "payment.failed":
        payment_data = entity.get("payment", {}).get("entity", {})
        sub_id       = payment_data.get("subscription_id", "")
        email        = payment_data.get("email", "")
        payment_id   = payment_data.get("id", "")
        if email:
            with db_conn() as conn:
                upsert_subscriber(conn, email=email, status="payment_failed")
                log_payment(conn, email=email, phone=None,
                    plan_key="unknown", payment_id=payment_id or f"fail_{utcnow()}",
                    sub_id=sub_id, event_type="payment.failed",
                    status="failed", webhook_payload=payload_str)
            log.warning(f"Payment failed: {payment_id} | {email}")

    return JSONResponse({"status": "processed"})


@app.post("/api/cancel-subscription")
async def cancel_subscription(req: CancelRequest):
    """
    Cancel a user's Razorpay subscription at end of current billing period.
    The subscription remains active until the period end.
    """
    with db_conn() as conn:
        row = conn.execute(
            "SELECT razorpay_sub_id, status FROM subscribers WHERE email = ?",
            (req.email,)
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Subscriber not found")

    sub_id = row["razorpay_sub_id"]
    status = row["status"]

    if status in ("cancelled", "expired"):
        return {"status": "already_cancelled"}

    if sub_id:
        try:
            rz_client.subscription.cancel(sub_id, {"cancel_at_cycle_end": 1})
            log.info(f"Subscription cancel-at-period-end set: {sub_id} | {req.email}")
        except Exception as e:
            log.error(f"Razorpay cancel error for {sub_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Razorpay error: {e}")

    with db_conn() as conn:
        upsert_subscriber(conn, email=req.email,
                          cancel_at_period_end=1, status="active")
        # Full cancellation will be processed by the subscription.cancelled webhook

    return {"status": "cancel_scheduled", "message":
            "Your subscription will not renew. Access continues until end of billing period."}


@app.get("/api/subscription-status")
async def subscription_status(email: str):
    """
    Return current subscription status for a given email.
    Used by the screener to gate report delivery.
    """
    if not email:
        raise HTTPException(status_code=400, detail="email required")

    with db_conn() as conn:
        row = conn.execute(
            """SELECT email, name, plan_key, status,
                      razorpay_sub_id, trial_start_at, activated_at,
                      cancelled_at, cancel_at_period_end, updated_at
               FROM subscribers WHERE email = ?""",
            (email,)
        ).fetchone()

    if not row:
        return {"status": "not_found", "active": False}

    active = row["status"] in ("trial", "active")
    return {
        "email":                 row["email"],
        "name":                  row["name"],
        "plan_key":              row["plan_key"],
        "status":                row["status"],
        "active":                active,
        "razorpay_sub_id":       row["razorpay_sub_id"],
        "trial_start_at":        row["trial_start_at"],
        "activated_at":          row["activated_at"],
        "cancelled_at":          row["cancelled_at"],
        "cancel_at_period_end":  bool(row["cancel_at_period_end"]),
        "updated_at":            row["updated_at"],
    }


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "alphadominico-backend"}


# ── Private helper ─────────────────────────────────────────────────────────
def _extract_entity_id(payload: dict) -> str:
    try:
        for key in ("subscription", "payment", "order"):
            entity = payload.get("payload", {}).get(key, {}).get("entity", {})
            if entity.get("id"):
                return entity["id"]
    except Exception:
        pass
    return ""
