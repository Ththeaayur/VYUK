"""Context menus — §7 item 3 follow-up (consumers batch 8).

Item 3 shipped menu-bar roots only: ``core.desktop.menu_paths`` walks the
foreground app's MenuBar (else Menu) element. A context menu is a
different root — it does not exist until a consented open step
creates it, and it belongs to a TARGET (a file in Explorer, an
item in VS Code's Explorer tree, a tray icon's menu is item 7's
own surface, not this engine). This module is that open step
plus the reuse of the item-3 walk for the opened menu.

Rules (inheriting §0 + items 1–3):

* Resolve through handles, never around them. The window resolves
  through ``core.desktop.target_handles`` (minted from the observation's
  identity when the caller passes no handle id); the target
  resolves by EXACT name against the anchoring snapshot's
  ``controls`` section (the enumerated marks of the foreground
  window), or through an item-1 MARK handle (the F-07
  grounding-table kind — the natural handle for a visible
  control, re-proven by the F-07 foreground/bounds rule plus
  a name-presence check in the same snapshot). Any other
  handle kind refuses at resolution: the engine offers no
  candidate list, because snapshot controls (marks) carry no
  automation ids to re-prove a control handle against — pass
  the target by exact name, or a mark handle, instead. Never
  fuzzy, never by position, never by memorised coordinates.
  The engine never reads a rectangle to pick a target (a
  mark's rectangle is only ever the F-07 bounds check inside
  handle resolution).
* Observe once, share the snapshot. Every call is anchored to
  ONE ``core.desktop.perception_adapter`` snapshot (passed in, or taken
  once here via the item-3 setup path). The backend's current
  foreground must still match that snapshot or the call refuses
  — the engine adds NO second observation path of its own.
* Trigger through an exposed path only. The backend opens the
  menu — either the app's exposed UIA context-menu pattern or
  a right-click on the tree-matched element (the element comes
  from the live UIA walk, never from memorised coordinates).
  No keystroke fallback, no pixel workaround. A backend that
  cannot open context menus refuses with the manual path
  (right-click the target yourself, then name the item).
* Verify fresh, never from pre-trigger state. After the trigger,
  the opened menu's items are read FRESH from the live tree
  (``context_menu_tree``) — that fresh read is the verification
  evidence. A trigger whose fresh read fails reports
  ``opened-without-verification`` (the trigger ran, the open
  state is unproven — mirroring item-3's
  ``invoked-without-verification``); a fresh read that finds
  no items refuses as unexposed. The engine does NOT
  cross-check against a perception snapshot: the perception
  walk covers the foreground window, while a context popup is
  often a separate top-level window, so a snapshot check
  would false-fail genuinely-open menus. The fresh tree read
  is the verification channel, and it is named as such.
* Reuse, never fork. Once open, the menu is walked by
  ``core.desktop.menu_paths`` itself (a wrapper backend whose
  ``menu_tree`` is the fresh context tree): exact-name
  matching only, accelerator ``&`` stripped, the same refusal
  classes (disabled, ambiguous, not-found with siblings,
  rebuilt, unexposed, stale, not-a-submenu, invoke-failed),
  the same C-14 confirm set (send / delete / purchase-class
  first words + caller-declared ``effectful``; "Save"
  deliberately NOT in the set), the same
  invoked-without-verification honesty. No second menu engine.
* By-design walls are never bypassed: the snapshot capture
  denylist already blocks secure-desktop / password-field
  captures at observe time (a denied capture refuses here
  before anything opens), and the engine never synthesises
  keystrokes to fake a menu the tree does not expose.
* State: the engine is stateless across calls, like item 3.
  ``trigger`` opens + verifies + lists; ``list`` reads the
  CURRENTLY open menu without triggering (refusing when none
  is open); ``invoke`` opens (idempotent — an already-open
  menu for the target is returned, not re-triggered) and then
  walks + invokes the path in the same call, walking the
  trigger's menu node itself, never a re-derived current menu
  (batch-8 deviation decision 2026-11-03). A menu left open
  after a refusal is the honest app state — the next call can
  list it; the engine never invents cleanup clicks.

Backends: every function takes an optional ``backend``
implementing the context protocol (``foreground_identity``,
``bounds``, ``open_context_menu``, ``current_context_menu``,
``context_menu_tree``, ``expand``, ``invoke``). The default
backend wires the tree's existing UIA sources with lazy
imports, so importing this module needs only the standard
library. Tests drive the protocol with a fake backend; live
Windows behaviour — real context popups, real right-click
delivery, real popup ownership (which varies by app and
toolkit) — stays field work and is not claimed here.
"""

