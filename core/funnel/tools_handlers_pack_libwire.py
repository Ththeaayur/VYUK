"""Pack registrations added by the libwiring sweep (2026-11-01).

Teach mode (w8) and the wallpaper pilot (w9) existed as wiring
helpers with no user-action path — library-only in the shipped
tree. This pack gives them funnel tools:

- teach_record_sequence (confirm-gated: persistence asks the
  human, save_routine parity) — record a named sequence of tool
  steps the user spells out explicitly. Live demonstration
  capture is NOT built here: it is GATED
  (TEACH_LIVE_CAPTURE_GATE in w8_wiring) — it needs a
  user-action observer + an event-to-funnel-tool mapper +
  field validation on real Windows; spell-out record is the
  shipped path.
- teach_list_sequences (read-only) — list stored sequences.
- teach_replay_sequence (confirm-gated: replay_programme
  parity — every replay asks first, even though each inner step
  is re-gated on its own classification) — replay a stored
  sequence through the gated funnel, verified step by step. A
  step that fails or is refused halts the replay honestly; no
  later step runs.
- wallpaper_state (read-only) — resolve the wallpaper pilot's
  state/art for an hour from art paths the user supplies,
  WITHOUT applying anything. Applying is NOT built here:
  Windows validation (apply + revert) + an explicit owner
  decision are owed (the pilot's own closing step).

Wallpaper apply (2026-11-03, code-rulings delegation): a
separate confirm-gated wallpaper_apply tool was added to this
pack. wallpaper_state is unchanged and still read-only; field
validation on real Windows is still owed.

New tools: 4. Registry 641 -> 645. Classifications are pinned
by the G2 snapshot (regenerated deliberately with this change).
"""


