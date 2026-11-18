# GUI split #3 INTENTIONAL: pf_surfaces split verbatim into
# pf_surfaces_helpers.py, pf_proof.py, pf_settings_help.py,
# pf_permissions.py, pf_citizenship.py, pf_privacy.py. This file
# re-exports every name the original module defined, so every
# existing importer keeps working unchanged.

"""Wave 9 (v5.64) — PF surfaces, WIRED in the conversion pass.

PF-1 first-run proof, PF-8 self-explaining settings + shortcuts,
PF-9 in-app help, PF-18 permissions with revoke switches,
PF-21 desktop citizenship, and the PF-3 privacy dashboard GUI
over the Wave 6 dashboard API (core/privacy.py).

WIRED means: the widgets below are placed in live windows by
the conversion pass (see core/w9_wiring.py): Proof on the
onboarding done page; Settings-explained, Shortcuts, Help,
Permissions and the Privacy Dashboard as More-settings cards;
Citizenship on the Everyday page beside Presence. No sidebar
buttons were added — the sidebar stays frozen at 7 buttons
(Assistant, Reminders, Memory, Skills, Settings, Pulse,
Senses), following the Doctor precedent (Doctor lives under
Advanced, page index 7, no sidebar entry). PLACEMENTS
below documents the placement per widget. The disclaimer
footers were stripped at wiring time; the PF-3 delete-everywhere
gap itself is NOT closed by that strip (file records only; DB
rows are rollback material) — see the Privacy Dashboard copy.

Import convention, matched to the existing GUI code: the
Doctor page lazily imports its backend inside a worker
(gui/doctor_page.py imports voice_assistant.doctor only when
it runs), and the Senses page takes its backends as injected
providers. This module follows both: the PF-3 widget takes an
injected ``api`` (a provider seam, exactly like SensesPage's
providers dict), and the default api lazily imports
voice_assistant.core.memory.privacy only when a widget is
constructed without one. Nothing is imported at module
import time beyond stdlib + PySide6, so importing this
module never touches the memory store, the settings store,
or any permission state.

Honesty rules carried over from Wave 6 (never weakened):
- Opinions are labelled as opinions, never rendered as facts.
  The dashboard is the one place opinions are visible as
  themselves, labelled, because the user must be able to see
  what is stored about them; the fact prompt still never
  renders opinions (Wave 6 CP-4.7).
- Inferred traits expire after 90 days (Wave 6 ethics rule).
  The dashboard shows the rule on every inferred row; the
  per-row expiry date is owed from the dashboard API, whose
  list output carries no expiry field (see PF-3 notes).
- PF-18 switches reflect a real readable state or honest
  unknown. The default state for every permission is unknown:
  no platform permission probe is wired in this module, so
  inventing "granted" would be fabrication. Revoke actions
  are documented seams (apply() wiring points), not wired
  enforcement: pressing Apply records intents and says so
  plainly. Nothing is revoked by this module, ever.
- PF-21 autostart consent is a recorded intent, not a
  system change: the toggle model never writes autostart
  state itself. The wiring step owns the platform write.
- Every failure and empty state in these widgets states
  what happened AND a next step (the PF-5 bar, applied to
  surfaces). No dead-end text ships in this module.
- Premium copy standard (v5.57 A9.4): every string reads
  premium, the same term names the same thing everywhere
  (VYUK, command palette, Advanced), and no string
  is a placeholder.

PF-3 vs its acceptance criterion, stated plainly: the
criterion asks that delete remove a fact everywhere it is
stored, including derived stores. The Wave 6 API does NOT
do that — delete_one deletes the file record only, and
says so when a key is DB-only, because the frozen DB is
rollback material. This widget surfaces that note verbatim
instead of hiding it, so a delete that leaves a DB row
behind never looks complete. Full everywhere-delete is
OWED (see the handoff), not claimed here.
"""


from .pf_surfaces_helpers import (
    MODULE_VERSION,
    PLACEMENTS,
    STAGED_NOTE,
    _card,
    _muted,
    _status_label,
)
from .pf_proof import (
    CHECK_FAILED,
    CHECK_NOT_RUN,
    CHECK_PASSED,
    PF1_CHECK_DEFS,
    FirstRunProofWidget,
    ProofCheck,
    ProofRun,
    build_proof_checks,
)
from .pf_settings_help import (
    HELP_INDEX,
    HELP_INDEX_BY_ID,
    SETTING_EXPLANATIONS,
    SETTING_EXPLANATIONS_BY_KEY,
    HelpTopic,
    HelpWidget,
    SettingExplanation,
    SettingsExplainedWidget,
    ShortcutsWidget,
    explain_setting,
    help_search,
    load_shortcut_rows,
    validate_help_index,
)
from .pf_permissions import (
    PERMISSION_DEFS,
    PERMISSION_DENIED,
    PERMISSION_GRANTED,
    PERMISSION_UNKNOWN,
    PermissionEntry,
    PermissionsWidget,
    apply_permission_changes,
    permissions_model,
)
from .pf_citizenship import (
    CONSENT_DECLINED,
    CONSENT_GRANTED,
    CONSENT_NOT_ASKED,
    AutostartConsent,
    CitizenshipCheck,
    CitizenshipWidget,
    citizenship_model,
    clean_uninstall_info,
)
from .pf_privacy import (
    KIND_LABELS,
    WIPE_CONFIRM_TEXT,
    PrivacyDashboardWidget,
    default_privacy_api,
    kind_label,
    milestone_line,
)

__all__ = [
    "MODULE_VERSION",
    "PLACEMENTS",
    "STAGED_NOTE",
    "PF1_CHECK_DEFS",
    "ProofCheck",
    "ProofRun",
    "build_proof_checks",
    "FirstRunProofWidget",
    "SettingExplanation",
    "SETTING_EXPLANATIONS",
    "explain_setting",
    "load_shortcut_rows",
    "SettingsExplainedWidget",
    "ShortcutsWidget",
    "HelpTopic",
    "HELP_INDEX",
    "validate_help_index",
    "help_search",
    "HelpWidget",
    "PermissionEntry",
    "permissions_model",
    "apply_permission_changes",
    "PermissionsWidget",
    "AutostartConsent",
    "CitizenshipCheck",
    "citizenship_model",
    "clean_uninstall_info",
    "CitizenshipWidget",
    "kind_label",
    "milestone_line",
    "default_privacy_api",
    "PrivacyDashboardWidget",
]
