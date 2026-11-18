"""PF-16 voice lock — on-device-only speaker verification, gated.

Conditions (absolute, from the task):
  * Templates live on-device only, never egress. This module
    contains NO network code — no imports of requests, urllib,
    socket, http, or any cloud client — and stores templates
    only in the caller-supplied store (a local mapping). A
    template is a unit-normalised embedding vector; raw audio is
    never stored here.
  * No hosted verifier, ever. Verification is cosine similarity
    between a fresh embedding and the enrolled template, against
    a threshold, computed locally in this module.
  * The embedding ENGINE is injected (a callable). No engine is
    bundled, no engine is imported, and no engine is a new
    dependency: SpeechBrain / Resemblyzer would both be new
    heavy dependencies whose pre-trained WEIGHTS have no
    commercial-redistribution clearance (see
    core/wake_licences.py). Until the coordinator clears one
    specific weights file and its dependencies, `VoiceLock`
    has no engine and `verify()` returns "unavailable" — it
    does NOT fall back to a weaker substitute (a hand-rolled
    MFCC-distance check does not separate speaker from phrase
    and would false-accept the owner's own voice saying
    anything, and anyone else's voice saying the wake phrase).

v5.58 status: PF-16 does NOT ship. This module is the
on-device-only store + verification protocol, tested with an
injected stub embedder; the product behaviour with no engine
is "unavailable", which is honest and safe, and the existing
session-provenance lock (config.session_provenance_lock) is
untouched — it is a session lock, not voice verification, and
this module neither renames nor replaces it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# Cosine-similarity bar for "same speaker". Deliberately high:
# a lock that waves strangers through is worse than no lock.
DEFAULT_VERIFY_THRESHOLD = 0.75


def _normalise(vector: list[float]) -> list[float] | None:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm <= 0 or not vector:
        return None
    return [v / norm for v in vector]


def _cosine(a: list[float], b: list[float]) -> float | None:
    if len(a) != len(b) or not a:
        return None
    return sum(x * y for x, y in zip(a, b))


@dataclass
class VoiceLock:
    """On-device speaker template store + verifier.

    `embedder` maps audio bytes (or a caller-side feature blob)
    to an embedding vector, or None when no engine is available.
    The lock never calls out: embedding computation happens in
    the injected engine, on-device, by the caller's choice of a
    cleared engine — never here, never over a network.
    """

    threshold: float = DEFAULT_VERIFY_THRESHOLD
    embedder: object = None
    _templates: dict[str, list[float]] = field(default_factory=dict)

    @property
    def engine_available(self) -> bool:
        return self.embedder is not None

    def enrol(self, speaker_id: str, embedding: list[float]) -> bool:
        """Stores a UNIT-NORMALISED template, on-device only.
        Returns False (and stores nothing) for a degenerate
        embedding. Raw audio is never accepted here."""
        normalised = _normalise(embedding)
        if normalised is None:
            return False
        self._templates[speaker_id] = normalised
        return True

    def enrolled(self, speaker_id: str) -> bool:
        return speaker_id in self._templates

    def forget(self, speaker_id: str) -> bool:
        return self._templates.pop(speaker_id, None) is not None

    def verify(self, speaker_id: str,
               embedding: list[float]) -> dict:
        """Verify a fresh embedding against the enrolled template.

        Returns a dict with `status`: 'verified' / 'rejected' /
        'not-enrolled' / 'unavailable'. 'unavailable' is the
        v5.58 product state (no cleared engine, or no usable
        embedding) — and it is NOT a pass: callers must treat
        anything but 'verified' as "do not treat as the owner".
        """
        if speaker_id not in self._templates:
            return {"status": "not-enrolled", "score": None}
        normalised = _normalise(embedding)
        if normalised is None:
            return {"status": "unavailable", "score": None}
        score = _cosine(self._templates[speaker_id], normalised)
        if score is None:
            return {"status": "unavailable", "score": None}
        return {"status": ("verified" if score >= self.threshold
                            else "rejected"),
                "score": score}
