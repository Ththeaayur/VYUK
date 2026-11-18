"""Minimal byte-mode QR encoder (own code, R7-5 kovrel pattern).

The companion server needs a QR code for pairing (scan the code on
the laptop screen, type the PIN on the phone). No QR *encoder* exists
in the tree — only a *decoder* (core/skills_web6_local.py, OpenCV,
read-only) — and no qrcode/segno package is importable in either lab
interpreter, so this module is VYUK's own implementation of the
ISO/IEC 18004 byte-mode encoder, restricted to what pairing needs:

- byte mode only (the pairing URL is ASCII; UTF-8 bytes otherwise),
- error-correction level M (the middle level — survives a smudged
  print without bloating a short URL),
- versions 1-10 (a pairing URL is ~40 chars; version 4-M already
  holds 64 bytes, so versions 5-10 are headroom, not a promise),
- mask 0 only (fixed; the spec allows any mask, and 0 is one of
  them — no penalty scoring, no mask search).

Verification (not self-verification): the module is checked
byte-for-byte against the independent PyPI `qrcode` package in a
scratch venv (tests/test_kovrel.py documents the oracle run; the
oracle is dev-only, never a tree dependency). A wrong Reed-Solomon
table, a wrong format/version-info BCH, or a wrong placement order
all show up as a matrix mismatch there.

`encode(text)` returns (version, modules) with modules a list of
rows of bools (True = dark). `render_png(text, path, scale, border)`
writes a PNG via Pillow (already in the tree's stack —
core/desktop/cursor_geometry.py imports PIL.ImageGrab).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Tables (ISO/IEC 18004, error-correction level M, versions 1-10)
# ---------------------------------------------------------------------------
# (total codewords, EC codewords per block, number of blocks).
# Versions 7-10 corrected against the qrcode package's RS block
# table (first draft had 2 blocks for V7-V10; the table says 4, 4,
# 5, 5 — V8/V9/V10 split unevenly into short + long data blocks,
# which _interleave handles generally).
_VERSION_TABLE_M = {
    1: (26, 10, 1),
    2: (44, 16, 1),
    3: (70, 26, 1),
    4: (100, 18, 2),
    5: (134, 24, 2),
    6: (172, 16, 4),
    7: (196, 18, 4),
    8: (242, 22, 4),
    9: (292, 22, 5),
    10: (346, 26, 5),
}

# Alignment pattern centres (version -> sorted positions).
_ALIGNMENT_CENTRES = {
    1: (),
    2: (6, 18),
    3: (6, 22),
    4: (6, 26),
    5: (6, 30),
    6: (6, 34),
    7: (6, 22, 38),
    8: (6, 24, 42),
    9: (6, 26, 46),
    10: (6, 28, 50),
}

_MODE_BYTE = 0b0100
_EC_LEVEL_BITS_M = 0b00  # L=01, M=00, Q=11, H=10
_MASK_PATTERN = 0  # fixed; (row + col) % 2 == 0 flips
_FORMAT_MASK = 0x5412
_FORMAT_GENERATOR = 0x537
_VERSION_GENERATOR = 0x1F25
_PAD_BYTES = (0xEC, 0x11)


# ---------------------------------------------------------------------------
# Reed-Solomon over GF(256), primitive polynomial 0x11D
# ---------------------------------------------------------------------------
def _build_gf_tables() -> tuple[list[int], list[int]]:
    exp = [0] * 512
    log = [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    return exp, log


_EXP, _LOG = _build_gf_tables()


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(degree: int) -> list[int]:
    """Generator polynomial of `degree` (coefficients, highest first).

    Product of (x + alpha^i) for i in 0..degree-1. Multiplying a
    highest-first coefficient list by (x + r): the x term keeps
    each coefficient at its position, the r term shifts it one
    place down — the two assignments below are exactly that, and
    swapping them builds the wrong polynomial (caught by the
    qrcode oracle: the EC codewords diverged while the data
    codewords matched).
    """
    poly = [1]
    for i in range(degree):
        root = _EXP[i]
        nxt = [0] * (len(poly) + 1)
        for j, coef in enumerate(poly):
            nxt[j] ^= coef
            nxt[j + 1] ^= _gf_mul(coef, root)
        poly = nxt
    return poly


def _rs_remainder(data: bytes, degree: int) -> bytes:
    """EC codewords for `data` under a generator of `degree`."""
    gen = _rs_generator(degree)
    result = bytearray(degree)
    for byte in data:
        factor = byte ^ result[0]
        result = result[1:] + bytearray([0])
        for i in range(degree):
            result[i] ^= _gf_mul(gen[i + 1], factor)
    return bytes(result)


# ---------------------------------------------------------------------------
# Bit buffer
# ---------------------------------------------------------------------------
class _BitBuffer:
    def __init__(self) -> None:
        self._bits: list[int] = []

    def append_bits(self, value: int, count: int) -> None:
        for i in range(count - 1, -1, -1):
            self._bits.append((value >> i) & 1)

    def __len__(self) -> int:
        return len(self._bits)

    def to_bytes(self) -> bytes:
        out = bytearray()
        for i in range(0, len(self._bits), 8):
            byte = 0
            for bit in self._bits[i:i + 8]:
                byte = (byte << 1) | bit
            byte <<= 8 - len(self._bits[i:i + 8])
            out.append(byte)
        return bytes(out)


def _get_bit(value: int, index: int) -> bool:
    return bool((value >> index) & 1)


# ---------------------------------------------------------------------------
# Data codewords (byte mode, level M)
# ---------------------------------------------------------------------------
def _data_codewords(version: int, payload: bytes) -> bytes:
    """The full data-codeword stream for `payload` at `version`."""
    total, ec_per_block, num_blocks = _VERSION_TABLE_M[version]
    data_codewords = total - ec_per_block * num_blocks
    cci_bits = 8 if version <= 9 else 16

    buf = _BitBuffer()
    buf.append_bits(_MODE_BYTE, 4)
    buf.append_bits(len(payload), cci_bits)
    for byte in payload:
        buf.append_bits(byte, 8)
    capacity_bits = data_codewords * 8
    if len(buf) > capacity_bits:
        raise ValueError(
            f"payload of {len(payload)} bytes does not fit version "
            f"{version} (level M holds {data_codewords} data codewords)")
    # Terminator (up to 4 zero bits), then pad to a byte, then pad
    # bytes 0xEC, 0x11 alternating until the capacity is full.
    buf.append_bits(0, min(4, capacity_bits - len(buf)))
    while len(buf) % 8:
        buf.append_bits(0, 1)
    raw = bytearray(buf.to_bytes())
    pad_index = 0
    while len(raw) < data_codewords:
        raw.append(_PAD_BYTES[pad_index % 2])
        pad_index += 1
    return bytes(raw)


def _pick_version(payload: bytes) -> int:
    """Smallest version 1-10 whose level-M data capacity fits."""
    for version in range(1, 11):
        total, ec_per_block, num_blocks = _VERSION_TABLE_M[version]
        data_codewords = total - ec_per_block * num_blocks
        cci_bits = 8 if version <= 9 else 16
        needed = 4 + cci_bits + 8 * len(payload)
        # The terminator + byte padding cost at most 4 + 7 bits.
        if needed + 11 <= data_codewords * 8:
            return version
    raise ValueError(
        f"payload of {len(payload)} bytes exceeds version 10 (level M)")


def _interleave(version: int, data: bytes) -> bytes:
    """Interleave data + EC codewords across blocks (spec order)."""
    total, ec_per_block, num_blocks = _VERSION_TABLE_M[version]
    data_codewords = total - ec_per_block * num_blocks
    # Blocks split into a short group and a long group (at most 1
    # codeword apart); level M versions 1-10 happen to split evenly,
    # but the general shape is implemented so a table slip still
    # interleaves correctly.
    short_len = data_codewords // num_blocks
    long_count = data_codewords % num_blocks
    blocks: list[bytes] = []
    offset = 0
    for i in range(num_blocks):
        length = short_len + (1 if i >= num_blocks - long_count else 0)
        blocks.append(data[offset:offset + length])
        offset += length
    ec_blocks = [_rs_remainder(block, ec_per_block) for block in blocks]
    out = bytearray()
    max_data = max(len(b) for b in blocks)
    for i in range(max_data):
        for block in blocks:
            if i < len(block):
                out.append(block[i])
    for i in range(ec_per_block):
        for block in ec_blocks:
            out.append(block[i])
    return bytes(out)


# ---------------------------------------------------------------------------
# Module matrix
# ---------------------------------------------------------------------------
def encode(text: str) -> tuple[int, list[list[bool]]]:
    """(version, modules) for `text` — modules are rows of bools."""
    payload = text.encode("utf-8")
    version = _pick_version(payload)
    data = _data_codewords(version, payload)
    codewords = _interleave(version, data)
    size = version * 4 + 17
    modules: list[list[bool]] = [[False] * size for _ in range(size)]
    is_function: list[list[bool]] = [[False] * size for _ in range(size)]

    def set_function(row: int, col: int, dark: bool) -> None:
        modules[row][col] = bool(dark)
        is_function[row][col] = True

    def finder(row: int, col: int) -> None:
        for dr in range(-4, 5):
            for dc in range(-4, 5):
                r, c = row + dr, col + dc
                if 0 <= r < size and 0 <= c < size:
                    set_function(r, c, max(abs(dr), abs(dc)) != 2
                                 and max(abs(dr), abs(dc)) != 4)

    def alignment(row: int, col: int) -> None:
        for dr in range(-2, 3):
            for dc in range(-2, 3):
                set_function(row + dr, col + dc,
                             max(abs(dr), abs(dc)) != 1)

    # Timing patterns.
    for i in range(size):
        set_function(6, i, i % 2 == 0)
        set_function(i, 6, i % 2 == 0)
    # Finder patterns (corners) + separators are drawn by finder().
    finder(3, 3)
    finder(3, size - 4)
    finder(size - 4, 3)
    # Alignment patterns (skip any overlapping a finder).
    centres = _ALIGNMENT_CENTRES[version]
    for a in centres:
        for b in centres:
            if not ((a == centres[0] and b == centres[0])
                    or (a == centres[0] and b == centres[-1])
                    or (a == centres[-1] and b == centres[0])):
                alignment(a, b)
    # Format bits (BCH(15,5), generator 0x537, XOR mask 0x5412).
    # Placement is (row, col) — the two copies run along column 8
    # and row 8. (First draft transposed the two copies: it read
    # the reference implementation's (x, y) calls as (row, col);
    # the qrcode oracle's setup_type_info pins the layout below.)
    data_bits = (_EC_LEVEL_BITS_M << 3) | _MASK_PATTERN
    rem = data_bits
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * _FORMAT_GENERATOR)
    format_bits = ((data_bits << 10) | rem) ^ _FORMAT_MASK
    for i in range(0, 6):
        set_function(i, 8, _get_bit(format_bits, i))
    set_function(7, 8, _get_bit(format_bits, 6))
    set_function(8, 8, _get_bit(format_bits, 7))
    set_function(8, 7, _get_bit(format_bits, 8))
    for i in range(9, 15):
        set_function(8, 14 - i, _get_bit(format_bits, i))
    for i in range(0, 8):
        set_function(8, size - 1 - i, _get_bit(format_bits, i))
    for i in range(8, 15):
        set_function(size - 15 + i, 8, _get_bit(format_bits, i))
    set_function(size - 8, 8, True)  # dark module
    # Version info (versions 7+; BCH(18,6), generator 0x1F25).
    if version >= 7:
        rem = version
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * _VERSION_GENERATOR)
        version_bits = (version << 12) | rem
        for i in range(18):
            bit = _get_bit(version_bits, i)
            a = size - 11 + i % 3
            b = i // 3
            set_function(a, b, bit)
            set_function(b, a, bit)
    # Data bits, zigzag from the bottom-right, skipping column 6.
    # Cells past the end of the codeword stream are REMAINDER bits:
    # they are 0 bits, but they are still MASKED like data bits (the
    # oracle masks them — V2-V6 each have 7, V10 has 3; leaving them
    # unmasked cost exactly 3 cells on V2/V3/V4/V6).
    bits = []
    for byte in codewords:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)
    bit_index = 0
    for right in range(size - 1, 0, -2):
        if right <= 6:
            right -= 1
        for vert in range(size):
            for j in range(2):
                col = right - j
                upward = ((right + 1) & 2) == 0
                row = size - 1 - vert if upward else vert
                if not is_function[row][col]:
                    dark = bool(bits[bit_index]) if bit_index < len(bits) else False
                    # Mask 0: flip where (row + col) is even.
                    if (row + col) % 2 == 0:
                        dark = not dark
                    modules[row][col] = dark
                    bit_index += 1
    return version, modules


def render_png(text: str, path, scale: int = 8, border: int = 4) -> tuple[int, str]:
    """Render `text` as a QR PNG at `path`; returns (version, path)."""
    from PIL import Image

    version, modules = encode(text)
    size = len(modules)
    side = (size + border * 2) * scale
    image = Image.new("RGB", (side, side), "white")
    pixels = image.load()
    for row in range(size):
        for col in range(size):
            if modules[row][col]:
                x0 = (col + border) * scale
                y0 = (row + border) * scale
                for dy in range(scale):
                    for dx in range(scale):
                        pixels[x0 + dx, y0 + dy] = (0, 0, 0)
    image.save(str(path), format="PNG")
    return version, str(path)
