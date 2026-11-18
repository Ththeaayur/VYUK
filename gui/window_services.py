"""MainWindow services mixin: timers, background services,
confirm-gate and plugin-trust plumbing, failsafe, audio-device
and speech-transport handlers.

v5.50 (programme item 43): split out of gui/main_window.py,
which remains as the re-export shim. Bodies moved verbatim;
behaviour is unchanged.
"""
from .window_services_confirm import (
    ServicesConfirmMixin,
    _CONFIRM_TARGET_KEYS,  # GUI split #10 batch 2 INTENTIONAL: re-exported so the facade preserves every top-level name the
    # old module defined (no importer addresses them through
    # window_services today — grep-proven — but completeness
    # requires them).
    _confirm_headline,
    _confirm_summary_value,
    _confirm_target_phrase,
    _mic_health_presentation,  # GUI split #10 batch 2 INTENTIONAL: re-export preserved; the speech module imports this helper
    # separately for _refresh_mic_health.
)
from .window_services_trust import ServicesTrustMixin
from .window_services_audio import ServicesAudioMixin
from .window_services_speech import ServicesSpeechMixin


class ServicesMixin(
    ServicesConfirmMixin,
    ServicesTrustMixin,
    ServicesAudioMixin,
    ServicesSpeechMixin,
):
    """Composed services mixin (GUI split #10): the four part-mixins
    above, in plan order. Method bodies live verbatim in the
    part modules; this class only composes them.
    """
