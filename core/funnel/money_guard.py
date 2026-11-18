"""MN-5.14 — the never-automate list (Money edition track, v5.66).

Enforced in CODE, never prompt-only. This module is the single
source of truth for the eight never-automate money rules; the
execution funnel consults it in two places, and BOTH must agree
before anything money-shaped runs:

* core/funnel/tools_gate._needs_confirmation — a guarded call returns
  True, routing the call into the human-confirmation ask even
  when the tool itself is normally ungated;
* core/confirm_gate.request_confirmation — a guarded call is
  REFUSED there, before the allowlist shortcut, before any
  batched decision replay, and before the handler is ever
  invoked. A human yes therefore cannot approve a never-
  automate action, because the human is never asked.

That pair is the funnel-level enforcement: no individual tool
handler needs to know about the list, and no handler can bypass
it, because handlers only run after the funnel's gate. (The
ToolRegistry.execute funnel calls exactly those two functions,
in that order, for every tool call.)

The eight rules (every refusal message names its rule):

  MN-5.14 rule 1 — never enter, read, relay, store or submit an
    OTP, UPI PIN, card PIN/CVV, or 3-D Secure password.
  MN-5.14 rule 2 — never perform KYC or hold identity documents.
  MN-5.14 rule 3 — never move money on an ambiguous instruction
    (unresolved payee, approximate amount, unstated currency) or
    on ANY instruction whose source is tool / web / document
    content. Injection-originated payments are REFUSED, not
    routed to confirmation.
  MN-5.14 rule 4 — never execute personalised investment
    transactions.
  MN-5.14 rule 5 — never take or roll credit.
  MN-5.14 rule 6 — never create an e-mandate, save a card, or
    raise a cap without fresh amount-and-payee-specific
    confirmation (a confirmation whose payee AND amount match
    this request, supplied in the request itself — a generic
    human yes at the confirm gate is NOT that confirmation).
  MN-5.14 rule 7 — never issue refunds / payouts on a chat
    instruction alone (an out-of-band approval reference is
    required, supplied in the request itself).
  MN-5.14 rule 8 — never pay a first-seen payee above the
    default cap without out-of-band identifier verification
    (VPA / bank-name read-back, identifiers copied
    character-for-character).

This extends INV-3 / INV-20 to money. No new tool declarations
are added here, requirements.txt is untouched, and no refusal
message ever echoes a user value (no payee name, no amount
from the request, no secret) — only the rule and the constant
cap below.

Pre-pinned constants (label + assertion + comment together —
see ~/workspace/code-lab/test_v566_leaf2_moneyguard.py):
  FIRST_SEEN_CAP = 5000 — the rule-8 default cap, in the
    payment's own currency units. A first-seen payment AT the
    cap passes rule 8; ABOVE it, without verified identifiers,
    it refuses. Pinned 2026-10-25 (MN-5.14 leaf 2).

Scope notes, stated plainly so a reviewer does not over-read
the guard:
* The guard judges TOOL calls (tool name + structured args).
  Free-form typing tools (type_text, browser_type, paste_*)
  are judged ONLY for rule 1 secrets: typing a letter that
  mentions a refund is not issuing a refund, but typing an OTP
  into a page IS entering it, so secrets refuse everywhere.
* A session that core.injection reports as tainted counts as
  an injection source for money moves when no explicit
  instruction source is supplied (the taint screen is the
  funnel's own record that untrusted content tried to
  instruct VYUK). An explicitly supplied source wins over the
  taint flag only when it is "user" — and even then, a
  tainted money move is refused: a source label carried in
  the same tainted turn is not independently verified
  provenance. Money moves are fail-closed; a user whose turn
  was tainted retries in a clean turn.
* Shell commands (run_command) are already human-gated by the
  funnel and are outside this module's structured-args
  judgement; the guard does not parse shell strings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Pre-pinned 2026-10-25 (MN-5.14 leaf 2): the rule-8 default cap,
# in the payment's own currency units. At the cap passes;
# above it, for a first-seen payee without verified
# identifiers, refuses. Asserted in test_v566_leaf2_moneyguard.
FIRST_SEEN_CAP = 5000

# Rule labels, pre-pinned with the messages below (same strings
# appear in the suite assertions, so a label drift fails loudly).
RULE_1 = "MN-5.14 rule 1"
RULE_2 = "MN-5.14 rule 2"
RULE_3 = "MN-5.14 rule 3"
RULE_4 = "MN-5.14 rule 4"
RULE_5 = "MN-5.14 rule 5"
RULE_6 = "MN-5.14 rule 6"
RULE_7 = "MN-5.14 rule 7"
RULE_8 = "MN-5.14 rule 8"


@dataclass(frozen=True)
class MoneyRefusal:
    """One never-automate refusal: the rule label and the
    plain-words message. `message` always contains `rule`."""

    rule: str
    message: str


# ---------------------------------------------------------------------------
# Source taxonomy (rule 3)
# ---------------------------------------------------------------------------

# Instruction sources that are NOT the user speaking: content
# that arrived through a tool, the web, or a document. Any money
# move carrying one of these sources is refused outright.
_INJECTION_SOURCES = frozenset({
    "tool", "tool_output", "tool output", "tool result",
    "web", "web_content", "web content", "webpage", "page",
    "document", "document_content", "document content",
    "file", "file_content", "email", "message_content",
    "search_result", "rss", "untrusted", "injection",
})

_USER_SOURCES = frozenset({
    "user", "human", "owner", "voice", "chat", "direct",
})


def _norm_source(value) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _effective_source(instruction_source, tainted: bool,
                      taint_source) -> str | None:
    """The instruction's provenance for rule 3.

    An explicitly supplied source is authoritative, except
    that a tainted session overrides a claimed user source
    for money moves (see the module docstring: a source label
    in a tainted turn is not verified provenance). With no
    explicit source, a tainted session supplies its taint
    source (tool / web / document content, per core.injection).
    """
    supplied = _norm_source(instruction_source)
    if supplied:
        if tainted and supplied in _USER_SOURCES:
            return "tainted-session"
        return supplied
    if tainted:
        return _norm_source(taint_source) or "tainted-session"
    return None


def _is_injection_source(source: str | None) -> bool:
    if not source:
        return False
    if source == "tainted-session":
        return True
    compact = source.replace(" ", "_")
    return source in _INJECTION_SOURCES or compact in _INJECTION_SOURCES


# ---------------------------------------------------------------------------
# Argument helpers
# ---------------------------------------------------------------------------

def _norm_key(key) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key or "").lower())


def _args_by_norm(args: dict) -> dict[str, object]:
    return {_norm_key(k): v for k, v in (args or {}).items()}


def _first(args_norm: dict, *keys: str):
    for key in keys:
        if key in args_norm:
            return args_norm[key]
    return None


def _as_text(value) -> str:
    if value is None:
        return ""
    return str(value)


def _coerce_amount(value):
    """A request amount as a float, or None when it is missing
    or not an exact number ("about 500" is not a number)."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    text = re.sub(r"^[₹$€£]", "", text).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


