"""Ollama model management (R11-1, ollama port).

The tree ALREADY has an Ollama backend — this module does not add
one. What existed before this batch (all read on the backup first):

  * core/voice/local_client_ollama.py (LocalLiveSessionOllamaMixin,
    406 lines) + core/voice/local_client_lifecycle.py — the local
    brain talks to an external Ollama server over stdlib
    ``urllib.request`` only (``/api/chat`` for turns, ``/api/tags``
    for the reachability gate). No ``ollama`` PyPI client anywhere:
    not declared in requirements.txt, not installed on either lab
    interpreter.
  * The offline story EXISTS at the session level:
    ``_check_ollama`` GETs ``/api/tags`` and on ANY failure emits
    "ERROR: Local brain offline — is Ollama running?" and start()
    returns without a session (mirroring the MISSING_API_KEY path).
  * The GUI already switches the model (gui/window_brain_local.py
    ``_save_ollama_model``), picks the backend
    (gui/window_brain_engine.py) and checks reachability
    (gui/window_brain_voice.py: "Can't reach Ollama — make sure
    it's installed and running (from ollama.com), then check
    again.").
  * requirements.txt:272-275 documents the operator flow: install
    the Ollama app yourself and ``ollama pull qwen2.5:7b``.

What was genuinely missing — the remainder this module builds,
and only this remainder:

  * Model PULL. The tree's own reachability gate prints
    "Pull it with: ollama pull <model>" when the configured model
    is not installed — and then cannot do it. ``ollama_pull``
    performs that pull (POST /api/pull, stream:false).
  * Model LIST as a user-facing surface. ``_fetch_tags`` exists
    session-internally; no funnel tool answered "what models do I
    have?" ``ollama_status`` answers it (version + installed
    models + whether the configured model is among them).
  * Model DELETE. Without it models accumulate on disk with no
    way back. ``ollama_delete`` removes one (DELETE /api/delete).

What is NOT built here, and why:

  * The Ollama server itself. It is a Go binary, not a Python
    dependency — there is nothing to "adopt" into the tree. The
    operator installs it (the GUI already points at ollama.com).
  * The ``ollama`` PyPI client (0.6.3, 16,603 B wheel). It would
    drag in ``pydantic>=2.9`` (declared NOWHERE — the R9-10/R10-2
    reason) and ``httpx>=0.27`` for nothing: the tree already
    speaks the REST API directly over stdlib urllib, exactly like
    the existing backend does. Nothing adopted, no new dependency.
  * Push (POST /api/push). Uploading a model needs an ollama.ai
    account and a public key first — out of scope.
  * Model SWITCHING as a funnel tool. The GUI already has it
    (Settings -> Brain -> Local model); the pull answer names
    that path. A voice tool mutating the brain's configured model
    is a bigger trust step than this batch takes.
  * Bundled models. No model ships: a 7B model is gigabytes, and
    the model the user pulls is the operator's choice.

Trust discipline (stated plainly, same shape as the existing
backend):

  * Loopback only in practice: every call goes to
    ``config.ollama_host`` (default http://127.0.0.1:11434) — the
    operator's own setting, the same host the local brain uses.
    VYUK never talks to the Ollama registry itself: the DOWNLOAD
    is performed by the Ollama server from its library; VYUK only
    POSTs the model name to the loopback server and reads back a
    status string. No model bytes ever pass through VYUK.
  * No taint entry: the answers are model-inventory metadata from
    the operator's own local server, never web content. No
    _LOCAL_ONLY_READS entry either: the status read DOES do
    loopback HTTP, so it is an egress read by the file's own test
    (``name in _READ_ONLY_TOOLS and name not in
    _LOCAL_ONLY_READS``) — and the egress concept is consulted
    only under taint, so untainted behaviour is unchanged.
  * ``ollama_pull`` and ``ollama_delete`` are confirm-gated: a
    pull downloads gigabytes from the model library, a delete
    destroys gigabytes. The confirm ask precedes the handler.
  * ``ollama_pull`` takes the "long" deadline class (900 s): a
    7B pull legitimately runs minutes. A pull that does not finish
    inside it is reported honestly — cancelled pulls resume where
    they left off (per the Ollama API docs), so the user can ask
    again to continue it.
  * Verification is A5-sysreadback, the same shape as the rustic
    backup tools: a pull is only claimed when the model appears
    in a fresh ``/api/tags`` list afterwards; a delete is only
    claimed when the model is absent from a fresh list.
  * Deleting the CONFIGURED model (config.ollama_model) is
    refused outright: that is the local brain's model, and
    deleting it by voice would break the next brain start. Switch
    first in Settings -> Brain.
  * Fail-closed model names: ``model:tag`` with an optional
    ``namespace/`` — letters, digits, ``.``, ``-``, ``_``, ``/``,
    ``:`` only, 200 chars max. Anything else is refused before
    any network call.
  * The field leg is still owed: the local brain has never
    completed a real task on his Windows machine, so no offline
    PRO-tier claim is made here — these tools manage models, they
    do not promise the brain works.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from .config import config

_STATUS_TIMEOUT_S = 10
_PULL_TIMEOUT_S = 900  # multi-GB download; matches the "long" deadline class
_DELETE_TIMEOUT_S = 30

_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]*(:[A-Za-z0-9._\-]+)?$")
_MAX_MODEL_NAME_LEN = 200

_OFFLINE_MESSAGE = "Local brain offline — is Ollama running?"


class _OllamaOffline(Exception):
    """The Ollama server did not answer (down, wrong host, timeout)."""


class _OllamaError(Exception):
    """The server answered with an HTTP error (its own error text)."""


def _http_error_message(e: urllib.error.HTTPError) -> str:
    """'HTTP 404: model not found' — the server's own error text."""
    try:
        body = json.loads(e.read().decode("utf-8"))
        detail = str(body.get("error", "") or "")
    except Exception:  # noqa: BLE001 — an unreadable error body is just a code
        detail = ""
    return f"HTTP {e.code}" + (f": {detail}" if detail else "")


