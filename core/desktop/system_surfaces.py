"""System-level surfaces — §7 item 7 (parent draft).

The assistant can work the system's own surfaces — tray, taskbar,
Start, notifications, settings pages — through the controls the OS
exposes to automation, and never touches the walls.

Scope (from the §7 item-7 definition, inheriting §0 + items 1–3):

* Tray icons + tray menus, taskbar (pin / launch / switch via the
  taskbar's exposed controls), Start menu search / launch,
  notification centre (list + act on user-named notifications),
  and system settings pages reached through their exposed
  controls. Only surfaces the OS exposes to automation.
* EXCLUDED by design, never built, never bypassed: the lock
  screen, the UAC secure desktop, password fields, CAPTCHAs, and
  any privilege escalation. The assistant asks the user to
  perform those; it never performs them (see :func:`route_surface`).
* A system target is a stable item-1 handle with ``surface`` in
  {tray, taskbar, start, notification, settings} plus the
  surface's own identity (tray icon id, taskbar button identity,
  notification id). Every action call re-lists FRESH inside the
  call and re-proves the target through ``core.desktop.target_handles``
  before invoking — shell surfaces have no foreground window to
  anchor a perception snapshot to, so the fresh listing IS the
  execution-time proof (the TOCTOU rule from §0, applied to a
  listing instead of a snapshot). A target that vanished,
  changed identity, or was dismissed/expired refuses; nothing
  acts on a stale entry.
* Two-call proof: when the caller passes a ``target_handle_id``
  minted from an EARLIER listing, the fresh candidate must
  re-prove that handle (the item-1 engine refuses on any
  stable-field contradiction) — that is the real consent →
  execution split. A single-call action (no handle id) mints
  from the fresh entry and resolves against it; that step
  guards usable-state and identity shape and leaves an audit
  handle, and the docstring says so — it is not presented as
  a consent-time proof, because the consent and the listing
  are the same call.
* Notifications are NEVER addressed by position: there is no
  index parameter anywhere in this module. An action names the
  notification by id + user-confirmed text; an expired or
  dismissed notification refuses naming it; a title that no
  longer matches refuses naming the current title.
* Notification content — DECISION D4 STAYS OPEN (owner's
  question, not decided here). The companion module builds the
  draft proposal only: titles-only by default; a body is
  returned only for a notification id the caller explicitly
  approved per notification.
* Tray menus ride the menu engine where it fits: path parsing
  and destructive-leaf confirm rules come from
  ``core.desktop.menu_paths`` (``parse_path`` / ``is_destructive_leaf``),
  and the walk mirrors its refusal semantics exactly (exact
  name in the freshly-expanded parent only, disabled refuses
  naming the item, ambiguous lists and asks, rebuilt tree
  refuses on a stamp change). The foreground-anchored
  ``menu_paths.invoke_path`` itself does NOT fit shell surfaces
  — a tray menu has no foreground app window — so the walk here
  is local, with the same rules, and says so.
* C-14 approve-first still governs: system actions that change
  system state (taskbar pin, notification dismissal, any
  caller-declared effectful action) refuse with
  ``needs-confirmation`` until the caller passes
  ``confirmed=True``. The engine grants no permission — it only
  refuses without one.
* Consequence verification is honest by default: unless a
  backend reports ``verified=True`` from its invoke, the result
  says ``invoked-without-verification`` (or
  ``action-without-verification`` for notifications).

Backends: every public function takes an optional ``backend``
implementing the system-surface protocol (``tray_icons``,
``tray_menu``, ``expand_tray``, ``invoke_tray``,
``taskbar_buttons``, ``activate_taskbar``, ``launch_taskbar``,
``pin_taskbar``, ``start_search``, ``launch_start``,
``notifications``, ``act_notification``, ``settings_pages``,
``open_settings``). The default backend degrades honestly
everywhere (surface unavailable + manual path) instead of
fabricating a surface: live tray / Start / notification
behaviour on real Windows stays field work and is not claimed
here. Tests drive the protocol with a fake backend.

The companion module ``core.desktop.system_surfaces_shell`` carries
Start, notifications, and settings pages on this same plumbing.
This module never touches the funnel or the gate stack directly:
callers pass ``confirmed=True`` only after their own approval
path has run. No funnel tool is registered here; the consumer
that wires these functions to voice commands is the named
follow-up, not something this module pretends exists.
"""

