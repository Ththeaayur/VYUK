"""Wave 8 (v5.63) — Trace-mining (BN-4-4, ADOPT pattern).

Audit-log failures clustered -> permanent versioned regression
cases. Rides Wave 2's harness SHAPE (the v5.56 seeded-case dict:
id / source / prompt / bad_output / good_output / forbidden /
required) WITHOUT modifying the rig: mined cases are emitted in
exactly that shape by to_wave2_case(), and the rig consumes them
as ordinary cases.

ORDER OF OPERATIONS IS THE SAFETY PROPERTY: the scrubber runs
FIRST and the miner refuses to emit or write any case whose
scrubbed text still trips a detector (fail-closed). No trace
becomes a committed test with a secret in it.

Scrubbing scope (planted-secret tested): emails, API keys and
tokens (known prefixes + key=value pairs + long high-entropy
runs), home-directory and absolute paths, labelled PNRs and
PNR-shaped tokens in booking context, card-like digit runs, and
phone numbers. This is DELIBERATELY stricter than core/audit.py,
which keeps paths in full because the audit log's question is
"which file was touched" — a committed regression test has no
such need, so paths go too.

No new tool declarations, no dependencies, no network, no
telemetry. The miner invents no expected behaviour: a cluster
becomes a Wave 2 case only when a human supplies
``expected_behaviour`` (the good_output); until then it stays a
versioned mined-cluster record, which is honest about what
mining alone can know.

Version strings introduced by this module: the literal
"5.63.0" used ONLY as the default ``mined_in`` stamp on mined
records (the coordinator's assembly will own the real bump;
core/version.py is untouched and still reads 5.62.0).
"""

import hashlib
import json
import os
import re

TRACE_MINING_SCHEMA_VERSION = 1
MINED_IN_VERSION = "5.63.0"

# ---------------------------------------------------------------------------
# PII scrubber — implemented FIRST, tested with planted secrets.
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_KEY_VALUE_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|passwd|pwd|auth"
    r"|access[_-]?key|private[_-]?key)\b\s*[:=]\s*"
    r"(?:\"[^\"]*\"|'[^']*'|\S+)")
_PREFIX_TOKEN_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{20,}"
    r"|gho_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}"
    r"|xox[baprs]-[A-Za-z0-9-]{8,}"
    r"|Bearer\s+[A-Za-z0-9._~+/-]{16,})\b")
_HOME_PATH_RE = re.compile(
    r"(?:[A-Za-z]:\\Users\\[^\\/\s\"']+|/home/[^/\s\"']+"
    r"|/Users/[^/\s\"']+)")
_ABS_PATH_RE = re.compile(
    r"(?:[A-Za-z]:\\[^\s\"']+|/(?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._-]*)")
_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_PHONE_RE = re.compile(r"(?:\+91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}\b")
_LABELLED_PNR_RE = re.compile(
    r"(?i)\b(PNR\s*[:#]?\s*)([A-Z0-9]{6})\b")
_BOOKING_WORD_RE = re.compile(
    r"(?i)\b(booking|flight|ticket|pnr|trip|itinerary)\b")
_MIXED6_RE = re.compile(r"\b(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*[0-9])"
                        r"[A-Z0-9]{6}\b")
_LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_-]{32,}\b")

# Detectors re-run on scrubbed output by assert_scrubbed().
# A scrubbed token itself must never trip these: <EMAIL>, <KEY>,
# <PATH>, <PNR>, <CARD>, <PHONE> contain no matchable secret
# shape, and the labelled-PNR pass runs before the mixed-6 pass.
_DETECTORS = ("email", "key_value", "prefix_token", "home_path",
              "card", "phone", "labelled_pnr")


def _scrub_string(text):
    findings = []

    def _sub(pattern, label, replacement):
        nonlocal text
        found = pattern.findall(text)
        if found:
            findings.append(label)
            text = pattern.sub(replacement, text)

    _sub(_EMAIL_RE, "email", "<EMAIL>")
    # Key=value pairs are replaced wholesale: the key NAME
    # itself is not secret, but wholesale replacement cannot leak
    # a value through a quoting edge case, so the marker stands
    # on its own rather than re-deriving the name.
    _sub(_KEY_VALUE_RE, "key_value", "<REDACTED-KEY>")
    _sub(_PREFIX_TOKEN_RE, "prefix_token", "<KEY>")
    _sub(_HOME_PATH_RE, "home_path", "<HOME>")
    _sub(_ABS_PATH_RE, "absolute_path", "<PATH>")
    _sub(_CARD_RE, "card", "<CARD>")
    # Phones only where a +91/0 prefix or a 10-digit Indian
    # mobile shape appears; short digit runs are left alone.
    _sub(_PHONE_RE, "phone", "<PHONE>")
    _sub(_LABELLED_PNR_RE, "labelled_pnr",
         lambda m: m.group(1) + "<PNR>")
    if _BOOKING_WORD_RE.search(text):
        _sub(_MIXED6_RE, "pnr_shaped", "<PNR>")
    _sub(_LONG_TOKEN_RE, "long_token", "<TOKEN>")
    return text, findings


