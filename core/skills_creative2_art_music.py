"""Creative skills impl - art-music tools (fractal art, song composer, drum machine) + their private helpers and constants.
Split from core/skills_creative_generate.py (part 1 of 2); code moved verbatim.
"""

import colorsys
import math
import random
import re
import sys
import time
from array import array
from pathlib import Path

_SAMPLE_RATE = 22050

# ---------------------------------------------------------------------------
# Shared helpers (local copies — packs never import packs)
# ---------------------------------------------------------------------------


def _non_clobbering(dest: Path) -> Path:
    """Return `dest`, or 'stem (2).suffix', 'stem (3).suffix', … —
    the house rule: a created file never silently overwrites."""
    if not dest.exists():
        return dest
    counter = 2
    while True:
        candidate = dest.with_name(f"{dest.stem} ({counter}){dest.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _suffix_note(dest: Path, final: Path) -> str:
    """The disclosure sentence when a taken name was suffixed."""
    if final == dest:
        return ""
    return (f" '{dest.name}' already existed, so the suffixed name "
            f"'{final.name}' was used instead.")


def _pick_seed(seed) -> tuple:
    """(seed_int, problem) — 0 means 'choose one for me'."""
    try:
        value = int(seed)
    except (TypeError, ValueError):
        return None, f"'{seed}' isn't a whole-number seed."
    if value < 0:
        return None, ("Seeds are 0 (I'll pick one and tell you) or "
                      "a positive whole number.")
    if value == 0:
        value = random.SystemRandom().randint(1, 999_999)
    return value, None


def _load_pil():
    """Lazy Pillow import, the media packs' pattern: (Image, None)
    or (None, honest install line)."""
    try:
        from PIL import Image
        return Image, None
    except ImportError:
        return None, (
            "The drawing part didn't make it into this build of "
            "VYUK, so I can't draw pictures.")


def _save_wav(dest: Path, samples: array) -> Path:
    """Write 22050 Hz mono 16-bit samples to `dest` (suffixed if
    taken) and return the final path. Raises OSError on failure."""
    import wave
    dest.parent.mkdir(parents=True, exist_ok=True)
    final = _non_clobbering(dest)
    with wave.open(str(final), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(_SAMPLE_RATE)
        wav.writeframes(samples.tobytes())
    return final


def _midi_freq(midi: int) -> float:
    """Equal temperament, A4 (MIDI 69) = 440 Hz."""
    return 440.0 * 2 ** ((midi - 69) / 12.0)


def _to_int16(buf) -> array:
    """Float mix buffer -> 16-bit samples (little-endian ready)."""
    samples = array("h", [int(max(-1.0, min(1.0, v)) * 32767)
                          for v in buf])
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


# ---------------------------------------------------------------------------
# Harmony maths (local copy of the creative pack's HSV arithmetic)
# ---------------------------------------------------------------------------

_PALETTE_STYLES = ("complementary", "analogous", "triadic", "monochrome")


def _hsv_hex(h: float, s: float, v: float) -> str:
    """One HSV colour (h/s/v in 0..1) as an #RRGGBB string."""
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, min(1.0, max(0.0, s)),
                                  min(1.0, max(0.0, v)))
    return "#{:02X}{:02X}{:02X}".format(round(r * 255), round(g * 255),
                                       round(b * 255))


def _harmonies(h: float, s: float, v: float) -> dict:
    """{style: [(role, hex), ...]} — five colours per harmony.
    Hue offsets are the textbook ones: complement +1/2, triadic
    ±1/3, analogous ±1/12."""
    light_v = min(1.0, v + 0.22)
    deep_v = max(0.18, v - 0.32)
    soft_s = s * 0.55
    return {
        "complementary": [
            ("Base", _hsv_hex(h, s, v)),
            ("Complement", _hsv_hex(h + 0.5, s, v)),
            ("Base, light", _hsv_hex(h, soft_s, light_v)),
            ("Complement, light", _hsv_hex(h + 0.5, soft_s, light_v)),
            ("Deep shade", _hsv_hex(h, s * 0.8, deep_v)),
        ],
        "analogous": [
            ("Cool neighbour", _hsv_hex(h - 1 / 12, s, v)),
            ("Base", _hsv_hex(h, s, v)),
            ("Warm neighbour", _hsv_hex(h + 1 / 12, s, v)),
            ("Far neighbour", _hsv_hex(h + 2 / 12, s * 0.85, v)),
            ("Deep shade", _hsv_hex(h, s * 0.8, deep_v)),
        ],
        "triadic": [
            ("Base", _hsv_hex(h, s, v)),
            ("Triad 1", _hsv_hex(h + 1 / 3, s, v)),
            ("Triad 2", _hsv_hex(h + 2 / 3, s, v)),
            ("Base, light", _hsv_hex(h, soft_s, light_v)),
            ("Triad 1, deep", _hsv_hex(h + 1 / 3, s, deep_v)),
        ],
        "monochrome": [
            ("Darkest", _hsv_hex(h, s, 0.20)),
            ("Dark", _hsv_hex(h, s, 0.35)),
            ("Mid", _hsv_hex(h, s, 0.50)),
            ("Bright", _hsv_hex(h, s, 0.70)),
            ("Brightest", _hsv_hex(h, s, 0.90)),
        ],
    }


def _hex_rgb(hexv: str) -> tuple:
    return tuple(int(hexv[i:i + 2], 16) for i in (1, 3, 5))


# ---------------------------------------------------------------------------
# FRACTAL ART — escape-time fractals in pure Python (no numpy)
# ---------------------------------------------------------------------------

_FRACTAL_KINDS = ("mandelbrot", "julia")
_FRACTAL_MIN_SIZE = 256
_FRACTAL_MAX_SIZE = 1024
_FRACTAL_INTERIOR = (6, 6, 14)

# Julia constants that are known to give beautiful, connected
# sets — the seed indexes into this set, so every seed looks good.
_JULIA_CONSTANTS = (
    (-0.7, 0.27015),
    (-0.8, 0.156),
    (0.285, 0.01),
    (-0.4, 0.6),
    (0.355, 0.355),
)


def _fractal_max_iter(size: int) -> int:
    """Iteration budget scaled by size — capped at 80 so a
    1024×1024 render stays responsive in pure Python."""
    return min(80, 48 + size // 32)


def _julia_constant(seed: int) -> tuple:
    """The known-good Julia constant for a seed."""
    return _JULIA_CONSTANTS[seed % len(_JULIA_CONSTANTS)]


def _escape(cr: float, ci: float, max_iter: int,
            jr: float = None, ji: float = None) -> tuple:
    """(iterations, |z|² at exit). Mandelbrot mode (jr is None):
    z starts at 0 and c is the point — with the main-cardioid and
    period-2-bulb tests so deep-interior points return at once.
    Julia mode: z starts at the point and c is (jr, ji)."""
    if jr is None:
        q = (cr - 0.25) ** 2 + ci * ci
        if q * (q + (cr - 0.25)) <= 0.25 * ci * ci:
            return max_iter, 0.0
        if (cr + 1.0) ** 2 + ci * ci <= 0.0625:
            return max_iter, 0.0
        zr = zi = 0.0
        kr, ki = cr, ci
    else:
        zr, zi = cr, ci
        kr, ki = jr, ji
    for n in range(max_iter):
        zr2 = zr * zr
        zi2 = zi * zi
        if zr2 + zi2 > 4.0:
            return n, zr2 + zi2
        zi = 2.0 * zr * zi + ki
        zr = zr2 - zi2 + kr
    return max_iter, zr * zr + zi * zi


def _palette_lut(rng: random.Random, stops: int = 128) -> tuple:
    """(harmony_style, [RGB, ...]) — a smooth cyclic LUT of
    `stops` colours interpolated through the five harmony colours
    drawn from `rng` (called first, like create_art's palette)."""
    style = rng.choice(_PALETTE_STYLES)
    h = rng.random()
    s = 0.55 + rng.random() * 0.35
    v = 0.78 + rng.random() * 0.22
    rgbs = [_hex_rgb(hexv) for _role, hexv in _harmonies(h, s, v)[style]]
    lut = []
    m = len(rgbs)
    for i in range(stops):
        t = i / stops * m
        j = int(t) % m
        frac = t - int(t)
        a, b = rgbs[j], rgbs[(j + 1) % m]
        lut.append(tuple(round(a[c] + (b[c] - a[c]) * frac)
                         for c in range(3)))
    return style, lut


def _render_fractal(kind: str, seed: int, size: int):
    """(Image, meta) — pure-Python escape-time render. The
    Mandelbrot view is centred on the real axis, so rows are
    computed once and mirrored (conjugate orbits escape alike)."""
    Image, err = _load_pil()
    if err:
        raise RuntimeError(err)
    rng = random.Random(seed)
    pal_style, lut = _palette_lut(rng)
    max_iter = _fractal_max_iter(size)
    if kind == "julia":
        jc = _julia_constant(seed)
        span = 3.0
        cx = cy = 0.0
    else:
        jc = None
        span = 2.8 + rng.random() * 0.4
        cx, cy = -0.65, 0.0
    symmetric = jc is None
    rows = (size + 1) // 2 if symmetric else size
    pixels = [(0, 0, 0)] * (size * size)
    for y in range(rows):
        imag = cy + (0.5 - y / (size - 1)) * span
        row = []
        for x in range(size):
            real = cx + (x / (size - 1) - 0.5) * span
            if jc is None:
                n, mag2 = _escape(real, imag, max_iter)
            else:
                n, mag2 = _escape(real, imag, max_iter, jc[0], jc[1])
            if n >= max_iter:
                row.append(_FRACTAL_INTERIOR)
            else:
                az = math.sqrt(mag2)
                nu = n + 1 - math.log2(math.log(az)) if az > 1.0 else n
                row.append(lut[int(nu * 2) % len(lut)])
        pixels[y * size:(y + 1) * size] = row
        if symmetric:
            mirror = size - 1 - y
            if mirror != y:
                pixels[mirror * size:(mirror + 1) * size] = row
    img = Image.new("RGB", (size, size))
    img.putdata(pixels)
    meta = {"palette": pal_style, "max_iter": max_iter,
            "span": span, "julia_c": jc}
    return img, meta


def fractal_art(kind: str = "mandelbrot", seed: int = 0,
                size: int = 768, output: str = ""):
    """Render a Mandelbrot or Julia fractal PNG, coloured by a
    harmony palette drawn from the seed.

    R1a (tools loopholes, seam pass 2): the save is VERIFIED by
    re-reading the file (core.tool_outcome.file_written) — the
    same pattern create_art uses — so the result is an Outcome,
    verified only when the PNG is on disk. A save whose re-read
    fails is never claimed as a success.
    """
    kind = (kind or "mandelbrot").strip().lower()
    if kind not in _FRACTAL_KINDS:
        return (f"I don't render a '{kind}' fractal — kinds are "
                "mandelbrot and julia.")
    try:
        size = int(size)
    except (TypeError, ValueError):
        return f"'{size}' isn't a whole number of pixels."
    if not (_FRACTAL_MIN_SIZE <= size <= _FRACTAL_MAX_SIZE):
        return (f"Fractal size must be between {_FRACTAL_MIN_SIZE} "
                f"and {_FRACTAL_MAX_SIZE} pixels — {size} is "
                "outside that (pure-Python rendering keeps the "
                "cap honest).")
    chosen_seed, problem = _pick_seed(seed)
    if problem:
        return problem
    _img, err = _load_pil()
    if err:
        return err
    started = time.perf_counter()
    try:
        img, meta = _render_fractal(kind, chosen_seed, size)
    except RuntimeError as e:
        return str(e)
    elapsed = time.perf_counter() - started
    if output:
        dest = Path(str(output)).expanduser()
        if not dest.suffix:
            dest = dest.with_suffix(".png")
    else:
        dest = (Path.home() / "Pictures" / "Nova"
                / f"nova_fractal_{kind}_{chosen_seed}.png")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        final = _non_clobbering(dest)
        img.save(final, "PNG")
    except OSError as e:
        return f"Couldn't save the fractal to {dest}: {e}"
    detail = ""
    if meta["julia_c"] is not None:
        jr, ji = meta["julia_c"]
        detail = f" Julia constant c = {jr:g} {'+' if ji >= 0 else '−'} {abs(ji):g}i,"
    message = (f"Rendered a {kind} fractal — {size}×{size}, up to "
               f"{meta['max_iter']} iterations per pixel,{detail} "
               f"{meta['palette']} palette (seed {chosen_seed}), "
               f"rendered in {elapsed:.1f} s. Saved to {final}."
               f"{_suffix_note(dest, final)}")
    # R1a: verify the save by re-reading the file — same pattern
    # as create_art (Fix 7).
    from . import tool_outcome as _outcome  # R1a: seam wiring

    ok, evidence = _outcome.file_written(final)
    if ok:
        return _outcome.verified(message, evidence)
    return _outcome.failed(
        f"The fractal was rendered but the save to {final} could "
        f"not be confirmed: {evidence}.", evidence)


# ---------------------------------------------------------------------------
# COMPOSE SONG — three layers (pads + bass + melody) over a progression
# ---------------------------------------------------------------------------

_MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
_MINOR_SCALE = (0, 2, 3, 5, 7, 8, 10)

# mood -> key, root MIDI, scale, tempo, and the chord progression
# as (display numerals, ((semitone offset from root, quality), …)).
# Every progression is diatonic, so chord tones are always a
# subset of the key's scale.
_SONG_MOODS = {
    "calm": {
        "key": "C major", "root": 60, "offsets": _MAJOR_SCALE,
        "bpm": 76, "numerals": "I–vi–IV–V",
        "chords": ((0, "maj"), (9, "min"), (5, "maj"), (7, "maj")),
    },
    "bright": {
        "key": "C major", "root": 60, "offsets": _MAJOR_SCALE,
        "bpm": 104, "numerals": "I–V–vi–IV",
        "chords": ((0, "maj"), (7, "maj"), (9, "min"), (5, "maj")),
    },
    "melancholy": {
        "key": "A minor", "root": 57, "offsets": _MINOR_SCALE,
        "bpm": 68, "numerals": "i–VI–III–VII",
        "chords": ((0, "min"), (8, "maj"), (3, "maj"), (10, "maj")),
    },
    "playful": {
        "key": "C major", "root": 60, "offsets": _MAJOR_SCALE,
        "bpm": 118, "numerals": "I–IV–V–IV",
        "chords": ((0, "maj"), (5, "maj"), (7, "maj"), (5, "maj")),
    },
}

_SONG_MIN_SECONDS = 8
_SONG_MAX_SECONDS = 60


def _fold_pad_root(midi: int) -> int:
    """Fold a chord root into the pad register (MIDI 55–66)."""
    while midi > 66:
        midi -= 12
    while midi < 55:
        midi += 12
    return midi


def _chord_tones(cfg: dict, chord: tuple) -> tuple:
    """(pad_root, (triad tones)) — root-position triad in pad
    register; third is major (4) or minor (3) by quality."""
    offset, quality = chord
    root = _fold_pad_root(cfg["root"] + offset)
    third = 4 if quality == "maj" else 3
    return root, (root, root + third, root + 7)


def _song_plan(seed: int, mood: str, seconds: float) -> list:
    """Pure: [(start_s, dur_s, midi, layer), …] for the whole
    song — pads and bass are formulaic per bar; the melody is a
    seeded random walk that favours the current bar's chord
    tones (60%) and otherwise steps within the key's scale.
    Same seed + mood + seconds -> the same plan, always."""
    cfg = _SONG_MOODS[mood]
    rng = random.Random(seed)
    beat = 60.0 / cfg["bpm"]
    bar = 4 * beat
    n_bars = max(1, round(seconds / bar))
    total = n_bars * bar
    events = []
    for b in range(n_bars):
        t0 = b * bar
        chord = cfg["chords"][b % len(cfg["chords"])]
        proot, triad = _chord_tones(cfg, chord)
        for tone in triad:
            events.append((t0, bar, tone, "pad"))
        events.append((t0, bar / 2, proot - 12, "bass"))
        second = proot - 12 if b % 2 == 0 else proot - 12 + 7
        events.append((t0 + bar / 2, bar / 2, second, "bass"))
    table = [cfg["root"] + 12 + off for off in cfg["offsets"]]
    table += [cfg["root"] + 24 + off for off in cfg["offsets"]]
    table.append(cfg["root"] + 36)
    idx = len(cfg["offsets"])
    t = 0.0
    while t < total - 0.01:
        dur = rng.choice((0.5, 1, 1, 1, 2)) * beat
        if t + dur > total:
            dur = total - t
        if rng.random() >= 0.14:  # not a rest
            bar_idx = int(t // bar) % len(cfg["chords"])
            proot, _triad = _chord_tones(cfg, cfg["chords"][bar_idx])
            if rng.random() < 0.6:
                tones = (proot + 12, proot + 12 + (4 if
                         cfg["chords"][bar_idx][1] == "maj" else 3),
                         proot + 19, proot + 24)
                note = rng.choice(tones)
                idx = min(range(len(table)),
                          key=lambda i: abs(table[i] - note))
            else:
                idx = max(0, min(len(table) - 1,
                                 idx + rng.choice((-2, -1, -1, 0,
                                                   1, 1, 2))))
                note = table[idx]
            events.append((t, dur, note, "melody"))
        t += dur
    events.sort(key=lambda e: (e[0], e[3]))
    return events


def _add_tone(buf, s0: int, freq: float, dur: float, gain: float,
              layer: str) -> None:
    """Add one synthesised note into the float mix buffer."""
    n = int(round(dur * _SAMPLE_RATE))
    for i in range(n):
        j = s0 + i
        if j >= len(buf):
            break
        t = i / _SAMPLE_RATE
        if layer == "pad":
            ramp = max(0.05, 0.3 * dur)
            env = min(1.0, t / ramp, (dur - t) / ramp)
            if env < 0.0:
                env = 0.0
            val = (math.sin(2 * math.pi * freq * t)
                   + 0.25 * math.sin(4 * math.pi * freq * t))
        elif layer == "bass":
            env = min(1.0, t / 0.008) * math.exp(-2.0 * t / dur)
            val = (math.sin(2 * math.pi * freq * t)
                   + 0.15 * math.sin(4 * math.pi * freq * t))
        else:  # melody
            env = min(1.0, t / 0.005) * math.exp(-3.0 * t / dur)
            val = (math.sin(2 * math.pi * freq * t)
                   + 0.3 * math.sin(4 * math.pi * freq * t))
        buf[j] += gain * env * val


_LAYER_GAINS = {"pad": 0.14, "bass": 0.32, "melody": 0.30}


def _render_song(seed: int, mood: str, seconds: float) -> tuple:
    """(16-bit samples, total_seconds) — mix the plan, then
    normalise so the peak never exceeds 0.85."""
    plan = _song_plan(seed, mood, seconds)
    total = max(start + dur for start, dur, _m, _l in plan)
    n_samples = int(round(total * _SAMPLE_RATE))
    buf = array("d", bytes(8 * n_samples))
    for start, dur, midi, layer in plan:
        _add_tone(buf, int(round(start * _SAMPLE_RATE)),
                  _midi_freq(midi), dur, _LAYER_GAINS[layer], layer)
    peak = 0.0
    for v in buf:
        if abs(v) > peak:
            peak = abs(v)
    if peak > 0.85:
        scale = 0.85 / peak
        for i in range(len(buf)):
            buf[i] *= scale
    return _to_int16(buf), total


def compose_song(mood: str = "calm", seed: int = 0,
                 seconds: float = 24, output: str = "") -> str:
    """Compose a three-layer song (chord pads, bass, melody) over
    a classic progression and save it as a WAV."""
    mood = (mood or "calm").strip().lower()
    if mood not in _SONG_MOODS:
        return (f"I don't compose songs in a '{mood}' mood — "
                "moods are calm, bright, melancholy and playful.")
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return f"'{seconds}' isn't a number of seconds."
    if not (_SONG_MIN_SECONDS <= seconds <= _SONG_MAX_SECONDS):
        return (f"Songs run {_SONG_MIN_SECONDS}–"
                f"{_SONG_MAX_SECONDS} seconds — {seconds:g} is "
                "outside that.")
    chosen_seed, problem = _pick_seed(seed)
    if problem:
        return problem
    cfg = _SONG_MOODS[mood]
    samples, total = _render_song(chosen_seed, mood, seconds)
    if output:
        dest = Path(str(output)).expanduser()
        if not dest.suffix:
            dest = dest.with_suffix(".wav")
    else:
        dest = (Path.home() / "Music" / "Nova"
                / f"nova_song_{mood}_{chosen_seed}.wav")
    try:
        final = _save_wav(dest, samples)
    except OSError as e:
        return f"Couldn't save the song to {dest}: {e}"
    # R1b: re-read the saved WAV — _save_wav returning does not
    # prove it landed on disk.
    from . import tool_outcome as _outcome  # R1b: seam wiring

    ok, evidence = _outcome.file_written(final)
    if not ok:
        return (f"Couldn't confirm the saved song: {evidence}.")
    return _outcome.verified(
        f"Composed an original {total:.0f}-second song in "
        f"{cfg['key']} — progression {cfg['numerals']}, three "
        f"layers (chord pads, bass line, melody), seed "
        f"{chosen_seed} — saved to {final}."
        f"{_suffix_note(dest, final)} Synthesised note by "
        "note — no samples, nothing covered.",
        evidence)


# ---------------------------------------------------------------------------
# GENERATE BEAT — a synthesised drum loop (primitive on purpose)
# ---------------------------------------------------------------------------

# 16-step grids per style: x = hit, . = rest. Instruments: kick,
# snare (house uses it as a clap), chat (closed hat), ohat (open).
_BEAT_PATTERNS = {
    "lofi": {
        "swing": 0.14,
        "kick": "x......x..x.....",
        "snare": "....x.......x...",
        "chat": "x.x.x.x.x.x.x.x.",
        "ohat": "..............x.",
    },
    "house": {
        "swing": 0.0,
        "kick": "x...x...x...x...",
        "snare": "....x.......x...",
        "chat": "x.x.x.x.x.x.x.x.",
        "ohat": "..x...x...x...x.",
    },
    "rock": {
        "swing": 0.0,
        "kick": "x.......x.x.....",
        "snare": "....x.......x...",
        "chat": "x.x.x.x.x.x.x.x.",
        "ohat": "................",
    },
    "chill": {
        "swing": 0.0,
        "kick": "x.........x.....",
        "snare": "............x...",
        "chat": "x...x...x...x...",
        "ohat": "..............x.",
    },
}

_BEAT_MIN_BPM = 40
_BEAT_MAX_BPM = 220
_BEAT_MIN_SECONDS = 2
_BEAT_MAX_SECONDS = 60


def _pattern(style: str) -> dict:
    """{instrument: (bool × 16)} parsed from the style's grid."""
    grid = _BEAT_PATTERNS[style]
    return {inst: tuple(ch == "x" for ch in grid[inst])
            for inst in ("kick", "snare", "chat", "ohat")}


def _schedule(style: str, bpm: float, seconds: float) -> tuple:
    """([(time_s, instrument), …], total_seconds, n_bars) — whole
    bars only, so the loop tiles seamlessly; lofi's odd 16ths are
    delayed by its swing factor."""
    grid = _BEAT_PATTERNS[style]
    pats = _pattern(style)
    step = 60.0 / bpm / 4
    bar_dur = 16 * step
    n_bars = max(1, round(seconds / bar_dur))
    total = n_bars * bar_dur
    swing = grid["swing"]
    events = []
    for b in range(n_bars):
        for s in range(16):
            t = b * bar_dur + s * step
            if swing and s % 2 == 1:
                t += swing * step
            for inst in ("kick", "snare", "chat", "ohat"):
                if pats[inst][s]:
                    events.append((t, inst))
    events.sort(key=lambda e: e[0])
    return events, total, n_bars


def _add_kick(buf, s0: int) -> None:
    """A kick: sine burst whose pitch drops 120 -> 45 Hz."""
    n = int(0.28 * _SAMPLE_RATE)
    phase = 0.0
    for i in range(n):
        j = s0 + i
        if j >= len(buf):
            break
        t = i / _SAMPLE_RATE
        freq = 45.0 + 75.0 * math.exp(-t * 28.0)
        phase += 2.0 * math.pi * freq / _SAMPLE_RATE
        buf[j] += 0.95 * math.exp(-t * 11.0) * math.sin(phase)


def _add_noise_hit(buf, s0: int, rng: random.Random, dur: float,
                   gain: float, decay: float, body_freq: float = 0.0,
                   body_gain: float = 0.0) -> None:
    """A noise hit, band-shaped by simple differencing (a crude
    one-pole high-pass) — primitive synthesis, honestly."""
    n = int(dur * _SAMPLE_RATE)
    prev = 0.0
    for i in range(n):
        j = s0 + i
        if j >= len(buf):
            break
        t = i / _SAMPLE_RATE
        w = rng.uniform(-1.0, 1.0)
        hp = w - prev
        prev = w
        env = math.exp(-t * decay)
        val = hp * env * gain
        if body_freq:
            val += (body_gain * math.exp(-t * 28.0)
                    * math.sin(2 * math.pi * body_freq * t))
        buf[j] += val


def _render_beat(style: str, bpm: float, seconds: float) -> tuple:
    """(16-bit samples, total_seconds, n_bars) — noise comes from
    a fixed Random(1234) stream consumed in event order, so a
    style + tempo always renders the identical loop."""
    events, total, n_bars = _schedule(style, bpm, seconds)
    n_samples = int(round(total * _SAMPLE_RATE))
    buf = array("d", bytes(8 * n_samples))
    rng = random.Random(1234)
    for t, inst in events:
        s0 = int(round(t * _SAMPLE_RATE))
        if inst == "kick":
            _add_kick(buf, s0)
        elif inst == "snare":
            _add_noise_hit(buf, s0, rng, 0.20, 0.55, 16.0,
                           body_freq=180.0, body_gain=0.40)
        elif inst == "chat":
            _add_noise_hit(buf, s0, rng, 0.05, 0.20, 80.0)
        else:  # ohat
            _add_noise_hit(buf, s0, rng, 0.35, 0.18, 10.0)
    peak = 0.0
    for v in buf:
        if abs(v) > peak:
            peak = abs(v)
    if peak > 0.85:
        scale = 0.85 / peak
        for i in range(len(buf)):
            buf[i] *= scale
    return _to_int16(buf), total, n_bars


def generate_beat(style: str = "lofi", bpm: int = 92,
                  seconds: float = 8, output: str = ""):
    """Synthesise a drum loop (kick/snare/hats from sine + noise)
    and save it as a WAV.

    Fix 7 (tools loopholes): returns an Outcome — the save is
    verified by re-reading the file, never claimed on the save call
    alone.
    """
    style = (style or "lofi").strip().lower()
    if style not in _BEAT_PATTERNS:
        return (f"I don't play a '{style}' beat — styles are "
                "lofi, house, rock and chill.")
    try:
        bpm = int(bpm)
    except (TypeError, ValueError):
        return f"'{bpm}' isn't a whole-number tempo."
    if not (_BEAT_MIN_BPM <= bpm <= _BEAT_MAX_BPM):
        return (f"Tempo must be {_BEAT_MIN_BPM}–{_BEAT_MAX_BPM} "
                f"BPM — {bpm} is outside that.")
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return f"'{seconds}' isn't a number of seconds."
    if not (_BEAT_MIN_SECONDS <= seconds <= _BEAT_MAX_SECONDS):
        return (f"Beats run {_BEAT_MIN_SECONDS}–"
                f"{_BEAT_MAX_SECONDS} seconds — {seconds:g} is "
                "outside that.")
    samples, total, n_bars = _render_beat(style, bpm, seconds)
    if output:
        dest = Path(str(output)).expanduser()
        if not dest.suffix:
            dest = dest.with_suffix(".wav")
    else:
        dest = (Path.home() / "Music" / "Nova"
                / f"nova_beat_{style}_{bpm}bpm.wav")
    try:
        final = _save_wav(dest, samples)
    except OSError as e:
        return f"Couldn't save the beat to {dest}: {e}"
    snap = ""
    if abs(total - seconds) > 0.05:
        snap = (f" Loops are built in whole bars, so the length "
                f"snapped from {seconds:g} s to {total:.1f} s.")
    from . import tool_outcome as _outcome  # Fix 7: verify the save
    _ok, _evidence = _outcome.file_written(final)
    if not _ok:
        return _outcome.failed(
            "The beat was built but the save to %s could not be "
            "confirmed: %s." % (final, _evidence), _evidence)
    return _outcome.verified(
        "Built a %s beat at %d BPM — %d bars, "
        "%.1f seconds of synthesised drums (sine-drop "
        "kicks, differenced-noise hats — primitive synthesis, "
        "no drum samples) — saved to %s."
        "%s%s"
        % (style, bpm, n_bars, total, final,
           _suffix_note(dest, final), snap),
        _evidence)
