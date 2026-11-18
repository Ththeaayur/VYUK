# Part of the skill_gate split (Phase B batch B3): grants family, moved verbatim from core/skill_gate.py.
from .skill_gate_helpers import SCOPES, SIGNATURE_TIER_SCOPES, _CREDENTIAL_ENV, _SCOPE_SENTENCES  # INTENTIONAL (skill_gate split B3): cross-part name from the helpers module.

def parse_grant(grant):
    """Parse one ``scope:target`` grant. Returns (scope, target)
    or (None, reason) for a malformed or unknown scope."""
    if not isinstance(grant, str) or ":" not in grant:
        return None, f"malformed grant (expected scope:target): {grant!r}"
    scope, target = grant.split(":", 1)
    scope, target = scope.strip(), target.strip()
    if scope not in SCOPES or not target:
        return None, f"unknown scope or empty target: {grant!r}"
    return (scope, target), ""


def _is_signature_tier(scope, target):
    """Signature-tier grants (SK4-02): ``run:`` always, plus
    credential-adjacent ``env:`` (a setting whose name looks like
    a token, secret, password, key, or credential)."""
    if scope in SIGNATURE_TIER_SCOPES:
        return True
    return scope == "env" and bool(_CREDENTIAL_ENV.search(target))


def evaluate_grants(requested, denied=(), signed_by_vyuk=False):
    """Deny-by-default grant evaluation. ``requested`` is the
    skill's grant list, ``denied`` an explicit deny list:
    DENY OVERRIDES ALLOW -- a grant on both lists is refused,
    never granted. Signature-tier grants requested by an
    unsigned skill are REFUSED, not silently downgraded.
    Returns {"granted": [...], "refused": [{"grant", "reason"}]}."""
    denied_norm = set()
    for grant in denied:
        parsed, _ = parse_grant(grant)
        if parsed is not None:
            denied_norm.add(f"{parsed[0]}:{parsed[1]}")
    granted, refused = [], []
    for grant in requested or ():
        parsed, reason = parse_grant(grant)
        if parsed is None:
            refused.append({"grant": str(grant), "reason": reason})
            continue
        scope, target = parsed
        norm = f"{scope}:{target}"
        if norm in denied_norm:
            refused.append({"grant": norm,
                            "reason": "explicitly denied: deny "
                            "overrides allow"})
        elif _is_signature_tier(scope, target) and not signed_by_vyuk:
            refused.append({"grant": norm,
                            "reason": "signature-tier grant: only "
                            "grantable to a VYUK-signed skill; "
                            "this skill is unsigned, so the grant "
                            "is refused, not downgraded"})
        else:
            granted.append(norm)
    return {"granted": granted, "refused": refused}


# ---------------------------------------------------------------------------
# Skill card (SK4-03) -- derived from the scan, never author text
# ---------------------------------------------------------------------------

def _sentence_for(scope, target):
    template = _SCOPE_SENTENCES.get(scope)
    if template is None:
        return f"Can use {target}"
    return template.format(target=target)


def build_skill_card(scan, declared_grants=None):
    """A human-readable summary DERIVED FROM THE SCAN. Declared
    vs derived mismatches are labelled as such; permissions are
    plain-language sentences, never raw scopes. Returns a dict
    with a ``text`` rendering plus structured fields."""
    declared = sorted(set(declared_grants if declared_grants is not None
                          else scan.get("declared_capabilities", [])))
    derived_scopes = set(scan.get("derived_capabilities", []))
    declared_scopes = set()
    for grant in declared:
        parsed, _ = parse_grant(grant)
        if parsed is not None:
            declared_scopes.add(parsed[0])
    declared_not_derived = sorted(declared_scopes - derived_scopes)
    derived_not_declared = sorted(derived_scopes - declared_scopes)
    sentences = [_sentence_for(*parse_grant(g)[0]) for g in declared
                 if parse_grant(g)[0] is not None]
    # Derived-but-undeclared capabilities are still disclosed, as
    # scan findings -- never hidden because the author omitted them.
    for scope in sorted(derived_not_declared):
        sentences.append(
            f"Found in the scan but not declared by the author: "
            f"{_sentence_for(scope, 'this skill')}")
    lines = [f"Skill class: {scan.get('class')} "
             f"({'declarative only' if scan.get('class') == 0 else 'carries code or fetches it at runtime'})",
             "This skill is default-admissible, not trusted, and "
             "not exempt from safety checks."]
    lines.extend(f"- {sentence}" for sentence in sentences)
    if declared_not_derived:
        lines.append("Declared by the author but NOT found in the "
                     f"scan: {', '.join(declared_not_derived)} "
                     f"(declared-vs-derived mismatch).")
    if derived_not_declared:
        lines.append("Found in the scan but NOT declared by the "
                     "author (declared-vs-derived mismatch).")
    if scan.get("findings"):
        blocked = [f for f in scan["findings"]
                   if f["disposition"] == "blocked"]
        lines.append(f"Scan findings: {len(scan['findings'])} "
                     f"({len(blocked)} blocking).")
    return {"text": "\n".join(lines),
            "sentences": sentences,
            "declared_not_derived": declared_not_derived,
            "derived_not_declared": derived_not_declared,
            "skill_class": scan.get("class")}
