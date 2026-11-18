"""Wave 7 (v5.62) pilot / licence verdict records — data, not code.

Every entry names a seat's licence/terms verdict as read from a
primary source (downloaded wheel METADATA + LICENSE files, or
the source's own terms page), its ship state, and what ships.
Nothing in this module is imported by any runtime path as an
engine: seats marked anything other than ``ships: live`` are
records of a gate, not a capability. AGPL / NC / revenue-cap /
territory-restricted material never ships; a verdict naming such
a licence for a STUDY/REFUSED seat is a record of exclusion,
not a use.
"""

from __future__ import annotations

VERDICTS = {
    # --- Anticipation (stdlib only; no third-party verdicts) ---
    "prefixspan-extratools": {
        "licence": "NOT-USED",
        "state": "SIDESTEPPED",
        "evidence": (
            "PrefixSpan implemented in own code "
            "(core/anticipation.py); Extratools never "
            "imported, so its unverified licence is moot."
        ),
        "ships": "own-core-only",
    },
    # --- Reach ---
    "apprise": {
        "licence": "BSD-2-Clause",
        "state": "VERIFIED-WITHIN-PIN",
        "evidence": (
            "apprise 1.13.1 wheel LICENSE + METADATA read "
            "locally. Pin >=1.0,<2.0 enforced in code; "
            "2.1.0 also BSD-2-Clause but REFUSED on the "
            "breaking-major pin (licence does not override "
            "the pin)."
        ),
        "ships": "gate-only",
    },
    "pyserial": {
        "licence": "BSD",
        "state": "VERIFIED",
        "evidence": (
            "pyserial 3.5 wheel METADATA read locally "
            "(BSD field + classifier; no LICENSE file in "
            "the wheel). Lazy optional only."
        ),
        "ships": "gate-only",
    },
    "presidio": {
        "licence": "MIT-CODE-ONLY",
        "state": "VERIFIED-CODE / MODEL-OWED-O-24k",
        "evidence": (
            "presidio / presidio-analyzer / presidio-"
            "anonymizer 2.2.364 + presidio-image-redactor "
            "0.0.60 METADATA License-Expression MIT read "
            "locally (code only). Deployed spaCy model "
            "licence is SEPARATE and STILL-OWED (O-24k, "
            "#1 ship-blocking for the redaction seat); "
            "model NOT deployed, NER leg absent."
        ),
        "ships": "pilot-only",
    },
    "ha-mcp": {
        "licence": "MIT",
        "state": "VERIFIED-EXTERNAL-SERVER",
        "evidence": (
            "ha-mcp 3.5.1 LICENSE + METADATA read locally. "
            "Treated as an external user-installed server, "
            "never imported; toolbox-routing only."
        ),
        "ships": "routing-only",
    },
    "homeassistant-mcp-official": {
        "licence": "UNVERIFIED",
        "state": "STILL-OWED",
        "evidence": (
            "No official Home Assistant MCP PyPI package "
            "verified from any primary source. Candidates "
            "homeassistant_mcp 3.2.1 (MIT) and mcp-server-"
            "home-assistant 0.1.0 (Apache-2.0) are third-"
            "party, NOT claimed official. HM-1 therefore "
            "imports NO Home Assistant package (own-code "
            "consume-only shape over the declared MCP bus)."
        ),
        "ships": "nothing",
    },
    # --- Daily layer ---
    "pypdf": {
        "licence": "BSD-3-Clause",
        "state": "VERIFIED",
        "evidence": (
            "pypdf 6.21.2 wheel METADATA (License-"
            "Expression: BSD-3-Clause) + licenses/LICENSE "
            "read locally. Lazy-guarded optional use only; "
            "not added to requirements."
        ),
        "ships": "gate-only",
    },
    "breach-source": {
        "licence": "STILL-OWED",
        "state": "STILL-OWED-HARD-BLOCK",
        "evidence": (
            "Breach-data source terms unverified; live "
            "breach checking hard-blocked until verified. "
            "Hash-prefix machinery only; no source "
            "contacted."
        ),
        "ships": "nothing",
    },
    # --- Carried shelves (unchanged by this wave) ---
    "dots-ocr": {
        "licence": "CUSTOM + AGPL/NC-DATA",
        "state": "STUDY-ONLY",
        "evidence": "Per plan §5.5: custom licence + AGPL/NC data components — no embed until cleared.",
        "ships": "nothing",
    },
    "teleocr": {
        "licence": "UNNAMED",
        "state": "STILL-OWED-O-15",
        "evidence": "No licence identifier in any source read; licence + Windows/CPU runtime check remain owed.",
        "ships": "nothing",
    },
}


def verdict(seat: str) -> dict:
    """The verdict record for a seat, or a blocked record."""
    entry = VERDICTS.get(seat)
    if entry is None:
        return {
            "seat": seat,
            "licence": "UNKNOWN",
            "state": "BLOCKED-UNKNOWN-SEAT",
            "evidence": "No verdict exists for this seat.",
            "ships": "nothing",
        }
    return {"seat": seat, **entry}
