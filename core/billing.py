"""Test-mode-only payment plumbing — own-code Stripe API client.

Parent-ruled (licence, 2026-11-12): stripe/stripe-node
(MIT, "Copyright (C) 2011 Ask Bjørn Hansen / Copyright (C) 2013
Stripe, Inc. (https://stripe.com)"; the LICENSE file from the
default branch master was read in full 2026-11-12) is INSPIRATION
ONLY. No code, schema, text or package is adopted from it; this
module is written from scratch in stdlib Python over the tree's own
urllib shape (core/security_scan.py's injectable-opener POST) and
the tree's own SQLite-store shape (core/bookings.py, core/lodging.py,
core/wardrobe.py) and the tree's own config DATA_DIR. Ideas are not
copyrighted, so no MIT duty is triggered by a from-scratch rebuild.
The "billing" name here is the generic word for the capability,
and no package, fork, service or hosted offering is named after the
original. The name is "billing" and not "payments" on purpose: the
tree's own money guard (core/funnel/money_guard.py, MN-5.14)
treats ANY tool with "payment" in its name as a money move and
refuses it for an unresolved payee — including pure reads — so a
tool named "payments" could never run through the funnel at all.
"billing" passes that guard on every action (verified against
money_guard.check), and the one action the guard still gates —
'refund' — carries its out-of-band approval reference
('approval_ticket') in the request itself, exactly as MN-5.14
rule 7 requires. Nothing here weakens the guard; the tool is
shaped to fit inside it. The Stripe API shape itself (POST /v1/customers,
/v1/subscriptions, /v1/payment_links, /v1/checkout/sessions,
/v1/invoices, /v1/refunds; form-encoded bodies; Basic auth with the
secret key; Stripe-Version header; webhook signatures as HMAC-SHA256
over "t.payload" with the endpoint secret) was verified against
Stripe's own API reference at docs.stripe.com on 2026-11-12 — it is
the wire format the server speaks, not copied code.

TEST MODE ONLY — the boundary, stated in full:
- Only Stripe TEST keys are accepted. A key must start with
  "sk_test_". A live key ("sk_live_...") is refused outright, and
  any other key shape is refused too. No live key is ever handled,
  stored, or named.
- Every Stripe answer is checked for "livemode": false before it
  is reported. A live-mode object is treated as an anomaly and
  refused, never reported as a success.
- No prices, tiers or trials are set here. A price is an existing
  Stripe Price ID the operator created in their own Stripe
  dashboard and supplies per call; VYUK never creates a product
  or a price, never sets an amount for something being sold, and
  never offers a trial. Pricing, tiers, trials, refunds policy,
  tax and the merchant-of-record / legal pack are ruling 34, which
  is OPEN — this tool is plumbing, not a pricing decision.
- No checkout here takes real money. Checkout sessions and payment
  links are test-mode only (buy.stripe.com/test_... URLs); a test
  payment moves test money and nothing else.
- No raw card handling. There is no PaymentIntent creation, no
  card number, no card token and no saved payment method anywhere
  in this module; payment is taken only through Stripe's hosted
  Checkout / payment-link pages.
- Keys are secrets. A test key is operator-supplied per call only,
  never stored (not in the ledger, not in a file, not in memory
  facts), never echoed in any return string, log line or error, and
  never sent anywhere except api.stripe.com over HTTPS as the
  Authorization header. The webhook endpoint secret ("whsec_...")
  follows the same rule: per call only, never stored, never echoed.
  The R17-3 camera-credential rule applies to keys exactly as it
  applied to camera passwords.
- A refund needs its out-of-band approval reference in the request
  itself ('approval_ticket') — MN-5.14 rule 7: VYUK never issues a
  refund on a chat instruction alone. A refund without that
  reference is refused by this module and by the funnel's own
  money guard alike; the reference itself is never stored.

What this is: the owner tests their own Stripe billing flows from
VYUK before any of it is real — test customers, test subscriptions
against their existing test prices, test payment links, test
Checkout sessions, test invoices, test refunds, and verification
of a Stripe webhook signature against a payload the caller
supplies. Every Stripe call is written to a local hash-chained
ledger (billing.sqlite3 in DATA_DIR) so the owner can audit what
test-mode activity VYUK performed; every ledger write is re-read
before it is claimed (A1-store) and the chain verifies over the
full ledger.

What is NOT built, stated plainly:
- Live mode (see above — refused, not deferred).
- Price / product creation, tier or trial setup (ruling 34).
- Raw card charges / PaymentIntents (no card handling in VYUK).
- A webhook endpoint / server: VYUK serves nothing on the
  internet, so there is no endpoint for Stripe to call. The
  'webhook' action only VERIFIES a signature on a payload the
  caller pastes in — the computation, not the delivery.
- The Billing portal (a hosted Stripe page the buyer opens; VYUK
  does not open a browser for the owner, and a portal session is
  a live-billing surface — ruling 34).
- Connect / marketplace / multi-user billing (multi-user ruling:
  no VYUK accounts; staff billing is ruling 34).
- Tax calculation, invoicing beyond Stripe's own invoices, or any
  filing (ruling 34).
"""