__all__ = [
    "open_context_menu",
    "list_context_menu",
    "invoke_context_path",
    "describe",
    "default_backend",
    "CONTEXT_MANUAL",
]


# INTENTIONAL (context_menus split B2): every name the old module
# defined is re-exported from the impl module that now owns it, so
# existing module-attribute importers (tools_handlers_pack_
# s7consumers3, test_s7_consumers8_context, test_batch8_deviation)
# work unchanged and with identical objects. The trigger/invoke ops
# below stay defined in this facade: test_s7_consumers8_context
# patches cm.default_backend, and the ops resolve default_backend
# by bare name at call time -- moving them would silently unplug
# that patch seam, and the §3.7 binding never moves at all.
from .context_menus_helpers import (
    CONTEXT_MANUAL,
    _check_backend,
    _clean_name,
    _fail,
    _from_menu_result,
    _project_node,
    _refusal,
    _resolve_target,
    _setup,
    describe,
)
from .context_menus_tree import _ContextTreeBackend
from .context_menus_default import _DefaultContextBackend, default_backend


# ---------------------------------------------------------------------------
# Trigger / list / invoke
# ---------------------------------------------------------------------------


def _trigger_menu(target=None, *, target_handle_id=None,
                  snapshot=None, backend=None,
                  window_handle_id=None):
    """Trigger + fresh-verify, internal: (menu node, result).

    The body is exactly the public trigger's: ``menu_node`` is
    the node the backend opened for this target, or None when
    no menu was obtained. ``open_context_menu`` is the public
    face (result only — nodes carry backend-private keys and
    never leak into receipts); ``invoke_context_path`` keeps
    the node so its walk uses the triggered menu itself, never
    a re-derived current menu (batch-8 deviation decision
    2026-11-03).
    """
    if backend is None:
        backend = default_backend()
    snapshot, backend, window_handle_id, stamp, error = _setup(
        snapshot, backend, window_handle_id)
    if error is not None:
        return None, error
    node, name, target_error = _resolve_target(
        target, target_handle_id, snapshot, stamp, window_handle_id)
    if target_error is not None:
        return None, target_error
    unsupported = _check_backend(backend, window_handle_id, name,
                                 target_handle_id, stamp)
    if unsupported is not None:
        return None, unsupported
    try:
        opened = backend.open_context_menu(node)
    except Exception as exc:  # noqa: BLE001 — backend failure is a refusal
        return None, _fail(
            "trigger-failed",
            "opening the context menu for '%s' failed (%s) — %s"
            % (name, exc, CONTEXT_MANUAL),
            [], window_handle_id, name, target_handle_id, stamp,
            instruction="")
    if isinstance(opened, tuple) and len(opened) == 2:
        menu_node, open_note = opened
    elif isinstance(opened, tuple) and len(opened) == 3:
        menu_node, open_note = opened[0], opened[2]
    else:
        menu_node, open_note = opened, ""
    if menu_node is None:
        return None, _fail(
            "trigger-failed",
            "the context menu for '%s' did not open%s — %s"
            % (name,
               (" (%s)" % open_note) if open_note else "",
               CONTEXT_MANUAL),
            [], window_handle_id, name, target_handle_id, stamp,
            instruction="")
    wrapper = _ContextTreeBackend(backend, menu_node)
    from . import menu_paths as _mp  # Phase 3 S7a-2: menu_paths already in core/desktop/  # Phase 3 S7a: menu_paths moved to core/desktop/
    listed = _mp.list_menu(snapshot=snapshot, backend=wrapper,
                           window_handle_id=window_handle_id)
    if listed.get("ok"):
        result = _from_menu_result(
            listed, action_override="opened", target_name=name,
            target_handle_id=target_handle_id,
            window_handle_id=window_handle_id)
        result["verification"] = "verified"
        return menu_node, result
    code = (listed.get("refusal") or {}).get("code")
    if code == "menu-unavailable":
        # The trigger ran; only the fresh read failed. The open
        # state is unproven, never claimed — mirroring item-3's
        # invoked-without-verification.
        return menu_node, {"ok": True, "path": [], "action": "opened",
                "invoked": False, "opened": True,
                "window_handle_id": window_handle_id,
                "target_handle_id": target_handle_id,
                "target_name": name, "item_handle_ids": [],
                "snapshot_stamp": stamp, "tree_stamp": None,
                "items": [],
                "verification": "opened-without-verification",
                "refusal": None}
    return menu_node, _from_menu_result(
        listed, action_override=None, target_name=name,
        target_handle_id=target_handle_id,
        window_handle_id=window_handle_id)


