"""MainWindow memory mixin: voice picker tail, memory page
handlers, learned facts, reminders/home, presence, watchers,
routines/plans and closeEvent.

v5.50 (programme item 43): split out of gui/main_window.py,
which remains as the re-export shim. Bodies moved verbatim;
behaviour is unchanged.
"""


# GUI split #12 INTENTIONAL: MemoryMixin split verbatim into the
# six part-mixins below; this file keeps the composed class so
# every existing importer (main_window, tests) keeps working
# unchanged. No method name is defined by more than one mixin.
from .window_memory_web import MemoryWebMixin
from .window_memory_learned import MemoryLearnedMixin
from .window_memory_presence import MemoryPresenceMixin
from .window_memory_watchers import MemoryWatchersMixin
from .window_memory_routines import MemoryRoutinesMixin
from .window_memory_activity import MemoryActivityMixin


class MemoryMixin(
    MemoryWebMixin,
    MemoryLearnedMixin,
    MemoryPresenceMixin,
    MemoryWatchersMixin,
    MemoryRoutinesMixin,
    MemoryActivityMixin,
):
    """Composed memory mixin (GUI split #12): the six part-mixins
    above, in plan order. Method bodies live verbatim in the
    part modules; this class only composes them.
    """
