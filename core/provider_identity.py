"""Explicit provider identity (Phase 1, C09).

Before this module, provider identity was inferred ad hoc in three
places that could disagree silently:

- ``gui/window_brain.py`` ``_brain_engine`` normalized anything not
  ``gemini``/``local`` to ``gemini``;
- ``core/brain/brain.py`` ``start()`` picked the cloud provider from
  ``config.cloud_provider`` with its own inline ``groq``-else-``gemini``
  rule;
- ``core/config.py`` ``selected_provider()`` (Phase 0, C01) resolved
  the same question a third way.

This module is the single pure resolver. It imports nothing from
the package (no import cycles, no Qt, no config object): callers
pass the two raw config strings and get back one explicit answer.

DISPATCH IS UNCHANGED. An unrecognized cloud-provider value still
resolves to ``gemini`` — that is the pre-existing behaviour of both
``brain.py`` and ``config.selected_provider()``. What changes is
that the normalization is now VISIBLE: ``normalized`` is True
whenever the raw value was not itself a recognized provider, so
callers (and the GUI) can say so instead of silently presenting a
default as if it were the pick. A test pins that this resolver
agrees with ``config.selected_provider()`` across the input matrix,
so the two can never drift apart unnoticed again.

GAPS-22 (B-V3): engine identity is a type, not a bare string.
``Engine`` is a ``str`` enum, so every existing ``== "local"``
comparison and JSON dump keeps working unchanged — but dispatch
and the session surface now compare against ``Engine`` members,
and no caller identifies an engine by a class name.
"""

import enum


class Engine(str, enum.Enum):
    """The one engine identity (GAPS-22, B-V3).

    A ``str`` enum on purpose: ``Engine.GEMINI == "gemini"`` and
    ``json.dumps(Engine.GEMINI)`` both behave exactly like the
    plain strings this module used to return, so every existing
    reader (provider_status, the GUI, the config agreement test)
    is unaffected — while new code dispatches on the type, and
    the four session classes declare which engine they serve
    (see ``core/brain/engine_session.py``).
    """

    GEMINI = "gemini"
    GROQ = "groq"
    LOCAL = "local"

    def __str__(self) -> str:
        # A plain str-Enum str()s as "Engine.GEMINI" — which would
        # break every str(_engine) / f"{provider}" / .lower()
        # consumer (notably the GUI's engine reads). The value
        # is the identity, so str() is the value.
        return self.value


GEMINI = Engine.GEMINI
GROQ = Engine.GROQ
LOCAL = Engine.LOCAL

#: Every provider the resolver can return. Anything else in config
#: is a misspelling or a stale value, never a fourth provider.
PROVIDERS = (GEMINI, GROQ, LOCAL)


def _clean(value) -> str:
    return str(value or "").strip().lower()


def resolve_provider(engine, cloud_provider) -> dict:
    """Resolve (engine, cloud_provider) to one explicit provider.

    Returns a dict with:

    - ``provider``: an ``Engine`` member (``LOCAL`` | ``GROQ`` |
      ``GEMINI``) — a ``str`` enum, so ``== "local"`` and JSON
      dumps behave exactly as before.
    - ``normalized``: True when the raw inputs did not themselves
      name the returned provider (an unrecognized cloud value fell
      back to gemini, or a non-local engine value rode the cloud
      path). False when the inputs named the provider directly.
    - ``raw_engine`` / ``raw_cloud_provider``: the cleaned inputs,
      so a diagnostic can name what was actually configured.
    - ``requested_cloud``: True when the engine asked for a cloud
      brain at all (engine != local), regardless of which one.
    """
    raw_engine = _clean(engine)
    raw_cloud = _clean(cloud_provider)
    if raw_engine == LOCAL:
        return {
            "provider": LOCAL,
            "normalized": False,
            "raw_engine": raw_engine,
            "raw_cloud_provider": raw_cloud,
            "requested_cloud": False,
        }
    if raw_cloud == GROQ:
        provider, normalized = GROQ, False
    elif raw_cloud == GEMINI:
        provider, normalized = GEMINI, False
    else:
        # Unrecognized cloud value: dispatch keeps serving gemini
        # (the pre-existing rule), but the caller must be able to
        # see that a default was substituted for a bad value.
        provider, normalized = GEMINI, True
    return {
        "provider": provider,
        "normalized": normalized,
        "raw_engine": raw_engine,
        "raw_cloud_provider": raw_cloud,
        "requested_cloud": True,
    }
