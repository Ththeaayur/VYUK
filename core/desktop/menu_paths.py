"""Menus / toolbars — §7 item 3 (parent draft).

Menus and toolbars become addressable controls, not pixel hunts:
open, traverse, and invoke them through the accessibility tree.
A menu target is a stable item-1 handle plus a path::

    {window_handle, path: ["File", "Save As"]}

Rules (from the §7 item-3 definition, inheriting §0 + items 1–2):

* Resolve through handles, never around them. The window resolves
  through ``core.desktop.target_handles`` (minted from the observation's
  identity when the caller passes no handle id); every walked item
  carrying a UIA automation id is minted as an item-1 control
  handle and resolved against the freshly-expanded parent before
  the walk continues. Items without an automation id are matched
  by exact name inside the freshly-expanded parent only — never
  by fuzzy match, never by position, never by memorised
  coordinates. The engine never reads a rectangle.
* Observe once, share the snapshot. Every call is anchored to ONE
  ``core.desktop.perception_adapter`` snapshot (passed in, or taken once
  here via :func:`core.desktop.perception_adapter.observe`). The menu
  backend's current foreground must still match that snapshot
  (``perception_adapter.is_stale``: hwnd-first, title fallback,
  unreadable never counts as a change) or the call refuses —
  the engine adds NO second observation path of its own.
* Refuse, never guess. A disabled item refuses naming the item
  and its state (never silently skips to another entry); an
  ambiguous path refuses listing every match (never picks the
  first); a menu rebuilt between observation and invoke refuses
  on a tree-stamp change (never resumes mid-path blindly, never
  invokes a look-alike); an item whose text changed refuses with
  the current siblings listed (never fuzzy-matches to a
  different command); an unexposed menu refuses naming the
  manual alternative.
* Survival. A path survives re-renders and moves: the window
  handle re-proves on hwnd when the title edits, and an item
  re-proves on automation id when its pixels move. A window
  replaced under the same title refuses via the item-1 engine.
* C-14 approve-first still governs. A leaf whose name marks it
  as a send / delete / purchase-class action (see
  :data:`DESTRUCTIVE_LEAF_WORDS`) refuses until the caller
  passes ``confirmed=True``; a caller-declared ``effectful=True``
  does the same for any leaf. The engine grants no permission —
  it only refuses without one.
* By-design walls are never bypassed: menus of the secure
  desktop are out of scope, and the engine never synthesises
  keystrokes to fake a menu the tree does not expose.

Backends: :func:`invoke_path` / :func:`list_menu` take an optional
``backend`` implementing the menu protocol (``foreground_identity``,
``bounds``, ``menu_tree``, ``expand``, ``invoke``). The default
backend wires the tree's existing UIA sources with lazy imports,
so importing this module needs only the standard library. Tests
drive the protocol with a fake backend; live Windows behaviour —
real menu bars, real ExpandCollapse / Invoke patterns, real
consequence verification — stays field work and is not claimed
here. Consequence verification is honest by default: unless a
backend reports ``verified=True`` from its invoke, the result
says ``invoked-without-verification``.

Deliberately NOT built here (named follow-ups, not silent gaps):
no funnel tool registration (the registry count and the G2
snapshot are untouched by this build), no coordinator consumer,
no consequence verifier beyond the backend's own flag, and no
context-menu trigger (menu-bar roots only — a context trigger
needs its own consented open step).
"""

import hashlib
import json

__all__ = [
    "parse_path",
    "list_menu",
    "invoke_path",
    "is_destructive_leaf",
    "describe",
    "default_backend",
    "REOBSERVE_INSTRUCTION",
    "DESTRUCTIVE_LEAF_WORDS",
]

REOBSERVE_INSTRUCTION = (
    "Re-observe (take_screenshot or screen_look) and name the "
    "menu path again."
)