__all__ = [
    "SURFACES",
    "WALL_SURFACES",
    "RELIST_INSTRUCTION",
    "route_surface",
    "list_tray_icons",
    "open_tray_menu",
    "invoke_tray_menu_path",
    "list_taskbar_buttons",
    "switch_taskbar",
    "launch_taskbar",
    "pin_taskbar",
    "describe",
    "default_backend",
]

SURFACES = ("tray", "taskbar", "start", "notification", "settings")

#: By-design walls. These are never automation targets; the only
#: honest response is to refuse and hand the step to the user.
WALL_SURFACES = ("lock-screen", "uac", "password-field", "captcha",
                  "lock screen", "secure desktop", "privilege-escalation")

RELIST_INSTRUCTION = (
    "List again and pick a fresh target — never act on a stale entry."
)


# ---------------------------------------------------------------------------
# Result plumbing (mirrors core.desktop.menu_paths / core.desktop.file_dialogs)
# ---------------------------------------------------------------------------


def _refusal(code, reason, instruction=RELIST_INSTRUCTION):
    return {"code": code,
            "reason": reason + " " + instruction}


def _fail(code, reason, surface, action, target=None,
          instruction=RELIST_INSTRUCTION):
    return {"ok": False, "surface": surface, "action": action,
            "target": target, "target_handle_id": None,
            "verification": None,
            "refusal": _refusal(code, reason, instruction)}


def _ok(surface, action, target=None, handle_id=None, verification=None,
        **extra):
    result = {"ok": True, "surface": surface, "action": action,
              "target": target, "target_handle_id": handle_id,
              "verification": verification, "refusal": None}
    result.update(extra)
    return result


def describe(result):
    """One plain-language line for a result dict."""
    result = result or {}
    if result.get("ok"):
        target = result.get("target")
        verification = result.get("verification")
        line = "%s: %s" % (result.get("surface"), result.get("action"))
        if target:
            line += " — %s" % target
        if verification in ("invoked-without-verification",
                            "action-without-verification"):
            line += " (reported by the surface, not verified)"
        return line
    refusal = result.get("refusal") or {}
    return "refused (%s): %s" % (refusal.get("code"),
                                 refusal.get("reason"))


def _clean(value):
    return str(value or "").strip()


def _clean_name(name):
    """Menu-path name cleaning, mirroring core.desktop.menu_paths."""
    return " ".join(_clean(name).split())


# ---------------------------------------------------------------------------
# Surface routing + wall guard
# ---------------------------------------------------------------------------


def route_surface(name):
    """Route a caller-named surface to its handling.

    Returns ``{"ok", "surface", "refusal"}``: a known surface
    name routes to that surface; a wall name refuses with
    ``wall-refused`` (the step is the user's to perform, never
    the assistant's to bypass); anything else refuses with
    ``unknown-surface``. No wall name ever routes to an action.
    The wall check is deliberately broad (fail-closed): any
    name mentioning a wall keyword refuses.
    """
    cleaned = _clean(name).lower()
    wall_hit = cleaned in WALL_SURFACES or \
        cleaned.replace(" ", "-") in WALL_SURFACES or \
        any(word in cleaned for word in
            ("lock", "uac", "captcha", "password", "secure desktop",
             "privilege", "credential"))
    if wall_hit:
        return {"ok": False, "surface": None,
                "refusal": _refusal(
                    "wall-refused",
                    "that surface (%s) is a by-design wall — the "
                    "assistant never touches it. Please perform "
                    "that step yourself." % _clean(name),
                    "No automation alternative exists for a wall; "
                    "do it manually.")}
    for surface in SURFACES:
        if cleaned == surface:
            return {"ok": True, "surface": surface, "refusal": None}
    return {"ok": False, "surface": None,
            "refusal": _refusal(
                "unknown-surface",
                "'%s' is not a system surface this build addresses "
                "(tray, taskbar, start, notification, settings)."
                % _clean(name))}


