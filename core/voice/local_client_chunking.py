"""chunking concern extracted from core/voice/local_client.py (refactor Phase 1 step 2). Behaviour identical; every name here stays importable from core.local_client."""

import re


_ABBREV_WORDS = {
    "mr", "mrs", "ms", "dr", "st", "jr", "sr", "prof", "gen", "sen",
    "rep", "gov", "sgt", "capt", "lt", "col", "fig", "figs", "no",
    "nos", "vol", "vs", "approx", "appt", "dept", "est", "min", "max",
    "e.g", "i.e", "etc", "u.s", "u.k", "a.m", "p.m",
}
_SENTENCE_END_RE = re.compile(r"[.!?…]+[\"'”’)\]]*\s+")
def _split_sentences(text: str) -> list[str]:
    """Splits a reply into speakable sentence chunks (pure function)."""
    text = " ".join((text or "").split())  # normalize whitespace/newlines
    if not text:
        return []
    parts: list[str] = []
    start = 0
    for match in _SENTENCE_END_RE.finditer(text):
        candidate = text[start:match.end()].strip()
        tail = candidate.rstrip(".\"'”’)]…")
        last_word = tail.rsplit(" ", 1)[-1].lower() if tail else ""
        if last_word in _ABBREV_WORDS:
            continue  # "Dr. Smith" — the boundary is inside the sentence
        parts.append(candidate)
        start = match.end()
    tail_text = text[start:].strip()
    if tail_text:
        parts.append(tail_text)
    merged: list[str] = []
    pending = ""
    for part in parts:
        part = f"{pending} {part}".strip() if pending else part
        pending = ""
        if len(part.split()) < 2:
            pending = part  # too small to speak alone — glue to the next
            continue
        merged.append(part)
    if pending:
        if merged:
            merged[-1] = f"{merged[-1]} {pending}"
        else:
            merged.append(pending)
    return merged
_BREAK_NONE = 0
_BREAK_SENTENCE = 1
_BREAK_PARAGRAPH = 2
_BREAK_CLAUSE = 3
_BREAK_RANK = {_BREAK_NONE: 0, _BREAK_CLAUSE: 1,
               _BREAK_SENTENCE: 2, _BREAK_PARAGRAPH: 3}
