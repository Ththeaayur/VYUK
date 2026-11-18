"""Scrub/summarize family of the confirm gate (split B18).

Pure functions only: sensitive-key masking, secret detection, and the
masked args summary. No module state lives here.
"""

import math
import re


# Exact sensitive key NAMES (v5.45, H22b): a value under a key that
# IS one of these is masked whatever it looks like. This replaces
# the old substring test ("pin" in "spinning", "auth" in "author"
# masked innocent values): matching is on the whole normalised key
# (lowercased, non-alphanumerics stripped), never a substring.
_SENSITIVE_KEYS = frozenset({
    "password", "passwd", "pwd", "token", "accesstoken",
    "refreshtoken", "secret", "apikey", "credential",
    "credentials", "auth", "otp", "pin", "passcode",
    "privatekey", "sessiontoken",
})

# Decision-critical keys (v5.45, H22a): for file/app operations the
# human is deciding on the path / target / command / URL — those
# values are shown IN FULL, never clipped at 60 chars. The total
# budget is kept sane by clipping NON-critical fields harder
# (_NONCRITICAL_CLIP) and dropping them when the budget runs out.
FULL_FIDELITY_KEYS = frozenset({
    "path", "filepath", "file", "filename", "file_name",
    "target", "destination", "dest", "source", "folder",
    "directory", "dir", "command", "url", "app", "app_name",
    "application", "link", "address", "to", "recipient",
})

_NONCRITICAL_CLIP = 30

_KNOWN_SECRET_PREFIXES = (
    "sk-", "sk-proj-", "ghp_", "gho_", "ghu_", "github_pat_",
    "AIza", "xoxb-", "xoxp-", "xoxs-", "AKIA", "-----BEGIN",
    "eyJ",  # base64 JSON — a JWT's header segment
)

_B64_RUN = re.compile(r"[A-Za-z0-9+/=_-]{32,}")
_HEX_RUN = re.compile(r"\b[0-9a-fA-F]{32,}\b")


def _looks_like_secret(text: str) -> bool:
    """True when a VALUE's shape says secret (H22b), wherever the
    value appears: a known key prefix, a long base64/hex run, or a
    single high-entropy whitespace-free token. Ordinary prose,
    paths and short values never match."""
    t = (text or "").strip()
    if not t:
        return False
    if any(t.startswith(p) for p in _KNOWN_SECRET_PREFIXES):
        return True
    if _HEX_RUN.search(t) and len(t) >= 32 and " " not in t:
        return True
    if " " not in t and len(t) >= 32 and _B64_RUN.fullmatch(t):
        has_mix = (any(c.islower() for c in t)
                   and any(c.isupper() for c in t)
                   and any(c.isdigit() for c in t))
        if has_mix:
            return True
    if " " not in t and 20 <= len(t) <= 512:
        # Shannon entropy over the token: random keys sit > ~4.2
        # bits/char; English words and repeated padding sit below.
        freq: dict[str, int] = {}
        for ch in t:
            freq[ch] = freq.get(ch, 0) + 1
        n = len(t)
        entropy = -sum((c / n) * math.log2(c / n) for c in freq.values())
        charset = sum([
            any(c.islower() for c in t), any(c.isupper() for c in t),
            any(c.isdigit() for c in t),
            any(not c.isalnum() for c in t)])
        if entropy >= 4.2 and charset >= 3:
            return True
    return False





# ---------------------------------------------------------------------------
# Args summary (masked — safe to show on screen and to log)
# ---------------------------------------------------------------------------


def _is_sensitive(key: str) -> bool:
    """Exact-key sensitivity (H22b): the normalised key must BE a
    sensitive name — substrings no longer mask ("spinning" carries
    "pin" and "author" carries "auth"; both are innocent)."""
    normalised = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return normalised in _SENSITIVE_KEYS


def summarize_args(args: dict | None, limit: int = 300,
                   verbatim_keys=frozenset()) -> str:
    """One-line, masked summary of tool args for display and logging.

    v5.45 (H22) presentation rules:
    * Masking is by VALUE SHAPE + exact sensitive key names
      (_looks_like_secret / _is_sensitive): a secret-looking value
      is masked wherever it appears; an innocent value under a
      lookalike key ("spinning", "author") is shown.
    * Decision-critical keys (FULL_FIDELITY_KEYS — paths, targets,
      commands, URLs, app names) and caller-named `verbatim_keys`
      are shown IN FULL, first, exempt from clipping (the v5.3 H1
      run_command precedent, generalised): a clipped path is an
      unreadable decision.
    * Non-critical fields are clipped harder (_NONCRITICAL_CLIP)
      and dropped once the `limit` budget is spent, so the total
      stays sane without ever eating the critical fields. When
      critical fields alone exceed `limit`, they still win — the
      summary is then longer than `limit`, deliberately."""
    if not args:
        return "(no arguments)"
    critical_parts = []
    normal_parts = []
    for key in sorted(args, key=str):
        value = args[key]
        text = value if isinstance(value, str) else repr(value)
        if _is_sensitive(key) or _looks_like_secret(text):
            # Masked values are never decision-critical display:
            # they ride with the normal fields, as •••.
            normal_parts.append(f"{key}=•••")
            continue
        if key in verbatim_keys or str(key).lower() in FULL_FIDELITY_KEYS:
            critical_parts.append(f"{key}={text}")
            continue
        if len(text) > _NONCRITICAL_CLIP:
            text = text[: _NONCRITICAL_CLIP - 3] + "..."
        normal_parts.append(f"{key}={text}")
    head = ", ".join(critical_parts)
    if not normal_parts:
        return head if head else "(no arguments)"
    budget = limit - (len(head) + 2 if head else 0)
    kept = []
    used = 0
    for part in normal_parts:
        extra = len(part) + (2 if kept else 0)
        if kept and used + extra > budget:
            continue
        if not kept and len(part) > max(budget, _NONCRITICAL_CLIP + 8):
            part = part[: max(budget, 8) - 3] + "..." if budget >= 8 else part
        kept.append(part)
        used += extra
    summary = ", ".join(kept)
    if head:
        return f"{head}, {summary}" if summary else head
    return summary
