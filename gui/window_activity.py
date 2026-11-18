"""Activity feed + egress ledger surfaces (v5.56, Wave 2,
Stream F): PF-2 activity history with undo, PF-23 egress
ledger — the user-facing companions to Stream E's record
family (core/record_family.py).

CONTRACT (Stream E builds it; this module codes to the
contract exactly — if names differ at integration, the
coordinator reconciles; this module degrades, it never
crashes):
  read_activity(limit: int) -> list
  read_ledger(limit: int) -> list
  register_undo(action_id: str, undo_callable) -> None
  verify_chain() -> bool
ORDER (Stream E coordination note): read_activity() and
read_ledger() return NEWEST-FIRST (matching core/audit.py's
read_recent convention). These surfaces display entries IN
THE ORDER RETURNED — no re-sort, no reversal. If a future
design wants chronological order, it reverses at display
time and says so; this version does not.

Surfaces are READ-ONLY displays: they never write the chain
directly, and they never call register_undo (registration is
the actor's job at action time, not the viewer's).
Entries are normalised best-effort (the contract pins the
function names, not the entry shape): title/text/action,
when/timestamp, action_id/id, undoable/can_undo are all
accepted; an entry that is not a dict renders as its string.

UNDO — resolved at build time: Stream E's record_family
landed DURING this stream's build with execute_undo
(action_id) as the executor (plus pending_undos() listing
live undoable action ids). perform_undo() tries
execute_undo first, then defensive fallbacks. The activity
dialog renders one Undo button per pending undo id — the
feed entries themselves carry event ids, not undo action
ids, so buttons are driven by pending_undos(), never by
guessing a link from a feed line. When no undo is pending,
no Undo button is shown at all (nothing to pretend with).
When core/record_family is absent, every function returns
available False and the dialogs render "not yet available"
— the surface degrades, the app does not break.

UNIFIED FEED (GAPS-27, M-V4): unified_feed() is the opt-in
view that also sees the per-tool ledgers, the cost ledger and
data/activity.log (core/activity_feed.read_unified). The
DEFAULT feed (activity_feed above) stays chain-only human
lines — the unified view is a separate surface, never a
change to what the Activity page shows by default.

Like gui/window_trust, this module imports no Qt at the top:
pure functions first, dialog builders (lazy Qt) at the bottom.
"""

#: Executor attribute names tried, in order, on the record
#: family module. Stream E landed execute_undo() — that is
#: the real executor; the rest are defensive fallbacks for a
#: renamed contract, kept so a rename degrades instead of
#: breaking (see perform_undo()).
_UNDO_EXECUTORS = ("execute_undo", "run_undo", "undo_action", "undo")


def _record_family():
    """The record_family module, or None when absent /
    unimportable. Never raises."""
    try:
        from voice_assistant.core.gui_facade import record_family

        return record_family
    except Exception:  # noqa: BLE001
        return None


def _normalise_entry(entry):
    """One raw record-family entry -> {"action_id", "title",
    "when", "undoable", "reversible"}. Real entry shape (audit_chain.read):
    {id, ts, kind, data, hashes}, newest-first. Activity data
    carries {text}; egress data carries {when, where, why} —
    both are rendered in plain language. `action_id` here is
    the ENTRY id (for display/tracing only); undo linkage is
    pending_undo_ids(), not this field — an event id is not
    an undo action id, and conflating them would undo the
    wrong thing. `reversible` is the PF-2 marker: False means
    the producer knows the action cannot be undone (funnel
    tool-call lines); True/None means undoable or unknown.
    Never raises; non-dicts render as title."""
    try:
        if not isinstance(entry, dict):
            return {"action_id": None, "title": str(entry),
                    "when": "", "undoable": False, "reversible": None}
        data = entry.get("data")
        if not isinstance(data, dict):
            data = {}
        title = ""
        if data.get("text"):
            title = str(data.get("text"))
        elif data.get("where") or data.get("why"):
            where = str(data.get("where") or "")
            why = str(data.get("why") or "")
            title = f"{where} — {why}".strip(" —") if (where or why) \
                else ""
        if not title:
            for key in ("title", "text", "action", "description",
                        "what"):
                value = entry.get(key)
                if value:
                    title = str(value)
                    break
        if not title:
            title = str(entry)
        when = ""
        for value in (data.get("when"), entry.get("ts"),
                      entry.get("when"), entry.get("timestamp")):
            if value:
                when = str(value)
                break
        undoable = False
        for source in (data, entry):
            for key in ("undoable", "can_undo", "has_undo"):
                if key in source:
                    undoable = bool(source.get(key))
                    break
        reversible = None
        if isinstance(data.get("reversible"), bool):
            reversible = data["reversible"]
        return {"action_id": str(entry.get("id")) if entry.get("id")
                else None, "title": title, "when": when,
                "undoable": undoable, "reversible": reversible}
    except Exception:  # noqa: BLE001
        return {"action_id": None, "title": str(entry),
                "when": "", "undoable": False, "reversible": None}


