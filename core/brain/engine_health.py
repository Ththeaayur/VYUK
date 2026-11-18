"""Engine health / ``can_serve`` (GAPS-22, B-V10).

Before this module there was no engine-health concept at all:
an unservable model was discovered by failing the connect —
a missing Gemini/Groq key produced a start attempt, a status
error, and only then the local fallback. ``can_serve`` answers
"can this engine serve right now?" WITHOUT connecting, from
facts knowable before any session exists:

- local / builtin: the brain GGUF is on disk (``brain_ready``).
  No model yet is not an error — the lite session serves while
  the pack downloads — so local is always servable, and the
  answer says whether the full model or the lite session will
  serve (``serves``: "model" | "lite").
- local / ollama: the Ollama host is configured. Server
  reachability is NOT probed here — a probe is a connect of
  its own, and this check must never connect; the answer says
  so plainly (``checked`` names what was actually verified).
- gemini / groq: the provider's own key is present.

``can_serve`` is pure over injected inputs (like
``model_manager`` and ``query_routing`` take theirs as
arguments) so tests pin it without config or disk.
``can_serve_configured`` is the thin wrapper the proxy calls:
it reads the config + assets and passes them in. The proxy's
start() pre-flights the requested cloud engine and, when it
cannot serve, goes straight to local with the honest reason —
the same fallback outcome as a failed connect, without the
doomed attempt. A pre-flight "ok" is not a guarantee: the
connect can still fail, and the existing fallback still
catches that.
"""

from __future__ import annotations

from ..provider_identity import Engine

__all__ = ["can_serve", "can_serve_configured"]


def can_serve(engine, *, local_backend: str | None = None,
              local_model_ready: bool | None = None,
              ollama_host: str | None = None,
              gemini_key_present: bool | None = None,
              groq_key_present: bool | None = None) -> dict:
    """Can ``engine`` serve right now, without connecting?

    Returns {"ok", "reason", "engine", "serves", "checked"}.
    ``serves`` is "model" (full engine), "lite" (local with no
    model yet — the lite session serves), or None (cannot serve).
    ``checked`` names exactly what was verified, so a caller can
    see what this answer does and does not cover. Never raises
    on bad input — an unrecognized engine reads as unservable.
    """
    try:
        engine = Engine(engine)
    except Exception:  # noqa: BLE001 — unknown engine: unservable
        return {"ok": False,
                "reason": f"unknown engine {engine!r} — nothing can serve it",
                "engine": None, "serves": None,
                "checked": ["engine identity"]}
    if engine is Engine.LOCAL:
        backend = str(local_backend or "builtin").strip().lower()
        if backend == "ollama":
            host = str(ollama_host or "").strip()
            if host:
                return {"ok": True,
                        "reason": ("local engine (ollama backend): host "
                                   f"{host} is configured; server "
                                   "reachability is checked at connect, "
                                   "not here"),
                        "engine": engine, "serves": "model",
                        "checked": ["ollama host configured"]}
            return {"ok": False,
                    "reason": ("local engine (ollama backend): no "
                               "Ollama host is configured"),
                    "engine": engine, "serves": None,
                    "checked": ["ollama host configured"]}
        if local_model_ready:
            return {"ok": True,
                    "reason": "local engine: brain model is on disk",
                    "engine": engine, "serves": "model",
                    "checked": ["brain model present"]}
        return {"ok": True,
                "reason": ("local engine: brain model is not downloaded "
                           "yet — the lite session serves while the "
                           "pack downloads"),
                "engine": engine, "serves": "lite",
                "checked": ["brain model present"]}
    if engine is Engine.GEMINI:
        if gemini_key_present:
            return {"ok": True,
                    "reason": "gemini engine: API key is present",
                    "engine": engine, "serves": "model",
                    "checked": ["gemini key present"]}
        return {"ok": False,
                "reason": "gemini engine: no API key is configured",
                "engine": engine, "serves": None,
                "checked": ["gemini key present"]}
    if groq_key_present:
        return {"ok": True,
                "reason": "groq engine: API key is present",
                "engine": engine, "serves": "model",
                "checked": ["groq key present"]}
    return {"ok": False,
            "reason": "groq engine: no API key is configured",
            "engine": engine, "serves": None,
            "checked": ["groq key present"]}


def can_serve_configured(engine) -> dict:
    """``can_serve`` with the inputs read from config + assets.

    Best-effort: any unreadable input reads as absent (a key
    that cannot be read is treated as missing, never as present).
    """
    try:
        from ..config import config as _config
    except Exception:  # noqa: BLE001 — config unreadable: nothing serves
        return {"ok": False,
                "reason": "engine health: config is unreadable",
                "engine": None, "serves": None,
                "checked": ["config readable"]}
    try:
        from ..voice import local_assets as _assets
        ready = bool(_assets.brain_ready())
    except Exception:  # noqa: BLE001 — assets unreadable: no model
        ready = False
    return can_serve(
        engine,
        local_backend=getattr(_config, "local_backend", "builtin"),
        local_model_ready=ready,
        ollama_host=getattr(_config, "ollama_host", ""),
        gemini_key_present=bool(
            str(getattr(_config, "api_key", "") or "").strip()),
        groq_key_present=bool(
            str(getattr(_config, "groq_api_key", "") or "").strip()),
    )
