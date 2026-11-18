"""Pack registrations for the §7 consumers batch 2 (2026-11-02).

Batch 1 gave funnel tools to menus (item 3), file dialogs (item 4),
system surfaces (item 7), and app adapters (item 9). This pack
completes the funnel-tool set with the three remaining §7 engines
that have a funnel surface:

- drag_drop_action — perform a drag (item 5).
- monitor_action — resolve monitors and convert coordinates
  (item 6).
- journal_action — read the action journal and run
  user-confirmed reconciliation (item 8).

Resolve-only where the engine is resolve-only does not apply
here the way it did in batch 1: drag_drop_action EXECUTES a drag
through the item-5 engine (it invents no drag path of its own —
no filesystem move or delete, ever; the engine performs none
either), monitor_action only resolves and converts (it never
rearranges displays), and journal_action reads plus the core
reconcile() user-confirmed action (it never rewrites history).
Handle / stamp args are EXPLICIT in this batch (the caller
supplies handle ids, snapshot anchors, and observations);
funnel auto-threading of handle / stamp context is a separate
follow-up and is NOT built here.

Confirm-gating (two layers, both fail-closed — the batch-1
protocol):
- Funnel layer: drag_drop_action and journal_action are in
  _CONFIRM_GATE_TOOLS, with args-aware branches in
  _needs_confirmation that free ONLY journal_action's pure-read
  legs (list, plan). monitor_action is a pure read and is not
  gated at all (it sits in _READ_ONLY_TOOLS). A voice "yes"
  never counts, and no handler runs without an explicit human
  yes on a gated path.
- Engine layer: drag_drop_action passes confirm_destructive
  through to the item-5 engine (destructive targets stop with
  needs_confirmation and ZERO drags until re-called with the
  flag); journal_action passes the funnel approval through as
  the reconcile/compensate confirmed flag, and the engine
  additionally requires a confirmer name (confirmed_by,
  defaulting to "user" — the GUI approver — because the funnel
  confirm path carries a decision, not a name). An engine
  needs_confirmation stop returns verbatim with ZERO invokes;
  only a caller-supplied confirmed=True after GUI approval
  re-issues the call. The gate branches deliberately do NOT
  treat a caller-supplied confirmed=True as approval: a
  model-set flag must never wave itself through the popup.

New tools: 3. Registry 649 -> 652. Classifications are pinned
by the G2 snapshot (regenerated deliberately with this change).
"""

from ..desktop import action_journal as _aj  # Phase 3 S7a-2: moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)
from ..desktop import drag_drop as _dd  # Phase 3 S7a: drag_drop moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)
from ..desktop import monitors as _mon  # Phase 3 S7a: monitors moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)
from ..desktop import s7_threading as _s7t  # Phase 3 S7a-2: moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)
from .. import tool_receipts as _tr  # Phase 3 F3: file now in core/funnel/; import climbs one more level


def _validate_snapshot_arg(snapshot):
    """Validate an optional item-2 snapshot arg.

    Returns (snapshot_dict_or_None, error_string_or_None). A
    non-dict snapshot is a caller error, never passed to an
    engine.
    """
    if snapshot is None:
        return None, None
    if not isinstance(snapshot, dict):
        return None, ("A snapshot anchor must be an item-2 "
                      "snapshot object, not %s."
                      % type(snapshot).__name__)
    return snapshot, None


def _validate_observation_arg(observation, name):
    """Validate an optional item-1 observation arg."""
    if observation is None:
        return {}, None
    if not isinstance(observation, dict):
        return None, ("A %s observation must be an object, not %s."
                      % (name, type(observation).__name__))
    return observation, None


_CONFIG_ACTIONS = (
    "arrange", "set_resolution", "set_primary", "set_scale",
    "rotate", "set_position", "set_arrangement", "mirror",
    "extend", "duplicate",
)


