"""MainWindow pages mixin: reminders / memory / skills /
pulse / senses page builders.

v5.50 (programme item 43): split out of gui/main_window.py,
which remains as the re-export shim. Bodies moved verbatim;
behaviour is unchanged.
"""

# GUI split #8 INTENTIONAL: PagesMixin split verbatim into the
# three part-mixins below; this file keeps the composed class so
# every existing importer (main_window, tests) keeps working
# unchanged. No method name is defined by more than one mixin.
from .window_pages_activity import PagesActivityMixin
from .window_pages_memory import PagesMemoryMixin
from .window_pages_p5 import PagesP5Mixin
from .window_pages_system import PagesSystemMixin
from .window_pages_views import PagesViewsMixin


class PagesMixin(
    PagesActivityMixin,
    PagesMemoryMixin,
    PagesP5Mixin,
    PagesSystemMixin,
    PagesViewsMixin,
):
    """Composed pages mixin (GUI split #8): the three part-mixins
    above, in plan order. Method bodies live verbatim in the
    part modules; this class only composes them.
    """
