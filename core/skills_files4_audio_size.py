"""WAV audio + folder-size group of the skills_files_media_ops pack (moved verbatim)."""

import os
import wave
from pathlib import Path

# INTENTIONAL: cross-part names — the audio-size group resolves these bare
# names in the helpers module's globals (bare-name rule, plan section 2 P3),
# not in the facade's.
from .skills_files4_helpers import (
    _MAP_ENTRY_CAP,
    _MAP_LIST_CAP,
    _existing_file,
    _existing_folder,
    _human_size,
    _load_numpy,
    _non_clobbering,
    _suffix_note,
)

# ---------------------------------------------------------------------------
# WAV AUDIO — trim and fade (stdlib wave + numpy; WAV only, honestly)
# ---------------------------------------------------------------------------

_WAV_DTYPES = {1: "uint8", 2: "int16", 4: "int32"}
_WAV_FULL_SCALE = {1: 128.0, 2: 32768.0, 4: 2147483648.0}


def _read_wav(target: Path, np):
    """(params dict, float samples in -1..1) from a WAV file.

    Samples are a numpy array shaped (frames, channels); 8-bit WAV
    is unsigned, so its 128 offset is removed. Raises ValueError
    with a plain message for sample widths we don't handle.
    """
    with wave.open(str(target), "rb") as handle:
        params = {
            "channels": handle.getnchannels(),
            "sampwidth": handle.getsampwidth(),
            "framerate": handle.getframerate(),
            "nframes": handle.getnframes(),
            "comptype": handle.getcomptype(),
        }
        raw = handle.readframes(params["nframes"])
    if params["comptype"] != "NONE":
        raise ValueError(f"compressed WAV ({params['comptype']}) isn't supported")
    dtype = _WAV_DTYPES.get(params["sampwidth"])
    if dtype is None:
        raise ValueError(f"{params['sampwidth'] * 8}-bit WAV samples aren't supported")
    data = np.frombuffer(raw, dtype=np.dtype(dtype))
    frames = data.size // params["channels"]
    data = data[:frames * params["channels"]].reshape(frames, params["channels"])
    if params["sampwidth"] == 1:  # 8-bit WAV is unsigned, centred on 128
        samples = (data.astype(np.float64) - 128.0) / 128.0
    else:
        samples = data.astype(np.float64) / _WAV_FULL_SCALE[params["sampwidth"]]
    return params, samples


def _write_wav(dest: Path, params: dict, samples, np) -> Path:
    """Write float samples back as a WAV beside the input, never overwriting."""
    final = _non_clobbering(dest)
    width = params["sampwidth"]
    if width == 1:
        pcm = np.clip(np.round(samples * 128.0 + 128.0), 0, 255).astype(np.uint8)
    else:
        full = _WAV_FULL_SCALE[width]
        pcm = np.clip(np.round(samples * full), -full, full - 1).astype(
            np.dtype(_WAV_DTYPES[width]))
    try:
        with wave.open(str(final), "wb") as handle:
            handle.setnchannels(params["channels"])
            handle.setsampwidth(width)
            handle.setframerate(params["framerate"])
            handle.writeframes(pcm.tobytes())
    except Exception:
        try:
            if final.exists():
                final.unlink()  # never leave a half-written WAV behind
        except OSError:
            pass
        raise
    return final


def _wav_target(path: str, verb: str):
    """Validate a WAV input, or return the honest refusal string."""
    target = _existing_file(path, "WAV file")
    if isinstance(target, str):
        return target
    if target.suffix.lower() != ".wav":
        return (f"I can {verb} WAV files, but '{target.name}' is "
                f"{target.suffix.lower() or 'extensionless'} — convert "
                "it to WAV first (converting is a different tool's job).")
    return target


def _fmt_seconds(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f} seconds"
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes)} minute(s) {rest:.0f} seconds"