class LibwirePacksMixin:
    def _register_libwire_tools(self):
        self.register(
            name="teach_record_sequence",
            description="Record a taught sequence: a named list of tool steps you spell out explicitly (each step names a tool, its arguments, and a check), stored for later replay. Recording only stores what you described — it runs nothing. Live demonstration capture (watching you do it) is not part of this; its trigger is still owed. Replay a stored sequence with teach_replay_sequence.",
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name to store the sequence under (e.g., 'morning-briefing')."
                    },
                    "steps": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Steps to record: each step is an object with 'tool' (an existing tool name), 'args' (its arguments), and 'verify' (a check: {'kind': 'ok'} or {'kind': 'field_contains', 'field': ..., 'substring': ...})."
                    },
                    "pattern": {
                        "type": "object",
                        "description": "Optional generalisation pattern: placeholder name -> full-match regular expression. Replay inputs must full-match it, or the replay is refused before anything runs."
                    }
                },
                "required": ["name", "steps"]
            },
            handler=self._tool_teach_record_sequence
        )

        self.register(
            name="teach_list_sequences",
            description="List the taught sequences stored on this machine, by name. Read-only: lists names, never runs anything.",
            parameters={"type": "object", "properties": {}},
            handler=self._tool_teach_list_sequences
        )

        self.register(
            name="teach_replay_sequence",
            description="Replay a stored taught sequence through VYUK's tools, one step at a time, each step checked before the next runs. Asks first (confirm-gated). A step that fails, is refused, or fails its check stops the replay honestly — no later step runs, and no success is claimed for work that did not verify. Inputs fill the sequence's placeholders; inputs that miss the recorded pattern refuse the whole replay before anything runs.",
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the stored sequence to replay."
                    },
                    "inputs": {
                        "type": "object",
                        "description": "Optional placeholder values for the sequence's pattern (e.g., {'city': 'Mumbai'})."
                    }
                },
                "required": ["name"]
            },
            handler=self._tool_teach_replay_sequence
        )

        self.register(
            name="wallpaper_state",
            description="Resolve which wallpaper the state pilot picks for an hour, from art paths YOU supply. Never applies anything — it only reports the state, the art it would use, and why nothing was applied. Applying is a separate confirm-gated tool (wallpaper_apply); field validation on real Windows is still owed, so this tool always reports applied: no. On non-Windows the pilot reports itself unavailable and nothing changes.",
            parameters={
                "type": "object",
                "properties": {
                    "opt_in": {
                        "type": "boolean",
                        "description": "Whether you opt into the wallpaper pilot. Even with opt-in true, this tool only resolves — it never applies."
                    },
                    "hour": {
                        "type": "integer",
                        "description": "Hour of day 0-23 to resolve for. Defaults to the current hour."
                    },
                    "entries": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Optional art entries: each is an object with 'state' (day, dusk, night, focus, or away) and 'art_path' (a path to YOUR OWN art file — no bundled or third-party art is ever used)."
                    }
                },
                "required": []
            },
            handler=self._tool_wallpaper_state
        )

        self.register(
            name="wallpaper_apply",
            description="Apply the wallpaper the state pilot picks for an hour, from art paths YOU supply — once, on demand, after human confirmation (confirm-gated). Applying requires opting into the wallpaper pilot (opt_in true) and the pilot reporting available; without opt-in, or when the pilot reports unavailable, or on non-Windows hosts, it resolves and reports why nothing was applied. It applies exactly the file resolution picks for that hour's state: no art entry, a missing file, a non-image, art inside the app's own package tree, or art escaping a supplied art directory all refuse honestly and nothing changes. On non-Windows hosts it refuses honestly and nothing changes. Field validation on real Windows (actual desktop change, persistence flags, multi-monitor behaviour) is still owed. This tool never applies bundled or third-party art and never substitutes its own art.",
            parameters={
                "type": "object",
                "properties": {
                    "opt_in": {
                        "type": "boolean",
                        "description": "Whether you opt into the wallpaper pilot. Applying requires opt-in true; without it, the tool resolves and reports why nothing was applied."
                    },
                    "hour": {
                        "type": "integer",
                        "description": "Hour of day 0-23 to resolve for. Defaults to the current hour."
                    },
                    "entries": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Art entries: each is an object with 'state' (day, dusk, night, focus, or away) and 'art_path' (a path to YOUR OWN art file — no bundled or third-party art is ever used). When art_dir is given, art paths are read relative to it."
                    },
                    "art_dir": {
                        "type": "string",
                        "description": "Optional directory your art lives in: when given, art paths are read relative to it and must stay inside it."
                    },
                    "monitor": {
                        "type": "string",
                        "description": "Optional: which screen to change — 'primary', 'left', 'right', 'second monitor', a device name like 'DISPLAY2', or 'all'. Without it the image is set on all monitors at once; with it, only that screen changes and the others are left exactly as they are."
                    }
                },
                "required": []
            },
            handler=self._tool_wallpaper_apply
        )

        self.register(
            name="wallpaper_revert",
            description="Restore the desktop wallpaper VYUK last changed, to exactly what it was immediately before that change (image + style) — once, on demand, after human confirmation (confirm-gated). The previous is saved automatically before every wallpaper change VYUK makes, in a single slot that always holds the desktop from immediately before the most recent VYUK change; reverting never clears it, so reverting twice restores the same wallpaper, and a later VYUK change replaces the slot. Optional monitor names ONE screen (primary, left, right, second monitor, a device name like DISPLAY2, or all) — without it the whole desktop is restored at once; with it, only that screen is restored and the others are left exactly as they are. When VYUK has never saved a previous (for example the current wallpaper was set by an older build or by Windows itself), or the saved file is gone, it refuses honestly and names Windows Settings > Personalization > Background instead of inventing a wallpaper. On non-Windows hosts it refuses honestly and nothing changes. Field validation on real Windows (actual desktop change, persistence flags, multi-monitor behaviour) is still owed.",
            parameters={
                "type": "object",
                "properties": {
                    "monitor": {
                        "type": "string",
                        "description": "Optional: which screen to restore — 'primary', 'left', 'right', 'second monitor', a device name like 'DISPLAY2', or 'all'. Without it the whole desktop is restored at once; with it, only that screen is restored."
                    },
                },
            },
            handler=self._tool_wallpaper_revert
        )

    def _tool_teach_record_sequence(self, name, steps, pattern=None):
        from .. import w8_wiring as _w8  # Phase 3 F3: file now in core/funnel/; import climbs one more level
        if not isinstance(name, str) or not name.strip():
            return ("I need a non-empty name to record a taught "
                    "sequence under.")
        if not isinstance(steps, list) or not steps:
            return ("A taught sequence needs at least one step — each "
                    "step names a tool, its arguments, and a check.")
        try:
            sequence = _w8.record_taught_sequence(
                name, steps, pattern=pattern)
        except Exception as exc:  # noqa: BLE001 — bad steps are data
            return "Couldn't record taught sequence %r: %s" % (name, exc)
        return ("Taught sequence %r recorded with %d step(s). Replay "
                "it with teach_replay_sequence — every replay asks "
                "first, and each step is checked before the next "
                "runs." % (name, len(sequence.steps)))

    def _tool_teach_list_sequences(self):
        from .. import w8_wiring as _w8  # Phase 3 F3: file now in core/funnel/; import climbs one more level
        names = _w8.list_taught_sequences()
        if not names:
            return "No taught sequences stored yet."
        return "Taught sequences (%d): %s" % (len(names), ", ".join(names))

    def _tool_teach_replay_sequence(self, name, inputs=None):
        from .. import w8_wiring as _w8  # Phase 3 F3: file now in core/funnel/; import climbs one more level
        if not isinstance(name, str) or not name.strip():
            return "I need the name of a taught sequence to replay."
        try:
            result = _w8.replay_taught_sequence(name, inputs or {})
        except ValueError as exc:
            return "Couldn't replay: %s" % (exc,)
        if result.status == "completed":
            return ("Replay of %r completed: all %d step(s) ran and "
                    "verified." % (name, result.steps_completed))
        if result.status == "halted":
            return ("Replay of %r halted after %d step(s): %s No "
                    "later step ran." % (name, result.steps_completed,
                                         result.reason))
        return ("Replay of %r refused: %s No step ran."
                % (name, result.reason))

    def _tool_wallpaper_state(self, opt_in=False, hour=None, entries=None):
        """GAPS-23 (F-V7): delegates to the module-level wallpaper_state_core() at the end of this file — the funnel-guarded entry point; the logic lives in the core function so white-box tests use the same code without calling a handler outside the funnel."""
        return wallpaper_state_core(opt_in=opt_in, hour=hour, entries=entries)

    def _tool_wallpaper_apply(self, opt_in=False, hour=None, entries=None,
                              art_dir=None, monitor=None):
        """GAPS-23 (F-V7): delegates to the module-level wallpaper_apply_core() at the end of this file — the funnel-guarded entry point; the logic lives in the core function so white-box tests use the same code without calling a handler outside the funnel."""
        return wallpaper_apply_core(opt_in=opt_in, hour=hour, entries=entries, art_dir=art_dir, monitor=monitor)

    def _tool_wallpaper_revert(self, monitor=None):
        # The funnel's confirm gate (wallpaper_revert is a
        # _CONFIRM_GATE_TOOLS member) already asked the human
        # before this handler runs; without that approval this
        # code never executes. The revert itself restores the
        # single save-previous slot (core/wallpaper): the desktop
        # from immediately before the most recent VYUK change.
        # It never clears the slot and never invents a wallpaper.
        # R4: an optional monitor names ONE screen (or "all");
        # without it the whole desktop is restored at once.
        from .. import wallpaper as _wallpaper  # Phase 3 F3: file now in core/funnel/; import climbs one more level
        result = _wallpaper.revert_wallpaper(monitor=monitor)
        if result.get("reverted"):
            image = result.get("image") or "(solid colour — no image)"
            where = (f" on {result.get('monitor')}"
                     if result.get("monitor") else "")
            return ("Wallpaper reverted to %(image)s (style: "
                    "%(style)s)%(where)s — the desktop from "
                    "immediately before the last change VYUK made."
                    % {"image": image, "style": result.get("style"),
                       "where": where})
        reason = result.get("reason", "")
        if reason == "windows-only":
            return ("Wallpaper not reverted: this machine is not "
                    "Windows, and the pilot applies on Windows only.")
        if reason == "no-previous-saved":
            return ("No previous wallpaper was saved — VYUK has not "
                    "changed the wallpaper since this version started "
                    "saving one, so there is nothing to restore. "
                    "Restore your old wallpaper in Windows Settings > "
                    "Personalization > Background.")
        if reason == "no-previous-saved-for-monitor":
            return ("No previous wallpaper was saved for that screen "
                    "— VYUK has not changed that screen's wallpaper "
                    "since this version started saving one, so there "
                    "is nothing to restore for it. Restore your old "
                    "wallpaper in Windows Settings > "
                    "Personalization > Background.")
        if reason == "monitor-gone":
            return ("That screen is no longer connected, so nothing "
                    "was reverted for it. The other screens were not "
                    "touched — name one of them to revert it.")
        if reason == "per-monitor-unavailable":
            return ("The per-monitor wallpaper service could not be "
                    "reached, so that screen was not reverted. The "
                    "saved record is kept; try again, or restore "
                    "your old wallpaper in Windows Settings > "
                    "Personalization > Background.")
        if reason == "partial":
            lines = []
            for device, outcome in sorted(
                    (result.get("per_monitor") or {}).items()):
                if outcome.get("reverted"):
                    lines.append(
                        f"{device}: reverted to "
                        f"{outcome.get('image') or '(solid colour)'}")
                else:
                    lines.append(
                        f"{device}: not reverted "
                        f"({outcome.get('reason')})")
            return ("Wallpaper reverted on some screens, not all: "
                    + "; ".join(lines) + ".")
        if reason == "previous-file-missing":
            return ("The saved previous wallpaper file no longer "
                    "exists (deleted, moved, or on a removed drive), "
                    "so nothing was reverted. The saved record is "
                    "kept in case the file comes back; or restore "
                    "your old wallpaper in Windows Settings > "
                    "Personalization > Background.")
        return ("Wallpaper not reverted: %s." % reason)


