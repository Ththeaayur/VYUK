"""Pack registrations for the §7 consumers batch 1 (2026-11-02).

The §7 engines landed without consumers: menus (item 3), file
dialogs (item 4), system surfaces (item 7), and app adapters
(item 9) could resolve but nothing in the funnel could call them.
This pack gives each engine one funnel tool:

- menu_action — list a window's menus / invoke a menu path.
- file_dialog_action — drive an Open / Save / folder dialog.
- system_surface_action — tray / taskbar / Start / notification /
  settings actions.
- adapter_action — resolve a spoken phrase through a registered
  app adapter.

Resolve-only where the engine is resolve-only: no tool here
invents its own click / type / dispatch path around the engines
it calls, and file_dialog_action never falls back to keystrokes.
Handle / stamp args are EXPLICIT in this batch (the caller
supplies a window handle id and, where it has one, an item-2
snapshot dict); funnel auto-threading of handle / stamp context
is a separate follow-up and is NOT built here.

Confirm-gating (two layers, both fail-closed):
- Funnel layer: all four tools are in _CONFIRM_GATE_TOOLS, with
  args-aware branches in _needs_confirmation that free ONLY the
  pure-read paths (menu list, surface lists, Start search;
  adapter resolutions that are not confirm-gated). Every
  effectful path pops the GUI confirmation; a voice "yes" never
  counts, and no handler runs without an explicit human yes.
- Engine layer: each handler passes its confirmed flag through
  to the engine (menu confirmed, dialog confirm_overwrite,
  surface confirmed, adapter confirmed). An engine
  needs_confirmation stop returns verbatim with ZERO invokes;
  only a caller-supplied confirmed=True after GUI approval
  re-issues the call.
- The two layers are INDEPENDENT, on the run_command precedent
  (funnel popup + trust-layer confirm flag): funnel approval
  alone does NOT satisfy the engine flag. An approved call
  still stops at the engine until re-issued carrying
  confirmed=True — the coordinator consumer (a later batch)
  owns that re-issue protocol. The gate branches deliberately
  do NOT treat a caller-supplied confirmed=True as approval:
  a model-set flag must never wave itself through the popup.

New tools: 4. Registry 645 -> 649. Classifications are pinned
by the G2 snapshot (regenerated deliberately with this change).
"""

from ..desktop import file_dialogs as _fd  # Phase 3 S7a: file_dialogs moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)
from ..desktop import menu_paths as _mp  # Phase 3 S7a: menu_paths moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)
from ..desktop import perception_adapter as _pa  # Phase 3 S7a: perception_adapter moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)
from ..desktop import s7_threading as _s7t  # Phase 3 S7a-2: moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)
from ..desktop import system_surfaces as _ss  # Phase 3 S7a: system_surfaces moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)
from ..desktop import system_surfaces_shell as _sh  # Phase 3 S7a: system_surfaces_shell moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)


