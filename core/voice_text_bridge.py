"""Shim — the voice→text bridge moved to core/voice/ (GAPS-25, V-8).

The canonical home is now ``core.voice.voice_text_bridge``; this
module exists only so older imports (and anything outside the tree
that imported ``core.voice_text_bridge``) keep working. It re-exports
every public name of the canonical module and nothing else — the
bridge itself (OFF by default, engine-based refusal, the
VOICE_FREEFORM_COMPANION_GATE) is unchanged; only its package
moved, per the GAPS-23 GUI-shim precedent.
"""

from .voice.voice_text_bridge import *  # noqa: F401,F403
from .voice.voice_text_bridge import __all__
