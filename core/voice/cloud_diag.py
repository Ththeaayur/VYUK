"""Cloud diagnostics (Fix 5, 2026-11-05) — ONE probe, TWO doors.

THE LOOPHOLE FAMILY this closes (all read from the tree, not assumed):
  L1. "Test key" is REST-only: live_client_helpers.validate_api_key
      does one client.models.get — a pass proves the key authenticates
      and the model exists, but says NOTHING about the Live door
      (bidiGenerateContent). The Settings label still read "Key works",
      so a REST pass + Live fail presented as "key OK / key broken"
      with no door distinction.
  L2. No quota class in the Live path: _classify_key_error buckets
      model/auth/network/unknown only — a 429 / RESOURCE_EXHAUSTED /
      quota refusal fell to "unknown" (or "auth" via the "invalid"
      wording). The Groq path already names 429; Gemini Live did not.
  L3. Key tier unknowable and unreported: doctor.check_gemini_key_tier
      says so honestly ("VYUK can't tell a free key from a paid one"),
      and nothing else tries. Google exposes no tier endpoint — the
      tier is a billing property of the AI Studio project. The only
      honest tier signal is a quota error actually observed.
  L4. Verbatim swallowed in the UI: _report_exhaustion capped Google's
      exact reply at 180 chars in the status (which IS the tooltip),
      and _friendly_error had no quota / bidi branch, so those
      failures fell through to "Something went wrong". The full text
      DID reach data/activity.log — half the journey, not all of it.
  L5. Proxy premise corrected: the system-proxy export + direct
      retry already exist (live_client_proxy._apply_system_proxy_env,
      start() retries once without it). The gap was classification,
      not the retry — a proxy failure still read as a generic
      timeout / model rejection.
  L6. Quota burns reconnect attempts: _is_fatal_error treats a model
      rejection as fatal (no auto-reconnect burn — correct, pinned),
      but a quota refusal was NOT fatal, so the GUI's 2/5/15/30 s
      episode burned four attempts against a server-side limit.
  L7. Brain fallback IS announced (FALLBACK status -> _friendly_fallback
      -> chat + caption, brain_session_core.py:374). Pinned, not rebuilt.

THE ELEGANT SOLUTION — one design, not one fix per loophole:
  ONE metadata GET answers both doors. client.models.get(model)
  proves the REST door (the GET itself) AND the Live door: the
  returned Model carries supported_actions, and the Live API is
  usable for this key+model exactly when "bidiGenerateContent" is
  in it. No Live session is opened to test the Live door — opening
  one would be heavy, consume quota, and need the audio machinery.
  The same single classifier (classify_live_error) serves the
  diagnostic, the connect-exhaustion report, and the key test, so
  the three surfaces can never disagree about what a failure was.

REJECTED alternatives (with reasons):
  * Open a real Live session as the Live-door test — heavy, consumes
    quota, needs audio machinery; models.get answers the same
    question with one GET.
  * Two separate test buttons (REST / Live) — two confusing verdicts
    for one key; one diagnostic with two NAMED doors is the same
    probe, honestly labelled.
  * Guess the tier from the key prefix / project id — no such signal
    exists in the key; that would be fabrication. Tier stays
    "unknown" unless a quota error is actually observed.
  * Silent brain switching on cloud failure — the FALLBACK notice
    already announces it; keep the announcement, add nothing silent.
  * Retry quota failures inside the reconnect episode — burns
    attempts against a server-side limit; quota is fatal for the
    episode, the human waits (billing is the fix, not retrying).

SECURITY: no function here ever returns, logs, or prints a key.
Every diagnostic string passes through _redact first.
"""

from __future__ import annotations

import re
from typing import Any

try:
    from google import genai
except Exception:  # noqa: BLE001 — a build without google-genai
    # still imports: cloud paths report the missing library
    # honestly instead of crashing (live_client's own pattern).
    genai = None  # type: ignore[assignment]

# Reuse the Live path's own classifiers — one classification
# vocabulary for the diagnostic, the exhaustion report, and the
# key test, so the three surfaces can never disagree.
from .live_client_error_cost import (
    _classify_key_error,
    _error_text,
    _is_model_rejection,
    _is_network_error,
    _is_quota_error,
)

LIVE_ACTION = "bidiGenerateContent"

# Kinds classify_live_error / diagnose_cloud report. "quota" is the
# Fix-5 addition to _classify_key_error's model/auth/network/unknown
# set; the two classifiers share this vocabulary on purpose.
KINDS = ("model", "quota", "auth", "network", "library", "unknown")