# ---------------------------------------------------------------------------
# Backend plumbing
# ---------------------------------------------------------------------------


def _backend(backend):
    return backend if backend is not None else default_backend()


def _read(backend, method, *args):
    """Call a listing backend method.

    Returns ``(items_or_None, note)``: ``None`` means the call
    itself failed (not an empty surface — an empty list is a
    real, listable answer). Never raises.
    """
    try:
        func = getattr(backend, method)
    except Exception as exc:  # noqa: BLE001 — degrade, never raise
        return None, "surface backend has no %s: %s" % (method, exc)
    try:
        items, note = func(*args)
    except Exception as exc:  # noqa: BLE001 — degrade, never raise
        return None, "surface read failed: %s" % exc
    if items is None:
        return None, note or "surface read returned nothing"
    return items, note or ""


def _read_tree(backend, method, *args):
    """Call a tree backend method returning (items, stamp, note).

    Returns ``(items_or_None, stamp, note)``. Never raises.
    """
    try:
        func = getattr(backend, method)
    except Exception as exc:  # noqa: BLE001 — degrade, never raise
        return None, None, "surface backend has no %s: %s" % (method, exc)
    try:
        items, stamp, note = func(*args)
    except Exception as exc:  # noqa: BLE001 — degrade, never raise
        return None, None, "surface read failed: %s" % exc
    if items is None:
        return None, stamp, note or "surface read returned nothing"
    return items, stamp, note or ""


def _invoke(backend, method, *args):
    """Call an action backend method.

    Returns ``(invoked, verified, note)``. Never raises.
    """
    try:
        func = getattr(backend, method)
    except Exception as exc:  # noqa: BLE001 — degrade, never raise
        return False, False, "surface backend has no %s: %s" % (method, exc)
    try:
        invoked, verified, note = func(*args)
    except Exception as exc:  # noqa: BLE001 — degrade, never raise
        return False, False, "surface action failed: %s" % exc
    return bool(invoked), bool(verified), note or ""


def _unavailable(surface, action, note):
    return _fail("surface-unavailable",
                 "that system surface isn't available to automation "
                 "right now%s — perform the step manually."
                 % ((" (%s)" % note) if note else ""),
                 surface, action)


def _unexposed(surface, action, what, note):
    return _fail("surface-unexposed",
                 "that %s isn't exposed to automation%s — use the "
                 "system's own controls manually."
                 % (what, (" (%s)" % note) if note else ""),
                 surface, action)


def _prove_target(surface, target_id, name, fresh_candidate,
                  handle_id=None):
    """Prove a system target against one fresh candidate.

    ``fresh_candidate`` is ``{"identity", "usable", "name"}``
    built from the fresh listing entry — never from caller
    input alone. When ``handle_id`` names a handle minted from
    an EARLIER listing, the item-1 engine must re-prove that
    handle against the fresh candidate (the real consent →
    execution split); otherwise a handle is minted from the
    fresh entry and resolved against it (same-call audit
    handle — see the module docstring, not a consent proof).
    Returns ``(handle_id_or_None, refusal_or_None)``.
    """
    from . import target_handles as _th
    if handle_id:
        resolved = _th.resolve(handle_id,
                               {"candidates": [fresh_candidate]})
        if not resolved.get("ok"):
            return None, resolved.get("refusal")
        return handle_id, None
    handle = _th.mint_control(
        {"automation_id": "%s:%s" % (surface, target_id),
         "title": name},
        name=name, control_type=surface)
    resolved = _th.resolve(handle.get("id"),
                           {"candidates": [fresh_candidate]})
    if not resolved.get("ok"):
        return None, resolved.get("refusal")
    return handle.get("id"), None


def _candidate(surface, target_id, name, usable=True):
    return {"identity": {"automation_id": "%s:%s" % (surface, target_id),
                         "title": name},
            "usable": usable, "name": name}


