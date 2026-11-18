"""Fleet Learning track (v5.74) — FL-3 redaction engine.

Two layers, kept separate by design:
1. Native known-filth regex detectors (log-pass ONLY, WATCH
   acceptable, never the gate): emails, IPv4 addresses, Windows
   and Unix paths, API-key-shaped tokens. The redaction log
   records types and counts ONLY — never the matched text.
2. Presidio PILOT (lazy, fail-closed, recognizers-only scope):
   reuses the Business track's bz6_presidio_v572 pilot module
   directly — no new dependency, no new licence claim. The
   presidio-analyzer 2.2.364 MIT verdict was primary-verified at
   CC-1 v5.72 from wheel METADATA. The Windows footprint
   measurement (installed size + startup cost) is OWED and gates
   any wider pilot scope; it is not claimed here.
3. sos-clean-style pseudonymisation map: consistent pseudonyms
   within one bundle; the private map NEVER leaves with the
   bundle (it is not part of bundle output — tested).

This module NEVER imports the known-filth library whose licence
is owed again under the dispute rule and whose gate use is
rejected; its GPL NER detector pack is EXCLUDED
regardless. Native detectors above are the honest alternative.
"""

import re

from .bz6_presidio_v572 import PilotUnavailable, redact_pii

__all__ = ["PilotUnavailable", "redact_log_text", "redact_with_presidio",
           "PseudonymMap"]

_DETECTORS = (
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("IPV4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("WIN_PATH", re.compile(r"\b[A-Za-z]:\\[^\s\"']+")),
    ("UNIX_PATH", re.compile(r"(?<![\w./])/(?:home|Users|tmp|var)/[^\s\"']+")),
    ("API_KEY", re.compile(
        r"\b(?:sk-[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{16}|"
        r"ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})\b")),
)


def redact_log_text(text):
    """Redact known-filth patterns from log text (log-pass only).

    Returns {"redacted": str, "log": [{"type": str, "count": int}]}.
    The log carries types and counts only — matched secrets never
    appear in it. This pass is WATCH-acceptable only: it is NOT
    the redaction gate (the gate is the Presidio pilot + the
    user's preview, in fleet_consent).
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    log = []
    redacted = text
    for kind, pattern in _DETECTORS:
        matches = pattern.findall(redacted)
        if matches:
            log.append({"type": kind, "count": len(matches)})
            redacted = pattern.sub("<%s>" % kind, redacted)
    return {"redacted": redacted, "log": log}


def redact_with_presidio(text, entities=None, analyzer=None):
    """Presidio pilot redaction (lazy, fail-closed).

    Delegates to the Business pilot: when presidio-analyzer is
    absent and no analyzer is injected, PilotUnavailable is raised
    — the pilot never pretends to scan. ``analyzer`` injection
    exists for tests (a fake engine); production callers pass none.
    Returns {"redacted": str, "count": int}.
    """
    return redact_pii(text, entities=entities, analyzer=analyzer)


class PseudonymMap:
    """Consistent pseudonyms within one bundle, private by design.

    ``apply`` replaces caller-named identifiers with stable
    tokens (PSEUDO-1, PSEUDO-2, ...) in encounter order. The map
    itself is never serialised into bundle output: ``bundle_view``
    returns only the redacted text, and callers must not attach
    the map to anything they send.
    """

    def __init__(self):
        self._map = {}

    def apply(self, text, identifiers):
        """Replace each identifier with its stable pseudonym."""
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        out = text
        for ident in identifiers:
            if ident not in self._map:
                self._map[ident] = "PSEUDO-%d" % (len(self._map) + 1)
            out = out.replace(ident, self._map[ident])
        return out

    def bundle_view(self, text):
        """The only view of mapped text safe to put in a bundle."""
        return {"redacted": text, "pseudonyms_used": len(self._map)}
