"""Fleet Learning track (v5.74) — FL-3 volunteered-diagnostics consent layer.

Local collect -> local redact -> exact per-section preview -> THE
USER performs the send. There is NO send function in this module,
by construction (mirror of Social's no-send rule): the bundle is
built and previewed here; sending is the user's own action in
their own client, never this code's.

Bundle spec (Troubleshoot PATTERN only — its licences are owed
beyond the pattern, so nothing is imported from it): sections in a
fixed order, each with a per-section redaction list, and an
auditable redaction log that records WHAT KIND of thing was
removed and how many, never the removed content itself.

Preview (Apport preview-UX pattern + Firefox about:support
zero-infrastructure pattern): the preview renders the EXACT bundle
the user would send, section by section, with the redaction list
attached. Preview output is byte-identical to bundle rendering;
a preview that differs from the bundle is a defect.

Consent record: per-section include/exclude chosen by the user.
Support-ID forms take ONLY user-supplied IDs: no auto-fill from
anywhere, no clipboard reading (clipboard content rules from S-6
apply: fingerprint-only, never content — so this module has no
clipboard function at all).

# REJECTED shape (FL-3-9, OBS log-upload flow): no preview, no
# redaction, bearer URL upload. Never built here.
"""

import json

SECTION_ORDER = ("system", "app", "logs", "steps")


def build_bundle(sections, redact_fn=None):
    """Build a diagnostic bundle from caller-supplied sections.

    ``sections`` is a list of {"name": str, "content": str}.
    ``redact_fn`` (optional) maps text -> {"redacted": str,
    "log": [{"type": str, "count": int}]}. Unknown section names
    are kept in caller order after the known order; the bundle
    never adds, removes, or rewrites caller content beyond the
    redaction function the caller supplied.
    """
    if not isinstance(sections, list):
        raise ValueError("sections must be a list")
    known = [s for s in sections if s.get("name") in SECTION_ORDER]
    known.sort(key=lambda s: SECTION_ORDER.index(s["name"]))
    unknown = [s for s in sections if s.get("name") not in SECTION_ORDER]
    ordered = known + unknown
    out_sections = []
    for section in ordered:
        name = section.get("name")
        content = section.get("content")
        if not isinstance(name, str) or not isinstance(content, str):
            raise ValueError("each section needs a str name and content")
        if redact_fn is not None:
            result = redact_fn(content)
            redacted = result["redacted"]
            log = result["log"]
        else:
            redacted, log = content, []
        out_sections.append({"name": name, "content": redacted,
                             "redaction_log": log})
    return {"sections": out_sections}


def render_bundle(bundle):
    """Render a bundle deterministically (the preview renderer)."""
    return json.dumps(bundle, sort_keys=True, indent=2)


def preview_bundle(bundle):
    """Preview the EXACT bundle the user would send.

    Implemented through the same renderer as render_bundle, so
    preview output is byte-identical to bundle output.
    """
    return render_bundle(bundle)


class ConsentRecord:
    """Per-section include/exclude chosen by the user.

    Default is include-all; the user excludes by naming sections.
    Nothing is excluded or included by detection, and nothing here
    sends anything.
    """

    def __init__(self, excluded=()):
        self.excluded = set(excluded)

    def exclude(self, name):
        self.excluded.add(name)

    def include(self, name):
        self.excluded.discard(name)

    def apply(self, bundle):
        """Return a bundle with only user-included sections."""
        kept = [s for s in bundle["sections"]
                if s["name"] not in self.excluded]
        return {"sections": kept}


def support_id_form(support_id):
    """Support-ID form from a USER-SUPPLIED id only.

    No auto-fill, no lookup, no clipboard read. An empty or
    non-string id is rejected rather than guessed.
    """
    if not isinstance(support_id, str) or not support_id.strip():
        raise ValueError("support_id must be a non-empty user-supplied id")
    return {"support_id": support_id.strip(),
            "note": "Supplied by the user for a support request. "
                    "Nothing is sent by this form; the user sends it."}
