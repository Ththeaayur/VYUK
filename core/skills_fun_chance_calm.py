import math
import random
import re
import threading
import time
from datetime import date, datetime, timedelta
from .skills_smart_chance import _MAX_DICE, _MAX_SIDES, _parse_dice


# ---------------------------------------------------------------------------
# DICE ROLLER PLUS — roll_dice's notation, with advantage
# ---------------------------------------------------------------------------

_DICE_EXAMPLES = ("Dice look like '3d6+2', 'd20' or '2d8-1' — a "
                  "count, then 'd', then the sides, then an "
                  "optional + or - modifier.")


def _roll_once(count: int, sides: int) -> list:
    return [random.randint(1, sides) for _ in range(count)]


def _dice_label(count: int, sides: int, modifier: int) -> str:
    label = f"{count}d{sides}"
    if modifier:
        label += f"{modifier:+d}"
    return label


def _dice_line(count: int, sides: int, modifier: int,
               rolls: list) -> tuple:
    """(spoken line, total) in roll_dice's exact house format."""
    parts = " + ".join(str(r) for r in rolls)
    total = sum(rolls) + modifier
    label = _dice_label(count, sides, modifier)
    if modifier:
        return f"{label}: rolled {parts}, {modifier:+d} → {total}", total
    return f"{label}: rolled {parts} → {total}", total


def dice_roller_plus(notation: str = "1d6", mode: str = "normal") -> str:
    """Roll NdM dice (roll_dice's parser and caps), listing every
    die; 'advantage'/'disadvantage' roll the set twice and keep
    the better/worse total."""
    mode = (mode or "normal").strip().lower()
    if mode not in ("normal", "advantage", "disadvantage"):
        return (f"I don't know a dice mode called '{mode}' — use "
                "'normal', 'advantage' or 'disadvantage'.")
    parsed = _parse_dice(notation)
    if parsed is None:
        return (f"I can't read '{notation}' as dice. "
                f"{_DICE_EXAMPLES} Mode 'advantage' rolls the whole "
                "set twice and keeps the higher total; "
                "'disadvantage' keeps the lower.")
    count, sides, modifier = parsed
    if count < 1 or count > _MAX_DICE:
        return (f"I roll between 1 and {_MAX_DICE} dice at a time "
                f"— '{notation}' asks for {count}.")
    if sides < 2 or sides > _MAX_SIDES:
        return (f"A die needs between 2 and {_MAX_SIDES} sides — "
                f"'{notation}' asks for {sides}.")
    if mode == "normal":
        line, _total = _dice_line(count, sides, modifier,
                                  _roll_once(count, sides))
        return line
    first_line, first_total = _dice_line(
        count, sides, modifier, _roll_once(count, sides))
    second_line, second_total = _dice_line(
        count, sides, modifier, _roll_once(count, sides))
    if mode == "advantage":
        kept = max(first_total, second_total)
        word = "higher"
    else:
        kept = min(first_total, second_total)
        word = "lower"
    return (f"{mode.capitalize()} — two sets:\n• {first_line}\n"
            f"• {second_line}\nKeeping the {word} total: {kept}.")



# ---------------------------------------------------------------------------
# COIN DECISION — weigh the stated pros, never pretend to know more
# ---------------------------------------------------------------------------

_POS_WORDS = (
    "good", "great", "cheap", "cheaper", "easy", "easier", "fun",
    "healthy", "healthier", "fast", "faster", "quick", "close",
    "closer", "convenient", "love", "like", "best", "better",
    "tasty", "fresh", "quiet", "safe", "safer", "comfy",
    "comfortable", "exciting", "relaxing", "productive", "free",
)
_NEG_WORDS = (
    "bad", "expensive", "costly", "hard", "difficult", "boring",
    "far", "farther", "slow", "slower", "hate", "worst", "worse",
    "risky", "tiring", "noisy", "unhealthy", "inconvenient",
    "crowded", "late", "stressful", "complicated", "annoying",
)


def _parse_options(options) -> list:
    """Options arrive as one string: ';'-separated (commas also
    accepted when there are no semicolons) or as a list."""
    if isinstance(options, (list, tuple)):
        items = [str(o) for o in options]
    elif isinstance(options, str):
        separator = ";" if ";" in options else ","
        items = options.split(separator)
    else:
        items = []
    return [item.strip() for item in items if str(item).strip()]


