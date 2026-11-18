"""Policy cluster split out of core/tools_gate.py (refactor Phase 1 step 3).
Bodies moved verbatim; behaviour is unchanged. Every name here stays
importable from core.funnel.tools_gate via re-export.
"""

# GAPS-23 (F-V3): the read-action split lives in
# core/funnel/tool_classification.py now — the single
# source of truth. _is_read_action is re-exported from
# there; _is_read_class is the canonical classifier's
# is_read verdict plus the plugin gate class.

from typing import Any
from .tool_classification import (_is_read_action, classify_call)  # noqa: F401  (re-export; GAPS-23 F-V3)
from .tools_gate_egress import (_plugin_gate_class)


def _is_read_class(name: str, args: dict[str, Any] | None) -> bool:
    """True when this call is READ-class (v5.46, programme item
    5, A3) — this module's own classification, in one place, for
    the executor's failsafe refusal: the tool is in
    _READ_ONLY_TOOLS, its plugin gate class is "read" (H19b), or
    this call is the read action of an action-dispatched tool
    (_is_read_action, the speaker-lock split). EVERYTHING else
    is non-read for the failsafe — including use_toolbox (a
    dispatcher that can reach any tool, so classifying it read
    would reopen the whole registry) and take_screenshot (a
    capture, deliberately kept out of _READ_ONLY_TOOLS since
    H2): the A3 brief's letter is "every tool outside the
    read-only set" refuses while the failsafe is tripped."""
    if _plugin_gate_class(name) == "read":
        return True
    return classify_call(name, args).is_read