_APPROX_WORDS = ("about", "around", "approx", "roughly", "or so",
                 "something like", "~")


def _amount_is_approximate(value) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return False
    text = str(value).lower()
    return any(w in text for w in _APPROX_WORDS) \
        or _coerce_amount(value) is None


_UNRESOLVED_PAYEES = frozenset({
    "", "unknown", "someone", "that person", "them", "anyone",
    "whoever", "unresolved", "tbd", "?", "that guy", "that lady",
})


# Tools whose free-form text is content, not execution: only
# rule-1 secrets are judged for these (see module docstring).
_TYPING_TOOLS = frozenset({
    "type_text", "browser_type", "paste_text", "paste_snippet",
    "type_clipboard", "uia_type_text",
})

# Explicit money-executing tool names (structured payment
# tools). The name test is equality for this set, plus the
# payment-name rule in _is_money_move — deliberately NOT a
# bare "pay" substring test: "loan_payment" is a pure
# calculator and must never trip this guard.
_MONEY_TOOL_NAMES = frozenset({
    "pay", "payment", "make_payment", "send_payment",
    "send_money", "upi_pay", "upi_payment", "execute_payment",
    "do_payment", "transfer", "transfer_money",
})

_MONEY_ACTIONS = frozenset({
    "pay", "make_payment", "send_payment", "send_money",
    "transfer", "transfer_money", "upi_pay", "pay_money",
    "make a payment",
})