def _score_pros(options: list, pros: str) -> list:
    """Score each option from the pros text alone: per clause, an
    option named in the clause earns its positive words minus its
    negative words. Returns [{option, score, pos_words, neg_words}].
    Pure — the heuristic is deliberately simple and shown to the
    user in the result."""
    text = (pros or "").lower()
    clauses = [c for c in re.split(r"[.;,\n]|\bbut\b", text)
               if c.strip()]
    scored = []
    for option in options:
        keywords = [w for w in re.findall(r"[a-z]+", option.lower())
                    if len(w) >= 3]
        score = 0
        pos_found: list = []
        neg_found: list = []
        for clause in clauses:
            if keywords and not any(k in clause for k in keywords):
                continue
            for word in _POS_WORDS:
                if re.search(rf"\b{word}\b", clause):
                    score += 1
                    pos_found.append(word)
            for word in _NEG_WORDS:
                if re.search(rf"\b{word}\b", clause):
                    score -= 1
                    neg_found.append(word)
        scored.append({"option": option, "score": score,
                       "pos": pos_found, "neg": neg_found})
    return scored


def _flip_coin() -> str:
    return random.choice(("Heads", "Tails"))


def coin_decision(options: str = "", pros: str = "") -> str:
    """Recommend between options by weighing the pros and cons
    the user actually stated — and say when there's nothing to
    weigh, settling a true tie with a coin flip."""
    choices = _parse_options(options)
    if len(choices) < 2:
        return ("Give me at least two options to decide between, "
                "separated by semicolons — like coin_decision"
                "(options='stay home; go out', pros='staying is "
                "cheap and relaxing, going out is fun but "
                "expensive').")
    scored = _score_pros(choices, pros)
    best_score = max(s["score"] for s in scored)
    leaders = [s for s in scored if s["score"] == best_score]
    if len(leaders) == 1 and best_score > 0:
        winner = leaders[0]
        lines = [f"I'd go with '{winner['option']}'."]
        for entry in scored:
            bits = []
            if entry["pos"]:
                bits.append("good: " + ", ".join(entry["pos"]))
            if entry["neg"]:
                bits.append("against: " + ", ".join(entry["neg"]))
            detail = ("; ".join(bits) if bits
                      else "nothing said either way")
            lines.append(f"• {entry['option']}: {detail} "
                         f"(score {entry['score']:+d})")
        lines.append("That's weighed only from the pros and cons "
                     "you gave me — the reasoning is no better "
                     "than they are, and I know nothing about the "
                     "options beyond them.")
        return "\n".join(lines)
    # A true tie (including nothing to weigh at all).
    if not (pros or "").strip():
        opening = ("You didn't give me any pros or cons to weigh, "
                   "so there's nothing to reason from.")
    else:
        opening = ("Honestly, that comes out level — what you "
                   "said doesn't tip it either way.")
    if len(choices) == 2:
        flip = _flip_coin()
        winner = choices[0] if flip == "Heads" else choices[1]
        return (f"{opening} So, the coin: {flip.lower()} — go "
                f"with '{winner}'. (Heads was '{choices[0]}', "
                f"tails '{choices[1]}'.) No reasoning behind that "
                "one — pure chance, as agreed.")
    tied_names = ", ".join(f"'{s['option']}'" for s in leaders)
    pick = random.choice(leaders)["option"]
    return (f"{opening} With more than two options the coin "
            f"becomes a random pick among the tied ones "
            f"({tied_names}): '{pick}'. Pure chance — no "
            "reasoning behind it.")



# ---------------------------------------------------------------------------
# AMBIENT SCENE — generated soundscapes, looped, with a sleep timer
# ---------------------------------------------------------------------------

_AMBIENT_RATE = 22050
_AMBIENT_SECONDS = 8.0
_AMBIENT_MAX_SLEEP = 600

_SCENE_LABELS = {
    "rain": "Rain",
    "wind": "Wind",
    "cafe": "Café murmur",
    "storm": "Storm (rain and wind)",
    "cosy_cafe": "Cosy café (rain outside, murmur inside)",
    "windy_cafe": "Windy café",
    "full_mix": "Full mix (rain, wind and café)",
}

_AMBIENT = {
    "gen": 0, "stop": None, "thread": None, "scene": None,
    "label": None, "started": None, "until": None, "timer": None,
}
_AMBIENT_LOCK = threading.Lock()


def _lowpass(samples, window: int):
    import numpy as np

    if window <= 1:
        return samples
    kernel = np.ones(window, dtype=np.float32) / window
    return np.convolve(samples, kernel, mode="same")