#: First-word markers (lowercased, accelerator stripped) for leaves
#: that need C-14 confirmation before invoke. A small explicit set
#: on purpose: the engine refuses to guess at a leaf it cannot
#: classify, and a caller that knows better declares
#: ``effectful=True`` explicitly. "Save" is deliberately absent —
#: saving is routine, and the funnel's own gate stack covers the
#: effectful tools a menu leaf could reach.
DESTRUCTIVE_LEAF_WORDS = frozenset({
    "send", "delete", "remove", "purchase", "buy", "pay",
    "subscribe", "format", "erase", "uninstall", "discard",
})

_INTERNAL_NODE_KEYS = frozenset({"key"})


# ---------------------------------------------------------------------------
# Path parsing + naming
# ---------------------------------------------------------------------------


def _clean_name(name) -> str:
    """A menu name for comparison: stripped, UIA accelerator '&'
    removed. Nothing else is normalised — "Save As" never matches
    "Save As..." and "Save" never matches "Save As"."""
    return str(name or "").replace("&", "").strip()


def parse_path(path):
    """Segments for a menu path: a list/tuple passes through
    (stripped, empties dropped); a string splits on "→" or ">".
    Returns [] for an empty path (callers refuse it, never walk
    it)."""
    if isinstance(path, (list, tuple)):
        return [str(seg).strip() for seg in path
                if str(seg).strip()]
    text = str(path or "").strip()
    if not text:
        return []
    if "→" in text:
        parts = text.split("→")
    elif ">" in text:
        parts = text.split(">")
    else:
        parts = [text]
    return [part.strip() for part in parts if part.strip()]


def is_destructive_leaf(name) -> bool:
    """True when a leaf name starts with a destructive marker
    (first word, accelerator stripped, lowercased)."""
    cleaned = _clean_name(name).lower()
    if not cleaned:
        return False
    first = cleaned.split(None, 1)[0].strip(".,:;")
    return first in DESTRUCTIVE_LEAF_WORDS


def _project_node(node) -> dict:
    """The public face of a menu node: name, type, state — never
    a rectangle, never a backend-private key."""
    children = node.get("children") if isinstance(node, dict) else None
    return {
        "name": _clean_name(node.get("name")),
        "control_type": str(node.get("control_type") or "MenuItem"),
        "automation_id": node.get("automation_id"),
        "enabled": node.get("enabled"),
        "checked": node.get("checked"),
        "has_children": bool(children) if children is not None else None,
    }


def _refusal(code, reason, instruction=REOBSERVE_INSTRUCTION) -> dict:
    if instruction:
        reason = reason + " " + instruction
    return {"code": code, "reason": reason}


def _fail(code, reason, path, window_handle_id, snapshot_stamp,
          instruction=REOBSERVE_INSTRUCTION):
    return {"ok": False, "path": list(path),
            "action": None, "invoked": False, "opened": False,
            "window_handle_id": window_handle_id,
            "item_handle_ids": [], "snapshot_stamp": snapshot_stamp,
            "tree_stamp": None, "items": [], "verification": None,
            "refusal": _refusal(code, reason, instruction)}


def describe(result) -> str:
    """One line for receipts / activity records."""
    if not isinstance(result, dict):
        return "no menu result"
    path = " → ".join(result.get("path") or [])
    if result.get("ok"):
        verb = "opened" if result.get("opened") else "invoked"
        line = "Menu path %s %s" % (path, verb)
        handle = result.get("window_handle_id")
        if handle:
            line += " (window handle %s)" % handle
        verification = result.get("verification")
        if verification:
            line += " — %s" % verification
        return line
    refusal = result.get("refusal") or {}
    return "Menu path %s refused: %s" % (
        path, refusal.get("reason") or "no reason given")


# ---------------------------------------------------------------------------
# Shared call setup: one snapshot, one window handle
# ---------------------------------------------------------------------------