def _tool_action_blob(tool_name: str, args: dict) -> str:
    action = _as_text(_first(_args_by_norm(args), "action",
                             "operation", "verb"))
    return f"{tool_name or ''} {action}".lower()


def _stripped_blob(tool_name: str, args: dict) -> str:
    """The tool+action blob with every separator removed, so
    "buy_stock", "buy stock" and "buyStock" all read the same
    ("buystock") for the concatenated-word checks below."""
    return re.sub(r"[^a-z0-9]", "", _tool_action_blob(tool_name, args))


def _is_money_move(tool_name: str, args: dict) -> bool:
    """True when this call MOVES money (structured payment),
    as opposed to calculating, reading, or typing about it."""
    tool = str(tool_name or "").lower().strip()
    if tool in _TYPING_TOOLS:
        return False
    args_norm = _args_by_norm(args)
    action = _as_text(args_norm.get("action",
                                    args_norm.get("operation", ""))).lower().strip()
    action_stripped = re.sub(r"[^a-z0-9]", "", action)
    if tool in _MONEY_TOOL_NAMES or action in _MONEY_ACTIONS \
            or action_stripped in ("pay", "makepayment", "sendpayment",
                                   "sendmoney", "transfer",
                                   "transfermoney", "upipay",
                                   "paymoney"):
        return True
    # A payment-named tool is a payment tool, whatever prefix
    # it carries ("probe_pay", "upi_payment") — EXCEPT the one
    # known calculator, loan_payment, which is pure maths and
    # must never trip this guard (pinned by the suite).
    if tool != "loan_payment" and (
            tool.endswith("_pay") or tool.startswith("pay_")
            or "payment" in tool):
        return True
    if "transfer" in tool or "payout" in tool:
        return True
    # A structured payment bundle — a payee plus an amount —
    # is a payment whatever tool name carries it.
    has_payee = any(k in args_norm for k in
                    ("payee", "recipient", "beneficiary", "to"))
    has_amount = any(k in args_norm for k in
                     ("amount", "paymentamount"))
    if has_payee and has_amount:
        return True
    return False


def _is_refund_or_payout(tool_name: str, args: dict) -> bool:
    args_norm = _args_by_norm(args)
    blob = _stripped_blob(tool_name, args)
    if "refund" in blob or "payout" in blob:
        return True
    return any(k in args_norm for k in ("refundamount", "payoutamount"))


def _is_mandate_or_card_save(tool_name: str, args: dict) -> bool:
    args_norm = _args_by_norm(args)
    blob = _stripped_blob(tool_name, args)
    if any(w in blob for w in ("mandate", "autopay", "autodebit",
                               "savecard", "storecard", "savedcard")):
        return True
    if any(w in blob for w in ("raisecap", "raiselimit",
                               "increaselimit", "increasecap")):
        return True
    return any(k in args_norm for k in
               ("emandate", "mandateid", "savedcard", "autopay"))


def _is_investment(tool_name: str, args: dict) -> bool:
    args_norm = _args_by_norm(args)
    blob = _stripped_blob(tool_name, args)
    if "invest" in blob:
        return True
    if "buy" in blob or "sell" in blob:
        asset_words = ("stock", "share", "mutualfund", "etf", "sip",
                       "security", "crypto", "bitcoin")
        if any(w in blob for w in asset_words):
            return True
        return any(k in args_norm for k in
                   ("symbol", "ticker", "asset", "security"))
    return False


def _is_credit(tool_name: str, args: dict) -> bool:
    blob = _stripped_blob(tool_name, args)
    if any(a in blob for a in ("takeloan", "applyforloan",
                               "rollcredit", "rollovercredit",
                               "borrowmoney", "takecredit",
                               "increasecreditlimit")):
        return True
    verbs = any(v in blob for v in ("take", "apply", "roll",
                                    "borrow", "increase"))
    nouns = ("loan" in blob or "credit" in blob)
    return verbs and nouns


