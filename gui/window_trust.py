"""Trust surfaces (v5.56, Wave 2, Stream F): PF-7 degraded-mode
status + PF-10 report-a-problem.

DESIGN (why this module imports no Qt at the top): every pure
function here — degraded_mode_summary(), render_*, the report
bundle + freeze + mailto builders — is importable and testable
without a GUI toolkit, exactly like window_shell's lazy Qt
imports inside handlers. Qt is imported ONLY inside the dialog
builders at the bottom. A dialog is therefore a thin renderer
over a tested pure core, never the logic itself.

PF-7 — ONE surface, LIVE, from ONE state source:
  degraded_mode_summary() reads core/ambient.current_
  degradation() — the SAME state core/ambient.ambient_packet()
  feeds the brains' system prompts (PR-4). If the surface and
  the prompt ever disagree on what is down, that is a defect,
  not a difference of opinion: both read the same function.
  "Live" (PF acceptance line: "always reflects current ...
  updates when state changes") is the dialog's Refresh button
  + a 5-second auto-refresh timer, both re-rendering from
  current state — the test proves it by flipping ambient flags
  and re-rendering (Live dies -> surface changes; mic drops ->
  surface changes). The surface NEVER claims a broken
  capability works: its "still works" list is computed from
  the down set (never a static "everything works" line), and
  when nothing is down it says so explicitly.
  The PF-12 visibility half rides here too (acceptance line:
  "the current throttling state is visible (via PF-7's
  surface)"): every summary carries the power_policy summary
  line (battery throttle / metered pause / full speed).

PF-10 — bundle + preview + consent-gated LOCAL export:
  build_report_bundle() snapshots version / platform /
  degradation / power policy — deliberately NO log tails, NO
  memory, NO user data (there is no redaction story for logs
  yet; adding a tail is a Wave 10 decision, not a silent
  extra). prepare_report_export() FREEZES that snapshot:
  (preview text, sha256). export_frozen_report() writes
  EXACTLY the frozen preview bytes and refuses when the hash
  does not match — nothing may be added between preview and
  export, so "the bundle contains only what was previewed"
  holds by construction, and the consent checkbox applies to
  the frozen bundle: rebuilding the bundle resets consent.
  SEND SHAPE (honest — no support backend exists): there is
  no endpoint and no upload. The flow offers (a) a local file
  export the user can attach to an email, and (b) a mailto
  draft whose body says to attach that file (the bundle
  itself is never stuffed into a mailto URL — it would be
  truncated and would leak diagnostics into a URL). Wave 10
  must decide: a support inbox address (to prefill the mailto
  recipient) and/or a real endpoint (to replace the export).
  Until then the recipient is empty and the export is the
  send.
"""

import copy
import hashlib
import json
import platform as _platform
import sys
from datetime import datetime

#: Display names for the ambient degradation capability ids
#: (the ids themselves come from core/ambient — this table is
#: display copy only, and the surface/prompt agreement test
#: pins the IDS, not the wording).
_CAPABILITY_NAMES = {
    "live_voice": "Live voice conversation",
    "microphone": "Microphone listening",
    "cloud_brain": "Cloud brain + live web answers",
    "local_brain": "On-device brain",
}

#: Auto-refresh interval for the PF-7 dialog (ms).
STATUS_REFRESH_MS = 5000


def _ambient_state(state=None):
    """The degradation state dict, from ambient when state is
    None. Never raises; falls back to an empty state."""
    try:
        if isinstance(state, dict) and "down" in state:
            return state
        from voice_assistant.core.gui_facade import ambient  # Phase 3 B1b: moved to core/brain/

        return ambient.current_degradation()
    except Exception:  # noqa: BLE001
        return {"down": [], "works": [], "actions": [],
                "prompt_line": None}


def _power_state(policy=None):
    """The power policy dict, from power_policy when policy is
    None. Never raises; None when unavailable."""
    try:
        if isinstance(policy, dict):
            return policy
        from voice_assistant.core.gui_facade import power_policy

        return power_policy.current_policy()
    except Exception:  # noqa: BLE001
        return None