def open_context_menu(target=None, *, target_handle_id=None,
                      snapshot=None, backend=None,
                      window_handle_id=None):
    """Open a target's context menu and verify it fresh.

    Returns {ok, action "opened", items, window_handle_id,
    target_name, snapshot_stamp, tree_stamp, verification,
    refusal}. ``verification`` is "verified" when the fresh
    tree read lists items, "opened-without-verification" when
    the trigger ran but the fresh read failed (the open state
    is unproven, never claimed), and absent on refusal.
    """
    _node, result = _trigger_menu(
        target, target_handle_id=target_handle_id, snapshot=snapshot,
        backend=backend, window_handle_id=window_handle_id)
    return result


def list_context_menu(target=None, *, target_handle_id=None,
                      snapshot=None, backend=None,
                      window_handle_id=None):
    """List the CURRENTLY open context menu for a target,
    without triggering. Refuses (never triggers) when no
    context menu is open for that target.
    """
    if backend is None:
        backend = default_backend()
    snapshot, backend, window_handle_id, stamp, error = _setup(
        snapshot, backend, window_handle_id)
    if error is not None:
        return error
    node, name, target_error = _resolve_target(
        target, target_handle_id, snapshot, stamp, window_handle_id)
    if target_error is not None:
        return target_error
    unsupported = _check_backend(backend, window_handle_id, name,
                                 target_handle_id, stamp)
    if unsupported is not None:
        return unsupported
    try:
        current = backend.current_context_menu()
    except Exception as exc:  # noqa: BLE001 — backend failure is a refusal
        return _fail(
            "menu-unavailable",
            "reading the open context menu failed (%s) — %s"
            % (exc, CONTEXT_MANUAL),
            [], window_handle_id, name, target_handle_id, stamp,
            instruction="")
    if isinstance(current, tuple) and len(current) == 2:
        menu_node, current_note = current
    elif isinstance(current, tuple) and len(current) == 3:
        menu_node, current_note = current[0], current[2]
    else:
        menu_node, current_note = current, ""
    if menu_node is None:
        return _fail(
            "no-context-menu-open",
            "no context menu is open for '%s'%s — trigger it "
            "first, or %s"
            % (name,
               (" (%s)" % current_note) if current_note else "",
               CONTEXT_MANUAL.lower()),
            [], window_handle_id, name, target_handle_id, stamp,
            instruction="")
    wrapper = _ContextTreeBackend(backend, menu_node)
    from . import menu_paths as _mp  # Phase 3 S7a-2: menu_paths already in core/desktop/  # Phase 3 S7a: menu_paths moved to core/desktop/
    listed = _mp.list_menu(snapshot=snapshot, backend=wrapper,
                           window_handle_id=window_handle_id)
    return _from_menu_result(
        listed, action_override="listed", target_name=name,
        target_handle_id=target_handle_id,
        window_handle_id=window_handle_id)


