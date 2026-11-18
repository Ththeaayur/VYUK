#: Manual alternative for every trigger-class refusal: the user
#: right-clicks the target themselves, then names the item.
CONTEXT_MANUAL = (
    "Right-click the target yourself, then name the menu item."
)


# ---------------------------------------------------------------------------
# Small helpers (mirroring core.desktop.menu_paths shapes, not forking it)
# ---------------------------------------------------------------------------


def _clean_name(name) -> str:
    """A control/menu name for comparison: stripped, UIA
    accelerator '&' removed. Nothing else is normalised."""
    return str(name or "").replace("&", "").strip()


def _refusal(code, reason, instruction=""):
    if instruction:
        reason = reason + " " + instruction
    return {"code": code, "reason": reason}


def _fail(code, reason, path, window_handle_id, target_name,
          target_handle_id, snapshot_stamp, instruction=""):
    return {"ok": False, "path": list(path), "action": None,
            "invoked": False, "opened": False,
            "window_handle_id": window_handle_id,
            "target_handle_id": target_handle_id,
            "target_name": target_name,
            "item_handle_ids": [], "snapshot_stamp": snapshot_stamp,
            "tree_stamp": None, "items": [], "verification": None,
            "refusal": _refusal(code, reason, instruction)}


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


def describe(result) -> str:
    """One line for receipts / activity records."""
    if not isinstance(result, dict):
        return "no context menu result"
    if result.get("ok"):
        action = result.get("action")
        target = result.get("target_name") or "target"
        handle = result.get("window_handle_id")
        handle_note = (" (window handle %s)" % handle) if handle else ""
        verification = result.get("verification")
        verification_note = (" — %s" % verification) if verification \
            else ""
        if action == "opened" and not result.get("path"):
            count = len(result.get("items") or [])
            return ("Context menu for '%s' opened (%d items)%s%s"
                    % (target, count, handle_note, verification_note))
        if action == "listed":
            count = len(result.get("items") or [])
            return ("Context menu for '%s' listed (%d items)%s"
                    % (target, count, handle_note))
        path = " → ".join(result.get("path") or [])
        verb = "opened" if result.get("opened") else "invoked"
        return ("Context menu path %s %s%s%s"
                % (path, verb, handle_note, verification_note))
    refusal = result.get("refusal") or {}
    code = refusal.get("code") or "refused"
    reason = refusal.get("reason") or "no reason given"
    path = result.get("path") or []
    if path:
        return ("Context menu path %s refused: %s"
                % (" → ".join(path), reason))
    return "Context menu refused (%s): %s" % (code, reason)


# ---------------------------------------------------------------------------
# Shared call setup: one snapshot, one window handle, one target
# ---------------------------------------------------------------------------


def _setup(snapshot, backend, window_handle_id):
    """(snapshot, backend, window_handle_id, stamp, error-result).

    Reuses the item-3 setup (one observation anchors the call;
    the backend's current foreground must still match it; the
    window resolves through the item-1 engine) and translates
    its menu-shaped error into a context-shaped one. The
    backend here is always the CONTEXT backend (defaulted by
    the caller, never the menu default).
    """
    from . import menu_paths as _mp  # Phase 3 S7a-2: menu_paths already in core/desktop/  # Phase 3 S7a: menu_paths moved to core/desktop/

    _snapshot, _backend, _handle, stamp, error = _mp._prepare(
        snapshot, backend, window_handle_id)
    if error is not None:
        refusal = error.get("refusal") or {}
        return None, None, None, stamp, _fail(
            refusal.get("code") or "setup-failed",
            refusal.get("reason") or "menu setup failed.",
            [], window_handle_id, None, None, stamp)
    return _snapshot, _backend, _handle, stamp, None


