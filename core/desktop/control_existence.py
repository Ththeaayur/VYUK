"""Control-existence verifier — one contract for "is this still there?".

Before the assistant acts on a UI target — clicks it, reads it,
types into it — something must verify that target still exists
and is still the target the assistant thinks it is. This module
is that contract (design: control_existence_verifier_contract.md,
2026-11-03; owner rulings C1–C10, 2026-11-04 evening).

One function, per-kind adapters::

    verify(target, fresh) -> {"ok", "kind", "label",
                              "resolved", "refusal"}

``target`` is a typed reference, never a bare string or a bare
coordinate pair. ``fresh`` is a live observation taken at action
time (a handle-resolution observation, a fresh menu tree, a
fresh DOM state, the last capture record). The verifier never
accepts a stored snapshot slice as ``fresh`` on its own; a stored
slice may narrow the search, never prove existence.

"Exists" per kind (contract §6.2):

* ``handle`` — an item-1 handle (window / control / mark / file)
  re-proves through ``target_handles.resolve``. Strongest-first
  identity, no title-only fallback when a stronger field
  contradicts, no nearest-match, no silent substitution.
* ``menu_item`` — the menu tree, walked live one level at a
  time from the caller's fresh tree, still contains the
  exact-named item at each level, and the tree stamp has not
  changed since listing (``menu-rebuilt`` / ``item-not-found``).
* ``browser_element`` — the element is still in the live DOM,
  still visible, and (for refs) on the same URL as the snapshot.
* ``file`` — the canonical path re-resolves and the file still
  exists. Without a minted baseline this proves existence only,
  not sameness; sameness needs the ``handle`` kind.
* ``point`` — HONESTLY UNVERIFIABLE as identity. "Exists" here
  means only: the geometry floor passes (no positive evidence
  the capture grid moved, including the C8 window-identity
  branch) AND, when the action names its source capture, the
  last capture is still that capture (C7 binding). The result
  label says ``geometry-checked`` or ``geometry-unverified``,
  never ``identity-verified``.
* ``fresh_lookup`` — Family A: the caller supplies the result
  of a live scan at action time (the lookup IS the
  verification). Exactly one unambiguous hit passes; zero
  hits or ambiguity refuses. A cached scan result must never
  be passed here — re-scan per action (C5).

Timing (C1–C5):

* C1: every action path verifies — A by lookup, B by this
  module, C by the geometry floor with an honest label.
* C2/C5: a verified target lasts ONE action only. Every ok
  result carries a one-shot token: ``token.consume()`` returns
  the resolved target once, then raises — a second action must
  re-verify, never reuse the token. Tokens are in-process
  only, never persisted.
* C3: after approval, if the target moved before the step
  runs, re-verification failure refuses the step even after
  approval. The coordinator's re-issue path (``resolve_pending``)
  re-verifies Family-C steps through this module; Family-B
  steps re-prove in their handlers at dispatch; Family-A steps
  re-scan at dispatch.
* C4: verification runs BEFORE the approval dialog wherever
  the path both verifies and asks — ``verify_before_confirm``
  is the primitive (refusal means no dialog). Residual, stated
  plainly: funnel confirm dialogs for Family-B tools still
  precede handler re-proof (the funnel has no observation
  supplier to probe before the dialog); dispatch still refuses
  on failed re-proof, so no dead-target action ever runs —
  the user may approve a target that then refuses at dispatch.
  §7 engine stops are the brief's stated exception, protected
  by C3 re-verification on re-issue.
* C6: destructive/irreversible actions get fresh region
  re-observation FIRST. A destructive ``point`` action MUST
  carry a stored crop and a fresh crop, compared by
  ``compare_region`` (below); without crops it refuses with
  ``region-unobserved``. A destructive action of another kind
  with a stored crop must also pass the region compare; one
  without a crop proceeds on its live re-proof (the fresh
  UIA walk / menu walk / DOM lookup IS the fresh look).

C7 "Verify the spot, not the snapshot" — region compare
(threshold decision, 2026-11-05): a bare-coordinate action
carries the pixels it was read from (a small region around
the target as its source capture showed it); at act time the
caller re-captures just that region and ``compare_region``
compares structurally. Match requires mean absolute
difference <= 12 (grayscale, 0–255) AND at most 10% of pixels
differing by more than 48, after both grids are normalised
to at most 48 px per side. That tolerates small local
animation (blinking caret/cursor, shimmer: few pixels move
a lot, or many pixels move a little) and refuses everything
else — including full-motion video regions, which change
most pixels a lot. A video region therefore refuses honestly
and must use an identity path; the threshold never pretends
a moving picture is a match. The approval dialog shows the
stored crop (a GUI concern, not this module's).

C8: the norm1000 geometry floor compares window identity
(hwnd first, title fallback) for foreground crops; the vision
record carries foreground identity at capture time
(``capture_id`` / ``foreground_hwnd`` / ``foreground_title``
in ``last_capture_info``). Same-rectangle swaps are refused
when identity is known on both sides, skipped (never
refused) when identity is unknown on either side.

C9 full-shape failures: every refusal carries ``code`` (the
precise per-path code), ``reason`` (naming what changed),
``last_known`` (the last-known description), and
``instruction`` (``REOBSERVE_INSTRUCTION``), plus ``text``
combining them. Refusals land in the step's verification
block (receipt + ledger ``verification`` key + Activity —
the existing ``task.verification`` list, NOT a second key;
the contract's proposed ``verifications`` name differs from
the tree's existing key, and duplicating it would split the
story in two).

C10 proof: lab proves refusal logic here (one test per
adapter: stale refuses, fresh passes, plus the approval-
pause and capture-binding sequences). Real-Windows field
legs per path stay owed — listed in the batch report, never
claimed.

What this module never does: substitute (no nearest-match,
no fuzzy re-pick, no title-only fallback on contradiction,
no re-derived menu for a context invoke); cache "verified"
across steps or restarts (handles are session-scoped);
verify against its own stored snapshot; bypass or weaken a
confirm gate, a §7 stop, or the second-opinion trigger
(verification passing is not permission; permission passing
is not verification); or invent a refusal when validation
cannot run (unreadable world -> ``geometry-unverified``
label, not a fake refusal) — or invent a pass either
(absence of a refusal is not a certificate; the label says
which).

This module never touches the screen, the funnel, or the
gate stack: adapters take explicit observations, so every
rule is drivable headless. It never consults PF-18 grants
(permission is not existence) and it binds no tools — the
registry count is untouched.
"""