def _is_kyc(tool_name: str, args: dict) -> bool:
    args_norm = _args_by_norm(args)
    blob = _stripped_blob(tool_name, args)
    if "kyc" in blob:
        return True
    if "identitydocument" in args_norm or "identitydocuments" in args_norm:
        return True
    return "identitydocument" in blob


# ---------------------------------------------------------------------------
# Rule 1 — secrets
# ---------------------------------------------------------------------------

_SECRET_KEYS = frozenset({
    "otp", "upipin", "cardpin", "cvv", "threedsecurepassword",
    "3dsecurepassword", "dspassword", "3dspassword",
    "onetimepassword", "one-timepassword",
})

_SECRET_PATTERNS = (
    re.compile(r"\botp\b[^0-9]{0,30}\d{4,8}\b", re.IGNORECASE),
    re.compile(r"\bupi\s*pin\b[^0-9]{0,20}\d{4,6}\b", re.IGNORECASE),
    re.compile(r"\bcard\s*pin\b[^0-9]{0,20}\d{4,6}\b", re.IGNORECASE),
    re.compile(r"\bcvv\b[^0-9]{0,20}\d{3,4}\b", re.IGNORECASE),
    re.compile(r"3[ -]?d\s*secure\b.{0,30}?password", re.IGNORECASE),
    re.compile(r"\b3ds\s*password\b", re.IGNORECASE),
)


def _secret_hit(tool_name: str, args: dict):
    """A rule-1 hit reason, or None. Never returns a secret value."""
    args_norm = _args_by_norm(args)
    blob = _stripped_blob(tool_name, args)
    for key, value in args_norm.items():
        if key in _SECRET_KEYS and _as_text(value).strip():
            return "a credential field is present"
        if key == "pin" and _as_text(value).strip():
            # A bare "pin" is only a money secret in a money /
            # card / bank context (a device PIN is not this rule).
            if any(w in blob for w in ("pay", "bank", "card",
                                       "upi", "atm")):
                return "a PIN field is present"
    if "otp" in blob or "cvv" in blob or "upipin" in blob \
            or "cardpin" in blob or "3dsecure" in blob \
            or "3ds" in blob:
        return "a credential action is requested"
    # Typing / sending tools: the text itself entering a
    # credential is entering it, whatever the arg key is called.
    tool = str(tool_name or "").lower().strip()
    if tool in _TYPING_TOOLS or tool in ("send_email", "send_message"):
        for value in (args or {}).values():
            if isinstance(value, str):
                if any(p.search(value) for p in _SECRET_PATTERNS):
                    return "credential text would be entered or sent"
    return None


# ---------------------------------------------------------------------------
# Rules 6 / 7 / 8 — conditional refusals (pass with proof)
# ---------------------------------------------------------------------------

def _fresh_confirmation_ok(args: dict, payee, amount) -> bool:
    """True only when the request itself carries a fresh
    confirmation whose payee AND amount match this request."""
    args_norm = _args_by_norm(args)
    conf = args_norm.get("freshamountpayeeconfirmation")
    if isinstance(conf, dict):
        conf_norm = {_norm_key(k): v for k, v in conf.items()}
        c_payee = _as_text(conf_norm.get("payee", "")).strip().lower()
        c_amount = _coerce_amount(conf_norm.get("amount"))
        if c_payee and c_amount is not None \
                and c_payee == _as_text(payee).strip().lower() \
                and abs(c_amount - float(amount)) < 0.01:
            return True
    if args_norm.get("freshconfirmation"):
        c_payee = _as_text(_first(
            args_norm, "confirmationpayee", "confirmedpayee",
            "")).strip().lower()
        c_amount = _coerce_amount(_first(
            args_norm, "confirmationamount", "confirmedamount"))
        if c_payee and c_amount is not None \
                and c_payee == _as_text(payee).strip().lower() \
                and abs(c_amount - float(amount)) < 0.01:
            return True
    return False


def _out_of_band_approval(args: dict) -> bool:
    args_norm = _args_by_norm(args)
    if args_norm.get("outofbandapproval"):
        return True
    for key in ("refundauthorisationid", "refundauthorizationid",
                "approvalticket", "approvedrefundid",
                "outofbandapprovalid"):
        if _as_text(args_norm.get(key, "")).strip():
            return True
    return False


