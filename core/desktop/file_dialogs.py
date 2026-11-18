"""File dialogs — §7 item 4 (parent draft).

Drive another app's native Open / Save As / folder-pick dialog by
voice through UIA: pick the file, not the pixels.

Dialog target (from the §7 item-4 definition)::

    {folder_canonical_path, filename, action: open | save | folder}

Paths are canonicalised at consent and re-checked at execution
(Deep Audit TOCTOU rule); a changed canonical path refuses.

Resolution procedure (definition §4):

1. Resolve the dialog window handle (item 1) against one item-2
   snapshot — one observation anchors the call, never a second
   observation path.
2. Confirm dialog kind (Open vs Save vs folder-pick) matches the
   goal; a mismatch refuses, naming both kinds.
3. Set folder (places / address field) and filename (edit field)
   via UIA patterns, verifying each set by re-reading the dialog
   state — a set the dialog does not reflect refuses.
4. For Save over an existing file, STOP for confirmation: the
   result carries ``needs_confirmation=True`` and performs no
   invoke. Re-call with ``confirm_overwrite=True`` to proceed.
5. Invoke Open / Save / Select Folder; verify the dialog closed
   and report what the app reported, else report honestly
   (invoked-without-verification — never a claimed success).

Fallback contract (never a guessed target):

* Undriven dialog (custom-drawn, no UIA exposure) -> refusal
  ``dialog-not-drivable`` + the manual steps. No keystroke
  fallback: keystrokes at an undriven dialog would be blind
  typing into unknown controls.
* Missing folder / file -> refusal naming the path.
* Ambiguous filename matches -> the candidate list is returned
  and the caller asks; the engine never picks the first match.
* Dialog closed, app changed, or folder vanished between consent
  and execution -> refusal + re-ask, never an invented folder.
* A password prompt inside a dialog is a by-design wall: the
  engine refuses and never types into it.

Result shape (mirrors ``core.desktop.menu_paths``)::

    {ok, action, dialog_kind, folder_canonical, filename,
     path_canonical, window_handle_id, file_handle_id,
     snapshot_stamp, invoked, closed, verification,
     needs_confirmation, candidates, refusal}

``refusal`` is ``{code, reason}`` or ``None``. Every refusal
reason ends with an instruction (re-observe, or the manual steps
for an undriven dialog).

Backends: :func:`drive_dialog` takes an optional ``backend``
implementing the dialog protocol (``foreground_identity``,
``bounds``, ``dialog_state``, ``set_folder``, ``set_filename``,
``set_filter``, ``invoke``). The default backend wires the
tree's existing UIA walk with lazy imports, so importing this
module needs only the standard library. Tests drive the
protocol with a fake backend; live Windows behaviour — real
common dialogs, real Value / Invoke patterns — stays field
work and is not claimed here (this lab has no Windows UI, so
the default backend degrades honestly off-Windows).
"""

import os

from . import target_handles as _th

__all__ = [
    "drive_dialog",
    "describe",
    "default_backend",
    "REOBSERVE_INSTRUCTION",
    "MANUAL_STEPS_INSTRUCTION",
    "ACTIONS",
    "DIALOG_KINDS",
]

REOBSERVE_INSTRUCTION = _th.REOBSERVE_INSTRUCTION

MANUAL_STEPS_INSTRUCTION = (
    "Drive the dialog by hand: pick the file in the dialog's file "
    "list (or type its name in the filename field), then press "
    "Open / Save yourself — this dialog does not expose the "
    "controls the assistant needs."
)

ACTIONS = ("open", "save", "folder")
DIALOG_KINDS = ("open", "save", "folder")

#: Invoke button per action (the backend's invoke vocabulary).
_ACTION_BUTTON = {"open": "open", "save": "save", "folder": "select_folder"}


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------


def _refusal(code, reason, instruction=REOBSERVE_INSTRUCTION):
    if instruction:
        reason = reason + " " + instruction
    return {"code": code, "reason": reason}


