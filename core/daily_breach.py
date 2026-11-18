from __future__ import annotations  # INTENTIONAL (skills split B21): moved code was written under postponed evaluation.
import hashlib

# ===========================================================================
# Breach monitor — hash-prefix machinery ONLY
# ===========================================================================

class BreachRefused(RuntimeError):
    """Raised when a breach lookup is refused (terms unverified)."""


def breach_prefix(password: str) -> str:
    """k-anonymity prefix for a password: first 5 hex chars of
    its SHA-1, uppercased (the HIBP range-API shape). Fully
    local. Returns ONLY the prefix — never the full hash, never
    the password. Callers must not log the return alongside the
    password, and must never send anything but the prefix."""
    if not isinstance(password, str) or not password:
        raise ValueError("breach_prefix needs a non-empty password string")
    digest = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    return digest[:5]


class BreachSource:
    """Breach-data source interface. Terms are STILL-OWED and
    hard-block the item: until ``terms_verified`` is True, every
    lookup REFUSES and no source is contacted. Even with verified
    terms this build performs NO live lookup and claims none —
    it returns the prefix machinery result only; a live check
    would be a separate, explicitly authorised integration. The
    source therefore never receives a password or a full hash
    from this module — only a 5-char prefix could ever leave,
    and only through a verified source outside this module."""

    TERMS_STILL_OWED = (
        "breach-data source terms are STILL-OWED and unverified — "
        "no breach source may be contacted until its terms are "
        "verified. No live breach check was performed.")

    def __init__(self, terms_verified: bool = False):
        self.terms_verified = bool(terms_verified)

    def lookup(self, prefix: str):
        """(performed, detail). performed is always False in this
        build: without verified terms it is a refusal; with
        verified terms it is an honest not-implemented (no live
        check exists here to claim). Raises BreachRefused when
        terms are unverified."""
        if not self.terms_verified:
            raise BreachRefused(self.TERMS_STILL_OWED)
        if not isinstance(prefix, str) or len(prefix) != 5:
            raise ValueError("breach lookup needs a 5-character prefix")
        return False, ("terms verified, but no live breach check is "
                       "implemented in this build — prefix computed "
                       "locally only; no breach status is claimed.")


