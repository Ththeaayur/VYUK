"""Refusal family of core/brain/task_coordinator.py (skills split B42).

Moved verbatim from task_coordinator.py: the funnel executor, the §7
menu refusal rewrite (batch 4), the §7 refusal-shape table (batch 5),
the §7 refusal rewrite, and the confirm-stop detector. No behaviour
change; the facade re-exports every name here.
"""

from __future__ import annotations

import re
def _funnel_execute(tool: str, args: dict, cancel_token=None) -> dict:
    """Default executor: the funnel, every gate intact.

    B20: ``cancel_token`` is the step's cooperative token
    (core/brain/task_cancel.live_token) — passed straight to
    the registry, which hands it ONLY to handlers that declare
    a `cancel_token` parameter. None (the default) keeps the
    old per-call token behaviour exactly. The token is passed
    ONLY when supplied: test fakes of registry.execute take
    two arguments, and an unconditional third argument would
    break every one of them (the coordinator's
    _executor_accepts_token rule, same reason).
    """
    try:
        from ..funnel.tools_registry import tools as _registry  # Phase 3 F3: moved to core/funnel/
        if cancel_token is None:
            return _registry.execute(tool, args)
        return _registry.execute(tool, args, cancel_token=cancel_token)
    except Exception as exc:  # noqa: BLE001 — fail the step, not the loop
        return {"status": "failure", "kind": "unavailable",
                "message": f"funnel unavailable for {tool}: {exc}"}


def _rewrite_menu_refusal(out: dict) -> dict:
    """Refused menu step → refused outcome, never silent success.

    §7 consumers batch 4: the funnel's menu handler renders
    engine refusals as success-carrying sentences ("Menu path …
    refused: …" for invoke, "Menu list refused (…)" for list),
    so without this rewrite a refused menu path would record
    as a successful step. The two sentence shapes are the only
    ones rewritten — success lines ("Menu path … invoked …",
    "Menu path … opened …", "Menu roots: …") pass through
    untouched, and a non-string result passes through untouched.
    The rewritten kind is "menu_refused" (the funnel rendering
    carries no engine code for invoke refusals, so no code is
    invented); the message is the engine's sentence verbatim.
    """
    if not isinstance(out, dict):
        return out
    if str(out.get("status", "")) != "success":
        return out
    result = out.get("result", "")
    if not isinstance(result, str):
        return out
    text = result.strip()
    if (text.startswith("Menu path ") and " refused: " in text) or \
            text.startswith("Menu list refused ("):
        return {"status": "refused", "kind": "menu_refused",
                "message": result}
    return out