def degraded_mode_summary(state=None, policy=None):
    """PF-7 summary: {"down", "down_plain", "works", "actions",
    "power", "policy", "headline", "body"}. `down` is the
    ambient capability ids (the prompt's source); `works` is
    the still-works list (from the same state — never a static
    claim); `power` is the PF-12 visibility line. When nothing
    is down, headline says all systems working and works lists
    every capability. Never raises."""
    try:
        st = _ambient_state(state)
        down = [str(c) for c in (st.get("down") or [])
                if str(c) in _CAPABILITY_NAMES]
        down_plain = [_CAPABILITY_NAMES[c] for c in down]
        works = [str(w) for w in (st.get("works") or [])]
        actions = [str(a) for a in (st.get("actions") or [])]
        if not down:
            works = ["Live voice conversation",
                     "Microphone listening",
                     "Cloud brain + live web answers",
                     "On-device brain", "Local tools"]
            headline = "All systems working."
        else:
            joined = " and ".join(down_plain)
            headline = f"Degraded mode: {joined} down."
        pol = _power_state(policy)
        power_line = None
        try:
            if pol is not None:
                from voice_assistant.core.gui_facade import power_policy

                power_line = power_policy.policy_summary(pol)
        except Exception:  # noqa: BLE001
            power_line = None
        if power_line is None:
            power_line = "Power state unavailable."
        body = render_degraded_mode_text({
            "down": down, "down_plain": down_plain, "works": works,
            "actions": actions, "power": power_line,
            "headline": headline})
        return {"down": down, "down_plain": down_plain,
                "works": works, "actions": actions,
                "power": power_line, "policy": pol,
                "headline": headline, "body": body}
    except Exception:  # noqa: BLE001
        return {"down": [], "down_plain": [], "works": [],
                "actions": [], "power": "Power state unavailable.",
                "policy": None, "headline": "Status unavailable.",
                "body": "Status unavailable."}


def render_degraded_mode_text(summary):
    """The PF-7 surface as plain text (what the dialog's label
    shows). Pure: re-rendering after a state change is how
    the live-update test proves the surface moves. Never
    raises."""
    try:
        lines = [str(summary.get("headline") or "Status")]
        down_plain = summary.get("down_plain") or []
        if down_plain:
            lines.append("Down: " + "; ".join(str(d) for d in down_plain))
        works = summary.get("works") or []
        if works:
            lines.append("Still works: " + "; ".join(str(w) for w in works))
        actions = summary.get("actions") or []
        if actions:
            lines.append("What to do: " + "; ".join(str(a) for a in actions))
        power = summary.get("power")
        if power:
            lines.append(f"Power & network: {power}")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        return "Status unavailable."


def build_report_bundle(*, extra=None, state=None, policy=None):
    """PF-10 bundle snapshot: version, platform, generated_at,
    degradation state, power policy. NO logs, NO memory, NO
    user content — the bundle is only what the preview shows,
    and the preview shows only this. `extra` (a dict, e.g. a
    user-typed note supplied BEFORE preview) is merged under
    "note" only when given — it is part of the frozen preview
    like everything else, never appended after. Never raises;
    on total failure returns a minimal bundle saying so."""
    try:
        version = "unknown"
        try:
            from voice_assistant.core.gui_facade import VERSION

            version = str(VERSION)
        except Exception:  # noqa: BLE001
            pass
        st = _ambient_state(state)
        pol = _power_state(policy)
        bundle = {
            "app": "VYUK",
            "version": version,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "platform": sys.platform,
            "platform_detail": _platform.platform(),
            "degradation": {
                "down": [str(c) for c in (st.get("down") or [])],
                "works": [str(w) for w in (st.get("works") or [])],
                "actions": [str(a) for a in (st.get("actions") or [])],
            },
            "power_policy": pol,
        }
        if isinstance(extra, dict) and extra:
            bundle["note"] = copy.deepcopy(extra)
        return bundle
    except Exception:  # noqa: BLE001
        return {"app": "VYUK", "version": "unknown",
                "generated_at": datetime.now().isoformat(
                    timespec="seconds"),
                "platform": sys.platform, "degradation": {"down": []},
                "power_policy": None, "bundle_error": True}


def report_preview_text(bundle):
    """The EXACT text the user previews — and, after freezing,
    the exact bytes exported. Canonical JSON (sorted keys,
    indent 2) so preview == export byte-for-byte. Never
    raises."""
    try:
        return json.dumps(bundle, indent=2, sort_keys=True,
                           default=str)
    except Exception:  # noqa: BLE001
        return json.dumps({"bundle_error": True}, indent=2,
                           sort_keys=True)


def prepare_report_export(bundle):
    """FREEZE a bundle for send: {"bundle" (deep copy),
    "preview" (exact preview text), "sha256" (of the preview
    bytes)}. The dialog previews `preview` and exports ONLY
    `preview`; mutating the original bundle afterwards cannot
    change what ships. Never raises."""
    try:
        frozen = copy.deepcopy(bundle)
        preview = report_preview_text(frozen)
        digest = hashlib.sha256(preview.encode("utf-8")).hexdigest()
        return {"bundle": frozen, "preview": preview, "sha256": digest}
    except Exception:  # noqa: BLE001
        preview = report_preview_text({"bundle_error": True})
        return {"bundle": {"bundle_error": True}, "preview": preview,
                "sha256": hashlib.sha256(
                    preview.encode("utf-8")).hexdigest()}


