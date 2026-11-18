"""Pack registration for the §7 consumers batch 8 (2026-11-02).

Item 3 shipped menu-bar roots only; the context-menu trigger
(``core.desktop.context_menus``) closes that gap, and it unblocks the
VS Code adapter's "delete file" job (the phrase names the job;
context-menu driving builds the path). This pack gives the
trigger one funnel tool:

- context_menu_action — trigger / list / invoke a target's
  context menu through the item-3 walk.

The tool invents no click / type / dispatch path around the
engine it calls, and never falls back to keystrokes. Handle /
stamp args are EXPLICIT (the caller supplies a window handle
id, a mark handle id, and, where it has one, an item-2
snapshot dict); batch-3 auto-threading notes the window
handle production for later calls' receipts (evidence only).

Confirm-gating (two layers, both fail-closed, batch-1
protocol):
- Funnel layer: the tool is in _CONFIRM_GATE_TOOLS, with an
  args-aware branch in _needs_confirmation that frees ONLY
  the list leg (reading an already-open menu changes
  nothing). Trigger and invoke always pop the GUI
  confirmation; a voice "yes" never counts, and no handler
  runs without an explicit human yes.
- Engine layer: the handler passes its confirmed flag through
  to the item-3 walk (menu confirmed). An engine
  needs_confirmation stop returns verbatim with ZERO invokes;
  only a caller-supplied confirmed=True after GUI approval
  re-issues the call. The two layers are INDEPENDENT (the
  run_command precedent): funnel approval alone does NOT
  satisfy the engine flag, and the gate branch deliberately
  does NOT treat a caller-supplied confirmed=True as
  approval.

New tools: 1. Registry 652 -> 653. Classification is pinned
by the G2 snapshot (regenerated deliberately with this
change).
"""

from ..desktop import context_menus as _cm  # Phase 3 S7a-2: moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)
from ..desktop import s7_threading as _s7t  # Phase 3 S7a-2: moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)


class S7Consumers3PacksMixin:
    def _register_s7consumers3_tools(self):
        self.register(
            name="context_menu_action",
            description="Trigger, list, or invoke a target's context menu in the foreground app (for example, right-click a file in Explorer and invoke Delete). Triggering opens the menu and lists it; listing reads an already-open menu without triggering; invoking opens the menu and walks a path in it through the item-3 menu engine. Triggering and invoking ask first through the GUI confirmation; listing is a read. Context-menu refusals are reported exactly as the engine gives them (target not found or ambiguous, target gone, no context menu open, menu unexposed, trigger failed, disabled item, ambiguous name, item not found with its siblings listed, menu rebuilt, submenu opened instead of invoked, invoked without verification). This tool never falls back to keystrokes.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "One of 'trigger' (open the target's context menu and list it), 'list' (read an already-open menu without triggering), or 'invoke' (open the menu and walk a path in it)."
                    },
                    "target": {
                        "type": "string",
                        "description": "Exact target name, for example the file in the Explorer tree. Matched exactly against the observation's controls; ambiguous or missing names refuse."
                    },
                    "target_handle_id": {
                        "type": "string",
                        "description": "Optional item-1 mark handle minted from an earlier observation, identifying the target. Re-proven by the F-07 rule plus a name-presence check; handles that cannot be re-proven from the observation refuse."
                    },
                    "window_handle_id": {
                        "type": "string",
                        "description": "Optional item-1 window handle to re-prove. Omitted: a handle is minted from the observation."
                    },
                    "snapshot": {
                        "type": "object",
                        "description": "Optional item-2 snapshot dict anchoring the call (from a prior observation). Omitted: a fresh observation is taken."
                    },
                    "path": {
                        "type": "string",
                        "description": "Menu path for 'invoke', for example 'Delete'. Not needed for 'trigger' or 'list'."
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
            handler=self._tool_context_menu_action
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _s7cm_snapshot_arg(self, snapshot):
        """Validate an optional item-2 snapshot arg (pack-local
        copy of the batch-1 helper shape: a non-dict snapshot
        is a caller error, never passed to the engine)."""
        if snapshot is None:
            return None, None
        if not isinstance(snapshot, dict):
            return None, ("A snapshot anchor must be an item-2 "
                          "snapshot object, not %s."
                          % type(snapshot).__name__)
        return snapshot, None

    def _s7cm_render_items(self, result):
        """Render an opened/listed context menu as plain lines,
        like the menu handler renders menu roots."""
        items = result.get("items") or []
        if not items:
            return "Context menu is empty."
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
        rendered = "Context menu items:\n%s" % "\n".join(lines)
        handle = result.get("window_handle_id")
        if handle:
            rendered += ("\n(window handle %s — pass it back as "
                         "window_handle_id on the next call for "
                         "this window.)" % handle)
        return rendered

    # ------------------------------------------------------------------
    # context_menu_action
    # ------------------------------------------------------------------

    def _tool_context_menu_action(self, action, target=None,
                                  target_handle_id=None,
                                  window_handle_id=None, snapshot=None,
                                  path=None, confirmed=False,
                                  effectful=None):
        if not isinstance(action, str):
            return ("Context menu action must be 'trigger', 'list', "
                    "or 'invoke'.")
        action = action.strip().lower()
        if action not in ("trigger", "list", "invoke"):
            return ("Unknown context menu action '%s' — use "
                    "'trigger', 'list', or 'invoke'." % action)
        snapshot, error = self._s7cm_snapshot_arg(snapshot)
        if error is not None:
            return error
        if action == "trigger":
            result = _cm.open_context_menu(
                target, target_handle_id=target_handle_id,
                snapshot=snapshot, window_handle_id=window_handle_id)
            if not result.get("ok"):
                return _cm.describe(result)
            handle = result.get("window_handle_id")
            if handle:
                # §7 consumers batch 3: thread the production
                # for later calls' receipts (evidence only).
                _s7t.note_produced(
                    handle, snapshot=snapshot,
                    stamp=result.get("snapshot_stamp"))
            return self._s7cm_render_items(result)
        if action == "list":
            result = _cm.list_context_menu(
                target, target_handle_id=target_handle_id,
                snapshot=snapshot, window_handle_id=window_handle_id)
            if not result.get("ok"):
                return _cm.describe(result)
            return self._s7cm_render_items(result)
        if path is None or not str(path).strip():
            return ("Invoking a context menu path needs a path, "
                    "for example 'Delete'.")
        result = _cm.invoke_context_path(
            path, target, target_handle_id=target_handle_id,
            snapshot=snapshot, window_handle_id=window_handle_id,
            confirmed=bool(confirmed), effectful=effectful)
        if result.get("ok"):
            # §7 consumers batch 3: thread the production for
            # later calls' receipts (evidence only).
            _s7t.note_produced(
                result.get("window_handle_id"), snapshot=snapshot,
                stamp=result.get("snapshot_stamp"))
        return _cm.describe(result)
