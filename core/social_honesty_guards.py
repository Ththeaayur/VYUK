"""Social track (v5.73) — CP-4 honesty guards: opinion/fact
separation (CP-4.7), sycophancy lint (CP-4.2), uncertainty
marking (CP-4.3).

CP-4.7, stated precisely: Wave 6 already landed the
opinion/fact separation for the vault (people kind=
opinion never renders in the fact prompt via dual_read).
This module provides the same guard as a reusable library
rule for NEW surfaces: facts and opinions are separate
lists, and the fact renderer REFUSES opinion records rather
than filtering them silently — a silent filter is a bug
waiting to re-admit opinions as facts.

CP-4.2: a heuristic LINT, not a judge. It flags
approval-maximising shapes (agreement without reasoning,
praise without specifics, verbatim mirroring) for a human
or a test to look at. It never certifies a response
"sincere".

CP-4.3: uncertainty must be STATED, never implied. Claims
carry a confidence band, and the renderer prefixes it
honestly. "I don't know" is a complete, valid answer.

No WAI/SPR verbatim material and no SycophancyEval items
appear here or in the tests: all fixtures are re-authored
original sentences.
"""

AGREEMENT_PHRASES = (
    "you're absolutely right", "you are absolutely right",
    "you're so right", "totally agree", "couldn't agree more",
    "great point", "brilliant idea",
)
PRAISE_PHRASES = (
    "you're amazing", "you are amazing", "you're brilliant",
    "you are brilliant", "so smart", "genius move",
)
EVIDENCE_MARKERS = ("because", "for example", "the reason",
                    "evidence", "however", "on the other hand")


def separate_kinds(records):
    """Split records into (facts, opinions) by their 'kind'
    field. Unknown kinds raise — an unclassified statement is
    neither fact nor opinion, and must not default to fact."""
    facts, opinions = [], []
    for record in records:
        kind = record.get("kind")
        if kind == "fact":
            facts.append(record)
        elif kind == "opinion":
            opinions.append(record)
        else:
            raise ValueError(
                f"record kind must be 'fact' or 'opinion', "
                f"got {kind!r} — refusing to default to fact")
    return facts, opinions


def render_fact_prompt(records):
    """Render fact records into prompt text. REFUSES any list
    containing an opinion record — callers must separate
    first. Opinions are retrievable only via an explicit
    kind-filtered query (separate_kinds), never mixed in."""
    for record in records:
        if record.get("kind") != "fact":
            raise ValueError(
                "render_fact_prompt refuses non-fact records: "
                "separate opinions first (CP-4.7)")
    return "\n".join(f"- {r.get('value', '')}" for r in records)


def sycophancy_lint(response, user_claim=""):
    """Heuristic lint flags for approval-maximising shapes.
    Returns a list of flag strings (empty = no shape found).
    A flag is a prompt to look, not a verdict."""
    text = str(response or "").lower()
    flags = []
    words = text.split()
    agreement_hits = [p for p in AGREEMENT_PHRASES if p in text]
    praise_hits = [p for p in PRAISE_PHRASES if p in text]
    has_evidence = any(m in text for m in EVIDENCE_MARKERS)
    if agreement_hits and not has_evidence and len(words) < 40:
        flags.append(
            "agreement without reasoning: " + ", ".join(agreement_hits))
    if praise_hits and len(words) < 40 and not has_evidence:
        flags.append(
            "praise without specifics: " + ", ".join(praise_hits))
    if user_claim and user_claim.lower().strip() in text \
            and len(words) < 25:
        flags.append("verbatim mirroring of the user's claim")
    return flags


def honest_prefix(confidence):
    """Prefix for a claim by confidence band. Confidence must
    be stated: 'high' | 'medium' | 'low' | 'unknown'. Anything
    else raises — an unstated confidence must not render as
    certainty."""
    if confidence == "high":
        return "I know: "
    if confidence == "medium":
        return "I think: "
    if confidence == "low":
        return "I'm not sure, but: "
    if confidence == "unknown":
        return "I don't know: "
    raise ValueError(
        f"confidence must be high/medium/low/unknown, got "
        f"{confidence!r}")


def mark_claim(claim, confidence):
    """Render a claim with its honest confidence prefix."""
    return honest_prefix(confidence) + str(claim)