def export_frozen_report(prepared, path):
    """Write a frozen prepared bundle to `path`, byte-exact.
    Refuses (no file written) when the preview hash does not
    match — a tampered prepared bundle never ships. Returns
    {"path", "bytes", "sha256"} or {"error": ...}. The ONLY
    writer for report bundles: nothing else may write bundle
    bytes, so preview == file holds by construction."""
    try:
        preview = prepared.get("preview")
        digest = prepared.get("sha256")
        if not isinstance(preview, str) or not isinstance(digest, str):
            return {"error": "not a frozen prepared bundle"}
        actual = hashlib.sha256(preview.encode("utf-8")).hexdigest()
        if actual != digest:
            return {"error": "frozen bundle hash mismatch — refusing "
                             "to write; rebuild and re-preview"}
        data = preview.encode("utf-8")
        with open(path, "wb") as fh:
            fh.write(data)
        return {"path": str(path), "bytes": len(data), "sha256": digest}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def mailto_draft(prepared, recipient=""):
    """The mailto handoff for a frozen bundle: subject +
    short body (degradation headline + power line + 'attach
    the exported file'). The bundle itself is NEVER in the
    URL. `recipient` is empty until Wave 10 names a support
    inbox — an empty recipient is an honest draft, not a
    send to nowhere (the user fills it in). Returns {"mailto",
    "subject", "body"}. Never raises."""
    try:
        from urllib.parse import quote

        bundle = prepared.get("bundle") if isinstance(prepared, dict) \
            else None
        version = "unknown"
        headline = "No degradation reported."
        power = ""
        if isinstance(bundle, dict):
            version = str(bundle.get("version") or "unknown")
            down = (bundle.get("degradation") or {}).get("down") or []
            if down:
                headline = ("Degraded mode: "
                            + ", ".join(str(d) for d in down)
                            + " down.")
            pol = bundle.get("power_policy")
            if isinstance(pol, dict) and pol.get("reason"):
                power = str(pol["reason"])
        subject = f"VYUK problem report (v{version})"
        body_lines = [
            "Problem report from VYUK.",
            headline,
        ]
        if power:
            body_lines.append(power)
        body_lines.append(
            "The full diagnostic bundle is in the exported file — "
            "please attach it to this email. Nothing beyond the "
            "previewed bundle is included.")
        body = "\n".join(body_lines)
        to = str(recipient or "").strip()
        mailto = (f"mailto:{to}?subject={quote(subject)}"
                  f"&body={quote(body)}")
        return {"mailto": mailto, "subject": subject, "body": body}
    except Exception:  # noqa: BLE001
        return {"mailto": "mailto:", "subject": "VYUK problem report",
                "body": "Problem report from VYUK."}


# ---------------------------------------------------------------------------
# Dialog builders — Qt is imported HERE, lazily, so the pure core
# above stays importable without a GUI toolkit. Builders return
# (dialog, widgets) WITHOUT exec()ing, so tests can drive them
# offscreen; show_* wrappers exec for production use.
# ---------------------------------------------------------------------------

def build_degraded_mode_dialog(parent=None):
    """PF-7 dialog: headline + body label, Refresh button, and
    a 5s auto-refresh timer — both re-render from CURRENT
    ambient state, so the surface moves when state moves.
    Returns (dialog, widgets dict)."""
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import (
        QDialog,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QVBoxLayout,
    )
    from voice_assistant.gui.style import DIALOG_STYLE

    dlg = QDialog(parent)
    dlg.setWindowTitle("System status")
    # v5.57 (redo): the dialog in the house dark language.
    dlg.setStyleSheet(DIALOG_STYLE)
    dlg.resize(560, 360)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(20, 20, 20, 16)
    lay.setSpacing(10)
    title = QLabel("What is down, what still works, what to do")
    title.setStyleSheet("font-size: 16px; font-weight: 600;")
    title.setWordWrap(True)
    lay.addWidget(title)
    body_label = QLabel("")
    body_label.setWordWrap(True)
    body_label.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse)
    lay.addWidget(body_label, stretch=1)
    btn_row = QHBoxLayout()
    btn_row.addStretch(1)
    btn_refresh = QPushButton("Refresh")
    btn_row.addWidget(btn_refresh)
    lay.addLayout(btn_row)

    def _refresh():
        summary = degraded_mode_summary()
        body_label.setText(summary["body"])

    btn_refresh.clicked.connect(_refresh)
    timer = QTimer(dlg)
    timer.setInterval(STATUS_REFRESH_MS)
    timer.timeout.connect(_refresh)
    timer.start()
    _refresh()
    widgets = {"body": body_label, "refresh": btn_refresh,
               "timer": timer, "do_refresh": _refresh}
    return dlg, widgets