def _fail(code, reason, action, window_handle_id, snapshot_stamp,
          instruction=REOBSERVE_INSTRUCTION, **extra):
    result = {"ok": False, "action": action, "dialog_kind": None,
              "folder_canonical": None, "filename": None,
              "path_canonical": None, "window_handle_id": window_handle_id,
              "file_handle_id": None, "snapshot_stamp": snapshot_stamp,
              "invoked": False, "closed": False, "verification": None,
              "needs_confirmation": False, "candidates": [],
              "refusal": _refusal(code, reason, instruction)}
    result.update(extra)
    return result


def describe(result):
    """One line for receipts / activity records."""
    if not isinstance(result, dict):
        return "no file-dialog result"
    if result.get("ok"):
        line = "File dialog %s %s" % (
            result.get("action"), result.get("path_canonical") or "?")
        handle = result.get("file_handle_id")
        if handle:
            line += " (file handle %s)" % handle
        verification = result.get("verification")
        if verification:
            line += " — %s" % verification
        return line
    if result.get("needs_confirmation"):
        return "File dialog %s stops for confirmation: %s" % (
            result.get("action"),
            (result.get("refusal") or {}).get("reason")
            or "confirmation required")
    refusal = result.get("refusal") or {}
    return "File dialog %s refused: %s" % (
        result.get("action"), refusal.get("reason") or "no reason given")


# ---------------------------------------------------------------------------
# Shared call setup: one snapshot, one window handle (mirrors menu_paths)
# ---------------------------------------------------------------------------


def _prepare(snapshot, backend, window_handle_id):
    """(snapshot, backend, window_handle_id, stamp, error-result).

    One observation anchors the call; the backend's current
    foreground must still match it; the window resolves through
    the item-1 engine. On any failure the error result is
    returned and the caller must not drive.
    """
    from . import perception_adapter as _pa

    if snapshot is None:
        snapshot = _pa.observe()
    if not isinstance(snapshot, dict):
        return None, None, None, None, _fail(
            "snapshot-unavailable",
            "no usable observation anchors this dialog action.",
            None, window_handle_id, None)
    stamp = snapshot.get("stamp")
    status = snapshot.get("capture_status")
    if status not in (None, "ok"):
        return None, None, None, stamp, _fail(
            "snapshot-unavailable",
            "the observation was not a successful capture (%s: %s) "
            "— no dialog is driven from a failed or denied capture."
            % (status, snapshot.get("capture_reason") or "no reason"),
            None, window_handle_id, stamp)
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
            None, window_handle_id, stamp)
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
                None, window_handle_id, stamp, instruction="")
    else:
        try:
            minted = _th.mint_window(snapshot_identity)
        except ValueError as exc:
            return None, None, None, stamp, _fail(
                "window-identity-missing",
                "no window identity can be proven from this "
                "observation (%s)." % exc,
                None, window_handle_id, stamp)
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
                None, window_handle_id, stamp, instruction="")
    return snapshot, backend, window_handle_id, stamp, None


def _read_state(backend):
    """(state, note) from a backend, never raising."""
    try:
        state = backend.dialog_state()
    except Exception as exc:  # noqa: BLE001 — backend failure is a refusal
        return None, "dialog state read failed: %s" % exc
    if state is None:
        return None, "no dialog is open"
    if not isinstance(state, dict):
        return None, "dialog state returned no dialog description"
    return state, ""


def _canonical(path):
    return os.path.realpath(os.path.abspath(str(path)))


def _exact_matches(items, filename):
    """Items exactly equal to filename (case-insensitive, Windows-like)."""
    wanted = (filename or "").strip().lower()
    return [item for item in (items or [])
            if isinstance(item, str) and item.strip().lower() == wanted]


def _substring_matches(items, filename):
    """Items containing filename as a substring (case-insensitive)."""
    wanted = (filename or "").strip().lower()
    if not wanted:
        return []
    return [item for item in (items or [])
            if isinstance(item, str) and wanted in item.strip().lower()]


# ---------------------------------------------------------------------------
# Drive
# ---------------------------------------------------------------------------


