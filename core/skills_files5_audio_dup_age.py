import math
import wave
from datetime import datetime
from pathlib import Path
# INTENTIONAL: cross-part names imported from the helpers module
# (bare-name rule, §2 P3) — these resolve in the defining
# module's globals, not the facade's.
from .skills_files5_helpers import (
    _AGE_LIST_CAP,
    _IMAGE_EXTS,
    _PHOTO_SCAN_CAP,
    _existing_file,
    _existing_folder,
    _fmt_seconds,
    _human_size,
    _load_numpy,
    _load_pil,
    _non_clobbering,
    _suffix_note,
)


# ---------------------------------------------------------------------------
# WAV plumbing — the files4 pattern, re-factored locally (16-bit and
# 8/32-bit samples, float in -1..1), shared by the two audio tools.
# ---------------------------------------------------------------------------

_WAV_DTYPES = {1: "uint8", 2: "int16", 4: "int32"}
_WAV_FULL_SCALE = {1: 128.0, 2: 32768.0, 4: 2147483648.0}
_AUDIO_MAX_SECONDS = 600.0   # WSOLA is a Python loop — cap disclosed


def _read_wav(target: Path, np):
    """(params dict, float samples in -1..1) from a WAV file."""
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
    """Write float samples as a WAV at `dest` (non-clobbering);
    the final path actually used is returned."""
    final = _non_clobbering(dest)
    width = params["sampwidth"]
    if width == 1:
        pcm = np.clip(np.round(samples * 128.0 + 128.0), 0, 255).astype(np.uint8)
    else:
        full = _WAV_FULL_SCALE[width]
        pcm = np.clip(np.round(samples * full), -full, full - 1).astype(
            np.dtype(_WAV_DTYPES[width]))
    try:
        final.parent.mkdir(parents=True, exist_ok=True)
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


def _wav_duration(path: Path) -> float | None:
    """Seconds of audio in a WAV, read from its header — verification."""
    try:
        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate()
            return handle.getnframes() / rate if rate else None
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# AUDIO SPEED CHANGE — WSOLA time-stretch, pitch roughly preserved
# ---------------------------------------------------------------------------


def _wsola_stretch(samples, factor: float, np):
    """Time-stretch (frames, channels) float samples by `factor`
    (>1 = faster/shorter, <1 = slower/longer) with WSOLA: windowed
    segments are re-laid at a synthesis hop, each chosen near its
    nominal analysis position to maximise the normalised
    correlation with the previous segment's natural continuation.
    Returns the stretched samples, roughly len(samples)/factor."""
    frames = samples.shape[0]
    window = 1024 if frames >= 8192 else 256
    hop = window // 4
    tol = window // 2
    mono = samples.mean(axis=1)
    hann = np.hanning(window)
    target_len = max(1, int(round(frames / factor)))
    out = np.zeros((target_len + window, samples.shape[1]),
                   dtype=np.float64)
    wsum = np.zeros(target_len + window, dtype=np.float64)
    # Segment energies via a cumulative sum, for normalised scores.
    energy = np.concatenate(([0.0], np.cumsum(mono * mono)))

    def _segment_energy(start: int, length: int) -> float:
        start = max(0, min(start, frames - 1))
        end = max(start, min(start + length, frames))
        return float(energy[end] - energy[start])

    first = min(window, frames)
    out[:first] += samples[:first] * hann[:first, None]
    wsum[:first] += hann[:first]
    prev_ip = 0
    op = hop
    overlap = window - hop
    while op + window <= target_len + window:
        nominal = int(round(op * factor))
        if nominal >= frames - overlap:
            break
        tgt_start = prev_ip + hop
        if tgt_start + overlap > frames:
            break
        target_seg = mono[tgt_start:tgt_start + overlap]
        target_energy = _segment_energy(tgt_start, overlap)
        if target_energy <= 1e-12:
            best_ip = max(0, min(nominal, frames - window))
        else:
            lo = max(0, nominal - tol)
            hi = min(frames - window, nominal + tol)
            if hi < lo:
                break
            def _score(cand: int) -> float:
                seg_energy = _segment_energy(cand, overlap)
                if seg_energy <= 1e-12:
                    return 0.0
                dot = float(np.dot(mono[cand:cand + overlap],
                                   target_seg))
                return dot / math.sqrt(seg_energy * target_energy)

            # Two-stage search: coarse stride-4 pass, then a
            # stride-1 refinement around the coarse winner.
            best_ip, best_score = lo, -2.0
            for cand in range(lo, hi + 1, 4):
                score = _score(cand)
                if score > best_score:
                    best_score, best_ip = score, cand
            for cand in range(max(lo, best_ip - 4),
                              min(hi, best_ip + 4) + 1):
                score = _score(cand)
                if score > best_score:
                    best_score, best_ip = score, cand
        out[op:op + window] += samples[best_ip:best_ip + window] * hann[:, None]
        wsum[op:op + window] += hann
        prev_ip = best_ip
        op += hop
    covered = wsum > 1e-6
    if not covered.any():
        return samples[:0]
    last = int(np.max(np.nonzero(covered)[0])) + 1
    result = out[:last].copy()
    weights = wsum[:last]
    loud = weights > 1e-3
    result[loud] = result[loud] / weights[loud][:, None]
    if last >= target_len:
        result = result[:target_len]
    return result


