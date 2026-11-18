"""The failure script for action-tool failures (v5.51, H-1).

ONE formatter, applied by ToolRegistry.execute() (core/
tools_registry.py) to a tool call's result dict before it leaves
the funnel, so every brain — Gemini Live, the local/Ollama path,
the cloud text path and the lite session — sees the same shaped
failure, because every one of them speaks the result's `message`
field (core/local_client._result_to_text renders an error dict
as "Error: {message}", and planner failures read `message` too).

The codebase's failure convention (found in execute(), v5.43 H8):
a result is a FAILURE exactly when it is a dict with
status == "error", carrying a machine-readable `kind` and a
human `message`. There is no `ok` flag in this codebase. A handler
that RETURNS an error-looking string is not a failure here — it
flows out as {"status": "success", "result": <string>}, exactly
as before H-1, and this formatter never touches it.

Scope — deliberate, and pinned by the H-1 suite:
  * ACTION tools only: the caller passes `is_read` from the
    gate's own classification (core/funnel/tools_gate._is_read_class —
    _READ_ONLY_TOOLS, plugin "read" class, and the read actions
    of action-dispatched tools). Read-class failures return
    byte-identical.
  * Success results return byte-identical (identity).
  * Gate / always-refuse failures return byte-identical: kinds
    "not_confirmed" (the confirmation gate — no handler, denied,
    gate error) and "refused" (failsafe trip, protected paths /
    destinations, install-tree writes, epoch void) keep their
    existing exact wording, including the pinned marker
    "needs the user's OK in VYUK's window". "validation" is
    exempt for the same reason: its `message` is the validator's
    exact text and the dict carries the v5.50 self-repair
    `schema`; the handler never ran, so there is no attempt to
    narrate.
  * Unknown tools (not in the registry) return byte-identical:
    there is no classified action to narrate.

What an in-scope failure gains:
  result["failure"] = {"tried": ..., "next": ...}
    tried — what she attempted, in plain terms: the tool name
      plus the salient args, summarised by the EXISTING redaction
      machinery (core/confirm_gate.summarize_args — the same
      masked summary the confirmation popup shows: sensitive key
      names and secret-shaped values become •••, so a secret is
      never echoed raw into a result a brain may speak).
    next — what she will do differently, or why she stopped,
      derived from the H8 `kind` (see _KIND_NEXT below). The
      wording NEVER claims a retry happened and never promises
      one that will not happen: every mapping is either a stop
      with its reason, or the honest stop line. There is no
      retry anywhere in the executor for these paths, so the
      script invents none.
  and the result's `message` gains ONE appended line —
  "<original message> <tried>. <next>" — so the brains that
  speak `message` speak the script, while the original
  diagnostic text stays intact at the head of the message.

Kind -> next mapping (the H8 taxonomy execute() actually
produces — _handler_error_kind in core/funnel/tools_gate.py plus the
funnel's own kinds; there is NO "not_found" kind in this
taxonomy: a missing file/module surfaces as "unavailable",
which is the mapping that names what was missing):
  timeout       the target stopped responding in time; she
                stopped and won't keep hammering it.
  unavailable   names the missing module/file when the error
                message carries it; she stopped — no other way
                to run the tool until it is available.
  not_found     (defensive alias, not produced today) same
                missing-naming as unavailable.
  budget        the executor's per-minute safety budget
                tripped; she stopped rather than keep calling.
  handler_error no sensible kind-derived alternative exists:
                the honest stop line.
  (any other)   the honest stop line.

v5.56 (PF-5): in-scope failures ALSO carry
result["failure_action"] (see failure_action /
perform_failure_action below) — an OFFERED next step (retry /
open setting / run doctor check), executed only when taken, and
one appended sentence in `message`. result["failure"] itself
keeps its exact v5.51 shape.
"""

import re
from typing import Any

# v5.51 (H-1): kinds whose messages are pinned verbatim by the
# gate / validator contract — never reworded by this formatter.
# Audit A5: "timeout" joins them — a timeout result's message is
# the complete guidance (outcome unknown, do not auto-retry), and
# it must never gain a "Retry" next-step offer.
_EXEMPT_KINDS = frozenset(
    {"not_confirmed", "refused", "validation", "timeout"})

_HONEST_STOP = (
    "I don't have another way to try that, so I stopped — "
    "nothing else was attempted and nothing was retried.")