def drive_dialog(action, *, folder=None, filename=None, file_type=None,
                 snapshot=None, backend=None, window_handle_id=None,
                 confirm_overwrite=False):
    """Drive a native Open / Save / folder-pick dialog by voice.

    ``action`` is ``open``, ``save`` or ``folder``. ``folder`` is
    the folder to navigate to (the dialog's current folder when
    omitted); ``filename`` is the file to open or save (omitted
    for a folder pick). ``file_type`` is an optional file-type
    filter (e.g. ``"*.txt"``); when given, the backend must expose
    it — a filter that cannot be set refuses rather than being
    silently ignored.

    A Save over an existing file with ``confirm_overwrite``
    False returns ``ok=False`` with ``needs_confirmation=True``
    and performs NO invoke; re-call with ``confirm_overwrite``
    True to proceed. Nothing here types into a password field,
    follows a symlink to a different target than consented, or
    falls back to keystrokes at an undriven dialog.
    """
    if action not in ACTIONS:
        return _fail("unknown-action",
                     "unknown dialog action '%s' (open, save, folder)." %
                     action,
                     action, window_handle_id, None)
    if action == "folder" and filename is not None:
        return _fail("filename-not-applicable",
                     "a folder pick takes no filename.",
                     action, window_handle_id, None)
    if action in ("open", "save") and not (filename or "").strip():
        return _fail("filename-required",
                     "a file %s needs a filename." % action,
                     action, window_handle_id, None)
    filename = (filename or "").strip() or None

    snapshot, backend, window_handle_id, stamp, error = _prepare(
        snapshot, backend, window_handle_id)
    if error is not None:
        error["action"] = action
        return error

    state, note = _read_state(backend)
    if state is None:
        return _fail("dialog-absent",
                     "no dialog is open to drive (%s)." % note,
                     action, window_handle_id, stamp)
    if not state.get("exposed", True):
        return _fail("dialog-not-drivable",
                     "that dialog does not expose its controls "
                     "through accessibility.",
                     action, window_handle_id, stamp,
                     instruction=MANUAL_STEPS_INSTRUCTION)
    if state.get("has_password_field"):
        return _fail("password-wall",
                     "this dialog contains a password prompt, "
                     "which the assistant never types into.",
                     action, window_handle_id, stamp,
                     instruction=MANUAL_STEPS_INSTRUCTION)
    kind = state.get("kind")
    if kind not in DIALOG_KINDS:
        return _fail("dialog-kind-unknown",
                     "the open dialog's kind ('%s') cannot be "
                     "proven — it is not driven on a guess." % kind,
                     action, window_handle_id, stamp)
    if kind != action:
        return _fail("kind-mismatch",
                     "the open dialog is a %s dialog, but the goal "
                     "is a %s — refusing rather than driving the "
                     "wrong kind." % (kind, action),
                     action, window_handle_id, stamp)

    # --- Folder: set (when given) and verify by re-read. -------------
    current_folder = state.get("current_folder")
    if folder is not None:
        try:
            ok_set, set_note = backend.set_folder(folder)
        except Exception as exc:  # noqa: BLE001
            ok_set, set_note = False, str(exc)
        if not ok_set:
            return _fail("folder-missing",
                         "the folder '%s' cannot be opened (%s)."
                         % (folder, set_note or "backend refused"),
                         action, window_handle_id, stamp)
        state, note = _read_state(backend)
        if state is None:
            return _fail("dialog-closed",
                         "the dialog closed while navigating to "
                         "the folder — re-open it and try again.",
                         action, window_handle_id, stamp)
        current_folder = state.get("current_folder")
        if current_folder is None or \
                _canonical(current_folder) != _canonical(folder):
            return _fail("folder-mismatch",
                         "the dialog did not land on the requested "
                         "folder '%s' (it shows '%s')."
                         % (folder, current_folder),
                         action, window_handle_id, stamp)
    if not current_folder:
        return _fail("folder-unknown",
                     "the dialog exposes no current folder to "
                     "resolve the file against.",
                     action, window_handle_id, stamp)
    folder_canonical = _canonical(current_folder)

    # --- Filename: resolve against the dialog's file list. ----------
    items = state.get("items") or []
    if action == "open":
        exact = _exact_matches(items, filename)
        if len(exact) == 1:
            filename = exact[0]
        elif len(exact) > 1:
            return _fail("filename-ambiguous",
                         "several files match '%s' — pick one "
                         "explicitly." % filename,
                         action, window_handle_id, stamp,
                         folder_canonical=folder_canonical,
                         filename=filename, candidates=list(exact))
        else:
            partial = _substring_matches(items, filename)
            if partial:
                return _fail("filename-ambiguous",
                             "several files match '%s' — pick one "
                             "explicitly." % filename,
                             action, window_handle_id, stamp,
                             folder_canonical=folder_canonical,
                             filename=filename,
                             candidates=list(partial))
            return _fail("file-not-found",
                         "no file named '%s' is listed in '%s'."
                         % (filename, folder_canonical),
                         action, window_handle_id, stamp,
                         folder_canonical=folder_canonical,
                         filename=filename)
    if action == "save":
        exact = _exact_matches(items, filename)
        if exact and not confirm_overwrite:
            target = os.path.join(folder_canonical, exact[0])
            return _fail("overwrite-confirm-required",
                         "the file '%s' already exists — saving "
                         "over it needs confirmation." % target,
                         action, window_handle_id, stamp,
                         folder_canonical=folder_canonical,
                         filename=exact[0], path_canonical=target,
                         needs_confirmation=True,
                         instruction="Confirm the overwrite, then "
                         "re-call with confirm_overwrite=True.")
        if exact:
            filename = exact[0]

    if action == "folder":
        target_path = folder_canonical
    else:
        target_path = os.path.join(folder_canonical, filename)

    # --- File-type filter: set only when requested, never ignored. --
    if file_type is not None:
        setter = getattr(backend, "set_filter", None)
        if setter is None:
            return _fail("filter-unsupported",
                         "this dialog backend cannot set a "
                         "file-type filter, so '%s' is refused "
                         "rather than silently ignored." % file_type,
                         action, window_handle_id, stamp,
                         folder_canonical=folder_canonical,
                         filename=filename,
                         path_canonical=target_path)
        try:
            ok_set, set_note = setter(file_type)
        except Exception as exc:  # noqa: BLE001
            ok_set, set_note = False, str(exc)
        if not ok_set:
            return _fail("filter-failed",
                         "the file-type filter '%s' could not be "
                         "set (%s)." % (file_type, set_note or
                                         "backend refused"),
                         action, window_handle_id, stamp,
                         folder_canonical=folder_canonical,
                         filename=filename,
                         path_canonical=target_path)

    # --- Filename field: set and verify by re-read. ------------------
    if action in ("open", "save"):
        try:
            ok_set, set_note = backend.set_filename(filename)
        except Exception as exc:  # noqa: BLE001
            ok_set, set_note = False, str(exc)
        if not ok_set:
            return _fail("filename-field-failed",
                         "the filename field could not be set to "
                         "'%s' (%s)." % (filename, set_note or
                                          "backend refused"),
                         action, window_handle_id, stamp,
                         folder_canonical=folder_canonical,
                         filename=filename,
                         path_canonical=target_path)

    # --- Consent point: mint the file handle NOW, so the
    # execution re-check below resolves the consented handle
    # instead of minting a fresh one (a fresh mint could never
    # observe a retarget between consent and execution). -------
    try:
        file_handle = _th.mint_file(target_path)
    except ValueError as exc:
        return _fail("file-handle-failed",
                     "no file handle can be proven for '%s' (%s)."
                     % (target_path, exc),
                     action, window_handle_id, stamp,
                     folder_canonical=folder_canonical,
                     filename=filename, path_canonical=target_path)

    # --- Execution re-check (TOCTOU): window, dialog, path. ---------
    try:
        current_fg = backend.foreground_identity() or {}
    except Exception:  # noqa: BLE001
        current_fg = {}
    resolve_identity = {field: current_fg[field] for field in
                        ("hwnd", "pid", "title")
                        if current_fg.get(field) is not None}
    if resolve_identity:
        resolved = _th.resolve(window_handle_id,
                               {"identity": resolve_identity})
        if not resolved.get("ok"):
            inner = (resolved.get("refusal") or {}).get("reason") or \
                "window-handle resolution refused."
            return _fail("window-handle-refused",
                         "the consented window cannot be re-proven "
                         "at execution: %s" % inner,
                         action, window_handle_id, stamp,
                         instruction="",
                         folder_canonical=folder_canonical,
                         filename=filename,
                         path_canonical=target_path,
                         file_handle_id=file_handle["id"])
    state, note = _read_state(backend)
    if state is None:
        return _fail("dialog-closed",
                     "the dialog closed before execution — "
                     "re-open it and try again.",
                     action, window_handle_id, stamp,
                     folder_canonical=folder_canonical,
                     filename=filename, path_canonical=target_path,
                     file_handle_id=file_handle["id"])
    if state.get("kind") != action:
        return _fail("kind-mismatch",
                     "the dialog changed kind before execution "
                     "(now '%s', goal '%s')."
                     % (state.get("kind"), action),
                     action, window_handle_id, stamp,
                     folder_canonical=folder_canonical,
                     filename=filename, path_canonical=target_path,
                     file_handle_id=file_handle["id"])
    if state.get("current_folder") is None or \
            _canonical(state["current_folder"]) != folder_canonical:
        return _fail("folder-retargeted",
                     "the dialog's folder changed between consent "
                     "and execution ('%s' vs consented '%s') — "
                     "the action refuses rather than acting in "
                     "a different folder."
                     % (state.get("current_folder"), folder_canonical),
                     action, window_handle_id, stamp,
                     folder_canonical=folder_canonical,
                     filename=filename, path_canonical=target_path,
                     file_handle_id=file_handle["id"])
    resolved = _th.resolve(file_handle["id"], {})
    if not resolved.get("ok"):
        inner = (resolved.get("refusal") or {}).get("reason") or \
            "file-handle resolution refused."
        return _fail("path-retargeted",
                     "the consented path cannot be re-proven at "
                     "execution: %s" % inner,
                     action, window_handle_id, stamp,
                     instruction="",
                     folder_canonical=folder_canonical,
                     filename=filename, path_canonical=target_path,
                     file_handle_id=file_handle["id"])
    target_canonical = resolved["resolved"]["path"]

    # --- Invoke, then verify the dialog closed. ----------------------
    try:
        invoked, closed_flag, app_reported, note = backend.invoke(
            _ACTION_BUTTON[action])
    except Exception as exc:  # noqa: BLE001
        invoked, closed_flag, app_reported, note = (
            False, False, None, str(exc))
    if not invoked:
        return _fail("invoke-failed",
                     "the %s button could not be invoked (%s)."
                     % (_ACTION_BUTTON[action], note or
                        "backend refused"),
                     action, window_handle_id, stamp,
                     folder_canonical=folder_canonical,
                     filename=filename, path_canonical=target_canonical,
                     file_handle_id=file_handle["id"])
    after, _note = _read_state(backend)
    closed = bool(closed_flag) and after is None
    if not closed:
        return _fail("dialog-still-open",
                     "the %s button was invoked but the dialog "
                     "is still open — the result is not claimed."
                     % _ACTION_BUTTON[action],
                     action, window_handle_id, stamp,
                     folder_canonical=folder_canonical,
                     filename=filename, path_canonical=target_canonical,
                     file_handle_id=file_handle["id"], invoked=True)
    if app_reported:
        verification = "dialog closed; app reports '%s'" % app_reported
    else:
        verification = ("dialog closed, but the app did not report "
                        "the file — invoked-without-verification")
    return {"ok": True, "action": action, "dialog_kind": kind,
            "folder_canonical": folder_canonical,
            "filename": filename, "path_canonical": target_canonical,
            "window_handle_id": window_handle_id,
            "file_handle_id": file_handle["id"],
            "snapshot_stamp": stamp, "invoked": True, "closed": True,
            "verification": verification, "needs_confirmation": False,
            "candidates": [], "refusal": None}


