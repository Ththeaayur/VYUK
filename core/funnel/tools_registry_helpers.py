# tools_registry registry-helpers mixin (batch 1 split):
# activity logging, argument validation, human ask, and
# dispatch-argument shaping, moved verbatim from
# ToolRegistry. (GAPS-12: the MCP execution copy that used
# to live here — _execute_mcp — is deleted; MCP calls run
# the same native stages as every other call, with the MCP
# legs in the execute-stage mixins.)
import datetime
import inspect
from collections.abc import Callable  # def-time: _dispatch_args annotation
from typing import Any

from .tools_registry_validators import (  # INTENTIONAL (batch 1):
    _validate_args_against_schema,  # validate_args resolves it here
)


class ToolRegistryHelpersMixin:
    def _log_activity(self, name: str, status: str):
        """Append one line per execution to data/activity.log. Never raises."""
        try:
            import pathlib

            log_path = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "activity.log"  # Phase 3 F3: file now in core/funnel/ — one more level up to the package root
            log_path.parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.datetime.now().isoformat(timespec="seconds")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"{timestamp} | {name} | {status}\n")
                # RET-1: a funnel success inside a voice turn also
                # writes the voice-task marker line beside the
                # tool's own line. The tool's line is byte-identical;
                # the marker is what the daily count derives "used"
                # days and the nudge opt-in from (core/daily_count.py).
                # Only successes are marked, and only while a voice
                # turn is active (core/voice_origin.py) — the
                # success logging runs in the caller thread after
                # the deadline join, so the flag is visible here.
                if status.startswith("success"):
                    try:
                        from ..voice_origin import voice_turn_active
                    except Exception:  # noqa: BLE001 — no flag, no marker
                        voice_turn_active = None
                    if voice_turn_active is not None \
                            and voice_turn_active():
                        f.write(f"{timestamp} | voice_task | success\n")
        except Exception:  # noqa: BLE001 — logging must never break a tool call
            pass

    def validate_args(self, name: str, args: Any) -> str | None:
        """Strictly validates `args` against the tool's declared schema.

        Returns None when the call may run, or a human-readable error
        string naming the tool, the field, and the problem (e.g.
        "Invalid arguments for tool 'adjust_volume': 'steps' must be
        an integer (got str)"). Hand-rolled against the declared
        JSON-schema subset the registry uses — required fields (root
        and nested), the primitive types, enum/const membership under
        strict JSON equality, numeric bounds, string length/pattern,
        array item types and additionalProperties (audit fix A4).
        Undeclared extra arguments pass through untouched ONLY for
        open schemas: a schema declaring additionalProperties: false
        rejects them, and several handlers accept **kwargs-style
        extras, so validation must never break a working tool on an
        open schema. A field explicitly set to null is rejected
        unless its schema allows null (type "null", a type list
        containing it, const null, or an enum containing null).
        """
        tool = self._tools.get(name)
        if tool is None:
            return f"Unknown tool: {name}"
        return _validate_args_against_schema(
            name, args, tool.get("parameters") or {})

    def _ask_human(self, name: str, view: dict[str, Any],
                   verbatim: set[str], _audited, _audit_gate):
        """One fail-closed confirmation ask (v5.55, SE-3.1 /
        HE-1): the shared ask for the Rule-of-Two check and
        the MCP branch. Returns True on approval, else the
        audited not_confirmed error dict the caller returns.
        Mirrors the native gate block's failure shapes (no
        handler / decline / gate error) without its
        scope-ask machinery, which is native-navigation-only.
        On approval the audit gate records "user-approved".
        """
        try:
            from .. import confirm_gate  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            if not confirm_gate.has_handler():
                self._log_activity(
                    name, "error: not confirmed (no handler registered)")
                _audit_gate["value"] = "refused:no-handler"
                return _audited({
                    "status": "error", "kind": "not_confirmed",
                    "message": (f"'{name}' is a critical action and "
                                "needs the user's OK in VYUK's window "
                                "first — no confirmation handler is "
                                "active, so nothing was run.")})
            try:
                from ..confirm_gate import FULL_FIDELITY_KEYS  # Phase 3 F3: file now in core/funnel/; import climbs one more level

                verbatim = set(verbatim) | {
                    k for k in (view or {})
                    if str(k).lower() in FULL_FIDELITY_KEYS}
            except Exception:  # noqa: BLE001 — fidelity is
                pass  # presentation; the gate decision stands
            try:
                _dec_before = confirm_gate.last_decision()
            except Exception:  # noqa: BLE001 — join is additive
                _dec_before = None
            _approved = confirm_gate.request_confirmation(
                name, view, verbatim_keys=verbatim)
            try:
                _dec_after = confirm_gate.last_decision()
            except Exception:  # noqa: BLE001 — join is additive
                _dec_after = None
            if _dec_after is not None \
                    and _dec_after != _dec_before \
                    and _dec_after.get("tool_name") == name:
                _audit_gate["request_id"] = \
                    _dec_after.get("request_id")
            if not _approved:
                _audit_gate["value"] = "user-denied"
                self._log_activity(name, "error: not confirmed by user")
                return _audited({
                    "status": "error", "kind": "not_confirmed",
                    "message": (f"'{name}' is a critical action and "
                                "needs the user's OK in VYUK's window "
                                "first — it wasn't given, so nothing "
                                "was run.")})
            _audit_gate["value"] = "user-approved"
            return True
        except Exception as e:  # noqa: BLE001 — a broken gate denies
            self._log_activity(
                name, f"error: confirmation gate failed: {e!s}")
            _audit_gate["value"] = "refused:gate-error"
            return _audited({
                "status": "error", "kind": "not_confirmed",
                "message": (f"'{name}' is a critical action and "
                            "needs the user's OK in VYUK's window "
                            "first — the confirmation check itself "
                            "failed, so nothing was run.")})

    def _dispatch_args(self, name: str, handler: Callable,
                       args: dict[str, Any]
                       ) -> tuple[dict[str, Any], list[str]]:
        """Filters executor args down to what the handler can accept.

        Returns (filtered_args, dropped_keys). validate_args lets
        undeclared extras through only for open schemas (a closed
        schema rejects them — audit fix A4, and Batch 2 closes
        every fixed-signature schema at register()), but most
        handlers have fixed signatures — one plausible extra
        field from the brain used to turn a valid call into a
        raw TypeError ("got an unexpected keyword argument").
        So at dispatch: when the handler's signature has no
        **kwargs, keys that are not named parameters of the
        handler are dropped and the drop is logged; handlers
        that accept **kwargs receive everything, exactly as
        before. If the signature can't be inspected, args pass
        through as-is.

        Audit fix Batch 2 (TOOL-02): a drop is NEVER silent in
        the caller (execute()): for a non-read tool the call is
        refused before the handler runs, and for a read tool
        the dropped keys are surfaced in the result. The drop
        list here is what makes that possible — callers must
        not ignore it.
        """
        try:
            params = inspect.signature(handler).parameters
        except (TypeError, ValueError):
            return args, []
        if any(p.kind is inspect.Parameter.VAR_KEYWORD
               for p in params.values()):
            return args, []
        allowed = {
            key for key, p in params.items()
            if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                          inspect.Parameter.KEYWORD_ONLY)
        }
        dropped = [k for k in args if k not in allowed]
        if not dropped:
            return args, []
        self._log_activity(
            name, f"dropped args the handler doesn't accept: {sorted(dropped)}")
        return ({k: v for k, v in args.items() if k in allowed},
                sorted(dropped))