def audio_trim(path: str, start_s: float, end_s: float,
               output_path: str = "") -> str:
    """Cut a WAV down to the start_s..end_s range, saved as a new file."""
    target = _wav_target(path, "trim")
    if isinstance(target, str):
        return target
    np, err = _load_numpy()
    if err:
        return err
    try:
        start = float(start_s)
    except (TypeError, ValueError):
        return f"start_s must be a number of seconds (got {start_s!r})."
    try:
        end = float(end_s)
    except (TypeError, ValueError):
        return f"end_s must be a number of seconds (got {end_s!r})."
    try:
        params, samples = _read_wav(target, np)
    except (wave.Error, EOFError, ValueError) as e:
        return (f"'{target.name}' couldn't be read as a WAV file: {e}. "
                "It may be corrupt, or not really a WAV despite the name.")
    except OSError as e:
        return f"Couldn't read '{target.name}': {e}"
    rate = params["framerate"]
    duration = params["nframes"] / rate if rate else 0.0
    if start < 0:
        return f"start_s can't be negative (got {start:g})."
    if start >= duration:
        return (f"start_s ({start:g}s) is at or past the end of the "
                f"file — '{target.name}' is only {_fmt_seconds(duration)} long.")
    if end <= 0:  # 0 / negative reads as "to the end of the file"
        end = duration
    if end <= start:
        return (f"end_s ({end:g}s) must be after start_s ({start:g}s) — "
                "an empty cut saves nothing.")
    end = min(end, duration)
    first = int(start * rate)
    last = int(end * rate)
    cut = samples[first:last]
    if output_path and str(output_path).strip():
        dest = Path(str(output_path)).expanduser()
    else:
        dest = target.with_name(f"{target.stem}_trimmed.wav")
    try:
        final = _write_wav(dest, params, cut, np)
    except Exception as e:  # noqa: BLE001
        return f"The trimmed WAV couldn't be written: {e}"
    return (f"Trimmed '{target.name}' to {start:g}s–{end:g}s and saved "
            f"{final} ({_human_size(final.stat().st_size)}): "
            f"{_fmt_seconds(len(cut) / rate)} of audio, "
            f"{params['channels']} channel(s) at {rate} Hz. "
            f"The original ({_fmt_seconds(duration)}) is untouched."
            f"{_suffix_note(dest, final)}")


def audio_fade(path: str, fade_in_s: float = 1.0, fade_out_s: float = 2.0,
               output_path: str = "") -> str:
    """Apply linear fade-in / fade-out ramps to a WAV, saved as a new file."""
    target = _wav_target(path, "fade")
    if isinstance(target, str):
        return target
    np, err = _load_numpy()
    if err:
        return err
    try:
        fade_in = float(fade_in_s)
        fade_out = float(fade_out_s)
    except (TypeError, ValueError):
        return "fade_in_s and fade_out_s must be numbers of seconds."
    if fade_in < 0 or fade_out < 0:
        return "Fade lengths can't be negative — use 0 for no fade on that end."
    if fade_in == 0 and fade_out == 0:
        return ("Both fades are 0 seconds — the copy would be identical "
                "to the original, so nothing was written.")
    try:
        params, samples = _read_wav(target, np)
    except (wave.Error, EOFError, ValueError) as e:
        return (f"'{target.name}' couldn't be read as a WAV file: {e}. "
                "It may be corrupt, or not really a WAV despite the name.")
    except OSError as e:
        return f"Couldn't read '{target.name}': {e}"
    rate = params["framerate"]
    frames = params["nframes"]
    duration = frames / rate if rate else 0.0
    # One envelope: the lower of the in-ramp and the out-ramp at
    # every frame, so fades longer than the file (or overlapping)
    # still behave sensibly instead of overshooting.
    envelope = np.ones(frames, dtype=np.float64)
    in_frames = min(int(fade_in * rate), frames)
    if in_frames > 0:
        envelope[:in_frames] = np.minimum(
            envelope[:in_frames], np.linspace(0.0, 1.0, in_frames))
    out_frames = min(int(fade_out * rate), frames)
    if out_frames > 0:
        envelope[frames - out_frames:] = np.minimum(
            envelope[frames - out_frames:],
            np.linspace(1.0, 0.0, out_frames))
    faded = samples * envelope[:, None]
    if output_path and str(output_path).strip():
        dest = Path(str(output_path)).expanduser()
    else:
        dest = target.with_name(f"{target.stem}_faded.wav")
    try:
        final = _write_wav(dest, params, faded, np)
    except Exception as e:  # noqa: BLE001
        return f"The faded WAV couldn't be written: {e}"
    notes = []
    if fade_in + fade_out > duration:
        notes.append(f" Note: the fades ({fade_in:g}s in + {fade_out:g}s "
                     f"out) are longer than the file itself "
                     f"({_fmt_seconds(duration)}), so they overlap and "
                     "the middle never reaches full volume.")
    return (f"Faded '{target.name}' — in over {fade_in:g}s, out over "
            f"{fade_out:g}s (linear ramps) — and saved {final} "
            f"({_human_size(final.stat().st_size)}). Same length as "
            f"the original, {_fmt_seconds(duration)}; the original is "
            f"untouched.{_suffix_note(dest, final)}{''.join(notes)}")