def _prepare(snapshot, backend, window_handle_id):
    """(snapshot, backend, window_handle_id, stamp, error-result).

    One observation anchors the call; the backend's current
    foreground must still match it; the window resolves through
    the item-1 engine. On any failure the error result is
    returned and the caller must not walk."""
    from . import perception_adapter as _pa
    from . import target_handles as _th

    if snapshot is None:
        snapshot = _pa.observe()
    if not isinstance(snapshot, dict):
        return None, None, None, None, _fail(
            "snapshot-unavailable",
            "no usable observation anchors this menu action.",
            [], window_handle_id, None)
    stamp = snapshot.get("stamp")
    status = snapshot.get("capture_status")
    if status not in (None, "ok"):
        return None, None, None, stamp, _fail(
            "snapshot-unavailable",
            "the observation was not a successful capture (%s: %s) "
            "— no menu is walked from a failed or denied capture."
            % (status, snapshot.get("capture_reason") or "no reason"),
            [], window_handle_id, stamp)
    if backend is None:
        backend = default_backend()
    try:
        current_fg = backend.foreground_identity() or {}
    except Exception:  # noqa: BLE001 — unobservable foreground
        current_fg = {}
    try:
        bounds = backend.bounds()
    except Exception:  # noqa: BLE001
        bounds = None
    stale = _pa.is_stale(snapshot, current=(current_fg, bounds))
    if stale is not None:
        return None, None, None, stamp, _fail(
            "snapshot-stale",
            "the observation is stale: %s" % stale,
            [], window_handle_id, stamp)
    snapshot_identity = _pa.snapshot_window_identity(snapshot)
    current_identity = {field: current_fg[field] for field in
                        ("hwnd", "pid", "title")
                        if current_fg.get(field) is not None}
    resolve_identity = current_identity or snapshot_identity
    if window_handle_id is not None:
        resolved = _th.resolve(window_handle_id,
                               {"identity": resolve_identity})
        if not resolved.get("ok"):
            inner = (resolved.get("refusal") or {}).get("reason") or \
                "window-handle resolution refused."
            return None, None, None, stamp, _fail(
                "window-handle-refused",
                "the consented window cannot be re-proven: %s" % inner,
                [], window_handle_id, stamp,
                instruction="")  # inner reason already instructs
    else:
        try:
            minted = _th.mint_window(snapshot_identity)
        except ValueError as exc:
            return None, None, None, stamp, _fail(
                "window-identity-missing",
                "no window identity can be proven from this "
                "observation (%s)." % exc,
                [], window_handle_id, stamp)
        window_handle_id = minted["id"]
        resolved = _th.resolve(window_handle_id,
                               {"identity": resolve_identity})
        if not resolved.get("ok"):
            inner = (resolved.get("refusal") or {}).get("reason") or \
                "window-handle resolution refused."
            return None, None, None, stamp, _fail(
                "window-handle-refused",
                "the freshly-minted window handle cannot be "
                "re-proven: %s" % inner,
                [], window_handle_id, stamp,
                instruction="")
    return snapshot, backend, window_handle_id, stamp, None