def _match_exact(items, target, id_field, name_fields):
    """Match a caller target by id, else by exact name.

    Returns ``(entry_or_None, ambiguity_or_None)``: ambiguity is
    the list of matches when more than one name matches (never
    picks the first). Position is never a match key.
    """
    target = _clean(target)
    if not target:
        return None, None
    for entry in items:
        if not isinstance(entry, dict):
            continue
        if _clean(entry.get(id_field)) == target:
            return entry, None
    matches = [entry for entry in items
               if isinstance(entry, dict)
               and any(_clean_name(entry.get(field)) == _clean_name(target)
                       for field in name_fields)]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, matches
    return None, None


def _ambiguous_result(surface, action, matches, what, id_field,
                      name_fields):
    options = "; ".join(
        "'%s' (%s %s)" % (
            _clean_name(entry.get(name_fields[0])) or "?",
            id_field,
            _clean(entry.get(id_field)) or "?")
        for entry in matches)
    return _fail("target-ambiguous",
                 "several %s match — %s — name one exactly "
                 "instead of picking by position."
                 % (what, options),
                 surface, action)


# ---------------------------------------------------------------------------
# Tray
# ---------------------------------------------------------------------------


def _project_tray(entry):
    return {"icon_id": _clean(entry.get("icon_id")),
            "app": _clean(entry.get("app")),
            "title": _clean(entry.get("title")),
            "has_menu": bool(entry.get("has_menu"))}


def list_tray_icons(*, backend=None):
    """List tray icons (projections only — no bodies, no pixels)."""
    backend = _backend(backend)
    items, note = _read(backend, "tray_icons")
    if items is None:
        return _unavailable("tray", "listed", note)
    return _ok("tray", "listed",
               items=[_project_tray(entry) for entry in items])


def _resolve_tray_icon(target, *, backend):
    """Resolve a tray target against a fresh listing.

    Returns ``(entry, error_result)``. Ambiguous icons
    (same app, several icons) list and ask — never pick one.
    """
    items, note = _read(backend, "tray_icons")
    if items is None:
        return None, _unavailable("tray", "resolved", note)
    entry, ambiguous = _match_exact(items, target, "icon_id",
                                    ("app", "title"))
    if ambiguous is not None:
        return None, _fail(
            "tray-ambiguous",
            "several tray icons belong to the same app — %s — "
            "name the icon by its title or id exactly."
            % "; ".join("'%s' (id %s)" % (
                _clean_name(item.get("title")) or "?",
                _clean(item.get("icon_id")) or "?")
                for item in ambiguous),
            "tray", "resolved")
    if entry is None:
        return None, _fail(
            "icon-not-found",
            "no tray icon matches '%s' — list the tray icons "
            "and name one exactly." % _clean(target),
            "tray", "resolved")
    return entry, None


def open_tray_menu(icon, *, backend=None, target_handle_id=None):
    """Open a tray icon's menu and list its root items.

    Tray menus ride the menu engine's rules from here on: the
    roots below are walked by :func:`invoke_tray_menu_path`
    with exact-name matching only.
    """
    backend = _backend(backend)
    entry, error = _resolve_tray_icon(icon, backend=backend)
    if error is not None:
        return dict(error, action="menu-opened")
    if not entry.get("has_menu"):
        return _unexposed("tray", "menu-opened", "tray menu",
                          "icon '%s' exposes no menu"
                          % _clean(entry.get("title")))
    roots, stamp, note = _read_tree(backend, "tray_menu",
                                    _clean(entry.get("icon_id")))
    if roots is None:
        return _unavailable("tray", "menu-opened", note)
    if not roots:
        return _unexposed("tray", "menu-opened", "tray menu",
                          note or "menu exposed no items")
    handle_id, refusal = _prove_target(
        "tray", _clean(entry.get("icon_id")),
        _clean(entry.get("title")),
        _candidate("tray", _clean(entry.get("icon_id")),
                   _clean(entry.get("title"))),
        handle_id=target_handle_id)
    if refusal is not None:
        return _fail(refusal.get("code"), refusal.get("reason"),
                     "tray", "menu-opened")
    return _ok("tray", "menu-opened",
               target=_clean(entry.get("title")),
               handle_id=handle_id,
               icon_id=_clean(entry.get("icon_id")),
               tree_stamp=stamp,
               items=[_project_menu_node(node) for node in roots])