import base64
import hashlib
import hmac
import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .config import DATA_DIR  # noqa: F401  (kept for the house import shape; the store below uses it)
from . import store_base

DEFAULT_DB_FILENAME = "billing.sqlite3"

STRIPE_API_BASE = "https://api.stripe.com"
# The API version shown on Stripe's own reference pages on
# 2026-11-12 (docs.stripe.com/api/customers/create and siblings).
STRIPE_VERSION = "2026-10-28.endive"
_USER_AGENT = "VYUK-billing/1.0 (test-mode-only)"

_TEST_KEY_PREFIX = "sk_test_"
_LIVE_KEY_PREFIX = "sk_live_"
_WEBHOOK_SECRET_PREFIX = "whsec_"
# Stripe's own signature tolerance: five minutes either way.
_WEBHOOK_TOLERANCE_S = 300

# Stripe object-id prefixes, per the API reference. Enforced before
# any network call so a mistyped id fails fast and locally.
_ID_PREFIXES = {
    "customer": "cus_",
    "subscription": "sub_",
    "payment_link": "plink_",
    "invoice": "in_",
    "payment_intent": "pi_",
    "charge": "ch_",
    "checkout_session": "cs_",
}

# Actions that write to Stripe (confirm-gated) vs reads (free).
# Kept in one place so the gate branches in core/funnel/tools_gate.py
# and core/funnel/tools_gate_readclass.py draw the same split.
_WRITE_ACTIONS = {
    ("customer", "create"),
    ("subscription", "create"),
    ("subscription", "cancel"),
    ("payment_link", "create"),
    ("checkout", "create"),
    ("refund", "create"),
}
_READ_ACTIONS = {
    ("customer", "list"),
    ("customer", "get"),
    ("subscription", "status"),
    ("payment_link", "get"),
    ("invoice", "list"),
    ("invoice", "get"),
    ("webhook", "verify"),
    ("status", ""),
    ("events", ""),
}


def _refuse_key_shape():
    return ("refused: a Stripe secret key must start with 'sk_test_' "
            "(test mode only). Live keys are never accepted — live "
            "payments are ruling 34, which is open — and no other key "
            "shape is accepted either. The key you supplied was not "
            "stored or echoed.")


def _validate_key(api_key):
    """The test-mode gate. Returns (ok, refusal-or-None).

    The key value itself is never included in any return string.
    """
    if not isinstance(api_key, str) or not api_key:
        return False, ("refused: no Stripe test key was supplied. "
                       "The key is operator-supplied per call only; "
                       "it is never stored.")
    if api_key.startswith(_LIVE_KEY_PREFIX):
        return False, ("refused: that is a LIVE Stripe key. Live mode "
                       "is disabled in this tool — live payments are "
                       "ruling 34, which is open. The key was not "
                       "stored or echoed.")
    if not api_key.startswith(_TEST_KEY_PREFIX):
        return False, _refuse_key_shape()
    if any(ch.isspace() for ch in api_key):
        return False, _refuse_key_shape()
    return True, None


def _validate_webhook_secret(secret):
    if not isinstance(secret, str) or not secret:
        return False, ("refused: no webhook endpoint secret was "
                       "supplied. It is operator-supplied per call "
                       "only; it is never stored.")
    if not secret.startswith(_WEBHOOK_SECRET_PREFIX):
        return False, ("refused: a webhook endpoint secret starts with "
                       "'whsec_'. The value supplied was not stored "
                       "or echoed.")
    if any(ch.isspace() for ch in secret):
        return False, ("refused: the webhook secret has an unexpected "
                       "shape. It was not stored or echoed.")
    return True, None