_RUNT_TOKENS = 15
_MONSTER_TOKENS = 400
_TARGET_MIN_TOKENS = 100
_TARGET_MAX_TOKENS = 200
def _speech_token_estimate(text: str) -> int:
    """The token proxy for the speech bands: ceil(words x 4/3)."""
    words = len((text or "").split())
    return -(-words * 4 // 3)  # ceil for non-negative counts
_CLAUSE_SPLIT_RE = re.compile(
    r"[,;]\s+"                                # comma/semicolon boundary
    r"|\s+[—–-]{1,2}\s+"                      # dash boundary
    r"|\s+(?=(?:and|but|or|nor|which|while|because|although|though"
    r"|when|where)\s)"                        # conjunction boundary
)
def _clause_segments(text: str) -> list[str]:
    """Splits one sentence at clause boundaries, delimiters kept
    with the segment they END (a conjunction starts its segment)."""
    segments: list[str] = []
    start = 0
    for match in _CLAUSE_SPLIT_RE.finditer(text):
        end = match.end()
        # A punctuation boundary belongs to the LEFT segment; a
        # conjunction lookahead split already sits before the word.
        piece = text[start:end].strip()
        if piece:
            segments.append(piece)
        start = end
    tail = text[start:].strip()
    if tail:
        segments.append(tail)
    return segments or [text]
def _split_monster_chunk(text: str) -> list[str]:
    """Splits one over-_MONSTER_TOKENS sentence toward the target
    band (see the section header). Clause segments are the split
    unit; a single clause still over the band's top is cut by
    words into ~150-token slices as the last resort. The pieces
    concatenate (single spaces) to the original sentence."""
    segments: list[str] = []
    for segment in _clause_segments(text):
        if _speech_token_estimate(segment) <= _TARGET_MAX_TOKENS:
            segments.append(segment)
            continue
        words = segment.split()
        # ~150 estimated tokens = 112 words at the 4/3 proxy.
        step = 112
        segments.extend(
            " ".join(words[i:i + step])
            for i in range(0, len(words), step))
    pieces: list[str] = []
    current = ""
    for segment in segments:
        if not current:
            current = segment
            continue
        joined = f"{current} {segment}"
        if _speech_token_estimate(current) < _TARGET_MIN_TOKENS:
            current = joined  # still short of the band — keep eating
        elif _speech_token_estimate(joined) <= _TARGET_MAX_TOKENS:
            current = joined
        else:
            pieces.append(current)
            current = segment
    if current:
        pieces.append(current)
    return pieces or [text]
def _merge_runts_in_paragraph(
        chunks: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Merges chunks under _RUNT_TOKENS into a neighbour WITHOUT
    crossing a paragraph wall: a runt glues forward onto the next
    chunk (the merged chunk keeps the runt's break strength, so a
    paragraph-start runt merged forward still opens its topic);
    a trailing runt glues backward onto the previous chunk."""
    merged: list[tuple[str, int]] = []
    pending: tuple[str, int] | None = None
    for text, brk in chunks:
        if pending is not None:
            text = f"{pending[0]} {text}"
            brk = pending[1]
            pending = None
        if _speech_token_estimate(text) < _RUNT_TOKENS:
            pending = (text, brk)
            continue
        merged.append((text, brk))
    if pending is not None:
        if merged:
            prev_text, prev_brk = merged[-1]
            merged[-1] = (f"{prev_text} {pending[0]}", prev_brk)
        else:
            merged.append(pending)
    return merged
def _rechunk_for_speech(text: str) -> list[tuple[str, int]]:
    """The M1 pre-pass: the reply's speakable chunks as
    (chunk_text, break_before) in reply order (pure function).
    Paragraphs (blank-line separated in the source) are processed
    independently — monsters split, runts merged — and never mix.
    _split_sentences() itself is unchanged for its other callers;
    this is the speech path's own chunker built on top of it."""
    if not text or not text.strip():
        return []
    paragraphs = re.split(r"\n\s*\n", text.strip())
    out: list[tuple[str, int]] = []
    for para_index, paragraph in enumerate(paragraphs):
        sentences = _split_sentences(paragraph)
        chunks: list[tuple[str, int]] = []
        for sent_index, sentence in enumerate(sentences):
            if _speech_token_estimate(sentence) > _MONSTER_TOKENS:
                pieces = _split_monster_chunk(sentence)
            else:
                pieces = [sentence]
            for piece_index, piece in enumerate(pieces):
                if piece_index > 0:
                    # v5.58: a genuine clause-split continuation
                    # gets its own break strength (clause pause at
                    # the consumer) — NOT _BREAK_NONE. The reply's
                    # very first chunk and the emphasis sub-chunks
                    # built in _speak still get _BREAK_NONE (see
                    # _BREAK_CLAUSE above), so the distinction is
                    # made here, at chunk creation, where it is
                    # unambiguous: only monster-split pieces ever
                    # take this branch. Runt-merging below preserves
                    # a merged chunk's START break, so a merged
                    # chunk never acquires a clause break it did
                    # not start with.
                    brk = _BREAK_CLAUSE
                elif not out and not chunks:
                    brk = _BREAK_NONE  # the reply's very first chunk
                elif sent_index == 0 and para_index > 0:
                    brk = _BREAK_PARAGRAPH
                else:
                    brk = _BREAK_SENTENCE
                chunks.append((piece, brk))
        out.extend(_merge_runts_in_paragraph(chunks))
    return out
_EMPHASIS_SPAN_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
def _emphasis_chunks(sentence: str) -> list[tuple[str, bool]]:
    """Splits one raw sentence on its **emphasis** spans (v5.17,
    M14): returns [(text, emphasized), ...] in order, the span
    markers stripped. Text outside spans is unemphasized. An
    unbalanced "**" never forms a span — the sentence passes
    through as a single unemphasized chunk, markers and all (the
    normalizer's own markdown pass deals with the stray markers,
    exactly as it did before emphasis existed). Pure function."""
    if not sentence:
        return []
    chunks: list[tuple[str, bool]] = []
    pos = 0
    for match in _EMPHASIS_SPAN_RE.finditer(sentence):
        if match.start() > pos:
            chunks.append((sentence[pos:match.start()], False))
        chunks.append((match.group(1), True))
        pos = match.end()
    if pos < len(sentence):
        chunks.append((sentence[pos:], False))
    return [(text, emph) for text, emph in chunks if text] or [
        (sentence, False)]
_SSMD_BREAK_RE = re.compile(r'<break\s+time="(\d+)ms"\s*/>',
                            re.IGNORECASE)
_SSMD_PROSODY_RE = re.compile(
    r'<prosody\s+rate="([\d.]+)">(.*?)</prosody>',
    re.IGNORECASE | re.DOTALL)
_SSMD_EMPHASIS_TAG_RE = re.compile(r"<emphasis>(.*?)</emphasis>",
                                   re.IGNORECASE | re.DOTALL)
def _ssmd_break_strength(ms: int) -> int:
    """Maps a <break time="Nms"/> to a break strength by the
    v5.58 bands, using core/voice/mouth.py's band EDGES (single
    source of truth — no numbers copied here): under the
    sentence band's floor is a clause break, under the
    paragraph band's floor a sentence break, else paragraph."""
    from . import mouth  # Phase 3 V1b: moved to core/voice/

    if ms < mouth.SENTENCE_PAUSE_MIN_MS:
        return _BREAK_CLAUSE
    if ms < mouth.PARAGRAPH_PAUSE_MIN_MS:
        return _BREAK_SENTENCE
    return _BREAK_PARAGRAPH
def _rechunk_with_breaks(text: str) -> list[tuple[str, int]]:
    """_rechunk_for_speech with brain <break> tags honoured: the
    text is split at each break tag, each segment is rechunked
    on its own, and the first chunk of every later segment
    takes the STRONGER (by _BREAK_RANK) of its rechunked break
    and the tag's break — a tag never downgrades a structural
    paragraph break to a clause pause. (In practice a segment's
    first chunk always rechunks to _BREAK_NONE, so the tag
    governs; the rank is the guard if chunking ever changes.)
    The reply's very first chunk keeps its rechunked break
    (_BREAK_NONE); the consumer's heard_any guard additionally
    never pauses before the first played unit. Tags between
    segments are consumed as delimiters, never spoken. Plain
    text (no break tag) never reaches this function — see
    _speak."""
    parts: list[tuple[str, int | None]] = []
    pos = 0
    pending: int | None = None
    for match in _SSMD_BREAK_RE.finditer(text):
        parts.append((text[pos:match.start()], pending))
        pending = int(match.group(1))
        pos = match.end()
    parts.append((text[pos:], pending))
    out: list[tuple[str, int]] = []
    for seg_text, tag_ms in parts:
        chunks = _rechunk_for_speech(seg_text)
        if not chunks:
            continue
        if out and tag_ms is not None:
            tag_brk = _ssmd_break_strength(tag_ms)
            first_text, first_brk = chunks[0]
            if _BREAK_RANK[tag_brk] > _BREAK_RANK[first_brk]:
                chunks[0] = (first_text, tag_brk)
        out.extend(chunks)
    return out
def _ssmd_rate(value: str) -> float:
    """Clamps a <prosody rate> into the shipped Kokoro pace
    band 0.8–1.2 — the same band _kokoro_speed() clamps the
    configured pace into; garbage degrades to the natural
    rate, never a crash or a mute."""
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return 1.0
    if rate != rate:  # NaN
        return 1.0
    return min(1.2, max(0.8, rate))
def _speed_units_for_span(text: str, base_rate: float,
                          force_emphasis: bool = False
                          ) -> list[tuple[str, float]]:
    """_emphasis_chunks mapped to speeds: an emphasized span
    speaks at the shipped emphasis pace (mouth's
    EMPHASIS_SPEED, 0.92 — the same value _speak already used
    for ** spans), anything else at `base_rate`. With
    force_emphasis (inside an <emphasis> tag) the whole span
    is emphasized, ** markers included or not."""
    from . import mouth  # Phase 3 V1b: moved to core/voice/

    out = []
    for sub_text, emphasized in _emphasis_chunks(text):
        if force_emphasis or emphasized:
            out.append((sub_text, mouth.EMPHASIS_SPEED))
        else:
            out.append((sub_text, base_rate))
    return out
def _emphasis_tag_units(text: str,
                        base_rate: float) -> list[tuple[str, float]]:
    """Splits text on <emphasis>...</emphasis> spans (speed
    0.92 inside, `base_rate` outside), then on ** spans inside
    each piece — document order preserved, tags stripped."""
    out: list[tuple[str, float]] = []
    pos = 0
    for match in _SSMD_EMPHASIS_TAG_RE.finditer(text):
        if match.start() > pos:
            out.extend(_speed_units_for_span(text[pos:match.start()],
                                             base_rate))
        out.extend(_speed_units_for_span(match.group(1), base_rate,
                                         force_emphasis=True))
        pos = match.end()
    if pos < len(text):
        out.extend(_speed_units_for_span(text[pos:], base_rate))
    return out
def _prosody_tag_units(raw: str) -> list[tuple[str, float]]:
    """One raw chunk as [(text, speed_mult)] honouring the
    SSMD-subset speed spans in document order: <prosody
    rate="R"> (clamped 0.8–1.2), <emphasis> (0.92), and the
    shipped ** spans (0.92). Tags are stripped from the
    returned texts; a chunk with no tag spans returns exactly
    the old _emphasis_chunks expansion mapped to speeds, so
    plain-text unit building is unchanged. The speed
    multiplier rides the EXISTING per-unit mechanism (Kokoro
    composes it into its speed; Piper deliberately ignores
    multipliers, v5.17) — no new speed control is invented."""
    out: list[tuple[str, float]] = []
    pos = 0
    for match in _SSMD_PROSODY_RE.finditer(raw):
        if match.start() > pos:
            out.extend(_emphasis_tag_units(raw[pos:match.start()], 1.0))
        out.extend(_emphasis_tag_units(match.group(2),
                                       _ssmd_rate(match.group(1))))
        pos = match.end()
    if pos < len(raw):
        out.extend(_emphasis_tag_units(raw[pos:], 1.0))
    return out or [(raw, 1.0)]