def _project_menu_node(node):
    return {"name": _clean_name(node.get("name")),
            "control_type": node.get("control_type") or "MenuItem",
            "automation_id": node.get("automation_id"),
            "enabled": node.get("enabled"),
            "checked": node.get("checked"),
            "has_children": bool(node.get("has_children"))}


def invoke_tray_menu_path(icon, path, *, backend=None, confirmed=False,
                          effectful=None, expected_tree_stamp=None,
                          target_handle_id=None):
    """Walk and invoke one tray-menu path (menu-engine rules).

    ``path`` is a list of names or a ``"Open → Settings"``
    string. Intermediate segments expand one level at a time
    against freshly-expanded children; the leaf invokes via
    its exposed pattern, or opens when it is itself a submenu.
    A disabled item refuses naming the item; an ambiguous
    segment lists every match; a rebuilt tree refuses on a
    stamp change; a destructive leaf (or any leaf when the
    caller declares ``effectful=True``) refuses until
    ``confirmed=True``.
    """
    from . import menu_paths as _mp
    backend = _backend(backend)
    segments = _mp.parse_path(path)
    if not segments:
        return _fail("empty-path",
                     "no tray menu path was named — name it "
                     "(for example, Open → Settings).",
                     "tray", "menu-invoked")
    opened = open_tray_menu(icon, backend=backend,
                            target_handle_id=target_handle_id)
    if not opened.get("ok"):
        return dict(opened, action="menu-invoked")
    if (expected_tree_stamp is not None
            and opened.get("tree_stamp") is not None
            and expected_tree_stamp != opened["tree_stamp"]):
        return _fail("menu-rebuilt",
                     "the tray menu was rebuilt since it was "
                     "listed (tree stamp changed) — the listed "
                     "path is no longer the live menu, so "
                     "nothing is invoked from it.",
                     "tray", "menu-invoked")
    roots, _stamp, note = _read_tree(backend, "tray_menu",
                                     opened["icon_id"])
    if roots is None or not roots:
        return _unexposed("tray", "menu-invoked", "tray menu",
                          note or "menu exposed no items")
    current = roots
    walked = []
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        matches = [node for node in current
                   if isinstance(node, dict)
                   and _clean_name(node.get("name")) == segment]
        if not matches:
            siblings = ", ".join(
                "'%s'" % _clean_name(node.get("name"))
                for node in current[:10]
                if isinstance(node, dict)
                and _clean_name(node.get("name")))
            return _fail("item-not-found",
                         "'%s' is not in this tray menu level%s — "
                         "the item's text may have changed; "
                         "nothing is fuzzy-matched to a different "
                         "command."
                         % (segment,
                            (" (items here: %s)" % siblings)
                            if siblings else ""),
                         "tray", "menu-invoked")
        if len(matches) > 1:
            options = "; ".join(
                "'%s' (type %s%s)" % (
                    _clean_name(node.get("name")),
                    node.get("control_type") or "MenuItem",
                    (", automation id %s" % node.get("automation_id"))
                    if node.get("automation_id") else "")
                for node in matches)
            return _fail("item-ambiguous",
                         "several tray menu items are named '%s' "
                         "— %s — name one exactly."
                         % (segment, options),
                         "tray", "menu-invoked")
        node = matches[0]
        if node.get("enabled") is False:
            return _fail("item-disabled",
                         "'%s' is disabled in this tray menu — "
                         "nothing is skipped to another entry."
                         % segment,
                         "tray", "menu-invoked")
        walked.append(segment)
        if not last:
            children, _new_stamp, note = _read_tree(
                backend, "expand_tray", node)
            if children is None:
                return _unavailable("tray", "menu-invoked", note)
            if not children and node.get("has_children"):
                return _fail("menu-rebuilt",
                             "the tray menu changed while walking "
                             "to '%s' — nothing further is invoked "
                             "from the stale walk." % segment,
                             "tray", "menu-invoked")
            current = children
            continue
        # Leaf.
        if node.get("has_children"):
            children, _s, note = _read_tree(backend, "expand_tray",
                                            node)
            if children is None:
                return _unavailable("tray", "menu-invoked", note)
            return _ok("tray", "menu-invoked",
                       target=" → ".join(walked),
                       handle_id=opened.get("target_handle_id"),
                       action_detail="opened",
                       verification=None,
                       items=[_project_menu_node(item)
                              for item in children])
        if _mp.is_destructive_leaf(segment) or effectful:
            if not confirmed:
                return _fail("needs-confirmation",
                             "'%s' changes state, so it needs "
                             "your confirmation first — confirm "
                             "and retry." % segment,
                             "tray", "menu-invoked",
                             instruction="Confirm this action "
                             "explicitly, then retry.")
        invoked, verified, note = _invoke(backend, "invoke_tray", node)
        if not invoked:
            return _fail("invoke-failed",
                         "the tray menu item '%s' did not invoke"
                         "%s." % (segment,
                                   (" (%s)" % note) if note else ""),
                         "tray", "menu-invoked")
        return _ok("tray", "menu-invoked",
                   target=" → ".join(walked),
                   handle_id=opened.get("target_handle_id"),
                   verification=("verified" if verified else
                                 "invoked-without-verification"))