def _host() -> str:
    """The Ollama server base URL, same host the local brain uses."""
    return str(getattr(config, "ollama_host", "http://127.0.0.1:11434") or "http://127.0.0.1:11434").rstrip("/")


def _validate_model_name(model: object) -> str | None:
    """Return an error string for a bad model name, else None."""
    if not isinstance(model, str) or not model.strip():
        return "Give me a model name, e.g. 'qwen2.5:7b'."
    name = model.strip()
    if len(name) > _MAX_MODEL_NAME_LEN:
        return f"That model name is {len(name)} characters — the limit is {_MAX_MODEL_NAME_LEN}."
    if not _MODEL_NAME_RE.match(name):
        return (
            f"'{name}' is not a model name (letters, digits, '.', '-', '_', '/' and one ':tag' only, "
            "e.g. 'qwen2.5:7b' or 'namespace/model:tag')."
        )
    return None


def _get_json(path: str, timeout: int) -> dict:
    """GET one JSON object from the local Ollama server.

    Raises _OllamaOffline when the server does not answer,
    _OllamaError when it answers with an HTTP error (a 404 is the
    server saying "no such model", not the server being down),
    and ValueError when the answer is not JSON. Never raises any
    other network error: the caller turns these into honest answers.
    """
    req = urllib.request.Request(f"{_host()}{path}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise _OllamaError(_http_error_message(e)) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise _OllamaOffline(str(e)) from e
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"Ollama answered {path} with something that is not JSON: {e}") from e


def _post_json(path: str, payload: dict, timeout: int) -> dict:
    """POST one JSON body to the local Ollama server, return the JSON answer."""
    req = urllib.request.Request(
        f"{_host()}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise _OllamaError(_http_error_message(e)) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise _OllamaOffline(str(e)) from e
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"Ollama answered {path} with something that is not JSON: {e}") from e


def _delete_json(path: str, payload: dict, timeout: int) -> int:
    """DELETE with a JSON body; returns the HTTP status code.

    Raises _OllamaOffline when the server does not answer. A 404
    is returned as the code (the model is not installed), not
    raised — the caller answers it honestly.
    """
    req = urllib.request.Request(
        f"{_host()}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
            return int(resp.status)
    except urllib.error.HTTPError as e:
        return int(e.code)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise _OllamaOffline(str(e)) from e


def _human_size(size: object) -> str:
    """Bytes -> '4.7 GB' / '274 MB' / '512 B' (one decimal, plain)."""
    try:
        n = int(size)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "unknown size"
    if n < 0:
        return "unknown size"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(n)} B"
            return f"{n:.1f} {unit}"
        n = n / 1024
    return f"{n:.1f} TB"


def _list_models() -> list[dict]:
    """The installed models from GET /api/tags (names + sizes)."""
    tags = _get_json("/api/tags", _STATUS_TIMEOUT_S)
    models = tags.get("models", [])
    if not isinstance(models, list):
        return []
    return [m for m in models if isinstance(m, dict) and isinstance(m.get("name"), str)]


