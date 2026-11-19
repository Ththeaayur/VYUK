"""R18-1 stripe/stripe-node rebuild — test-mode-only billing plumbing.

Fake Stripe transport only: every network call goes through an
injectable fake opener returning recorded test-mode JSON
(livemode false). No real Stripe account, no real key, no network.
The test key below is an obviously fake string, never a real key.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".pkgroot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import billing as bl
from core.catalogue_snapshot import GROUP_MEMBERS
from core.funnel import money_guard as mg
from core.funnel.tools_gate import _needs_confirmation
from core.funnel.tools_gate_confirm import _CONFIRM_GATE_TOOLS
from core.funnel.tools_gate_readclass import _is_read_action
from core.funnel.tools_registry import tools

TREE = Path(__file__).resolve().parent.parent
FAKE_KEY = "sk_test_fakekey00000000000000"
FAKE_SECRET = "whsec_fakesecret00000000000000"

_CUSTOMER = {"id": "cus_fake001", "object": "customer", "livemode": False,
             "name": "Test Buyer", "email": "buyer@example.test",
             "balance": 0, "delinquent": False}
_CUSTOMER_LIST = {"object": "list", "livemode": False, "data": [_CUSTOMER]}
_SUBSCRIPTION = {"id": "sub_fake001", "object": "subscription",
                 "livemode": False, "status": "active",
                 "customer": "cus_fake001", "current_period_end": 1799999999}
_SUB_CANCELLED = dict(_SUBSCRIPTION, status="canceled")
_LINK = {"id": "plink_fake001", "object": "payment_link",
         "livemode": False, "active": True,
         "url": "https://buy.stripe.com/test_fake001"}
_SESSION = {"id": "cs_test_fake001", "object": "checkout.session",
            "livemode": False, "mode": "payment",
            "url": "https://checkout.stripe.com/test_fake001",
            "payment_status": "unpaid"}
_INVOICE = {"id": "in_fake001", "object": "invoice", "livemode": False,
            "customer": "cus_fake001", "status": "open",
            "amount_due": 1000, "currency": "usd", "amount_paid": 0}
_INVOICE_LIST = {"object": "list", "livemode": False, "data": [_INVOICE]}
_REFUND = {"id": "re_fake001", "object": "refund", "livemode": False,
           "status": "succeeded", "amount": 500,
           "payment_intent": "pi_fake001", "charge": None}
_LIVE_CUSTOMER = dict(_CUSTOMER, livemode=True)
_ERROR = {"error": {"message": "No such customer: 'cus_nope'",
                    "type": "invalid_request_error",
                    "code": "resource_missing"}}


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeStripe:
    """Maps (method, path) to a canned JSON body; records requests."""

    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def __call__(self, req, timeout=None):
        url = req.full_url
        path = urllib.parse.urlparse(url).path
        body = {}
        if req.data:
            body = {k: v[0] for k, v in
                    urllib.parse.parse_qs(req.data.decode("utf-8")).items()}
        self.calls.append({"url": url, "method": req.get_method(),
                           "path": path, "body": body,
                           "auth": req.get_header("Authorization"),
                           "version": req.get_header("Stripe-version")})
        key = (req.get_method(), path)
        if key not in self.mapping:
            raise AssertionError("unexpected Stripe call: %s %s" % key)
        payload = json.dumps(self.mapping[key]).encode("utf-8")
        return _FakeResponse(payload)


class _FailingOpener:
    def __init__(self, exc):
        self.exc = exc

    def __call__(self, req, timeout=None):
        raise self.exc


def _stripe_mapping():
    return {
        ("POST", "/v1/customers"): _CUSTOMER,
        ("GET", "/v1/customers"): _CUSTOMER_LIST,
        ("GET", "/v1/customers/cus_fake001"): _CUSTOMER,
        ("POST", "/v1/subscriptions"): _SUBSCRIPTION,
        ("GET", "/v1/subscriptions/sub_fake001"): _SUBSCRIPTION,
        ("DELETE", "/v1/subscriptions/sub_fake001"): _SUB_CANCELLED,
        ("POST", "/v1/payment_links"): _LINK,
        ("GET", "/v1/payment_links/plink_fake001"): _LINK,
        ("POST", "/v1/checkout/sessions"): _SESSION,
        ("GET", "/v1/invoices"): _INVOICE_LIST,
        ("GET", "/v1/invoices/in_fake001"): _INVOICE,
        ("POST", "/v1/refunds"): _REFUND,
    }


def _sig(payload, secret=FAKE_SECRET, ts=None):
    ts = ts if ts is not None else int(time.time())
    digest = hmac.new(secret.encode(), ("%d." % ts + payload).encode(),
                      hashlib.sha256).hexdigest()
    return "t=%d,v1=%s" % (ts, digest)


class BillingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "billing.sqlite3")
        self.fake = _FakeStripe(_stripe_mapping())

    def tearDown(self):
        self.tmp.cleanup()

    # -- key gates ----------------------------------------------------
    def test_live_key_refused_and_not_echoed(self):
        out = bl.billing("customer", mode="list", api_key="sk_live_abc",
                         _db_path=self.db)
        self.assertIn("LIVE", out)
        self.assertNotIn("sk_live_abc", out)

    def test_non_test_key_refused(self):
        out = bl.billing("customer", mode="list", api_key="pk_test_abc",
                         _db_path=self.db)
        self.assertIn("sk_test_", out)
        self.assertNotIn("pk_test_abc", out)

    def test_missing_key_refused(self):
        out = bl.billing("customer", mode="list", _db_path=self.db)
        self.assertIn("no Stripe test key", out)

    def test_key_never_echoed_on_success(self):
        out = bl.billing("customer", mode="list", api_key=FAKE_KEY,
                         _opener=self.fake, _db_path=self.db)
        self.assertIn("TEST customers", out)
        self.assertNotIn(FAKE_KEY, out)
        self.assertNotIn("fakekey", out)

    # -- customers ----------------------------------------------------
    def test_customer_create(self):
        out = bl.billing("customer", mode="create", name="Test Buyer",
                         email="buyer@example.test", api_key=FAKE_KEY,
                         _opener=self.fake, _db_path=self.db)
        self.assertIn("TEST customer created", out)
        self.assertIn("cus_fake001", out)
        self.assertIn("test mode", out)
        call = self.fake.calls[-1]
        self.assertEqual(call["method"], "POST")
        self.assertTrue(call["path"].endswith("/v1/customers"))
        self.assertEqual(call["body"]["name"], "Test Buyer")
        self.assertTrue(call["auth"].startswith("Basic "))
        self.assertEqual(call["auth"],
                         "Basic " + base64.b64encode(
                             (FAKE_KEY + ":").encode()).decode())
        self.assertEqual(call["version"], bl.STRIPE_VERSION)

    def test_customer_create_needs_name_or_email(self):
        out = bl.billing("customer", mode="create", api_key=FAKE_KEY,
                         _opener=self.fake, _db_path=self.db)
        self.assertIn("needs a name or an email", out)
        self.assertEqual(self.fake.calls, [])

    def test_customer_list_and_get(self):
        out = bl.billing("customer", mode="list", api_key=FAKE_KEY,
                         _opener=self.fake, _db_path=self.db)
        self.assertIn("cus_fake001", out)
        out = bl.billing("customer", mode="get", customer_id="cus_fake001",
                         api_key=FAKE_KEY, _opener=self.fake,
                         _db_path=self.db)
        self.assertIn("TEST customer", out)
        self.assertIn("Test Buyer", out)

    def test_customer_get_refuses_bad_id(self):
        out = bl.billing("customer", mode="get", customer_id="not-an-id",
                         api_key=FAKE_KEY, _opener=self.fake,
                         _db_path=self.db)
        self.assertIn("does not look like", out)
        self.assertEqual(self.fake.calls, [])

    # -- subscriptions ------------------------------------------------
    def test_subscription_create_cancel_status(self):
        out = bl.billing("subscription", mode="create",
                         customer_id="cus_fake001", price_id="price_fake001",
                         quantity=1, api_key=FAKE_KEY,
                         _opener=self.fake, _db_path=self.db)
        self.assertIn("TEST subscription created", out)
        self.assertIn("sub_fake001", out)
        call = self.fake.calls[-1]
        self.assertEqual(call["body"]["customer"], "cus_fake001")
        self.assertEqual(call["body"]["items[0][price]"], "price_fake001")
        out = bl.billing("subscription", mode="status",
                         subscription_id="sub_fake001", api_key=FAKE_KEY,
                         _opener=self.fake, _db_path=self.db)
        self.assertIn("active", out)
        out = bl.billing("subscription", mode="cancel",
                         subscription_id="sub_fake001", api_key=FAKE_KEY,
                         _opener=self.fake, _db_path=self.db)
        self.assertIn("TEST subscription cancelled", out)
        self.assertIn("canceled", out)

    def test_subscription_create_refuses_no_price(self):
        out = bl.billing("subscription", mode="create",
                         customer_id="cus_fake001", api_key=FAKE_KEY,
                         _opener=self.fake, _db_path=self.db)
        self.assertIn("Price ID", out)
        self.assertEqual(self.fake.calls, [])

    def test_subscription_create_refuses_bad_quantity(self):
        out = bl.billing("subscription", mode="create",
                         customer_id="cus_fake001", price_id="price_fake001",
                         quantity=0, api_key=FAKE_KEY,
                         _opener=self.fake, _db_path=self.db)
        self.assertIn("whole number", out)

    # -- payment links + checkout -------------------------------------
    def test_payment_link_create_and_get(self):
        out = bl.billing("payment_link", mode="create",
                         price_id="price_fake001", quantity=2,
                         api_key=FAKE_KEY, _opener=self.fake,
                         _db_path=self.db)
        self.assertIn("TEST payment link created", out)
        self.assertIn("plink_fake001", out)
        self.assertIn("buy.stripe.com/test_", out)
        out = bl.billing("payment_link", mode="get",
                         payment_link_id="plink_fake001", api_key=FAKE_KEY,
                         _opener=self.fake, _db_path=self.db)
        self.assertIn("TEST payment link", out)

    def test_checkout_create(self):
        out = bl.billing("checkout", mode="create",
                         price_id="price_fake001", quantity=1,
                         checkout_mode="payment",
                         success_url="https://example.test/ok",
                         cancel_url="https://example.test/no",
                         api_key=FAKE_KEY, _opener=self.fake,
                         _db_path=self.db)
        self.assertIn("TEST Checkout session created", out)
        self.assertIn("cs_test_fake001", out)
        call = self.fake.calls[-1]
        self.assertEqual(call["body"]["mode"], "payment")
        self.assertEqual(call["body"]["success_url"],
                         "https://example.test/ok")

    def test_checkout_refuses_missing_urls_and_bad_mode(self):
        out = bl.billing("checkout", mode="create",
                         price_id="price_fake001",
                         checkout_mode="payment", api_key=FAKE_KEY,
                         _opener=self.fake, _db_path=self.db)
        self.assertIn("success_url", out)
        out = bl.billing("checkout", mode="create",
                         price_id="price_fake001",
                         checkout_mode="forever",
                         success_url="https://example.test/ok",
                         cancel_url="https://example.test/no",
                         api_key=FAKE_KEY, _opener=self.fake,
                         _db_path=self.db)
        self.assertIn("checkout_mode", out)

    # -- invoices -----------------------------------------------------
    def test_invoice_list_and_get(self):
        out = bl.billing("invoice", mode="list", api_key=FAKE_KEY,
                         _opener=self.fake, _db_path=self.db)
        self.assertIn("in_fake001", out)
        out = bl.billing("invoice", mode="get", invoice_id="in_fake001",
                         api_key=FAKE_KEY, _opener=self.fake,
                         _db_path=self.db)
        self.assertIn("TEST invoice", out)
        self.assertIn("amount_due=1000", out)

    # -- refunds (MN-5.14 rule 7) --------------------------------------
    def test_refund_needs_approval_ticket(self):
        out = bl.billing("refund", mode="create",
                         payment_intent_id="pi_fake001", amount=500,
                         api_key=FAKE_KEY, _opener=self.fake,
                         _db_path=self.db)
        self.assertIn("approval_ticket", out)
        self.assertIn("rule 7", out)
        self.assertEqual(self.fake.calls, [])

    def test_refund_with_ticket(self):
        out = bl.billing("refund", mode="create",
                         payment_intent_id="pi_fake001", amount=500,
                         reason="requested_by_customer",
                         approval_ticket="TKT-TEST-1", api_key=FAKE_KEY,
                         _opener=self.fake, _db_path=self.db)
        self.assertIn("TEST refund created", out)
        self.assertIn("re_fake001", out)
        call = self.fake.calls[-1]
        self.assertEqual(call["body"]["payment_intent"], "pi_fake001")
        self.assertEqual(call["body"]["amount"], "500")

    def test_refund_refuses_float_amount(self):
        out = bl.billing("refund", mode="create",
                         payment_intent_id="pi_fake001", amount=5.5,
                         approval_ticket="TKT-TEST-1", api_key=FAKE_KEY,
                         _opener=self.fake, _db_path=self.db)
        self.assertIn("whole minor units", out)

    def test_refund_refuses_bad_reason(self):
        out = bl.billing("refund", mode="create",
                         payment_intent_id="pi_fake001",
                         reason="because-i-felt-like-it",
                         approval_ticket="TKT-TEST-1", api_key=FAKE_KEY,
                         _opener=self.fake, _db_path=self.db)
        self.assertIn("reason must be", out)

    # -- webhook verification ------------------------------------------
    def test_webhook_verify_good(self):
        payload = json.dumps({"id": "evt_fake001",
                              "type": "payment_intent.succeeded"})
        out = bl.billing("webhook", mode="verify", payload=payload,
                         signature=_sig(payload),
                         webhook_secret=FAKE_SECRET, _db_path=self.db)
        self.assertIn("VERIFIED", out)
        self.assertIn("evt_fake001", out)
        self.assertNotIn(FAKE_SECRET, out)

    def test_webhook_verify_tampered(self):
        payload = json.dumps({"id": "evt_fake001",
                              "type": "payment_intent.succeeded"})
        out = bl.billing("webhook", mode="verify", payload=payload + " ",
                         signature=_sig(payload),
                         webhook_secret=FAKE_SECRET, _db_path=self.db)
        self.assertIn("REJECTED", out)

    def test_webhook_verify_stale_timestamp(self):
        payload = json.dumps({"id": "evt_fake001", "type": "charge.succeeded"})
        old = int(time.time()) - 3600
        out = bl.billing("webhook", mode="verify", payload=payload,
                         signature=_sig(payload, ts=old),
                         webhook_secret=FAKE_SECRET, _db_path=self.db)
        self.assertIn("REJECTED", out)
        self.assertIn("tolerance", out)

    def test_webhook_verify_bad_secret_shape(self):
        payload = "{}"
        out = bl.billing("webhook", mode="verify", payload=payload,
                         signature=_sig(payload),
                         webhook_secret="not-a-secret", _db_path=self.db)
        self.assertIn("whsec_", out)
        self.assertNotIn("not-a-secret", out)

    # -- honest failure paths ------------------------------------------
    def test_live_mode_answer_refused(self):
        fake = _FakeStripe({("GET", "/v1/customers/cus_fake001"):
                            _LIVE_CUSTOMER})
        out = bl.billing("customer", mode="get",
                         customer_id="cus_fake001", api_key=FAKE_KEY,
                         _opener=fake, _db_path=self.db)
        self.assertIn("LIVE-mode", out)
        self.assertNotIn("Test Buyer", out)

    def test_stripe_error_surfaced(self):
        fake = _FakeStripe({("GET", "/v1/customers/cus_nope"): _ERROR})
        out = bl.billing("customer", mode="get",
                         customer_id="cus_nope", api_key=FAKE_KEY,
                         _opener=fake, _db_path=self.db)
        self.assertIn("Stripe refused", out)
        self.assertIn("No such customer", out)

    def test_network_failure_honest(self):
        opener = _FailingOpener(urllib.error.URLError("no route to host"))
        out = bl.billing("customer", mode="list", api_key=FAKE_KEY,
                         _opener=opener, _db_path=self.db)
        self.assertIn("could not reach api.stripe.com", out)
        self.assertIn("not a success", out)

    def test_unknown_action(self):
        out = bl.billing("explode", api_key=FAKE_KEY,
                         _opener=self.fake, _db_path=self.db)
        self.assertIn("must be 'customer'", out)

    # -- ledger ---------------------------------------------------------
    def test_ledger_status_events_and_chain(self):
        bl.billing("customer", mode="create", name="Test Buyer",
                   api_key=FAKE_KEY, _opener=self.fake, _db_path=self.db)
        bl.billing("subscription", mode="create",
                   customer_id="cus_fake001", price_id="price_fake001",
                   api_key=FAKE_KEY, _opener=self.fake, _db_path=self.db)
        out = bl.billing("status", _db_path=self.db)
        self.assertIn("TEST MODE ONLY", out)
        self.assertIn("2 recorded", out)
        self.assertIn("chain intact", out)
        out = bl.billing("events", _db_path=self.db)
        self.assertIn("customer.create", out)
        self.assertIn("subscription.create", out)
        self.assertIn("cus_fake001", out)
        self.assertNotIn(FAKE_KEY, out)
        db = bl._BillingDB(self.db)
        try:
            ok, detail = db.verify_chain()
            self.assertTrue(ok, detail)
            conn = sqlite3.connect(self.db)
            conn.execute("UPDATE billing_events SET detail='forged' "
                         "WHERE id=1")
            conn.commit()
            conn.close()
            ok, detail = db.verify_chain()
            self.assertFalse(ok)
        finally:
            db.close()

    # -- registry / classification / gates ------------------------------
    def test_registry_and_classification(self):
        decls = tools.get_declarations()
        self.assertEqual(len(decls), 758)  # INTENTIONAL — R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing
        mine = [d for d in decls if d["name"] == "billing"]
        self.assertEqual(len(mine), 1)
        self.assertEqual(len(mine[0]["parameters"]["properties"]), 24)  # INTENTIONAL — R18-1 stripe pack (2026-11-12): 1788 -> 1812 — billing adds 24 properties
        self.assertEqual(sum(len(d["parameters"]["properties"])
                           for d in decls), 2079)  # INTENTIONAL — R18-1 stripe pack (2026-11-12): 1788 -> 1812; R18-3 mcp-context-forge pack (2026-11-12): 1812 -> 1828 — mcp_registry adds 16 properties; R18-4 a2a pack (2026-11-12): 1828 -> 1841 — agent_call adds 13 properties; R18-5 dspy pack (2026-11-12): 1841 -> 1855 — prompt_tune adds 14 properties; R18-6 fonoster pack (2026-11-12): 1855 -> 1869 — voice_agent adds 14 properties; R18-7 attendize pack (2026-11-12): 1869 -> 1884 — event_tickets adds 15 properties; R18-8 ta pack (2026-11-12): 1884 -> 1893 — technical_analysis adds 9 properties; R18-10 timetrace pack (2026-11-12): 1893 -> 1906 — time_tracker adds 13 properties; R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan
        snap = json.loads((TREE / "tests" / "_classification_snapshot.json")
                          .read_text())
        self.assertEqual(snap["billing"],
                         {"confirm": True, "free": None, "read": False})
        self.assertIn("billing", _CONFIRM_GATE_TOOLS)
        self.assertIn("billing", GROUP_MEMBERS["Your world"])
        self.assertIn('"billing",',
                      (TREE / "core" / "catalogue_groups_data.py").read_text())
        text = (TREE / "TOOLS_LOOPHOLES_AUDIT.md").read_text()
        totals = json.loads(re.search(
            r"<!-- AUDIT-TOTALS: (\{.*?\}) -->", text).group(1))
        self.assertEqual(totals["total"], 758)  # INTENTIONAL — R18-1 stripe pack (2026-11-12): 734 -> 735; R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing
        self.assertEqual(totals["read"], 383)  # INTENTIONAL — round-3 fix Q6 (2026-11-19): 382 -> 383 — one new read tool get_morning_briefing
        self.assertEqual(totals["effectful"], 375)  # INTENTIONAL — R18-1 stripe pack (2026-11-12): 352 -> 353; R18-3 mcp-context-forge pack (2026-11-12): 353 -> 354; R18-4 a2a pack (2026-11-12): 354 -> 355; R18-5 dspy pack (2026-11-12): 355 -> 356; R18-6 fonoster pack (2026-11-12): 356 -> 357; R18-7 attendize pack (2026-11-12): 357 -> 358; R18-8 ta pack (2026-11-12): 358 -> 359; R18-10 timetrace pack (2026-11-12): 359 -> 360; R19-1 videolingo pack (2026-11-12): 360 -> 361; R19-2 ortools pack (2026-11-12): 361 -> 362; R19-3 aureuserp pack (2026-11-12): 362 -> 363; R19-4 vidbee pack (2026-11-12): 363 -> 364; R19-5 invoify pack (2026-11-12): 364 -> 365; R19-6 tolgee pack (2026-11-12): 365 -> 366; R19-7 spoolman pack (2026-11-12): 366 -> 367; R19-8 djangocrm pack (2026-11-12): 367 -> 368; R19-10 vroom pack (2026-11-12): 368 -> 369; GAPS-7 connectors pack (2026-11-13): 369 -> 370; SOCIAL Phase 1 (2026-11-13): 370 -> 371; GAPS-8 scheduled-agents pack (2026-11-13): 371 -> 372; Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 374 -> 375 — one new tool run_plan
        self.assertEqual(totals["confirm_gated"], 166)  # INTENTIONAL — R18-1 stripe pack (2026-11-12): 153 -> 154; R18-3 mcp-context-forge pack (2026-11-12): 154 -> 155; R18-4 a2a pack (2026-11-12): 155 -> 156; R18-5 dspy pack (2026-11-12): 156 -> 157; R18-6 fonoster pack (2026-11-12): 157 -> 158; R19-1 videolingo pack (2026-11-12): 158 -> 159; R19-4 vidbee pack (2026-11-12): 159 -> 160 — media_chat is confirm-gated for ask/summarize; R19-5 invoify pack (2026-11-12): 160 -> 161 — invoice is confirm-gated for send; R19-6 tolgee pack (2026-11-12): 161 -> 162 — translations is confirm-gated for translate; SOCIAL Phase 1 (2026-11-13): 162 -> 163 — social_send is confirm-gated; GAPS-8 scheduled-agents pack (2026-11-13): 163 -> 164 — scheduled_agents is confirm-gated; round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling
        self.assertEqual(totals["already_verified_in_handler"], 309)  # INTENTIONAL — R18-1 stripe pack (2026-11-12): 286 -> 287; R18-3 mcp-context-forge pack (2026-11-12): 287 -> 288; R18-4 a2a pack (2026-11-12): 288 -> 289; R18-5 dspy pack (2026-11-12): 289 -> 290; R18-6 fonoster pack (2026-11-12): 290 -> 291; R18-7 attendize pack (2026-11-12): 291 -> 292; R18-8 ta pack (2026-11-12): 292 -> 293; R18-10 timetrace pack (2026-11-12): 293 -> 294; R19-1 videolingo pack (2026-11-12): 294 -> 295; R19-2 ortools pack (2026-11-12): 295 -> 296; R19-3 aureuserp pack (2026-11-12): 296 -> 297; R19-4 vidbee pack (2026-11-12): 297 -> 298; R19-5 invoify pack (2026-11-12): 298 -> 299; R19-6 tolgee pack (2026-11-12): 299 -> 300; R19-7 spoolman pack (2026-11-12): 300 -> 301; R19-8 djangocrm pack (2026-11-12): 301 -> 302; R19-10 vroom pack (2026-11-12): 302 -> 303; GAPS-7 connectors pack (2026-11-13): 303 -> 304; SOCIAL Phase 1 (2026-11-13): 304 -> 305; GAPS-8 scheduled-agents pack (2026-11-13): 305 -> 306; Click-anywhere (2026-11-14): 306 -> 307 — click_anywhere verified in handler; field-check fix #32 (2026-11-18): 307 -> 308 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 308 -> 309 — one new tool run_plan
        counts = json.loads(re.search(
            r"<!-- AUDIT-COUNTS: (\{.*?\}) -->", text).group(1))
        self.assertEqual(counts["productivity"], 146)  # INTENTIONAL — R18-1 stripe pack (2026-11-12): 132 -> 133; R18-7 attendize pack (2026-11-12): productivity 133 -> 134 (effectful 75 -> 76); R18-8 ta pack (2026-11-12): productivity 134 -> 135 (effectful 76 -> 77); R18-10 timetrace pack (2026-11-12): productivity 135 -> 136 (effectful 77 -> 78); R19-2 ortools pack (2026-11-12): productivity 136 -> 137 (effectful 78 -> 79); R19-3 aureuserp pack (2026-11-12): productivity 137 -> 138 (effectful 79 -> 80); R19-5 invoify pack (2026-11-12): productivity 138 -> 139 (effectful 80 -> 81); R19-6 tolgee pack (2026-11-12): productivity 139 -> 140 (effectful 81 -> 82); R19-7 spoolman pack (2026-11-12): productivity 140 -> 141 (effectful 82 -> 83); R19-8 djangocrm pack (2026-11-12): productivity 141 -> 142 (effectful 83 -> 84); R19-10 vroom pack (2026-11-12): productivity 142 -> 143 (effectful 84 -> 85); GAPS-8 scheduled-agents pack (2026-11-13): productivity 143 -> 144 (effectful 85 -> 86); field-check fix #32 (2026-11-18): 144 -> 145 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 145 -> 146 — one new tool run_plan
        self.assertEqual(sum(counts.values()), 758)  # INTENTIONAL — R18-1 stripe pack (2026-11-12): 734 -> 735; R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing
        self.assertEqual(totals["read"] + totals["effectful"],
                         totals["total"])
        self.assertEqual(totals["seam_wired"]
                         + totals["already_verified_in_handler"]
                         + totals["honestly_unverifiable"],
                         totals["effectful"])

    def test_confirm_and_read_split(self):
        writes = [("customer", "create"), ("subscription", "create"),
                  ("subscription", "cancel"), ("payment_link", "create"),
                  ("checkout", "create"), ("refund", "create")]
        reads = [("customer", "list"), ("customer", "get"),
                 ("subscription", "status"), ("payment_link", "get"),
                 ("invoice", "list"), ("invoice", "get"),
                 ("webhook", "verify")]
        for action, mode in writes:
            self.assertTrue(_needs_confirmation(
                "billing", {"action": action, "mode": mode}),
                (action, mode))
            self.assertFalse(_is_read_action(
                "billing", {"action": action, "mode": mode}),
                (action, mode))
        for action, mode in reads:
            self.assertFalse(_needs_confirmation(
                "billing", {"action": action, "mode": mode}),
                (action, mode))
            self.assertTrue(_is_read_action(
                "billing", {"action": action, "mode": mode}),
                (action, mode))
        for action in ("status", "events"):
            self.assertFalse(_needs_confirmation("billing", {"action": action}))
            self.assertTrue(_is_read_action("billing", {"action": action}))
        self.assertTrue(_needs_confirmation("billing", {"action": "bogus"}))

    def test_money_guard_shape(self):
        # The billing name passes MN-5.14 on every action; the refund
        # passes only with its out-of-band approval reference.
        for args in ({"action": "customer", "mode": "create"},
                     {"action": "customer", "mode": "list"},
                     {"action": "subscription", "mode": "create"},
                     {"action": "payment_link", "mode": "create"},
                     {"action": "checkout", "mode": "create"},
                     {"action": "invoice", "mode": "list"},
                     {"action": "webhook", "mode": "verify"},
                     {"action": "status"}, {"action": "events"}):
            self.assertIsNone(mg.check("billing", args, tainted=False), args)
        self.assertIsNotNone(mg.check(
            "billing", {"action": "refund", "mode": "create", "amount": 500},
            tainted=False))
        self.assertEqual(mg.check(
            "billing", {"action": "refund", "mode": "create", "amount": 500},
            tainted=False).rule, "MN-5.14 rule 7")
        self.assertIsNone(mg.check(
            "billing", {"action": "refund", "mode": "create", "amount": 500,
                        "approval_ticket": "TKT-1"}, tainted=False))

    def test_licence_marker_proposed(self):
        text = (TREE / "core" / "billing.py").read_text()
        # Either marker state passes: PROPOSED until the parent rules
        # the licence at verification, Parent-ruled after. (The R17-9
        # wardrobe suite asserted PROPOSED only, and the parent's
        # ruling flip red-ded it in R18-1 neighbours — this test does
        # not repeat that trap.)
        self.assertTrue("PROPOSED — parent rules" in text
                        or "Parent-ruled (licence" in text)
        self.assertIn("stripe/stripe-node", text)
        self.assertIn("MIT", text)
        self.assertNotIn("import requests", text)
        self.assertNotIn("import stripe", text)


if __name__ == "__main__":
    unittest.main()
