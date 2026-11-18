"""Study edition — citation discipline (ST-5).

The honesty machinery, shared with Research by pattern: every
study artefact says where it came from, and weak evidence gets
flagged instead of trusted.

Authority: wave-4 scout §KW-1 (kotaemon PILOT — highlighted source
passage + relevance score + warning when retrieval returns only
low-relevance sources) + Study scope §2 (ST-5). KW-1 itself stays
a PILOT for the later Scholar surface; only its citation pattern
is adopted here, and no kotaemon code ships in this module.
Research assistance (pool 1443) is NOT built twice here: it rides
the Research track; this module contributes only the shared
citation discipline.

Rules (binding, asserted by suite):
- An artefact without provenance is refused, not published.
- A low-relevance case produces the warning, not a confident
  answer: when retrieval strength falls below the threshold, the
  output says retrieval is weak and the claim is not well
  supported, instead of presenting confidence.
- Provenance records carry material + location + retrieval
  strength, so any artefact is checkable back to its source.

No cross-module imports (edition precedent): ST-1 enforces its
Source field and ST-3 carries provenance per problem using this
same discipline by convention — the functions here are the shared
shape callers use, not a shared import. No pip-time dependency.
"""

from __future__ import annotations

DEFAULT_LOW_RELEVANCE_THRESHOLD = 0.5

LOW_RELEVANCE_WARNING = (
    "Warning: retrieval is weak — only low-relevance sources were "
    "found for this claim. This answer is not well supported; check "
    "the source material before trusting it."
)


class CitationError(ValueError):
    """An artefact offered without usable provenance."""


class Provenance:
    """Where an artefact came from: material, location (lecture +
    page, file + section, ...), and retrieval strength 0..1."""

    def __init__(self, material, location, retrieval_strength, source=""):
        if not material or not str(material).strip():
            raise CitationError("Provenance needs a material.")
        if not location or not str(location).strip():
            raise CitationError("Provenance needs a location.")
        try:
            strength = float(retrieval_strength)
        except (TypeError, ValueError):
            raise CitationError("Retrieval strength must be a number.")
        if not 0.0 <= strength <= 1.0:
            raise CitationError(
                "Retrieval strength %r is outside 0..1." % (retrieval_strength,))
        self.material = material
        self.location = location
        self.retrieval_strength = strength
        self.source = source

    def to_dict(self):
        return {"material": self.material, "location": self.location,
                "retrieval_strength": self.retrieval_strength,
                "source": self.source}

    @classmethod
    def from_dict(cls, d):
        return cls(d["material"], d["location"], d["retrieval_strength"],
                   d.get("source", ""))


def enforce_provenance(artefact):
    """Refuse an artefact without provenance. artefact is a dict
    with a "provenance" key holding a Provenance (or its dict form)
    or None. Returns the Provenance on pass."""
    prov = artefact.get("provenance") if isinstance(artefact, dict) else None
    if prov is None:
        raise CitationError(
            "Refused: artefact without provenance. Every study "
            "artefact must say where it came from.")
    if isinstance(prov, dict):
        prov = Provenance.from_dict(prov)
    if not isinstance(prov, Provenance):
        raise CitationError("Provenance is not a Provenance record.")
    return prov


def assess(provenance, threshold=DEFAULT_LOW_RELEVANCE_THRESHOLD):
    """KW-1 pattern: judge a claim against its provenance. Returns
    (verdict, warning_or_None): verdict is "supported" when
    retrieval strength meets the threshold, else "weak" with the
    low-relevance warning. A weak case NEVER returns a confident
    answer — the caller must surface the warning instead."""
    if isinstance(provenance, dict):
        provenance = Provenance.from_dict(provenance)
    if provenance.retrieval_strength >= threshold:
        return "supported", None
    return "weak", LOW_RELEVANCE_WARNING


def cite_check(artefact, threshold=DEFAULT_LOW_RELEVANCE_THRESHOLD):
    """Combined gate for callers: enforce provenance, then assess
    it. Returns (provenance, verdict, warning_or_None). Raises
    CitationError on missing provenance."""
    prov = enforce_provenance(artefact)
    verdict, warning = assess(prov, threshold)
    return prov, verdict, warning


__all__ = [
    "DEFAULT_LOW_RELEVANCE_THRESHOLD", "LOW_RELEVANCE_WARNING",
    "CitationError", "Provenance", "enforce_provenance", "assess",
    "cite_check",
]