_KEY_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z_\-]{10,}"),
    re.compile(r"(api[_-]?key\s*[=:]\s*['\"]?)[^'\"\s,}]+",
               re.IGNORECASE),
    re.compile(r"([\"']key[\"']\s*:\s*['\"])[^'\"]+",
               re.IGNORECASE),
)


def _redact(text: str) -> str:
    """Strip anything that looks like an API key from `text`.

    A diagnostic that leaks a key is a failure of this batch:
    every string this module returns has passed through here."""
    out = str(text or "")
    out = _KEY_PATTERNS[0].sub("[key redacted]", out)
    out = _KEY_PATTERNS[1].sub(r"\1[key redacted]", out)
    out = _KEY_PATTERNS[2].sub(r"\1[key redacted]", out)
    return out


_CAPTIONS = {
    "model": ("Google refused the voice model for this key — "
              "the model name is wrong for Live, or this key "
              "cannot use Gemini Live models."),
    "quota": ("Google refused with a quota / rate limit — the "
              "key has hit its Live usage limit for now."),
    "auth": ("Google rejected the API key — it looks invalid, "
             "revoked, or from a project without the Gemini "
             "API enabled."),
    "network": ("VYUK could not reach Google from this PC — "
                "check the internet connection (on campus or "
                "office networks the system proxy is the usual "
                "suspect)."),
    "library": ("The Google library (google-genai) is not "
                "installed in this build, so the cloud brain "
                "cannot be tested."),
    "unknown": ("Google answered in a way VYUK could not "
                "classify — the exact reply is kept verbatim "
                "below, nothing was guessed."),
}


def classify_live_error(exc: BaseException) -> dict[str, str]:
    """Classify ONE Live failure into {kind, verbatim, caption}.

    Order is load-bearing: model before auth ("invalid model"
    contains "invalid", which the auth check reads as a key
    problem), quota before auth (a quota body can say "invalid"),
    network before auth (a dead route is not a bad key). The
    verbatim is the exception's own words, redacted, capped at
    300 chars (parity with the connect ERROR cap); the caption
    is plain words naming the door and the cause. Never raises:
    an unclassifiable error is "unknown" with its verbatim kept,
    never a guess."""
    try:
        text = _error_text(exc)
        if _is_model_rejection(exc) or LIVE_ACTION.lower() in text:
            kind = "model"
        elif _is_quota_error(exc):
            kind = "quota"
        elif _is_network_error(exc):
            kind = "network"
        else:
            kind = _classify_key_error(exc)
            if kind not in ("model", "auth", "network"):
                kind = "unknown"
        verbatim = _redact(f"{type(exc).__name__}: {exc}")[:300]
        return {"kind": kind, "verbatim": verbatim,
                "caption": _CAPTIONS[kind]}
    except Exception:  # noqa: BLE001 — classification never breaks
        return {"kind": "unknown", "verbatim": "unclassifiable error",
                "caption": _CAPTIONS["unknown"]}


def live_support_from_model(model: Any) -> str:
    """The Live door, read off ONE models.get result.

    Returns "supported" when the model's supported_actions list
    carries bidiGenerateContent, "not_supported" when the list
    is present without it, and "unknown" when the list is
    absent — an absent list is missing evidence, not a refusal,
    and must never be reported as a failure."""
    actions = getattr(model, "supported_actions", None)
    if actions is None:
        return "unknown"
    try:
        return ("supported" if LIVE_ACTION in list(actions)
                else "not_supported")
    except Exception:  # noqa: BLE001 — a shapeless list is unknown
        return "unknown"


def _default_client_factory(key: str):
    """The production client factory: a bare genai.Client.

    The proxy is NOT passed here — live_client_proxy exports the
    detected system proxy into the environment and google-genai
    honours it from there (the v5.29 route, kept)."""
    if genai is None:
        raise ImportError("google-genai is not installed")
    return genai.Client(api_key=key)


