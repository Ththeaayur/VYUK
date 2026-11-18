# Part of the skill_gate split (Phase B batch B3): admit family, moved verbatim from core/skill_gate.py.
from pathlib import Path  # INTENTIONAL (skill_gate split B3): stdlib import travelling with the moved code.
import unicodedata  # INTENTIONAL (skill_gate split B3): stdlib import travelling with the moved code.
from .skill_gate_grants import evaluate_grants  # INTENTIONAL (skill_gate split B3): cross-part name from the grants module.
from .skill_gate_helpers import CLASS_HYBRID_FETCH_EXECUTE, CLASS_SCRIPT, EXECUTION_REFUSED_REASON, TYPOSQUAT_MAX_DISTANCE, WARNING_SCOPES, _CONFUSABLES, _finding, _is_blocked, _sha256_bytes  # INTENTIONAL (skill_gate split B3): cross-part name from the helpers module.
from .skill_gate_scan import scan_skill_dir  # INTENTIONAL (skill_gate split B3): cross-part name from the scan module.

def pin_skill_dir(skill_dir):
    """Record the pin for a skill directory: per-file SHA-256
    hashes plus the derived warning-level capabilities and class.
    Updates are PINNED BY DEFAULT -- a pin is the baseline every
    later version is diffed against."""
    root = Path(skill_dir)
    scan = scan_skill_dir(root)
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            try:
                files[str(path.relative_to(root))] = _sha256_bytes(
                    path.read_bytes())
            except OSError:
                pass
    return {"files": files,
            "capabilities": scan.get("derived_capabilities", []),
            "warning_capabilities": sorted(
                set(scan.get("derived_capabilities", []))
                & set(WARNING_SCOPES)),
            "class": scan.get("class"),
            "version": scan.get("version", "")}


def diff_pin(old_pin, skill_dir):
    """Diff a live directory against a pin: files added/removed/
    changed, capability gained/lost, and whether content changed
    at all. Capability delta != content delta -- both are
    reported separately."""
    new_pin = pin_skill_dir(skill_dir)
    old_files, new_files = old_pin.get("files", {}), new_pin["files"]
    changed = sorted(f for f in set(old_files) & set(new_files)
                     if old_files[f] != new_files[f])
    old_caps = set(old_pin.get("capabilities", []))
    new_caps = set(new_pin["capabilities"])
    gained = sorted(new_caps - old_caps)
    lost = sorted(old_caps - new_caps)
    return {"files_added": sorted(set(new_files) - set(old_files)),
            "files_removed": sorted(set(old_files) - set(new_files)),
            "files_changed": changed,
            "content_changed": bool(changed) or bool(
                set(new_files) ^ set(old_files)),
            "capability_gained": gained,
            "capability_lost": lost,
            "warning_capability_gained": sorted(
                set(gained) & set(WARNING_SCOPES)),
            "new_pin": new_pin}


def needs_reconsent(old_pin, skill_dir, is_security_update=False):
    """Re-consent verdict for a candidate version.

    * Gains any warning-level capability -> the version ships
      DISABLED until the user re-accepts.
    * Any content change -> a re-consent event (capability delta
      != content delta: a content change with no capability change
      still re-consents).
    * A security update with NO capability change may auto-apply
      AFTER a clean scan (blocking findings cancel auto-apply).
    """
    diff = diff_pin(old_pin, skill_dir)
    scan = scan_skill_dir(skill_dir)
    if scan.get("rejected") or _is_blocked(scan.get("findings", [])):
        return {"reconsent": True, "disabled_until_reconsent": True,
                "auto_apply": False,
                "reason": "candidate scan blocked: cannot ship "
                "until the findings are resolved",
                "diff": diff}
    if diff["warning_capability_gained"]:
        return {"reconsent": True, "disabled_until_reconsent": True,
                "auto_apply": False,
                "reason": "version gains warning-level "
                "capabilities "
                f"({', '.join(diff['warning_capability_gained'])}): "
                "ships disabled until the user re-accepts",
                "diff": diff}
    if diff["content_changed"]:
        if is_security_update and not diff["capability_gained"]:
            return {"reconsent": False, "disabled_until_reconsent": False,
                    "auto_apply": True,
                    "reason": "security update with no capability "
                    "change: may auto-apply after a clean scan",
                    "diff": diff}
        return {"reconsent": True, "disabled_until_reconsent": False,
                "auto_apply": False,
                "reason": "content changed: a re-consent event "
                "even with no capability change",
                "diff": diff}
    return {"reconsent": False, "disabled_until_reconsent": False,
            "auto_apply": False,
            "reason": "no content or capability change",
            "diff": diff}


