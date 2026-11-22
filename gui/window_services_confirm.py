"""MainWindow services mixin: confirm-gate plumbing (part 1 of 4).

Split from gui/window_services.py (GUI split #10 batch 1, move only).
Module funcs + confirm-gate dialog builders/handlers + capture
indicator + voice-confirm sets. Bodies moved verbatim; behaviour is
unchanged.
"""

import threading
import time
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QSystemTrayIcon
from voice_assistant.core.gui_facade import config
from voice_assistant.core.gui_facade import (
    DATA_DIR, always_allow, confirm_gate, mark_session_owner_proven,
    permission_grants, plan_approval, voice_confirm_allowed,
    voice_pin)
from .style import TEXT_MUTED, OK, AMBER, DANGER
from .window_widgets import _tool_friendly_name
from .design_system.dialogs import HudDialog
from .crop_grid import _C7_CROP_MAX_SIDE, stored_crop_grayscale_bytes
import re as _re

# ------------------------------------------------------------------
# v5.51 (S-1): the confirmation dialog's plain-language layer.
#
# The popup used to headline the friendly name and then show the
# raw tool id + args summary as its body — accurate, but it read
# like a log line. Now the VISIBLE text is, in order: a headline
# saying what will actually happen, a line naming the thing the
# request is about (the file / app / recipient from the masked
# summary), and the consequence note. The masked summary itself
# (confirm_gate.summarize_args output) moves behind a "Details"
# toggle, collapsed by default. All derivation is from the
# summary string the gate already masked — no raw args reach
# this layer, so nothing unmasked can leak into the headline.
# ------------------------------------------------------------------

import re as _re

_CONFIRM_TARGET_KEYS = (
    "to", "recipient", "address",
    "path", "filepath", "file", "filename", "file_name",
    "folder", "directory", "dir", "destination", "dest",
    "source", "target",
    "app", "app_name", "application", "url", "link", "command",
)


def _confirm_summary_value(summary: str, keys) -> tuple[str, str] | None:
    """The first (key, value) in `summary` whose key is in
    `keys`, or None. Values run to the next ", key=" boundary
    or the end of the string — the summarize_args format."""
    text = str(summary or "")
    for key in keys:
        match = _re.search(
            rf"(?:^|, ){_re.escape(key)}=((?:[^,]|,(?!\s*[A-Za-z_]+=+))*)",
            text)
        if match:
            value = match.group(1).strip()
            if value and value != "•••":
                return key, value
    return None


def _confirm_target_phrase(summary: str) -> str | None:
    """The request named in user terms: "the file report.docx",
    "the app Notepad", the recipient's address, … — or None
    when the summary names nothing concrete."""
    found = _confirm_summary_value(summary, _CONFIRM_TARGET_KEYS)
    if found is None:
        return None
    key, value = found
    if key in ("to", "recipient", "address"):
        return value
    if key in ("app", "app_name", "application"):
        return f"the app {value}"
    if key in ("url", "link"):
        return f"the page {value}"
    if key == "command":
        return "a command on this PC"
    base = value.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if key in ("folder", "directory", "dir", "destination", "dest"):
        return f"the folder {base or value}"
    return f"the file {base or value}"


def _mouse_headline(summary: str) -> str:
    """Plain-language consequence line for mouse_control (E-F304):
    the action plus the point, in the same voice as the other
    branches ("This will ..."). mouse_control's x/y are true
    screen pixels, so naming them is literally true. When the
    call carries no point, the click lands where the pointer
    already is; a scroll's direction comes from amount
    (positive scrolls up) when it is present."""
    action_found = _confirm_summary_value(summary, ("action",))
    action = action_found[1].lower() if action_found is not None else ""
    x_found = _confirm_summary_value(summary, ("x",))
    y_found = _confirm_summary_value(summary, ("y",))
    point = None
    if x_found is not None and y_found is not None:
        point = f"{x_found[1]}, {y_found[1]}"
    if action == "click":
        return (f"This will click at {point}." if point
                else "This will click where the mouse pointer is.")
    if action == "double_click":
        return (f"This will double-click at {point}." if point
                else "This will double-click where the mouse pointer is.")
    if action == "right_click":
        return (f"This will right-click at {point}." if point
                else "This will right-click where the mouse pointer is.")
    if action == "move":
        return (f"This will move the mouse pointer to {point}." if point
                else "This will move the mouse pointer.")
    if action == "scroll":
        amount_found = _confirm_summary_value(summary, ("amount",))
        direction = ""
        if amount_found is not None:
            try:
                n = int(amount_found[1])
                direction = " up" if n > 0 else " down" if n < 0 else ""
            except ValueError:
                direction = ""
        if point:
            return f"This will scroll{direction} at {point}."
        return f"This will scroll{direction}."
    return "This will use the mouse."