# §7 consumers batch 5: refusal-sentence shapes per §7 tool.
# Each entry is (prefix_or_regex, refused_kind). The shapes are
# read off the funnel handlers' own renderings (never guessed):
# handlers return these sentences as success-carrying results,
# so through the funnel directly the sentence IS the signal —
# but through the coordinator a step returning one recorded as
# a SUCCESSFUL step. The coordinator rewrites them (below) so
# the step fails honestly instead.
# Caller-validation strings (this batch): validation failures
# that REACH the coordinator as success ARE in this table, with
# per-tool *_validation kinds — a bad action value or a missing
# arg is not an engine refusal, but through the coordinator it
# recorded exactly like one: a successful step that did nothing.
# Caller-validation strings that CANNOT reach the coordinator
# are NOT in this table, each for a proven reason: menu action /
# path values (coordinator build-time validation rejects them
# in StepDef.from_mapping); every mistype / null string (funnel
# schema validation refuses with kind=validation before the
# handler runs); the dead fallthroughs ("Unknown surface",
# journal's trailing "Unknown journal action" — routing and
# the enum check make them unreachable); and the confirm stops
# ("… needs confirmation:", batch 6 — checked before this
# rewrite, so they pause instead of failing).
# Success lines are pinned per tool in tests: none of them
# matches any shape below (dialog success shares the "File
# dialog " opener, so its shape is a tight regex on the action
# word plus the refusal/stop infix, never a bare prefix).
_S7_REFUSAL_SHAPES = {
    # Dialog: describe() refusal + needs_confirmation stop.
    # Caller-validation (this batch): the engine's unknown-action
    # rendering escapes the tight enum regex below, because the
    # action word itself is the invalid value ("File dialog
    # bogus refused: unknown dialog action …") — the tight shape
    # covers only the enum actions, so the unknown-action shape
    # is listed separately. Success lines ("File dialog open
    # <path>") carry no "refused: unknown dialog action" infix.
    "file_dialog_action": [
        (r"^File dialog (open|save|folder) "
         r"(refused: |stops for confirmation: )", "dialog_refused"),
        (r"^File dialog .* refused: unknown dialog action ",
         "dialog_refused"),
    ],
    # Surfaces: route_surface refusal + describe() refusal for
    # every action leg (list renderers fall back to describe()).
    # Caller-validation (this batch): every "Unknown X action"
    # and "X needs a Y" leg that reaches the coordinator.
    "system_surface_action": [
        ("Surface refused (", "surface_refused"),
        ("refused (", "surface_refused"),
        ("Opening a tray menu needs a tray icon name.",
         "surface_validation"),
        ("Invoking a tray menu path needs an icon name.",
         "surface_validation"),
        ("Invoking a tray menu path needs a path.",
         "surface_validation"),
        ("Unknown tray action '", "surface_validation"),
        ("Switching needs a taskbar button name.",
         "surface_validation"),
        ("Launching needs a taskbar button name.",
         "surface_validation"),
        ("Pinning needs a taskbar button name.",
         "surface_validation"),
        ("Unknown taskbar action '", "surface_validation"),
        ("Searching Start needs search text.", "surface_validation"),
        ("Launching from Start needs a result name.",
         "surface_validation"),
        ("Unknown Start action '", "surface_validation"),
        ("Acting on a notification needs its id",
         "surface_validation"),
        ("Unknown notification action '", "surface_validation"),
        ("Opening a settings page needs a page name.",
         "surface_validation"),
        ("Unknown settings action '", "surface_validation"),
    ],
    # Adapter: mismatch (two variants share the opener) +
    # resolution refusal (needs_confirmation suffix included —
    # batch-4 precedent: an engine confirm stop fails rather
    # than silently succeeding; the re-issue-after-approval
    # protocol is still owed, not invented here).
    # Caller-validation (this batch): the empty-phrase leg.
    "adapter_action": [
        ("Adapter mismatch: ", "adapter_refused"),
        ("Adapter resolution refused (", "adapter_refused"),
        ("Resolving an adapter phrase needs a phrase to resolve.",
         "adapter_validation"),
    ],
    # Drag: describe() refusal (needs_confirmation stop renders
    # through the same shape plus a confirm suffix).
    # Caller-validation (this batch): missing handle ids, and
    # the norm1000 mistarget (wrong-length arrays pass the
    # schema's array type, so the handler line fires).
    "drag_drop_action": [
        ("Drag refused (", "drag_refused"),
        ("Dragging needs a source handle id.", "drag_validation"),
        ("Dragging needs a target handle id.", "drag_validation"),
        ("A monitor-targeted drag needs a norm1000 (x, y) "
         "target point to convert.", "drag_validation"),
    ],
    # Monitor: config-action refusal + resolve/convert/place.
    # Caller-validation (this batch): every "Unknown X action"
    # and "X needs a Y" leg that reaches the coordinator. NOT
    # listed: "Converting needs numeric point coordinates" —
    # the schema's number type pre-empts it entirely (no
    # schema-passing value fails float()), so it never renders
    # through any real path.
    "monitor_action": [
        ("Monitor action refused: ", "monitor_refused"),
        ("Monitor resolve refused: ", "monitor_refused"),
        ("Monitor convert refused: ", "monitor_refused"),
        ("Monitor place refused: ", "monitor_refused"),
        ("Unknown monitor action '", "monitor_validation"),
        ("Resolving a monitor needs a reference.",
         "monitor_validation"),
        ("Resolving a monitor point needs norm1000 numbers "
         "(nx, ny).", "monitor_validation"),
        ("Converting needs a direction: ", "monitor_validation"),
        ("Converting needs a monitor reference.",
         "monitor_validation"),
        ("Placing a window rect needs a rect (left, top, "
         "right, bottom).", "monitor_validation"),
        ("Placing a window rect needs 4 numbers (left, top, "
         "right, bottom).", "monitor_validation"),
        ("Placing a window rect needs a source monitor.",
         "monitor_validation"),
        ("Placing a window rect needs a target monitor.",
         "monitor_validation"),
    ],
    # Journal: corrupt-store report, reconcile/compensate
    # refusals, stopped run, and both needs_confirmation stops
    # (batch-4 precedent, as for the adapter above).
    # Caller-validation (this batch): the enum leg and every
    # "X needs a Y" leg that reaches the coordinator. NOT
    # listed: "Listing journal entries needs a numeric limit"
    # (the schema's integer type pre-empts it entirely) and
    # the two "… needs confirmation" stops (batch 6 — checked
    # before this rewrite, so they pause instead of failing).
    "journal_action": [
        ("Journal unreadable", "journal_refused"),
        ("Reconcile refused: ", "journal_refused"),
        ("Compensation refused: ", "journal_refused"),
        ("Compensation stopped for entry ", "journal_refused"),
        ("Reconciliation needs confirmation: ", "journal_refused"),
        ("Compensation needs confirmation: ", "journal_refused"),
        ("Unknown journal action '", "journal_validation"),
        ("Listing journal entries needs a limit of at least 1.",
         "journal_validation"),
        ("Reconciling needs a journal entry id.",
         "journal_validation"),
        ("Reconciling needs happened true or false — ",
         "journal_validation"),
        ("Reconciling needs a confirmer name — ",
         "journal_validation"),
        ("Compensating needs a journal entry id.",
         "journal_validation"),
        ("Compensating needs a confirmer name — ",
         "journal_validation"),
    ],
    # Context menu (batch 8): trigger/list refusals render as
    # "Context menu refused (code): ..." and invoke refusals
    # through the item-3 describe shape ("Context menu path …
    # refused: ..."). The invoke shape is a tight regex (the
    # success lines share the "Context menu path " opener).
    # Caller-validation (this batch): the enum leg and the
    # missing-path leg.
    "context_menu_action": [
        ("Context menu refused (", "context_menu_refused"),
        (r"^Context menu path .* refused: ", "context_menu_refused"),
        ("Unknown context menu action '", "context_menu_validation"),
        ("Invoking a context menu path needs a path, ",
         "context_menu_validation"),
    ],
}