def check_update(old_pin, candidate_dir, is_security_update=False):
    """Update CHECK, never an update APPLY: returns the diff and
    the re-consent requirement for a candidate version. Updates
    are pinned by default; this function never writes, installs,
    or enables anything (``applied`` is always False)."""
    verdict = needs_reconsent(old_pin, candidate_dir,
                              is_security_update=is_security_update)
    return {"applied": False,
            "note": "Updates are pinned by default. This check "
            "only reports what changed and whether re-consent "
            "is required; it never applies an update silently.",
            "reconsent": verdict["reconsent"],
            "disabled_until_reconsent": verdict[
                "disabled_until_reconsent"],
            "auto_apply": verdict["auto_apply"],
            "reason": verdict["reason"],
            "diff": verdict["diff"]}


# ---------------------------------------------------------------------------
# Identity / anti-typosquat (SK4-16)
# ---------------------------------------------------------------------------

def _fold_confusables(name):
    folded = "".join(_CONFUSABLES.get(ch, ch)
                     for ch in unicodedata.normalize("NFKC", name))
    return folded.casefold()


def levenshtein(a, b):
    """Plain Levenshtein distance (stdlib, no dependency)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def check_skill_name(name, existing_names):
    """Typosquat screen against a supplied set of existing names.
    Publisher identity is the SIGNATURE KEY, never the display
    name -- so a name that merely resembles an existing skill is
    a finding, not an identity. Returns a list of findings (empty
    = no resemblance flagged)."""
    findings = []
    folded = _fold_confusables(name)
    for existing in existing_names or ():
        if _fold_confusables(existing) == folded and \
                name.casefold() != existing.casefold():
            findings.append(_finding(
                name, "typosquat-confusable", "suspicious",
                f"Name folds to the same characters as existing "
                f"skill {existing!r} via confusable characters: "
                f"possible typosquat."))
        elif len(name) >= 4 and len(existing) >= 4 and \
                levenshtein(name.casefold(), existing.casefold()) \
                <= TYPOSQUAT_MAX_DISTANCE:
            findings.append(_finding(
                name, "typosquat-distance", "suspicious",
                f"Name is within edit distance "
                f"{TYPOSQUAT_MAX_DISTANCE} of existing skill "
                f"{existing!r}: possible typosquat."))
    return findings


# ---------------------------------------------------------------------------
# Admission verdict (SK4-04 + trust consultation)
# ---------------------------------------------------------------------------

def admit_skill(skill_dir, requested_grants=None, denied_grants=(),
                signed_by_vyuk=False, signing_key_id=None,
                trusted_key_ids=(), revoked_key_ids=()):
    """Full admission verdict. The gate consults the trust store
    through its parameters (the caller supplies the verdicts from
    core/skill_signing.py's trust store / kill list): an
    untrusted or revoked signing key refuses admission outright.
    Class 1/2 skills are admitted ONLY as data -- see
    ``data_only`` and ``execution_refused_reason``."""
    scan = scan_skill_dir(skill_dir)
    verdict = {"scan": scan, "admitted": False, "data_only": False,
               "execution_refused_reason": "",
               "grants": evaluate_grants(requested_grants or (),
                                         denied_grants,
                                         signed_by_vyuk=signed_by_vyuk),
               "reasons": []}
    if scan.get("rejected"):
        verdict["reasons"].append(scan["rejected"])
        return verdict
    if signing_key_id is not None and signing_key_id in revoked_key_ids:
        verdict["reasons"].append(
            "This skill's signing key is on the local kill list: "
            "it has been turned off and is not admitted.")
        return verdict
    if signed_by_vyuk and signing_key_id is not None and \
            signing_key_id not in trusted_key_ids:
        verdict["reasons"].append(
            "This skill claims a VYUK signature, but its signing "
            "key is not in the trusted store: signature-tier "
            "grants are refused and the skill is not admitted "
            "as signed.")
        return verdict
    if _is_blocked(scan.get("findings", [])):
        verdict["reasons"].append(
            "Scan findings block admission (suspicious means "
            "blocked, not warned).")
        return verdict
    verdict["admitted"] = True
    if scan["class"] in (CLASS_SCRIPT, CLASS_HYBRID_FETCH_EXECUTE):
        verdict["data_only"] = True
        verdict["execution_refused_reason"] = EXECUTION_REFUSED_REASON
    return verdict
