"""Extractive text summarisation — VYUK's own code (R8-3, sumy PORT).

sumy (miso-belica/sumy, Apache-2.0) was measured and NOT adopted:
its base closure pulls nltk + breadability + docopt-ng +
lxml-html-clean + pycountry + requests (plus defusedxml / click /
joblib / regex / tqdm via nltk) — roughly ten new packages for an
algorithm that is ~150 lines of stdlib — and its sentence tokenizer
needs nltk ``punkt`` data, which ships in NO wheel and would have
to be downloaded at runtime (forbidden by the tree's discipline)
or operator-provisioned as a second data surface. The algorithm
(TextRank, Mihalcea & Tarau 2004) is ported here as own code:
sentence split -> word-overlap similarity graph -> PageRank ->
top-N sentences in original document order. Deterministic, no
network, no files, no state, no model, no bundled data.

The one deliberate limitation: the stopword list is English.
Sentence splitting is punctuation-based and works on any text,
but the ranking only discounts English stopwords, so a
non-English transcript ranks on raw word overlap instead — it
still returns the most-connected sentences, just with noisier
scores. That is stated, not hidden.
"""

from __future__ import annotations

import math
import re
from core.funnel.tools_registry_core import _funnel_only_fn


# TextRank damping factor (Mihalcea & Tarau 2004).
_DAMPING = 0.85
_MAX_ITERATIONS = 50
_CONVERGENCE = 1e-4
# A sentence is never split after one of these (case-insensitive,
# trailing period stripped before the check).
_ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "st", "sr", "jr", "vs", "etc", "e.g",
    "i.e", "u.s", "u.k", "a.m", "p.m", "no", "fig", "approx",
    "prof", "rev", "hon", "capt", "lt", "col", "gen", "sgt",
})

_STOPWORDS = frozenset("""
a about above after again against all am an and any are as at be
because been before being below between both but by could did do
does doing down during each few for from further had has have
having he her here hers herself him himself his how i if in into
is it its itself just me more most my myself no nor not of off on
once only or other our ours ourselves out over own same she should
so some such than that the their theirs them themselves then there
these they this those through to too under until up very was we
were what when where which while who whom why will with you your
yours yourself yourselves
""".split())

# A sentence ends at . ! or ? when the next non-space character
# starts a new sentence (capital, digit, open quote/paren) or the
# line ends. Newlines are sentence boundaries on their own:
# meeting transcripts are one utterance per line.
_SENTENCE_END = re.compile(r"[.!?]+[\"'\)\]]*\s*(?=[A-Z0-9\"'\(\[]|$)")
_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def split_sentences(text: str) -> list[str]:
    """Split text into sentences (newlines are boundaries too).

    Abbreviations (Mr., e.g., U.S., ...) and decimals (3.5) never
    end a sentence. Leading bullets, dashes and quotes are
    stripped. Exact duplicates are dropped, keeping the first
    occurrence — a transcript that repeats "yeah" five times does
    not get "yeah" five times in its summary.
    """
    sentences: list[str] = []
    seen: set[str] = set()
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip(" \t-•*\"'").strip()
        if not line:
            continue
        start = 0
        for match in _SENTENCE_END.finditer(line):
            end = match.end()
            # The piece keeps its closing quote (if any) but not the
            # trailing whitespace; the guard checks use the bare
            # punctuation position.
            piece_end = end
            while piece_end > start and line[piece_end - 1] in " \t":
                piece_end -= 1
            punct_end = piece_end
            while punct_end > start and line[punct_end - 1] in "\"'":
                punct_end -= 1
            # Guard: abbreviation or decimal before the punctuation.
            before = line[start:punct_end]
            word_match = re.search(r"([A-Za-z]+(?:\.[A-Za-z]+)*)\.$",
                                   before)
            if word_match:
                word = word_match.group(1).lower()
                # Abbreviation ("Mr.", "e.g.", "U.S.") or a single
                # initial ("J. R. R. Tolkien") never ends a sentence.
                if (word in _ABBREVIATIONS
                        or (len(word) == 1 and word.isalpha())):
                    continue
            prev_char = line[punct_end - 1] if punct_end > 0 else ""
            if prev_char == ".":
                # Decimal: digit on both sides of the period.
                after = line[end:end + 1]
                before_char = line[punct_end - 2] if punct_end >= 2 else ""
                if before_char.isdigit() and after.isdigit():
                    continue
            piece = line[start:piece_end].strip(" \t-•*\"'").strip()
            if piece:
                key = piece.lower()
                if key not in seen:
                    seen.add(key)
                    sentences.append(piece)
            start = end
        tail = line[start:].strip(" \t-•*\"'").strip()
        if tail:
            key = tail.lower()
            if key not in seen:
                seen.add(key)
                sentences.append(tail)
    return sentences


