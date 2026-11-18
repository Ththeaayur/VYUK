# Refactor Phase 1 step 5: moved verbatim from gui/memory_web.py (split by family).

try:  # GL is a progressive enhancement — the web must import and
    # run (2D) even on a machine where Qt's GL stack is missing.
    from PySide6.QtOpenGL import (
        QOpenGLBuffer,
        QOpenGLShader,
        QOpenGLShaderProgram,
        QOpenGLVertexArrayObject,
    )
    from PySide6.QtOpenGLWidgets import QOpenGLWidget

    _GL_AVAILABLE = True
except Exception:  # noqa: BLE001
    QOpenGLWidget = None  # type: ignore[assignment]
    _GL_AVAILABLE = False
# Refactor Phase 1 step 5: MemoryWeb3DWidget, reassembled
# from its verbatim method mixins. Class attributes and
# docstring below are verbatim from gui/memory_web.py.
from PySide6.QtCore import Signal  # noqa: F401  (class signals below)
from .memory_web_3d_core import _MemoryWeb3DCoreMixin
from .memory_web_3d_render import _MemoryWeb3DRenderMixin


if _GL_AVAILABLE:
    class MemoryWeb3DWidget(_MemoryWeb3DCoreMixin,
                            _MemoryWeb3DRenderMixin,
                            QOpenGLWidget):
        """The memory web in 3D: lit spheres and threads of light,
        an orbit camera with a slow idle drift, and the same graph,
        folds, search dimming and selection contract as the 2D
        canvas (the container swaps the two freely)."""
        fact_selected = Signal(object)       # the store row (dict)
        selection_cleared = Signal()
        SETTLE_EPSILON = 0.10
        MAX_TICKS = 40
        STEPS_PER_TICK = 12
        TOUR_STOP_SECONDS = 2.6
        DEFAULT_DISTANCE = 780.0
        MIN_DISTANCE = 240.0
        MAX_DISTANCE = 1900.0
        FOV_DEGREES = 42.0
        ROTATE_STEP = 0.00017      # rad per 16 ms tick ≈ 0.6°/s
        IDLE_BEFORE_ROTATE = 4.0   # seconds of stillness first