def wallpaper_apply_core(opt_in=False, hour=None, entries=None,
                              art_dir=None, monitor=None):
    # Same import discipline as _tool_wallpaper_state: wallpaper
    # is stdlib-only, so validation runs even where Qt is
    # absent. No w9_wiring import here — the apply path
    # resolves with wallpaper.state_for_hour +
    # wallpaper.resolve_art directly, the same primitives
    # resolution uses, so apply sets what resolution resolved.
    # The funnel's confirm gate (wallpaper_apply is a
    # _CONFIRM_GATE_TOOLS member) already asked the human
    # before this handler runs; without that approval this
    # code never executes.
    # Wallpaper-apply gates (ruled 2026-11-05, "same gates,
    # honestly reported"): opt-in on AND pilot reports
    # available AND host Windows AND the funnel confirm gate
    # approved. Confirm runs in the funnel before this
    # handler; Windows is enforced at the tail by
    # apply_wallpaper. The two gates enforced here mirror
    # resolution (wallpaper_for_now): opt-in first, then
    # pilot disposition. On any gate failure the handler
    # still resolves (state + art below) and says why
    # nothing was applied — it never claims applied on a
    # mere resolve. The pilot gate checks disposition only;
    # available_here (Windows-ness) is the Windows gate.
    from pathlib import Path  # noqa: E402 — lazy, pack discipline
    import datetime  # noqa: E402 — lazy, pack discipline
    from . import tool_result as _tr  # B1: failure shape — a
    # failed apply returns a funnel-shaped failure dict, never
    # a plain string (see set_wallpaper / display_control);
    # the message text is unchanged.
    from .. import wallpaper as _wallpaper  # Phase 3 F3: file now in core/funnel/; import climbs one more level
    if hour is not None and (not isinstance(hour, int)
                             or isinstance(hour, bool)
                             or not 0 <= hour <= 23):
        return "Hour must be a whole number from 0 to 23."
    built = []
    if entries is not None:
        if not isinstance(entries, list):
            return ("Art entries must be a list of {state, art_path} "
                    "mappings.")
        for entry in entries:
            if not isinstance(entry, dict):
                return ("Each art entry must be a {state, art_path} "
                        "mapping.")
            state = entry.get("state")
            path = entry.get("art_path")
            if state not in _wallpaper.STATES:
                return ("Unknown wallpaper state %r — states are: %s."
                        % (state, ", ".join(_wallpaper.STATES)))
            if not isinstance(path, str) or not path:
                return ("Each art entry needs a non-empty art_path "
                        "(your own art only).")
            built.append(_wallpaper.WallpaperEntry(
                state=state, art_path=path))
    if art_dir is not None and (not isinstance(art_dir, str)
                                or not art_dir):
        return "Art directory must be a non-empty path."
    use_hour = (datetime.datetime.now().hour if hour is None
                else hour)
    state = _wallpaper.state_for_hour(use_hour)
    art = _wallpaper.resolve_art(state, built)
    art_display = art or "none (no art entry for this state)"
    if not opt_in:
        return ("Wallpaper state for this hour: %(state)s. Art: "
                "%(art)s. Applied: no — wallpaper-opt-in-off"
                % {"state": state, "art": art_display})
    if _wallpaper.pilot_status().get("disposition") != "PILOT":
        return ("Wallpaper state for this hour: %(state)s. Art: "
                "%(art)s. Applied: no — pilot-status-not-pilot"
                % {"state": state, "art": art_display})
    if art is None:
        return ("No art entry for state %r — supply an entry for "
                "this state; nothing applied." % state)
    if art_dir is not None:
        root = Path(art_dir).resolve()
        candidate = (root / art).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return ("Art path %r escapes the art directory — "
                    "apply only uses art inside it; nothing "
                    "applied." % art)
    else:
        candidate = Path(art).resolve()
    if not candidate.is_file():
        return ("Art file %r is missing — nothing applied." % art)
    if not _wallpaper.is_image_file(candidate):
        return ("Art file %r is not a wallpaper image (BMP, JPEG, "
                "or PNG) — nothing applied." % art)
    # Bundled-art rule: art inside the app's own package tree
    # is never applied, even when an art_dir points at it.
    tree_root = Path(__file__).resolve().parents[2]
    try:
        candidate.relative_to(tree_root)
    except ValueError:
        pass
    else:
        return ("Art file %r lives inside the app's own package "
                "tree — apply only uses your own art, never "
                "bundled art; nothing applied." % art)
    result = _wallpaper.apply_wallpaper(candidate, monitor=monitor)
    if result.get("applied"):
        # R1b: read the registry value back — the SPI call
        # succeeding does not prove the wallpaper took.
        # R4: a per-monitor apply (no SPI fallback) reads back
        # per monitor via GetWallpaper instead — the registry
        # value does not track per-monitor wallpapers.
        from .. import tool_outcome as _outcome  # R1b: seam wiring

        if monitor is not None and not result.get("fell_back"):
            from .. import wallpaper_monitors as _wmon  # R4

            per_monitor = result.get("per_monitor") or {}
            targets = list(per_monitor) or [result.get("monitor")]
            for device in targets:
                current = _wmon.get_wallpaper_for(device)
                if current is None:
                    return _outcome.unverified(
                        "Wallpaper applied for state %s on %s: %s, "
                        "but the desktop value could not be read "
                        "back." % (state, device, candidate),
                        "per-monitor wallpaper unreadable")
                import os as _os  # noqa: E402 — lazy, pack discipline

                if _os.path.normcase(_os.path.normpath(current)) != \
                        _os.path.normcase(_os.path.normpath(
                            str(candidate))):
                    return _tr.failure_result(
                        _tr.WALLPAPER_NOT_SET_KIND,
                        f"Wallpaper not applied for state "
                        f"{state} on {device}: Windows still "
                        f"shows {current}, not {candidate}.")
            return _outcome.verified(
                "Wallpaper applied for state %s on %s: %s."
                % (state, result.get("monitor") or monitor, candidate),
                "per-monitor wallpaper reads %s as expected"
                % candidate)
        if result.get("fell_back"):
            # The per-monitor API was unavailable, so the set
            # went to ALL monitors via SPI — the reply says
            # so, never silently.
            current, evidence = _outcome.wallpaper_current()
            if current is None:
                return _outcome.unverified(
                    "Wallpaper applied for state %s: %s, but the "
                    "desktop value could not be read back "
                    "(per-monitor wallpaper was unavailable, so "
                    "this set ALL monitors instead)."
                    % (state, candidate),
                    evidence)
            import os as _os  # noqa: E402 — lazy, pack discipline

            if _os.path.normcase(_os.path.normpath(current)) != \
                    _os.path.normcase(_os.path.normpath(str(candidate))):
                return _tr.failure_result(
                    _tr.WALLPAPER_NOT_SET_KIND,
                    f"Wallpaper not applied for state {state}: "
                    f"Windows still shows {current}, not {candidate}.")
            return _outcome.verified(
                "Wallpaper applied for state %s: %s "
                "(per-monitor wallpaper was unavailable, so this "
                "set ALL monitors instead)."
                % (state, candidate),
                evidence)
        current, evidence = _outcome.wallpaper_current()
        if current is None:
            return _outcome.unverified(
                "Wallpaper applied for state %s: %s, but the "
                "desktop value could not be read back."
                % (state, candidate),
                evidence)
        import os as _os  # noqa: E402 — lazy, pack discipline

        if _os.path.normcase(_os.path.normpath(current)) != \
                _os.path.normcase(_os.path.normpath(str(candidate))):
            return _tr.failure_result(
                _tr.WALLPAPER_NOT_SET_KIND,
                f"Wallpaper not applied for state {state}: "
                f"Windows still shows {current}, not {candidate}.")
        return _outcome.verified(
            "Wallpaper applied for state %s: %s." % (state, candidate),
            evidence)
    reason = result.get("reason", "")
    if reason == "windows-only":
        return ("Wallpaper not applied: this machine is not "
                "Windows, and the pilot applies on Windows only.")
    # B1: the apply was attempted and the seam refused it —
    # a funnel-shaped failure, never a success payload.
    return _tr.failure_result(
        _tr.WALLPAPER_NOT_SET_KIND,
        "Wallpaper not applied: %s." % reason)


