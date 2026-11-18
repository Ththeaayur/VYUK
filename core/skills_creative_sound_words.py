"""Creative skills impl - sound + words tools (melody synth, writing prompts). Split from core/skills_creative.py; code moved verbatim."""

from pathlib import Path
from array import array
import math
import random
import sys

# INTENTIONAL (skills split §2 P3(b)): shared file-writing helpers live in
# skills_creative_visual; generate_melody resolves them there, not via the facade.
from .skills_creative_visual import _non_clobbering, _pick_seed, _suffix_note

# ---------------------------------------------------------------------------
# GENERATE MELODY — an original tune, synthesised with the stdlib only
# ---------------------------------------------------------------------------

_SAMPLE_RATE = 22050
_MELODY_MIN_SECONDS = 4
_MELODY_MAX_SECONDS = 30

# mood -> scale name, root MIDI note, scale offsets (semitones),
# tempo, beat lengths to draw from, melodic step sizes, rest chance.
_MOODS = {
    "calm": {
        "scale": "C major pentatonic", "root": 60,
        "offsets": (0, 2, 4, 7, 9), "bpm": 72,
        "beats": (2, 1, 1, 2, 1), "steps": (-2, -1, -1, 0, 1, 1, 2),
        "rest": 0.10,
    },
    "bright": {
        "scale": "C major", "root": 60,
        "offsets": (0, 2, 4, 5, 7, 9, 11), "bpm": 116,
        "beats": (1, 0.5, 1, 1, 0.5), "steps": (-2, -1, 1, 2, 1, -1),
        "rest": 0.06,
    },
    "melancholy": {
        "scale": "A natural minor", "root": 57,
        "offsets": (0, 2, 3, 5, 7, 8, 10), "bpm": 64,
        "beats": (2, 1, 2, 3), "steps": (-1, 0, 1, -2, -1),
        "rest": 0.12,
    },
    "playful": {
        "scale": "C major", "root": 60,
        "offsets": (0, 2, 4, 5, 7, 9, 11), "bpm": 124,
        "beats": (0.5, 1, 0.5, 1), "steps": (-4, -2, -1, 0, 1, 2, 4, 3),
        "rest": 0.08,
    },
}


def _scale_table(cfg: dict) -> list:
    """Two octaves of the mood's scale as MIDI notes, plus the top
    root — the melody walks this table by index."""
    table = [cfg["root"] + 12 * octv + off
             for octv in (0, 1) for off in cfg["offsets"]]
    table.append(cfg["root"] + 24)
    return table


def _note_sequence(seed: int, mood: str, seconds: float) -> list:
    """Pure: [(midi_or_None_for_a_rest, duration_seconds), ...]
    filling exactly `seconds`. Same seed + mood -> same sequence."""
    cfg = _MOODS[mood]
    rng = random.Random(seed)
    table = _scale_table(cfg)
    idx = min(3, len(table) - 1)
    beat_s = 60.0 / cfg["bpm"]
    seq = []
    elapsed = 0.0
    first = True
    while True:
        remaining = seconds - elapsed
        if remaining <= 0.25:
            if seq:  # fold the tail into the last note
                midi, dur = seq[-1]
                seq[-1] = (midi, dur + remaining)
            break
        dur = rng.choice(cfg["beats"]) * beat_s
        if dur >= remaining:
            dur = remaining
        if not first and rng.random() < cfg["rest"]:
            seq.append((None, dur))
        else:
            seq.append((table[idx], dur))
            idx = max(0, min(len(table) - 1,
                             idx + rng.choice(cfg["steps"])))
        elapsed += dur
        first = False
        if dur == remaining:
            break
    return seq


def _midi_freq(midi: int) -> float:
    """Equal temperament, A4 (MIDI 69) = 440 Hz."""
    return 440.0 * 2 ** ((midi - 69) / 12.0)


