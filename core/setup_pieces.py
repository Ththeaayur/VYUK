"""The "finish setup" card's per-piece state (loophole batch 7,
#8 + #18).

The old card was binary: it showed while setup_incomplete()
(onboarded but no brain choice stored) and vanished at Finish —
which marks ALL FOUR stages done — even when Google, Gmail,
Calendar, the browser or the mic were never actually set up.
Skipping a piece in the wizard killed the only path back to it.

The close tracks each unfinished piece separately. A piece is:
* "done" — its live signal holds (a token file, a stored grant);
* "declined" — the user explicitly said not now (a wizard skip,
  recorded durably; a stored grant denial for mic/browser);
* "open" — neither. Only open pieces keep the card up.

The card itself is never permanently dismissible (#8): its
"Remind me later" snoozes it for SNOOZE_DAYS (7) — it returns
when the snooze expires. It disappears only when every piece is
done or declined (#18).

Qt-free. The store is a plain dict (tests use dicts; the GUI
builds one from QSettings and persists changes back). Live
signals are probed by live_signals() with injectable data_dir /
grants, so tests never touch live data/ or the real grants store.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

#: The five setup pieces, in card order. Iterating this yields
#: the ids — a signals dict is {id: bool}, so `{p: False for p
#: in SETUP_PIECES}` is the all-open dict (a (id, label) shape
#: here would silently key the dict by tuples and every piece
#: would read open forever).
SETUP_PIECES: tuple[str, ...] = (
    "google",
    "gmail",
    "calendar",
    "browser",
    "mic",
)

PIECE_IDS = SETUP_PIECES

PIECE_LABELS = {
    "google": "Google sign-in",
    "gmail": "Gmail",
    "calendar": "Calendar",
    "browser": "Browser",
    "mic": "Microphone",
}

#: Dismissing the card snoozes it this long — never kills it.
SNOOZE_DAYS = 7

#: QSettings keys (registered in core/config_spec
#: QSETTINGS_FIELDS): the declined pieces (";"-separated ids)
#: and the snooze deadline (ISO timestamp, "" = not snoozed).
DECLINED_KEY = "firstrun/pieces_declined"
SNOOZED_UNTIL_KEY = "firstrun/setup_card_snoozed_until"


def piece_states(signals: dict[str, bool],
                 declines: set[str] | frozenset[str]) -> dict[str, str]:
    """{piece: "done" | "declined" | "open"}. A done signal wins
    over a decline (the user declined, then completed it — the
    completion stands). Unknown pieces are ignored."""
    declines = set(declines or set())
    out = {}
    for pid in PIECE_IDS:
        if signals.get(pid):
            out[pid] = "done"
        elif pid in declines:
            out[pid] = "declined"
        else:
            out[pid] = "open"
    return out


def unfinished_pieces(signals: dict[str, bool],
                      declines: set[str] | frozenset[str]) -> list[str]:
    """The open piece ids, in card order — the pieces the card
    names. Declined pieces are resolved, not unfinished."""
    states = piece_states(signals, declines)
    return [pid for pid in PIECE_IDS if states[pid] == "open"]


def unfinished_labels(signals: dict[str, bool],
                      declines: set[str] | frozenset[str]) -> list[str]:
    """The open pieces' display labels, in card order."""
    return [PIECE_LABELS[pid]
            for pid in unfinished_pieces(signals, declines)]


def snooze_until(days: int = SNOOZE_DAYS,
                 now: datetime | None = None) -> str:
    """An ISO snooze deadline ``days`` from ``now``."""
    base = now if now is not None else datetime.now()
    return (base + timedelta(days=days)).isoformat(timespec="seconds")


def is_snoozed(until: str | None,
               now: datetime | None = None) -> bool:
    """True while the snooze deadline is still in the future. An
    empty or unparseable value is not snoozed — a broken pin
    must never hide the card forever."""
    if not until:
        return False
    try:
        deadline = datetime.fromisoformat(str(until).strip())
    except Exception:  # noqa: BLE001 — unparseable is not snoozed
        return False
    base = now if now is not None else datetime.now()
    return base < deadline


def card_visible(*, onboarded: bool, signals: dict[str, bool],
                 declines: set[str] | frozenset[str],
                 snoozed_until: str | None,
                 now: datetime | None = None) -> bool:
    """Whether the Home setup card shows: the install onboarded,
    at least one piece is still open, and the card is not
    snoozed. A fresh install that never ran the wizard reads
    False (the wizard itself is the way back there)."""
    if not onboarded:
        return False
    if not unfinished_pieces(signals, declines):
        return False
    return not is_snoozed(snoozed_until, now=now)


def setup_card_state(*, onboarded: bool, signals: dict[str, bool],
                     declines: set[str] | frozenset[str],
                     snoozed_until: str | None,
                     now: datetime | None = None) -> dict[str, Any]:
    """The one call the Home card makes: {"visible",
    "unfinished", "unfinished_labels", "states"}. Never raises —
    a failed read hides nothing and shows nothing it should not:
    on error the card reads as not-visible (the wizard is the way
    back, same as the old setup_incomplete() failure mode)."""
    try:
        states = piece_states(signals, declines)
        unfin = [pid for pid in PIECE_IDS if states[pid] == "open"]
        visible = bool(onboarded and unfin
                       and not is_snoozed(snoozed_until, now=now))
        return {
            "visible": visible,
            "unfinished": unfin,
            "unfinished_labels": [PIECE_LABELS[pid] for pid in unfin],
            "states": states,
        }
    except Exception:  # noqa: BLE001 — a failed read hides the card
        return {"visible": False, "unfinished": [],
                "unfinished_labels": [], "states": {}}


