"""MainWindow brain mixin: brain-engine / local-brain /
API-key / cloud-provider / updates / Google world handlers.

v5.50 (programme item 43): split out of gui/main_window.py,
which remains as the re-export shim. Bodies moved verbatim;
behaviour is unchanged.
"""

# GUI split #4 INTENTIONAL: BrainMixin split verbatim into the
# five part-mixins below; this file keeps the composed class so
# every existing importer (main_window, tests) keeps working
# unchanged. No method name is defined by more than one mixin.
from .window_brain_engine import BrainEngineMixin
from .window_brain_voice import BrainVoiceMixin
from .window_brain_local import BrainLocalMixin
from .window_brain_cloud import BrainCloudMixin
from .window_brain_services import BrainServicesMixin


class BrainMixin(
    BrainEngineMixin,
    BrainVoiceMixin,
    BrainLocalMixin,
    BrainCloudMixin,
    BrainServicesMixin,
):
    """Composed brain mixin (GUI split #4): the five part-mixins
    above, in plan order. Method bodies live verbatim in the
    part modules; this class only composes them.
    """
