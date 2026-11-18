"""
Telemetry substrate (v5.56, Wave 2 OB-1/OB-2): OpenTelemetry spans
emitted to a LOCAL FILE only, plus the raw-instrumentation layer.

OB-1 — what this module is
--------------------------
The single span ledger for VYUK. It wraps the OpenTelemetry SDK
(``opentelemetry-sdk`` + ``opentelemetry-semantic-conventions``)
with exactly ONE exporter, the local file exporter below
(``FileSpanExporter``): one JSON line per finished span in
``data/telemetry.jsonl`` (configurable). No other exporter may
exist in shipped code: no OTLP/HTTP/gRPC exporter, no cloud
endpoint, no default that phones home. The module imports
OpenTelemetry LAZILY — when the SDK is absent (or telemetry is
off) every public call is a silent no-op and no file is created.

Telemetry is OFF BY SHAPE. ``configure()`` defaults to disabled;
nothing in this module enables itself at import, and no caller
in this wave enables it either (the coordinator owns the
enablement decision). Viewers of the ledger are dev-only (Q4):
this module only WRITES the ledger; it never renders it.

DELIVERY EVIDENCE — the rule every span obeys
--------------------------------------------
A span closes on DELIVERY EVIDENCE, never on intent:

* A tool span closes when ``ToolRegistry.execute()`` RETURNS a
  result dict to its caller through the funnel — the return
  itself is the delivery. Starting the handler, intending to
  call, or queuing work never closes a span.
* A decorated function's span closes when the function RETURNS
  (delivery = the return value reaching the caller) or RAISES
  (delivery = the exception reaching the caller, recorded with
  error status). A function that merely started never closes.
* A success span means "the funnel delivered a success result";
  a refusal/validation span means "the funnel delivered that
  refusal". A claim ("done", "sent") is NOT delivery evidence —
  only the funnel's returned result is (see core/health_rules
  ``ClaimTracker``, which flags claims with no such delivery).

Span attributes are deliberately thin: tool/function name,
outcome (status/kind), delivery evidence, duration. Argument
VALUES, prompts, and completions are NEVER recorded here — the
redacted audit log (``data/audit.jsonl``) already holds the
accountable record, and span content capture is forced OFF
(see ``instrument_google_genai``).

OB-2 — raw instrumentations on top of OB-1 only
-----------------------------------------------
Traceloop's ``init()`` default-cloud configuration is REJECTED
inside this module: there is no wrapper entry point here, and
no code path here imports a ``traceloop`` package or configures
a cloud exporter. ``instrument_google_genai()`` activates ONLY
the official OpenTelemetry instrumentation for the Google GenAI
SDK (``opentelemetry-instrumentation-google-genai`` — the SDK
the product already depends on, ``google-genai``), pointed at
OB-1's own tracer provider, with message-content capture forced
to NO_CONTENT. It returns False unless telemetry is enabled:
instrumentation without OB-1's provider would emit into a
no-op tracer and prove nothing. Other raw instrumentations are
added the same way — one explicit function each, never a
blanket auto-instrument call.

FAILURE DISCIPLINE — telemetry must never break a tool call:
every public function swallows its own errors and returns a
safe default (None/False/empty). Span finishing is idempotent:
finishing the same span twice ends it once.

GenAI semconv: attribute names come from
``opentelemetry.semconv._incubating.attributes.gen_ai_attributes``
when importable, else the stable string literals — the same
names either way, so spans are identical with or without the
semconv package present.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import pathlib
import threading
import time

log = logging.getLogger("nova.telemetry")

# ---------------------------------------------------------------------------
# Configuration seams (same shape as core/audit: override, else default)
# ---------------------------------------------------------------------------

_DEFAULT_NAME = "vyuk.telemetry"


def _default_path() -> pathlib.Path:
    return (pathlib.Path(__file__).resolve().parent.parent
            / "data" / "telemetry.jsonl")


_path_override: pathlib.Path | None = None
_enabled = False
_provider = None
_exporter = None
_lock = threading.Lock()
_genai_instrumentor = None


def span_path() -> pathlib.Path:
    """Where finished spans are written right now."""
    return _path_override if _path_override is not None \
        else _default_path()


def configure(path=None, enabled: bool = False) -> bool:
    """Enable (or re-configure) local-file telemetry.

    ``enabled=False`` (the default) leaves telemetry OFF: no
    provider is built, no file is created, spans are no-ops.
    ``enabled=True`` builds a TracerProvider whose ONLY processor
    target is the local file exporter — and returns False (still
    off) when the OpenTelemetry SDK is not importable, rather
    than raising. Re-configuring replaces the provider; call
    ``shutdown()`` first when switching files in one process.
    """
    global _path_override, _enabled, _provider, _exporter
    with _lock:
        if path is not None:
            _path_override = pathlib.Path(path)
        if not enabled:
            _enabled = False
            return False
        otel = _load_otel()
        if otel is None:
            _enabled = False
            return False
        try:
            trace_sdk, _resources = otel
            exporter = FileSpanExporter(span_path())
            provider = trace_sdk.TracerProvider(
                resource=_resources.Resource.create(
                    {"service.name": "vyuk"}))
            from opentelemetry.sdk.trace.export import SimpleSpanProcessor
            provider.add_span_processor(SimpleSpanProcessor(exporter))
            _provider = provider
            _exporter = exporter
            _enabled = True
            return True
        except Exception:  # noqa: BLE001 — telemetry never breaks callers
            log.exception("telemetry configure failed; staying off")
            _enabled = False
            return False


def apply_settings(settings=None, path=None) -> bool:
    """Apply the telemetry setting (GAPS-27, M-V8): the enable
    path for telemetry, OFF by default behind explicit opt-in.

    Reads ``telemetry/enabled`` through config_spec.qsettings_get
    (the spec table's bool coercion; a QSettings-like `settings`
    may be passed in — production passes the window's settings
    store — or None, which reads the production store, guarded:
    no Qt / no store reads as the table default, False). Returns
    configure()'s verdict: False when OFF or when the SDK is
    absent, True only when telemetry is really on. Never raises.
    Enabling requires the user to flip the setting; nothing in
    this module ever enables telemetry on its own."""
    try:
        from . import config_spec

        enabled = bool(config_spec.qsettings_get(
            "telemetry/enabled", settings))
    except Exception:  # noqa: BLE001 — any settings failure
        enabled = False  # reads as OFF; telemetry stays off
    return configure(path=path, enabled=enabled)


def reset_configuration() -> None:
    """Restore defaults: telemetry OFF, default span path."""
    global _path_override, _enabled, _provider, _exporter
    shutdown()
    with _lock:
        _path_override = None
        _enabled = False
        _provider = None
        _exporter = None


def is_enabled() -> bool:
    """True only after a successful configure(enabled=True)."""
    return _enabled


def shutdown() -> None:
    """Flush and tear down the provider and the GenAI instrumentor.

    Safe to call when telemetry was never enabled. After shutdown
    spans are no-ops again until configure() runs again.
    """
    global _provider, _exporter, _genai_instrumentor
    try:
        uninstrument_google_genai()
    except Exception:  # noqa: BLE001 — shutdown never raises
        pass
    with _lock:
        provider, exporter = _provider, _exporter
        _provider = None
        _exporter = None
        _enabled = False
    try:
        if provider is not None:
            provider.shutdown()
    except Exception:  # noqa: BLE001 — shutdown never raises
        pass
    try:
        if exporter is not None:
            exporter.shutdown()
    except Exception:  # noqa: BLE001 — shutdown never raises
        pass


def _load_otel():
    """Lazy (trace_sdk, resources) import, or None when absent.

    Kept lazy so importing this module never requires the SDK:
    a source checkout without the OTel packages still runs, with
    telemetry inert. Returns the SDK trace module + resources
    module on success.
    """
    try:
        from opentelemetry.sdk import resources as _resources
        from opentelemetry.sdk import trace as trace_sdk
        return trace_sdk, _resources
    except Exception:  # noqa: BLE001 — absence is the inert path
        return None


# ---------------------------------------------------------------------------
# The ONLY exporter in shipped code: local file, JSON lines
# ---------------------------------------------------------------------------

def _json_safe(value):
    """Coerce a span attribute value to something json.dumps takes."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