def wallpaper_state_core(opt_in=False, hour=None, entries=None):
    # wallpaper is stdlib-only, so validation runs even
    # where Qt is absent; w9_wiring is imported last because
    # it pulls chat_render (PySide6) at module scope —
    # in the real app Qt is present, and in the lab the
    # wallpaper tests run on the Qt interpreter.
    from .. import wallpaper as _wallpaper  # Phase 3 F3: file now in core/funnel/; import climbs one more level
    if hour is not None and (not isinstance(hour, int)
                             or isinstance(hour, bool)
                             or not 0 <= hour <= 23):
        return "Hour must be a whole number from 0 to 23."
    built = []
    if entries is not None:
        if not isinstance(entries, list):
            return ("Art entries must be a list of {state, art_path} "
                    "mappings.")
        for entry in entries:
            if not isinstance(entry, dict):
                return ("Each art entry must be a {state, art_path} "
                        "mapping.")
            state = entry.get("state")
            path = entry.get("art_path")
            if state not in _wallpaper.STATES:
                return ("Unknown wallpaper state %r — states are: %s."
                        % (state, ", ".join(_wallpaper.STATES)))
            if not isinstance(path, str) or not path:
                return ("Each art entry needs a non-empty art_path "
                        "(your own art only).")
            built.append(_wallpaper.WallpaperEntry(
                state=state, art_path=path))
    from .. import w9_wiring as _w9  # Phase 3 F3: file now in core/funnel/; import climbs one more level
    try:
        resolved = _w9.wallpaper_for_now(
            bool(opt_in), entries=built or None, hour=hour)
    except Exception as exc:  # noqa: BLE001 — bad hour is data
        return "Couldn't resolve wallpaper state: %s" % (exc,)
    art = resolved.get("art") or "none (no art entry for this state)"
    return ("Wallpaper state for this hour: %(state)s. Art: "
            "%(art)s. Applied: no — %(reason)s"
            % {"state": resolved.get("state"), "art": art,
               "reason": resolved.get("reason")})