def audio_speed_change(path: str, factor: float = 1.5,
                       output_path: str = "") -> str:
    """Speed a WAV up / slow it down (WSOLA), pitch roughly kept."""
    target = _wav_target(path, "change the speed of")
    if isinstance(target, str):
        return target
    try:
        factor = float(factor)
    except (TypeError, ValueError):
        return f"factor must be a number between 0.5 and 2.0 (got {factor!r})."
    if not 0.5 <= factor <= 2.0:
        return (f"factor must be between 0.5 (half speed) and 2.0 "
                f"(double speed) — got {factor:g}.")
    if factor == 1.0:
        return ("A speed factor of 1.0 changes nothing, so no copy "
                "was written.")
    np, err = _load_numpy()
    if err:
        return err
    try:
        params, samples = _read_wav(target, np)
    except (wave.Error, EOFError, ValueError) as e:
        return (f"'{target.name}' couldn't be read as a WAV file: {e}. "
                "It may be corrupt, or not really a WAV despite the name.")
    except OSError as e:
        return f"Couldn't read '{target.name}': {e}"
    rate = params["framerate"]
    duration = params["nframes"] / rate if rate else 0.0
    if duration > _AUDIO_MAX_SECONDS:
        return (f"'{target.name}' is {_fmt_seconds(duration)} long — "
                f"the speed changer caps at "
                f"{_fmt_seconds(_AUDIO_MAX_SECONDS)} per file (the "
                "stretch runs in plain Python). Trim a section with "
                "audio_trim first, or split the recording up.")
    try:
        stretched = _wsola_stretch(samples, factor, np)
    except Exception as e:  # noqa: BLE001
        return f"The speed change failed on '{target.name}': {e}"
    if stretched.shape[0] == 0:
        return f"The speed change produced no audio from '{target.name}'."
    if output_path and str(output_path).strip():
        dest = Path(str(output_path)).expanduser()
    else:
        tag = f"x{factor:g}".replace(".", "p")
        dest = target.with_name(f"{target.stem}_{tag}.wav")
    try:
        final = _write_wav(dest, params, stretched, np)
    except Exception as e:  # noqa: BLE001
        return f"The sped-up WAV couldn't be written: {e}"
    measured = _wav_duration(final)
    if measured is None:
        return (f"The sped-up WAV was written to {final} but "
                "couldn't be re-opened to verify it.")
    return (f"Saved {final} ({_human_size(final.stat().st_size)}): "
            f"'{target.name}' at {factor:g}× speed — "
            f"{_fmt_seconds(duration)} became "
            f"{_fmt_seconds(measured)} (measured from the written "
            f"file). Method: a basic WSOLA stretch, so the pitch "
            f"stays roughly where it was instead of shifting with "
            f"the speed; quality is honest speech-grade, not "
            f"studio-grade. The original is untouched."
            f"{_suffix_note(dest, final)}")


# ---------------------------------------------------------------------------
# SILENCE TRIM — cut long quiet stretches out of a WAV
# ---------------------------------------------------------------------------