def _rewrite_s7_refusal(tool: str, out: dict) -> dict:
    """Refused §7 step → refused outcome, never silent success.

    §7 consumers batch 5: generalizes batch 4's menu rewrite to
    every §7 funnel tool (dialog, surface, adapter, drag,
    monitor, journal; context menu joined in batch 8). Menu steps still go through
    _rewrite_menu_refusal unchanged; every other §7 tool matches
    its shapes in _S7_REFUSAL_SHAPES. Non-§7 tools and
    non-success outcomes pass through untouched, as do success
    lines (an invoked/opened/listed/resolved line never matches
    a refusal shape). The rewritten message is the engine's
    sentence verbatim; no engine code is invented.
    Caller-validation batch: the same table also carries the
    validation shapes (per-tool *_validation kinds) — a bad
    action value or a missing arg that reaches the coordinator
    as success now fails honestly with the handler's sentence
    verbatim, instead of recording a successful step that did
    nothing. Validation strings that cannot reach the
    coordinator (menu build-time blocks, funnel schema
    refusals, dead fallthroughs, confirm stops) are not in
    the table; see its comment.
    """
    if tool == "menu_action":
        return _rewrite_menu_refusal(out)
    shapes = _S7_REFUSAL_SHAPES.get(tool)
    if shapes is None:
        return out
    if not isinstance(out, dict):
        return out
    if str(out.get("status", "")) != "success":
        return out
    result = out.get("result", "")
    if not isinstance(result, str):
        return out
    text = result.strip()
    for pattern, kind in shapes:
        if pattern.startswith("^"):
            import re as _re
            if _re.match(pattern, text):
                return {"status": "refused", "kind": kind,
                        "message": result}
        elif text.startswith(pattern):
            return {"status": "refused", "kind": kind,
                    "message": result}
    return out