class FileSpanExporter:
    """Span exporter that appends one JSON line per span to a file.

    This is the only exporter class in shipped code. It opens the
    file in append mode per export batch (no handle held across
    calls), flushes, and never raises: a failed write drops that
    batch and logs once per failure instead of touching the
    traced call. The file is LOCAL ONLY — nothing here opens a
    socket, builds a request, or knows any endpoint.
    """

    def __init__(self, path) -> None:
        self._path = pathlib.Path(path)
        self._lock = threading.Lock()
        self._shut_down = False

    @property
    def path(self) -> pathlib.Path:
        return self._path

    def export(self, spans) -> bool:
        """Write each finished span as one JSON line. Never raises."""
        if self._shut_down:
            return False
        lines = []
        try:
            for span in spans:
                ctx = span.get_span_context()
                lines.append(json.dumps({
                    "name": span.name,
                    "trace_id": format(ctx.trace_id, "032x"),
                    "span_id": format(ctx.span_id, "016x"),
                    "parent_span_id": (
                        format(span.parent.span_id, "016x")
                        if span.parent is not None else None),
                    "start_time_unix_nano": span.start_time,
                    "end_time_unix_nano": span.end_time,
                    "duration_ms": (
                        (span.end_time - span.start_time) / 1e6
                        if span.start_time and span.end_time
                        else None),
                    "status": span.status.status_code.name
                    if span.status is not None else None,
                    "attributes": {
                        k: _json_safe(v)
                        for k, v in (span.attributes or {}).items()},
                    "events": [
                        {"name": e.name, "timestamp": e.timestamp,
                         "attributes": {
                             k: _json_safe(v)
                             for k, v in (e.attributes or {}).items()}}
                        for e in (span.events or [])],
                }, sort_keys=True))
        except Exception:  # noqa: BLE001 — exporter never raises
            log.exception("telemetry file export: span serialise failed")
            return False
        if not lines:
            return True
        try:
            with self._lock:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._path, "a", encoding="utf-8") as fh:
                    fh.write("\n".join(lines) + "\n")
                    fh.flush()
            return True
        except Exception:  # noqa: BLE001 — exporter never raises
            log.exception("telemetry file export: write failed")
            return False

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return not self._shut_down

    def shutdown(self) -> None:
        self._shut_down = True