def silence_trim(path: str, output_path: str = "",
                 threshold_db: float = -40.0,
                 min_silence_s: float = 1.0) -> str:
    """Remove silences longer than min_silence_s from a WAV."""
    target = _wav_target(path, "trim silence from")
    if isinstance(target, str):
        return target
    try:
        threshold_db = float(threshold_db)
        min_silence_s = float(min_silence_s)
    except (TypeError, ValueError):
        return "threshold_db and min_silence_s must be numbers."
    if not -80.0 <= threshold_db <= 0.0:
        return (f"threshold_db must be between -80 and 0 dB (got "
                f"{threshold_db:g}) — 0 is full volume, -40 is a "
                "typical room-quiet cut-off.")
    if min_silence_s <= 0:
        return "min_silence_s must be longer than 0 seconds."
    np, err = _load_numpy()
    if err:
        return err
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
    if frames == 0:
        return f"'{target.name}' holds no audio frames — nothing to trim."
    # Energy gate at 10 ms resolution, mono-summed.
    block = max(1, int(rate * 0.01))
    n_blocks = int(math.ceil(frames / block))
    padded = np.zeros((n_blocks * block, samples.shape[1]),
                      dtype=np.float64)
    padded[:frames] = samples
    blocks = padded.reshape(n_blocks, block, samples.shape[1])
    rms = np.sqrt(np.mean(blocks * blocks, axis=(1, 2)))
    with np.errstate(divide="ignore"):
        db = 20.0 * np.log10(np.maximum(rms, 1e-12))
    silent = db < threshold_db
    min_blocks = max(1, int(round(min_silence_s * 100)))
    keep_blocks = int(round(0.25 * 100))   # each long gap keeps 0.25 s
    keep = np.ones(n_blocks, dtype=bool)
    removed_blocks = 0
    index = 0
    while index < n_blocks:
        if not silent[index]:
            index += 1
            continue
        run_end = index
        while run_end < n_blocks and silent[run_end]:
            run_end += 1
        run_len = run_end - index
        if run_len > min_blocks:
            drop = run_len - keep_blocks
            mid = index + (run_len - drop) // 2
            keep[mid:mid + drop] = False
            removed_blocks += drop
        index = run_end
    if removed_blocks == 0:
        return (f"No silences longer than {min_silence_s:g} second(s) "
                f"below {threshold_db:g} dB were found in "
                f"'{target.name}' ({_fmt_seconds(duration)}) — "
                "nothing was written.")
    frame_keep = np.repeat(keep, block)[:frames]
    trimmed = samples[frame_keep]
    if output_path and str(output_path).strip():
        dest = Path(str(output_path)).expanduser()
    else:
        dest = target.with_name(f"{target.stem}_silence_trimmed.wav")
    try:
        final = _write_wav(dest, params, trimmed, np)
    except Exception as e:  # noqa: BLE001
        return f"The trimmed WAV couldn't be written: {e}"
    measured = _wav_duration(final)
    if measured is None:
        return (f"The trimmed WAV was written to {final} but "
                "couldn't be re-opened to verify it.")
    removed = duration - measured
    return (f"Saved {final} ({_human_size(final.stat().st_size)}): "
            f"silences longer than {min_silence_s:g} second(s) below "
            f"{threshold_db:g} dB were cut down to 0.25 s each, "
            f"removing {_fmt_seconds(removed)} — "
            f"{_fmt_seconds(duration)} became "
            f"{_fmt_seconds(measured)} (measured from the written "
            f"file). The original is untouched."
            f"{_suffix_note(dest, final)}")


# ---------------------------------------------------------------------------
# DUPLICATE PHOTO FINDER — perceptual (average) hash near-duplicates
# ---------------------------------------------------------------------------

_AHASH_BITS = 64
_AHASH_MAX_DISTANCE = 5     # of 64 bits — "looks the same", disclosed


def _average_hash(image, np) -> int | None:
    """64-bit average hash of a PIL image (8×8 mean threshold)."""
    try:
        from PIL import Image as _pil_image

        gray = image.convert("L").resize(
            (8, 8), _pil_image.Resampling.LANCZOS)
        pixels = np.asarray(gray, dtype=np.float64)
        mean = float(pixels.mean())
        bits = 0
        for value in pixels.flatten():
            bits = (bits << 1) | (1 if value > mean else 0)
        return bits
    except Exception:  # noqa: BLE001
        return None