import os

from .target_handles import REOBSERVE_INSTRUCTION
from .target_handles import get_handle as _get_handle
from .target_handles import resolve as _resolve_handle

__all__ = [
    "REOBSERVE_INSTRUCTION",
    "FAMILY_A_TOOLS",
    "FAMILY_B_TOOLS",
    "FAMILY_C_TOOLS",
    "family_of",
    "refusal",
    "normalise_grid",
    "compare_region",
    "MEAN_ABS_DIFF_MAX",
    "LARGE_DIFF",
    "FRAC_LARGE_MAX",
    "verify",
    "verify_for_action",
    "verify_before_confirm",
]

# ---------------------------------------------------------------------------
# Families (contract §3; tool names verified against the catalogue)
# ---------------------------------------------------------------------------

#: Family A — fresh-lookup paths: existence proven by the lookup
#: itself. The lookup IS the verification; the adapter below is
#: the contract's enforcement point, and caching a Family-A
#: lookup across steps without re-scanning is forbidden (C5).
FAMILY_A_TOOLS = frozenset({
    "uia_click_control",
    "click_element",
    "click_text",
})

#: Family B — handle-resolved paths: existence proven by
#: re-proof through target_handles.resolve (plus the menu /
#: browser / file adapters, which share the live re-proof shape).
FAMILY_B_TOOLS = frozenset({
    "menu_action",
    "file_dialog_action",
    "system_surface_action",
    "drag_drop_action",
    "context_menu_action",
    "click_by_mark",
})

#: Family C — bare-observation paths: existence NOT proven, only
#: the geometry floor + C7 binding + (for destructive actions)
#: region compare. Receipts label these geometry-checked, never
#: identity-verified.
FAMILY_C_TOOLS = frozenset({
    "cursor_control",
    "mouse_control",
    "window_control",
})


def family_of(tool):
    """'A', 'B', 'C', or 'unknown' for a tool name.

    'unknown' is never guessed into a family: an unclassified
    tool gets no verifier shortcut, it gets classified first."""
    name = str(tool or "").strip()
    if name in FAMILY_A_TOOLS:
        return "A"
    if name in FAMILY_B_TOOLS:
        return "B"
    if name in FAMILY_C_TOOLS:
        return "C"
    return "unknown"