def _detect_s7_confirmation_stop(tool: str, out: dict):
    """Engine confirm stop → (sentence, confirm_arg), else None.

    §7 consumers batch 6: an engine needs_confirmation stop is
    a question, not a failure — _run_step checks this BEFORE
    _rewrite_s7_refusal, so a stop pauses the task for
    approval instead of failing as a refusal (the batch-4/5
    precedent). Only success-carrying string results are
    examined: funnel gate refusals are non-success and pass
    through untouched, as do genuine refusals, non-§7 tools,
    and non-string results. Every shape below is read off the
    engine/handler rendering, never guessed:

    * menu: the C-14 stop renders through menu describe() as a
      "refused" line whose reason carries "(C-14 approve-first)"
      (menu_paths.py invoke_path) — the C-14 phrase is the
      only thing distinguishing a stop from a refusal at the
      sentence layer, so the match requires it. If the
      engine's reason text ever changes, detection degrades
      to the old behaviour (the stop rewrites as a refusal
      and fails) — never to a silent success.
    * dialog: describe() renders stops as "stops for
      confirmation" (file_dialogs.py), distinct from
      "refused:" — so this shape is disjoint from the
      batch-5 dialog refusal shape by construction.
    * surface: the engine stop code "needs-confirmation"
      renders via describe() as "refused
      (needs-confirmation):" — checked before the batch-5
      refusal shapes, which also match the bare "refused ("
      prefix (a surface stop must pause, not fail).
    * adapter: a confirm-gated resolution renders as a
      refused line plus the handler's "Confirm it explicitly,
      then resolve again." suffix (app_adapters.py
      confirmation-required + pack handler). An OK resolution
      carrying "(confirmation still required)" is NOT a stop:
      the resolution happened, and the flag travels to the
      next step (the menu/dialog action it resolves for).
    * drag: describe() renders the stop as a refused line
      plus the handler's "Confirm it explicitly, then drag
      again with confirm_destructive set." suffix.
    * journal: the handler renders stops as "… needs
      confirmation:" lines (pack_s7consumers2.py) — checked
      before the batch-5 refusal table, which lists the same
      two shapes as journal_refused (a journal stop must
      pause, not fail).
    * context menu (batch 8): the invoke walk reuses item 3,
      so a C-14 stop renders through the context describe()
      as "Context menu path … refused: … confirm it first
      (C-14 approve-first) …" — the same C-14 phrase
      distinguisher as menu, checked before the batch-8
      refusal table (whose invoke regex would also match
      the stop sentence; a stop must pause, not fail).
    * monitor: no stop shape — a pure read never gates.
    """
    if not isinstance(out, dict):
        return None
    if str(out.get("status", "")) != "success":
        return None
    result = out.get("result", "")
    if not isinstance(result, str):
        return None
    text = result.strip()
    if tool == "menu_action":
        if text.startswith("Menu path ") and " refused: " in text \
                and "confirm it first (C-14 approve-first)" in text:
            return (result, "confirmed")
        return None
    if tool == "file_dialog_action":
        if re.match(r"^File dialog (open|save|folder) "
                    r"stops for confirmation: ", text):
            return (result, "confirm_overwrite")
        return None
    if tool == "system_surface_action":
        if text.startswith("refused (needs-confirmation): "):
            return (result, "confirmed")
        return None
    if tool == "adapter_action":
        if text.startswith("Adapter resolution refused (") \
                and "Confirm it explicitly, then resolve again." in text:
            return (result, "confirmed")
        return None
    if tool == "drag_drop_action":
        if text.startswith("Drag refused (") \
                and "Confirm it explicitly, then drag again with " \
                   "confirm_destructive set." in text:
            return (result, "confirm_destructive")
        return None
    if tool == "journal_action":
        if text.startswith("Reconciliation needs confirmation: ") \
                or text.startswith("Compensation needs confirmation: "):
            return (result, "confirmed")
        return None
    if tool == "context_menu_action":
        if text.startswith("Context menu path ") \
                and " refused: " in text \
                and "confirm it first (C-14 approve-first)" in text:
            return (result, "confirmed")
        return None
    return None


