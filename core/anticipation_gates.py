"""Split from core/anticipation.py (skills split B19).
Move only: code below is verbatim from the old module apart from
module-level import lines.
"""

from __future__ import annotations



# ---------------------------------------------------------------------------
# Named constants — the gates read these, never magic numbers inline.
# ---------------------------------------------------------------------------

#: AN-1: a prediction below this confidence NEVER becomes a
#: suggestion. Below it, suggestion() returns None (silence).
SUGGESTION_CONFIDENCE_THRESHOLD = 0.70

#: AN-1: predictions must have been scored this many times against
#: real outcomes before suggestion() may return anything at all.
#: This is the progressive-validation floor: no accuracy evidence,
#: no suggestion, however confident the raw counts look.
MIN_EVIDENCE_SAMPLES = 5

#: AN-3/AN-4: every bench helper in this module is dev-only.
DEV_ONLY = True

# ---------------------------------------------------------------------------
# CA-1 denylist — this module's OWN list (not imported from any
# other module, so its behaviour cannot drift with another file's
# edits). Process names are compared lowercased with ".exe"
# stripped; title keywords are lowercased substrings.
# ---------------------------------------------------------------------------

DEFAULT_DENYLIST_PROCESSES = frozenset({
    # Password managers / authenticators.
    "1password", "lastpass", "bitwarden", "keepass", "keepassxc",
    "dashlane", "nordpass", "roboform", "authy", "enpass",
    "keeper", "protonpass",
    # Personal finance / banking companions.
    "quicken", "moneydance", "gnucash",
})

DEFAULT_DENYLIST_TITLE_KEYWORDS = frozenset({
    "password manager", "password vault", "authenticator",
    "incognito", "inprivate", "private browsing", "private window",
    "online banking", "internet banking", "bank login",
    "banking login", "bank sign in", "bank sign-in",
    "online bank", "my bank account",
})


def _normalise_process(name) -> str:
    text = str(name or "").strip().lower().replace("\\", "/")
    text = text.rsplit("/", 1)[-1]
    if text.endswith(".exe"):
        text = text[:-4]
    return text


def is_denylisted(process_name=None, title=None,
                  processes=DEFAULT_DENYLIST_PROCESSES,
                  title_keywords=DEFAULT_DENYLIST_TITLE_KEYWORDS) -> str | None:
    """Return the denylist reason for an observation, else None.

    Returns ``"process:<name>"`` or ``"title-keyword:<keyword>"``.
    The caller must treat a non-None return as SUPPRESS — never
    construct, store, or forward an event for this observation.
    Pure function: it retains nothing about the observation.
    """
    proc = _normalise_process(process_name)
    if proc:
        for entry in processes or ():
            if proc == _normalise_process(entry):
                return f"process:{proc}"
    lowered = str(title or "").strip().lower()
    if lowered:
        for keyword in title_keywords or ():
            keyword = str(keyword).strip().lower()
            if keyword and keyword in lowered:
                return f"title-keyword:{keyword}"
    return None