def invoke_context_path(path, target=None, *, target_handle_id=None,
                        snapshot=None, backend=None,
                        window_handle_id=None, confirmed=False,
                        effectful=None):
    """Open a target's context menu, then walk + invoke a path
    in it — one call, reusing the item-3 walk exactly.

    ``path`` is a list of names or a "Delete" / "Open With →
    ..." string. The trigger step's failures are the answer
    (no walk is attempted from a menu that did not open); the
    walk's failures are the item-3 failures verbatim (disabled,
    ambiguous, not-found with siblings, rebuilt, unexposed,
    stale, not-a-submenu, invoke-failed, C-14 confirm-required).
    The walk uses the trigger's menu node itself — never a
    re-derived current menu — so the walked menu is always the
    one the trigger opened for this target (batch-8 deviation
    decision 2026-11-03).
    """
    from . import menu_paths as _mp  # Phase 3 S7a-2: menu_paths already in core/desktop/  # Phase 3 S7a: menu_paths moved to core/desktop/

    segments = _mp.parse_path(path)
    if not segments:
        return _fail("empty-path",
                     "no context menu path was named — name the "
                     "menu path (for example, 'Delete').",
                     [], window_handle_id, None, target_handle_id,
                     None)
    if backend is None:
        backend = default_backend()
    menu_node, opened = _trigger_menu(
        target, target_handle_id=target_handle_id, snapshot=snapshot,
        backend=backend, window_handle_id=window_handle_id)
    if not opened.get("ok"):
        # The trigger failure is the answer; the path is the
        # caller's path, not the trigger's empty one.
        result = dict(opened)
        result["path"] = segments
        return result
    if opened.get("verification") == "opened-without-verification":
        # The menu may be open, but its items are unread — a
        # walk from an unread menu would be blind. Refuse
        # honestly instead of walking it.
        return _fail(
            "menu-unavailable",
            "the context menu for '%s' opened but its items "
            "could not be read, so no path is walked from it — "
            "%s" % (opened.get("target_name") or "target",
                    CONTEXT_MANUAL),
            segments, window_handle_id,
            opened.get("target_name"), target_handle_id,
            opened.get("snapshot_stamp"), instruction="")
    # Batch-8 deviation decision 2026-11-03 INTENTIONAL: the
    # walk uses the TRIGGER's menu node, never a re-derived
    # current menu. The old code re-read
    # backend.current_context_menu() here, which returns
    # whatever Menu the foreground walk finds first — a
    # different menu than the triggered one when a second Menu
    # is present — and the walk would then act on an item
    # nobody chose for this target (a same-named "Delete" in
    # the wrong menu still passes the walk's exact-name match,
    # and a path approval names the path, not the menu, so
    # C-14 does not save that case). Binding the walk to the
    # trigger's node by construction closes the substitution:
    # past this point opened is ok AND verified, which only
    # the listed-ok branch produces, and that branch always
    # carries the trigger's node — so menu_node is present
    # here by construction, not by re-proof. If the triggered
    # menu later reads empty or dead, the walk refuses
    # honestly (menu-unexposed / unknown-node) instead of
    # walking a look-alike menu. current_context_menu stays
    # the list path's contract (list reads the currently open
    # menu by design); it is simply no longer the invoke
    # path's source of truth.
    wrapper = _ContextTreeBackend(backend, menu_node)
    walked = _mp.invoke_path(
        segments, snapshot=snapshot, backend=wrapper,
        window_handle_id=window_handle_id, confirmed=bool(confirmed),
        effectful=effectful)
    return _from_menu_result(
        walked, action_override=walked.get("action"),
        target_name=opened.get("target_name"),
        target_handle_id=target_handle_id,
        window_handle_id=window_handle_id)