def _normalise(samples, peak: float = 0.8):
    import numpy as np

    top = float(np.max(np.abs(samples))) if len(samples) else 0.0
    if top > 0:
        samples = samples * (peak / top)
    return samples.astype(np.float32)


def _loop_ready(samples, rate: int):
    """Crossfade the tail into the head so the loop doesn't
    click, trimming the faded tail."""
    import numpy as np

    fade = int(0.75 * rate)
    if len(samples) <= fade * 2:
        return samples
    ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
    head = samples[:fade] * ramp + samples[-fade:] * (1.0 - ramp)
    out = samples[:-fade].copy()
    out[:fade] = head
    return out


def _rain_buffer(rng, frames: int):
    import numpy as np

    base = rng.standard_normal(frames).astype(np.float32)
    body = _lowpass(base, 91) * 0.9
    patter = _lowpass(rng.standard_normal(frames).astype(np.float32),
                      7) * 0.18
    return body + patter


def _wind_buffer(rng, frames: int, rate: int):
    import numpy as np

    base = _lowpass(rng.standard_normal(frames).astype(np.float32),
                    301)
    t = np.arange(frames, dtype=np.float32) / rate
    # One whole swell cycle per buffer: the loop stays seamless.
    swell = 0.55 + 0.45 * np.sin(2 * math.pi * t / _AMBIENT_SECONDS)
    return base * swell * 1.4


def _cafe_buffer(rng, frames: int, rate: int):
    import numpy as np

    base = rng.standard_normal(frames).astype(np.float32)
    murmur = (_lowpass(base, 47) - _lowpass(base, 601)) * 0.8
    low = _lowpass(rng.standard_normal(frames).astype(np.float32),
                   2401) * 0.35
    out = murmur + low
    # Sparse soft "clinks" — cup-and-saucer punctuation.
    for _ in range(rng.integers(6, 12)):
        freq = float(rng.uniform(900, 2400))
        dur = float(rng.uniform(0.05, 0.14))
        n = int(dur * rate)
        start = int(rng.integers(0, max(1, frames - n)))
        tt = np.arange(n, dtype=np.float32) / rate
        burst = (np.sin(2 * math.pi * freq * tt)
                 * np.exp(-tt * 40.0) * 0.06)
        out[start:start + n] += burst.astype(np.float32)
    return out


def _generate_scene(scene: str):
    """Synthesise one loop of a scene: (float32 samples, rate).
    Raises when numpy is unavailable (monkeypatchable)."""
    import numpy as np

    rate = _AMBIENT_RATE
    frames = int(_AMBIENT_SECONDS * rate)
    rng = np.random.default_rng(
        abs(hash((scene, "visor-ambient"))) % (2 ** 32))
    if scene == "rain":
        buf = _rain_buffer(rng, frames)
    elif scene == "wind":
        buf = _wind_buffer(rng, frames, rate)
    elif scene == "cafe":
        buf = _cafe_buffer(rng, frames, rate)
    elif scene == "storm":
        buf = (_rain_buffer(rng, frames) * 0.8
               + _wind_buffer(rng, frames, rate) * 0.7)
    elif scene == "cosy_cafe":
        buf = (_rain_buffer(rng, frames) * 0.55
               + _cafe_buffer(rng, frames, rate) * 0.9)
    elif scene == "windy_cafe":
        buf = (_wind_buffer(rng, frames, rate) * 0.6
               + _cafe_buffer(rng, frames, rate) * 0.9)
    else:  # full_mix
        buf = (_rain_buffer(rng, frames) * 0.6
               + _wind_buffer(rng, frames, rate) * 0.55
               + _cafe_buffer(rng, frames, rate) * 0.85)
    return _loop_ready(_normalise(buf, 0.7), rate), rate


def _player_main(samples, rate: int, stop_event) -> None:
    """Loop the buffer until stopped (runs in a thread)."""
    try:
        import sounddevice as sd
    except Exception:  # noqa: BLE001 — the play action checks first
        return
    try:
        while not stop_event.is_set():
            sd.play(samples, rate)
            waited = 0.0
            total = len(samples) / rate
            while waited < total and not stop_event.is_set():
                time.sleep(0.2)
                waited += 0.2
            sd.stop()
    except Exception:  # noqa: BLE001 — a dead thread = silence
        return


def _start_player(samples, rate: int, stop_event):
    """Start the loop thread (monkeypatchable)."""
    thread = threading.Thread(target=_player_main,
                              args=(samples, rate, stop_event),
                              daemon=True, name="visor-ambient")
    thread.start()
    return thread