def get_tracer(name: str = _DEFAULT_NAME):
    """The OB-1 tracer, or the no-op tracer when telemetry is off.

    Never raises; when disabled this returns the API's NoOp
    tracer (spans start and end without recording anything).
    """
    try:
        if _enabled and _provider is not None:
            return _provider.get_tracer(name)
        from opentelemetry import trace as trace_api
        return trace_api.get_tracer(name)
    except Exception:  # noqa: BLE001 — telemetry never breaks callers
        return None


# ---------------------------------------------------------------------------
# GenAI semconv attribute names (package when present, literals else)
# ---------------------------------------------------------------------------

def genai_attribute_names() -> dict[str, str]:
    """GenAI semconv attribute names, identical either source."""
    try:
        from opentelemetry.semconv._incubating.attributes import (
            gen_ai_attributes as g)
        return {
            "operation": g.GEN_AI_OPERATION_NAME,
            "provider": g.GEN_AI_SYSTEM
            if hasattr(g, "GEN_AI_SYSTEM") else "gen_ai.system",
            "request_model": g.GEN_AI_REQUEST_MODEL,
            "response_model": g.GEN_AI_RESPONSE_MODEL,
        }
    except Exception:  # noqa: BLE001 — literals are the fallback
        return {
            "operation": "gen_ai.operation.name",
            "provider": "gen_ai.system",
            "request_model": "gen_ai.request.model",
            "response_model": "gen_ai.response.model",
        }


