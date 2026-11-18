"""
Vision Engine for Voice AI Assistant.
Provides screen capture and webcam video capture for multimodal Gemini input.
"""

# INTENTIONAL (skills split B16): facade — every name below is a
# re-export of the identical object from its owning impl module, so
# module-attribute importers and patch seams keep working unchanged.
from .vision_capture import (  # noqa: F401
    GROUNDING_MAX_DIM,
    Image,
    ImageGrab,
    _MARKS_OCR_THRESHOLD,
    _backend_raw_full_frame,
    _capture_block_reason,
    _cursor_position,
    _fit_within,
    _grab_image,
    _ocr_words_for_image,
    _record_virtual_bounds,
    _routed_backend,
    _virtual_screen_bounds,
    _w5_note_capture,
    draw_marks_on_image,
    foreground_window_rect,
    ground_marks_for_frame,
    io,
)
from .vision_manager import (  # noqa: F401
    HAS_OPENCV,
    VisionManager,
    capture_geometry_line,
    capture_native_image,
    last_capture_info,
    last_marks,
    last_sent_frame,
    marks_table_text,
    vision_manager,
)
# INTENTIONAL (skills split B16): the old module bound cv2 only when
# the import succeeded — mirror that so the dir diff is empty on
# machines with and without cv2.
try:
    from .vision_manager import cv2  # noqa: F401
except ImportError:
    pass
