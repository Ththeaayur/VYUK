"""CR-1 music core, native half (Creative track, wave 29).

Native MIDI read/write/inspect helpers (the MIDI file format is fully
specified — no mido dep) + symbolic helpers (note lists, tempo and
time-signature handling).

DISCHARGED by R8-7: Basic Pitch transcription (core/basic_pitch.py
— own numpy + ONNX port, operator-supplied model). Still QUEUED:
librosa analysis (weights + heavy deps + 8 GB pilot). Spleeter
weights: never (provenance + licence). Tone.js samples: never
bundled without per-sample licences.

Stdlib only (struct).
"""

import struct

_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F",
               "F#", "G", "G#", "A", "A#", "B"]
_NOTE_BASE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def note_name_to_midi(name):
    """'C4' -> 60. Supports sharps (#) and flats (b), octave -1..9."""
    match = None
    text = name.strip()
    if len(text) >= 2 and text[0].upper() in _NOTE_BASE:
        letter = text[0].upper()
        rest = text[1:]
        accidental = 0
        if rest.startswith("#"):
            accidental = 1
            rest = rest[1:]
        elif rest.startswith("b"):
            accidental = -1
            rest = rest[1:]
        try:
            octave = int(rest)
        except ValueError:
            raise ValueError("bad note name: %r" % name)
        return (octave + 1) * 12 + _NOTE_BASE[letter] + accidental
    raise ValueError("bad note name: %r" % name)


def midi_to_note_name(number):
    """60 -> 'C4'. Uses sharps."""
    if not 0 <= number <= 127:
        raise ValueError("MIDI note out of range: %r" % (number,))
    octave, pc = divmod(number, 12)
    return "%s%d" % (_NOTE_NAMES[pc], octave - 1)


def _encode_vlq(value):
    """Encode an integer as a MIDI variable-length quantity."""
    if value < 0:
        raise ValueError("VLQ cannot be negative")
    groups = [value & 0x7F]
    value >>= 7
    while value:
        groups.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(groups))


def _decode_vlq(data, pos):
    value = 0
    while True:
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not (byte & 0x80):
            return value, pos


def write_midi(notes, division=480, tempo_bpm=120.0,
               time_signature=(4, 4)):
    """Write a Format-1, single-track MIDI file from a note list.

    ``notes``: list of {"pitch" (int or name), "start" (ticks),
    "duration" (ticks), "velocity" (default 100), "bends" (optional
    list of (tick, 14-bit pitch-bend value 0-16383, centre 8192),
    +/-2 semitones at 4096 ticks per semitone)}. Returns bytes.
    Pitch-bend events are written on channel 0; read_midi skips
    them (it reports notes only).
    """
    events = []  # (tick, order, bytes)
    us_per_quarter = int(round(60_000_000 / tempo_bpm))
    events.append((0, 0, b"\xff\x51\x03" + us_per_quarter.to_bytes(3, "big")))
    num, den = time_signature
    den_power = {1: 0, 2: 1, 4: 2, 8: 3, 16: 4}.get(den)
    if den_power is None:
        raise ValueError("unsupported time-signature denominator: %r"
                         % (den,))
    events.append((0, 1, bytes([0xFF, 0x58, 0x04, num, den_power, 24, 8])))
    for note in notes:
        pitch = note["pitch"]
        if isinstance(pitch, str):
            pitch = note_name_to_midi(pitch)
        vel = note.get("velocity", 100)
        for bend_tick, bend_value in note.get("bends") or ():
            if not 0 <= bend_value <= 16383:
                raise ValueError("pitch-bend value out of range: %r"
                                 % (bend_value,))
            events.append((bend_tick, 1,
                           bytes([0xE0, bend_value & 0x7F,
                                  (bend_value >> 7) & 0x7F])))
        events.append((note["start"], 2,
                       bytes([0x90, pitch, vel])))
        events.append((note["start"] + note["duration"], 3,
                       bytes([0x80, pitch, 0])))
    events.sort(key=lambda e: (e[0], e[1]))
    track = bytearray()
    last_tick = 0
    for tick, _order, payload in events:
        track += _encode_vlq(tick - last_tick)
        track += payload
        last_tick = tick
    track += _encode_vlq(0) + b"\xff\x2f\x00"  # end of track
    header = b"MThd" + struct.pack(">IHHH", 6, 1, 1, division)
    return header + b"MTrk" + struct.pack(">I", len(track)) + bytes(track)


def read_midi(data):
    """Parse MIDI bytes. Returns {"format", "division", "tempo_bpm",
    "time_signature", "notes"} where notes are {"pitch", "start",
    "duration", "velocity"} dicts. Supports note on/off, tempo and
    time-signature meta events; other events are skipped by length."""
    if len(data) < 14 or data[:4] != b"MThd":
        raise ValueError("not a MIDI file (missing MThd)")
    _hlen, fmt, ntrks, division = struct.unpack(">IHHH", data[4:14])
    pos = 8 + _hlen
    tempo_bpm = 120.0
    time_signature = (4, 4)
    open_notes = {}  # (channel, pitch) -> (start_tick, velocity)
    notes = []
    for _ in range(ntrks):
        if data[pos:pos + 4] != b"MTrk":
            raise ValueError("expected MTrk chunk")
        tlen = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        end = pos + 8 + tlen
        pos += 8
        tick = 0
        running = None
        while pos < end:
            delta, pos = _decode_vlq(data, pos)
            tick += delta
            status = data[pos]
            if status < 0x80:
                if running is None:
                    raise ValueError("running status without prior event")
                status = running
            else:
                pos += 1
                if status < 0xF0:
                    running = status
            if status == 0xFF:
                mtype = data[pos]
                pos += 1
                length, pos = _decode_vlq(data, pos)
                payload = data[pos:pos + length]
                pos += length
                if mtype == 0x51 and length == 3:
                    us = int.from_bytes(payload, "big")
                    tempo_bpm = 60_000_000 / us
                elif mtype == 0x58 and length >= 2:
                    den = 2 ** payload[1]
                    time_signature = (payload[0], den)
                continue
            if status in (0xF0, 0xF7):
                length, pos = _decode_vlq(data, pos)
                pos += length
                continue
            channel = status & 0x0F
            kind = status & 0xF0
            if kind in (0xC0, 0xD0):
                pos += 1  # one data byte
                continue
            d1 = data[pos]
            d2 = data[pos + 1]
            pos += 2
            if kind == 0x90 and d2 > 0:
                open_notes[(channel, d1)] = (tick, d2)
            elif kind == 0x80 or (kind == 0x90 and d2 == 0):
                key = (channel, d1)
                if key in open_notes:
                    start, vel = open_notes.pop(key)
                    notes.append({"pitch": d1, "start": start,
                                  "duration": tick - start,
                                  "velocity": vel})
        pos = end
    notes.sort(key=lambda n: (n["start"], n["pitch"]))
    return {"format": fmt, "division": division, "tempo_bpm": tempo_bpm,
            "time_signature": time_signature, "notes": notes}


def ticks_to_seconds(ticks, tempo_bpm, division):
    """Convert ticks to seconds at a fixed tempo."""
    return ticks * (60.0 / tempo_bpm) / division