def _read_tree(backend):
    """(items, stamp, note) from a backend, never raising."""
    try:
        result = backend.menu_tree()
    except Exception as exc:  # noqa: BLE001 — backend failure is a refusal
        return None, None, "menu tree read failed: %s" % exc
    if isinstance(result, tuple) and len(result) == 3:
        items, stamp, note = result
    elif isinstance(result, tuple) and len(result) == 2:
        items, stamp = result
        note = ""
    else:
        items, stamp, note = result, None, ""
    if not isinstance(items, list):
        return None, None, "menu tree returned no item list"
    return items, stamp, (note or "")


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def list_menu(*, snapshot=None, backend=None, window_handle_id=None):
    """List the foreground app's root menu items (menu-bar roots).

    Returns {ok, items, window_handle_id, snapshot_stamp,
    tree_stamp, refusal}. Items are public projections: name,
    control type, enabled (False only when positively disabled —
    unknown reads as usable, mirroring ``core.desktop.uia._control_usable``),
    checked, has_children. An empty tree is a refusal, not an
    empty menu: an unexposed menu must say so."""
    snapshot, backend, window_handle_id, stamp, error = _prepare(
        snapshot, backend, window_handle_id)
    if error is not None:
        return dict(error, path=[])
    items, tree_stamp, note = _read_tree(backend)
    if items is None:
        return _fail("menu-unavailable", note, [], window_handle_id,
                     stamp)
    if not items:
        return _fail(
            "menu-unexposed",
            "that menu isn't exposed to automation%s — use the "
            "app's keyboard shortcut if it documents one, or "
            "perform the step manually."
            % ((" (%s)" % note) if note else ""),
            [], window_handle_id, stamp)
    return {"ok": True, "path": [], "action": "listed",
            "invoked": False, "opened": False,
            "window_handle_id": window_handle_id,
            "item_handle_ids": [], "snapshot_stamp": stamp,
            "tree_stamp": tree_stamp,
            "items": [_project_node(node) for node in items],
            "verification": None, "refusal": None}


# ---------------------------------------------------------------------------
# Walking + invoking
# ---------------------------------------------------------------------------