def _model_names(models: list[dict]) -> list[str]:
    return [str(m.get("name", "")) for m in models]


def ollama_status() -> str:
    """Read-only: is Ollama running, which models are installed, and
    is the configured brain model among them?"""
    try:
        version = _get_json("/api/version", _STATUS_TIMEOUT_S).get("version", "")
    except _OllamaOffline:
        return (
            f"{_OFFLINE_MESSAGE} (could not reach {_host()}/api/version — "
            "make sure Ollama is installed and running, from ollama.com, then check again.)"
        )
    except _OllamaError as e:
        return f"Ollama answered /api/version with an error: {e}"
    except ValueError as e:
        return f"Ollama answered, but not with JSON I can read: {e}"
    try:
        models = _list_models()
    except _OllamaOffline:
        return (
            f"{_OFFLINE_MESSAGE} (the version answered but /api/tags did not — "
            "is Ollama still starting up?)"
        )
    except _OllamaError as e:
        return f"Ollama answered /api/tags with an error: {e}"
    except ValueError as e:
        return f"Ollama answered, but not with JSON I can read: {e}"
    configured = str(getattr(config, "ollama_model", "") or "")
    names = _model_names(models)
    total = sum(int(m.get("size", 0)) for m in models if isinstance(m.get("size"), int))
    lines = [f"Ollama {version} at {_host()} — {len(models)} model{'s' if len(models) != 1 else ''} installed ({_human_size(total)} total)."]
    if configured:
        if configured in names:
            lines.append(f"Configured brain model: {configured} — installed.")
        else:
            lines.append(
                f"Configured brain model: {configured} — NOT installed. "
                f"Pull it with ollama_pull ('{configured}'), or pick another in Settings -> Brain."
            )
    if not models:
        lines.append("No models installed yet — pull one with ollama_pull, e.g. 'qwen2.5:7b'.")
    else:
        lines.append("Models:")
        for m in models:
            modified = str(m.get("modified_at", "") or "")[:10]
            when = f" (modified {modified})" if modified else ""
            lines.append(f"- {m.get('name')} — {_human_size(m.get('size'))}{when}")
    return "\n".join(lines)


def ollama_pull(model: str = "") -> str:
    """Confirm-gated: pull a model from the Ollama library.

    The download itself is performed by the Ollama server from its
    library; VYUK only POSTs the name to the loopback server. A
    pull is only claimed when the model appears in a fresh
    /api/tags list afterwards (A5-sysreadback).
    """
    error = _validate_model_name(model)
    if error is not None:
        return error
    name = str(model).strip()
    try:
        models = _list_models()
    except _OllamaOffline:
        return (
            f"{_OFFLINE_MESSAGE} (could not reach {_host()}/api/tags — "
            "make sure Ollama is installed and running, from ollama.com, then check again.)"
        )
    except _OllamaError as e:
        return f"Ollama answered /api/tags with an error: {e}"
    except ValueError as e:
        return f"Ollama answered, but not with JSON I can read: {e}"
    if name in _model_names(models):
        return f"'{name}' is already installed — nothing to pull."
    try:
        result = _post_json("/api/pull", {"model": name, "stream": False}, _PULL_TIMEOUT_S)
    except _OllamaOffline:
        return (
            f"The pull of '{name}' did not finish — Ollama stopped answering mid-pull. "
            "Cancelled pulls resume where they left off, so ask again to continue it."
        )
    except _OllamaError as e:
        return f"The pull of '{name}' failed: {e} — check the name exists in the Ollama library."
    except ValueError as e:
        return f"Ollama answered the pull, but not with JSON I can read: {e}"
    status = str(result.get("status", ""))
    if status != "success":
        detail = str(result.get("error", "") or status or "no status returned")
        return f"The pull of '{name}' failed: {detail} — check the name exists in the Ollama library."
    try:
        models = _list_models()
    except (_OllamaOffline, _OllamaError, ValueError):
        return (
            f"Ollama said the pull of '{name}' succeeded, but I could not re-list the models to "
            "confirm it — check with ollama_status before using it."
        )
    names = _model_names(models)
    if name not in names:
        return (
            f"Ollama said the pull of '{name}' succeeded, but '{name}' is not in the model list "
            "afterwards — I am not claiming it. Check with ollama_status."
        )
    size = next((m.get("size") for m in models if m.get("name") == name), None)
    return (
        f"Pulled '{name}' ({_human_size(size)}) — it is now in Ollama's model list. "
        "To use it as the local brain, switch to it in Settings -> Brain -> Local model."
    )


