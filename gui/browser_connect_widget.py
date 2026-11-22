"""Browser connect — the one-tap connect flow (first-run onboarding, 2026-11-16).

One widget, two hosts: the first-run wizard's Browser page and
the standalone BrowserConnectDialog (opened from the Assistant
strip). The widget is the ONLY caller of this flow in the
wizard/dialog path — the Settings card keeps its own inline
Enable flow as the second way in (pinned by
test_browser_enable's GUI wiring tests).

What one tap does, in order (scope §3):

1. Flip the PF-18 Browser permission to granted, via the same
   Settings-only write path the Permissions page uses
   (pf_permissions.apply_permission_changes -> via="settings").
   This is the explicit human act (a GUI tap); it never happens
   on a tool call and never by voice.
2. Flip browser_allow_real_chrome_attach on, via
   browser_enable._set_opt_in — the same writer the Settings
   Enable button uses.
3. Run browser_enable.enable_browser_control() on a worker
   thread (the attach can take ~10 s) and render the returned
   {state, message} on the page.

States, exactly as the Settings card renders them, plus the
toggle-first rule (parent ruling D7): on needs_restart the
chrome://inspect toggle is offered FIRST — it is the only path
that keeps the real profile (logins and tabs included). Round-3
wizard expansion: the toggle block shows UP FRONT whenever
this machine's path is toggle_or_restart (see
_refresh_path_line) — the toggle is the first thing, not a
consolation after a failed Connect tap. The restart button
appears only after the user presses "Restart Chrome instead",
having read the warning; the restart happens only on that
explicit press, on Windows only, never silently.
CDP-only (parent ruling D5): the page says plainly that VYUK
drives Chrome directly for now and the browser add-on comes
later; nothing here installs the extension, and
browser_extension/manifest.json is untouched. Field-check #45:
the page also names the path this machine takes
(browser_enable.browser_connect_path, re-read whenever the
page appears) instead of describing all three paths
conditionally.

The widget writes nothing on construction — a page that is
merely shown grants nothing. Every write happens on the
"Connect my browser" tap. The honest-cost line
(ASK_COPY["browser"]["cost_line"]) is shown below the path line
whenever the path line does not already state the cost — that is
the "unknown" path only: the launch and toggle paths name the
separate-profile cost in their own line, and the direct/attached
paths pay no cost. A page that never names the cost before the
tap would let the user tap blind (Build 5, §9.1 step 0).

The page also carries the quiet "Not now" link §9.1 step 0
requires. It records nothing durable — the browser stays
unknown, exactly as the wizard's global Continue leaves it —
and shows a confirm line; the user moves on with Continue (the
wizard) or closes the dialog. The widget emits
``not_now_requested`` so hosts can act: the wizard page records
``ask_result = "skipped"``; the standalone dialog closes.
"""

from __future__ import annotations

import json
import threading

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from voice_assistant.core.gui_facade import browser_enable as _be
from voice_assistant.core.gui_facade import first_run_asks as _asks
from voice_assistant.core.gui_facade import settings_voice as _sv

from . import pf_permissions as _pfp

#: Result states that mean "connected, verified by a status read".
_CONNECTED_STATES = ("attached", "already_attached")