def _content_words(sentence: str) -> list[str]:
    """Lowercased words minus stopwords (the ranking vocabulary)."""
    return [w for w in _WORD.findall(sentence.lower())
            if w not in _STOPWORDS]


def _similarity(words_i: list[str], words_j: list[str]) -> float:
    """TextRank sentence similarity (Mihalcea & Tarau 2004)."""
    if not words_i or not words_j:
        return 0.0
    common = len(set(words_i) & set(words_j))
    if common == 0:
        return 0.0
    denom = math.log(len(words_i)) + math.log(len(words_j))
    if denom <= 0:
        return common / float(len(words_i) + len(words_j))
    return common / denom


def extractive_summary(text: str, max_sentences: int = 5) -> dict:
    """Summarise text by picking its most-connected sentences.

    TextRank over the sentence similarity graph (damping 0.85,
    power iteration to 1e-4 or 50 rounds), sparse adjacency so a
    long transcript stays fast. The top ``max_sentences`` are
    returned in ORIGINAL document order, not rank order — a
    summary reads like the meeting, not like a leaderboard.
    Ties break by position (earlier sentence wins). Fewer
    sentences than requested returns them all; empty text
    returns an empty summary, honestly.
    """
    try:
        wanted = int(max_sentences)
    except (TypeError, ValueError):
        wanted = 5
    wanted = max(1, min(20, wanted))
    sentences = split_sentences(text)
    total = len(sentences)
    if total == 0:
        return {"summary": "", "sentences": [], "sentence_count": 0,
                "total_sentences": 0, "method": "extractive-textrank"}
    if total <= wanted:
        chosen = list(sentences)
        return {"summary": " ".join(chosen), "sentences": chosen,
                "sentence_count": total, "total_sentences": total,
                "method": "extractive-textrank"}
    word_lists = [_content_words(s) for s in sentences]
    # Sparse adjacency: edges[i] = [(j, weight)] for j != i.
    edges: list[list[tuple[int, float]]] = [[] for _ in range(total)]
    out_weight = [0.0] * total
    for i in range(total):
        for j in range(total):
            if i == j:
                continue
            w = _similarity(word_lists[i], word_lists[j])
            if w > 0:
                edges[i].append((j, w))
                out_weight[i] += w
    scores = [1.0] * total
    for _ in range(_MAX_ITERATIONS):
        new_scores = []
        max_delta = 0.0
        for i in range(total):
            rank = 1.0 - _DAMPING
            for j, w in edges[i]:
                if out_weight[j] > 0:
                    rank += _DAMPING * (w / out_weight[j]) * scores[j]
            new_scores.append(rank)
            max_delta = max(max_delta, abs(rank - scores[i]))
        scores = new_scores
        if max_delta < _CONVERGENCE:
            break
    ranked = sorted(range(total),
                    key=lambda i: (-scores[i], i))
    chosen_idx = sorted(ranked[:wanted])
    chosen = [sentences[i] for i in chosen_idx]
    return {"summary": " ".join(chosen), "sentences": chosen,
            "sentence_count": len(chosen), "total_sentences": total,
            "method": "extractive-textrank"}


def summarise_text(text: str, max_sentences: int = 5) -> dict:
    """Public wrapper: extractive summary of caller-supplied text.

    Pure computation — no network, no files, no state. Empty or
    whitespace-only text returns an empty summary, honestly.
    """
    if not str(text or "").strip():
        return {"summary": "", "sentences": [], "sentence_count": 0,
                "total_sentences": 0, "method": "extractive-textrank"}
    return extractive_summary(text, max_sentences=max_sentences)


@_funnel_only_fn
def _tool_summarise_text(text: str = "", max_sentences: int = 5) -> str:
    """Funnel handler: summarise the caller's text, in plain words."""
    if not str(text or "").strip():
        return ("There is nothing to summarise — give the text to "
                "summarise.")
    result = summarise_text(text, max_sentences=max_sentences)
    return (f"Summary ({result['sentence_count']} of "
            f"{result['total_sentences']} sentences):\n"
            f"{result['summary']}")


def register_all(registry) -> None:
    """Register the summarise_text tool (R8-3, sumy port)."""
    registry.register(
        name="summarise_text",
        description="Summarise a piece of text by picking the sentences that carry it (extractive summarisation, VYUK's own TextRank port — no model, no network, nothing leaves the machine). Give the text and how many sentences you want (default 5); the summary keeps the chosen sentences in their original order. Use when the user asks for the gist of a transcript, a long message, or pasted text.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string",
                         "description": "The text to summarise"},
                "max_sentences": {"type": "integer",
                                  "description": "How many sentences "
                                  "the summary may hold (1-20, "
                                  "default 5)"},
            },
            "required": ["text"],
        },
        handler=_tool_summarise_text,
    )