def _read(fn_name, limit):
    """Read via a record_family function: {"available",
    "entries" (normalised, newest-first as returned),
    "error"}. Never raises."""
    mod = _record_family()
    if mod is None:
        return {"available": False, "entries": [],
                "error": "record family not available yet"}
    fn = getattr(mod, fn_name, None)
    if not callable(fn):
        return {"available": False, "entries": [],
                "error": f"record family has no {fn_name}()"}
    try:
        raw = fn(int(limit))
    except TypeError:
        try:
            raw = fn()
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "entries": [],
                    "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "entries": [],
                "error": str(exc)}
    if not isinstance(raw, (list, tuple)):
        return {"available": False, "entries": [],
                "error": f"{fn_name}() did not return a list"}
    return {"available": True,
            "entries": [_normalise_entry(e) for e in raw],
            "error": None}


def activity_feed(limit=50):
    """PF-2 feed: read_activity(limit), newest-first as
    returned. Never raises."""
    return _read("read_activity", limit)


def egress_ledger(limit=50):
    """PF-23 ledger: read_ledger(limit), newest-first as
    returned. Never raises."""
    return _read("read_ledger", limit)


def _activity_feed_mod():
    """The core/activity_feed module, or None when absent /
    unimportable. Never raises. Reached through the GUI facade
    (core/gui_facade — the same seam the GUI's record_family
    comes through; GUI files never import core directly), so
    the chain it reads is the one the GUI sees."""
    try:
        from voice_assistant.core.gui_facade import activity_feed

        return activity_feed
    except Exception:  # noqa: BLE001
        return None


def unified_feed(limit=50, include=None):
    """GAPS-27 (M-V4): the unified view — chain human lines +
    per-tool ledgers + cost turns + data/activity.log, newest
    first, each entry source-tagged. Returns the same shape as
    activity_feed() ({"available", "entries", "error"}) with
    entries normalised to {action_id, title, when, undoable,
    reversible} plus "source" and "store". Never raises; the
    DEFAULT feed (activity_feed) is untouched by this."""
    mod = _activity_feed_mod()
    if mod is None:
        return {"available": False, "entries": [],
                "error": "unified activity feed not available yet"}
    try:
        raw = mod.read_unified(int(limit), include=include) \
            if include is not None else mod.read_unified(int(limit))
    except TypeError:
        try:
            raw = mod.read_unified()
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "entries": [],
                    "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "entries": [],
                "error": str(exc)}
    if not isinstance(raw, (list, tuple)):
        return {"available": False, "entries": [],
                "error": "read_unified() did not return a list"}
    entries = []
    for entry in raw:
        if not isinstance(entry, dict):
            entries.append({"action_id": None, "title": str(entry),
                            "when": "", "undoable": False,
                            "reversible": None, "source": "",
                            "store": ""})
            continue
        entries.append({
            "action_id": None,
            "title": str(entry.get("title", "")),
            "when": str(entry.get("ts", "")),
            "undoable": False, "reversible": None,
            "source": str(entry.get("source", "")),
            "store": str(entry.get("store", "")),
        })
    return {"available": True, "entries": entries, "error": None}


def chain_status():
    """Hash-chain verdict: {"available", "verified",
    "error"}. verified None = unknown (module / function
    absent). Never raises."""
    mod = _record_family()
    if mod is None:
        return {"available": False, "verified": None,
                "error": "record family not available yet"}
    fn = getattr(mod, "verify_chain", None)
    if not callable(fn):
        return {"available": False, "verified": None,
                "error": "record family has no verify_chain()"}
    try:
        return {"available": True, "verified": bool(fn()),
                "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "verified": None,
                "error": str(exc)}