def show_degraded_mode_dialog(parent=None):
    """Build + exec the PF-7 dialog. Returns the dialog result."""
    dlg, _widgets = build_degraded_mode_dialog(parent)
    return dlg.exec()


def build_report_problem_dialog(parent=None):
    """PF-10 dialog: frozen preview (read-only), consent
    checkbox, Export + mailto buttons. Consent gates the
    FROZEN bundle: Export/mailto stay disabled until the box
    is checked, and rebuilding the bundle (Refresh) resets
    consent — a new bundle always needs a new consent.
    Returns (dialog, widgets dict)."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QCheckBox,
        QDialog,
        QFileDialog,
        QHBoxLayout,
        QLabel,
        QPlainTextEdit,
        QPushButton,
        QVBoxLayout,
    )
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtCore import QUrl
    from voice_assistant.gui.style import DIALOG_STYLE
    from voice_assistant.gui.design_system.dialogs import HudDialog

    dlg = QDialog(parent)
    dlg.setWindowTitle("Report a problem")
    # v5.57 (redo): the dialog in the house dark language.
    dlg.setStyleSheet(DIALOG_STYLE)
    dlg.resize(720, 520)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(20, 20, 20, 16)
    lay.setSpacing(10)
    hint = QLabel(
        "This is exactly what will be exported — nothing more. "
        "Nothing sends until you tick consent and press Export.")
    hint.setObjectName("muted")
    hint.setWordWrap(True)
    lay.addWidget(hint)
    preview = QPlainTextEdit()
    preview.setReadOnly(True)
    preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    lay.addWidget(preview, stretch=1)
    consent = QCheckBox(
        "I have reviewed the bundle above and consent to "
        "exporting it.")
    lay.addWidget(consent)
    btn_row = QHBoxLayout()
    btn_row.addStretch(1)
    btn_refresh = QPushButton("Rebuild bundle")
    btn_export = QPushButton("Export bundle…")
    btn_mailto = QPushButton("Open email draft…")
    btn_close = QPushButton("Close")
    for btn in (btn_refresh, btn_export, btn_mailto, btn_close):
        btn_row.addWidget(btn)
    lay.addLayout(btn_row)
    state = {"prepared": None}

    def _rebuild():
        bundle = build_report_bundle()
        state["prepared"] = prepare_report_export(bundle)
        preview.setPlainText(state["prepared"]["preview"])
        consent.setChecked(False)
        _sync_buttons()

    def _sync_buttons():
        ok = consent.isChecked()
        btn_export.setEnabled(ok)
        btn_mailto.setEnabled(ok)

    def _export():
        prepared = state["prepared"]
        if prepared is None:
            return
        path, _filter = QFileDialog.getSaveFileName(
            dlg, "Export problem bundle", "vyuk-problem-report.json",
            "JSON files (*.json)")
        if not path:
            return
        result = export_frozen_report(prepared, path)
        if "error" in result:
            # v5.57 (redo): the result popup in the same dark
            # language (HudDialog carries it itself).
            HudDialog.warning(dlg, "Export failed", result["error"])
        else:
            HudDialog.information(
                dlg, "Exported",
                f"Bundle exported to:\n{result['path']}\n"
                f"SHA-256: {result['sha256']}\nAttach this file to "
                f"your support email.")

    def _mailto():
        prepared = state["prepared"]
        if prepared is None:
            return
        draft = mailto_draft(prepared)
        QDesktopServices.openUrl(QUrl(draft["mailto"]))

    consent.toggled.connect(_sync_buttons)
    btn_refresh.clicked.connect(_rebuild)
    btn_export.clicked.connect(_export)
    btn_mailto.clicked.connect(_mailto)
    btn_close.clicked.connect(dlg.accept)
    _rebuild()
    widgets = {"preview": preview, "consent": consent,
               "export": btn_export, "mailto": btn_mailto,
               "refresh": btn_refresh, "close": btn_close,
               "state": state, "rebuild": _rebuild,
               "sync": _sync_buttons}
    return dlg, widgets


def show_report_problem_dialog(parent=None):
    """Build + exec the PF-10 dialog. Returns the dialog result."""
    dlg, _widgets = build_report_problem_dialog(parent)
    return dlg.exec()