# ---------------------------------------------------------------------------
# Taskbar
# ---------------------------------------------------------------------------


def _project_taskbar(entry):
    return {"button_id": _clean(entry.get("button_id")),
            "app": _clean(entry.get("app")),
            "title": _clean(entry.get("title")),
            "pinned": bool(entry.get("pinned")),
            "running": bool(entry.get("running"))}


def list_taskbar_buttons(*, backend=None):
    backend = _backend(backend)
    items, note = _read(backend, "taskbar_buttons")
    if items is None:
        return _unavailable("taskbar", "listed", note)
    return _ok("taskbar", "listed",
               items=[_project_taskbar(entry) for entry in items])


def _resolve_taskbar_button(target, *, backend):
    items, note = _read(backend, "taskbar_buttons")
    if items is None:
        return None, _unavailable("taskbar", "resolved", note)
    entry, ambiguous = _match_exact(items, target, "button_id",
                                    ("app", "title"))
    if ambiguous is not None:
        return None, _ambiguous_result(
            "taskbar", "resolved", ambiguous, "taskbar buttons",
            "button_id", ("app", "title"))
    if entry is None:
        return None, _fail(
            "button-not-found",
            "no taskbar button matches '%s' — list the taskbar "
            "buttons and name one exactly." % _clean(target),
            "taskbar", "resolved")
    return entry, None