# R5 (routine wallpaper steps): the monitor a step names is
# gone — pause the task resumably, never claim success.
# B1 (2026-11-06): the same pause now covers the WHOLE set
# family — every reason the set can fail with — because the
# handlers return a funnel-shaped failure dict for each of
# them (core/funnel/tool_result.py: failure_result, kind
# "wallpaper_not_set") and this detector matches that kind
# structurally, so a new reason needs no new detector case.
# The sentence-prefix list below is only a backstop for a
# failure that still arrives as a success-carried string
# (an unconverted path, or a future plain-string regression).
_WALLPAPER_SET_TOOLS = frozenset((
    "display_control", "set_wallpaper", "wallpaper_apply",
))

# B1 backstop: every plain-string failure sentence the three
# set tools can produce (file:line anchors in the handlers).
# The "Wallpaper not set: " prefix covers every reason code
# the shared seam can produce, present and future.
_WALLPAPER_FAILURE_PREFIXES = (
    "Wallpaper not set:",
    "Wallpaper not applied:",
    "Wallpaper not applied for state",
    "Could not save the current wallpaper",
    "Could not save the wallpaper style",
    "Windows refused the",
    "Could not set the wallpaper:",
    "Windows could not apply the wallpaper:",
    "Windows accepted the wallpaper change",
    "Wallpaper image not found:",
    "That wallpaper file doesn't exist:",
)


def _is_wallpaper_set_failure(out: dict, text: str) -> bool:
    """True when a set-family failure arrived, on either shape.

    Structural first: a failure dict whose kind is the set
    family. Prefix second: a success-carried (or otherwise
    non-success) string starting with a known set-failure
    sentence. Never raises.
    """
    try:
        status = str(out.get("status", "") or "")
        if status != "success" and str(out.get("kind", "") or "") == (
                "wallpaper_not_set"):
            return True
        return text.startswith(_WALLPAPER_FAILURE_PREFIXES)
    except Exception:  # noqa: BLE001 — a bad shape is not a failure
        return False


def _detect_wallpaper_monitor_gone(tool: str, out: dict):
    """Wallpaper set that failed -> the plain-words sentence
    for the pause, else None.

    The shape is proved from the tree, not guessed: a
    display_control set_wallpaper leg whose selector names
    no connected monitor returns the plain string
    "Wallpaper not set: unknown-monitor: ..." — and
    "unknown-monitor" is in NO refusal marker, so the
    funnel returns it as {"status": "success", "result":
    <that sentence>} (probe, 2026-11-06). Without this
    detector the coordinator records the step as a SUCCESS
    for a wallpaper that was never set. B1 (2026-11-06):
    the same hole held for EVERY other set failure — the
    whole reason set (art-missing, could-not-save-previous,
    could-not-read-current, system-call-refused,
    system-call-failed, read-back-mismatch, windows-only),
    the legacy save / system-call sentences, and the
    wallpaper_apply "not applied" sentences all flowed
    through the funnel as success on BOTH executor paths
    (probe, 2026-11-06). The handlers now return a
    funnel-shaped failure dict for each of them, and this
    detector matches that kind structurally — plus the
    sentence prefixes as a backstop — so the whole family
    pauses. The detector reads the sentence off either
    shape — a success-carrying string (the real funnel
    shape) or a non-success dict carrying it in "message"
    (other executors) — and only for the set family:
    wallpaper_revert already reports a gone monitor honestly
    in its own message ("That screen is no longer
    connected, so nothing was reverted"), and it is not a
    set, so it never pauses here. A match pauses the task
    (the run loop intercepts it exactly like an engine
    confirm stop) so the human can fix the cause and resume;
    the step re-resolves the monitor fresh on resume
    (resolve_wallpaper_monitor reads a fresh table every
    call), so a reconnected monitor just proceeds. Never
    raises."""
    if tool not in _WALLPAPER_SET_TOOLS:
        return None
    if not isinstance(out, dict):
        return None
    status = str(out.get("status", "") or "")
    if status == "success":
        text = out.get("result", "")
    else:
        text = out.get("message", "")
    if not isinstance(text, str):
        return None
    text = text.strip()
    # B1: the funnel's failure script appends " I tried '<tool>'
    # with ..." to failure messages — strip it here so the pause
    # sentence names the reason, not the script's appendix. No
    # set-family sentence of ours contains that marker.
    _tried_at = text.find(" I tried '")
    if _tried_at >= 0:
        text = text[:_tried_at].strip()
    if "unknown-monitor" not in text:
        # B1: not the gone-monitor case — the rest of the set
        # family pauses too; only a non-family sentence falls
        # through.
        if not _is_wallpaper_set_failure(out, text):
            return None
        return _wallpaper_failure_sentence(text)
    # The refusal already lists the monitors that ARE
    # connected — carry that list into the pause sentence
    # so the human can pick one without guessing.
    detail = text
    marker = "unknown-monitor:"
    at = detail.find(marker)
    if at >= 0:
        detail = detail[at + len(marker):].strip()
    if len(detail) > 200:
        detail = detail[:200] + "…"
    return ("That screen is no longer connected, so the "
            "wallpaper was not set"
            + (f" ({detail})" if detail else "")
            + ". Reconnect the screen — or pick one that is "
              "connected — and resume; this step will try "
              "again.")


