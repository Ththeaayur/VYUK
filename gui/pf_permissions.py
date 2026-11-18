"""PF-18 permissions surface (GUI split #3).

Wired in the PF-18 widget batch: Apply writes real grants through
the Settings-only store API, re-reads the store, and reports
per-switch success/failure honestly. Unknown reads as a
half-checked switch — visibly neither on nor off.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .pf_surfaces_helpers import _card, _muted


# ================================================================ PF-18
# Permissions page: everything she can touch on one page,
# with switches that write real grants.

PERMISSION_GRANTED = "granted"
PERMISSION_DENIED = "denied"
PERMISSION_UNKNOWN = "unknown"

PERMISSION_DEFS = (
    ("microphone", "Microphone",
     "Hearing you — voice commands, dictation, and the "
     "microphone meter."),
    ("camera", "Camera",
     "Seeing the room briefly — only when a feature you "
     "switched on asks for it, such as ambient light."),
    ("screen", "Screen",
     "Looking at the screen when you ask her to — "
     "describing what is there, never silently."),
    ("files", "Files",
     "Reading and changing files you point her at, "
     "within the folders you allow."),
    ("browser", "Browser control",
     "Driving the browser for tasks you ask for, under "
     "the confirm gate."),
    ("home_devices", "Home devices",
     "Talking to devices on your home network that you "
     "have connected, and nothing else."),
)

_PERMISSION_TITLES = {pid: title for pid, title, _ in PERMISSION_DEFS}


def _load_grants():
    """The grants store module, either import mode (the GUI
    dual-import convention: installed package first, tree
    root fallback). Exactly one identity resolves per mode,
    so widget writes land in the store the funnel reads."""
    try:
        from voice_assistant.core.gui_facade import permission_grants as pg
    except Exception:  # noqa: BLE001 — lab mode has no package
        from core.gui_facade import permission_grants as pg
    return pg


def _load_record_family():
    """The record-family module, same dual-import convention."""
    try:
        from voice_assistant.core.gui_facade import record_family as rf
    except Exception:  # noqa: BLE001 — lab mode has no package
        from core.gui_facade import record_family as rf
    return rf


def _load_mirrors_entry():
    """read_home_mirrors, the same dual-import convention as
    _load_grants. Raises in a lab without the module — the
    caller treats that as no readable mirrors, never a crash."""
    try:
        from voice_assistant.core.gui_facade import w9_wiring_lan as lan
    except Exception:  # noqa: BLE001 — lab mode has no package
        from core.gui_facade import w9_wiring_lan as lan
    return lan.read_home_mirrors


def _load_platform_probe():
    """The platform (Windows) permission probe module, the
    same dual-import convention as _load_grants. Returns None
    in a lab without the module — the caller treats that as
    an unreadable OS side (unknown), never a crash."""
    try:
        from voice_assistant.core.gui_facade import platform_permission_probe
    except Exception:  # noqa: BLE001 — lab mode has no package
        try:
            from core.gui_facade import platform_permission_probe
        except Exception:  # noqa: BLE001 — probe module absent
            return None
    return platform_permission_probe


# Windows privacy pages the D2 jump link opens (user-pressed
# only — the probe never opens Settings on its own).
OS_SETTINGS_PAGES = {
    "microphone": "ms-settings:privacy-microphone",
    "camera": "ms-settings:privacy-webcam",
}


def _os_field(result, name, default="unknown"):
    """One field off a probe result that may be a dataclass,
    a dict, or a SimpleNamespace (caller-supplied fakes)."""
    if result is None:
        return default
    if isinstance(result, dict):
        return result.get(name, default)
    return getattr(result, name, default)


# ------------------------------------------------------------------ #
# State-mirrors device row (ruled 2026-11-05: display-only,
# device-gated, terms-gated, freshness-stamped). The row shows
# mirrored device states when devices are connected, and the
# honest-empty message otherwise. It reads ONLY through the
# read_home_mirrors entry (PF-18 hook gate FIRST inside it),
# never through the builder directly, so a denied grant blinds
# the row before any reader runs. It shows state, never an
# affordance that sets, controls, writes, toggles, or drives
# a device — there is deliberately no such call in this file.
# A freshness stamp is always shown as an age ("read 5s ago");
# a snapshot without one is labelled "freshness unknown" and
# never presented as live. An unreachable device is labelled
# unreachable and its state is never presented as current.

_EMPTY_DEVICES_MESSAGE = ("Nothing connected yet — no home devices "
                          "have been connected.")


def _format_device_age(freshness, now=None):
    """Age text for a freshness stamp, or None when the
    snapshot carries no usable stamp. Negative ages (clock
    skew) clamp to zero — a future stamp is not "live"."""
    if freshness is None:
        return None
    try:
        age = float(now if now is not None else time.time()) \
            - float(freshness)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(age):
        return None
    if age < 0:
        age = 0
    if age < 60:
        return f"read {age:.0f}s ago"
    if age < 3600:
        return f"read {age / 60:.0f}m ago"
    return f"read {age / 3600:.1f}h ago"


def _summarise_device_state(state):
    """One-line summary of an opaque state dict, keys sorted.
    Truncated with an ellipsis (never silently cut) so a long
    state cannot blow out the card."""
    if not isinstance(state, dict) or not state:
        return ""
    parts = [f"{k}: {v}" for k, v in
             sorted(state.items(), key=lambda kv: str(kv[0]))]
    text = ", ".join(parts)
    if len(text) > 200:
        text = text[:197] + "…"
    return text


def _device_lines(mirror_result):
    """Label strings for a read_home_mirrors-shaped result.
    Blinded, not-connected, malformed, or empty results all
    degrade to the honest-empty message — never a fabricated
    device list, never a spinner implying loading is coming."""
    if not isinstance(mirror_result, dict):
        return [_EMPTY_DEVICES_MESSAGE]
    if mirror_result.get("blinded") \
            or not mirror_result.get("connected"):
        return [_EMPTY_DEVICES_MESSAGE]
    snapshots = mirror_result.get("snapshots") or {}
    if not isinstance(snapshots, dict) or not snapshots:
        return [_EMPTY_DEVICES_MESSAGE]
    lines = []
    for key in sorted(snapshots):
        snap = snapshots[key]
        if not isinstance(snap, dict):
            continue
        if not snap.get("reachable", True):
            lines.append(f"{key}: unreachable — no current "
                         f"state to show.")
            continue
        summary = _summarise_device_state(snap.get("state"))
        age = _format_device_age(snap.get("freshness"))
        if age is None:
            if summary:
                lines.append(f"{key}: {summary} (freshness "
                             f"unknown — not shown as live.)")
            else:
                lines.append(f"{key}: freshness unknown — "
                             f"not shown as live.")
        elif summary:
            lines.append(f"{key}: {summary} ({age}.)")
        else:
            lines.append(f"{key}: {age}.")
    return lines or [_EMPTY_DEVICES_MESSAGE]


def _load_audio_manager():
    """The module-level voice manager (the live voice input
    owner). May raise in a lab without audio — the caller
    treats that as a skipped registration, never a failure."""
    try:
        from voice_assistant.core.gui_facade import audio_manager
    except Exception:  # noqa: BLE001 — lab mode has no package
        from core.gui_facade import audio_manager
    return audio_manager


def _state_word(state):
    if state == PERMISSION_GRANTED:
        return "on"
    if state == PERMISSION_DENIED:
        return "off"
    return "not set"


@dataclass
class PermissionEntry:
    permission_id: str
    title: str
    used_for: str
    state: str = PERMISSION_UNKNOWN
    state_source: str = "Settings switch — the durable grant."
    revoke_seam: str = ""  # Wired in the PF-18 widget batch: no seam remains.
    # Windows side (B3): the OS privacy state for microphone /
    # camera only — the other four ids have no Windows switch,
    # so these stay None and no OS line is rendered for them.
    # VYUK-side and OS-side are separate fields, never merged.
    os_state: str | None = None  # "allowed"/"denied"/"unknown"/None
    os_reason: str = ""
    diagnosis: str = ""  # Mic only: the three-way distinction.

    @property
    def is_known(self):
        return self.state in (PERMISSION_GRANTED, PERMISSION_DENIED)

    def state_line(self):
        if self.state == PERMISSION_GRANTED:
            return "On — VYUK can use this right now."
        if self.state == PERMISSION_DENIED:
            return "Off — VYUK cannot use this."
        if self.permission_id == "home_devices":
            # D10: no home devices exist yet, so an unknown
            # home-devices grant is "nothing connected yet",
            # not "ask each time" — there is nothing to ask
            # about until the first device lands.
            return ("Nothing connected yet — no home devices "
                    "have been connected.")
        return ("Not set yet — VYUK will ask each time a "
                "use comes up.")

    def os_state_line(self):
        """The Windows side, rendered separately from the
        VYUK-side state_line — the two are never merged into
        one claim. None (no Windows switch for this id) renders
        as nothing; the widget only adds the label for mic /
        camera, so this is a second guard, not the only one."""
        if self.os_state is None:
            return ""
        if self.os_state == "allowed":
            return ("Windows: on — Windows allows desktop apps "
                    "to use this.")
        if self.os_state == "denied":
            return "Windows: off — " + (self.os_reason or
                                         "Windows has it off.")
        return ("Windows: couldn't be read — unknown, not a "
                "denial.")


def permissions_model(probe_results=None, platform_probe=None,
                      device_reader=None):
    """Build the permission list. Without probe results the
    state comes from the durable grants store (the honest
    readable state — Settings is the store's only writer,
    so the page shows exactly what the funnel enforces).
    A caller-supplied probe result overrides the store for
    that id; a probe result outside the three states is
    treated as unknown, never upgraded to granted.

    B3: the Windows side is read alongside the store (the
    platform probe — ``platform_probe`` is an injected
    callable returning {pid: probe result} for tests; None
    means the real probe). For microphone and camera each
    entry also carries ``os_state`` / ``os_reason`` (the OS
    privacy state, separately from the VYUK-side state) and,
    for the microphone, a ``diagnosis`` (the three-way
    distinction: denied by you / denied by Windows / no
    usable device — ``device_reader`` is an injected
    microphone-device reader for tests; None means the real
    MMDevice reader). The probe is advisory only: a denied
    VYUK grant still refuses whatever Windows says, and a
    probe failure reads unknown, never a guess."""
    pg = _load_grants()
    probes = probe_results or {}
    probe_mod = _load_platform_probe()
    if platform_probe is not None:
        try:
            os_results = platform_probe() or {}
        except Exception:  # noqa: BLE001 — probe failure is unknown
            os_results = {}
    elif probe_mod is not None:
        try:
            os_results = probe_mod.probe_platform_permissions()
        except Exception:  # noqa: BLE001 — probe failure is unknown
            os_results = {}
    else:
        os_results = {}
    if device_reader is not None:
        try:
            device = device_reader() or {"device": "unknown"}
        except Exception:  # noqa: BLE001 — probe failure is unknown
            device = {"device": "unknown"}
    elif probe_mod is not None:
        try:
            device = probe_mod.probe_microphone_device()
        except Exception:  # noqa: BLE001 — probe failure is unknown
            device = {"device": "unknown"}
    else:
        device = {"device": "unknown"}
    entries = []
    for permission_id, title, used_for in PERMISSION_DEFS:
        if permission_id in probes:
            state, source = probes[permission_id]
        else:
            try:
                state = pg.get_state(permission_id)
            except Exception:  # noqa: BLE001 — never lie
                state = PERMISSION_UNKNOWN
            source = "Settings switch — the durable grant."
        if state not in (PERMISSION_GRANTED, PERMISSION_DENIED,
                         PERMISSION_UNKNOWN):
            state = PERMISSION_UNKNOWN
        entry = PermissionEntry(
            permission_id, title, used_for, state, source)
        if permission_id in ("microphone", "camera"):
            os_result = os_results.get(permission_id)
            os_state = _os_field(os_result, "state")
            if os_state not in ("allowed", "denied"):
                # The probe speaks "allowed"/"denied"/"unknown";
                # anything else (incl. a missing probe) is unknown.
                os_state = "unknown"
            entry.os_state = os_state
            entry.os_reason = str(
                _os_field(os_result, "reason", ""))
            if permission_id == "microphone" \
                    and probe_mod is not None:
                try:
                    headline, fix = \
                        probe_mod.diagnose_microphone(
                            state, os_result
                            if os_result is not None else {},
                            device)
                    entry.diagnosis = headline + " " + fix
                except Exception:  # noqa: BLE001 — no diagnosis
                    entry.diagnosis = ""
        entries.append(entry)
    return entries


def apply_permission_changes(entries, desired, how="Settings"):
    """Apply real grant changes (the Settings Apply path).

    ``desired`` maps permission_id -> "granted"/"denied" for
    the switches the user changed. Each change is written
    through the Settings-only store API, then re-read: a
    change counts as applied ONLY when the re-read matches
    the desired state. A failed write or a re-read mismatch
    is reported as failed, never as a silent success. A
    successful change logs one Activity line naming the
    permission and the old -> new state — with an Undo
    action (UI rethink Build 4 §4.8: the receipt for a
    durable grant change; Undo restores the old state).
    Logging and teardown registration never break a write:
    their failures are recorded, not raised.

    ``how`` (Build 4 §4.4) names the tap for the
    Always-allow screen's granted-when line — "Settings"
    (the switches), "first-run setup" (the wizard),
    "confirm dialog" (the mic ask) — and is recorded on
    the store's change event.

    Before writing, the live voice manager's stop is
    registered as a mic teardown hook (deduped by the
    registry): Settings is the store's only writer, so
    every real mic denial passes through here, and the
    hook guarantees an in-flight voice session is torn
    down on the spot. No live sidecar stop is registered
    here — no product sidecar instance exists yet (only
    test constructions); when one lands, its stop registers
    at its construction/start site.

    Returns {"changed": [...], "failed": {pid: reason},
    "results": {pid: {"ok", "old", "new", "error"}},
    "teardown_registration": "ok" | "skipped: <reason>"}.
    """
    pg = _load_grants()
    try:
        audio_manager = _load_audio_manager()
        pg.register_mic_teardown(audio_manager.stop)
        teardown = "ok"
    except Exception as exc:  # noqa: BLE001 — never break Apply
        teardown = f"skipped: {exc}"
    results = {}
    for pid, want in desired.items():
        if want not in (PERMISSION_GRANTED, PERMISSION_DENIED):
            results[pid] = {
                "ok": False, "old": "", "new": "",
                "error": f"invalid desired state {want!r}"}
            continue
        try:
            old = pg.get_state(pid)
        except Exception:  # noqa: BLE001 — never lie
            old = PERMISSION_UNKNOWN
        try:
            written = pg.set_permission_state(
                pid, want, via="settings", how=how)
        except Exception as exc:  # noqa: BLE001 — report, not raise
            written = False
            results[pid] = {
                "ok": False, "old": old, "new": old,
                "error": f"write failed: {exc}"}
            continue
        try:
            new = pg.get_state(pid)
        except Exception:  # noqa: BLE001 — never claim success blind
            new = old
        ok = bool(written) and new == want
        if not ok:
            results[pid] = {
                "ok": False, "old": old, "new": new,
                "error": ("write did not take effect"
                          if written else "write failed")}
            continue
        results[pid] = {"ok": True, "old": old, "new": new,
                        "error": ""}
        if old != new:
            try:
                rf = _load_record_family()
                # Build 4 §4.8: the receipt carries Undo —
                # restoring the old state (a "setting" undo).
                # One-shot and process-local by the
                # record-family design; a failed registration
                # leaves the line without Undo, never breaks
                # the Apply.
                undo_id = ""
                try:
                    import uuid as _uuid

                    undo_id = ("always_allow_revoke:"
                               f"permission:{pid}:"
                               f"{_uuid.uuid4().hex[:8]}")
                    rf.register_undo(
                        undo_id,
                        (lambda p=pid, s=old: pg.set_permission_state(
                            p, s, via="settings", how="Activity")),
                        kind="setting")
                except Exception:  # noqa: BLE001 — line without Undo
                    undo_id = ""
                rf.log_activity_human(
                    f"{_PERMISSION_TITLES.get(pid, pid)}: "
                    f"{_state_word(old)} → {_state_word(new)} "
                    f"in Settings",
                    reversible=True if undo_id else None,
                    undo_action_id=undo_id or None)
            except Exception:  # noqa: BLE001 — logging never breaks Apply
                pass
    return {
        "changed": [pid for pid, r in results.items()
                    if r["ok"] and r["old"] != r["new"]],
        "failed": {pid: r["error"] for pid, r in results.items()
                   if not r["ok"]},
        "results": results,
        "teardown_registration": teardown,
    }


def apply_mic_first_use_choice(choice, notify=None,
                               how="confirm dialog"):
    """Apply one microphone first-use choice.

    ("always" | "allow_once" | "not_now") — the one implementation
    shared by the Connect-time ask (window_session_connection) and
    the first-run wizard's mic page (onboarding_pages), so the two
    asks can never drift apart:

    * "always" makes the grant durable through the SAME
      Settings-only write path the Permissions page uses
      (apply_permission_changes -> via="settings"), and opens the
      mic for this session either way; if the durable save fails
      the user still gets this session's mic and is told the save
      did not stick (returns "allow_once").
    * "allow_once" grants this session only (in-memory, never
      persisted).
    * anything else records the decline, so the ask does not nag
      again this session, and returns "not_now".

    ``how`` (UI rethink Build 4 §4.4) names the tap for the
    Always-allow screen's granted-when line: "confirm dialog"
    for the Connect-time ask, "first-run setup" when the
    wizard calls it.

    Never raises: any failure reads as "not_now" (fail-closed —
    no grant). The "settings" choice (open the Permissions page)
    is NOT handled here — the caller owns it, because only the
    caller knows how to open the page. ``notify`` is an optional
    callable taking one plain-words string, for the
    save-did-not-stick note.
    """
    choice = str(choice or "").strip().lower()
    pg = _load_grants()
    if choice == "always":
        durable = False
        try:
            result = apply_permission_changes(
                [], {"microphone": "granted"}, how=how)
            durable = "microphone" not in (result.get("failed") or {})
        except Exception:  # noqa: BLE001 — falls back to this session
            durable = False
        try:
            pg.grant_session_only("microphone")
        except Exception:  # noqa: BLE001 — no grant, no mic
            return "not_now"
        if not durable and callable(notify):
            try:
                notify(
                    "Couldn't save the microphone permission "
                    "permanently — listening for this session "
                    "only. Turn it on in Settings → "
                    "Permissions to keep it.")
            except Exception:  # noqa: BLE001 — a note never breaks it
                pass
        return "always" if durable else "allow_once"
    if choice == "allow_once":
        try:
            pg.grant_session_only("microphone")
        except Exception:  # noqa: BLE001 — no grant, no mic
            return "not_now"
        return "allow_once"
    try:
        pg.decline_session("microphone")
    except Exception:  # noqa: BLE001 — the decline never breaks it
        pass
    return "not_now"


class PermissionsWidget(QWidget):
    """One page, one switch per permission. Unknown reads
    as a half-checked switch — visibly neither on nor off.
    Apply writes real grants (granted for switches moved
    on, denied for switches moved off), re-reads the store
    so the display never lies, and reports per-switch
    results. A half-checked switch is no instruction: the
    user has not said on or off. The folders section below
    edits the D9 folder allowlist immediately (each add /
    remove reports its own result)."""

    def __init__(self, entries=None, parent=None,
                 mirror_reader=None, mirror_specs=None,
                 platform_probe=None, device_reader=None):
        super().__init__(parent)
        # B3 injectables (same caller-supplied-override pattern
        # as mirror_reader): a callable returning the platform
        # probe dict, and a microphone-device reader. Default
        # None means the real probe — on Linux that reads
        # unknown immediately, never a guess.
        self._platform_probe = platform_probe
        self._device_reader = device_reader
        self.entries = entries if entries is not None \
            else permissions_model(
                platform_probe=platform_probe,
                device_reader=device_reader)
        # Mirror source (same caller-supplied-override pattern
        # as permissions_model's probe_results): a callable
        # taking specs and returning a read_home_mirrors-shaped
        # result. Default None means the real product entry
        # with no specs — today that reads honest-empty, and
        # the row lights up automatically once specs exist.
        self._mirror_reader = mirror_reader
        self._mirror_specs = mirror_specs
        self._switches = {}
        self._state_labels = {}
        self._source_labels = {}
        self._os_labels = {}
        self._diagnosis_labels = {}
        self._os_settings_buttons = {}
        self._device_labels = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        card, card_layout = _card(
            layout, "Permissions",
            "Everything VYUK can touch, on one page. A "
            "switch that reads as half-on means the "
            "permission is not set yet — VYUK does not "
            "guess. Flip a switch and press Apply; changes "
            "take effect immediately.")
        for entry in self.entries:
            row = QHBoxLayout()
            switch = QCheckBox(entry.title)
            switch.setTristate(True)
            self._set_switch(switch, entry.state)
            self._switches[entry.permission_id] = switch
            row.addWidget(switch)
            row.addStretch()
            card_layout.addLayout(row)
            card_layout.addWidget(_muted(entry.used_for))
            # State and source on separate lines: the
            # combined line (~98 chars) clips at narrow card
            # widths because the nested HudPanel layout does
            # not propagate height-for-width to the label —
            # each half fits on one line at any sane width.
            state_label = _muted(entry.state_line())
            self._state_labels[entry.permission_id] = state_label
            card_layout.addWidget(state_label)
            source_label = _muted(f"Source: {entry.state_source}")
            self._source_labels[entry.permission_id] = source_label
            card_layout.addWidget(source_label)
            if entry.permission_id in ("microphone", "camera"):
                # B3: the Windows side, on its own line, never
                # merged with the VYUK-side state above. The
                # shared-toggle note lives in the probe's reason
                # for the denied case; the D2 §5.4 copy below
                # says what the VYUK switch does and does not do.
                os_label = _muted(entry.os_state_line())
                self._os_labels[entry.permission_id] = os_label
                card_layout.addWidget(os_label)
                card_layout.addWidget(_muted(
                    "Switching this off stops VYUK using it. To "
                    "change what Windows itself allows, open "
                    "Windows privacy settings."))
                if entry.permission_id == "microphone":
                    diag_label = _muted(entry.diagnosis)
                    diag_label.setVisible(bool(entry.diagnosis))
                    self._diagnosis_labels["microphone"] = \
                        diag_label
                    card_layout.addWidget(diag_label)
                os_btn = QPushButton(
                    "Open Windows "
                    f"{entry.title.lower()} settings")
                os_btn.clicked.connect(
                    lambda _checked=False,
                    pid=entry.permission_id:
                    self._on_open_os_settings(pid))
                self._os_settings_buttons[entry.permission_id] = \
                    os_btn
                card_layout.addWidget(
                    os_btn, alignment=Qt.AlignmentFlag.AlignLeft)
            if entry.permission_id == "home_devices":
                # Device-state row (display-only mirrors):
                # labels inserted directly into the card
                # layout at this recorded index. A nested
                # container widget was tried first and
                # REJECTED: the card's custom layout does not
                # propagate a nested widget's height, so its
                # labels painted on top of each other (caught
                # by the render grab, not by text assertions).
                # Direct insertion keeps the row in the
                # home_devices section with real geometry.
                self._device_insert_parent = card_layout
                self._device_insert_index = card_layout.count()
        self._refresh_devices()
        # First-run surfacing (never a silent grant): a
        # fresh install reads mic denied (fail-closed), so
        # the page says so plainly and the user flips it
        # on with the same Apply path. Shown whenever mic
        # reads denied, fresh or explicitly denied.
        self.mic_notice = _muted(
            "Microphone is off — flip it on to use voice input.")
        self.mic_notice.setVisible(
            self._entry_state("microphone") == PERMISSION_DENIED)
        card_layout.addWidget(self.mic_notice)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.clicked.connect(self._on_apply)
        card_layout.addWidget(
            self.apply_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        self.result_label = _muted("")
        self.result_label.setVisible(False)
        card_layout.addWidget(self.result_label)
        # Folders VYUK may use (D9): immediate add/remove
        # through the Settings-only folder API.
        folders_card, folders_layout = _card(
            layout, "Folders VYUK may use",
            "Files tools may only touch these folders. "
            "Starting folders are Downloads and Documents; "
            "add more below.")
        self.folder_list = QListWidget()
        folders_layout.addWidget(self.folder_list)
        folder_row = QHBoxLayout()
        self.folder_input = QLineEdit()
        self.folder_input.setPlaceholderText("Add a folder path…")
        folder_row.addWidget(self.folder_input)
        self.folder_add_btn = QPushButton("Add")
        self.folder_add_btn.clicked.connect(self._on_folder_add)
        folder_row.addWidget(self.folder_add_btn)
        self.folder_remove_btn = QPushButton("Remove selected")
        self.folder_remove_btn.clicked.connect(self._on_folder_remove)
        folder_row.addWidget(self.folder_remove_btn)
        folders_layout.addLayout(folder_row)
        self.folder_feedback = _muted("")
        self.folder_feedback.setVisible(False)
        folders_layout.addWidget(self.folder_feedback)
        self._refresh_folders()

    @staticmethod
    def _set_switch(switch, state):
        if state == PERMISSION_GRANTED:
            switch.setCheckState(Qt.CheckState.Checked)
        elif state == PERMISSION_DENIED:
            switch.setCheckState(Qt.CheckState.Unchecked)
        else:
            switch.setCheckState(Qt.CheckState.PartiallyChecked)

    def _read_mirrors(self):
        """One read through the product entry (hook gate
        FIRST inside it) — never through the builder
        directly. Any failure returns None, which degrades
        to the honest-empty message, never a crash."""
        reader = self._mirror_reader
        if reader is None:
            try:
                reader = _load_mirrors_entry()
            except Exception:  # noqa: BLE001 — never crash Settings
                return None
        try:
            return reader(self._mirror_specs)
        except Exception:  # noqa: BLE001 — degrade, never crash
            return None

    def _refresh_devices(self):
        """Repopulate the device-state row from one fresh
        read, inserted at the recorded index right after the
        home_devices source line. Called at build and on
        every store refresh so the row never shows a stale
        permission's devices."""
        if not hasattr(self, "_device_insert_index"):
            return
        for label in self._device_labels:
            label.setParent(None)
            label.deleteLater()
        self._device_labels = []
        for offset, text in enumerate(
                _device_lines(self._read_mirrors())):
            label = _muted(text)
            self._device_insert_parent.insertWidget(
                self._device_insert_index + offset, label)
            self._device_labels.append(label)

    def _entry_state(self, pid):
        for entry in self.entries:
            if entry.permission_id == pid:
                return entry.state
        return PERMISSION_UNKNOWN

    def _desired_changes(self):
        """Switches the user moved, as {pid: state}. A
        half-checked switch is no instruction."""
        changes = {}
        for entry in self.entries:
            state = self._switches[entry.permission_id].checkState()
            if state == Qt.CheckState.Checked:
                want = PERMISSION_GRANTED
            elif state == Qt.CheckState.Unchecked:
                want = PERMISSION_DENIED
            else:
                continue
            if want != entry.state:
                changes[entry.permission_id] = want
        return changes

    def _refresh_from_store(self):
        """Re-read the store so the display never lies
        (covers failed writes and external changes). The
        platform probe re-reads too, so the Windows side
        never shows a stale OS state."""
        fresh = permissions_model(
            platform_probe=self._platform_probe,
            device_reader=self._device_reader)
        by_id = {e.permission_id: e for e in fresh}
        for entry in self.entries:
            new = by_id.get(entry.permission_id)
            if new is None:
                continue
            entry.state = new.state
            entry.state_source = new.state_source
            entry.os_state = new.os_state
            entry.os_reason = new.os_reason
            entry.diagnosis = new.diagnosis
            self._set_switch(self._switches[entry.permission_id],
                             new.state)
            self._state_labels[entry.permission_id].setText(
                new.state_line())
            self._source_labels[entry.permission_id].setText(
                f"Source: {new.state_source}")
            if entry.permission_id in self._os_labels:
                self._os_labels[entry.permission_id].setText(
                    new.os_state_line())
            if entry.permission_id in self._diagnosis_labels:
                label = self._diagnosis_labels[entry.permission_id]
                label.setText(new.diagnosis)
                label.setVisible(bool(new.diagnosis))
        self.mic_notice.setVisible(
            self._entry_state("microphone") == PERMISSION_DENIED)
        self._refresh_devices()
        self._refresh_folders()

    def _on_open_os_settings(self, pid):
        """D2 jump link (user-pressed only): open the Windows
        privacy page for this capability. Never raises; off
        Windows the URI simply does not resolve, and the probe
        never opens Settings on its own."""
        uri = OS_SETTINGS_PAGES.get(pid)
        if not uri:
            return
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl(uri))
        except Exception:  # noqa: BLE001 — never crash Settings
            pass

    def _refresh_folders(self):
        try:
            pg = _load_grants()
            folders = pg.list_folders()
        except Exception:  # noqa: BLE001 — never crash Settings
            folders = []
        self.folder_list.clear()
        self.folder_list.addItems(folders)

    def _on_apply(self):
        changes = self._desired_changes()
        if not changes:
            self.result_label.setVisible(True)
            self.result_label.setText("No changes to apply.")
            return
        record = apply_permission_changes(self.entries, changes)
        parts = []
        for pid in record["changed"]:
            title = _PERMISSION_TITLES.get(pid, pid)
            parts.append(f"{title} {'on' if record['results'][pid]['new'] == PERMISSION_GRANTED else 'off'}")
        for pid, reason in record["failed"].items():
            title = _PERMISSION_TITLES.get(pid, pid)
            parts.append(f"{title} failed: {reason}")
        self.result_label.setVisible(True)
        self.result_label.setText("Applied: " + "; ".join(parts) + ".")
        self._refresh_from_store()

    def _on_folder_add(self):
        text = self.folder_input.text().strip()
        if not text:
            self.folder_feedback.setVisible(True)
            self.folder_feedback.setText("Type a folder path first.")
            return
        try:
            pg = _load_grants()
            before = set(pg.list_folders())
            ok = pg.add_folder(text, via="settings")
            after = set(pg.list_folders())
        except Exception as exc:  # noqa: BLE001 — report, not crash
            ok = False
            after = set()
            before = set()
            self.folder_feedback.setVisible(True)
            self.folder_feedback.setText(
                f"Could not add that folder: {exc}")
            return
        self.folder_feedback.setVisible(True)
        if not ok:
            self.folder_feedback.setText(
                "Could not add that folder — check the path "
                "and try again.")
        elif len(after) > len(before):
            self.folder_feedback.setText("Added to allowed folders.")
            self.folder_input.clear()
        else:
            self.folder_feedback.setText("Already allowed.")
        self._refresh_folders()

    def _on_folder_remove(self):
        item = self.folder_list.currentItem()
        if item is None:
            self.folder_feedback.setVisible(True)
            self.folder_feedback.setText("Select a folder to remove.")
            return
        folder = item.text()
        try:
            pg = _load_grants()
            ok = pg.remove_folder(folder, via="settings")
        except Exception as exc:  # noqa: BLE001 — report, not crash
            self.folder_feedback.setVisible(True)
            self.folder_feedback.setText(
                f"Could not remove that folder: {exc}")
            return
        self.folder_feedback.setVisible(True)
        if ok:
            self.folder_feedback.setText("Removed from allowed folders.")
        else:
            self.folder_feedback.setText(
                "Could not remove that folder — check the path "
                "and try again.")
        self._refresh_folders()