_KIND_NEXT = {
    "timeout": (
        "The target stopped responding in time, so I stopped — "
        "I won't keep hammering it, and I didn't retry it."),
    "budget": (
        "I hit the executor's safety limit on tool calls, so I "
        "stopped there — I won't keep calling tools, and "
        "nothing was retried."),
    "handler_error": _HONEST_STOP,
}

# What-was-missing extractors for "unavailable" / "not_found":
# the two shapes _handler_error_kind's FileNotFoundError /
# ImportError funnel actually carries ("Execution failed: …").
_MISSING_PATTERNS = (
    re.compile(r"No module named '([^']+)'"),
    re.compile(r"No such file or directory:?\s*'([^']+)'"),
    re.compile(r"\[Errno 2\][^']*'([^']+)'"),
)


def _missing_what(message: str) -> str | None:
    """The missing module/file named by an error message, or
    None. A secret-shaped token is never echoed (the same shape
    detector the arg summary uses makes that call)."""
    for pattern in _MISSING_PATTERNS:
        match = pattern.search(message or "")
        if match:
            token = match.group(1).strip()[:80]
            if not token:
                return None
            try:
                from ..confirm_gate import _looks_like_secret  # Phase 3 F2b: file now in core/funnel/; flat sibling climbs one more level

                if _looks_like_secret(token):
                    return None
            except Exception:  # noqa: BLE001 — detector down:
                pass  # the patterns only match quoted names
            return token
    return None


def _next_for(kind: str, message: str) -> str:
    """(b) of the H-1 script: the kind -> next mapping."""
    if kind in ("unavailable", "not_found"):
        missing = _missing_what(message)
        if missing:
            return (f"It needs '{missing}', which wasn't there, "
                    f"so I stopped — I don't have another way to "
                    f"run it until that's available.")
        return ("Something it needs wasn't there, so I stopped "
                "— I don't have another way to run it until "
                "that's available.")
    return _KIND_NEXT.get(kind, _HONEST_STOP)


def _tried(name: str, args: dict[str, Any] | None) -> str:
    """(a) of the H-1 script: tool name + salient args, via the
    existing confirm_gate.summarize_args redaction machinery
    (no parallel redactor is invented here). If that machinery
    is somehow unavailable, the args are NOT echoed raw — the
    line degrades to the tool name alone."""
    if not args:
        return f"I tried '{name}' with no arguments"
    try:
        from ..confirm_gate import summarize_args  # Phase 3 F2b: file now in core/funnel/; flat sibling climbs one more level

        summary = summarize_args(args)
    except Exception:  # noqa: BLE001 — never echo args unredacted
        return f"I tried '{name}'"
    if not summary or summary == "(no arguments)":
        return f"I tried '{name}' with no arguments"
    return f"I tried '{name}' with {summary}"


def apply_failure_script(name: str, args: dict[str, Any] | None,
                         result: Any, *, is_read: bool = False,
                         known: bool = True) -> Any:
    """Apply the v5.51 H-1 failure script to one execute()
    result. Identity (the same object, untouched) for everything
    out of scope: non-dict results, successes, read-class tools,
    unknown tools, and the exempt gate/validation kinds. An
    in-scope failure returns a NEW dict: every original key
    preserved, `message` gaining the one-line script, and the
    parts under result["failure"] = {"tried", "next"}."""
    if not isinstance(result, dict):
        return result
    # Audit A5: the contract statuses (plus legacy "error" from
    # direct callers) reach the scope checks below; successes
    # return untouched, exactly as before.
    from .tool_result import is_non_success as _is_non_success  # Phase 3 F2b: tool_result now a funnel sibling (F2a interim closed)

    if not _is_non_success(result):
        return result
    if is_read or not known:
        return result
    kind = str(result.get("kind") or "")
    if kind in _EXEMPT_KINDS:
        return result
    if "failure" in result:
        return result
    original = str(result.get("message") or "")
    tried = _tried(name, args if isinstance(args, dict) else None)
    nxt = _next_for(kind, original)
    line = f"{tried}. {nxt}".replace("\n", " ").strip()
    out = dict(result)
    out["message"] = f"{original} {line}".strip() \
        if original else line
    out["failure"] = {"tried": tried, "next": nxt}
    # v5.56 (PF-5): the offered next-step action rides in its
    # OWN top-level key — result["failure"] keeps its exact
    # v5.51 shape {"tried", "next"} (pinned by test_v551_h1),
    # and the message gains one appended sentence naming the
    # offer. The offer is never a claim: "Retry" means "ask me
    # to run this call again" (a fresh execute() through the
    # gate), never "I retried it" — the v5.51 no-retry-claim
    # rule still holds for everything already done.
    action = failure_action(name, kind, original)
    out["failure_action"] = action
    out["message"] = (f"{out['message']} Offered next step: "
                      f"{action['label']} — {action['detail']}.")
    return out


