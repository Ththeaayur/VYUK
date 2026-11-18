"""CR-2 edit bay, native half (Creative track, wave 29).

Native SRT/VTT parsing + retime / restyle / sync helpers (the pysubs2
job, done natively — subtitle formats are plain text; pysubs2 itself
is NOT depended on) and a native timeline spine (clips, cuts,
transitions as a JSON model) that EXPORTS OTIO JSON — the neutral
spine that bypasses Resolve's Studio scripting gate via file import
(file import only, no scripting bridge).

QUEUED behind the shared FFmpeg binary-provenance audit (LGPL vs GPL
build, codec patents — owed): silence-cutting (Auto-Editor), MoviePy
glue, burn-in. No ffmpeg binary ships, no ffmpeg-dependent code here.
MLT is a pattern note only (per-module GPL audit owed). Manim is a
pattern note only (generated Manim code is an arbitrary-code surface
-> INV-2 confirm gate, no Manim dep).

Stdlib only.
"""

import json
import re

_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


def _parse_time(text):
    """Parse 'HH:MM:SS,mmm' (SRT) or 'HH:MM:SS.mmm' (VTT) -> seconds."""
    text = text.strip().replace(",", ".")
    parts = text.split(":")
    if len(parts) != 3:
        raise ValueError("bad timestamp: %r" % text)
    hours = int(parts[0])
    minutes = int(parts[1])
    sec_parts = parts[2].split(".")
    seconds = int(sec_parts[0])
    millis = int((sec_parts[1] + "000")[:3]) if len(sec_parts) > 1 else 0
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0


def _format_time(seconds, vtt=False):
    """Seconds -> 'HH:MM:SS,mmm' (SRT) or 'HH:MM:SS.mmm' (VTT)."""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours, rem = divmod(total_ms, 3600000)
    minutes, rem = divmod(rem, 60000)
    secs, millis = divmod(rem, 1000)
    sep = "." if vtt else ","
    return "%02d:%02d:%02d%s%03d" % (hours, minutes, secs, sep, millis)


def parse_subtitles(text):
    """Parse SRT or VTT text into a list of cue dicts.

    Cue: {"index": int, "start": float seconds, "end": float seconds,
    "text": str}. VTT cue ids and NOTE/STYLE blocks are skipped; the
    WEBVTT header is skipped. Raises ValueError on malformed blocks.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[0].strip().startswith("WEBVTT"):
        lines = lines[1:]
    blocks = []
    current = []
    for line in lines:
        if line.strip() == "":
            if current:
                blocks.append(current)
                current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)
    cues = []
    for block in blocks:
        # Drop VTT NOTE/STYLE/REGION blocks.
        if block[0].startswith(("NOTE", "STYLE", "REGION")):
            continue
        time_idx = None
        for i, line in enumerate(block):
            if "-->" in line:
                time_idx = i
                break
        if time_idx is None:
            raise ValueError("subtitle block without a time line: %r"
                             % (block,))
        start_s, end_s = [t.strip() for t in
                          block[time_idx].split("-->")]
        text_lines = block[time_idx + 1:]
        cues.append({
            "index": len(cues) + 1,
            "start": _parse_time(start_s),
            "end": _parse_time(end_s),
            "text": "\n".join(text_lines),
        })
    return cues


def retime(cues, shift=0.0, scale=1.0):
    """Shift (seconds) and/or scale cue times. Times clamp at zero."""
    out = []
    for cue in cues:
        start = max(0.0, cue["start"] * scale + shift)
        end = max(start, cue["end"] * scale + shift)
        new = dict(cue)
        new["start"] = start
        new["end"] = end
        out.append(new)
    return out


def sync_to(cues, target_first_start):
    """Shift all cues so the first cue starts at ``target_first_start``."""
    if not cues:
        return []
    return retime(cues, shift=target_first_start - cues[0]["start"])


def restyle(cues, strip_tags=True, max_line_len=None):
    """Restyle cue text: strip markup tags and/or rewrap lines.

    ``max_line_len`` rewraps each cue's text to lines no longer than
    the limit (word wrap; overlong single words stay whole).
    """
    out = []
    for cue in cues:
        text = cue["text"]
        if strip_tags:
            text = _TAG_RE.sub("", text)
        if max_line_len:
            wrapped = []
            for line in text.split("\n"):
                words = line.split()
                cur = ""
                for word in words:
                    if cur and len(cur) + 1 + len(word) > max_line_len:
                        wrapped.append(cur)
                        cur = word
                    else:
                        cur = (cur + " " + word) if cur else word
                if cur:
                    wrapped.append(cur)
            text = "\n".join(wrapped)
        new = dict(cue)
        new["text"] = text
        out.append(new)
    return out


def format_srt(cues):
    """Serialise cues back to SRT text."""
    parts = []
    for cue in cues:
        parts.append("%d\n%s --> %s\n%s"
                     % (cue["index"], _format_time(cue["start"]),
                        _format_time(cue["end"]), cue["text"]))
    return "\n\n".join(parts) + ("\n" if parts else "")


# ------------------------------------------------------------ timeline
def make_timeline(name, fps=30):
    """Create an empty timeline spine: {"name", "fps", "tracks": []}."""
    return {"name": name, "fps": fps, "tracks": []}


def add_track(timeline, name):
    """Append a track to the spine. Returns the track dict."""
    track = {"name": name, "clips": []}
    timeline["tracks"].append(track)
    return track


def add_clip(track, clip_id, source, start, duration, transition=None):
    """Append a clip. ``start``/``duration`` are seconds on the timeline.

    ``transition`` is a dict like {"type": "dissolve", "duration": 0.5}
    or None (a hard cut). No media is touched here — the spine is a
    model; rendering is the queue's job (cr_render_v576).
    """
    clip = {"id": clip_id, "source": source, "start": start,
            "duration": duration, "transition": transition}
    track["clips"].append(clip)
    return clip


def _rational(seconds, fps):
    return {"OTIO_SCHEMA": "RationalTime.1", "rate": fps,
            "value": int(round(seconds * fps))}


def export_otio_json(timeline):
    """Export the spine as OTIO JSON (Timeline.1).

    OTIO JSON is the neutral spine: DaVinci Resolve imports it as a
    file (no scripting bridge, no Studio-EULA claim). Clips carry a
    source_range; transitions are recorded as neighbour metadata on
    the following clip because OTIO Transition objects need resolved
    in/out frames this spine does not invent.
    """
    fps = timeline.get("fps", 30)
    tracks = []
    for track in timeline["tracks"]:
        children = []
        for clip in track["clips"]:
            child = {
                "OTIO_SCHEMA": "Clip.1",
                "name": clip["id"],
                "source_range": {
                    "OTIO_SCHEMA": "TimeRange.1",
                    "start_time": _rational(clip["start"], fps),
                    "duration_time": _rational(clip["duration"], fps),
                },
                "metadata": {"source": clip["source"]},
            }
            if clip.get("transition"):
                child["metadata"]["transition"] = clip["transition"]
            children.append(child)
        tracks.append({"OTIO_SCHEMA": "Track.1", "name": track["name"],
                       "children": children})
    return json.dumps({"OTIO_SCHEMA": "Timeline.1",
                       "name": timeline["name"], "tracks": tracks},
                      indent=2)