class S7Consumers2PacksMixin:
    def _register_s7consumers2_tools(self):
        self.register(
            name="drag_drop_action",
            description="Drag a payload (files, text, or a URL) from a consented source handle onto a consented target handle through the true OLE drag engine. Every drag asks first through the GUI confirmation. The engine's gate order is preserved exactly: handle re-proof, by-design walls, destructive-target confirm stop with zero drags, auto-run/startup refusal, per-monitor drop-point conversion at execution time, OLE availability, target-kind/profile-change refusal, format negotiation, data-object build from declared fields only (never credentials), then the drag itself with verification or honest dropped-without-verification. A pointer-gesture alternative runs only when explicitly allowed and proven for that target, and the receipt always names the mechanism. This tool never moves or deletes a source file itself.",
            parameters={
                "type": "object",
                "properties": {
                    "source_handle_id": {
                        "type": "string",
                        "description": "Item-1 handle id of the drag source (minted from an earlier observation)."
                    },
                    "target_handle_id": {
                        "type": "string",
                        "description": "Item-1 handle id of the drop target (minted from an earlier observation)."
                    },
                    "payload": {
                        "type": "object",
                        "description": "Payload object: {'kind': 'files', 'files': [paths]} or {'kind': 'text', 'text': '...'} or {'kind': 'url', 'url': '...'}. Declared fields only; anything else refuses."
                    },
                    "target_kind": {
                        "type": "string",
                        "description": "Consented target kind, for example 'folder', 'editor', 'trash'. A backend-reported profile that contradicts it refuses."
                    },
                    "snapshot": {
                        "type": "object",
                        "description": "Optional item-2 snapshot dict the handles resolve against (from a prior observation)."
                    },
                    "source_observation": {
                        "type": "object",
                        "description": "Optional current observation the source handle re-proves against. Omitted: resolved against an empty observation (file sources still resolve; control targets need their identity observation)."
                    },
                    "target_observation": {
                        "type": "object",
                        "description": "Optional current observation the target handle re-proves against. Omitted: resolved against an empty observation."
                    },
                    "monitor_reference": {
                        "type": "string",
                        "description": "Optional monitor reference ('second monitor', device id, or index) for a per-monitor drop point, converted at execution time against a fresh table. Needs target_norm1000 with it."
                    },
                    "target_norm1000": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Optional norm1000 (x, y) drop point on the referenced monitor."
                    },
                    "allow_gesture": {
                        "type": "boolean",
                        "description": "Allow the pointer-gesture alternative when the target has no OLE support. Runs only when explicitly allowed AND the backend proves the gesture for that target; the receipt always names the mechanism."
                    },
                    "confirm_destructive": {
                        "type": "boolean",
                        "description": "Set only after GUI approval for a destructive target (trash, format, overwrite). Without it, a destructive drag stops with needs_confirmation and drags nothing."
                    }
                },
                "required": ["source_handle_id", "target_handle_id",
                             "payload"]
            },
            handler=self._tool_drag_drop_action
        )

        self.register(
            name="monitor_action",
            description="Resolve monitors and convert coordinates — a pure read, never a display change. List the current monitors; resolve a reference ('second monitor', device id, or index) plus a norm1000 point to physical pixels against a fresh table read inside the call; convert a point between a monitor's logical frame and physical pixels; or fit a window rect onto another monitor. Refusals are reported exactly as the monitor engine gives them (unreadable scale, unknown reference with the monitor list, ambiguous position ties, bounds violation naming monitor, point, and bounds — never clamped onto another monitor). Display configuration changes (resolution, arrangement, primary, scale, rotation) are out of scope and refuse.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "One of 'list', 'resolve', 'convert', 'place'."
                    },
                    "reference": {
                        "type": "string",
                        "description": "Monitor reference for 'resolve' and 'convert': 'second monitor', a device id, or an index shorthand, confirmed against the current table on every call."
                    },
                    "nx": {
                        "type": "number",
                        "description": "Norm1000 x for 'resolve'."
                    },
                    "ny": {
                        "type": "number",
                        "description": "Norm1000 y for 'resolve'."
                    },
                    "direction": {
                        "type": "string",
                        "description": "For 'convert': 'logical_to_physical' or 'physical_to_logical'."
                    },
                    "x": {
                        "type": "number",
                        "description": "Point x for 'convert'."
                    },
                    "y": {
                        "type": "number",
                        "description": "Point y for 'convert'."
                    },
                    "rect": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Window rect (left, top, right, bottom) in physical pixels for 'place'."
                    },
                    "from_reference": {
                        "type": "string",
                        "description": "Source monitor reference for 'place'."
                    },
                    "to_reference": {
                        "type": "string",
                        "description": "Target monitor reference for 'place'."
                    }
                },
                "required": ["action"]
            },
            handler=self._tool_monitor_action
        )

        self.register(
            name="journal_action",
            description="Read the action journal and run user-confirmed reconciliation. List recent entries (newest first, with retryable/done/unknown counts); plan a resume (classified entries, read-only); reconcile one unknown entry with what the user confirmed actually happened (verified absent records failed so a fresh retry is safe, verified happened records succeeded so it never re-runs — the original attempt line is never rewritten); or run an entry's declared compensations in reverse order, each verified, first failure stopping. Reconcile and compensate ask first through the GUI confirmation and additionally require a confirmer name. No declaration means 'no undo available' plus manual steps, and the executor is never called. Corrupt journal lines fail loudly with the manual repair path; there is no automatic repair, ever. Handle ids and snapshot stamps on entries are evidence only, never re-resolved.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "One of 'list', 'plan', 'reconcile', 'compensate'."
                    },
                    "receipt_id": {
                        "type": "string",
                        "description": "Journal entry id for 'reconcile' and 'compensate', and optionally for 'plan' (compensation plan for that entry)."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Entries to list for 'list' (default 20, at least 1)."
                    },
                    "store_path": {
                        "type": "string",
                        "description": "Journal file path. Omitted: the funnel's receipt store (data/tool_receipts.jsonl). Tests and lab runs pass a temp path."
                    },
                    "happened": {
                        "type": "boolean",
                        "description": "For 'reconcile': true when the user confirmed the effect happened, false when verified absent. Required — the tool cannot guess what happened."
                    },
                    "verification": {
                        "type": "string",
                        "description": "For 'reconcile': the verification text — what the user confirmed actually happened (or did not). Required."
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": "Set only after GUI approval for 'reconcile' and 'compensate'. Unconfirmed effectful legs stop with needs_confirmation and write nothing."
                    },
                    "confirmed_by": {
                        "type": "string",
                        "description": "Confirmer name recorded for 'reconcile' and 'compensate' (default 'user' — the GUI approver). The funnel popup, not the name, is the approval; an empty name refuses."
                    }
                },
                "required": ["action"]
            },
            handler=self._tool_journal_action
        )

    # ------------------------------------------------------------------
    # drag_drop_action
    # ------------------------------------------------------------------

    def _tool_drag_drop_action(self, source_handle_id, target_handle_id,
                               payload, target_kind="",
                               snapshot=None, source_observation=None,
                               target_observation=None,
                               monitor_reference=None,
                               target_norm1000=None, allow_gesture=False,
                               confirm_destructive=False):
        if not isinstance(source_handle_id, str) \
                or not source_handle_id.strip():
            return "Dragging needs a source handle id."
        if not isinstance(target_handle_id, str) \
                or not target_handle_id.strip():
            return "Dragging needs a target handle id."
        if not isinstance(payload, dict):
            return ("A drag payload must be an object: {'kind': "
                    "'files'|'text'|'url', ...} with declared fields "
                    "only.")
        snapshot, error = _validate_snapshot_arg(snapshot)
        if error is not None:
            return error
        source_observation, error = _validate_observation_arg(
            source_observation, "source")
        if error is not None:
            return error
        target_observation, error = _validate_observation_arg(
            target_observation, "target")
        if error is not None:
            return error
        if target_norm1000 is not None:
            try:
                nx, ny = target_norm1000
                float(nx)
                float(ny)
            except (TypeError, ValueError):
                return ("A monitor-targeted drag needs a norm1000 "
                        "(x, y) target point to convert.")
            target_norm1000 = (nx, ny)
        result = _dd.drag(
            source_handle_id, target_handle_id, payload,
            source_observation=source_observation,
            target_observation=target_observation,
            snapshot=snapshot,
            target_kind=target_kind or "",
            confirm_destructive=bool(confirm_destructive),
            allow_gesture=bool(allow_gesture),
            monitor_reference=monitor_reference,
            target_norm1000=target_norm1000)
        rendered = _dd.describe(result)
        if result.get("ok"):
            # §7 consumers batch 3: thread the drop target for
            # later calls' receipts (evidence only; the engine
            # performs no filesystem move/delete itself).
            _s7t.note_produced(
                result.get("target_handle_id"), snapshot=snapshot,
                stamp=result.get("snapshot_stamp"))
        if result.get("needs_confirmation"):
            rendered += (" Confirm it explicitly, then drag again "
                         "with confirm_destructive set.")
        return rendered

    # ------------------------------------------------------------------
    # monitor_action
    # ------------------------------------------------------------------

    def _monitor_table(self):
        """Fresh monitor table + the engine's read note."""
        return _mon.read_table()

    def _resolve_reference(self, table, reference):
        """Resolve one reference against a fresh table.

        Returns (monitor_or_None, refusal_string). A refusal is
        returned verbatim — it already lists the monitors and
        asks which screen was meant.
        """
        monitor, refusal = _mon.resolve_monitor(table, reference)
        if monitor is None:
            return None, refusal
        return monitor, ""

    def _tool_monitor_action(self, action, reference=None, nx=None,
                             ny=None, direction=None, x=None, y=None,
                             rect=None, from_reference=None,
                             to_reference=None):
        if not isinstance(action, str):
            return "Monitor action must be 'list', 'resolve', 'convert', or 'place'."
        action = action.strip().lower()
        if action in _CONFIG_ACTIONS:
            return ("Monitor action refused: display configuration "
                    "changes (resolution, arrangement, primary, "
                    "scale, rotation) are out of scope — this tool "
                    "adapts to the arrangement, it never rearranges "
                    "it.")
        if action == "list":
            table, note = self._monitor_table()
            if not table:
                return note if note else "No monitors readable."
            return "Monitors:\n%s" % _mon.describe_table(table)
        if action == "resolve":
            if reference is None or not str(reference).strip():
                return "Resolving a monitor needs a reference."
            try:
                nx = float(nx)
                ny = float(ny)
            except (TypeError, ValueError):
                return ("Resolving a monitor point needs norm1000 "
                        "numbers (nx, ny).")
            resolved, refusal = _mon.resolve_for_action(
                str(reference), nx, ny)
            if resolved is None:
                return "Monitor resolve refused: %s" % refusal
            monitor = resolved["monitor"]
            point = resolved["point"]
            return ("Resolved %s; bounds %s,%s %sx%s."
                    % (_mon.receipt_line(monitor, point),
                       monitor.get("left"), monitor.get("top"),
                       monitor.get("width"), monitor.get("height")))
        if action == "convert":
            if direction is None \
                    or str(direction).strip().lower() not in (
                        "logical_to_physical", "physical_to_logical"):
                return ("Converting needs a direction: "
                        "'logical_to_physical' or "
                        "'physical_to_logical'.")
            direction = str(direction).strip().lower()
            if reference is None or not str(reference).strip():
                return "Converting needs a monitor reference."
            try:
                px = float(x)
                py = float(y)
            except (TypeError, ValueError):
                return "Converting needs numeric point coordinates."
            table, note = self._monitor_table()
            if not table:
                return note if note else "No monitors readable."
            monitor, refusal = self._resolve_reference(
                table, str(reference))
            if monitor is None:
                return "Monitor convert refused: %s" % refusal
            if direction == "logical_to_physical":
                point, refusal = _mon.logical_to_physical(px, py, monitor)
            else:
                point, refusal = _mon.physical_to_logical(px, py, monitor)
            if point is None:
                return "Monitor convert refused: %s" % refusal
            return ("Converted (%s, %s) -> (%s, %s) on %s."
                    % (px, py, point[0], point[1],
                       _mon.receipt_line(monitor, point)))
        if action == "place":
            if rect is None:
                return ("Placing a window rect needs a rect (left, "
                        "top, right, bottom).")
            try:
                rect = tuple(int(v) for v in rect)
                if len(rect) != 4:
                    raise ValueError("rect needs 4 numbers")
            except (TypeError, ValueError):
                return ("Placing a window rect needs 4 numbers "
                        "(left, top, right, bottom).")
            if from_reference is None or not str(from_reference).strip():
                return "Placing a window rect needs a source monitor."
            if to_reference is None or not str(to_reference).strip():
                return "Placing a window rect needs a target monitor."
            table, note = self._monitor_table()
            if not table:
                return note if note else "No monitors readable."
            from_monitor, refusal = self._resolve_reference(
                table, str(from_reference))
            if from_monitor is None:
                return "Monitor place refused: %s" % refusal
            to_monitor, refusal = self._resolve_reference(
                table, str(to_reference))
            if to_monitor is None:
                return "Monitor place refused: %s" % refusal
            placed, refusal = _mon.place_rect_on_monitor(
                rect, from_monitor, to_monitor)
            if placed is None:
                return "Monitor place refused: %s" % refusal
            target_id = (to_monitor.get("monitor_id")
                         or to_monitor.get("device")
                         or ("monitor %s" % to_monitor.get("index")))
            return ("Placed rect on %s: (%s, %s, %s, %s)."
                    % (target_id, placed[0],
                       placed[1], placed[2], placed[3]))
        return ("Unknown monitor action '%s' — use 'list', "
                "'resolve', 'convert', or 'place'." % action)

    # ------------------------------------------------------------------
    # journal_action
    # ------------------------------------------------------------------

    def _journal_store(self, store_path):
        """Receipt store for the journal legs.

        Omitted path: the funnel's receipt store. A path is an
        explicit lab/test override — the tool never guesses a
        store, and every leg re-reads from the file, never from
        a stale in-memory table.
        """
        if store_path is None:
            return _tr.ReceiptStore()
        return _tr.ReceiptStore(path=store_path)

    def _journal_health(self, store):
        """Loud corruption report, or None when the journal reads."""
        try:
            health = _aj.check_journal(store)
        except ValueError as exc:
            return ("Journal unreadable: %s" % exc)
        if not health.get("ok"):
            lines = ["Journal unreadable: %s" % health.get("error", "")]
            lines.append("Manual repair path (no automatic repair):")
            for step in health.get("repair_steps") or []:
                lines.append("- %s" % step)
            return "\n".join(lines)
        return None

    def _tool_journal_action(self, action, receipt_id=None, limit=20,
                             store_path=None, happened=None,
                             verification=None, confirmed=False,
                             confirmed_by="user"):
        if not isinstance(action, str):
            return "Journal action must be 'list', 'plan', 'reconcile', or 'compensate'."
        action = action.strip().lower()
        if action not in ("list", "plan", "reconcile", "compensate"):
            return ("Unknown journal action '%s' — use 'list', "
                    "'plan', 'reconcile', or 'compensate'." % action)
        store = self._journal_store(store_path)
        if action == "list":
            try:
                limit = int(limit)
            except (TypeError, ValueError):
                return "Listing journal entries needs a numeric limit."
            if limit < 1:
                return "Listing journal entries needs a limit of at least 1."
            try:
                readout = _aj.read_out(store, limit)
            except ValueError:
                problem = self._journal_health(store)
                return problem or "Journal unreadable."
            lines = ["Journal entries (newest first):"]
            for summary in readout.get("entries") or []:
                lines.append("- %s" % summary)
            counts = readout.get("counts") or {}
            lines.append("Counts: retryable %s, done %s, unknown %s."
                         % (counts.get("retryable", 0),
                            counts.get("done", 0),
                            counts.get("unknown", 0)))
            return "\n".join(lines)
        if action == "plan":
            try:
                plan = _aj.plan_resume(store)
            except ValueError:
                problem = self._journal_health(store)
                return problem or "Journal unreadable."
            counts = plan.get("counts") or {}
            lines = ["Resume plan (read-only — nothing re-runs):",
                     "Retryable (never dispatched — safe to retry as "
                     "new attempts): %s." % counts.get("retryable", 0),
                     "Done (outcome known — never re-run): %s."
                     % counts.get("done", 0),
                     "Unknown (dispatched without outcome — blocked "
                     "until user-confirmed reconcile): %s."
                     % counts.get("unknown", 0)]
            if receipt_id is not None:
                try:
                    entries = _aj.load_entries(store)
                except ValueError:
                    problem = self._journal_health(store)
                    return problem or "Journal unreadable."
                entry = next(
                    (e for e in entries
                     if str(e.get("entry_id")) == str(receipt_id)),
                    None)
                if entry is None:
                    return ("No journal entry for receipt %r."
                            % receipt_id)
                comp = _aj.compensation_plan(entry)
                if comp["status"] == "no_undo_available":
                    lines.append("Compensation for %s: no undo "
                                 "available." % receipt_id)
                    for step in comp.get("manual_steps") or []:
                        lines.append("- %s" % step)
                else:
                    lines.append("Compensation for %s (reverse "
                                 "declaration order):" % receipt_id)
                    for step in comp.get("steps") or []:
                        lines.append("- %s %s"
                                     % (step.get("tool"),
                                        step.get("note") or ""))
            return "\n".join(lines)
        if action == "reconcile":
            if not confirmed:
                return ("Reconciliation needs confirmation: "
                        "reconcile is a user-confirmed action, so "
                        "this call stops with needs_confirmation "
                        "and writes nothing — confirm it "
                        "explicitly, then reconcile again with "
                        "confirmed set.")
            if receipt_id is None or not str(receipt_id).strip():
                return "Reconciling needs a journal entry id."
            if not isinstance(happened, bool):
                return ("Reconciling needs happened true or false — "
                        "what the user confirmed actually happened "
                        "(or did not). The tool cannot guess.")
            if not isinstance(confirmed_by, str) \
                    or not confirmed_by.strip():
                return ("Reconciling needs a confirmer name — "
                        "reconcile is a user-confirmed action, "
                        "never automatic.")
            try:
                result = _aj.reconcile(
                    store, receipt_id=str(receipt_id),
                    happened=happened, verification=verification,
                    confirmed_by=confirmed_by)
            except ValueError as exc:
                problem = self._journal_health(store)
                return problem or "Reconcile refused: %s" % exc
            return ("Reconciled entry %s: was %s, now %s. %s"
                    % (result["receipt_id"],
                       result["previous_state"],
                       result["new_outcome"],
                       result["summary"]))
        if action == "compensate":
            if not confirmed:
                return ("Compensation needs confirmation: "
                        "running declared compensations is "
                        "effectful, so this call stops with "
                        "needs_confirmation and runs nothing — "
                        "confirm it explicitly, then compensate "
                        "again with confirmed set.")
            if receipt_id is None or not str(receipt_id).strip():
                return "Compensating needs a journal entry id."
            if not isinstance(confirmed_by, str) \
                    or not confirmed_by.strip():
                return ("Compensating needs a confirmer name — "
                        "effectful compensation asks first, "
                        "never auto-runs.")
            try:
                entries = _aj.load_entries(store)
            except ValueError:
                problem = self._journal_health(store)
                return problem or "Journal unreadable."
            entry = next(
                (e for e in entries
                 if str(e.get("entry_id")) == str(receipt_id)),
                None)
            if entry is None:
                return ("No journal entry for receipt %r."
                        % receipt_id)
            plan = _aj.compensation_plan(entry)
            if plan["status"] == "no_undo_available":
                lines = ["No undo available for entry %s — the "
                         "executor is never called." % receipt_id]
                for step in plan.get("manual_steps") or []:
                    lines.append("- %s" % step)
                return "\n".join(lines)
            try:
                result = _aj.run_compensations(
                    store, entry, executor=self._compensation_executor,
                    confirmed_by=confirmed_by)
            except ValueError as exc:
                problem = self._journal_health(store)
                return problem or "Compensation refused: %s" % exc
            if result["status"] == "complete":
                verified = len(result.get("verified") or [])
                return ("Compensation complete for entry %s: %s "
                        "step(s) verified." % (receipt_id, verified))
            lines = ["Compensation stopped for entry %s: %s"
                     % (receipt_id, result.get("reason") or "")]
            stopped = result.get("stopped_at") or {}
            if stopped:
                lines.append("Stopped at: %s" % stopped.get("tool"))
            verified = result.get("verified") or []
            if verified:
                lines.append("Verified before stopping: %s."
                             % ", ".join(str(v.get("tool"))
                                         for v in verified))
            return "\n".join(lines)
        return "Unknown journal action '%s'." % action

    def journal_action(self, action, receipt_id=None, limit=20,
                       store_path=None, happened=None,
                       verification=None, confirmed=False,
                       confirmed_by="user"):
        """Public seam for the journal tool (GAPS-28, G-V5): the
        GUI's compensate leg used to call the private
        ``_tool_journal_action`` directly; it calls this instead.
        Same signature, same behaviour — the funnel handler
        registration still points at the private method.

        The handler is funnel-guarded (GAPS-23 F-V7): a direct
        call outside the funnel raises, and the GUI's leg calls
        from a worker thread — so this seam enters the dispatch
        scope around the delegate call. That is the sanctioned
        exception, not a bypass: the GUI calls it only after its
        own confirm dialog (``present_pending_dialog``) and with
        ``confirmed=True`` (the handler's own confirmed check
        still runs), and the compensation steps themselves run
        through the funnel executor with their own gates and
        receipts. Without the scope the leg raises and the user
        sees "Compensation could not run" — which is what the
        GUI showed ever since GAPS-23 installed the guard.
        """
        from .tools_registry_core import _funnel_dispatch_scope
        with _funnel_dispatch_scope():
            return self._tool_journal_action(
                action, receipt_id=receipt_id, limit=limit,
                store_path=store_path, happened=happened,
                verification=verification, confirmed=confirmed,
                confirmed_by=confirmed_by)

    def _compensation_executor(self, step):
        """Run one declared compensation through the funnel.

        The engine's contract: the executor is the funnel in
        production, so each step runs through the gate stack
        with its own funnel receipt. The funnel's execute()
        returns {"status": "success", ...} on success; anything
        else is not verified, and the runner stops.
        """
        try:
            payload = self.execute(step.get("tool"),
                                   step.get("args") or {})
        except Exception as exc:  # noqa: BLE001 — a failing step
            # stops the run and is reported, not hidden.
            return {"status": "error", "error": str(exc)}
        if isinstance(payload, dict) and payload.get("status") == "success":
            return {"status": "succeeded"}
        kind = ""
        if isinstance(payload, dict):
            kind = payload.get("kind") or payload.get("status") or ""
        return {"status": kind or "failed"}