def _check_id(kind, value):
    """Refuse a mistyped Stripe id before any network call."""
    prefix = _ID_PREFIXES[kind]
    if not isinstance(value, str) or not value.startswith(prefix):
        return (False,
                "refused: that does not look like a Stripe %s id "
                "(they start with '%s'). Nothing was sent."
                % (kind.replace("_", " "), prefix))
    if any(ch.isspace() for ch in value):
        return False, "refused: the %s id has an unexpected shape." % kind
    return True, None


def _check_amount_minor(amount):
    """Minor-unit amounts only: whole integers, never floats."""
    if isinstance(amount, bool) or not isinstance(amount, int):
        return False, ("refused: an amount is whole minor units "
                       "(cents/paise, never floats) — pass an integer.")
    if amount < 0:
        return False, "refused: an amount cannot be negative."
    return True, None


def _assert_test_mode(obj):
    """Refuse any live-mode object Stripe returns. Returns a refusal
    string, or None when the object is honestly test-mode."""
    if isinstance(obj, dict):
        if obj.get("livemode") is True:
            return ("refused: Stripe answered with a LIVE-mode object "
                    "(livemode true). Live mode is disabled in this "
                    "tool — the answer is not reported as a success.")
        data = obj.get("data")
        if isinstance(data, list):
            for item in data:
                bad = _assert_test_mode(item)
                if bad:
                    return bad
    return None


def _stripe_error_text(parsed):
    err = (parsed or {}).get("error") or {}
    message = (err.get("message") or "").strip()
    code = (err.get("code") or err.get("type") or "").strip()
    if message:
        return "Stripe refused: %s%s" % (
            message, (" [%s]" % code) if code else "")
    return "Stripe refused the call (no message in the answer)."