# ---------------------------------------------------------------------------
# Tool spans — the funnel hook surface (see module docstring + handoff)
# ---------------------------------------------------------------------------

def start_tool_span(tool_name: str):
    """Start a tool span. Returns the span, or None when inert.

    The span is STARTED here but must be CLOSED by
    ``finish_tool_span`` with the funnel's returned result: the
    return is the delivery evidence, and nothing before it closes
    the span. Returns None (not a span) when telemetry is off or
    the SDK is absent, so the funnel never branches on it.
    """
    try:
        tracer = get_tracer()
        if tracer is None or not _enabled:
            return None
        span = tracer.start_span(
            f"tool.{tool_name}",
            attributes={"tool.name": tool_name,
                        "delivery.evidence": "pending"})
        return span
    except Exception:  # noqa: BLE001 — telemetry never breaks callers
        return None


def finish_tool_span(span, result) -> None:
    """Close a tool span on the funnel's DELIVERED result.

    ``result`` is the exact dict ``execute()`` returns to its
    caller. ``status == "success"`` records delivery evidence
    "tool result delivered"; any other outcome records the
    delivered outcome ("refusal delivered" /
    "error delivered") — the refusal or error reaching the
    caller IS its delivery. Idempotent: finishing twice, or
    finishing None, is a no-op. Never mutates ``result``.
    """
    if span is None:
        return
    # Idempotency guard FIRST, outside any try/finally: an early
    # return inside try would still run a finally end() and
    # double-end the span (SDK warns on that).
    if getattr(span, "_vyuk_finished", False):
        return
    try:
        span._vyuk_finished = True  # type: ignore[attr-defined]
        outcome = "unknown"
        evidence = "tool result delivered"
        if isinstance(result, dict):
            if result.get("status") == "success":
                outcome = "success"
            else:
                kind = result.get("kind") or ""
                if kind in ("refused", "not_confirmed", "validation"):
                    outcome = kind
                    evidence = "refusal delivered"
                else:
                    outcome = kind or "error"
                    evidence = "error delivered"
        try:
            from opentelemetry.trace import StatusCode
            span.set_status(
                StatusCode.OK if outcome == "success"
                else StatusCode.ERROR)
        except Exception:  # noqa: BLE001 — status is best-effort
            pass
        span.set_attribute("tool.outcome", outcome)
        span.set_attribute("delivery.evidence", evidence)
    except Exception:  # noqa: BLE001 — telemetry never breaks callers
        pass
    try:
        span.end()
    except Exception:  # noqa: BLE001 — end is best-effort
        pass


class tool_span:  # noqa: N801 — funnel-style name, mirrors ToolRegistry
    """Context manager for non-funnel spans (decorators, helpers).

    Delivery evidence on exit: normal exit closes with
    "function returned"; an exception closes with error status
    and "exception delivered". Inert (yields None) when off.
    """

    def __init__(self, name: str, attributes=None) -> None:
        self._name = name
        self._attributes = dict(attributes or {})
        self._span = None

    def __enter__(self):
        try:
            tracer = get_tracer()
            if tracer is None or not _enabled:
                return None
            self._span = tracer.start_span(
                self._name, attributes=dict(self._attributes))
            return self._span
        except Exception:  # noqa: BLE001 — telemetry never breaks callers
            return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._span is None:
            return False
        try:
            if exc_type is not None:
                self._span.record_exception(exc)
                try:
                    from opentelemetry.trace import StatusCode
                    self._span.set_status(StatusCode.ERROR, str(exc))
                except Exception:  # noqa: BLE001 — best-effort
                    pass
                self._span.set_attribute(
                    "delivery.evidence", "exception delivered")
            else:
                self._span.set_attribute(
                    "delivery.evidence", "function returned")
        except Exception:  # noqa: BLE001 — telemetry never breaks callers
            pass
        finally:
            try:
                self._span.end()
            except Exception:  # noqa: BLE001 — best-effort
                pass
        return False