def _resolve_target(target, target_handle_id, snapshot, stamp,
                    window_handle_id):
    """(target_node, target_name, error-result).

    Exact-name match against the snapshot's controls section
    (the enumerated marks), or an item-1 MARK handle re-proven
    by the F-07 rule plus a name-presence check in the same
    snapshot. Control-kind handles refuse with guidance (see
    the module docstring); window/file handles are not
    context-menu targets at all.
    """
    from . import target_handles as _th  # Phase 3 S7a-2: target_handles already in core/desktop/  # Phase 3 S7a: target_handles moved to core/desktop/

    controls = snapshot.get("controls") or []
    controls = [c for c in controls if isinstance(c, dict)]
    if target_handle_id is not None:
        fg = (snapshot.get("foreground_identity") or {})
        resolved = _th.resolve(target_handle_id, {
            "foreground": fg, "bounds": snapshot.get("bounds")})
        if not resolved.get("ok"):
            inner = (resolved.get("refusal") or {}).get("reason") or \
                "target-handle resolution refused."
            return None, None, _fail(
                "target-handle-refused",
                "the consented target cannot be re-proven: %s" % inner,
                [], window_handle_id, None, target_handle_id, stamp,
                instruction="")
        # Only mark handles can resolve against this
        # observation shape (foreground + bounds, the F-07
        # rule): window/control/file kinds need a candidate
        # list the engine deliberately does not offer, because
        # snapshot controls (marks) carry no automation ids
        # to re-prove them against — so they fail inside
        # resolve() with target-handle-refused, honestly.
        # A resolved handle here is always a mark handle.
        mark = (resolved.get("resolved") or {}).get("mark") or {}
        name = _clean_name(mark.get("name"))
        if not name:
            return None, None, _fail(
                "target-unnamed",
                "the consented mark carries no name to match — "
                "name the target explicitly.",
                [], window_handle_id, None, target_handle_id, stamp)
        matches = [c for c in controls
                   if _clean_name(c.get("name")) == name]
        if not matches:
            return None, None, _fail(
                "target-gone",
                "the consented target '%s' is no longer among "
                "this observation's controls — it is gone or "
                "replaced." % name,
                [], window_handle_id, name, target_handle_id, stamp)
        if len(matches) > 1:
            options = "; ".join(
                "'%s' (type %s)" % (
                    _clean_name(c.get("name")),
                    c.get("control_type") or "Control")
                for c in matches)
            return None, None, _fail(
                "target-ambiguous",
                "more than one control is named '%s': %s — say "
                "which one (or pick from a fresh listing); the "
                "first match is never picked blindly."
                % (name, options),
                [], window_handle_id, name, target_handle_id, stamp)
        node = dict(matches[0])
        return node, name, None
    if target is None or not str(target).strip():
        return None, None, _fail(
            "target-required",
            "a context-menu action needs a target — name the "
            "item (for example, the file in the Explorer tree), "
            "or pass a mark handle minted from an earlier "
            "observation.",
            [], window_handle_id, None, None, stamp)
    name = _clean_name(target)
    matches = [c for c in controls
               if _clean_name(c.get("name")) == name]
    if not matches:
        siblings = ", ".join(
            "'%s'" % _clean_name(c.get("name"))
            for c in controls[:10] if _clean_name(c.get("name")))
        return None, None, _fail(
            "target-not-found",
            "'%s' is not among this observation's controls%s — "
            "the item's name may have changed; nothing is "
            "fuzzy-matched to a different control."
            % (name, (" (controls here: %s)" % siblings)
               if siblings else ""),
            [], window_handle_id, name, None, stamp)
    if len(matches) > 1:
        options = "; ".join(
            "'%s' (type %s)" % (
                _clean_name(c.get("name")),
                c.get("control_type") or "Control")
            for c in matches)
        return None, None, _fail(
            "target-ambiguous",
            "more than one control is named '%s': %s — say "
            "which one (or pick from a fresh listing); the "
            "first match is never picked blindly."
            % (name, options),
            [], window_handle_id, name, None, stamp)
    node = dict(matches[0])
    return node, name, None


def _check_backend(backend, window_handle_id, target_name,
                   target_handle_id, stamp):
    """Refusal when the backend cannot open context menus."""
    for method in ("open_context_menu", "current_context_menu",
                   "context_menu_tree"):
        if not callable(getattr(backend, method, None)):
            return _fail(
                "context-menu-unsupported",
                "this backend cannot open context menus — %s"
                % CONTEXT_MANUAL,
                [], window_handle_id, target_name, target_handle_id,
                stamp, instruction="")
    return None


def _from_menu_result(menu_result, *, action_override, target_name,
                      target_handle_id, window_handle_id):
    """Translate an item-3 result into a context result.

    The refusal code/reason ride through verbatim; only the
    action label and the target fields are context-shaped.
    A menu-unexposed failure gains the context manual path
    (right-click) instead of the menu-bar keyboard advice.
    """
    refusal = menu_result.get("refusal")
    if refusal is not None and refusal.get("code") == "menu-unexposed":
        refusal = dict(refusal)
        note = refusal.get("reason") or ""
        refusal["reason"] = (
            "that target's context menu isn't exposed to "
            "automation%s — %s"
            % ((" (%s)" % note) if note else "", CONTEXT_MANUAL))
    return {
        "ok": bool(menu_result.get("ok")),
        "path": list(menu_result.get("path") or []),
        "action": action_override,
        "invoked": bool(menu_result.get("invoked")),
        "opened": bool(menu_result.get("opened")),
        "window_handle_id": window_handle_id,
        "target_handle_id": target_handle_id,
        "target_name": target_name,
        "item_handle_ids": list(menu_result.get("item_handle_ids") or []),
        "snapshot_stamp": menu_result.get("snapshot_stamp"),
        "tree_stamp": menu_result.get("tree_stamp"),
        "items": menu_result.get("items") or [],
        "verification": menu_result.get("verification"),
        "refusal": refusal,
    }