def _identifier_verified(args: dict) -> bool:
    """Out-of-band identifier verification for rule 8: a VPA /
    bank-name read-back, identifiers copied character-for-
    character. Any one of these flags, supplied in the request,
    counts as that verification having happened."""
    args_norm = _args_by_norm(args)
    for key in ("identifierverified", "vpaverified",
                "banknamereadback", "outofbandverified",
                "identifierreadback", "payeeverified"):
        if args_norm.get(key):
            return True
    return False


def _first_seen_payee(args: dict) -> bool:
    args_norm = _args_by_norm(args)
    if args_norm.get("firstseenpayee") or args_norm.get("payeefirstseen"):
        return True
    if args_norm.get("isnewpayee"):
        return True
    if "knownpayee" in args_norm and not args_norm.get("knownpayee"):
        return True
    if "payeeseenbefore" in args_norm \
            and not args_norm.get("payeeseenbefore"):
        return True
    return False


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------

def check(tool_name: str = "", args: dict | None = None,
          instruction_source=None, *, tainted=None) -> MoneyRefusal | None:
    """The never-automate verdict for one funnel call.

    Returns None when the call is NOT refused by this list
    (it still faces the funnel's ordinary confirmation gate),
    else a MoneyRefusal whose message names its rule.

    `instruction_source` is the provenance of the instruction
    itself ("user" vs "tool_output" / "web_content" /
    "document_content" / ...). When it is not supplied, the
    session taint state is consulted (guarded: an unconsultable
    injection module reads as tainted — A1 fail-closed). Callers may pass
    `tainted=` explicitly to pin the taint state in tests.
    """
    args = args or {}
    args_norm = _args_by_norm(args)

    # Session taint (guarded: an unconsultable injection module
    # reads as TAINTED — A1 fail-closed, so _effective_source
    # yields "tainted-session" and Rule 3 refuses money moves
    # rather than letting an unprovable-clean session move money).
    if tainted is None:
        tainted_flag = False
        taint_src = None
        try:
            from .. import injection as _inj  # Phase 3 F2b: file now in core/funnel/; flat sibling climbs one more level

            tainted_flag = bool(_inj.tainted())
            taint_src = _inj.taint_source() if tainted_flag else None
        except Exception:  # noqa: BLE001 — A1 fail-closed: unconsultable
            # reads TAINTED with an unknown source.
            tainted_flag, taint_src = True, None
    else:
        tainted_flag, taint_src = bool(tainted), None
    source = _effective_source(instruction_source, tainted_flag,
                               taint_src)

    # Rule 1 — secrets, in any tool, before anything else.
    hit = _secret_hit(tool_name, args)
    if hit is not None:
        return MoneyRefusal(
            RULE_1,
            f"Refused — {RULE_1}: VYUK never enters, reads, "
            f"relays, stores or submits an OTP, UPI PIN, card "
            f"PIN/CVV, or 3-D Secure password ({hit}). Do that "
            f"step yourself, directly, outside VYUK.")

    # Rule 2 — KYC / identity documents.
    if _is_kyc(tool_name, args):
        return MoneyRefusal(
            RULE_2,
            f"Refused — {RULE_2}: VYUK never performs KYC or "
            f"holds identity documents. Complete KYC yourself, "
            f"directly with the provider.")

    move = _is_money_move(tool_name, args)
    invest = _is_investment(tool_name, args)
    credit = _is_credit(tool_name, args)
    refund = _is_refund_or_payout(tool_name, args)
    mandate = _is_mandate_or_card_save(tool_name, args)
    any_money_action = move or invest or credit or refund or mandate

    # Rule 3 (source half) — injection-originated money moves
    # are REFUSED, never routed to confirmation.
    if any_money_action and _is_injection_source(source):
        return MoneyRefusal(
            RULE_3,
            f"Refused — {RULE_3}: VYUK never moves money on an "
            f"instruction whose source is tool, web, or document "
            f"content. An instruction that arrived in content "
            f"is refused outright — it is never sent to "
            f"confirmation. Say the payment yourself, in your "
            f"own words, in a clean turn.")

    # Rule 6 — mandate / saved card / raised cap: only a fresh
    # amount-and-payee-specific confirmation in the request
    # itself passes the guard; anything else refuses.
    if mandate:
        payee = _first(args_norm, "payee", "recipient",
                       "beneficiary", "to")
        amount = _coerce_amount(_first(args_norm, "amount",
                                       "paymentamount"))
        if payee is not None and amount is not None \
                and _fresh_confirmation_ok(args, payee, amount):
            return None
        return MoneyRefusal(
            RULE_6,
            f"Refused — {RULE_6}: VYUK never creates an "
            f"e-mandate, saves a card, or raises a cap without "
            f"a fresh amount-and-payee-specific confirmation "
            f"for this exact payee and amount. A generic yes "
            f"at a confirmation popup is not that "
            f"confirmation.")

    # Rule 7 — refunds / payouts on a chat instruction alone.
    if refund:
        if _out_of_band_approval(args):
            return None
        return MoneyRefusal(
            RULE_7,
            f"Refused — {RULE_7}: VYUK never issues a refund "
            f"or payout on a chat instruction alone. A refund "
            f"needs an out-of-band approval reference, not "
            f"just this conversation.")

    # Rule 8 — first-seen payee above the default cap.
    if move:
        amount = _coerce_amount(_first(args_norm, "amount",
                                       "paymentamount"))
        if amount is not None and amount > FIRST_SEEN_CAP \
                and _first_seen_payee(args) \
                and not _identifier_verified(args):
            return MoneyRefusal(
                RULE_8,
                f"Refused — {RULE_8}: VYUK never pays a "
                f"first-seen payee above the default cap "
                f"without out-of-band identifier verification "
                f"(VPA / bank-name read-back, identifiers "
                f"copied character-for-character). Verify the "
                f"payee's identifiers out of band first, then "
                f"retry with that verification recorded.")

    # Rule 4 — personalised investment transactions.
    if invest:
        return MoneyRefusal(
            RULE_4,
            f"Refused — {RULE_4}: VYUK never executes "
            f"personalised investment transactions. Place "
            f"investment orders yourself, directly with your "
            f"broker or fund.")

    # Rule 5 — taking or rolling credit.
    if credit:
        return MoneyRefusal(
            RULE_5,
            f"Refused — {RULE_5}: VYUK never takes or rolls "
            f"credit. Arrange borrowing yourself, directly "
            f"with the lender.")

    # Rule 3 (ambiguity half) — each dimension refuses alone.
    if move:
        payee = _first(args_norm, "payee", "recipient",
                       "beneficiary", "to")
        if args_norm.get("payeeunresolved") \
                or args_norm.get("payeeresolved") is False \
                or payee is None \
                or _as_text(payee).strip().lower() \
                in _UNRESOLVED_PAYEES:
            return MoneyRefusal(
                RULE_3,
                f"Refused — {RULE_3}: VYUK never moves money "
                f"on an ambiguous instruction — the payee is "
                f"unresolved. Name the exact payee first.")
        raw_amount = _first(args_norm, "amount", "paymentamount")
        amount = _coerce_amount(raw_amount)
        if raw_amount is None or amount is None \
                or _amount_is_approximate(raw_amount) \
                or amount <= 0:
            return MoneyRefusal(
                RULE_3,
                f"Refused — {RULE_3}: VYUK never moves money "
                f"on an ambiguous instruction — the amount is "
                f"missing, approximate, or not an exact "
                f"number. State the exact amount first.")
        currency = _first(args_norm, "currency", "curr")
        if currency is None or not _as_text(currency).strip() \
                or args_norm.get("currencyunstated"):
            return MoneyRefusal(
                RULE_3,
                f"Refused — {RULE_3}: VYUK never moves money "
                f"on an ambiguous instruction — the currency "
                f"is unstated. State the currency first.")
    return None


# Backwards-friendly alias: the funnel and suites may call the
# check either name; both are the same verdict.
check_request = check


def refusal_message(tool_name: str = "", args: dict | None = None,
                    instruction_source=None, *,
                    tainted=None) -> str | None:
    """The refusal message for a call, or None when allowed."""
    refusal = check(tool_name, args, instruction_source,
                    tainted=tainted)
    return refusal.message if refusal is not None else None