def invoke_path(path, *, snapshot=None, backend=None,
                window_handle_id=None, expected_tree_stamp=None,
                confirmed=False, effectful=None):
    """Open, walk, and invoke one menu path.

    ``path`` is a list of names or a "File → Save As" string.
    Intermediate segments are expanded one level at a time; the
    leaf is invoked via its Invoke pattern, or — when the leaf
    is itself a submenu — opened and reported as opened. Every
    refusal names the item, the state, and the alternatives; no
    refusal ever silently substitutes another entry."""
    from . import target_handles as _th

    segments = parse_path(path)
    if not segments:
        return _fail("empty-path",
                     "no menu path was named — name the menu path "
                     "(for example, File → Save As).",
                     [], window_handle_id, None)
    snapshot, backend, window_handle_id, stamp, error = _prepare(
        snapshot, backend, window_handle_id)
    if error is not None:
        return dict(error, path=segments)
    items, tree_stamp, note = _read_tree(backend)
    if items is None:
        return _fail("menu-unavailable", note, segments,
                     window_handle_id, stamp)
    if not items:
        return _fail(
            "menu-unexposed",
            "that menu isn't exposed to automation%s — use the "
            "app's keyboard shortcut if it documents one, or "
            "perform the step manually."
            % ((" (%s)" % note) if note else ""),
            segments, window_handle_id, stamp)
    if (expected_tree_stamp is not None
            and tree_stamp is not None
            and expected_tree_stamp != tree_stamp):
        return _fail(
            "menu-rebuilt",
            "the menu was rebuilt since it was listed (tree stamp "
            "changed) — the listed path is no longer the live "
            "menu, so nothing is invoked from it.",
            segments, window_handle_id, stamp)

    current_items = items
    current_stamp = tree_stamp
    walked = []
    item_handle_ids = []

    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        matches = [node for node in current_items
                   if isinstance(node, dict)
                   and _clean_name(node.get("name")) == segment]
        if not matches:
            siblings = ", ".join(
                "'%s'" % _clean_name(node.get("name"))
                for node in current_items[:10]
                if isinstance(node, dict) and _clean_name(node.get("name")))
            return _fail(
                "item-not-found",
                "'%s' is not in this menu level%s — the item's "
                "text may have changed; nothing is fuzzy-matched "
                "to a different command."
                % (segment,
                   (" (items here: %s)" % siblings) if siblings else ""),
                segments, window_handle_id, stamp)
        if len(matches) > 1:
            options = "; ".join(
                "'%s' (type %s%s)" % (
                    _clean_name(node.get("name")),
                    node.get("control_type") or "MenuItem",
                    (", automation id %s" % node.get("automation_id"))
                    if node.get("automation_id") else "")
                for node in matches)
            return _fail(
                "item-ambiguous",
                "more than one item is named '%s': %s — say which "
                "one (or pick from a fresh listing); the first "
                "match is never picked blindly."
                % (segment, options),
                segments, window_handle_id, stamp)
        node = matches[0]
        if node.get("enabled") is False:
            return _fail(
                "item-disabled",
                "'%s' is disabled — the action refuses rather "
                "than forcing it or silently skipping to another "
                "entry." % _clean_name(node.get("name")),
                segments, window_handle_id, stamp)
        automation_id = node.get("automation_id")
        if automation_id:
            # Item-1 proof for this segment: mint + resolve the
            # control handle against the node just matched, so
            # the receipt names a handle, not a bare name.
            try:
                minted = _th.mint_control(
                    {"automation_id": automation_id},
                    _clean_name(node.get("name")),
                    str(node.get("control_type") or "MenuItem"))
                resolved = _th.resolve(minted["id"], {
                    "candidates": [{
                        "identity": {"automation_id": automation_id},
                        "usable": node.get("enabled") is not False,
                        "name": _clean_name(node.get("name")),
                        "control_type":
                            str(node.get("control_type") or "MenuItem"),
                    }]})
            except Exception as exc:  # noqa: BLE001 — fail closed
                return _fail(
                    "handle-error",
                    "the handle layer failed for '%s' (%s) — the "
                    "action refuses rather than acting without a "
                    "proven target."
                    % (_clean_name(node.get("name")), exc),
                    segments, window_handle_id, stamp)
            if not resolved.get("ok"):
                inner = (resolved.get("refusal") or {}).get("reason") \
                    or "handle resolution refused."
                return _fail("handle-refused", inner, segments,
                             window_handle_id, stamp, instruction="")
            item_handle_ids.append(minted["id"])
        walked.append(node)
        if last:
            break
        # Intermediate segment: expand one level, re-proving the
        # walk against the FRESH children — never resume blindly.
        try:
            expanded = backend.expand(node)
        except Exception as exc:  # noqa: BLE001
            return _fail(
                "menu-expand-failed",
                "opening '%s' failed (%s) — the walk stops here."
                % (_clean_name(node.get("name")), exc),
                segments, window_handle_id, stamp)
        if isinstance(expanded, tuple) and len(expanded) == 3:
            children, new_stamp, expand_note = expanded
        elif isinstance(expanded, tuple) and len(expanded) == 2:
            children, new_stamp = expanded
            expand_note = ""
        else:
            children, new_stamp, expand_note = expanded, current_stamp, ""
        if (new_stamp is not None and current_stamp is not None
                and new_stamp != current_stamp):
            return _fail(
                "menu-rebuilt",
                "the menu was rebuilt while walking '%s' (tree "
                "stamp changed) — the walk stops instead of "
                "resuming mid-path on a look-alike menu."
                % " → ".join(segments[:index + 1]),
                segments, window_handle_id, stamp)
        if expand_note == "not-expandable" or not children:
            return _fail(
                "not-a-submenu",
                "'%s' has no submenu, but the path continues past "
                "it — name a path that ends at or inside a real "
                "submenu." % _clean_name(node.get("name")),
                segments, window_handle_id, stamp)
        current_items = children
        current_stamp = new_stamp

    leaf = walked[-1]
    # Leaf: expand first to learn whether it is a submenu. A leaf
    # that expands to children is OPENED, not invoked.
    try:
        expanded = backend.expand(leaf)
    except Exception as exc:  # noqa: BLE001
        return _fail(
            "menu-expand-failed",
            "opening '%s' failed (%s) — nothing is invoked."
            % (_clean_name(leaf.get("name")), exc),
            segments, window_handle_id, stamp)
    if isinstance(expanded, tuple) and len(expanded) == 3:
        leaf_children, leaf_stamp, leaf_note = expanded
    elif isinstance(expanded, tuple) and len(expanded) == 2:
        leaf_children, leaf_stamp = expanded
        leaf_note = ""
    else:
        leaf_children, leaf_stamp, leaf_note = expanded, current_stamp, ""
    if (leaf_stamp is not None and current_stamp is not None
            and leaf_stamp != current_stamp):
        return _fail(
            "menu-rebuilt",
            "the menu was rebuilt before invoking '%s' (tree "
            "stamp changed) — nothing is invoked from the stale "
            "menu." % _clean_name(leaf.get("name")),
            segments, window_handle_id, stamp)
    if leaf_children:
        return {"ok": True, "path": segments, "action": "opened",
                "invoked": False, "opened": True,
                "window_handle_id": window_handle_id,
                "item_handle_ids": item_handle_ids,
                "snapshot_stamp": stamp, "tree_stamp": leaf_stamp,
                "items": [_project_node(node) for node in leaf_children],
                "verification": "menu opened (no action invoked)",
                "refusal": None}
    if leaf_note not in ("", "not-expandable"):
        return _fail(
            "menu-expand-failed",
            "opening '%s' failed (%s) — nothing is invoked."
            % (_clean_name(leaf.get("name")), leaf_note),
            segments, window_handle_id, stamp)
    # Leaf invoke: C-14 first — a send / delete / purchase-class
    # leaf, or a caller-declared effectful leaf, needs confirmed.
    want_effectful = effectful if effectful is not None \
        else is_destructive_leaf(leaf.get("name"))
    if want_effectful and not confirmed:
        return _fail(
            "confirm-required",
            "'%s' is an effectful menu action — confirm it first "
            "(C-14 approve-first) before it is invoked."
            % _clean_name(leaf.get("name")),
            segments, window_handle_id, stamp,
            instruction="Confirm the action, then invoke the path again.")
    try:
        invoked = backend.invoke(leaf)
    except Exception as exc:  # noqa: BLE001
        return _fail(
            "invoke-failed",
            "invoking '%s' failed (%s) — nothing ran."
            % (_clean_name(leaf.get("name")), exc),
            segments, window_handle_id, stamp)
    if isinstance(invoked, tuple) and len(invoked) == 3:
        ok, verified, invoke_note = invoked
    elif isinstance(invoked, tuple) and len(invoked) == 2:
        ok, verified = invoked
        invoke_note = ""
    else:
        ok, verified, invoke_note = bool(invoked), False, ""
    if not ok:
        return _fail(
            "invoke-failed",
            "invoking '%s' did not run%s."
            % (_clean_name(leaf.get("name")),
               (" (%s)" % invoke_note) if invoke_note else ""),
            segments, window_handle_id, stamp)
    return {"ok": True, "path": segments, "action": "invoked",
            "invoked": True, "opened": False,
            "window_handle_id": window_handle_id,
            "item_handle_ids": item_handle_ids,
            "snapshot_stamp": stamp, "tree_stamp": leaf_stamp,
            "items": [], "verification":
                "verified" if verified else "invoked-without-verification",
            "refusal": None}