def _confirm_headline(tool_name: str, summary: str) -> str:
    """A plain-language consequence line for the popup's
    headline — derived from the tool's friendly name and the
    target the summary names. The raw tool id is NEVER the
    headline (it lives in Details, for support)."""
    friendly = _tool_friendly_name(tool_name)
    target = _confirm_target_phrase(summary)
    low = str(tool_name or "").lower()
    # Action-dispatched tools (file_control & friends) carry the
    # real verb in the summary's action= field — it counts too.
    action_found = _confirm_summary_value(summary, ("action",))
    if action_found is not None:
        low = f"{low} {action_found[1].lower()}"
    # E-F304: mouse_control gets its own branch FIRST -- the
    # write branch below matches "move", so a mouse move would
    # otherwise read "This will change things on this PC."
    if "mouse_control" in low:
        return _mouse_headline(summary)
    if any(w in low for w in ("delete", "remove", "shred",
                              "erase", "empty_recycle")):
        return (f"This will remove {target}." if target
                else "This will remove things on this PC.")
    if "send" in low and "email" in low:
        return (f"This will send an email to {target}." if target
                else "This will send an email.")
    if low == "run_command":
        return "This will run a command on this PC."
    if any(w in low for w in ("power", "shutdown", "restart",
                              "hibernate", "sleep")):
        return "This will change this PC's power — it may shut down, restart or sleep."
    if "uninstall" in low:
        return f"This will uninstall {target or 'an app'}."
    if "install" in low:
        return f"This will install {target or 'an app'}."
    if any(w in low for w in ("write", "replace", "save",
                              "create", "move", "rename", "copy")):
        return (f"This will change {target}." if target
                else "This will change things on this PC.")
    return f"VYUK wants to run “{friendly}”."