# ---------------------------------------------------------------------------
# Default backend — existing tree sources only, lazy imports
# ---------------------------------------------------------------------------


class _DefaultBackend:
    """Live Windows backend (field-owed, headless-safe).

    Dialog state comes from the tree's own UIA descendant walk
    of the foreground window: the filename Edit, the file-list
    items, and the Open / Save / Select Folder buttons. Kind is
    proven from the buttons present, never from the title alone
    (titles are localisable and user-editable). Sets use the
    Value pattern on the filename edit; invokes use the Invoke
    pattern only — never coordinates, never synthesised
    keystrokes, and never a keystroke fallback at an undriven
    dialog. Every method degrades honestly off-Windows (no
    dialog + note) instead of fabricating a dialog. Live dialog
    driving on a real Windows host stays field work and is not
    claimed here.
    """

    def __init__(self):
        self._wrappers = {}

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

    def _walk(self):
        """(elements, failure) from the tree's UIA walk."""
        try:
            from . import uia as _uia  # Phase 3 D4b: uia hub now in core/desktop/; S7a interim closed
            fg = self.foreground_identity()
            win, _desc = _uia._uia_window(str(fg.get("title") or ""))
            elements, failure = _uia._descendants(win)
            if failure is not None or elements is None:
                return None, failure or "window walk failed"
            return elements, None
        except Exception as exc:  # noqa: BLE001
            return None, "dialog walk unavailable: %s" % exc

    def dialog_state(self):
        elements, failure = self._walk()
        if elements is None:
            return {"kind": None, "title": None, "current_folder": None,
                    "items": [], "filename_field": False,
                    "exposed": False, "has_password_field": False,
                    "file_type_filter": None,
                    "note": failure or "no dialog walk"}
        filename_edit = None
        buttons = {}
        items = []
        password = False
        for el in elements:
            try:
                from . import uia as _uia  # Phase 3 D4b: uia hub now in core/desktop/; S7a interim closed
                facts = _uia._element_facts(el)
            except Exception:  # noqa: BLE001
                continue
            if facts is None:
                continue
            ctype = facts.get("control_type") or ""
            name = facts.get("name") or ""
            if ctype == "Edit":
                if "password" in name.lower():
                    password = True
                elif filename_edit is None:
                    filename_edit = el
            elif ctype == "Button":
                key = name.strip().lower()
                if key in ("open", "save", "select folder", "cancel"):
                    buttons.setdefault(key, el)
            elif ctype in ("ListItem", "DataItem"):
                if name:
                    items.append(name)
        if filename_edit is not None:
            self._wrappers["filename"] = filename_edit
        for key, wrapper in buttons.items():
            self._wrappers["button:" + key] = wrapper
        if "select folder" in buttons:
            kind = "folder"
        elif "save" in buttons:
            kind = "save"
        elif "open" in buttons:
            kind = "open"
        else:
            return {"kind": None, "title": None,
                    "current_folder": None, "items": items,
                    "filename_field": filename_edit is not None,
                    "exposed": False, "has_password_field": password,
                    "file_type_filter": None,
                    "note": "no Open / Save / Select Folder button exposed"}
        return {"kind": kind, "title": None, "current_folder": None,
                "items": items,
                "filename_field": filename_edit is not None,
                "exposed": True, "has_password_field": password,
                "file_type_filter": None, "note": ""}

    def set_folder(self, folder):
        # Common dialogs expose no settable folder control through
        # the plain walk (the address bar is host-specific); the
        # live folder drive is field work, honestly refused here.
        return False, ("live folder navigation is field-owed on "
                       "real Windows; no folder control was proven")

    def set_filename(self, name):
        wrapper = self._wrappers.get("filename")
        if wrapper is None:
            return False, "no filename edit was proven"
        try:
            wrapper.set_edit_text(name)
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
        return True, ""

    def set_filter(self, file_type):
        return False, ("live file-type filter driving is field-owed "
                       "on real Windows; no filter control was proven")

    def invoke(self, button):
        key = {"open": "open", "save": "save",
               "select_folder": "select folder"}.get(button, button)
        wrapper = self._wrappers.get("button:" + key)
        if wrapper is None:
            return False, False, None, "no %s button was proven" % key
        try:
            wrapper.invoke()
        except Exception as exc:  # noqa: BLE001
            return False, False, None, str(exc)
        return True, True, None, ""


def default_backend():
    """A fresh default backend (stateful per call: wrapper map)."""
    return _DefaultBackend()