# ---------------------------------------------------------------------------
# Refusals — C9 full shape
# ---------------------------------------------------------------------------


def refusal(code, reason, last_known=""):
    """A verifier refusal: code + what changed + last-known
    description + the re-observe instruction, plus ``text``
    combining them. Never a bare string: a refusal that only
    appears in a handler's return string is not a story."""
    reason = str(reason or "").strip()
    last_known = str(last_known or "").strip()
    text = reason
    if last_known:
        text += " Last known: %s." % last_known
    text += " " + REOBSERVE_INSTRUCTION
    return {"code": code, "reason": reason, "last_known": last_known,
            "instruction": REOBSERVE_INSTRUCTION, "text": text}


def _strip_instruction(reason):
    """Drop a trailing re-observe instruction (target_handles
    refusals already carry it) so the full shape never doubles
    the instruction."""
    reason = str(reason or "")
    suffix = " " + REOBSERVE_INSTRUCTION
    if reason.endswith(suffix):
        reason = reason[:-len(suffix)]
    return reason.strip()


# ---------------------------------------------------------------------------
# One-action tokens — C2/C5
# ---------------------------------------------------------------------------


class _VerifiedToken:
    """One-shot proof of a verification (C2/C5).

    ``consume()`` returns the resolved target exactly once;
    a second call raises — the next action must re-verify,
    never reuse this token. In-process only, never persisted
    (receipts and ledger carry the verdict, never the token)."""

    def __init__(self, resolved):
        self._resolved = dict(resolved)
        self._consumed = False

    def consume(self):
        if self._consumed:
            raise ValueError(
                "verification already consumed — re-verify for "
                "the next action (a verified target lasts one "
                "action only).")
        self._consumed = True
        return dict(self._resolved)

    @property
    def consumed(self):
        return self._consumed


# ---------------------------------------------------------------------------
# Region compare — C7 (threshold decision, see module docstring)
# ---------------------------------------------------------------------------

#: C7 structural-match thresholds (grayscale 0–255, after
#: normalisation to at most 48 px per side): mean absolute
#: difference at most 12, and at most 10% of pixels differing
#: by more than 48. Small local animation passes; full-motion
#: video refuses honestly.
MEAN_ABS_DIFF_MAX = 12.0
LARGE_DIFF = 48
FRAC_LARGE_MAX = 0.10
_REGION_MAX_SIDE = 48