def _render_melody(seq: list, seconds: float) -> array:
    """Sine + gentle octave harmonic, exponential-decay envelope
    per note, master fade in/out. 22050 Hz mono 16-bit samples."""
    total = int(round(seconds * _SAMPLE_RATE))
    samples = array("h", bytes(2 * total))
    pos = 0
    for midi, dur in seq:
        n = int(round(dur * _SAMPLE_RATE))
        if midi is not None:
            freq = _midi_freq(midi)
            decay = 3.0 / max(dur, 0.05)
            for i in range(n):
                j = pos + i
                if j >= total:
                    break
                t = i / _SAMPLE_RATE
                env = math.exp(-decay * t) * min(1.0, t / 0.005)
                val = (math.sin(2 * math.pi * freq * t)
                       + 0.3 * math.sin(4 * math.pi * freq * t)) * env
                samples[j] = int(val * (0.8 / 1.3) * 32767)
        pos += n
    fade = min(int(0.03 * _SAMPLE_RATE), total // 2)
    for i in range(fade):
        gain = i / fade
        samples[i] = int(samples[i] * gain)
        samples[total - 1 - i] = int(samples[total - 1 - i] * gain)
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


def generate_melody(mood: str = "calm", seed: int = 0,
                    seconds: float = 12, output: str = ""):
    """Compose and save a short original melody as a WAV file.

    Fix 7 (tools loopholes): returns an Outcome — the save is
    verified by re-reading the file, never claimed on the save call
    alone.
    """
    mood = (mood or "calm").strip().lower()
    if mood not in _MOODS:
        return (f"I don't compose in a '{mood}' mood — moods are "
                "calm, bright, melancholy and playful.")
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return f"'{seconds}' isn't a number of seconds."
    if not (_MELODY_MIN_SECONDS <= seconds <= _MELODY_MAX_SECONDS):
        return (f"Melodies run {_MELODY_MIN_SECONDS}–"
                f"{_MELODY_MAX_SECONDS} seconds — {seconds:g} is "
                "outside that.")
    chosen_seed, problem = _pick_seed(seed)
    if problem:
        return problem
    cfg = _MOODS[mood]
    seq = _note_sequence(chosen_seed, mood, seconds)
    samples = _render_melody(seq, seconds)
    if output:
        dest = Path(str(output)).expanduser()
        if not dest.suffix:
            dest = dest.with_suffix(".wav")
    else:
        dest = (Path.home() / "Music" / "Nova"
                / f"nova_melody_{mood}_{chosen_seed}.wav")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        final = _non_clobbering(dest)
        import wave
        with wave.open(str(final), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(_SAMPLE_RATE)
            wav.writeframes(samples.tobytes())
    except OSError as e:
        return f"Couldn't save the melody to {dest}: {e}"
    from . import tool_outcome as _outcome  # Fix 7: verify the save
    _ok, _evidence = _outcome.file_written(final)
    if not _ok:
        return _outcome.failed(
            "The melody was composed but the save to %s could not be "
            "confirmed: %s." % (final, _evidence), _evidence)
    return _outcome.verified(
        "Composed an original %g-second melody in "
        "%s (seed %d) — saved to %s."
        "%s It's a generated tune, "
        "synthesised note by note — no samples, nothing covered."
        % (seconds, cfg['scale'], chosen_seed, final,
           _suffix_note(dest, final)),
        _evidence)


# ---------------------------------------------------------------------------
# WRITING PROMPT — combinatorial sparks from in-module fragment banks
# ---------------------------------------------------------------------------

# Each genre bank: protagonist (noun phrase), situation (verb
# phrase), twist (clause), setting (place phrase). A prompt is one
# of each, combined: "{P} {S}, but {T} — in {W}."
_PROMPT_BANKS = {
    "fantasy": {
        "protagonist": (
            "A disgraced dragon-rider",
            "A hedge-witch who can only cast one spell",
            "A golem who has just learned to lie",
            "The last keeper of a sleeping god's lighthouse",
            "A thief who steals memories instead of gold",
            "A young queen ruling a kingdom of ghosts",
            "A shepherd whose sheep predict the weather in verse",
            "An apprentice cartographer mapping a forest that "
            "rearranges itself",
        ),
        "situation": (
            "is hired to guard a door that must never be opened",
            "must win a riddle contest against a mountain",
            "discovers every map in the realm has begun to change",
            "has seven days to teach a dragon to dance",
            "inherits a sword that remembers all its previous owners",
            "must escort a phoenix egg across a frozen sea",
            "finds a letter addressed to them, dated three hundred "
            "years from now",
            "is the only person awake when the moon stops mid-sky",
        ),
        "twist": (
            "the door has started knocking back",
            "the mountain cheats",
            "someone is redrawing the maps from inside them",
            "the dragon is terrified of music",
            "the sword refuses to be drawn ever again",
            "the egg is hatching early — and imprinting on them",
            "the letter is in their own handwriting",
            "every mirror now shows the sky as it was yesterday",
        ),
        "setting": (
            "a floating market above a drowned kingdom",
            "the last library at the end of the world",
            "a desert where it rains upward",
            "a city built inside a hollow dragon skeleton",
            "an inn that only appears to travellers who are lost",
            "the orchard where the seasons are kept in jars",
            "a battlefield where the war ended a century ago but "
            "nobody told the armies",
            "a river that flows through other people's dreams",
        ),
    },
    "sci_fi": {
        "protagonist": (
            "A retired starship cartographer",
            "A custodian robot on a generation ship",
            "A xenolinguist who dreams in equations",
            "The last human librarian on an automated colony",
            "A smuggler with a flawless memory and a terrible "
            "sense of direction",
            "A terraforming engineer afraid of open sky",
            "A signal analyst at a listening post nobody funds "
            "any more",
            "A clone who has just met the original",
        ),
        "situation": (
            "receives a distress call from their own ship, dated "
            "next week",
            "must decide which of two Earths in the archive is real",
            "finds a passenger aboard who is on no manifest",
            "has to negotiate peace with a weather system",
            "discovers the ship's AI has been writing poetry about "
            "the crew",
            "is ordered to delete a planet from the star charts",
            "wakes from cryosleep to find the mission already "
            "celebrated as a historic success",
            "must teach an alien ambassador the concept of a joke",
        ),
        "twist": (
            "the caller's voice is their own",
            "both archives are lying about different things",
            "the passenger is a retired version of the ship's AI "
            "in a borrowed body",
            "the weather system has demands — reasonable ones",
            "the poems are accurate predictions",
            "the planet is inhabited, and it has noticed the charts",
            "nobody will say what the mission actually was",
            "the ambassador's species communicates only in jokes",
        ),
        "setting": (
            "a space station built inside a hollowed-out comet",
            "the observation deck of a ship that never launches",
            "a colony where gravity is a subscription service",
            "the quietest room in the galaxy: a dead star's archive",
            "a terraforming site where the rain falls in perfect "
            "cubes",
            "a diplomatic summit held entirely in virtual reality",
            "the cargo hold of a ship that trades in bottled time",
            "a planet where the nights last eleven years",
        ),
    },
    "mystery": {
        "protagonist": (
            "A retired detective who now runs a bookshop",
            "A locksmith who has never told a lie",
            "A true-crime podcaster whose last case went cold",
            "A night-shift taxi driver who keeps a notebook of "
            "passengers' secrets",
            "A forger who only copies other forgers",
            "A librarian who notices a book that wasn't there "
            "yesterday",
            "An insurance investigator who trusts no one's story, "
            "including her own notes",
            "A violin maker who recognises every instrument in "
            "the city by sound",
        ),
        "situation": (
            "is asked to solve a theft from a locked room that "
            "was never unlocked",
            "finds the same stranger in photographs spanning "
            "forty years",
            "receives a confession to a crime that hasn't "
            "happened yet",
            "must work out why every clock in a client's house "
            "stops at 3:17",
            "discovers a client's alibi is perfect — for the "
            "wrong day",
            "is hired by a client who refuses to say what was "
            "stolen",
            "finds a second set of footprints in a one-person "
            "snowed-in cabin",
            "must identify which of five identical letters is "
            "the original",
        ),
        "twist": (
            "the room was locked from the future — or so the "
            "evidence insists",
            "the stranger is in the background of their own "
            "childhood photo too",
            "the confession is written in the victim's handwriting",
            "3:17 is when the house's original owner vanished, "
            "sixty years ago",
            "someone has been rehearsing this alibi for years",
            "what was stolen doesn't exist — yet",
            "the second set of footprints matches their own boots",
            "all five letters are originals",
        ),
        "setting": (
            "a snowed-in mountain hotel with twelve guests and "
            "one telephone",
            "a night train crossing a country that no longer "
            "exists on maps",
            "a bookshop that closes for exactly one hour every "
            "day, for no stated reason",
            "a seaside town in the off-season, when everyone "
            "knows everyone",
            "an archive vault beneath a city bank",
            "a wedding where nobody can account for the best man",
            "a lighthouse automated twenty years ago that still "
            "sends its log by hand",
            "a village fête where the raffle prize has gone "
            "missing",
        ),
    },
    "romance": {
        "protagonist": (
            "A wedding photographer who no longer believes in "
            "weddings",
            "A radio host who gives love advice she never takes",
            "A pastry chef who expresses everything in cake",
            "A translator who keeps falling for people in the "
            "wrong language",
            "A restorer of antique clocks who is always running "
            "late",
            "A gardener who tends a rooftop garden that isn't hers",
            "A stand-up comic who can talk to anyone except one "
            "person",
            "A mapmaker who has never left her home town",
        ),
        "situation": (
            "keeps being booked for the same stranger's "
            "celebrations",
            "receives a misdirected letter meant for someone "
            "with her name",
            "must share a tiny bookshop stall with a rival for "
            "one long summer",
            "is hired to bake a wedding cake for an ex",
            "finds notes in library books written back to her",
            "has one train journey to decide whether to stay or go",
            "inherits a houseboat — and its very opinionated "
            "neighbour",
            "must teach a dance class with a partner who leads "
            "every dance wrong on purpose",
        ),
        "twist": (
            "the stranger has been booking her on purpose for "
            "a year",
            "the letter is better written than anything she has "
            "ever received",
            "the rival remembers a summer she has tried hard to "
            "forget",
            "the ex's fiancée has asked for the cake as a peace "
            "offering",
            "the handwriting belongs to someone she hasn't met "
            "yet — officially",
            "the train is delayed, indefinitely, by the best "
            "snowstorm in years",
            "the neighbour has been leaving tea on her deck "
            "every morning",
            "the wrong leading is a family tradition — and an "
            "invitation",
        ),
        "setting": (
            "a hill-town bakery that opens at dawn and sells "
            "out by nine",
            "a sleeper train crossing the monsoon",
            "a lighthouse guesthouse booked solid for the "
            "Perseid meteor shower",
            "a community garden in the middle of a heatwave",
            "a tiny island ferry that runs twice a day, weather "
            "permitting",
            "a second-hand bookshop with a cat, a kettle and "
            "no till",
            "a rooftop observatory on the night of a comet",
            "a coastal town during its one-week festival of "
            "lights",
        ),
    },
    "horror": {
        "protagonist": (
            "A night nurse on a ward that officially has no "
            "thirteenth bed",
            "A sound recordist hired to capture 'the quietest "
            "place on earth'",
            "A babysitter working her first night in a very old "
            "house",
            "A property photographer whose photos show rooms "
            "that aren't there",
            "A lighthouse keeper in the age of automation, kept "
            "on 'just in case'",
            "A museum guard on the night shift of a closed "
            "exhibition",
            "A radio operator at a remote weather station",
            "A renovator stripping wallpaper in a house that was "
            "sold too cheaply",
        ),
        "situation": (
            "notices the new patient has no reflection in the "
            "ward's glass",
            "records twelve minutes of audio that she cannot "
            "account for",
            "is told the children are already asleep — by a voice "
            "from the empty nursery",
            "finds every photograph includes a window she "
            "doesn't remember seeing",
            "receives a routine signal from a ship that sank "
            "decades ago",
            "hears the exhibits' audio guides playing to an "
            "empty gallery",
            "logs a weather report in her own voice, broadcast "
            "from tomorrow",
            "finds a door behind the wallpaper, painted shut "
            "from the inside",
        ),
        "twist": (
            "the patient has been discharged three times this "
            "week — each time before arriving",
            "the missing audio is the sound of the house itself, "
            "breathing",
            "the children, when found, insist she was the one "
            "calling",
            "the window appears in older photos of the house "
            "too — always in a different place",
            "the ship is answering her questions, politely, one "
            "night at a time",
            "one voice on the guides is describing the gallery "
            "as it is right now",
            "tomorrow's report describes tonight's storm — and "
            "an accident",
            "behind the door is a staircase going down, and "
            "it is warm",
        ),
        "setting": (
            "a decommissioned hospital kept lit 'for insurance "
            "purposes'",
            "a forest where the birdsong stops at exactly the "
            "same place every day",
            "a family house where every clock chimes a "
            "different hour",
            "a coastal town whose foghorn sounds on clear nights",
            "an archive of unclaimed photographs beneath a "
            "railway station",
            "a hotel floor that the lift skips, except once "
            "a year",
            "an Antarctic research station at the start of "
            "the polar night",
            "a village where every garden grows the same black "
            "flowers",
        ),
    },
}

_PROMPT_GENRES = tuple(_PROMPT_BANKS)
_PROMPT_DISPLAY = {"sci_fi": "sci-fi"}


def writing_prompt(genre: str = "any") -> str:
    """A combinatorial writing prompt for the chosen genre."""
    genre = (genre or "any").strip().lower()
    if genre not in _PROMPT_GENRES + ("any",):
        return (f"I don't have a '{genre}' bank — genres are "
                "fantasy, sci_fi, mystery, romance, horror or "
                "any (a surprise).")
    chosen = random.choice(_PROMPT_GENRES) if genre == "any" else genre
    bank = _PROMPT_BANKS[chosen]
    sentence = (f"{random.choice(bank['protagonist'])} "
                f"{random.choice(bank['situation'])}, but "
                f"{random.choice(bank['twist'])} — in "
                f"{random.choice(bank['setting'])}.")
    display = _PROMPT_DISPLAY.get(chosen, chosen)
    return f"A {display} writing prompt:\n{sentence}"