def _taskbar_action(target, method, action, *, backend, confirmed,
                    effectful, require_running, require_pinned,
                    refuse_if_pinned, target_handle_id=None):
    backend = _backend(backend)
    entry, error = _resolve_taskbar_button(target, backend=backend)
    if error is not None:
        return dict(error, action=action)
    if require_running and not entry.get("running"):
        return _fail("button-not-running",
                     "'%s' is pinned but not running — launch it "
                     "instead of switching to it."
                     % _clean(entry.get("title")),
                     "taskbar", action)
    if require_pinned and not entry.get("pinned"):
        return _fail("button-not-pinned",
                     "'%s' is not pinned to the taskbar — pin it "
                     "first, or launch it from Start."
                     % _clean(entry.get("title")),
                     "taskbar", action)
    if refuse_if_pinned and entry.get("pinned"):
        return _fail("already-pinned",
                     "'%s' is already pinned to the taskbar — "
                     "nothing further is done."
                     % _clean(entry.get("title")),
                     "taskbar", action)
    if effectful and not confirmed:
        return _fail("needs-confirmation",
                     "pinning '%s' to the taskbar changes system "
                     "state, so it needs your confirmation first "
                     "— confirm and retry."
                     % _clean(entry.get("title")),
                     "taskbar", action,
                     instruction="Confirm this action explicitly, "
                     "then retry.")
    handle_id, refusal = _prove_target(
        "taskbar", _clean(entry.get("button_id")),
        _clean(entry.get("title")),
        _candidate("taskbar", _clean(entry.get("button_id")),
                   _clean(entry.get("title"))),
        handle_id=target_handle_id)
    if refusal is not None:
        return _fail(refusal.get("code"), refusal.get("reason"),
                     "taskbar", action)
    invoked, verified, note = _invoke(backend, method,
                                      _clean(entry.get("button_id")))
    if not invoked:
        return _fail("invoke-failed",
                     "the taskbar action '%s' did not complete"
                     "%s." % (action, (" (%s)" % note) if note else ""),
                     "taskbar", action)
    return _ok("taskbar", action,
               target=_clean(entry.get("title")),
               handle_id=handle_id,
               verification=("verified" if verified else
                             "invoked-without-verification"))


def switch_taskbar(target, *, backend=None, target_handle_id=None):
    """Switch to a running taskbar button (exposed control)."""
    return _taskbar_action(target, "activate_taskbar", "switched",
                           backend=_backend(backend), confirmed=True,
                           effectful=False, require_running=True,
                           require_pinned=False, refuse_if_pinned=False,
                           target_handle_id=target_handle_id)


def launch_taskbar(target, *, backend=None, target_handle_id=None):
    """Launch a pinned taskbar button (exposed control)."""
    return _taskbar_action(target, "launch_taskbar", "launched",
                           backend=_backend(backend), confirmed=True,
                           effectful=False, require_running=False,
                           require_pinned=True, refuse_if_pinned=False,
                           target_handle_id=target_handle_id)


def pin_taskbar(target, *, backend=None, confirmed=False,
                target_handle_id=None):
    """Pin a taskbar button — effectful, confirm-gated."""
    return _taskbar_action(target, "pin_taskbar", "pinned",
                           backend=_backend(backend),
                           confirmed=confirmed, effectful=True,
                           require_running=False, require_pinned=False,
                           refuse_if_pinned=True,
                           target_handle_id=target_handle_id)


# ---------------------------------------------------------------------------
# Default backend: honest unavailability (live wiring field-owed)
# ---------------------------------------------------------------------------


class _DefaultBackend:
    """Default backend: every surface honestly unavailable.

    No shell walker exists in this tree (no tray-overflow,
    taskbar, Start, notification-centre, or settings walker
    has been written or run), so this backend fabricates
    nothing: every listing is unavailable-with-note and every action
    fails with a manual path. Live Windows wiring — real
    shell UIA roots, real Invoke patterns, real consequence
    verification — is field work, not claimed here. Importing
    this module needs only the standard library.
    """

    _NOTE = ("system surfaces are not wired to live automation "
             "in this build — perform the step manually")

    def tray_icons(self):
        return None, self._NOTE

    def tray_menu(self, icon_id):
        return None, None, self._NOTE

    def expand_tray(self, node):
        return None, None, self._NOTE

    def invoke_tray(self, node):
        return False, False, self._NOTE

    def taskbar_buttons(self):
        return None, self._NOTE

    def activate_taskbar(self, button_id):
        return False, False, self._NOTE

    def launch_taskbar(self, button_id):
        return False, False, self._NOTE

    def pin_taskbar(self, button_id):
        return False, False, self._NOTE

    def start_search(self, query):
        return None, self._NOTE

    def launch_start(self, target):
        return False, False, self._NOTE

    def notifications(self):
        return None, self._NOTE

    def act_notification(self, notification_id, action):
        return False, False, self._NOTE

    def settings_pages(self):
        return None, self._NOTE

    def open_settings(self, page_id):
        return False, False, self._NOTE


def default_backend():
    """A fresh default backend (stateless)."""
    return _DefaultBackend()
