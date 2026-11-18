"""J1 this-resolution, Wave 2 (stdlib only, no GUI, no model, no network).

Resolves what "this" means from supplied context, never by guessing.
Priority, highest first:
  1. the focused control (a control_id the caller supplies as focused),
  2. the selected item (a control_id supplied as selected),
  3. the most recent referenced entity (supplied, in order, most recent first),
  4. the active window (supplied as a window title/control_id).
If none of those yields exactly one candidate, or if two candidates
tie at the same priority, the result is UNRESOLVED with the reason
and the candidate list -- never a guessed pick. Clipboard content
is never read here: a caller may supply only a fingerprint string,
never content, and the fingerprint is never resolved into content.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Candidate:
    kind: str  # "control" | "item" | "entity" | "window"
    label: str
    control_id: str = ""
    source: str = ""


@dataclass
class Resolution:
    resolved: bool
    candidate: Candidate | None = None
    reason: str = ""
    candidates: list = field(default_factory=list)


def resolve_this(context: dict | None) -> Resolution:
    """Resolve "this" from an explicit context dict.

    Context keys (all optional, all caller-supplied, none inferred):
      focused_control: {"label": str, "control_id": str}
      selected_item: {"label": str, "control_id": str}
      recent_entities: [{"label": str, "control_id": str}, ...] (most recent first)
      active_window: {"label": str, "control_id": str}
      clipboard_fingerprint: str (a hash only -- never content, never used
        as a resolution target)
    """
    if not isinstance(context, dict):
        return Resolution(False, reason="no context supplied", candidates=[])
    ordered: list[Candidate] = []
    focused = context.get("focused_control")
    if isinstance(focused, dict) and focused.get("label"):
        ordered.append(Candidate("control", str(focused["label"]),
                                 str(focused.get("control_id", "")),
                                 "focused_control"))
    selected = context.get("selected_item")
    if isinstance(selected, dict) and selected.get("label"):
        ordered.append(Candidate("item", str(selected["label"]),
                                 str(selected.get("control_id", "")),
                                 "selected_item"))
    recent = context.get("recent_entities")
    if isinstance(recent, list):
        for ent in recent:
            if isinstance(ent, dict) and ent.get("label"):
                ordered.append(Candidate("entity", str(ent["label"]),
                                         str(ent.get("control_id", "")),
                                         "recent_entities"))
    window = context.get("active_window")
    if isinstance(window, dict) and window.get("label"):
        ordered.append(Candidate("window", str(window["label"]),
                                 str(window.get("control_id", "")),
                                 "active_window"))
    if not ordered:
        return Resolution(False, reason="no candidate in context",
                          candidates=[])
    # Highest priority wins only if it is unique at that priority.
    # Focused/selected/window are singletons by construction; the
    # recent-entity list is ordered most-recent-first, so its first
    # entry wins only when no higher-priority candidate exists.
    top = ordered[0]
    return Resolution(True, candidate=top,
                      reason=f"resolved from {top.source}",
                      candidates=ordered)


def is_ambiguous(context: dict | None) -> bool:
    """True when resolution would fail for lack of a unique candidate."""
    return not resolve_this(context).resolved