# ---------------------------------------------------------------------------
# Default backend — existing tree sources only, lazy imports
# ---------------------------------------------------------------------------


class _DefaultBackend:
    """Live Windows backend (field-owed, headless-safe).

    Menu roots come from the tree's own UIA descendant walk: the
    first MenuBar (else Menu) element's MenuItem children. Expand
    uses the pywinauto ExpandCollapse pattern; invoke uses the
    Invoke pattern only — never coordinates, never synthesised
    keystrokes. The tree stamp is a content hash of the root
    items, so a root rebuild with different items changes it;
    submenu rebuilds are still caught because every level is
    re-matched against freshly-expanded children. Every method
    degrades honestly off-Windows (empty tree + note) instead of
    fabricating a menu. Automation ids are best-effort (the
    element-info property may be absent); checked state is
    honestly unknown (None) — no Toggle-pattern guesswork.
    """

    def __init__(self):
        self._wrappers = {}
        self._counter = 0

    def foreground_identity(self):
        try:
            from . import control as _control  # Phase 3 D4a: control hub now in core/desktop/; S7a interim closed
            return dict(_control._foreground_identity())
        except Exception:  # noqa: BLE001
            return {"hwnd": None, "pid": None, "title": None,
                    "proc": None}

    def bounds(self):
        try:
            from . import vision as _vision  # Phase 3 D4c: vision now in core/desktop/; S7a interim closed
            return _vision.foreground_window_rect()
        except Exception:  # noqa: BLE001
            return None

    def _node(self, wrapper, facts):
        self._counter += 1
        key = "m%d" % self._counter
        self._wrappers[key] = wrapper
        try:
            automation_id = wrapper.element_info.automation_id
        except Exception:  # noqa: BLE001
            automation_id = None
        try:
            from . import uia as _uia  # Phase 3 D4b: uia hub now in core/desktop/; S7a interim closed
            usable, reason = _uia._control_usable(wrapper, facts)
        except Exception:  # noqa: BLE001
            usable, reason = True, ""
        enabled = True if usable else (
            False if reason == "disabled" else None)
        return {"key": key, "name": facts.get("name") or "",
                "control_type": facts.get("control_type") or "MenuItem",
                "automation_id": automation_id,
                "enabled": enabled, "checked": None}

    def _stamp(self, items):
        raw = json.dumps(
            [(item.get("name"), item.get("automation_id"),
              item.get("control_type")) for item in items],
            sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _children_nodes(self, wrapper):
        from . import uia as _uia  # Phase 3 D4b: uia hub now in core/desktop/; S7a interim closed
        try:
            children = wrapper.children()
        except Exception:  # noqa: BLE001
            return []
        nodes = []
        for child in children:
            facts = _uia._element_facts(child)
            if facts is None or not facts.get("name"):
                continue
            if facts.get("control_type") not in ("MenuItem", "Menu"):
                continue
            nodes.append(self._node(child, facts))
        return nodes

    def menu_tree(self):
        try:
            from . import uia as _uia  # Phase 3 D4b: uia hub now in core/desktop/; S7a interim closed
            fg = self.foreground_identity()
            win, _desc = _uia._uia_window(str(fg.get("title") or ""))
            elements, failure = _uia._descendants(win)
            if failure is not None or elements is None:
                return [], None, failure or "window walk failed"
            root = None
            for el in elements:
                facts = _uia._element_facts(el)
                if facts is None:
                    continue
                if facts.get("control_type") == "MenuBar":
                    root = el
                    break
                if root is None and facts.get("control_type") == "Menu":
                    root = el
            if root is None:
                return [], None, "no menu bar exposed"
            items = self._children_nodes(root)
            if not items:
                return [], None, "menu bar exposed no items"
            return items, self._stamp(items), ""
        except Exception as exc:  # noqa: BLE001 — degrade, never raise
            return [], None, "menu tree unavailable: %s" % exc

    def expand(self, node):
        wrapper = self._wrappers.get(node.get("key")) \
            if isinstance(node, dict) else None
        if wrapper is None:
            return [], None, "unknown menu node"
        try:
            wrapper.expand()
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__
            if "NoPattern" in name or "pattern" in str(exc).lower():
                return [], None, "not-expandable"
            raise
        children = self._children_nodes(wrapper)
        return children, self._stamp(children), ""

    def invoke(self, node):
        wrapper = self._wrappers.get(node.get("key")) \
            if isinstance(node, dict) else None
        if wrapper is None:
            return False, False, "unknown menu node"
        try:
            wrapper.invoke()
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__
            if "NoPattern" in name or "pattern" in str(exc).lower():
                return False, False, \
                    "item does not expose an Invoke pattern"
            return False, False, str(exc)
        return True, False, ""


def default_backend():
    """A fresh default backend (stateful per call: wrapper map)."""
    return _DefaultBackend()