def trace_function(name=None):
    """OB-2 decorator: span a function on OB-1, closing on return.

    The span closes when the wrapped function returns or raises
    — that return/raise is the delivery evidence. When telemetry
    is off the wrapper is a pure pass-through.
    """
    def decorator(fn):
        span_name = name or f"func.{fn.__module__}.{fn.__qualname__}"

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with tool_span(span_name):
                return fn(*args, **kwargs)

        return wrapper
    return decorator


def start_genai_span(operation: str, model: str | None = None,
                     provider: str = "google"):
    """Start a GenAI-semconv span (operation/model only, no content).

    No prompt, completion, or message content is ever attached:
    content capture is OFF for this product (see
    ``instrument_google_genai``). Returns the span or None.
    """
    try:
        names = genai_attribute_names()
        attrs = {names["operation"]: operation,
                 names["provider"]: provider}
        if model:
            attrs[names["request_model"]] = model
        tracer = get_tracer()
        if tracer is None or not _enabled:
            return None
        return tracer.start_span(f"gen_ai.{operation}",
                                 attributes=attrs)
    except Exception:  # noqa: BLE001 — telemetry never breaks callers
        return None


# ---------------------------------------------------------------------------
# OB-2: raw instrumentations only — no wrapper init, no cloud config
# ---------------------------------------------------------------------------

def instrument_google_genai() -> bool:
    """Activate the official GenAI-SDK instrumentation on OB-1.

    Imports ONLY ``opentelemetry.instrumentation.google_genai``
    (the raw instrumentation for the ``google-genai`` SDK the
    product already uses) and instruments it against OB-1's
    provider. Before instrumenting it forces message-content
    capture to NO_CONTENT in-process, so prompts/completions
    never land in spans even if the environment asked for them.
    Returns False unless telemetry is enabled, unless the
    instrumentation package is importable, or on any error.
    There is deliberately NO wrapper-init entry point here:
    Traceloop's ``init()`` default-cloud configuration is
    rejected — raw instrumentations only, on OB-1 only.
    """
    global _genai_instrumentor
    if not _enabled:
        return False
    try:
        os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] \
            = "NO_CONTENT"
        from opentelemetry.instrumentation.google_genai import (
            GoogleGenAiSdkInstrumentor)
        if _genai_instrumentor is not None:
            return True
        instrumentor = GoogleGenAiSdkInstrumentor()
        instrumentor.instrument(skip_dep_check=True)
        _genai_instrumentor = instrumentor
        return True
    except Exception:  # noqa: BLE001 — telemetry never breaks callers
        log.exception("google-genai instrumentation failed")
        return False


def uninstrument_google_genai() -> bool:
    """Undo ``instrument_google_genai``. True when it was active."""
    global _genai_instrumentor
    instrumentor, _genai_instrumentor = _genai_instrumentor, None
    if instrumentor is None:
        return False
    try:
        instrumentor.uninstrument()
        return True
    except Exception:  # noqa: BLE001 — never raises
        return False


def instrumentation_status() -> dict:
    """What OB-2 has activated (for doctor/dev surfaces, not spans)."""
    return {
        "telemetry_enabled": _enabled,
        "span_file": str(span_path()),
        "exporter": "local-file-only",
        "google_genai_instrumented": _genai_instrumentor is not None,
        "content_capture": os.environ.get(
            "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT",
            "unset (upstream default NO_CONTENT)"),
    }


def feed_funnel_result(tool_name: str, result) -> None:
    """Feed one funnel return to the health monitor (NO-33).

    Thin delegation so the funnel hook has ONE call site: the
    health rules (core/health_rules) own the monitor and the
    delivery accounting; this function only forwards. A success
    result counts as a delivered action; anything else counts
    as a delivered non-action (refusal/error), which does NOT
    satisfy a claim that the action happened. Never raises.
    """
    try:
        from . import health_rules as _health
        ok = isinstance(result, dict) \
            and result.get("status") == "success"
        _health.get_monitor().note_delivery(ok)
    except Exception:  # noqa: BLE001 — telemetry never breaks callers
        pass