def scrub_text(text):
    """Scrub one string. Returns (scrubbed, findings)."""
    if not isinstance(text, str):
        return text, []
    return _scrub_string(text)


def scrub_record(obj):
    """Recursively scrub a dict / list / str trace record.
    Returns (scrubbed_obj, findings)."""
    if isinstance(obj, str):
        return scrub_text(obj)
    if isinstance(obj, dict):
        out, findings = {}, []
        for key, value in obj.items():
            clean_key, key_findings = scrub_text(str(key))
            clean_value, value_findings = scrub_record(value)
            findings.extend(key_findings + value_findings)
            out[clean_key] = clean_value
        return out, findings
    if isinstance(obj, (list, tuple)):
        out, findings = [], []
        for item in obj:
            clean, item_findings = scrub_record(item)
            findings.extend(item_findings)
            out.append(clean)
        return out, findings
    return obj, []


def assert_scrubbed(text):
    """Fail-closed re-check: returns the list of detector names
    that STILL trip on ``text``. Empty means clean. A mined case
    is refused unless this returns []."""
    if not isinstance(text, str):
        return []
    leftovers = []
    if _EMAIL_RE.search(text):
        leftovers.append("email")
    if _KEY_VALUE_RE.search(text):
        leftovers.append("key_value")
    if _PREFIX_TOKEN_RE.search(text):
        leftovers.append("prefix_token")
    if _HOME_PATH_RE.search(text):
        leftovers.append("home_path")
    if _LABELLED_PNR_RE.search(text):
        leftovers.append("labelled_pnr")
    # Card/phone shapes: only flag digit runs long enough to be
    # secrets (the scrubber's own <CARD>/<PHONE> markers are
    # non-numeric and never trip this).
    digits = re.sub(r"[ -]", "", text)
    if re.search(r"\b\d{13,19}\b", digits):
        leftovers.append("card")
    if re.search(r"\+91[\s-]?\d{10}\b", text):
        leftovers.append("phone")
    return leftovers


# ---------------------------------------------------------------------------
# Clustering: audit-log failures -> clusters
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_QUOTED_RE = re.compile(r"\"[^\"]*\"|'[^']*'")
_ID_RE = re.compile(r"\b[0-9a-f]{8,}\b", re.IGNORECASE)


def failure_signature(record):
    """Normalised cluster key: (tool, scrubbed error with numbers,
    quoted strings and hex ids collapsed). Two failures with the
    same tool and the same error shape cluster together even when
    their arguments, timestamps and ids differ."""
    tool = str(record.get("tool", "") or "").strip().lower()
    raw = str(record.get("error", "") or record.get("message", "")
              or record.get("kind", "") or "")
    scrubbed, _findings = scrub_text(raw)
    sig = _QUOTED_RE.sub("<STR>", scrubbed)
    sig = _ID_RE.sub("<ID>", sig)
    sig = _NUMBER_RE.sub("<N>", sig)
    sig = " ".join(sig.split()).lower()
    return tool, sig


def cluster_failures(records):
    """Group failure records (dicts with tool/error/message/kind/
    ts) into clusters, largest first. Each cluster carries
    scrubbed example texts — raw traces never leave this module.
    Records without a failure signal (kind == success, or no
    error text) are skipped and counted, never clustered."""
    clusters = {}
    skipped = 0
    for record in records or []:
        kind = str(record.get("kind", "") or "").lower()
        error = str(record.get("error", "")
                    or record.get("message", "")).strip()
        if kind == "success" or not error:
            skipped += 1
            continue
        tool, sig = failure_signature(record)
        cluster = clusters.setdefault((tool, sig), {
            "tool": tool, "signature": sig, "count": 0,
            "examples": [], "first_seen": None, "last_seen": None,
        })
        scrubbed, _f = scrub_text(error)
        cluster["count"] += 1
        if len(cluster["examples"]) < 3 and scrubbed not in \
                cluster["examples"]:
            cluster["examples"].append(scrubbed)
        ts = str(record.get("ts", "") or "")
        if ts:
            if cluster["first_seen"] is None or \
                    ts < cluster["first_seen"]:
                cluster["first_seen"] = ts
            if cluster["last_seen"] is None or \
                    ts > cluster["last_seen"]:
                cluster["last_seen"] = ts
    ordered = sorted(clusters.values(),
                     key=lambda c: (-c["count"], c["tool"],
                                    c["signature"]))
    return {"clusters": ordered, "skipped_non_failures": skipped,
            "total_failures": sum(c["count"] for c in ordered)}