# ---------------------------------------------------------------------------
# v5.56 (Wave 2, PF-5): from WORDING to ACTION.
#
# Every in-scope failure (the same scope as apply_failure_script
# — action tools only; read-class, gate/refused/validation and
# unknown tools are untouched, exactly as in v5.51) now carries
# result["failure_action"] = {"action", "label", "target",
# "detail"} and a matching sentence in `message`. The three
# action kinds, and what makes each one WORKING rather than a
# dead button:
#   retry        re-issue the identical call through the real
#                funnel — perform_failure_action() calls
#                tools_registry's module-level `tools.execute`
#                (the same gate + failure script apply again,
#                so a retry is never a gate bypass). A retry is
#                only ever OFFERED here; nothing auto-retries.
#   open_setting open the named Settings card in VYUK's window
#                — perform_failure_action() calls the opener
#                registered by the GUI (register_setting_opener;
#                the Settings page registers its card switch at
#                build time). With no GUI opener registered
#                (headless / tests) the action still names its
#                real target card and perform returns
#                {"ok": False, "reason": "no_gui"} — an honest
#                unavailable, never a silent no-op.
#   run_doctor   run the matching doctor.py check function for
#                real — run_doctor_check() imports doctor.py
#                (stdlib-only at import) and captures the
#                check's own output. The check runs synchronously
#                and returns its text; it never sends anything.
# Tool-family precedence beats kind precedence: an audio/mic
# tool opens the Voice card, a browser/network tool runs the
# world check — because "the relevant setting / check" is the
# PF-5 wording, not "any setting / check".
# ---------------------------------------------------------------------------

#: Tool-name fragments -> (action, target). Checked before the
#: kind table, in dict order (first match wins).
_TOOL_ACTION_HINTS = (
    (("volume", "audio", "mic", "mute", "speak", "tts", "sound"),
     "open_setting", "voice"),
    (("browser", "web", "network", "search", "download"),
     "run_doctor", "world"),
    (("brain", "model", "ollama", "gemini"),
     "run_doctor", "brain"),
)

#: kind -> (action, target) when no tool hint matched.
_KIND_ACTION = {
    "timeout": ("retry", "same_call"),
    "budget": ("retry", "same_call"),
    "unavailable": ("run_doctor", "module_imports"),
    "handler_error": ("run_doctor", "tool_registry"),
}

#: Doctor check functions whitelisted for run_doctor (names
#: are doctor.py's own check_* functions; anything else is
#: refused, never attempted).
_DOCTOR_CHECKS = {
    "microphone": "check_microphone",
    "brain": "check_brain",
    "tool_registry": "check_tool_registry",
    "module_imports": "check_module_imports",
    "world": "check_world",
    "watchers": "check_watchers",
    "capability_probes": "check_capability_probes",
}

#: Action -> human label spoken in the failure message.
_ACTION_LABELS = {
    "retry": "Retry",
    "open_setting": "Open the relevant setting",
    "run_doctor": "Run the doctor check",
}

#: The GUI's settings opener: a callable(target_card: str)
#: -> dict, registered by the Settings page at build time.
#: Tests inject a fake through register_setting_opener().
_setting_opener = None


def register_setting_opener(fn) -> None:
    """Register the GUI opener for open_setting actions
    (callable taking a card name, returning a dict). Pass None
    to unregister. Never raises."""
    global _setting_opener
    try:
        _setting_opener = fn
    except Exception:  # noqa: BLE001
        pass


