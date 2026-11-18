"""MainWindow session mixin: status/caption, reconnect,
learning + goal pill, suggestions, wake word, connection
lifecycle, push-to-talk/mute, watch mode and briefing playback.

v5.50 (programme item 43): split out of gui/main_window.py,
which remains as the re-export shim. Bodies moved verbatim;
behaviour is unchanged.
"""

from .window_session_status import (
    SessionStatusMixin,
    _WATCHDOG_DIAGNOSTIC_AFTER,
    _WATCHDOG_DIAGNOSTIC_TEXT,
)  # GUI split #11 batch 1 INTENTIONAL: watchdog constants moved with
# _on_status into the status module; re-exported here so the facade
# still exposes every old module name.
from .window_session_learning import SessionLearningMixin
from .window_session_suggestions import SessionSuggestionsMixin
from .window_session_connection import SessionConnectionMixin
from .window_session_typed import (
    SessionTypedMixin,
    _log,
    _FRAME_DELIVERY_WAIT_S,
    _await_frame_delivery,
)  # GUI split #11 batch 2 INTENTIONAL: _log + the frame-delivery
# helper moved with the typed methods that use them; re-exported here
# so the facade still exposes every old module name.
from .window_session_watch import SessionWatchMixin


class SessionMixin(
    SessionStatusMixin,
    SessionLearningMixin,
    SessionSuggestionsMixin,
    SessionConnectionMixin,
    SessionTypedMixin,
    SessionWatchMixin,
):
    """Composed session mixin (GUI split #11): the six part-mixins
    above, in plan order. Method bodies live verbatim in the
    part modules; this class only composes them.
    """