class S7ConsumersPacksMixin:
    def _register_s7consumers_tools(self):
        self.register(
            name="menu_action",
            description="List a window's menus, or invoke one menu path in the foreground app (for example, File → Save As). Listing is a read; invoking changes the app, so invoking asks first through the GUI confirmation. Menu refusals are reported exactly as the menu engine gives them (disabled item, ambiguous name, item not found with its siblings listed, menu rebuilt, menu not exposed, stale observation, submenu opened instead of invoked, invoked without verification).",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Either 'list' (show the menu-bar roots) or 'invoke' (walk and invoke a path)."
                    },
                    "path": {
                        "type": "string",
                        "description": "Menu path for 'invoke', for example 'File → Save As'. Not needed for 'list'."
                    },
                    "window_handle_id": {
                        "type": "string",
                        "description": "Optional item-1 window handle to re-prove. Omitted: a handle is minted from the observation."
                    },
                    "snapshot": {
                        "type": "object",
                        "description": "Optional item-2 snapshot dict anchoring the call (from a prior observation). Omitted: a fresh observation is taken."
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": "Set only after GUI approval for an effectful (send/delete/purchase-class or caller-declared effectful) leaf. Unconfirmed effectful leaves stop with needs_confirmation and invoke nothing."
                    },
                    "effectful": {
                        "type": "boolean",
                        "description": "Optional caller declaration that the leaf is effectful even when its name is not in the destructive set."
                    }
                },
                "required": ["action"]
            },
            handler=self._tool_menu_action
        )

        self.register(
            name="file_dialog_action",
            description="Drive a native Open, Save, or folder-pick dialog by voice: set the folder and filename through the dialog's exposed controls and invoke its button. Every drive asks first through the GUI confirmation. Dialog refusals are reported exactly as the dialog engine gives them (wrong dialog kind, file not found, several matches with candidates listed, overwrite needing confirmation with zero invokes, unsupported filter, password prompt wall, undriven dialog). This tool never falls back to keystrokes.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Either 'open', 'save', or 'folder' (folder pick takes no filename)."
                    },
                    "folder": {
                        "type": "string",
                        "description": "Folder to navigate the dialog to. Omitted: the dialog's current folder."
                    },
                    "filename": {
                        "type": "string",
                        "description": "File to open or save. Required for 'open' and 'save'."
                    },
                    "file_type": {
                        "type": "string",
                        "description": "Optional file-type filter, for example '*.txt'. Refused rather than silently ignored when the backend cannot set it."
                    },
                    "window_handle_id": {
                        "type": "string",
                        "description": "Optional item-1 window handle to re-prove. Omitted: a handle is minted from the observation."
                    },
                    "snapshot": {
                        "type": "object",
                        "description": "Optional item-2 snapshot dict anchoring the call. Omitted: a fresh observation is taken."
                    },
                    "confirm_overwrite": {
                        "type": "boolean",
                        "description": "Set only after GUI approval when saving over an existing file. Without it, an overwrite stops with needs_confirmation and invokes nothing."
                    }
                },
                "required": ["action"]
            },
            handler=self._tool_file_dialog_action
        )

        self.register(
            name="system_surface_action",
            description="Act on a system surface: list tray icons, taskbar buttons, notifications, or settings pages; open a tray menu and invoke a path in it; switch to, launch, or pin a taskbar button; search Start and launch a result; act on or dismiss a notification by id and confirmed title; open a settings page. Listings and Start search are reads; every other action asks first through the GUI confirmation. Surface refusals are reported exactly as the surface engine gives them (ambiguous tray icons listed, menu-less icons, pinned-only launch, already-pinned, expired/dismissed notification, title mismatch, by-design walls for lock screen, UAC, password fields, CAPTCHAs, and privilege escalation). Notifications are addressed by id plus confirmed title only — never by position.",
            parameters={
                "type": "object",
                "properties": {
                    "surface": {
                        "type": "string",
                        "description": "One of 'tray', 'taskbar', 'start', 'notification', 'settings'. Wall names (lock screen, UAC, password, CAPTCHA, secure desktop, privilege) refuse."
                    },
                    "action": {
                        "type": "string",
                        "description": "Per surface: tray 'list'/'open_menu'/'invoke_path'; taskbar 'list'/'switch'/'launch'/'pin'; start 'search'/'launch'; notification 'list'/'act'/'dismiss'; settings 'list'/'open'."
                    },
                    "target": {
                        "type": "string",
                        "description": "Exact target name or id: tray icon (app/title/id), taskbar button, Start result, settings page. Notifications use notification_id instead."
                    },
                    "path": {
                        "type": "string",
                        "description": "Tray menu path for 'invoke_path', for example 'Settings → General'."
                    },
                    "query": {
                        "type": "string",
                        "description": "Start search text for 'search'."
                    },
                    "notification_id": {
                        "type": "string",
                        "description": "Notification id for 'act'/'dismiss'. Never a position."
                    },
                    "expected_title": {
                        "type": "string",
                        "description": "Confirmed current title for 'act'/'dismiss'. A title that no longer matches refuses."
                    },
                    "detail_action": {
                        "type": "string",
                        "description": "Notification action name for 'act' (one the notification exposes; 'dismiss' is the confirm-gated one)."
                    },
                    "target_handle_id": {
                        "type": "string",
                        "description": "Optional item-1 handle minted from an earlier listing; it must re-prove against the fresh candidate or the action refuses."
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": "Set only after GUI approval for confirm-gated paths (taskbar pin, tray destructive leaves, notification dismissal). Unconfirmed gated paths stop with needs_confirmation and invoke nothing."
                    },
                    "effectful": {
                        "type": "boolean",
                        "description": "Optional caller declaration that a tray menu leaf is effectful."
                    },
                    "include_body": {
                        "type": "boolean",
                        "description": "For notification 'list': include bodies only for ids also listed in body_approved_ids. Otherwise bodies are withheld visibly, never silently empty."
                    },
                    "body_approved_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Notification ids whose bodies the user approved for 'list' with include_body."
                    }
                },
                "required": ["surface", "action"]
            },
            handler=self._tool_system_surface_action
        )

        self.register(
            name="adapter_action",
            description="Resolve a spoken phrase through the registered app adapter for the foreground app (Chrome, Explorer, VS Code, or generic controls when no adapter matches). Resolution only: it returns the menu path or dialog spec the item-3/item-4 engines would drive, and never executes anything itself. Confirm-gated adapter actions stop with needs_confirmation until confirmed through the GUI path. Receipts name the adapter and app version honestly; an unpinned version is reported as unverified, never invented. The foreground must carry a window class for class-bearing signatures (Chrome, Explorer); observed foregrounds carry process and title only, so they identify process-only adapters and fall back to generic controls otherwise.",
            parameters={
                "type": "object",
                "properties": {
                    "app_id": {
                        "type": "string",
                        "description": "Optional adapter to pin ('chrome', 'explorer', 'vscode'). When given, a foreground that identifies a different adapter (or none) refuses as a mismatch instead of resolving generically."
                    },
                    "phrase": {
                        "type": "string",
                        "description": "Spoken goal, for example 'open new tab' or 'open folder'. Exact adapter vocabulary only; unknown phrases refuse."
                    },
                    "foreground": {
                        "type": "object",
                        "description": "Optional foreground identity dict with 'proc', 'window_class', and 'title'. Omitted: a fresh observation supplies the foreground (process and title only — see the description for what that can identify)."
                    },
                    "app_version": {
                        "type": "string",
                        "description": "Optional app version. A version outside the adapter's verified list falls back to generic controls honestly."
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": "Set only after GUI approval for confirm-gated adapter actions. Unconfirmed gated actions stop with needs_confirmation and resolve nothing further."
                    }
                },
                "required": ["phrase"]
            },
            handler=self._tool_adapter_action
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _s7_snapshot_arg(self, snapshot):
        """Validate an optional item-2 snapshot arg.

        Returns (snapshot_dict_or_None, error_string_or_None). A
        non-dict snapshot is a caller error, never passed to an
        engine (engines treat non-dicts as snapshot-unavailable,
        but the tool says so plainly first).
        """
        if snapshot is None:
            return None, None
        if not isinstance(snapshot, dict):
            return None, ("A snapshot anchor must be an item-2 "
                          "snapshot object, not %s."
                          % type(snapshot).__name__)
        return snapshot, None

    # ------------------------------------------------------------------
    # menu_action
    # ------------------------------------------------------------------

    def _tool_menu_action(self, action, path=None,
                          window_handle_id=None, snapshot=None,
                          confirmed=False, effectful=None):
        if not isinstance(action, str):
            return "Menu action must be 'list' or 'invoke'."
        action = action.strip().lower()
        if action not in ("list", "invoke"):
            return ("Unknown menu action '%s' — use 'list' or "
                    "'invoke'." % action)
        snapshot, error = self._s7_snapshot_arg(snapshot)
        if error is not None:
            return error
        if action == "list":
            result = _mp.list_menu(snapshot=snapshot,
                                   window_handle_id=window_handle_id)
            if not result.get("ok"):
                refusal = result.get("refusal") or {}
                return ("Menu list refused (%s): %s"
                        % (refusal.get("code") or "refused",
                           refusal.get("reason") or "no reason given"))
            items = result.get("items") or []
            if not items:
                return "Menu list is empty."
            lines = []
            for item in items:
                state = "disabled" if item.get("enabled") is False \
                    else "enabled"
                extra = ""
                if item.get("checked"):
                    extra = ", checked"
                if item.get("has_children"):
                    extra += ", submenu"
                lines.append("- %s (%s%s)"
                             % (item.get("name"), state, extra))
            rendered = "Menu roots:\n%s" % "\n".join(lines)
            handle = result.get("window_handle_id")
            if handle:
                # §7 consumers batch 3: thread the production for
                # later calls' receipts (evidence only — the
                # rendered text is unchanged).
                _s7t.note_produced(
                    handle, snapshot=snapshot,
                    stamp=result.get("snapshot_stamp"))
                rendered += ("\n(window handle %s — pass it back as "
                             "window_handle_id on the next call for "
                             "this window.)" % handle)
            return rendered
        if path is None or not str(path).strip():
            return ("Invoking a menu path needs a path, for example "
                    "'File → Save As'.")
        result = _mp.invoke_path(
            path, snapshot=snapshot, window_handle_id=window_handle_id,
            confirmed=bool(confirmed),
            effectful=effectful)
        if result.get("ok"):
            # §7 consumers batch 3: thread the production for
            # later calls' receipts (evidence only).
            _s7t.note_produced(
                result.get("window_handle_id"), snapshot=snapshot,
                stamp=result.get("snapshot_stamp"))
        return _mp.describe(result)

    # ------------------------------------------------------------------
    # file_dialog_action
    # ------------------------------------------------------------------

    def _tool_file_dialog_action(self, action, folder=None, filename=None,
                                 file_type=None, window_handle_id=None,
                                 snapshot=None, confirm_overwrite=False):
        if not isinstance(action, str):
            return "Dialog action must be 'open', 'save', or 'folder'."
        action = action.strip().lower()
        snapshot, error = self._s7_snapshot_arg(snapshot)
        if error is not None:
            return error
        result = _fd.drive_dialog(
            action, folder=folder, filename=filename,
            file_type=file_type, snapshot=snapshot,
            window_handle_id=window_handle_id,
            confirm_overwrite=bool(confirm_overwrite))
        if result.get("ok"):
            # §7 consumers batch 3: thread the dialog window for
            # later calls' receipts (evidence only; the file
            # handle is the file, not the window, so it is not
            # the threaded handle).
            _s7t.note_produced(
                result.get("window_handle_id"), snapshot=snapshot,
                stamp=result.get("snapshot_stamp"))
        return _fd.describe(result)

    # ------------------------------------------------------------------
    # system_surface_action
    # ------------------------------------------------------------------

    def _tool_system_surface_action(self, surface, action, target=None,
                                    path=None, query=None,
                                    notification_id=None,
                                    expected_title=None, detail_action=None,
                                    target_handle_id=None, confirmed=False,
                                    effectful=None, include_body=False,
                                    body_approved_ids=None):
        if not isinstance(surface, str) or not isinstance(action, str):
            return "System surface action needs a surface and an action."
        surface = surface.strip().lower()
        action = action.strip().lower()
        routed = _ss.route_surface(surface)
        if not routed.get("ok"):
            refusal = routed.get("refusal") or {}
            return ("Surface refused (%s): %s"
                    % (refusal.get("code") or "refused",
                       refusal.get("reason") or "no reason given"))
        surface = routed["surface"]
        approved = set(body_approved_ids or [])
        if surface == "tray":
            if action == "list":
                result = _ss.list_tray_icons()
                return self._render_surface_list(result, "tray icons")
            if action == "open_menu":
                if target is None or not str(target).strip():
                    return "Opening a tray menu needs a tray icon name."
                result = _ss.open_tray_menu(
                    str(target), target_handle_id=target_handle_id)
                return self._describe_surface(result)
            if action == "invoke_path":
                if target is None or not str(target).strip():
                    return "Invoking a tray menu path needs an icon name."
                if path is None or not str(path).strip():
                    return "Invoking a tray menu path needs a path."
                result = _ss.invoke_tray_menu_path(
                    str(target), str(path), confirmed=bool(confirmed),
                    effectful=effectful, target_handle_id=target_handle_id)
                return self._describe_surface(result)
            return ("Unknown tray action '%s' — use 'list', "
                    "'open_menu', or 'invoke_path'." % action)
        if surface == "taskbar":
            if action == "list":
                result = _ss.list_taskbar_buttons()
                return self._render_surface_list(result, "taskbar buttons")
            if action == "switch":
                if target is None or not str(target).strip():
                    return "Switching needs a taskbar button name."
                result = _ss.switch_taskbar(
                    str(target), target_handle_id=target_handle_id)
                return self._describe_surface(result)
            if action == "launch":
                if target is None or not str(target).strip():
                    return "Launching needs a taskbar button name."
                result = _ss.launch_taskbar(
                    str(target), target_handle_id=target_handle_id)
                return self._describe_surface(result)
            if action == "pin":
                if target is None or not str(target).strip():
                    return "Pinning needs a taskbar button name."
                result = _ss.pin_taskbar(
                    str(target), confirmed=bool(confirmed),
                    target_handle_id=target_handle_id)
                return self._describe_surface(result)
            return ("Unknown taskbar action '%s' — use 'list', "
                    "'switch', 'launch', or 'pin'." % action)
        if surface == "start":
            if action == "search":
                if query is None or not str(query).strip():
                    return "Searching Start needs search text."
                result = _sh.start_search(str(query))
                return self._render_start_results(result)
            if action == "launch":
                if target is None or not str(target).strip():
                    return "Launching from Start needs a result name."
                result = _sh.launch_start(
                    str(target), target_handle_id=target_handle_id)
                return self._describe_surface(result)
            return ("Unknown Start action '%s' — use 'search' or "
                    "'launch'." % action)
        if surface == "notification":
            if action == "list":
                result = _sh.list_notifications(
                    include_body=bool(include_body),
                    body_approved_ids=approved)
                return self._render_notification_list(result)
            if action in ("act", "dismiss"):
                if notification_id is None \
                        or not str(notification_id).strip():
                    return ("Acting on a notification needs its id — "
                            "never a position.")
                if action == "dismiss":
                    result = _sh.dismiss_notification(
                        str(notification_id), expected_title or "",
                        confirmed=bool(confirmed),
                        target_handle_id=target_handle_id)
                else:
                    result = _sh.act_notification(
                        str(notification_id), expected_title or "",
                        str(detail_action or ""), confirmed=bool(confirmed),
                        target_handle_id=target_handle_id)
                return self._describe_surface(result)
            return ("Unknown notification action '%s' — use 'list', "
                    "'act', or 'dismiss'." % action)
        if surface == "settings":
            if action == "list":
                result = _sh.list_settings_pages()
                return self._render_surface_list(result, "settings pages")
            if action == "open":
                if target is None or not str(target).strip():
                    return "Opening a settings page needs a page name."
                result = _sh.open_settings(
                    str(target), target_handle_id=target_handle_id)
                return self._describe_surface(result)
            return ("Unknown settings action '%s' — use 'list' or "
                    "'open'." % action)
        return "Unknown surface '%s'." % surface

    def _describe_surface(self, result):
        """Engine describe() plus the target handle when minted.

        The two-call proof needs the caller to pass a handle from
        an earlier call back in — so the handle must survive
        rendering. Without this line the tool's target_handle_id
        arg would be unusable through the tool surface itself.
        """
        rendered = _ss.describe(result)
        handle = (result or {}).get("target_handle_id")
        if handle and (result or {}).get("ok"):
            # §7 consumers batch 3: thread the target for later
            # calls' receipts (evidence only; surfaces carry no
            # snapshot stamp, so the note is handle-only and
            # keeps a stored stamp for the same handle).
            _s7t.note_produced(handle)
        if handle:
            rendered += (" (target handle %s — pass it back as "
                         "target_handle_id on the next call for "
                         "this target.)" % handle)
        return rendered

    def _render_start_results(self, result):
        """Render a Start search result (its items live under
        ``results``, not ``items`` — the one surface whose list
        shape differs)."""
        if not result.get("ok"):
            return _ss.describe(result)
        items = result.get("results") or []
        if not items:
            return "No Start results."
        lines = []
        for item in items:
            lines.append("- %s (%s, id %s)"
                         % (item.get("name"), item.get("kind"),
                            item.get("result_id")))
        return "Start results:\n%s" % "\n".join(lines)

    def _render_surface_list(self, result, label):
        """Render an engine list result as plain lines."""
        if not result.get("ok"):
            return _ss.describe(result)
        items = result.get("items") or []
        if not items:
            return "No %s." % label
        lines = []
        for item in items:
            name = item.get("title") or item.get("name") or "?"
            ident = (item.get("icon_id") or item.get("button_id")
                     or item.get("result_id") or item.get("page_id")
                     or "")
            lines.append("- %s%s" % (name, (" (id %s)" % ident)
                                     if ident else ""))
        return "%s:\n%s" % (label.capitalize(), "\n".join(lines))

    def _render_notification_list(self, result):
        """Render a notification list, honouring D4 body rules."""
        if not result.get("ok"):
            return _ss.describe(result)
        items = result.get("items") or []
        if not items:
            return "No notifications."
        lines = []
        for item in items:
            state = []
            if item.get("expired"):
                state.append("expired")
            if item.get("dismissed"):
                state.append("dismissed")
            body_note = ""
            if "body" in item:
                body_note = " — %s" % item.get("body")
            elif item.get("body_withheld"):
                body_note = " — body withheld (not approved)"
            lines.append("- %s: %s (id %s%s)%s"
                         % (item.get("app"), item.get("title"),
                            item.get("notification_id"),
                            (", " + ", ".join(state)) if state else "",
                            body_note))
        return "Notifications:\n%s" % "\n".join(lines)

    # ------------------------------------------------------------------
    # adapter_action
    # ------------------------------------------------------------------

    def _tool_adapter_action(self, phrase, app_id=None, foreground=None,
                             app_version=None, confirmed=False):
        from ..desktop import app_adapters as _aa  # Phase 3 S7a-2: moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

        if phrase is None or not str(phrase).strip():
            return "Resolving an adapter phrase needs a phrase to resolve."
        if foreground is not None and not isinstance(foreground, dict):
            return ("A foreground identity must be an object with "
                    "'proc', 'window_class', and 'title'.")
        if foreground is None:
            # Fresh observation: its foreground identity carries
            # process and title, but no window class (the tree's
            # foreground source has no class field). That identifies
            # process-only adapters and falls back to generic
            # controls for class-bearing ones — honestly, never by
            # guessing a class.
            try:
                snapshot = _pa.observe()
            except Exception:  # noqa: BLE001 — observe degrades
                snapshot = {}
            foreground = (snapshot or {}).get("foreground_identity") or {}
            if not isinstance(foreground, dict):
                foreground = {}
        if app_id is not None:
            app_id = str(app_id).strip().lower()
            identified = _aa.identify(foreground)
            if identified.get("matched") != app_id:
                matched = identified.get("matched")
                # F-F501: nothing resolves under the wrong adapter,
                # so the verdict is failed, not a plain string the
                # receipts would read "succeeded".
                from .. import tool_outcome as _outcome  # R1b: seam wiring
                if matched is None:
                    if identified.get("ambiguous"):
                        detail = ("foreground matches several adapters "
                                  "(%s) — no single adapter resolves."
                                  % ", ".join(identified["ambiguous"]))
                    else:
                        detail = (identified.get("note")
                                  or "no adapter matches this foreground")
                    return _outcome.failed(
                        ("Adapter mismatch: the foreground does not "
                         "identify '%s' (%s) — nothing resolves "
                         "under the wrong adapter." % (app_id, detail)),
                        "foreground adapter mismatch")
                return _outcome.failed(
                    ("Adapter mismatch: the foreground identifies "
                     "'%s', not '%s' — nothing resolves under the "
                     "wrong adapter." % (matched, app_id)),
                    "foreground adapter mismatch")
        resolution = _aa.resolve(str(phrase), foreground=foreground,
                                 app_version=app_version,
                                 confirmed=bool(confirmed))
        if not resolution.get("ok"):
            refusal = resolution.get("refusal") or {}
            line = ("Adapter resolution refused (%s): %s"
                    % (refusal.get("code") or "refused",
                       refusal.get("reason") or "no reason given"))
            if resolution.get("needs_confirmation"):
                line += (" Confirm it explicitly, then resolve again.")
            return line
        if resolution.get("mode") == "generic":
            return ("No profile for this app — using generic "
                    "controls. %s" % (resolution.get("note") or ""))
        line = _aa.receipt_line(resolution)
        target = resolution.get("target") or {}
        if target.get("path"):
            line += " — menu path: %s" % " → ".join(target["path"])
        elif target.get("action"):
            line += " — dialog: %s" % target["action"]
        elif target.get("name"):
            line += " — target: %s" % target["name"]
        if resolution.get("needs_confirmation"):
            line += " (confirmation still required)"
        return line