def _mic_health_presentation(count, stream_ran):
    """(key, colour, tooltip) for the microphone health dot
    (v5.51, E-2) — pure, so the wording is testable without a
    window. `count` is the live A9 drop count for the current
    stream (AudioManager.get_mic_drop_count()); `stream_ran`
    separates "no stream yet" (neutral) from a healthy zero —
    a zero with no stream behind it must never read as healthy."""
    if not stream_ran:
        return ("no_stream", TEXT_MUTED,
                "Microphone health: no stream has run yet this "
                "session — connect and talk, and VYUK will keep "
                "an eye on dropped sound here.")
    try:
        n = int(count or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return ("healthy", OK,
                "Microphone health: the stream held — no drops "
                "this session.")
    return ("dropping", DANGER,
            f"Microphone health: the microphone dropped {n} "
            f"time{'s' if n != 1 else ''} this session — speech "
            f"may have gaps. If this keeps happening, close "
            f"other apps using the microphone or try a "
            f"different one in Settings.")


# C7 approval-dialog crop display (2026-11-05): the S-1 dialog can
# show the STORED crop for a point action — the pixels the spot
# was read from — when the action carries one. The crop is a
# grayscale grid (rows of ints), the same shape the verifier's
# compare_region takes. Gating is by SUPPLY, not by tool name:
# a builder shows a crop only when one is supplied; no crop
# supplied means no image widget at all, never a placeholder.
# The caption never claims the crop is fresh — it is the stored
# observation, and the C4 enumeration stands: ask-paths have no
# fresh-observation supplier at dialog time, so this slot shows
# stored pixels only. No live path supplies a crop today (no
# stored_crop producer exists in the tree); the slot is the GUI
# half, tested with injected grids, and live dialogs render the
# honest fallback until a producer lands. Exact bytes live in
# gui/crop_grid.py (stdlib, testable without PySide6).
_C7_CROP_CAPTION = ("Stored view — what was seen when this spot "
                    "was read, not a new look.")


def stored_crop_pixmap(grid, max_side=_C7_CROP_MAX_SIDE):
    """QPixmap for a stored-crop grid, or None if unusable.

    The conversion itself is pixel-exact (grayscale bytes straight
    into a QImage); only the DISPLAY copy is downscaled, and only
    when the grid exceeds max_side on a side — a grid at or under
    the cap displays byte-for-byte. Qt only.
    """
    converted = stored_crop_grayscale_bytes(grid)
    if converted is None:
        return None
    flat, width, height = converted
    image = QImage(flat, width, height, width,
                   QImage.Format.Format_Grayscale8)
    if image.isNull():
        return None
    pixmap = QPixmap.fromImage(image)
    if pixmap.isNull():
        return None
    if max(width, height) > max_side:
        pixmap = pixmap.scaled(
            max_side, max_side, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
    return pixmap


def _files_folder_button(dlg, layout, row,
                         tool_name: str, request_id=None):
    """The per-call files ask's "Always allow this folder"
    button (UI rethink Build 4 §4.5), or None when the
    call offers no folder. The folder comes from the
    gate's pending entry (raw args, by request id) —
    never from the masked summary. The button shows only
    when the tool is files-bound, files is UNKNOWN and a
    judgable folder is named (see
    always_allow.files_folder_offer). Tapping it grants
    BOTH the folder and the files permission, durably,
    and accepts the dialog — this call runs, and the
    folder will not ask again. A failed grant leaves the
    dialog open with a plain warning instead of silently
    pretending. The button never co-occurs with "Always
    allow this kind" (no files tool is a navigation
    kind), so the row never holds four buttons.

    Module-level on purpose, not a mixin method: the
    builders are also called unbound on bare-QWidget stubs
    (c7/review/s7/uiinteraction tests drive
    ServicesConfirmMixin._build_confirm_dialog with a plain
    QWidget as self), so anything they call must not depend
    on self — this helper uses only the passed dlg/layout/
    row plus the confirm_gate / always_allow / permission_
    grants modules. (Build 4 first shipped it as a method
    and broke every stub-driven confirm test on Qt.)"""
    try:
        folder = confirm_gate.pending_folder(request_id)
        offer = always_allow.files_folder_offer(tool_name, folder)
    except Exception:  # noqa: BLE001 — no offer, no button
        return None
    if not offer:
        return None
    warn_folder = QLabel(
        "Couldn't remember that folder — it will ask "
        "again. The folder couldn't be saved.")
    warn_folder.setStyleSheet(f"color: {AMBER}; font-size: 12px;")
    warn_folder.setWordWrap(True)
    warn_folder.setVisible(False)
    layout.addWidget(warn_folder)
    btn_folder = QPushButton("Always allow this folder")
    row.addWidget(btn_folder)

    def _on_folder():
        try:
            ok = permission_grants.grant_folder_and_files(offer)
        except Exception:  # noqa: BLE001 — warning, not a crash
            ok = False
        if ok:
            dlg.accept()
        else:
            warn_folder.setVisible(True)

    btn_folder.clicked.connect(_on_folder)
    return btn_folder


class ServicesConfirmMixin:
    """Confirm-gate mixin (GUI split #10 batch 1).

    Confirm-gate dialog builders/handlers, capture indicator,
    and the voice-confirm sets.
    """
    # ------------------------------------------------- hardening (v4.3)
    def _confirm_gate_handler(self, tool_name: str, args_summary: str,
                              request_id=None) -> bool:
        """confirm_gate handler: runs on a brain/worker thread.

        Serialises concurrent requests, marshals the popup to the GUI
        thread and waits for the human's answer. confirm_gate wraps
        this in a 60 s default-deny timeout, and any failure here
        (no answer in time, window gone) also denies — a critical
        action never runs because a popup failed.

        v5.48 (programme item 19): the handler takes the gate's
        3-arg contract (tool_name, summary, request_id) — the ID
        rides the bridge signal so the popup names the same
        request the gate logs — and the self-wait is
        confirm_gate.HANDLER_BUDGET_S, so the margin under the
        gate's timeout lives in one place (core), not a hardcoded
        55 here."""
        with self._confirm_lock:
            self._confirm_result = False
            self._confirm_event = threading.Event()
            self.signals.confirm_requested.emit(
                tool_name, args_summary,
                int(request_id) if request_id is not None else 0)
            self._confirm_event.wait(confirm_gate.HANDLER_BUDGET_S)
            return bool(self._confirm_result)

    # ------------------------------------------------- Fix 9: voice plan approval
    def _plan_approval_handler(self, task, plan) -> dict:
        """plan_approval handler: runs on a voice/worker thread.

        Serialises concurrent asks, marshals the plan dialog
        to the GUI thread and waits for the human's answer.
        core/plan_approval wraps this in a 55 s timeout; the
        dialog itself closes at 50 s, so a timeout here means
        the human truly did not answer. Any failure (no
        answer in time, window gone) reads as ([], "timeout")
        when the dialog's own timer fired, else ([], "ask
        each time") — a plan approval never happens because a
        popup failed. Returns {"approved": [...], "outcome":
        ...} (the dict form request_plan_decision_ex reads);
        the returned indices are recorded by the coordinator
        through approve_plan, which re-validates them; this
        handler never approves by itself. The plan is shown,
        not spoken: the human reads the steps and presses a
        button (the same dialog the typed path uses), because
        a spoken plan the user cannot see is not an approval.
        """
        with self._plan_approval_lock:
            self._plan_approval_result = []
            self._plan_approval_outcome = "ask_each_time"
            self._plan_approval_event = threading.Event()
            self.signals.plan_approval_requested.emit(task, plan)
            self._plan_approval_event.wait(
                plan_approval.HANDLER_TIMEOUT_S)
            return {"approved": list(self._plan_approval_result or []),
                    "outcome": self._plan_approval_outcome or
                    "ask_each_time"}

    def _on_plan_approval_requested(self, task, plan):
        """GUI-thread slot: the plan-approval dialog itself."""
        try:
            from .plan_approval_dialog import (
                present_plan_approval_dialog_ex)
            title = str(getattr(task, "title", "") or "task")
            indices, outcome = present_plan_approval_dialog_ex(
                self, title, plan,
                timeout_s=plan_approval.DIALOG_TIMEOUT_S)
            self._plan_approval_result = indices
            self._plan_approval_outcome = outcome
        except Exception:  # noqa: BLE001 — a broken popup is an
            # error, not an explicit ask (the coordinator runs
            # step by step on "error" exactly as on "ask each
            # time"; the outcome only names what happened).
            self._plan_approval_result = []
            self._plan_approval_outcome = "error"
        finally:
            if self._plan_approval_event is not None:
                self._plan_approval_event.set()

    def _build_confirm_dialog(self, tool_name: str,
                              args_summary: str, request_id=None,
                              stored_crop=None):
        """The v5.51 (S-1) confirmation popup: headline =
        consequence, then what the request is about, then the
        consequence note; the masked args summary lives behind
        a Details toggle, collapsed by default. Buttons keep
        their exact labels ("Don't allow" / "Allow once");
        v5.54.1 adds a third button, "Always allow this
        kind", ONLY for allowlistable kinds (core/confirm_
        gate.is_allowlistable) — destructive kinds never get
        it, and a failed remember leaves the dialog open with
        a plain warning instead of silently pretending.
        stored_crop (optional grayscale grid, C7 2026-11-05):
        when supplied, the dialog shows it as a stored view
        above Details; when absent, no image region renders."""
        friendly = _tool_friendly_name(tool_name)
        # Phase C: HudDialog base (chamfer + role line + own
        # sheet); every widget, string, button and focus rule
        # below is unchanged.
        dlg = HudDialog(self, title="VYUK — confirm a critical action")
        try:
            dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        except Exception:  # noqa: BLE001 — the popup still works
            pass
        layout = dlg.content_layout()
        headline = QLabel(_confirm_headline(tool_name, args_summary))
        headline.setStyleSheet("font-size: 16px; font-weight: 600;")
        headline.setWordWrap(True)
        layout.addWidget(headline)
        self._confirm_headline_label = headline
        target = _confirm_target_phrase(args_summary)
        about = QLabel(
            f"This is about {target} — the one from your request."
            if target else
            "This came from the request you just made.")
        about.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        about.setWordWrap(True)
        layout.addWidget(about)
        self._confirm_about_label = about
        if "Taint source" in str(args_summary or ""):
            warn = QLabel(
                "Heads up: this request was prompted by content "
                "VYUK read, not directly by you — check it "
                "carefully before allowing it.")
            warn.setStyleSheet(f"color: {AMBER}; font-size: 12px;")
            warn.setWordWrap(True)
            layout.addWidget(warn)
        note = QLabel(
            "This kind of action can change or remove things on "
            "this PC. It runs only if you allow it.")
        note.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        note.setWordWrap(True)
        layout.addWidget(note)
        # C7 dialog crop (2026-11-05): show the STORED crop when
        # the action carries one; no crop means no image widget
        # at all — never a placeholder. Any conversion failure
        # degrades to the same absence, never a broken image.
        try:
            crop_pixmap = stored_crop_pixmap(stored_crop)
        except Exception:  # noqa: BLE001 — absence, not breakage
            crop_pixmap = None
        if crop_pixmap is not None:
            crop_label = QLabel()
            crop_label.setObjectName("c7CropLabel")
            crop_label.setPixmap(crop_pixmap)
            layout.addWidget(crop_label)
            crop_caption = QLabel(_C7_CROP_CAPTION)
            crop_caption.setObjectName("c7CropCaption")
            crop_caption.setStyleSheet(
                f"color: {TEXT_MUTED}; font-size: 12px;")
            crop_caption.setWordWrap(True)
            layout.addWidget(crop_caption)
        details = QLabel(
            f"Action: {friendly} ({tool_name})\n{args_summary}"
            + (f"\nRequest {int(request_id)}"
               if request_id else ""))
        details.setWordWrap(True)
        details.setVisible(False)
        toggle = QPushButton("Details")
        toggle.setObjectName("chipBtn")
        toggle.setCheckable(True)
        toggle.toggled.connect(details.setVisible)
        layout.addWidget(toggle)
        layout.addWidget(details)
        self._confirm_details_label = details
        self._confirm_details_toggle = toggle
        warn_always = QLabel(
            "Couldn't remember that choice — it will ask "
            "again. The allowlist file couldn't be written.")
        warn_always.setStyleSheet(f"color: {AMBER}; font-size: 12px;")
        warn_always.setWordWrap(True)
        warn_always.setVisible(False)
        layout.addWidget(warn_always)
        # v5.57 (redo): one muted line above the buttons, so the
        # two labels' difference is never a guess.
        explainer = QLabel(
            "Allow once runs it this time only. Don't allow stops "
            "it and asks again next time.")
        explainer.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        explainer.setWordWrap(True)
        layout.addWidget(explainer)
        row = QHBoxLayout()
        row.addStretch(1)
        btn_deny = QPushButton("Don't allow")
        btn_allow = QPushButton("Allow once")
        row.addWidget(btn_deny)
        row.addWidget(btn_allow)
        if confirm_gate.is_allowlistable(tool_name):
            btn_always = QPushButton("Always allow this kind")
            row.addWidget(btn_always)

            def _on_always():
                if confirm_gate.allow_kind(tool_name):
                    dlg.accept()
                else:
                    warn_always.setVisible(True)

            btn_always.clicked.connect(_on_always)
        _files_folder_button(dlg, layout, row,
                             tool_name, request_id)
        layout.addLayout(row)
        # UI interaction slice: the safe button is the default and
        # holds initial focus — Enter denies, never allows.
        btn_deny.setDefault(True)
        btn_deny.setFocus()
        btn_deny.clicked.connect(dlg.reject)
        btn_allow.clicked.connect(dlg.accept)
        return dlg

    def _build_plain_confirm_dialog(self, tool_name: str,
                                    args_summary: str,
                                    request_id=None,
                                    stored_crop=None):
        """The fail-closed fallback (v5.51, S-1): if building
        the designed popup fails for any reason, this plain one
        still names the action and still requires an explicit
        Allow — a broken popup must deny, never wave through.
        stored_crop (optional, C7 2026-11-05): same stored-view
        slot as the designed popup."""
        friendly = _tool_friendly_name(tool_name)
        # Phase C: HudDialog base, as in the designed popup.
        dlg = HudDialog(self, title="VYUK — confirm a critical action")
        layout = dlg.content_layout()
        title = QLabel(f"Allow VYUK to run “{friendly}”?")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(title)
        detail = QLabel(
            f"Tool: {tool_name}\n{args_summary}\n\n"
            "This action can change or remove things on this PC. "
            "It runs only if you allow it.")
        detail.setWordWrap(True)
        # Golden-fix D7 (plain fallback ONLY — the designed
        # HudDialog confirm is untouched): a word-wrapped QLabel
        # sizes its height from its unwrapped line count, so when
        # the dialog lands narrower than its sizeHint width the
        # last paragraph wraps to an extra line the label has no
        # room for — and QLabel's default vertical-centre
        # alignment then clipped the FIRST line's top ("Tool:
        # open_file" showing glyph bottoms only at 120%).
        # Top alignment keeps the safety-critical first lines
        # (tool + args) fully visible even if a later line ever
        # runs short, and the dialog's minimum width (below) keeps
        # the dialog at its sizeHint width in the normal case, so
        # the paragraph fits on one line and nothing clips.
        detail.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(detail)
        # C7 dialog crop (2026-11-05): same stored-crop slot as
        # the designed popup — a supplied crop shows, no crop
        # means no image widget at all, never a placeholder.
        try:
            crop_pixmap = stored_crop_pixmap(stored_crop)
        except Exception:  # noqa: BLE001 — absence, not breakage
            crop_pixmap = None
        if crop_pixmap is not None:
            crop_label = QLabel()
            crop_label.setObjectName("c7CropLabel")
            crop_label.setPixmap(crop_pixmap)
            layout.addWidget(crop_label)
            crop_caption = QLabel(_C7_CROP_CAPTION)
            crop_caption.setObjectName("c7CropCaption")
            crop_caption.setStyleSheet(
                f"color: {TEXT_MUTED}; font-size: 12px;")
            crop_caption.setWordWrap(True)
            layout.addWidget(crop_caption)
        warn_always = QLabel(
            "Couldn't remember that choice — it will ask "
            "again. The allowlist file couldn't be written.")
        warn_always.setStyleSheet(f"color: {AMBER}; font-size: 12px;")
        warn_always.setWordWrap(True)
        warn_always.setVisible(False)
        layout.addWidget(warn_always)
        explainer = QLabel(
            "Allow once runs it this time only. Don't allow stops "
            "it and asks again next time.")
        explainer.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        explainer.setWordWrap(True)
        layout.addWidget(explainer)
        row = QHBoxLayout()
        row.addStretch(1)
        btn_deny = QPushButton("Don't allow")
        btn_allow = QPushButton("Allow once")
        row.addWidget(btn_deny)
        row.addWidget(btn_allow)
        if confirm_gate.is_allowlistable(tool_name):
            btn_always = QPushButton("Always allow this kind")
            row.addWidget(btn_always)

            def _on_always_plain():
                if confirm_gate.allow_kind(tool_name):
                    dlg.accept()
                else:
                    warn_always.setVisible(True)

            btn_always.clicked.connect(_on_always_plain)
        _files_folder_button(dlg, layout, row,
                             tool_name, request_id)
        layout.addLayout(row)
        # UI interaction slice: the safe button is the default and
        # holds initial focus — Enter denies, never allows.
        btn_deny.setDefault(True)
        btn_deny.setFocus()
        btn_deny.clicked.connect(dlg.reject)
        btn_allow.clicked.connect(dlg.accept)
        # Golden-fix D7, second half: the dialog must not land
        # narrower than the width its labels were sized for —
        # a capped dialog width is what pushed the last paragraph
        # onto a second line the detail label had no height for.
        # A minimum (not fixed) width keeps the dialog at its
        # sizeHint width normally while still letting it GROW
        # (e.g. when the allowlist-failure warning appears).
        # Capped at 720 px so a pathological args summary can
        # never push the dialog off-screen; past the cap the
        # top alignment above degrades gracefully instead.
        dlg.setMinimumWidth(min(dlg.sizeHint().width(), 720))
        return dlg

    def _build_mic_first_use_dialog(self):
        """First-use mic ask (D3, 2026-11-05): the mic is off on a
        fresh install and VYUK has never asked — the dialog VYUK
        cannot hear a spoken yes through (the mic is the thing
        that is off), so this is a GUI dialog only, never
        voice-resolvable: it does NOT go through confirm_gate
        (no request_confirmation, no resolve_pending, no voice
        reply) and it never grants anything durable.

        Same HudDialog pattern as _build_plain_confirm_dialog:
        headline, plain-words detail, explainer, button row. Four
        buttons — "Always allow" (switches the microphone on in
        Settings for good, so this ask never returns), "Allow once"
        (this session only), "Open Settings" (the Permissions
        page), "Not now" (skip it this time). "Always allow" is the
        default and holds initial focus: the ask only ever appears
        right after the user pressed Connect on a voice assistant,
        and the old safe default ("Not now") left a connected
        session that silently never heard anything. It is still a
        GUI-only click — never voice-resolvable. Returns (dialog,
        choice) where choice is a one-item list the button handlers
        append to; the caller reads choice[0] after exec()
        ("always" | "allow_once" | "settings" | "not_now")."""
        dlg = HudDialog(self, title="VYUK — your microphone")
        layout = dlg.content_layout()
        title = QLabel("VYUK can't hear you yet")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        title.setWordWrap(True)
        layout.addWidget(title)
        detail = QLabel(
            "Your microphone is switched off — this is a fresh "
            "install and VYUK has never asked before.\n\n"
            "Always allow switches the microphone on for good, so "
            "you won't be asked again. You can turn it off any time "
            "in Settings \u2192 Permissions.\n\n"
            "Allow once lets VYUK listen for this session only, and "
            "it'll ask again next time you start the app.")
        detail.setWordWrap(True)
        detail.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(detail)
        explainer = QLabel(
            "Not now skips it this time \u2014 VYUK will still "
            "connect, but it won't hear you.")
        explainer.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        explainer.setWordWrap(True)
        layout.addWidget(explainer)
        choice: list = []
        row = QHBoxLayout()
        row.addStretch(1)
        btn_not_now = QPushButton("Not now")
        btn_settings = QPushButton("Open Settings")
        btn_allow = QPushButton("Allow once")
        btn_always = QPushButton("Always allow")
        row.addWidget(btn_not_now)
        row.addWidget(btn_settings)
        row.addWidget(btn_allow)
        row.addWidget(btn_always)
        layout.addLayout(row)
        # "Always allow" is the default and holds initial focus:
        # pressing Enter on a fresh Connect gives the user a
        # working mic instead of a connected-but-deaf session.
        btn_always.setDefault(True)
        btn_always.setFocus()
        btn_not_now.clicked.connect(
            lambda: (choice.append("not_now"), dlg.reject()))
        btn_settings.clicked.connect(
            lambda: (choice.append("settings"), dlg.accept()))
        btn_allow.clicked.connect(
            lambda: (choice.append("allow_once"), dlg.accept()))
        btn_always.clicked.connect(
            lambda: (choice.append("always"), dlg.accept()))
        dlg.setMinimumWidth(min(dlg.sizeHint().width(), 720))
        return dlg, choice

    def _ask_mic_first_use(self) -> str:
        """Show the first-use mic ask; returns "always" |
        "allow_once" | "settings" | "not_now". GUI thread only (called from the
        Connect handler). Never raises: any failure returns
        "not_now" (fail-closed — no grant, connect proceeds
        without the mic)."""
        try:
            dlg, choice = self._build_mic_first_use_dialog()
        except Exception:  # noqa: BLE001 — a broken popup denies
            return "not_now"
        try:
            dlg.exec()
        except Exception:  # noqa: BLE001 — a broken popup denies
            return "not_now"
        if choice and choice[0] in ("always", "allow_once", "settings"):
            return choice[0]
        return "not_now"

    def _on_confirm_requested(self, tool_name: str, args_summary: str,
                              request_id=None):
        """GUI-thread slot: the native confirmation popup itself."""
        # v5.48 (programme item 19): notification fallback — the
        # app HAS a tray icon (see _setup_tray / self.tray), so a
        # request that arrives while the window is not the active
        # window also surfaces there, naming the tool.
        tray = getattr(self, "tray", None)
        if tray is not None:
            try:
                if not self.isActiveWindow():
                    tray.showMessage(
                        f"{config.assistant_name} — confirmation needed",
                        f"VYUK is waiting for your answer before "
                        f"running "
                        f"{_tool_friendly_name(tool_name)}.",
                        QSystemTrayIcon.MessageIcon.Information, 5000)
            except Exception:  # noqa: BLE001 — fallback is a courtesy
                pass
        try:
            try:
                dlg = self._build_confirm_dialog(
                    tool_name, args_summary, request_id)
            except Exception:  # noqa: BLE001 — presentation must
                dlg = self._build_plain_confirm_dialog(  # never deny
                    tool_name, args_summary, request_id)  # the human their say:
                # the plain dialog still asks, explicitly.
            self._confirm_dialog = dlg
            # v5.48 (programme item 19): shown -> front (see the
            # topmost flag in the builders); exec() then runs its
            # modal loop.
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
            self._confirm_result = dlg.exec() == QDialog.DialogCode.Accepted
        except Exception:  # noqa: BLE001 — a broken popup must deny
            self._confirm_result = False
        finally:
            if self._confirm_event is not None:
                self._confirm_event.set()

    # ------------------------------------- voice confirm (v5.48, item 19)
    _VOICE_CONFIRM_YES = frozenset({
        "yes", "yeah", "yep", "approve", "approved", "go ahead",
        "confirm"})
    _VOICE_CONFIRM_NO = frozenset({
        "no", "nope", "deny", "denied", "cancel", "stop"})

    def _maybe_confirm_reply(self, text: str) -> bool:
        """A spoken yes/no answers a pending confirmation.

        v5.48 (programme item 19): called ONLY from _on_transcript's
        user branch — the surface session transcripts of the user's
        own words arrive on (mic speech in every client; typed
        send_text echoes also land there, already marked self-sent
        when VYUK herself injected them, and bracketed injections /
        self-sent echoes are refused below). Assistant/TTS text
        arrives with role != "user" and NEVER reaches this hook.

        Acts only while confirm_gate.pending_request() is live, and
        only on a WHOLE utterance in the small yes/no sets (after
        strip/lower/trailing-punctuation strip) — "yes please do
        it" is a command, not an answer. On a match it resolves
        THAT request's ID via="voice", posts the chat caption, and
        returns True: the utterance is consumed (not learned, not
        a watch phrase, not fed on as a brain command).

        Owner proof (loophole batch 4 — #1, #20, L-27, #19): a
        spoken yes/no resolves a pending ask ONLY when the session
        is owner-proven (started from the app UI, or unlocked on
        screen / with the voice PIN). In an unproven session the
        voice answer is refused in plain words and resolves
        NOTHING — the popup stays up for the owner to answer on
        screen, or it times out and the act is refused. A voice
        PIN phrase ("voice PIN 1234") unlocks the session when it
        matches the owner-set PIN, and is consumed whether it
        matches or not. The old residual risk (any voice in the
        room could say "yes") is closed by this hook; the
        remaining risk is a spoken PIN overheard in the room,
        which is opt-in and session-scoped."""
        try:
            raw = str(text or "")
            if raw.lstrip().startswith("["):
                return False  # bracketed injection, never the user
            pending_self = getattr(self, "_self_sent_texts", []) or []
            now = time.monotonic()
            if any(t == raw and exp > now for (t, exp) in pending_self):
                return False  # our own injected text echoing back
            normalised = raw.strip().lower()
            while normalised and normalised[-1] in ".,!?;:":
                normalised = normalised[:-1].rstrip()
            # UI rethink Build 4 §4.7 (D8): a spoken "always
            # allow" is refused in words, naming the screen —
            # voice can never set, widen or restore an
            # always-allow, and the third button is never
            # voice-activatable. The predicate is the same one
            # the settings-voice router uses
            # (always_allow.is_always_allow_utterance), so a
            # per-permission shape like "always allow the
            # camera" is refused here too — the exact phrase
            # set alone missed it. The utterance is consumed
            # (not a command, not learned) and the pending
            # request is resolved NOTHING: no yes, no
            # allow-kind, no plan approval.
            if always_allow.is_always_allow_utterance(normalised):
                try:
                    self._add_system(
                        always_allow.ALWAYS_ALLOW_VOICE_REFUSAL)
                except Exception:  # noqa: BLE001 — the answer
                    pass  # already landed; a failed caption
                    # must not un-consume it
                return True
            # Loophole batch 4: a voice PIN phrase unlocks the
            # session when it matches the owner-set PIN. The
            # utterance is consumed whether it matches or not
            # (never a command, never learned) and the PIN is
            # never echoed. A wrong PIN resolves nothing.
            _pin = voice_pin.parse_voice_pin_phrase(normalised)
            if _pin is not None:
                try:
                    _ok = voice_pin.check_voice_pin(DATA_DIR, _pin)
                except Exception:  # noqa: BLE001 — unreadable: no unlock
                    _ok = False
                if _ok:
                    try:
                        mark_session_owner_proven("voice-pin")
                    except Exception:  # noqa: BLE001 — the proof is the unlock
                        pass
                    try:
                        self._add_system(
                            "Voice unlocked for this session.")
                    except Exception:  # noqa: BLE001 — already landed
                        pass
                else:
                    try:
                        self._add_system(
                            "That PIN didn't match — voice stays "
                            "locked. Approve on screen, or set the "
                            "voice PIN in Settings.")
                    except Exception:  # noqa: BLE001 — already landed
                        pass
                return True
            if normalised in self._VOICE_CONFIRM_YES:
                approved = True
            elif normalised in self._VOICE_CONFIRM_NO:
                approved = False
            else:
                return False
            # Loophole batch 4: a spoken yes/no resolves a pending
            # ask ONLY in an owner-proven session (or with the lock
            # off). In an unproven session the voice answer is
            # refused in plain words and resolves NOTHING — the
            # popup stays up for an on-screen answer. Fail-closed:
            # an unreadable proof state refuses the voice answer.
            try:
                _allowed = bool(voice_confirm_allowed())
            except Exception:  # noqa: BLE001 — unreadable: no voice answer
                _allowed = False
            if not _allowed:
                try:
                    self._add_system(
                        "I can't take a yes by voice until this "
                        "session is unlocked — approve on screen, "
                        "or unlock voice in Settings.")
                except Exception:  # noqa: BLE001 — already landed
                    pass
                return True
            pending = confirm_gate.pending_request()
            if pending is None:
                return False
            if not confirm_gate.resolve_pending(
                    pending[0], approved, via="voice"):
                return False
            try:
                self._add_system(
                    "Confirmed by voice." if approved
                    else "Denied by voice.")
            except Exception:  # noqa: BLE001 — the answer already
                pass  # landed; a failed caption must not un-consume it
            return True
        except Exception:  # noqa: BLE001 — never break transcripts
            return False

    # --------------------------------- capture indicator (v5.48, item 21)
    def _on_capture_indicator(self, kind: str, active: bool):
        """capture_guard indicator listener: fires on the CAPTURING
        thread, so it touches no widget — it only marshals through
        the bridge signal, the same pattern watcher callbacks use
        (watcher_event). The render reads the guard's live state."""
        try:
            self.signals.capture_indicator_changed.emit(
                str(kind), bool(active))
        except Exception:  # noqa: BLE001 — a display must never
            pass  # sink the capture it reports

    def _render_capture_indicator(self, kind: str = "",
                                  active: bool = False):
        """GUI-thread render of the capture pill (v5.48, programme
        item 21): the text is built from
        capture_guard.active_captures(), never from the event's
        own claim, so overlapping captures of several kinds read
        as one honest line; hidden when the set empties."""
        pill = getattr(self, "capture_pill", None)
        if pill is None:
            return
        try:
            from voice_assistant.core.gui_facade import capture_guard

            kinds = capture_guard.active_captures()
        except Exception:  # noqa: BLE001
            kinds = frozenset()
        if not kinds:
            pill.setText("")
            pill.setVisible(False)
            return
        labels = {
            "camera": "● Camera in use",
            "microphone": "● Microphone in use",
            "screenshot_save": "● Saving screenshot",
        }
        order = ("camera", "microphone", "screenshot_save")
        parts = [labels[k] for k in order if k in kinds]
        parts += [f"● {k} in use" for k in sorted(kinds)
                  if k not in labels]
        pill.setText(" · ".join(parts))
        pill.setVisible(True)