class BrowserConnectWidget(QWidget):
    """The browser-connect flow as a reusable widget.

    ``connect_result`` carries each enable/launch/restart result
    as JSON to the GUI-thread slot (the Settings card's signal
    shape, window_brain_services.browser_enable_completed).
    """

    connect_result = Signal(str)
    #: Emitted when the user taps "Not now" (§9.1 step 0). The
    #: widget itself records the skip (below); hosts act on it —
    #: the wizard page records ask_result, the dialog closes.
    not_now_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAccessibleName("Browser connect")
        self._busy = False
        self._chrome_pending: str | None = None
        #: True once the user tapped "Not now" on this page.
        self.skipped = False
        copy = _asks.ASK_COPY["browser"]
        toggle = _asks.TOGGLE_FIRST_LINES

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        title = QLabel(copy["title"])
        title.setObjectName("pageTitle")
        title.setWordWrap(True)
        layout.addWidget(title)
        body = QLabel(copy["body"])
        body.setObjectName("muted")
        body.setWordWrap(True)
        layout.addWidget(body)
        # Field-check #45: the path line names the path THIS
        # machine takes (computed, not conditional). The direct
        # line stays: it states CDP-only-first (D5) in all paths.
        # Build 5 (§9.1 step 0): the honest-cost line is shown
        # below the path line when the path line does not already
        # state the cost — the "unknown" path only (see
        # _refresh_path_line).
        self.path_label = QLabel("")
        self.path_label.setObjectName("muted")
        self.path_label.setWordWrap(True)
        self.path_label.setAccessibleName("Browser connect path")
        layout.addWidget(self.path_label)
        self.cost_label = QLabel("")
        self.cost_label.setObjectName("muted")
        self.cost_label.setWordWrap(True)
        self.cost_label.setAccessibleName("Browser connect cost")
        layout.addWidget(self.cost_label)
        direct = QLabel(copy["direct_line"])
        direct.setObjectName("muted")
        direct.setWordWrap(True)
        layout.addWidget(direct)

        self.btn_connect = QPushButton("Connect my browser")
        self.btn_connect.setObjectName("primaryBtn")
        self.btn_connect.setAccessibleName("Connect my browser")
        self.btn_connect.setMinimumHeight(34)
        self.btn_connect.clicked.connect(self._on_connect_clicked)
        layout.addWidget(self.btn_connect)

        # Build 5 (§9.1 step 0): the quiet "Not now" link. It
        # records nothing durable — the browser stays unknown —
        # and the user moves on with Continue (wizard) or closes
        # the dialog.
        self.btn_not_now = QPushButton("Not now")
        self.btn_not_now.setObjectName("chipBtn")
        self.btn_not_now.setAccessibleName("Not now")
        self.btn_not_now.clicked.connect(self._on_not_now_clicked)
        layout.addWidget(self.btn_not_now)

        self.status_label = QLabel("")
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName("Browser connect status")
        layout.addWidget(self.status_label)

        # Toggle-first block (needs_restart only): the
        # one-switch way that keeps the real profile. Round-3
        # wizard expansion: it also shows up front when this
        # machine's path is toggle_or_restart (see
        # _refresh_path_line) — the toggle is the first thing,
        # not a consolation after a failed Connect tap.
        self.toggle_block = QWidget()
        toggle_layout = QVBoxLayout(self.toggle_block)
        toggle_layout.setContentsMargins(0, 0, 0, 0)
        toggle_layout.setSpacing(8)
        steps = QLabel(toggle["steps"])
        steps.setObjectName("muted")
        steps.setWordWrap(True)
        toggle_layout.addWidget(steps)
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(6)
        self.btn_open_inspect = QPushButton("Open chrome://inspect")
        self.btn_open_inspect.setObjectName("chipBtn")
        self.btn_open_inspect.setAccessibleName(
            "Open chrome inspect")
        self.btn_open_inspect.clicked.connect(self._on_open_inspect)
        toggle_row.addWidget(self.btn_open_inspect)
        self.btn_check_again = QPushButton("Check again")
        self.btn_check_again.setObjectName("chipBtn")
        self.btn_check_again.setAccessibleName("Check again")
        self.btn_check_again.clicked.connect(self._on_check_again)
        toggle_row.addWidget(self.btn_check_again)
        toggle_row.addStretch()
        toggle_layout.addLayout(toggle_row)
        self.btn_restart_instead = QPushButton(
            toggle["restart_instead"])
        self.btn_restart_instead.setObjectName("chipBtn")
        self.btn_restart_instead.setAccessibleName(
            "Restart Chrome instead")
        self.btn_restart_instead.clicked.connect(
            self._on_restart_instead)
        toggle_layout.addWidget(self.btn_restart_instead)
        self.restart_warning = QLabel(toggle["restart_warning"])
        self.restart_warning.setObjectName("muted")
        self.restart_warning.setWordWrap(True)
        self.restart_warning.setVisible(False)
        toggle_layout.addWidget(self.restart_warning)
        self.toggle_block.setVisible(False)
        layout.addWidget(self.toggle_block)

        # Single action row: launch / restart / try-again share
        # one button whose label names the action it will take
        # (the Settings card's shape).
        self.btn_chrome_action = QPushButton("")
        self.btn_chrome_action.setObjectName("primaryBtn")
        self.btn_chrome_action.setMinimumHeight(34)
        self.btn_chrome_action.clicked.connect(
            self._on_chrome_action_clicked)
        self.btn_chrome_action.setVisible(False)
        layout.addWidget(self.btn_chrome_action)

        layout.addStretch()
        self.connect_result.connect(self._on_connect_result)
        # No probe at construction: the path line fills on first
        # show (below). A construction-time probe would consume
        # the connect flow's _is_connected sequence — the enable
        # flow re-reads it (False at enable time, True after the
        # attach) — and a page merely shown must not probe
        # anything before the user reaches it.

    def _refresh_path_line(self) -> None:
        """Re-read this machine's browser path into the path
        line (field-check #45). Read-only probe; any failure
        keeps the previous line, never blanks it, never raises.
        Build 5 (§9.1 step 0): when the path is "unknown" the
        path line names no cost, so the honest-cost line is
        shown below it — the user never taps blind. Every other
        path either states its cost in its own line (launch,
        toggle_or_restart) or pays none (direct, attached), so
        the cost line stays empty there and the cost is never
        stated twice."""
        try:
            result = _be.browser_connect_path()
        except Exception:  # noqa: BLE001 — a failed probe keeps the line
            return
        if not isinstance(result, dict):
            return
        line = str(result.get("line", "")).strip()
        if line:
            self.path_label.setText(line)
        if str(result.get("path", "")) == "unknown":
            self.cost_label.setText(
                str(_asks.ASK_COPY["browser"]["cost_line"]))
        else:
            self.cost_label.setText("")
        # Round-3 wizard expansion (one-tap-simple): the toggle
        # block shows up front when this machine's path is
        # toggle_or_restart — the toggle is the first thing, not
        # a consolation after a failed Connect tap. Hidden for
        # attached/direct (no toggle needed) and for every other
        # path (the result handler owns those). Re-read on every
        # show, like the path line, so it tracks Chrome starting
        # or closing while the wizard is open.
        self.toggle_block.setVisible(
            str(result.get("path", "")) == "toggle_or_restart")

    def showEvent(self, event):  # noqa: N802 (Qt naming)
        # The path can change while the wizard is open (Chrome
        # started or closed) — re-read it every time the page
        # appears, so the line never goes stale.
        self._refresh_path_line()
        super().showEvent(event)

    # ------------------------------------------------------------ actions

    def _set_busy(self, busy: bool, status: str = "") -> None:
        self._busy = bool(busy)
        self.btn_connect.setEnabled(not busy)
        self.btn_chrome_action.setEnabled(not busy)
        self.btn_check_again.setEnabled(not busy)
        self.btn_open_inspect.setEnabled(not busy)
        if status:
            self.status_label.setText(status)

    def _emit_result(self, result: dict) -> None:
        try:
            self.connect_result.emit(json.dumps(result or {}))
        except Exception:  # noqa: BLE001 — a lost result leaves the buttons busy, never crashes
            pass

    def _run_in_thread(self, fn) -> None:
        def run():
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001 — a broken run is a state, never a crash
                result = {"state": "failed",
                          "message": f"Couldn't connect the browser ({exc})."}
            self._emit_result(result)

        threading.Thread(target=run, daemon=True).start()

    def _connect_flow(self) -> dict:
        """The three steps behind one tap. Returns the enable
        result dict; a failed permission flip or opt-in flip
        stops the flow with its own honest message."""
        try:
            record = _pfp.apply_permission_changes(
                [], {"browser": "granted"})
        except Exception as exc:  # noqa: BLE001 — a failed flip says so
            return {"state": "failed",
                    "message": ("Couldn't switch the browser on in "
                                f"Settings ({exc}) — try Settings → "
                                "Permissions.")}
        if "browser" in (record.get("failed") or {}):
            return {"state": "failed",
                    "message": ("Couldn't switch the browser on in "
                                "Settings — try Settings → "
                                "Permissions.")}
        try:
            _be._set_opt_in(True)
        except Exception as exc:  # noqa: BLE001 — a failed flip says so
            return {"state": "failed",
                    "message": ("Couldn't turn browser control on "
                                f"({exc}). Try again from Settings → "
                                "Browser control.")}
        try:
            return dict(_be.enable_browser_control())
        except Exception as exc:  # noqa: BLE001 — enable must answer
            return {"state": "failed",
                    "message": f"Couldn't enable browser control ({exc})."}

    def _on_connect_clicked(self) -> None:
        if self._busy:
            return
        self._hide_actions()
        self._set_busy(True, "Connecting to your Chrome…")
        self._run_in_thread(self._connect_flow)

    def _on_not_now_clicked(self) -> None:
        """The quiet "Not now" link (Build 5, §9.1 step 0).
        Records nothing durable — the browser stays unknown,
        exactly as the wizard's global Continue leaves it — so
        a later browser use still asks again (the refusal +
        strip button is the way back). Mirrors the other first-
        run asks: the answer hides the buttons and shows a
        confirm line; the user moves on with Continue (wizard)
        or closes the dialog. Hosts act on not_now_requested."""
        if self._busy:
            return
        self.skipped = True
        self.btn_connect.setVisible(False)
        self.btn_not_now.setVisible(False)
        self.status_label.setText(
            "Not now — skipped. VYUK will ask again when you "
            "use the browser.")
        self.not_now_requested.emit()

    def _on_check_again(self) -> None:
        """Re-run the enable flow after the chrome://inspect
        toggle — the permission and the opt-in are already on,
        so this is just the probe + attach."""
        if self._busy:
            return
        self._hide_actions()
        self._set_busy(True, "Checking again…")
        self._run_in_thread(
            lambda: dict(_be.enable_browser_control()))

    def _on_open_inspect(self) -> None:
        try:
            QDesktopServices.openUrl(QUrl("chrome://inspect"))
        except Exception:  # noqa: BLE001 — a failed open is not fatal
            pass

    def _on_restart_instead(self) -> None:
        """The user declines the toggle: reveal the restart
        button with its warning. Nothing closes Chrome yet —
        the restart happens only on the button's explicit press."""
        self.restart_warning.setVisible(True)
        self._chrome_pending = "restart"
        self.btn_chrome_action.setText(
            "Restart Chrome with browser control")
        self.btn_chrome_action.setAccessibleName(
            "Restart Chrome with browser control")
        self.btn_chrome_action.setVisible(True)

    def _on_chrome_action_clicked(self) -> None:
        if self._busy:
            return
        pending = self._chrome_pending
        if pending == "retry":
            # "Try again" after a failure state: re-run the whole
            # one-tap flow (permission + opt-in + enable), so a
            # flip that failed the first time is retried too.
            self._on_connect_clicked()
            return
        if pending not in ("launch", "restart"):
            return
        self._hide_actions()
        self._set_busy(
            True,
            "Launching Chrome with browser control…"
            if pending == "launch" else
            "Restarting Chrome with browser control…")

        def run():
            try:
                if pending == "launch":
                    result = _be.launch_chrome_for_browser_control(
                        confirm=True)
                else:
                    result = _be.restart_chrome_for_browser_control(
                        confirm=True)
            except Exception as exc:  # noqa: BLE001
                result = {"state": "failed",
                          "message": f"Couldn't start Chrome ({exc})."}
            return dict(result)

        self._run_in_thread(run)

    # ------------------------------------------------------------ rendering

    def _hide_actions(self) -> None:
        self.toggle_block.setVisible(False)
        self.restart_warning.setVisible(False)
        self.btn_chrome_action.setVisible(False)
        self._chrome_pending = None

    def _on_connect_result(self, payload: str) -> None:
        try:
            result = json.loads(payload) if payload else {}
        except Exception:  # noqa: BLE001 — unparseable payload
            result = {}
        if not isinstance(result, dict):
            result = {}
        state = str(result.get("state", ""))
        message = str(result.get("message", "")).strip()
        self._hide_actions()
        self._set_busy(False)
        if state in _CONNECTED_STATES:
            detail = message
            if detail.startswith("Attached to your Chrome."):
                detail = detail[len("Attached to your Chrome."):].strip()
            status = "Connected — VYUK can see your Chrome."
            if detail:
                status = f"{status}\n{detail}"
            self.status_label.setText(status)
            self.btn_connect.setText("Connected")
            self.btn_connect.setEnabled(False)
            try:
                _sv.clear_browser_no_session()
            except Exception:  # noqa: BLE001 — the clear never breaks it
                pass
            try:
                # Build 5 (§9.1): the offer's job is done.
                _sv.clear_browser_offer()
            except Exception:  # noqa: BLE001 — the clear never breaks it
                pass
            return
        if state == "needs_launch":
            self.status_label.setText(message)
            self._chrome_pending = "launch"
            self.btn_chrome_action.setText(
                "Open Chrome with browser control")
            self.btn_chrome_action.setAccessibleName(
                "Open Chrome with browser control")
            self.btn_chrome_action.setVisible(True)
            return
        if state == "needs_restart":
            # Toggle first (D7): the one-switch way keeps the real
            # profile. The state's own long message is not pasted
            # on the page — the honest-cost line above already
            # states the separate-profile cost, and the restart
            # warning states it again when the restart is offered.
            self.status_label.setText(
                _asks.TOGGLE_FIRST_LINES["headline"])
            self.toggle_block.setVisible(True)
            return
        if state == "chrome_state_unknown":
            self.status_label.setText(message)
            self._chrome_pending = "launch"
            self.btn_chrome_action.setText(
                "Open Chrome with browser control")
            self.btn_chrome_action.setAccessibleName(
                "Open Chrome with browser control")
            self.btn_chrome_action.setVisible(True)
            return
        # Every other state names its own next step; the page
        # renders the message verbatim and offers a retry.
        self.status_label.setText(
            message or "Couldn't connect the browser — try again.")
        self._chrome_pending = "retry"
        self.btn_chrome_action.setText("Try again")
        self.btn_chrome_action.setAccessibleName("Try again")
        self.btn_chrome_action.setVisible(True)

    # ------------------------------------------------------------ test seams

    def run_connect_for_tests(self) -> dict:
        """Run the three connect steps synchronously (tests only):
        the same flow the tap runs, without the thread."""
        return self._connect_flow()