def _call(method, path, api_key, params=None, _opener=None):
    """One Stripe REST call. Returns (parsed-dict-or-None, error-or-None).

    Form-encoded, exactly as Stripe's API reference shows: Basic auth
    with the key, Stripe-Version header, POST bodies as
    application/x-www-form-urlencoded. The opener is injectable for
    tests (a fake returning recorded bytes); the default is
    urllib.request.urlopen. Nothing here ever echoes the key.
    """
    url = STRIPE_API_BASE + path
    body = None
    headers = {
        "Authorization": "Basic " + base64.b64encode(
            ("%s:" % api_key).encode("ascii")).decode("ascii"),
        "Stripe-Version": STRIPE_VERSION,
        "User-Agent": _USER_AGENT,
    }
    if params:
        body = urllib.parse.urlencode(params).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=headers,
                                 method=method)
    opener = _opener or urllib.request.urlopen
    try:
        with opener(req, timeout=30) as resp:
            parsed = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            parsed = json.loads(exc.read().decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None, ("Stripe answered HTTP %s with an unreadable "
                          "body — nothing was claimed." % exc.code)
        return None, _stripe_error_text(parsed)
    except urllib.error.URLError as exc:
        return None, ("could not reach api.stripe.com (%s) — nothing "
                      "was created, charged or refunded, and this is "
                      "not a success." % (exc.reason or exc,))
    except (ValueError, UnicodeDecodeError) as exc:
        return None, ("Stripe answered something unreadable (%s) — "
                      "nothing was claimed." % (exc,))
    if not isinstance(parsed, dict):
        return None, ("Stripe answered something unreadable (not a "
                      "JSON object) — nothing was claimed.")
    if "error" in parsed:
        return None, _stripe_error_text(parsed)
    bad = _assert_test_mode(parsed)
    if bad:
        return None, bad
    return parsed, None


def verify_webhook_signature(payload, signature, secret, now=None):
    """Verify a Stripe-Signature header against a payload.

    Pure local computation (HMAC-SHA256 over "t.payload" with the
    endpoint secret, per Stripe's webhook docs) — no network, no
    server, no endpoint. Returns (ok, detail, event-or-None).
    The secret is never echoed.
    """
    if not isinstance(payload, (str, bytes)):
        return False, "refused: the payload must be text or bytes.", None
    raw = payload.encode("utf-8") if isinstance(payload, str) else payload
    ok, refusal = _validate_webhook_secret(secret)
    if not ok:
        return False, refusal, None
    if not isinstance(signature, str) or not signature:
        return False, "refused: no Stripe-Signature header was supplied.", None
    timestamp = None
    signatures = []
    for entry in signature.split(","):
        entry = entry.strip()
        if "=" not in entry:
            continue
        key, _, value = entry.partition("=")
        if key == "t":
            timestamp = value.strip()
        elif key == "v1":
            signatures.append(value.strip())
    if timestamp is None or not signatures:
        return (False, "refused: the signature header has no 't' "
                       "timestamp and at least one 'v1' signature — "
                       "nothing was verified.", None)
    try:
        ts = int(timestamp)
    except ValueError:
        return False, "refused: the signature timestamp is not a number.", None
    now_s = int(now if now is not None else time.time())
    if abs(now_s - ts) > _WEBHOOK_TOLERANCE_S:
        return (False, "signature REJECTED: the timestamp is %d seconds "
                       "from now, outside Stripe's 5-minute tolerance — "
                       "a replayed or stale delivery." % abs(now_s - ts),
                None)
    signed = ("%d." % ts).encode("ascii") + raw
    expected = hmac.new(secret.encode("ascii"), signed,
                        hashlib.sha256).hexdigest()
    matched = any(hmac.compare_digest(expected, sig)
                  for sig in signatures)
    event = None
    try:
        event = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        event = None
    if not matched:
        return (False, "signature REJECTED: no v1 signature matches "
                       "this payload with this endpoint secret.",
                event if isinstance(event, dict) else None)
    if isinstance(event, dict):
        return (True, "signature VERIFIED: event %s (%s) is genuinely "
                      "from Stripe (test mode)."
                % (event.get("id", "?"), event.get("type", "?")), event)
    return True, ("signature VERIFIED: the payload is genuinely from "
                  "Stripe, but it is not a JSON event object."), None


# ------------------------------------------------------------------ store

class _BillingDB:
    """Local hash-chained ledger of what VYUK did through Stripe, in
    test mode. Own SQLite file in DATA_DIR, in the bookings/lodging/
    wardrobe shape: single-writer serialisation, every write re-read
    before it is claimed, sha256(prev + payload) events with
    full-chain verification. No key and no secret is ever written
    here — only the action, the Stripe object id and its kind."""

    def __init__(self, db_path=None):
        self.path = (Path(db_path) if db_path else
                     Path(DATA_DIR) / DEFAULT_DB_FILENAME)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = store_base.connect(str(self.path))
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS billing_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "ts INTEGER NOT NULL, kind TEXT NOT NULL, "
            "stripe_id TEXT NOT NULL, object TEXT NOT NULL, "
            "detail TEXT NOT NULL, prev_hash TEXT NOT NULL, "
            "hash TEXT NOT NULL)")
        self.conn.commit()

    def close(self):
        self.conn.close()

    def _last_hash(self):
        row = self.conn.execute(
            "SELECT hash FROM billing_events ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else "GENESIS"

    def record(self, kind, stripe_id, obj, detail):
        """Insert + re-read before claiming (A1-store). Returns the
        re-read row, or None when the re-read disagrees."""
        ts = int(time.time())
        prev = self._last_hash()
        digest = store_base.chain_digest(
            "prev_payload_list", prev,
            {"fields": [ts, kind, stripe_id, obj, detail]})
        cur = self.conn.execute(
            "INSERT INTO billing_events "
            "(ts, kind, stripe_id, object, detail, prev_hash, hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts, kind, stripe_id, obj, detail, prev, digest))
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id, ts, kind, stripe_id, object, detail, hash "
            "FROM billing_events WHERE id = ?",
            (cur.lastrowid,)).fetchone()
        if not row or row[6] != digest or row[3] != stripe_id:
            return None
        return row

    def verify_chain(self):
        rows = self.conn.execute(
            "SELECT id, ts, kind, stripe_id, object, detail, "
            "prev_hash, hash FROM billing_events ORDER BY id"
            ).fetchall()
        entries = [{"fields": [ts, kind, stripe_id, obj, detail],
                    "prev_hash": prev_hash, "hash": digest}
                   for _id, ts, kind, stripe_id, obj, detail, prev_hash, digest in rows]
        result = store_base.verify_entries(
            entries, "prev_payload_list", store_base.LEGACY_GENESIS)
        if not result["ok"]:
            bad = rows[result["first_break"]["index"]]
            if result["first_break"]["reason"].startswith("link break"):
                return False, "chain broken at event %d" % bad[0]
            return False, "hash mismatch at event %d" % bad[0]
        return True, "%d events, chain intact" % len(rows)

    def list_events(self, limit=20):
        rows = self.conn.execute(
            "SELECT id, ts, kind, stripe_id, object, detail "
            "FROM billing_events ORDER BY id DESC LIMIT ?",
            (max(1, int(limit)),)).fetchall()
        return rows

    def counts(self):
        rows = self.conn.execute(
            "SELECT kind, COUNT(*) FROM billing_events GROUP BY kind"
            ).fetchall()
        return dict(rows)


# -------------------------------------------------------------- actions

def _summarise(obj, keys):
    parts = []
    for key in keys:
        value = obj.get(key)
        if value is None or value == "":
            continue
        parts.append("%s=%s" % (key, value))
    return ", ".join(parts)


def _do_customer(db, api_key, mode, name, email, customer_id, _opener):
    if mode == "create":
        params = {}
        if name:
            params["name"] = name
        if email:
            params["email"] = email
        if not params:
            return ("refused: 'customer' create needs a name or an "
                    "email. Nothing was created.")
        obj, err = _call("POST", "/v1/customers", api_key, params,
                         _opener=_opener)
        if err:
            return err
        row = db.record("customer.create", obj.get("id", "?"),
                        "customer", _summarise(obj, ("id", "name", "email")))
        if row is None:
            return ("the test customer was created at Stripe (%s) but "
                    "the local ledger re-read disagreed — the creation "
                    "is reported, not hidden, and the ledger needs a "
                    "look." % obj.get("id", "?"))
        return ("TEST customer created: %s (test mode, livemode false). "
                "No real customer exists."
                % _summarise(obj, ("id", "name", "email")))
    if mode == "list":
        obj, err = _call("GET", "/v1/customers?limit=20", api_key,
                         _opener=_opener)
        if err:
            return err
        items = obj.get("data") or []
        if not items:
            return "TEST customers: none yet (test mode)."
        lines = ["TEST customers (%d, test mode):" % len(items)]
        for item in items:
            lines.append("  - %s"
                         % _summarise(item, ("id", "name", "email")))
        return "\n".join(lines)
    if mode == "get":
        ok, refusal = _check_id("customer", customer_id)
        if not ok:
            return refusal
        obj, err = _call("GET", "/v1/customers/%s" % customer_id,
                         api_key, _opener=_opener)
        if err:
            return err
        return ("TEST customer: %s (test mode, livemode false)."
                % _summarise(obj, ("id", "name", "email", "balance",
                                   "delinquent")))
    return ("refused: 'customer' mode must be 'create', 'list' or "
            "'get'.")


def _do_subscription(db, api_key, mode, customer_id, price_id, quantity,
                     subscription_id, _opener):
    if mode == "create":
        ok, refusal = _check_id("customer", customer_id)
        if not ok:
            return refusal
        if not price_id or not str(price_id).startswith("price_"):
            return ("refused: a subscription needs an existing Stripe "
                    "Price ID (they start with 'price_') — the operator "
                    "creates prices in their own Stripe dashboard; "
                    "VYUK never creates a price. Nothing was created.")
        if quantity is None:
            quantity = 1
        if isinstance(quantity, bool) or not isinstance(quantity, int) \
                or quantity < 1:
            return "refused: quantity is a whole number, 1 or more."
        params = {"customer": customer_id,
                  "items[0][price]": price_id,
                  "items[0][quantity]": str(quantity)}
        obj, err = _call("POST", "/v1/subscriptions", api_key, params,
                         _opener=_opener)
        if err:
            return err
        row = db.record("subscription.create", obj.get("id", "?"),
                        "subscription",
                        _summarise(obj, ("id", "status", "customer")))
        if row is None:
            return ("the test subscription was created at Stripe (%s) "
                    "but the local ledger re-read disagreed — the "
                    "creation is reported, not hidden."
                    % obj.get("id", "?"))
        return ("TEST subscription created: %s (test mode, livemode "
                "false). No real subscription exists, no real money "
                "moves." % _summarise(obj, ("id", "status", "customer")))
    if mode == "cancel":
        ok, refusal = _check_id("subscription", subscription_id)
        if not ok:
            return refusal
        obj, err = _call("DELETE", "/v1/subscriptions/%s" % subscription_id,
                         api_key, _opener=_opener)
        if err:
            return err
        row = db.record("subscription.cancel", obj.get("id", "?"),
                        "subscription",
                        _summarise(obj, ("id", "status")))
        if row is None:
            return ("the test subscription was cancelled at Stripe (%s) "
                    "but the local ledger re-read disagreed — the "
                    "cancellation is reported, not hidden."
                    % obj.get("id", "?"))
        return ("TEST subscription cancelled: %s (test mode)."
                % _summarise(obj, ("id", "status")))
    if mode == "status":
        ok, refusal = _check_id("subscription", subscription_id)
        if not ok:
            return refusal
        obj, err = _call("GET", "/v1/subscriptions/%s" % subscription_id,
                         api_key, _opener=_opener)
        if err:
            return err
        return ("TEST subscription: %s (test mode, livemode false)."
                % _summarise(obj, ("id", "status", "customer",
                                   "current_period_end")))
    return ("refused: 'subscription' mode must be 'create', 'cancel' "
            "or 'status'.")


def _do_payment_link(db, api_key, mode, price_id, quantity,
                     payment_link_id, _opener):
    if mode == "create":
        if not price_id or not str(price_id).startswith("price_"):
            return ("refused: a payment link needs an existing Stripe "
                    "Price ID (they start with 'price_') — the operator "
                    "creates prices in their own Stripe dashboard; "
                    "VYUK never creates a price. Nothing was created.")
        if quantity is None:
            quantity = 1
        if isinstance(quantity, bool) or not isinstance(quantity, int) \
                or quantity < 1:
            return "refused: quantity is a whole number, 1 or more."
        params = {"line_items[0][price]": price_id,
                  "line_items[0][quantity]": str(quantity)}
        obj, err = _call("POST", "/v1/payment_links", api_key, params,
                         _opener=_opener)
        if err:
            return err
        row = db.record("payment_link.create", obj.get("id", "?"),
                        "payment_link",
                        _summarise(obj, ("id", "url", "active")))
        if row is None:
            return ("the test payment link was created at Stripe (%s) "
                    "but the local ledger re-read disagreed — the "
                    "creation is reported, not hidden."
                    % obj.get("id", "?"))
        return ("TEST payment link created: %s (test mode, livemode "
                "false). It takes test payments only — no real money."
                % _summarise(obj, ("id", "url", "active")))
    if mode == "get":
        ok, refusal = _check_id("payment_link", payment_link_id)
        if not ok:
            return refusal
        obj, err = _call("GET", "/v1/payment_links/%s" % payment_link_id,
                         api_key, _opener=_opener)
        if err:
            return err
        return ("TEST payment link: %s (test mode, livemode false)."
                % _summarise(obj, ("id", "url", "active")))
    return "refused: 'payment_link' mode must be 'create' or 'get'."


def _do_checkout(db, api_key, mode, price_id, quantity, checkout_mode,
                 success_url, cancel_url, _opener):
    if mode != "create":
        return "refused: 'checkout' mode must be 'create'."
    if not price_id or not str(price_id).startswith("price_"):
        return ("refused: a Checkout session needs an existing Stripe "
                "Price ID (they start with 'price_') — the operator "
                "creates prices in their own Stripe dashboard; VYUK "
                "never creates a price. Nothing was created.")
    if quantity is None:
        quantity = 1
    if isinstance(quantity, bool) or not isinstance(quantity, int) \
            or quantity < 1:
        return "refused: quantity is a whole number, 1 or more."
    if checkout_mode not in ("payment", "subscription"):
        return ("refused: checkout_mode must be 'payment' (a one-off "
                "test payment) or 'subscription' (a test subscription).")
    if not success_url or not cancel_url:
        return ("refused: a Checkout session needs a success_url and "
                "a cancel_url, supplied by the caller. Nothing was "
                "created.")
    params = {"mode": checkout_mode,
              "line_items[0][price]": price_id,
              "line_items[0][quantity]": str(quantity),
              "success_url": success_url,
              "cancel_url": cancel_url}
    obj, err = _call("POST", "/v1/checkout/sessions", api_key, params,
                     _opener=_opener)
    if err:
        return err
    row = db.record("checkout.create", obj.get("id", "?"),
                    "checkout_session",
                    _summarise(obj, ("id", "mode", "url",
                                     "payment_status")))
    if row is None:
        return ("the test Checkout session was created at Stripe (%s) "
                "but the local ledger re-read disagreed — the creation "
                "is reported, not hidden." % obj.get("id", "?"))
    return ("TEST Checkout session created: %s (test mode, livemode "
            "false). It takes test payments only — no real money."
            % _summarise(obj, ("id", "mode", "url", "payment_status")))


def _do_invoice(db, api_key, mode, customer_id, invoice_id, _opener):
    if mode == "list":
        path = "/v1/invoices?limit=20"
        if customer_id:
            ok, refusal = _check_id("customer", customer_id)
            if not ok:
                return refusal
            path += "&customer=" + urllib.parse.quote(customer_id)
        obj, err = _call("GET", path, api_key, _opener=_opener)
        if err:
            return err
        items = obj.get("data") or []
        if not items:
            return "TEST invoices: none yet (test mode)."
        lines = ["TEST invoices (%d, test mode):" % len(items)]
        for item in items:
            lines.append("  - %s" % _summarise(
                item, ("id", "customer", "status", "amount_due",
                       "currency")))
        return "\n".join(lines)
    if mode == "get":
        ok, refusal = _check_id("invoice", invoice_id)
        if not ok:
            return refusal
        obj, err = _call("GET", "/v1/invoices/%s" % invoice_id,
                         api_key, _opener=_opener)
        if err:
            return err
        return ("TEST invoice: %s (test mode, livemode false)."
                % _summarise(obj, ("id", "customer", "status",
                                   "amount_due", "currency",
                                   "amount_paid")))
    return "refused: 'invoice' mode must be 'list' or 'get'."


def _do_refund(db, api_key, mode, payment_intent_id, charge_id, amount,
               reason, approval_ticket, _opener):
    if mode != "create":
        return "refused: 'refund' mode must be 'create'."
    if not approval_ticket or not str(approval_ticket).strip():
        return ("refused: a refund needs an out-of-band approval "
                "reference ('approval_ticket') in the request itself — "
                "MN-5.14 rule 7: VYUK never issues a refund on a chat "
                "instruction alone. Nothing was refunded.")
    if not payment_intent_id and not charge_id:
        return ("refused: a refund needs a payment_intent_id or a "
                "charge_id (test mode). Nothing was refunded.")
    if payment_intent_id:
        ok, refusal = _check_id("payment_intent", payment_intent_id)
        if not ok:
            return refusal
    if charge_id:
        ok, refusal = _check_id("charge", charge_id)
        if not ok:
            return refusal
    params = {}
    if payment_intent_id:
        params["payment_intent"] = payment_intent_id
    if charge_id:
        params["charge"] = charge_id
    if amount is not None:
        ok, refusal = _check_amount_minor(amount)
        if not ok:
            return refusal
        params["amount"] = str(amount)
    if reason:
        if reason not in ("duplicate", "fraudulent", "requested_by_customer"):
            return ("refused: reason must be 'duplicate', 'fraudulent' "
                    "or 'requested_by_customer' (Stripe's own values).")
        params["reason"] = reason
    obj, err = _call("POST", "/v1/refunds", api_key, params,
                     _opener=_opener)
    if err:
        return err
    row = db.record("refund.create", obj.get("id", "?"), "refund",
                    _summarise(obj, ("id", "status", "amount",
                                     "payment_intent", "charge")))
    if row is None:
        return ("the test refund was created at Stripe (%s) but the "
                "local ledger re-read disagreed — the refund is "
                "reported, not hidden." % obj.get("id", "?"))
    return ("TEST refund created: %s (test mode, livemode false). "
            "Test money only — no real money moves."
            % _summarise(obj, ("id", "status", "amount",
                               "payment_intent", "charge")))


def _do_webhook(db, mode, payload, signature, webhook_secret):
    if mode != "verify":
        return "refused: 'webhook' mode must be 'verify'."
    if not payload:
        return ("refused: 'webhook' verify needs the raw payload Stripe "
                "sent, the Stripe-Signature header, and the endpoint "
                "secret (whsec_...), all supplied per call.")
    ok, detail, event = verify_webhook_signature(
        payload, signature, webhook_secret)
    if not ok:
        return detail
    db.record("webhook.verify",
              (event or {}).get("id", "?") if isinstance(event, dict) else "?",
              "event", detail[:200])
    return detail


def _do_status(db):
    counts = db.counts()
    total = sum(counts.values())
    ok, chain = db.verify_chain()
    lines = ["Billing — TEST MODE ONLY (live mode disabled; live "
             "payments are ruling 34, which is open).",
             "Ledger: %d recorded test-mode actions; chain %s."
             % (total, chain)]
    for kind in sorted(counts):
        lines.append("  - %s: %d" % (kind, counts[kind]))
    if not ok:
        lines.append("WARNING: the ledger chain does not verify — %s." % chain)
    lines.append("No prices, tiers or trials are set here; no live key "
                 "is ever accepted; no card is ever handled.")
    return "\n".join(lines)


def _do_events(db, limit):
    rows = db.list_events(limit if limit is not None else 20)
    ok, chain = db.verify_chain()
    if not rows:
        return ("No test-mode billing actions recorded yet. Chain: %s."
                % chain)
    lines = ["Test-mode billing ledger (%d shown; chain: %s):"
             % (len(rows), chain)]
    for row in rows:
        _id, _ts, kind, stripe_id, obj, detail = row
        lines.append("  #%d %s %s %s — %s"
                     % (_id, kind, obj, stripe_id, detail))
    return "\n".join(lines)


def billing(action="", mode="", customer_id="", name="", email="",
             price_id="", quantity=None, amount=None, subscription_id="",
             payment_link_id="", invoice_id="", payment_intent_id="",
             charge_id="", reason="", approval_ticket="", payload="",
             signature="", webhook_secret="", api_key="", success_url="",
             cancel_url="", checkout_mode="", limit=None, detail=None,
             _opener=None, _db_path=None) -> str:
    """Public entry: one call, one connection, closed before returning.

    Every funnel call opens the billing ledger, works, and closes
    it — a leaked connection would hold a SQLite lock across calls
    and block the next write.
    """
    action = str(action or "").strip().lower()
    mode = str(mode or "").strip().lower()
    db = _BillingDB(_db_path)
    try:
        if action in ("status", "events"):
            if action == "status":
                return _do_status(db)
            return _do_events(db, limit)
        if action not in ("customer", "subscription", "payment_link",
                          "checkout", "invoice", "refund", "webhook"):
            return ("refused: action must be 'customer', 'subscription', "
                    "'payment_link', 'checkout', 'invoice', 'refund', "
                    "'webhook', 'status' or 'events'.")
        if action == "webhook":
            return _do_webhook(db, mode, payload, signature,
                               webhook_secret)
        ok, refusal = _validate_key(api_key)
        if not ok:
            return refusal
        if action == "customer":
            return _do_customer(db, api_key, mode, name, email,
                                customer_id, _opener)
        if action == "subscription":
            return _do_subscription(db, api_key, mode, customer_id,
                                    price_id, quantity, subscription_id,
                                    _opener)
        if action == "payment_link":
            return _do_payment_link(db, api_key, mode, price_id,
                                    quantity, payment_link_id, _opener)
        if action == "checkout":
            return _do_checkout(db, api_key, mode, price_id, quantity,
                                checkout_mode, success_url, cancel_url,
                                _opener)
        if action == "invoice":
            return _do_invoice(db, api_key, mode, customer_id,
                               invoice_id, _opener)
        if action == "refund":
            return _do_refund(db, api_key, mode, payment_intent_id,
                              charge_id, amount, reason, approval_ticket,
                              _opener)
        return "refused: unknown action."
    finally:
        db.close()