def normalise_grid(grid, max_side=_REGION_MAX_SIDE):
    """Downscale a grayscale grid (rows of ints) to at most
    ``max_side`` px per side by block averaging. Returns a list
    of rows of ints, or None when the grid is unusable (empty,
    ragged, or non-numeric) — unusable grids never compare as
    a match."""
    try:
        rows = [[int(v) for v in row] for row in (grid or [])]
    except (TypeError, ValueError):
        return None
    if not rows:
        return None
    width = len(rows[0])
    if width == 0 or any(len(row) != width for row in rows):
        return None
    height = len(rows)
    if width <= max_side and height <= max_side:
        return rows
    block_x = (width + max_side - 1) // max_side
    block_y = (height + max_side - 1) // max_side
    out = []
    for by in range(0, height, block_y):
        out_row = []
        for bx in range(0, width, block_x):
            total = 0
            count = 0
            for y in range(by, min(by + block_y, height)):
                for x in range(bx, min(bx + block_x, width)):
                    total += rows[y][x]
                    count += 1
            out_row.append(total // count if count else 0)
        out.append(out_row)
    return out


def compare_region(stored, fresh):
    """Structural comparison of two grayscale grids.

    Returns {"match", "mean_abs_diff", "frac_large", "reason"}.
    Dimension mismatch after normalisation, and any unusable
    grid, is a mismatch — never a crash, never a pass."""
    a = normalise_grid(stored)
    b = normalise_grid(fresh)
    if a is None or b is None:
        return {"match": False, "mean_abs_diff": None,
                "frac_large": None,
                "reason": "a region crop is missing or unusable — "
                          "the spot cannot be compared."}
    if len(a) != len(b) or len(a[0]) != len(b[0]):
        return {"match": False, "mean_abs_diff": None,
                "frac_large": None,
                "reason": "region dimensions differ after "
                          "normalisation — the spot changed shape."}
    total = 0
    large = 0
    count = 0
    for row_a, row_b in zip(a, b):
        for va, vb in zip(row_a, row_b):
            diff = abs(va - vb)
            total += diff
            if diff > LARGE_DIFF:
                large += 1
            count += 1
    mean = total / count if count else 0.0
    frac = large / count if count else 0.0
    if mean <= MEAN_ABS_DIFF_MAX and frac <= FRAC_LARGE_MAX:
        return {"match": True, "mean_abs_diff": mean,
                "frac_large": frac,
                "reason": "region matches within the animation "
                          "tolerance (mean diff %.1f, %.0f%% of "
                          "pixels changed a lot)." % (mean, frac * 100)}
    return {"match": False, "mean_abs_diff": mean,
            "frac_large": frac,
            "reason": "region changed (mean diff %.1f exceeds "
                      "%.1f, or %.0f%% of pixels changed a lot "
                      "exceeds %.0f%%) — the spot is not the "
                      "spot that was consented to."
                      % (mean, MEAN_ABS_DIFF_MAX, frac * 100,
                         FRAC_LARGE_MAX * 100)}


# ---------------------------------------------------------------------------
# Adapters — one per kind (contract §6.1)
# ---------------------------------------------------------------------------


def _verify_handle(target, fresh):
    """Family B core: re-prove an item-1 handle."""
    handle_id = str(target.get("handle_id") or "").strip()
    if not handle_id:
        return None, refusal(
            "unknown-handle",
            "no target handle id was supplied, so nothing can "
            "be re-proven.",
            target.get("description", ""))
    observation = (fresh or {}).get("observation") or {}
    result = _resolve_handle(handle_id, observation)
    if result.get("ok"):
        resolved = dict(result.get("resolved") or {})
        resolved["description"] = resolved.get(
            "description") or target.get("description", "")
        return resolved, None
    inner = result.get("refusal") or {}
    last_known = target.get("description", "")
    if not last_known:
        record = _get_handle(handle_id) or {}
        last_known = record.get("description", "")
    return None, refusal(
        inner.get("code") or "resolution-error",
        _strip_instruction(inner.get("reason") or
                           "target-handle resolution refused."),
        last_known)


def _verify_menu_item(target, fresh):
    """Menu item: live walk, exact name per level, stamp check."""
    path = target.get("path") or []
    if isinstance(path, str):
        path = [path]
    path = [str(s or "").strip() for s in path]
    path = [s for s in path if s]
    if not path:
        return None, refusal(
            "item-not-found",
            "no menu path was supplied to walk.",
            target.get("description", ""))
    tree = (fresh or {}).get("tree")
    if not isinstance(tree, list):
        return None, refusal(
            "menu-unavailable",
            "no fresh menu tree was supplied — a menu item "
            "cannot be verified against a stored listing.",
            target.get("description", ""))
    listed_stamp = (fresh or {}).get("listed_stamp")
    tree_stamp = (fresh or {}).get("tree_stamp")
    if (listed_stamp is not None and tree_stamp is not None
            and listed_stamp != tree_stamp):
        return None, refusal(
            "menu-rebuilt",
            "the menu tree changed since listing (stamp %r, "
            "now %r) — the listed item may no longer mean "
            "the same entry." % (listed_stamp, tree_stamp),
            target.get("description", ""))
    for level, name in enumerate(path):
        if level >= len(tree):
            return None, refusal(
                "item-not-found",
                "menu level %d is missing from the fresh tree "
                "— the path %s cannot be walked."
                % (level, " > ".join(path)),
                target.get("description", ""))
        siblings = tree[level] or []
        if name not in [str(s) for s in siblings]:
            return None, refusal(
                "item-not-found",
                "menu item %r is not in the fresh tree at "
                "level %d (siblings there: %s) — nothing "
                "similar is substituted."
                % (name, level, ", ".join(str(s) for s in siblings)
                   or "(none)"),
                target.get("description", ""))
    return {"path": path,
            "description": target.get("description", "")}, None


def _verify_browser_element(target, fresh):
    """Browser element: live DOM state, same URL for refs."""
    fresh = fresh or {}
    if not fresh.get("present", False):
        return None, refusal(
            "target-gone",
            "the browser element is no longer in the live DOM.",
            target.get("description", ""))
    if not fresh.get("visible", False):
        return None, refusal(
            "target-unusable",
            "the browser element is present but not visible.",
            target.get("description", ""))
    snapshot_url = target.get("snapshot_url")
    live_url = fresh.get("url")
    if (snapshot_url and live_url
            and str(snapshot_url) != str(live_url)):
        return None, refusal(
            "url-changed",
            "the page URL changed since the snapshot (%r, now "
            "%r) — element references from the old page may "
            "point at the wrong elements."
            % (snapshot_url, live_url),
            target.get("description", ""))
    resolved = {"description": target.get("description", "")}
    if target.get("selector") is not None:
        resolved["selector"] = target.get("selector")
    if target.get("ref") is not None:
        resolved["ref"] = target.get("ref")
    if live_url is not None:
        resolved["url"] = live_url
    return resolved, None


def _verify_file(target, fresh):
    """File: canonical path re-resolves and the file exists.

    Existence only, not sameness — a bare path carries no
    minted baseline to compare against, so a symlink swap
    cannot be detected here. Sameness needs the handle kind."""
    path = str(target.get("path") or "").strip()
    if not path:
        return None, refusal(
            "invalid-target",
            "no file path was supplied to verify.",
            target.get("description", ""))
    canonical = os.path.realpath(os.path.abspath(path))
    if not os.path.exists(canonical):
        return None, refusal(
            "target-gone",
            "the file '%s' no longer exists." % canonical,
            target.get("description", ""))
    return {"path": canonical,
            "description": target.get("description", "")}, None


def _verify_point(target, fresh):
    """Family C point: C7 capture binding + geometry floor.

    Order: binding first (a wrong baseline explains a floor
    failure), then the floor (positive evidence only — an
    unreadable world passes through with the unverified
    label, never a fake refusal). Region compare for
    destructive actions lives in verify(), not here."""
    fresh = fresh or {}
    capture_info = fresh.get("capture_info")
    if not isinstance(capture_info, dict):
        capture_info = None
    source_id = target.get("source_capture_id")
    last_id = (capture_info or {}).get("capture_id")
    if (source_id is not None and last_id is not None
            and str(source_id) != str(last_id)):
        return None, refusal(
            "capture-mismatch",
            "these coordinates were read off capture %r, but "
            "the last capture is %r — the grid changed "
            "between reading and acting, so the coordinates "
            "no longer point at what was seen."
            % (source_id, last_id),
            target.get("description", ""))
    try:
        from .cursor_geometry import _capture_freshness_verdict
        code, message = _capture_freshness_verdict()
    except Exception:  # noqa: BLE001 — floor that cannot run
        code, message = None, None  # never blocks on its own error
    if code is not None:
        return None, refusal(
            code, _strip_instruction(message or
                                     "the capture grid moved."),
            target.get("description", ""))
    label = ("geometry-checked" if capture_info is not None
             else "geometry-unverified")
    return {"label": label, "capture_id": last_id,
            "description": target.get("description", "")}, None


def _verify_fresh_lookup(target, fresh):
    """Family A: the live scan result IS the verification.

    Exactly one unambiguous hit passes. Zero hits, or an
    ambiguous scan, refuses — the caller must re-scan, never
    act on a cached scan (C5)."""
    fresh = fresh or {}
    candidates = fresh.get("candidates") or []
    if not candidates:
        return None, refusal(
            "target-not-present",
            "the fresh lookup found no target.",
            target.get("description", ""))
    names = []
    for cand in candidates:
        if isinstance(cand, dict):
            names.append(str(cand.get("name") or ""))
    if fresh.get("ambiguous") or len(candidates) > 1:
        return None, refusal(
            "ambiguous-target",
            "the fresh lookup found %d candidates (%s) — "
            "nothing is clicked until exactly one wins."
            % (len(candidates), ", ".join(n for n in names if n)
               or "(unnamed)"),
            target.get("description", ""))
    only = candidates[0] if isinstance(candidates[0], dict) else {}
    return {"name": only.get("name", ""),
            "rect": only.get("rect"),
            "description": target.get("description", "")}, None


_ADAPTERS = {
    "handle": _verify_handle,
    "menu_item": _verify_menu_item,
    "browser_element": _verify_browser_element,
    "file": _verify_file,
    "point": _verify_point,
    "fresh_lookup": _verify_fresh_lookup,
}


# ---------------------------------------------------------------------------
# The verifier
# ---------------------------------------------------------------------------


def verify(target, fresh=None):
    """Verify one typed target against a fresh observation.

    Returns {"ok", "kind", "label", "resolved", "refusal"}.
    ``label`` is "identity-verified" (handle / menu / browser /
    file / fresh-lookup) or "geometry-checked" /
    "geometry-unverified" (point). On ok, ``resolved`` carries
    a one-shot ``token`` (C2/C5). Never raises on bad input:
    a malformed target is a refusal, not an exception."""
    try:
        if not isinstance(target, dict):
            return {"ok": False, "kind": None,
                    "label": "refused", "resolved": None,
                    "refusal": refusal(
                        "invalid-target",
                        "the target to verify is not a typed "
                        "reference.")}
        kind = str(target.get("kind") or "").strip()
        adapter = _ADAPTERS.get(kind)
        if adapter is None:
            return {"ok": False, "kind": kind or None,
                    "label": "refused", "resolved": None,
                    "refusal": refusal(
                        "unknown-kind",
                        "the target kind %r is not one this "
                        "verifier resolves." % kind,
                        target.get("description", ""))}
        resolved, failure = adapter(target, fresh or {})
        if failure is not None:
            return {"ok": False, "kind": kind, "label": "refused",
                    "resolved": None, "refusal": failure}
        # C6 region gate (destructive actions): a stored crop
        # must pass the region compare against a fresh crop.
        # A destructive point has no identity to fall back on,
        # so missing crops refuse; any other destructive kind
        # without a crop proceeds on its live re-proof above
        # (the fresh walk / lookup IS the fresh look).
        if target.get("destructive"):
            stored_crop = target.get("stored_crop")
            if kind == "point" or stored_crop is not None:
                fresh_crop = (fresh or {}).get("fresh_crop")
                if stored_crop is None or fresh_crop is None:
                    return {"ok": False, "kind": kind,
                            "label": "refused", "resolved": None,
                            "refusal": refusal(
                                "region-unobserved",
                                "this destructive action needs a "
                                "fresh region re-observation, but "
                                "a stored or fresh crop is "
                                "missing — the spot cannot be "
                                "re-proven.",
                                target.get("description", ""))}
                compared = compare_region(stored_crop, fresh_crop)
                if not compared["match"]:
                    return {"ok": False, "kind": kind,
                            "label": "refused", "resolved": None,
                            "refusal": refusal(
                                "region-changed",
                                compared["reason"],
                                target.get("description", ""))}
                resolved["region"] = {
                    "mean_abs_diff": compared["mean_abs_diff"],
                    "frac_large": compared["frac_large"]}
        label = ("identity-verified" if kind != "point"
                 else resolved.get("label", "geometry-checked"))
        resolved["token"] = _VerifiedToken(resolved)
        return {"ok": True, "kind": kind, "label": label,
                "resolved": resolved, "refusal": None}
    except Exception as exc:  # noqa: BLE001 — verify never raises
        return {"ok": False,
                "kind": (target.get("kind") if isinstance(target, dict)
                         else None),
                "label": "refused", "resolved": None,
                "refusal": refusal(
                    "resolution-error",
                    "target verification failed (%s)." % exc,
                    target.get("description", "")
                    if isinstance(target, dict) else "")}


def verify_for_action(target, fresh=None, destructive=False):
    """verify() for an action about to run: ``destructive``
    arms the C6 region gate (fresh region re-observation for
    destructive/irreversible actions first; cheap actions keep
    identity-only verification). The caller's target dict is
    never mutated."""
    copied = dict(target) if isinstance(target, dict) else target
    if destructive and isinstance(copied, dict):
        copied["destructive"] = True
    return verify(copied, fresh)


def verify_before_confirm(target, fresh=None):
    """C4 timing primitive: (proceed, refusal-or-None).

    Callers that HAVE a fresh observation call this BEFORE
    showing an approval dialog; a refusal means no dialog is
    shown — the user never approves a dead target. Callers
    without a fresh observation cannot use this primitive
    (it never invents one); for them the C3 re-verification
    on re-issue is the protection."""
    result = verify(target, fresh)
    if result["ok"]:
        return True, None
    return False, result["refusal"]
