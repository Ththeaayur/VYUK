"""The one engine-session interface (GAPS-22, B-V4).

Before this module the four sessions (Gemini, Groq, local,
lite) shared a surface by convention only: ``_bind`` assigned
callbacks onto whatever object ``start()`` built, with no check
that it had the rest of the surface — a session missing
``stop`` or ``is_connected`` failed later, at the call site,
with an AttributeError far from the cause. Engine identity was
a string (``self._engine == "local"``) or a class name
(``type(inner).__name__ == "LiteLiveSession"``) in eight places.

The interface is a ``runtime_checkable`` Protocol plus an
explicit ``check_session_conforms`` — ``_bind`` calls it before
assigning anything, so a non-conforming inner fails fast with
the missing member named. Every session class declares its
engine (an ``Engine`` member, never a string) and the model it
is actually serving (``connected_model`` — see B-V5), and every
session implements ``release_model`` (see B-V6): the local
sessions drop their own model refs through their own
knowledge; cloud sessions no-op (they hold no local model).

``runtime_checkable`` ``isinstance`` only verifies methods, so
the data members (``engine``, ``connected_model``) are checked
explicitly by ``check_session_conforms`` — do not rely on
``isinstance`` alone for those.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable

from ..provider_identity import Engine

__all__ = ["EngineSession", "check_session_conforms",
           "SESSION_SURFACE"]


@runtime_checkable
class EngineSession(Protocol):
    """The surface every brain session must offer.

    Callback attributes are assigned by the proxy's ``_bind``
    (they start as None); ``engine`` is a class attribute on
    each session; ``connected_model`` is None until the session
    connects, then the exact model id / file serving.
    """

    engine: Engine
    connected_model: str | None

    on_status_change: Callable[[str], None] | None
    on_transcript: Callable[[str, str], None] | None
    on_audio_data: Callable[[bytes], None] | None
    on_tool_call: Callable[[str, dict[str, Any], Any], None] | None

    @property
    def is_connected(self) -> bool:
        ...

    async def start(self, audio_manager) -> None:
        ...

    def stop(self) -> None:
        ...

    def release_model(self) -> None:
        """Drop this session's local model refs, if any.

        Only ever called on a never-started session (the warm
        standby discard path). Cloud sessions no-op. Never
        raises.
        """


#: (member, kind) the conformance check verifies. "method" must
#: be callable; "attr" must merely exist (None counts — a session
#: that has not connected yet has ``connected_model`` None).
SESSION_SURFACE: tuple[tuple[str, str], ...] = (
    ("engine", "attr"),
    ("connected_model", "attr"),
    ("is_connected", "attr"),
    ("start", "method"),
    ("stop", "method"),
    ("release_model", "method"),
)


def check_session_conforms(inner: Any) -> None:
    """Raise TypeError naming the first missing surface member.

    Called by the proxy's ``_bind`` before any callback is
    assigned, so a non-conforming inner fails at the bind —
    with the member named — instead of later at a call site.
    """
    name = type(inner).__name__
    for member, kind in SESSION_SURFACE:
        value = getattr(inner, member, None)
        if kind == "method":
            if not callable(value):
                raise TypeError(
                    f"engine session {name!r} has no callable "
                    f"{member}() — it does not conform to "
                    "EngineSession (GAPS-22, B-V4)")
        elif value is None and member == "engine":
            raise TypeError(
                f"engine session {name!r} declares no engine — "
                "it does not conform to EngineSession "
                "(GAPS-22, B-V4)")
        elif not hasattr(inner, member):
            raise TypeError(
                f"engine session {name!r} has no attribute "
                f"{member} — it does not conform to "
                "EngineSession (GAPS-22, B-V4)")