def failure_action(name: str, kind: str, message: str = "") -> dict:
    """Pure chooser: (tool name, failure kind) -> the offered
    action dict {"action", "label", "target", "detail"}.
    Detail names the missing thing for unavailable failures
    (from the same _missing_what extractor as the wording),
    and always states what the action WILL do (never what it
    already did). Never raises."""
    try:
        low = str(name or "").lower()
        for fragments, action, target in _TOOL_ACTION_HINTS:
            if any(frag in low for frag in fragments):
                break
        else:
            action, target = _KIND_ACTION.get(
                str(kind or ""), ("run_doctor", "tool_registry"))
        label = _ACTION_LABELS[action]
        if action == "retry":
            if str(kind or "") == "budget":
                detail = ("ask me to try this call again once "
                          "the safety window passes")
            else:
                detail = "ask me to try this call again"
        elif action == "open_setting":
            detail = f"open the {target} setting in VYUK's window"
        else:
            missing = _missing_what(message or "")
            if missing:
                detail = (f"run the doctor's {target} check to "
                          f"see what is missing ({missing})")
            else:
                detail = f"run the doctor's {target} check"
        return {"action": action, "label": label, "target": target,
                "detail": detail}
    except Exception:  # noqa: BLE001 — an action must never break
        return {"action": "run_doctor", "label": "Run the doctor check",
                "target": "tool_registry",
                "detail": "run the doctor's tool_registry check"}


def run_doctor_check(target: str) -> dict:
    """Actually run one whitelisted doctor.py check and return
    {"check", "ran", "output"} (output = the check's own
    printed text, tail-trimmed). Unknown targets return
    {"ran": False, "error": ...} — never attempted. The doctor
    module is imported lazily (it is stdlib-only at import);
    any import/call failure returns ran False with the error,
    never raises."""
    import contextlib
    import io

    fn_name = _DOCTOR_CHECKS.get(str(target or ""))
    if not fn_name:
        return {"check": str(target or ""), "ran": False,
                "error": f"no whitelisted doctor check for {target!r}"}
    # Mypy re-baseline 2026-11-05: the old `from ... import doctor`
    # is invalid under the flat core.* mapping (core is the
    # top-level package, so three dots climb past it and mypy
    # 2.4.0 aborts the whole run with "No parent module"); it was
    # valid only under the voice_assistant.* mapping. This chain
    # preserves both mappings: flat tree-root doctor.py first,
    # then the voice_assistant package form, then unavailable.
    try:
        import doctor as _doctor
    except Exception:  # noqa: BLE001
        try:
            from voice_assistant import doctor as _doctor  # type: ignore[no-redef]
        except Exception as exc2:  # noqa: BLE001
            return {"check": target, "ran": False,
                    "error": f"doctor module unavailable: {exc2}"}
    try:
        fn = getattr(_doctor, fn_name, None)
        if not callable(fn):
            return {"check": target, "ran": False,
                    "error": f"doctor has no check {fn_name!r}"}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn()
        output = buf.getvalue()
        if len(output) > 2000:
            output = output[-2000:]
        return {"check": target, "ran": True, "output": output}
    except Exception as exc:  # noqa: BLE001
        return {"check": target, "ran": False, "error": str(exc)}


def perform_failure_action(action: dict, *, name: str = "",
                           args: dict | None = None) -> dict:
    """Execute an offered failure action for real, and report
    what happened. retry -> a fresh funnel call (gate applies
    again); open_setting -> the registered GUI opener, or an
    honest {"ok": False, "reason": "no_gui"} when no GUI is
    attached; run_doctor -> run_doctor_check(). Never raises;
    unknown actions return ok False."""
    try:
        if not isinstance(action, dict):
            return {"ok": False, "reason": "not_an_action"}
        kind = str(action.get("action") or "")
        if kind == "retry":
            try:
                from .tools_registry import tools as _tools  # Phase 3 F2b: file now in core/funnel/; flat sibling climbs one more level (Phase 3 F3: moved to core/funnel/)

                result = _tools.execute(
                    str(name or ""), dict(args or {}))
                return {"ok": True, "action": "retry",
                        "result": result}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "action": "retry",
                        "error": str(exc)}
        if kind == "open_setting":
            opener = _setting_opener
            if not callable(opener):
                return {"ok": False, "action": "open_setting",
                        "reason": "no_gui",
                        "target": action.get("target")}
            try:
                opened = opener(str(action.get("target") or ""))
                return {"ok": True, "action": "open_setting",
                        "target": action.get("target"),
                        "result": opened}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "action": "open_setting",
                        "error": str(exc)}
        if kind == "run_doctor":
            ran = run_doctor_check(str(action.get("target") or ""))
            return {"ok": bool(ran.get("ran")), "action": "run_doctor",
                    "target": action.get("target"), "result": ran}
        return {"ok": False, "reason": f"unknown action {kind!r}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