def duplicate_photo_finder(folder: str) -> str:
    """Group visually near-identical photos by perceptual hash."""
    root = _existing_folder(folder, "photo folder")
    if isinstance(root, str):
        return root
    Image, err = _load_pil()
    if err:
        return err
    np, err = _load_numpy()
    if err:
        return err
    try:
        all_images = sorted(
            (p for p in root.rglob("*")
             if p.is_file() and p.suffix.lower() in _IMAGE_EXTS),
            key=lambda p: str(p).lower())
    except OSError as e:
        return f"Couldn't walk '{root}': {e}"
    if not all_images:
        return (f"No image files found under '{root}' — the finder "
                "looks for PNG, JPEG, GIF, BMP, WEBP and TIFF files, "
                "including in subfolders.")
    over_cap = len(all_images) > _PHOTO_SCAN_CAP
    all_images = all_images[:_PHOTO_SCAN_CAP]
    hashed: list[tuple[Path, int]] = []
    unreadable = 0
    for candidate in all_images:
        try:
            with Image.open(candidate) as image:
                image.load()
                digest = _average_hash(image, np)
        except Exception:  # noqa: BLE001
            digest = None
        if digest is None:
            unreadable += 1
        else:
            hashed.append((candidate, digest))
    if len(hashed) < 2:
        return (f"Only {len(hashed)} readable image(s) under "
                f"'{root}' — near-duplicate finding needs at least two.")
    # Union-find over pairs within the hamming-distance threshold.
    parent = list(range(len(hashed)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(hashed)):
        for j in range(i + 1, len(hashed)):
            distance = bin(hashed[i][1] ^ hashed[j][1]).count("1")
            if distance <= _AHASH_MAX_DISTANCE:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj
    clusters: dict[int, list[Path]] = {}
    for i, (candidate, _digest) in enumerate(hashed):
        clusters.setdefault(find(i), []).append(candidate)
    groups = [members for members in clusters.values() if len(members) >= 2]
    groups.sort(key=lambda members: (-len(members), str(members[0]).lower()))
    header = (f"Checked {len(hashed)} readable image(s) under "
              f"'{root}' by perceptual hash (8×8 average hash; images "
              f"within {_AHASH_MAX_DISTANCE} of 64 hash bits count "
              "as near-duplicates).")
    notes = ""
    if over_cap:
        notes += (f" Note: the scan stopped at the "
                  f"{_PHOTO_SCAN_CAP}-image cap.")
    if unreadable:
        notes += f" {unreadable} file(s) couldn't be read and were skipped."
    if not groups:
        return (header + " No near-duplicate photos found — every "
                "picture looks distinct." + notes)
    lines = [header + f" Found {len(groups)} group(s):"]
    for index, members in enumerate(groups, start=1):
        total = 0
        for member in members:
            try:
                total += member.stat().st_size
            except OSError:
                pass
        names = ", ".join(
            str(m.relative_to(root)) if m.is_relative_to(root)
            else m.name for m in members)
        lines.append(f"• Group {index}: {len(members)} photo(s), "
                     f"{_human_size(total)} together — {names}")
    lines.append("Read-only: nothing was deleted or moved — "
                 "find_duplicates (exact byte copies) is the stricter "
                 "tool if you want identical files only." + notes)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# FILE AGE REPORT — what's rotting in a folder (read-only)
# ---------------------------------------------------------------------------


def file_age_report(folder: str = "") -> str:
    """Oldest untouched files in a folder, with an old-file total."""
    if folder and str(folder).strip():
        root = _existing_folder(folder, "folder to report on")
        if isinstance(root, str):
            return root
    else:
        root = Path.home() / "Downloads"
        if not root.is_dir():
            return (f"There's no Downloads folder at {root} — name "
                    "the folder to report on instead.")
    try:
        entries = [p for p in root.iterdir() if p.is_file()]
    except OSError as e:
        return f"Couldn't list '{root}': {e}"
    if not entries:
        return f"'{root}' holds no files directly inside it — nothing to age-report."
    now = datetime.now().timestamp()
    rows: list[tuple[float, Path, int]] = []
    old_count = 0
    old_bytes = 0
    for entry in entries:
        try:
            stat = entry.stat()
        except OSError:
            continue
        age_days = (now - stat.st_mtime) / 86400.0
        rows.append((stat.st_mtime, entry, stat.st_size))
        if age_days >= 90:
            old_count += 1
            old_bytes += stat.st_size
    rows.sort(key=lambda r: r[0])
    lines = [f"Age report for '{root}' — {len(rows)} file(s) directly "
             f"in the folder (subfolders aren't counted):"]
    for mtime, entry, size in rows[:_AGE_LIST_CAP]:
        age_days = (now - mtime) / 86400.0
        when = datetime.fromtimestamp(mtime).strftime("%d %b %Y")
        lines.append(f"• {entry.name} — {_human_size(size)}, untouched "
                     f"since {when} ({age_days:.0f} day(s) ago)")
    if len(rows) > _AGE_LIST_CAP:
        lines.append(f"…and {len(rows) - _AGE_LIST_CAP} newer file(s) "
                     "not listed.")
    lines.append(f"Total rotting: {old_count} file(s) untouched for "
                 f"90+ days, {_human_size(old_bytes)} together. "
                 "Read-only shortlist — nothing was deleted or moved.")
    return "\n".join(lines)
