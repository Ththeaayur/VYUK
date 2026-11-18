"""Stored-crop grid bytes for the C7 approval-dialog crop display.

C7 ("Verify the spot, not the snapshot") has two halves: the
verifier's region compare (core/desktop/control_existence.py)
and the approval dialog showing the STORED crop — the pixels
the spot was read from. This module is the stdlib-only half of
the dialog slot: the exact byte conversion for a stored-crop
grid (rows of ints, the verifier's grid shape). It lives here,
not in the Qt dialog module, so the exactness contract is
testable on interpreters without PySide6.

Contract (pinned by tests/test_c7_dialog_crop.py): bytes equal
the grid values exactly — no scaling, no clamping, no
truncation, no substitution. Anything unusable returns None,
and the dialog then renders no image region at all — never a
placeholder, never a reconstructed image.
"""

_C7_CROP_MAX_SIDE = 360


def stored_crop_grayscale_bytes(grid):
    """Exact bytes for a stored-crop grid, or None if unusable.

    Returns (flat_bytes, width, height) for rows of ints 0-255,
    non-empty, rectangular. Anything else — None, empty,
    ragged, out-of-range, float, bool, non-list rows — returns
    None. The caller (the Qt dialog) renders no image region
    on None.
    """
    if not isinstance(grid, (list, tuple)) or not grid:
        return None
    rows = []
    for row in grid:
        if not isinstance(row, (list, tuple)) or not row:
            return None
        for v in row:
            if (isinstance(v, bool) or not isinstance(v, int)
                    or v < 0 or v > 255):
                return None
        rows.append(list(row))
    width = len(rows[0])
    if any(len(row) != width for row in rows):
        return None
    flat = bytes(v for row in rows for v in row)
    return flat, width, len(rows)
