"""Split from core/anticipation.py (skills split B19).
Move only: code below is verbatim from the old module apart from
module-level import lines.
"""

from __future__ import annotations


from collections import Counter
from dataclasses import dataclass



# ---------------------------------------------------------------------------
# AN-2 PrefixSpan — ORIGINAL core (see module docstring §4).
# No third-party sequence-mining import, no database access.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrequentPattern:
    """One frequent sequence: ``pattern`` (tuple of items, in
    order) occurring as a subsequence in ``support`` sequences."""
    pattern: tuple
    support: int


def _projected_database(sequences, item):
    """Suffixes of `sequences` after the FIRST occurrence of
    `item` in each sequence (sequences without `item` drop out).
    This is the standard PrefixSpan projection for single-item
    sequences; support counts sequences, never occurrences."""
    projected = []
    for seq in sequences:
        for idx, value in enumerate(seq):
            if value == item:
                projected.append(seq[idx + 1:])
                break
    return projected


def prefix_span(sequences, min_support: int, max_pattern_len=None):
    """Mine frequent subsequences (PrefixSpan, own implementation).

    Args:
        sequences: caller-supplied iterable of sequences (each a
            list/tuple of hashable items). This function never
            reads a database, a file, or any other store.
        min_support: minimum number of sequences a pattern must
            occur in (>= 1). Patterns are counted per sequence.
        max_pattern_len: optional cap on pattern length.

    Returns a list of FrequentPattern, sorted by (-support,
    -length, pattern) for deterministic output. Raises ValueError
    for min_support < 1 or a non-positive max_pattern_len, and
    TypeError for malformed sequences.
    """
    if not isinstance(min_support, int) or isinstance(min_support, bool):
        raise ValueError("min_support must be an integer >= 1")
    if min_support < 1:
        raise ValueError("min_support must be >= 1")
    if max_pattern_len is not None:
        if (not isinstance(max_pattern_len, int)
                or isinstance(max_pattern_len, bool)
                or max_pattern_len < 1):
            raise ValueError("max_pattern_len must be a positive integer")
    cleaned = []
    for seq in sequences:
        if isinstance(seq, (str, bytes)):
            raise TypeError("each sequence must be a list/tuple of items, not a string")
        try:
            cleaned.append(list(seq))
        except TypeError as exc:
            raise TypeError("each sequence must be iterable") from exc

    results: list = []

    def _mine(prefix, projected):
        if max_pattern_len is not None and len(prefix) >= max_pattern_len:
            return
        # Count each item once per projected sequence.
        counts: Counter = Counter()
        for seq in projected:
            for item in set(seq):
                counts[item] += 1
        for item in sorted(counts, key=lambda v: (str(type(v)), str(v))):
            support = counts[item]
            if support < min_support:
                continue
            pattern = tuple(prefix) + (item,)
            results.append(FrequentPattern(pattern=pattern, support=support))
            _mine(list(pattern), _projected_database(projected, item))

    _mine([], cleaned)
    results.sort(key=lambda p: (-p.support, -len(p.pattern), str(p.pattern)))
    return results


#: Readable alias; same function, same guarantees.
mine_frequent_sequences = prefix_span