def diagnose_cloud(key: str, model: str,
                   client_factory=None) -> dict[str, Any]:
    """ONE probe, TWO doors. Returns a redacted report dict::

        {"rest": {"status": "pass"|"fail", "kind": ..., "detail": ...},
         "live": {"status": "pass"|"fail"|"unknown", "kind": ...,
                  "detail": ...},
         "tier": "unknown",
         "tier_note": "...",
         "summary": "REST: ... / Live: ..."}

    The probe is a single client.models.get(model): the GET
    itself is the REST door, and the returned Model's
    supported_actions is the Live door. `client_factory` is
    injected by tests (a fake client); production passes None
    and gets the real genai client. Never raises, never returns
    the key, never invents a tier: tier is "unknown" unless a
    quota error was actually observed, in which case tier_note
    says so and names billing in AI Studio as the fix — the
    tier itself is a billing property Google does not expose,
    so even then tier stays "unknown"."""
    key = (key or "").strip()
    model = (model or "").strip()
    if not key:
        return {
            "rest": {"status": "fail", "kind": "auth",
                     "detail": "No API key to test."},
            "live": {"status": "unknown", "kind": "unknown",
                     "detail": "Live could not be tested without "
                               "a key."},
            "tier": "unknown",
            "tier_note": ("Google does not expose the key tier — "
                          "it lives in the AI Studio project's "
                          "billing."),
            "summary": ("REST: no key — Live: not tested."),
        }
    if not model:
        return {
            "rest": {"status": "fail", "kind": "model",
                     "detail": "No voice model name configured."},
            "live": {"status": "unknown", "kind": "unknown",
                     "detail": "Live could not be tested without "
                               "a model name."},
            "tier": "unknown",
            "tier_note": ("Google does not expose the key tier — "
                          "it lives in the AI Studio project's "
                          "billing."),
            "summary": ("REST: no model name — Live: not tested."),
        }
    factory = client_factory or _default_client_factory
    try:
        client = factory(key)
        got = client.models.get(model=model)
    except ImportError:
        return {
            "rest": {"status": "fail", "kind": "library",
                     "detail": "The Google library (google-genai) "
                               "is not installed in this build."},
            "live": {"status": "unknown", "kind": "unknown",
                     "detail": "Live could not be tested without "
                               "the Google library."},
            "tier": "unknown",
            "tier_note": ("Google does not expose the key tier — "
                          "it lives in the AI Studio project's "
                          "billing."),
            "summary": ("REST: library missing — Live: not tested."),
        }
    except Exception as exc:  # noqa: BLE001
        classified = classify_live_error(exc)
        kind = classified["kind"]
        # The GET is the shared probe: an auth / model / quota
        # refusal answers BOTH doors (same key, same model); a
        # network failure answers neither — Live is "unknown",
        # not failed, because it was never reached.
        if kind in ("model", "auth", "quota"):
            live = {"status": "fail", "kind": kind,
                    "detail": classified["caption"]}
        else:
            live = {"status": "unknown", "kind": kind,
                    "detail": ("Live could not be tested — the "
                               "connection failed first: "
                               + classified["caption"])}
        tier_note = (
            "Google refused with a quota / rate limit — free-tier "
            "limits are the common cause; turn on billing for the "
            "key's project in Google AI Studio to lift them. The "
            "tier itself cannot be read from the key, so it stays "
            "unknown."
            if kind == "quota" else
            "Google does not expose the key tier — it lives in "
            "the AI Studio project's billing.")
        return {
            "rest": {"status": "fail", "kind": kind,
                     "detail": classified["caption"] + " Google's "
                             "exact reply: " + classified["verbatim"]},
            "live": live,
            "tier": "unknown",
            "tier_note": tier_note,
            "summary": (f"REST: failed ({kind}) — Live: "
                        f"{live['status']} ({live['kind']})."),
        }
    support = live_support_from_model(got)
    if support == "supported":
        live = {"status": "pass", "kind": "ok",
                "detail": (f"Model '{model}' lists "
                           f"{LIVE_ACTION} — this key can use "
                           "Gemini Live.")}
    elif support == "not_supported":
        live = {"status": "fail", "kind": "model",
                "detail": (f"Model '{model}' does not list "
                           f"{LIVE_ACTION} for this key — Google "
                           "will refuse it for Live (not supported "
                           "for bidiGenerateContent).")}
    else:
        live = {"status": "unknown", "kind": "unknown",
                "detail": ("Google did not say whether this model "
                           "supports Live for this key — the "
                           "supported-actions list was absent, so "
                           "nothing was assumed.")}
    return {
        "rest": {"status": "pass", "kind": "ok",
                 "detail": f"Model '{model}' is available."},
        "live": live,
        "tier": "unknown",
        "tier_note": ("Google does not expose the key tier — it "
                      "lives in the AI Studio project's billing."),
        "summary": (f"REST: passed — Live: {live['status']} "
                    f"({live['kind']})."),
    }