def undo_available():
    """True when the record family exposes an undo executor
    AND the module itself is present. Never raises."""
    try:
        mod = _record_family()
        if mod is None:
            return False
        return any(callable(getattr(mod, name, None))
                   for name in _UNDO_EXECUTORS)
    except Exception:  # noqa: BLE001
        return False


def pending_undo_ids():
    """Live undoable action ids (record_family.pending_undos),
    or [] when the module/function is absent. These ids — not
    feed entry ids — are what the activity dialog's Undo
    buttons run. Never raises."""
    try:
        mod = _record_family()
        if mod is None:
            return []
        fn = getattr(mod, "pending_undos", None)
        if not callable(fn):
            return []
        ids = fn()
        if isinstance(ids, (list, tuple)):
            return [str(i) for i in ids]
        return []
    except Exception:  # noqa: BLE001
        return []


def perform_undo(action_id):
    """Run an undo for action_id through the discovered
    executor. Returns {"ok", "action_id", ...}; ok False with
    reason "no_undo_executor" when the contract gap applies
    (see module docstring). Never raises; never writes the
    chain directly — the executor owns that."""
    try:
        if not action_id:
            return {"ok": False, "action_id": action_id,
                    "reason": "no_action_id"}
        mod = _record_family()
        if mod is None:
            return {"ok": False, "action_id": action_id,
                    "reason": "record_family_unavailable"}
        for name in _UNDO_EXECUTORS:
            fn = getattr(mod, name, None)
            if callable(fn):
                try:
                    result = fn(str(action_id))
                except TypeError:
                    try:
                        result = fn(action_id)
                    except Exception as exc:  # noqa: BLE001
                        return {"ok": False, "action_id": action_id,
                                "error": str(exc)}
                except Exception as exc:  # noqa: BLE001
                    return {"ok": False, "action_id": action_id,
                            "error": str(exc)}
                # The executor's own verdict propagates: a
                # one-shot undo already spent (or never
                # registered) reports ok False from
                # execute_undo itself — surfacing ok True for
                # a call that undid nothing would be a lie.
                if isinstance(result, dict) and "ok" in result:
                    return {"ok": bool(result.get("ok")),
                            "action_id": action_id,
                            "executor": name, "result": result}
                return {"ok": True, "action_id": action_id,
                        "executor": name, "result": result}
        return {"ok": False, "action_id": action_id,
                "reason": "no_undo_executor"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "action_id": action_id,
                "error": str(exc)}


def render_feed_text(feed):
    """A feed/ledger dict as plain display text (newest
    first, as returned). Never raises."""
    try:
        if not feed.get("available"):
            return ("Not yet available: "
                    + str(feed.get("error") or "record family "
                                               "not available yet"))
        entries = feed.get("entries") or []
        if not entries:
            return "Nothing recorded yet."
        lines = []
        for entry in entries:
            when = f" ({entry['when']})" if entry.get("when") else ""
            undo = " [undoable]" if entry.get("undoable") else ""
            irrevers = " [irreversible]" \
                if entry.get("reversible") is False else ""
            lines.append(f"{entry.get('title', '')}{when}{undo}{irrevers}")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        return "Not yet available."


# ---------------------------------------------------------------------------
# Dialog builders — Qt imported lazily here (see window_trust
# for the pattern). Builders return (dialog, widgets) without
# exec()ing.
# ---------------------------------------------------------------------------

