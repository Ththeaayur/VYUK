"""Wave 6 (v5.61) — KN-2.7 GLiNER2.5 extractor bake-off + triple gate.

GLiNER2.5 (74M, span-grounded, confidence-scored triples) versus
the incumbent LLM extraction, on a fixed EN+HI gold set, scored
by precision + corruption rate per written triplet. The bake-off
IS the check for the gate the plan names: whether any engine may
ingest through its bundled generative extractor at all — i.e.
whether the lite tier can ingest safely (no published benchmark
measures it on conversational EN/HI).

Honesty shape (Wave 4/5 bake-off rule): the GLiNER seat is a
facade — no weights downloaded, no triples fabricated. With no
engine present the verdict is `winner: None` (NO-DATA). A winner
requires EVERY seat measured on the shared gold set.

The live item in this wave is the gate, not the engine:
- A triple is a (subject, relation, object, confidence) record.
- Triples below the confidence threshold are HELD for
  confirmation and never written — the corruption control that
  keeps a bad extraction from becoming a settled fact.
- Generative-extractor ingestion is REFUSED until the bake-off
  measures it: `ingest_via_generative_extractor()` returns a
  refusal record, never a write, while the bake-off is undecided.

This module imports stdlib only. It never imports a brain, a
tool, or an executor.
"""

GLINER_MODEL = "gliner2.5"
GLINER_PARAMS = "74M"
CONFIDENCE_THRESHOLD = 0.75


class Triple:
    """One confidence-scored extraction triple."""

    def __init__(self, subject, relation, obj, confidence,
                 source=""):
        self.subject = str(subject or "")
        self.relation = str(relation or "")
        self.object = str(obj or "")
        self.confidence = float(confidence)
        self.source = source

    def key(self):
        return (self.subject.strip().lower(),
                self.relation.strip().lower(),
                self.object.strip().lower())

    def is_valid(self):
        """A triple with an empty leg, or a confidence outside
        [0, 1], is malformed — never writable, whatever its
        source."""
        if not self.subject.strip() or not self.relation.strip() \
                or not self.object.strip():
            return False
        return 0.0 <= self.confidence <= 1.0

    def to_dict(self):
        return {
            "subject": self.subject, "relation": self.relation,
            "object": self.object, "confidence": self.confidence,
            "source": self.source,
        }


class GlinerSeat:
    """The GLiNER2.5 seat. Facade: with no weights present,
    extraction returns available=False and zero triples."""

    def __init__(self):
        self.model = GLINER_MODEL
        self.params = GLINER_PARAMS

    def extract(self, text):
        return {
            "engine": self.model, "triples": [],
            "available": False,
            "reason": "GLiNER weights absent; no triples fabricated",
        }


class IncumbentSeat:
    """The incumbent LLM extraction seat. On this machine the
    incumbent runs through the brain path, not through this
    module — so this seat reports its availability honestly
    instead of re-implementing it: a caller supplies measured
    triples, and the seat scores them. It never invents any."""

    def __init__(self):
        self.engine = "incumbent-llm"

    def score(self, gold, predicted):
        return score_extraction(gold, predicted)


def score_extraction(gold, predicted):
    """Precision + corruption rate on the shared gold set.
    `gold` and `predicted` are iterables of Triple. Precision =
    exact-key matches / predicted; corruption = predicted triples
    that are valid-shaped but absent from gold, per written
    triplet. Empty prediction scores precision 0.0, corruption
    0.0 — never a division by zero, never a free pass."""
    gold_keys = {triple.key() for triple in gold}
    predicted = list(predicted)
    if not predicted:
        return {"precision": 0.0, "corruption_rate": 0.0,
                "predicted": 0, "matched": 0}
    matched = sum(1 for triple in predicted
                  if triple.key() in gold_keys)
    valid = [triple for triple in predicted if triple.is_valid()]
    corrupt = sum(1 for triple in valid
                  if triple.key() not in gold_keys)
    return {
        "precision": matched / len(predicted),
        "corruption_rate": corrupt / len(valid) if valid else 0.0,
        "predicted": len(predicted), "matched": matched,
    }


def gliner_bakeoff(gold, incumbent_predicted):
    """KN-2.7 verdict. The GLiNER seat has no weights here, so
    the GLiNER leg is unmeasured and the verdict is NO-DATA
    (winner None) regardless of the incumbent's score. Declaring
    a winner on one measured leg would be fabrication."""
    gliner = GlinerSeat().extract("")
    incumbent = score_extraction(gold, incumbent_predicted)
    return {
        "winner": None,
        "gliner": gliner,
        "incumbent": incumbent,
        "reason": "GLiNER leg unmeasured (weights absent); "
                  "bake-off undecided (NO-DATA)",
    }


def gate_triples(triples, threshold=CONFIDENCE_THRESHOLD):
    """The corruption control, live: split triples into writable
    and held. Invalid triples are dropped outright (never held,
    never written); valid triples at or above threshold write;
    valid triples below threshold are HELD for confirmation."""
    writable, held, dropped = [], [], []
    for triple in triples:
        if not triple.is_valid():
            dropped.append(triple.to_dict())
        elif triple.confidence >= threshold:
            writable.append(triple.to_dict())
        else:
            held.append(triple.to_dict())
    return {"writable": writable, "held": held, "dropped": dropped}


def ingest_via_generative_extractor(text):
    """REFUSED while the KN-2.7 bake-off is undecided. No engine
    may ingest through its bundled generative extractor until
    the bake-off measures that path on conversational EN/HI —
    the check the plan makes the gate. This refusal is the gate,
    not a missing feature."""
    return {
        "written": 0, "available": False,
        "reason": "generative-extractor ingestion refused: KN-2.7 "
                  "bake-off undecided (no engine measured on "
                  "conversational EN/HI); use the confidence-gated "
                  "triple path instead",
    }
