"""Policy cluster split out of core/tools_gate.py (refactor Phase 1 step 3).
Bodies moved verbatim; behaviour is unchanged. Every name here stays
importable from core.funnel.tools_gate via re-export.
"""

# GAPS-23 (F-V3): the sets live in
# core/funnel/tool_classification.py now — the single
# source of truth for gate classification. This module
# is a re-export shim so every existing importer keeps
# working at its old address.

from .tool_classification import (_CONFIRM_GATE_TOOLS)  # noqa: F401 (confirm concern, moved verbatim; GAPS-23 F-V3)