def _build_list_dialog(parent, title, read_fn):
    from PySide6.QtWidgets import (
        QDialog,
        QHBoxLayout,
        QLabel,
        QListWidget,
        QPushButton,
        QVBoxLayout,
    )
    from voice_assistant.gui.style import DIALOG_STYLE

    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    # v5.57 (redo): the dialog in the house dark language.
    dlg.setStyleSheet(DIALOG_STYLE)
    dlg.resize(720, 480)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(20, 20, 20, 16)
    lay.setSpacing(10)
    status = QLabel("")
    status.setObjectName("muted")
    status.setWordWrap(True)
    lay.addWidget(status)
    view = QListWidget()
    lay.addWidget(view, stretch=1)
    btn_row = QHBoxLayout()
    btn_row.addStretch(1)
    btn_refresh = QPushButton("Refresh")
    btn_close = QPushButton("Close")
    btn_row.addWidget(btn_refresh)
    btn_row.addWidget(btn_close)
    # UI interaction slice: the read-only dialog's safe button is
    # the default — Enter closes, never refreshes into a side
    # effect the user did not ask for.
    btn_close.setDefault(True)
    lay.addLayout(btn_row)

    def _fill():
        feed = read_fn()
        chain = chain_status()
        if chain.get("available"):
            verdict = "verified" if chain.get("verified") \
                else "CHAIN MISMATCH — records may be tampered with"
            status.setText(f"Record chain: {verdict}.")
        else:
            status.setText("Record chain: unavailable.")
        view.clear()
        if not feed.get("available"):
            view.addItem(render_feed_text(feed))
            return
        entries = feed.get("entries") or []
        if not entries:
            # v5.57 (redo): the empty state says what happened
            # and what happens next. Worded for both callers of
            # this shared builder (activity feed, ledger).
            view.addItem(
                "Nothing recorded yet — new records will appear "
                "here as VYUK works.")
            return
        can_undo = undo_available()
        for entry in entries:
            when = f" ({entry['when']})" if entry.get("when") else ""
            undo = " [undoable]" if entry.get("undoable") else ""
            irrevers = " [irreversible]" \
                if entry.get("reversible") is False else ""
            # GAPS-27 (M-V4): unified-feed entries carry their
            # source ("activity" / "ledger" / "cost" / "log") and
            # store; chain-only entries carry none, so their
            # rendering is unchanged.
            source = entry.get("source") or ""
            store = entry.get("store") or ""
            tag = f" [{source}:{store}]" if source and store else \
                (f" [{source}]" if source else "")
            view.addItem(f"{entry.get('title', '')}{when}{undo}{irrevers}{tag}")
        # Undo acts on the newest undoable entry (the list is
        # newest-first, so that is entries[0] when undoable);
        # the button below is wired by the caller variant.
        _fill.latest = entries
        _fill.can_undo = can_undo

    _fill.latest = []
    _fill.can_undo = False
    btn_refresh.clicked.connect(_fill)
    btn_close.clicked.connect(dlg.accept)
    _fill()
    widgets = {"status": status, "view": view,
               "refresh": btn_refresh, "close": btn_close,
               "fill": _fill}
    return dlg, widgets


def build_activity_dialog(parent=None):
    """PF-2 dialog: the activity feed (newest first) + one
    Undo button per PENDING undo action id (from
    pending_undos() — feed lines carry event ids, not undo
    ids, so buttons are never guessed from a line). With no
    pending undo, no Undo button appears. Returns
    (dialog, widgets)."""
    from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

    dlg, widgets = _build_list_dialog(
        parent, "Recent activity", lambda: activity_feed(50))
    undo_label = QLabel("Undoable actions:")
    undo_box = QWidget()
    undo_layout = QVBoxLayout(undo_box)
    undo_layout.setContentsMargins(0, 0, 0, 0)
    dlg.layout().addWidget(undo_label)
    dlg.layout().addWidget(undo_box)
    widgets["undo_label"] = undo_label
    widgets["undo_box"] = undo_box

    def _sync_undo():
        while undo_layout.count():
            item = undo_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        ids = pending_undo_ids()
        undo_label.setVisible(bool(ids))
        undo_box.setVisible(bool(ids))
        for action_id in ids:
            btn = QPushButton(f"Undo {action_id}")
            btn.clicked.connect(
                lambda _=False, aid=action_id: _do_undo(aid))
            undo_layout.addWidget(btn)
        _sync_undo.buttons = undo_layout.count()

    def _do_undo(action_id):
        result = perform_undo(action_id)
        if result.get("ok"):
            widgets["fill"]()
        _sync_undo()
        return result

    _sync_undo.buttons = 0
    widgets["refresh"].clicked.connect(_sync_undo)
    _sync_undo()
    widgets["sync_undo"] = _sync_undo
    widgets["do_undo"] = _do_undo
    return dlg, widgets


def build_ledger_dialog(parent=None):
    """PF-23 dialog: the egress ledger (newest first),
    read-only. Returns (dialog, widgets)."""
    return _build_list_dialog(parent, "Egress ledger",
                              lambda: egress_ledger(50))


def build_unified_dialog(parent=None):
    """GAPS-27 (M-V4) dialog: the unified activity view — chain
    human lines + per-tool ledgers + cost turns +
    data/activity.log, newest first, each line source-tagged.
    Read-only. Returns (dialog, widgets)."""
    return _build_list_dialog(parent, "All activity",
                              lambda: unified_feed(50))