def _wallpaper_failure_sentence(text: str) -> str:
    """The pause sentence for a non-monitor set failure.

    Names the REASON in plain words (the brief's rule: the
    pause carries the named reason), not the raw sentence:
    the "Wallpaper not set: " / "Wallpaper not applied: "
    prefix is stripped to the reason code, the legacy
    sentences map to their reason ("Could not save ..." ->
    the code in its parens, "Windows refused ..." ->
    system-call-refused, "Could not set ..." ->
    system-call-failed, "Windows accepted ..." ->
    read-back-mismatch, image-not-found -> art-missing),
    and only a sentence with no reason code keeps its raw
    wording as the detail. The hint names the fix; the step
    re-runs on resume. Never raises.
    """
    detail = text.strip()
    if detail.startswith("Wallpaper not set: "):
        detail = detail[len("Wallpaper not set: "):]
    elif detail.startswith("Wallpaper not applied: "):
        detail = detail[len("Wallpaper not applied: "):]
    elif detail.startswith("Wallpaper not applied for state"):
        detail = "read-back-mismatch"
    elif (detail.startswith("Wallpaper image not found:")
          or detail.startswith("That wallpaper file doesn't exist:")):
        detail = "art-missing"
    elif detail.startswith("Could not save the current wallpaper"):
        # "... changing it (<reason>), so the desktop was left
        # unchanged." — the reason code sits in the parens.
        start = detail.find("(")
        end = detail.find(")", start + 1) if start >= 0 else -1
        if start >= 0 and end > start:
            detail = detail[start + 1:end]
    elif detail.startswith("Windows refused the"):
        detail = "system-call-refused"
    elif (detail.startswith("Could not set the wallpaper:")
          or detail.startswith(
              "Windows could not apply the wallpaper:")):
        detail = "system-call-failed"
    elif detail.startswith("Windows accepted the wallpaper change"):
        detail = "read-back-mismatch"
    detail = detail.rstrip(".").strip()
    if len(detail) > 200:
        detail = detail[:200] + "…"
    low = detail.lower()
    if "art-missing" in low:
        hint = "Put the image file in place"
    elif "could-not-save-previous" in low or "could-not-read-current" in low:
        hint = "Check the saved wallpaper record can be read and written"
    elif "system-call-refused" in low or "system-call-failed" in low:
        hint = "Check nothing is holding the desktop"
    elif "read-back-mismatch" in low or "still shows" in low:
        hint = "Check the desktop to see what actually applied"
    elif "windows-only" in low or "not windows" in low:
        hint = "This step needs Windows"
    else:
        hint = "Fix the cause"
    return ("The wallpaper was not set"
            + (f" ({detail})" if detail else "")
            + f". {hint} — and resume; this step will try "
              "again.")