def _halt_playback() -> None:
    try:
        import sounddevice as sd

        sd.stop()
    except Exception:  # noqa: BLE001
        pass


def _stop_ambient_locked() -> str | None:
    """Stop whatever is playing; returns its label or None.
    Caller holds _AMBIENT_LOCK."""
    if _AMBIENT["stop"] is None:
        return None
    label = _AMBIENT["label"]
    _AMBIENT["gen"] += 1
    _AMBIENT["stop"].set()
    timer = _AMBIENT["timer"]
    if timer is not None:
        timer.cancel()
    _AMBIENT.update(stop=None, thread=None, scene=None, label=None,
                    started=None, until=None, timer=None)
    _halt_playback()
    return label


def _auto_stop(gen: int) -> None:
    with _AMBIENT_LOCK:
        if _AMBIENT["gen"] == gen and _AMBIENT["stop"] is not None:
            _stop_ambient_locked()


def ambient_scene(action: str = "status", scene: str = "rain",
                  sleep_minutes: int = 0) -> str:
    """Play, stop or report a generated ambient soundscape."""
    action = (action or "status").strip().lower()
    if action not in ("play", "stop", "status"):
        return ("Ambient actions are 'play', 'stop' and "
                "'status'.")
    if action == "status":
        with _AMBIENT_LOCK:
            if _AMBIENT["stop"] is None:
                return "No ambient scene is playing."
            started = _AMBIENT["started"]
            until = _AMBIENT["until"]
            label = _AMBIENT["label"]
        mins_in = max(0, int((time.time() - started) // 60))
        line = (f"{label} is playing — started at "
                f"{datetime.fromtimestamp(started):%H:%M}, about "
                f"{mins_in} minute(s) in.")
        if until is not None:
            left = max(0, int((until - time.time()) // 60))
            line += (f" The sleep timer stops it in about "
                     f"{left} minute(s).")
        else:
            line += " No sleep timer is set — it'll play until you stop it."
        return line
    if action == "stop":
        with _AMBIENT_LOCK:
            started = _AMBIENT["started"]
            label = _stop_ambient_locked()
        if label is None:
            return "No ambient scene is playing — nothing to stop."
        mins = max(0, int((time.time() - started) // 60)) if started else 0
        return (f"Stopped the {label} soundscape — it had been "
                f"playing for about {mins} minute(s).")
    # play
    key = (scene or "rain").strip().lower()
    if key not in _SCENE_LABELS:
        return ("Ambient scenes are: rain, wind, cafe, storm, "
                "cosy_cafe, windy_cafe and full_mix.")
    try:
        sleep = int(sleep_minutes)
    except (TypeError, ValueError):
        return "sleep_minutes must be a whole number of minutes."
    sleep = max(0, min(_AMBIENT_MAX_SLEEP, sleep))
    try:
        samples, rate = _generate_scene(key)
    except ImportError:
        return ("I can't make the soundscape — the audio library "
                "(numpy) isn't available on this machine.")
    except Exception as e:  # noqa: BLE001
        return f"I couldn't generate the {key} soundscape: {e}"
    try:
        import sounddevice  # noqa: F401 — presence probe
    except Exception as e:  # noqa: BLE001
        return (f"I can't play the soundscape — the audio library "
                f"isn't available ({e}).")
    with _AMBIENT_LOCK:
        previous = _stop_ambient_locked()
        _AMBIENT["gen"] += 1
        gen = _AMBIENT["gen"]
        stop_event = threading.Event()
        thread = _start_player(samples, rate, stop_event)
        now = time.time()
        _AMBIENT.update(stop=stop_event, thread=thread, scene=key,
                        label=_SCENE_LABELS[key], started=now,
                        until=(now + sleep * 60) if sleep else None,
                        timer=None)
        if sleep:
            timer = threading.Timer(sleep * 60, _auto_stop,
                                    args=(gen,))
            timer.daemon = True
            timer.start()
            _AMBIENT["timer"] = timer
    lead = (f"Switching from {previous} — " if previous else "")
    timer_line = (f"It'll fade out by itself in {sleep} minute(s) "
                  f"(the sleep timer)."
                  if sleep else
                  "It'll play until you tell me to stop.")
    return (f"{lead}Playing '{_SCENE_LABELS[key]}' now — a "
            f"generated loop of shaped noise, not a recording. "
            f"{timer_line}")