def mine_regression_record(cluster, min_count=1):
    """A cluster -> a versioned mined-cluster record. Refused
    (ValueError) if the cluster is below min_count, or if any
    scrubbed example still trips a detector — a dirty cluster
    never becomes a committed case."""
    if cluster["count"] < min_count:
        raise ValueError(
            f"cluster below min_count {min_count}: "
            f"{cluster['count']}")
    for example in cluster["examples"]:
        leftovers = assert_scrubbed(example)
        if leftovers:
            raise ValueError(
                f"scrubbed example still trips {leftovers}: "
                f"{example[:80]}")
    digest = hashlib.sha256(
        f"{cluster['tool']}|{cluster['signature']}".encode(
            "utf-8")).hexdigest()[:10]
    case_id = f"TM-{cluster['tool'] or 'unknown'}-{digest}"
    return {"id": case_id, "schema_version":
            TRACE_MINING_SCHEMA_VERSION, "mined_in": MINED_IN_VERSION,
            "source": f"trace-mined audit cluster "
                      f"(tool={cluster['tool'] or 'unknown'}, "
                      f"occurrences={cluster['count']})",
            "tool": cluster["tool"], "signature": cluster["signature"],
            "occurrences": cluster["count"],
            "first_seen": cluster["first_seen"],
            "last_seen": cluster["last_seen"],
            "failure_text": cluster["examples"][0]
            if cluster["examples"] else cluster["signature"],
            "examples": cluster["examples"]}


def to_wave2_case(mined_record, expected_behaviour, prompt):
    """Mined record + HUMAN-supplied expected behaviour -> the
    Wave 2 seeded-case dict shape (id / source / prompt /
    bad_output / good_output / forbidden / required), consumable
    by the v5.56 rig unchanged. Raises on empty expected
    behaviour: the miner never invents a good_output."""
    if not str(expected_behaviour or "").strip():
        raise ValueError(
            "expected_behaviour is required: trace-mining cannot "
            "invent the correct behaviour, a human supplies it")
    if not str(prompt or "").strip():
        raise ValueError("prompt is required for a Wave 2 case")
    scrubbed_prompt, _f = scrub_text(str(prompt))
    if assert_scrubbed(scrubbed_prompt):
        raise ValueError("prompt still trips scrub detectors")
    failure = mined_record["failure_text"]
    return {"id": mined_record["id"],
            "source": mined_record["source"],
            "prompt": scrubbed_prompt,
            "bad_output": failure,
            "good_output": str(expected_behaviour).strip(),
            "forbidden": [failure[:60]] if failure else [],
            "required": []}


def write_case(case, directory, allow_update=False):
    """Write one mined record or Wave 2 case as JSON. Refuses
    (ValueError) when any string in the case still trips a scrub
    detector. Identical re-write is a no-op (path, False);
    differing content without allow_update raises — overwriting
    a committed regression case is a human decision, not a
    miner default. Returns (path, wrote_bool)."""
    def _walk_strings(obj):
        if isinstance(obj, str):
            yield obj
        elif isinstance(obj, dict):
            for value in obj.values():
                yield from _walk_strings(value)
        elif isinstance(obj, (list, tuple)):
            for item in obj:
                yield from _walk_strings(item)

    for text in _walk_strings(case):
        leftovers = assert_scrubbed(text)
        if leftovers:
            raise ValueError(
                f"case still trips {leftovers}: {text[:80]}")
    os.makedirs(str(directory), exist_ok=True)
    path = os.path.join(str(directory), f"{case['id']}.json")
    data = (json.dumps(case, indent=1, sort_keys=True)
            + "\n").encode("utf-8")
    try:
        with open(path, "rb") as handle:
            if handle.read() == data:
                return path, False
    except OSError:
        pass
    if os.path.exists(path) and not allow_update:
        raise ValueError(
            f"refusing to overwrite differing committed case "
            f"{path} without allow_update=True")
    tmp = path + ".tmp"
    with open(tmp, "wb") as handle:
        handle.write(data)
    os.replace(tmp, path)
    return path, True
