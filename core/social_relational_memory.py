"""Social track (v5.73) — CP-5 relational memory overlays:
turning-points ledger (CP-5.1), preference evolution pilot
(CP-5.2), transparency (CP-5.4), forgetting (CP-5.5).

These overlay the Wave 6 vault; they do NOT mutate its
schema. The vault stays the store; this module is the
relational layer on a parallel store (INV-8).

Rules, enforced in code:
- A turning point is named by HIM, in his words. No function
  here decides an event was significant — significance is a
  field he supplies, never inferred.
- CP-5.2 pilot scope: THREE low-stakes domains only (food,
  music, leisure). Any other domain is rejected. The pilot
  stays a pilot: it never claims to know him.
- CP-5.5 forgetting, mirroring Wave 6 (90-day inferred TTL):
  inferred preferences expire 90 days without re-evidence;
  user-told preferences NEVER auto-expire. Episodes
  (turning points) decay after 180 days unless pinned or
  milestone — decay means ARCHIVED, never deleted (Wave 6
  pressure-valve semantics).
- CP-5.4 transparency: every preference record can explain
  itself in one sentence — source, date, expiry. If it
  cannot explain itself, it must not be used.
"""

import json
import os
from datetime import date, timedelta

DEFAULT_STORE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "data", "social_relational_v573.json")

PILOT_DOMAINS = ("food", "music", "leisure")
INFERRED_TTL_DAYS = 90
EPISODE_DECAY_DAYS = 180


def default_store_path():
    return os.path.normpath(DEFAULT_STORE)


def _load(path):
    if not os.path.exists(path):
        return {"turning_points": [], "archived_turning_points": [],
                "preferences": []}
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    for key in ("turning_points", "archived_turning_points",
                "preferences"):
        data.setdefault(key, [])
    return data


def _save(path, data):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1, sort_keys=True)
    os.replace(tmp, path)


def _parse_day(text):
    try:
        year, month, day = (int(p) for p in str(text).split("-"))
        return date(year, month, day)
    except (ValueError, AttributeError):
        raise ValueError(f"expected YYYY-MM-DD, got {text!r}")


# --- Turning points (CP-5.1) ---

def add_turning_point(store_path, person, date_text, event,
                      significance, pinned=False, milestone=False):
    """Add a turning point HE named, in his words. significance
    is his explanation, never inferred. pinned/milestone
    protect the episode from 180-day decay."""
    if not str(event or "").strip() or not str(significance or "").strip():
        raise ValueError("a turning point needs an event and "
                         "his significance for it")
    _parse_day(date_text)
    data = _load(store_path)
    entry = {"id": f"tp-{len(data['turning_points']) + 1}",
             "person": person, "date": date_text, "event": event,
             "significance": significance, "pinned": bool(pinned),
             "milestone": bool(milestone)}
    data["turning_points"].append(entry)
    _save(store_path, data)
    return entry


def list_turning_points(store_path, person):
    data = _load(store_path)
    out = [t for t in data["turning_points"] if t["person"] == person]
    return sorted(out, key=lambda t: t["date"])


def sweep_old_turning_points(store_path, today):
    """Archive unpinned, non-milestone turning points older
    than 180 days. Pinned and milestone episodes are NEVER
    swept. Archived, never deleted. Returns archived ids."""
    today_d = _parse_day(today)
    data = _load(store_path)
    kept, archived = [], []
    for entry in data["turning_points"]:
        age = (today_d - _parse_day(entry["date"])).days
        if age > EPISODE_DECAY_DAYS and not entry["pinned"] \
                and not entry["milestone"]:
            archived.append(entry)
        else:
            kept.append(entry)
    data["turning_points"] = kept
    for entry in archived:
        entry["archived_on"] = today_d.isoformat()
        data["archived_turning_points"].append(entry)
    if archived:
        _save(store_path, data)
    return [e["id"] for e in archived]


# --- Preference evolution pilot (CP-5.2 / CP-5.4 / CP-5.5) ---

def record_preference(store_path, person, domain, preference,
                      source, observed_date, evidence=""):
    """Record a preference. domain must be one of the 3 pilot
    domains. source: 'user_told' (never auto-expires) or
    'inferred' (expires 90 days without re-evidence)."""
    if domain not in PILOT_DOMAINS:
        raise ValueError(
            f"pilot domains only {PILOT_DOMAINS}; got "
            f"{domain!r} — the pilot does not generalise")
    if source not in ("user_told", "inferred"):
        raise ValueError(f"source must be user_told/inferred, "
                         f"got {source!r}")
    _parse_day(observed_date)
    data = _load(store_path)
    entry = {"id": f"pref-{len(data['preferences']) + 1}",
             "person": person, "domain": domain,
             "preference": preference, "source": source,
             "observed_date": observed_date,
             "last_evidence_date": observed_date,
             "evidence": evidence}
    data["preferences"].append(entry)
    _save(store_path, data)
    return entry


def re_evidence_preference(store_path, preference_id, today):
    """Re-evidence refreshes an inferred preference's 90-day
    clock. User-told preferences need no refresh."""
    data = _load(store_path)
    for entry in data["preferences"]:
        if entry["id"] == preference_id:
            entry["last_evidence_date"] = _parse_day(today).isoformat()
            _save(store_path, data)
            return entry
    raise ValueError(f"no preference {preference_id!r}")


def preference_expires_on(entry):
    """Expiry date for an inferred preference, or None for
    user-told (never auto-expires)."""
    if entry["source"] == "user_told":
        return None
    return (_parse_day(entry["last_evidence_date"])
            + timedelta(days=INFERRED_TTL_DAYS)).isoformat()


def current_preferences(store_path, person, today):
    """Live preferences for a person today: user-told always,
    inferred only within 90 days of last evidence. Expired
    inferred preferences are excluded, never silently kept."""
    today_d = _parse_day(today)
    data = _load(store_path)
    out = []
    for entry in data["preferences"]:
        if entry["person"] != person:
            continue
        if entry["source"] == "inferred":
            expiry = _parse_day(preference_expires_on(entry))
            if today_d > expiry:
                continue
        out.append(entry)
    return out


def explain_preference(entry):
    """CP-5.4 transparency: one sentence stating source, date,
    and expiry. If the record cannot explain itself, the
    caller must not use it."""
    if entry["source"] == "user_told":
        return (f"You told me on {entry['observed_date']}: "
                f"{entry['preference']}. This never auto-expires.")
    expiry = preference_expires_on(entry)
    return (f"I inferred on {entry['observed_date']} "
            f"(last evidence {entry['last_evidence_date']}): "
            f"{entry['preference']}. It expires on {expiry} "
            "unless you confirm it.")