def ollama_delete(model: str = "") -> str:
    """Confirm-gated: delete a model and its data.

    Refuses the configured brain model outright. A delete is only
    claimed when the model is absent from a fresh /api/tags list
    afterwards (A5-sysreadback).
    """
    error = _validate_model_name(model)
    if error is not None:
        return error
    name = str(model).strip()
    configured = str(getattr(config, "ollama_model", "") or "")
    if configured and name == configured:
        return (
            f"'{name}' is the local brain's configured model — I will not delete it by voice. "
            "Switch to another model in Settings -> Brain first, then delete this one."
        )
    try:
        models = _list_models()
    except _OllamaOffline:
        return (
            f"{_OFFLINE_MESSAGE} (could not reach {_host()}/api/tags — "
            "make sure Ollama is installed and running, from ollama.com, then check again.)"
        )
    except _OllamaError as e:
        return f"Ollama answered /api/tags with an error: {e}"
    except ValueError as e:
        return f"Ollama answered, but not with JSON I can read: {e}"
    names = _model_names(models)
    if name not in names:
        return f"'{name}' is not installed — nothing to delete."
    size = next((m.get("size") for m in models if m.get("name") == name), None)
    try:
        code = _delete_json("/api/delete", {"model": name}, _DELETE_TIMEOUT_S)
    except _OllamaOffline:
        return (
            f"The delete of '{name}' did not finish — Ollama stopped answering. "
            "Check with ollama_status whether it is gone."
        )
    if code == 404:
        return f"'{name}' is not installed — nothing to delete."
    if code != 200:
        return f"The delete of '{name}' failed (Ollama answered HTTP {code}) — it may still be installed."
    try:
        models = _list_models()
    except (_OllamaOffline, _OllamaError, ValueError):
        return (
            f"Ollama said the delete of '{name}' succeeded, but I could not re-list the models to "
            "confirm it — check with ollama_status before assuming it is gone."
        )
    if name in _model_names(models):
        return (
            f"Ollama said the delete of '{name}' succeeded, but '{name}' is still in the model list "
            "afterwards — I am not claiming it. Check with ollama_status."
        )
    return f"Deleted '{name}' ({_human_size(size)} freed) — it is no longer in Ollama's model list."


def register_all(registry) -> None:
    """Register the ollama pack (R11-1, ollama port): ollama_status
    (read-only), ollama_pull + ollama_delete (confirm-gated). Own
    stdlib code over the Ollama REST API; nothing adopted, no new
    dependency — no Go server, no ``ollama`` PyPI client (it would
    drag pydantic + httpx for nothing the tree's own urllib calls
    do not already do). The module owns its catalogue via
    register_all (same pattern as the other packs)."""
    registry.register(
        name="ollama_status",
        description="Check the local Ollama server: is it running, which version, which models are installed and how big they are, and is the local brain's configured model among them? Read-only — nothing is downloaded or deleted. Use when the user asks what AI models they have, whether Ollama is running, or whether a model is ready to use.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=ollama_status,
    )
    registry.register(
        name="ollama_pull",
        description="Download a model into the local Ollama server, e.g. 'qwen2.5:7b'. The download is done by the Ollama server from its model library — VYUK only asks the loopback server to pull it, no model bytes pass through VYUK. A pull downloads gigabytes and takes minutes; if it does not finish in time it resumes where it left off, so ask again to continue it. Asks first, every time. To use the pulled model as the local brain, switch to it in Settings -> Brain -> Local model.",
        parameters={
            "type": "object",
            "properties": {
                "model": {"type": "string",
                          "description": "The model to pull, e.g. 'qwen2.5:7b' or 'llama3.2:3b'"},
            },
            "required": ["model"],
        },
        handler=ollama_pull,
    )
    registry.register(
        name="ollama_delete",
        description="Delete a model and its data from the local Ollama server, freeing its disk space. Asks first, every time. Refuses the local brain's configured model — switch to another model in Settings -> Brain first. Use when the user asks to remove a model they no longer use.",
        parameters={
            "type": "object",
            "properties": {
                "model": {"type": "string",
                          "description": "The installed model to delete, e.g. 'llama3:13b'"},
            },
            "required": ["model"],
        },
        handler=ollama_delete,
    )