def record_declines(pieces: set[str] | frozenset[str], *,
                    get_fn: Any = None, set_fn: Any = None) -> None:
    """Record ``pieces`` as explicitly declined through the given
    get/set (default: the live QSettings via core/config_spec).
    The one call the wizard's skip handlers make. Never raises —
    a failed write simply leaves the piece open (the card keeps
    showing it, which is the safe direction)."""
    try:
        if get_fn is None or set_fn is None:
            from .config_spec import (qsettings_get as _get,
                                      qsettings_set as _set)
            get_fn, set_fn = _get, _set
        store = {DECLINED_KEY: str(get_fn(DECLINED_KEY) or "")}
        write_declines(store, set(pieces))
        set_fn(DECLINED_KEY, store[DECLINED_KEY])
    except Exception:  # noqa: BLE001 — a failed write leaves the
        pass  # piece open


def read_declines(store: dict) -> set[str]:
    """The declined pieces recorded in the store. Unknown ids
    are dropped (a stale id must never resolve a real piece).
    Never raises."""
    try:
        raw = str(store.get(DECLINED_KEY) or "")
        return {pid for pid in (p.strip() for p in raw.split(";"))
                if pid in PIECE_IDS}
    except Exception:  # noqa: BLE001 — unreadable is undeclined
        return set()


def write_declines(store: dict,
                   pieces: set[str] | frozenset[str]) -> dict:
    """Record ``pieces`` as declined in the store (union with
    what is already there — a decline is never undone by
    recording another). Returns the store. Never raises."""
    try:
        merged = read_declines(store) | {p for p in pieces
                                         if p in PIECE_IDS}
        store[DECLINED_KEY] = ";".join(sorted(merged))
    except Exception:  # noqa: BLE001 — a broken store keeps memory
        pass
    return store


def read_snoozed_until(store: dict) -> str:
    """The snooze deadline recorded in the store ("" = none).
    Never raises."""
    try:
        return str(store.get(SNOOZED_UNTIL_KEY) or "")
    except Exception:  # noqa: BLE001 — unreadable is not snoozed
        return ""


def write_snooze(store: dict, until: str) -> dict:
    """Record a snooze deadline in the store. Returns the store.
    Never raises."""
    try:
        store[SNOOZED_UNTIL_KEY] = str(until or "")
    except Exception:  # noqa: BLE001 — a broken store keeps memory
        pass
    return store


def _token_present(path: Path) -> bool:
    """A sign-in token file counts when it exists and parses as
    a JSON object — a corrupt or empty file is not a sign-in.
    Never raises."""
    try:
        if not path.is_file():
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        return isinstance(data, dict) and bool(data)
    except Exception:  # noqa: BLE001 — unparseable is not signed in
        return False


def live_signals(*, data_dir: str | Path | None = None,
                 grants: Any = None) -> dict[str, bool]:
    """The live done-signals for all five pieces. ``data_dir``
    defaults to the install's data/; ``grants`` defaults to
    core/permission_grants. Never raises — a failed probe reads
    as not done."""
    try:
        if data_dir is None:
            from .config import DATA_DIR

            data_dir = DATA_DIR
        data_dir = Path(data_dir)
        google = _token_present(data_dir / "gemini_token.json")
        services = _token_present(data_dir / "google_token.json")
        if grants is None:
            from . import permission_grants as _pg

            grants = _pg
        try:
            browser = grants.get_state("browser") == "granted"
        except Exception:  # noqa: BLE001 — unreadable is not done
            browser = False
        try:
            mic = grants.get_state("microphone") == "granted"
        except Exception:  # noqa: BLE001 — unreadable is not done
            mic = False
        return {"google": google, "gmail": services,
                "calendar": services, "browser": browser, "mic": mic}
    except Exception:  # noqa: BLE001 — a failed probe is all-open
        return {pid: False for pid in PIECE_IDS}


def live_declines(*, store: dict | None = None,
                  grants: Any = None) -> set[str]:
    """The declined pieces: the store's recorded declines, plus
    mic/browser when their grant record holds a STORED denial.
    A stored denial is read through get_record (state "denied"):
    the blank record is "unknown", so the fail-closed default
    (get_state reads "denied" for mic/camera when the store is
    unreadable or no record exists) never counts as a decline.
    Never raises."""
    try:
        declined = read_declines(store or {})
        if grants is None:
            from . import permission_grants as _pg

            grants = _pg
        # Piece id -> permission id: the grants store calls the
        # mic "microphone" (PERMISSION_IDS), so get_record("mic")
        # would read a blank unknown record forever and a stored
        # mic denial would never count as a decline.
        for pid, grant_id in (("mic", "microphone"),
                              ("browser", "browser")):
            try:
                record = grants.get_record(grant_id)
                if isinstance(record, dict) \
                        and record.get("state") == "denied":
                    declined.add(pid)
            except Exception:  # noqa: BLE001 — unreadable is not
                pass  # a decline
        return declined
    except Exception:  # noqa: BLE001 — a failed read declines
        return set()  # nothing