# ---------------------------------------------------------------------------
# FOLDER SIZE MAP — where a folder's bytes live (read-only)
# ---------------------------------------------------------------------------


def _tree_size(root: Path, budget: list) -> tuple[int, int]:
    """(recursive byte size, files counted) under one entry.

    Symlinks are never followed. `budget` is a one-element counter
    shared across the whole map: when it runs out we stop counting
    and the caller discloses the cap — a partial map is always
    labelled, never silent.
    """
    total = 0
    counted = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            if budget[0] <= 0:
                return total, counted
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
                    counted += 1
                    budget[0] -= 1
            except OSError:
                continue
    return total, counted


def folder_size_map(path: str, depth: int = 2) -> str:
    """Break a folder's size down per entry — the textual treemap."""
    root = _existing_folder(path, "folder to map")
    if isinstance(root, str):
        return root
    try:
        depth = int(depth)
    except (TypeError, ValueError):
        return f"depth must be a whole number, 1 to 3 (got {depth!r})."
    if not 1 <= depth <= 3:
        return f"depth must be between 1 and 3 (got {depth}) — deeper maps turn into dumps."
    try:
        children = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError as e:
        return f"Couldn't list '{root}': {e}"
    if not children:
        return f"'{root}' is empty — there is nothing to map."
    budget = [_MAP_ENTRY_CAP]
    rows: list[tuple[str, int, bool, Path]] = []
    files_total = 0
    for child in children:
        try:
            if child.is_symlink():
                continue
            if child.is_dir():
                size, count = _tree_size(child, budget)
                files_total += count
                rows.append((child.name, size, True, child))
            elif child.is_file():
                size = child.stat().st_size
                budget[0] -= 1
                files_total += 1
                rows.append((child.name, size, False, child))
        except OSError:
            continue
    if not rows:
        return (f"'{root}' holds only links or unreadable entries — "
                "nothing with a size to map.")
    rows.sort(key=lambda r: -r[1])
    total = sum(r[1] for r in rows)
    lines = [f"Size map of '{root}': {_human_size(total)} across "
             f"{len(rows)} top-level entr{'ies' if len(rows) != 1 else 'y'}, "
             f"{files_total} file(s) counted in all "
             f"(sizes are recursive — a folder's number includes "
             f"everything inside it)."]
    shown = rows[:_MAP_LIST_CAP]
    for name, size, is_dir, _p in shown:
        share = (size / total * 100) if total else 0.0
        kind = "folder" if is_dir else "file"
        lines.append(f"• {name} ({kind}) — {_human_size(size)} ({share:.1f}%)")
    if len(rows) > len(shown):
        rest = sum(r[1] for r in rows[len(shown):])
        lines.append(f"• …and {len(rows) - len(shown)} smaller entries "
                     f"totalling {_human_size(rest)}.")
    if depth >= 2:
        # One level deeper for the biggest folders: where INSIDE
        # them the bytes live (top 3 children each). Depth 2 peeks
        # into the top 3 folders; depth 3 peeks into every folder
        # shown (the shared budget still caps the counting).
        peek = shown[:3] if depth == 2 else shown
        for name, size, is_dir, child_path in peek:
            if not is_dir or size == 0 or budget[0] <= 0:
                continue
            try:
                subs = sorted(child_path.iterdir(), key=lambda p: p.name.lower())
            except OSError:
                continue
            sub_rows = []
            for sub in subs:
                try:
                    if sub.is_symlink():
                        continue
                    if sub.is_dir():
                        sub_size, _c = _tree_size(sub, budget)
                    elif sub.is_file():
                        sub_size = sub.stat().st_size
                        budget[0] -= 1
                    else:
                        continue
                except OSError:
                    continue
                sub_rows.append((sub.name, sub_size))
            sub_rows.sort(key=lambda r: -r[1])
            for sub_name, sub_size in sub_rows[:3]:
                share = (sub_size / total * 100) if total else 0.0
                lines.append(f"    – inside {name}: {sub_name} — "
                             f"{_human_size(sub_size)} ({share:.1f}% of the whole)")
    if budget[0] <= 0:
        lines.append(f"Note: the count stopped at the {_MAP_ENTRY_CAP}-file "
                     "cap, so the biggest folders' sizes are partial — "
                     "treat this map as a lower bound, not an audit.")
    lines.append("folder_size gives just the total; this map is the breakdown.")
    return "\n".join(lines)
