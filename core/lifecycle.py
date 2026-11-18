"""Wave 10 (v5.65) Leaf 5 — PF-24 installer lifecycle controls,
in-app logic only.

This module owns the IN-APP half of PF-24: the autostart consent
record and the uninstall data-choice record. It owns NO installer
behaviour: uninstall itself, and the .iss hooks that consult the
choice record, belong to the installer (Wave 10 Leaf 1). Nothing
here shells out, downloads, or touches the network.

Autostart mechanism — READ, NEVER REINVENTED
---------------------------------------------
The autostart mechanism already exists in ``core/presence.py`` and
its value name is FROZEN (INV-10): the per-user Run key
``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``,
value name ``"Nova"`` (the pre-rename plumbing name, kept by
design so existing installs keep their entry). This module
invents no second mechanism: the default adapter delegates to
``presence.set_autostart`` / ``presence.autostart_enabled``,
and ``RealMechanismAdapter`` exposes those exact callables plus
the frozen key/value names for inspection. Any other adapter
(a test fake, a future platform adapter) must implement the
same two-method shape.

Consent rule (PF-24)
--------------------
Autostart is OFF unless the user consents. Consent is a
persisted record in ``data/lifecycle_v565.json`` (default off:
a missing, unreadable, or corrupt record reads as "not
consented"). ``set_autostart(True)`` with no stored consent
REFUSES without calling the mechanism adapter at all — the
Settings toggle can therefore never silently enable autostart.
``set_autostart(False)`` always applies: turning autostart off
needs no consent, and a refused/failed off is never hidden —
the result says so. Consent and current state are distinct:
turning autostart off does NOT erase the consent record, so a
later consented re-enable does not need to re-ask; withdrawing
consent is ``record_autostart_consent(False)`` followed by
``set_autostart(False)`` (``withdraw_autostart`` does both).

Uninstall data choice (PF-24)
------------------------------
The choice is recorded EXPLICITLY as ``"keep"`` (keep-my-data)
or ``"delete"`` (delete-my-data). There is no default choice:
``get_uninstall_data_choice`` returns ``None`` until the user
has chosen, and the uninstaller MUST treat undecided as "keep"
(``UNINSTALL_DEFAULT_ON_UNDECIDED = "keep"``) — deleting user
data on an unrecorded choice is never acceptable. This module
deletes nothing; it only records and reports the choice the
uninstaller consults.

Persistence discipline: atomic tmp + os.replace writes (the
Wave 8 checkpoint precedent), tolerant reads (a corrupt record
yields defaults, never an exception), stdlib only, no new
dependencies, no new tool registrations, no GUI, no telemetry.

Version note: the shipped tree stays at 5.64.1 during Wave 10
(the parent gate owns the bump to 5.65.0), so MODULE_VERSION
is "5.64.1" — this module asserts no other version.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

MODULE_VERSION = "5.64.1"

__all__ = [
    "MODULE_VERSION",
    "CONSENT_FILENAME",
    "UNINSTALL_CHOICES",
    "UNINSTALL_DEFAULT_ON_UNDECIDED",
    "AutostartAdapter",
    "FakeAutostartAdapter",
    "RealMechanismAdapter",
    "AutostartResult",
    "default_data_dir",
    "read_record",
    "get_autostart_consent",
    "record_autostart_consent",
    "current_autostart_state",
    "set_autostart",
    "withdraw_autostart",
    "get_uninstall_data_choice",
    "record_uninstall_data_choice",
]

CONSENT_FILENAME = "lifecycle_v565.json"
UNINSTALL_CHOICES = ("keep", "delete")
UNINSTALL_DEFAULT_ON_UNDECIDED = "keep"


def default_data_dir() -> Path:
    """The tree's data/ directory (same root core/config uses)."""
    return Path(__file__).resolve().parent.parent / "data"


def _resolve_data_dir(data_dir: Path | str | None) -> Path:
    return Path(data_dir) if data_dir is not None else default_data_dir()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _blank_record() -> dict:
    return {
        "autostart_consent": False,
        "autostart_consent_ts": None,
        "uninstall_data_choice": None,
        "uninstall_data_choice_ts": None,
    }


def read_record(data_dir: Path | str | None = None) -> dict:
    """Read the lifecycle record; missing/corrupt -> safe defaults.

    Never raises on a bad file: a record that cannot be trusted
    reads as "no consent, no uninstall choice", which is the
    fail-closed direction for both decisions this file governs.
    Unknown extra keys are dropped on read so a hand-edited file
    cannot smuggle in a new decision field.
    """
    path = _resolve_data_dir(data_dir) / CONSENT_FILENAME
    record = _blank_record()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return record
    if not isinstance(raw, dict):
        return record
    if raw.get("autostart_consent") is True:
        record["autostart_consent"] = True
        ts = raw.get("autostart_consent_ts")
        record["autostart_consent_ts"] = ts if isinstance(ts, str) else None
    if raw.get("uninstall_data_choice") in UNINSTALL_CHOICES:
        record["uninstall_data_choice"] = raw["uninstall_data_choice"]
        ts = raw.get("uninstall_data_choice_ts")
        record["uninstall_data_choice_ts"] = ts if isinstance(ts, str) else None
    return record


def _write_record(data_dir: Path, record: dict) -> dict:
    """Atomic write of the record (tmp file in-dir + os.replace)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / CONSENT_FILENAME
    fd, tmp_name = tempfile.mkstemp(
        prefix=CONSENT_FILENAME + ".", dir=str(data_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return record


def get_autostart_consent(data_dir: Path | str | None = None) -> bool:
    """True only if the user has explicitly consented. Default off."""
    return read_record(data_dir)["autostart_consent"] is True


def record_autostart_consent(
    consented: bool, data_dir: Path | str | None = None
) -> dict:
    """Persist the user's autostart consent decision (either way)."""
    resolved = _resolve_data_dir(data_dir)
    record = read_record(resolved)
    record["autostart_consent"] = bool(consented)
    record["autostart_consent_ts"] = _now_iso()
    return _write_record(resolved, record)


class AutostartAdapter(Protocol):
    """The two-method shape every autostart mechanism must offer."""

    def set_autostart(self, enabled: bool) -> str: ...
    def autostart_enabled(self) -> bool | None: ...


class FakeAutostartAdapter:
    """In-memory mechanism stand-in for tests and non-Windows dev.

    Records every call; ``enabled`` is the mechanism state.
    ``set_autostart`` mirrors presence's delete-of-missing-value
    success: turning off when already off still succeeds.
    """

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self.calls: list[bool] = []

    def set_autostart(self, enabled: bool) -> str:
        self.calls.append(bool(enabled))
        self.enabled = bool(enabled)
        if enabled:
            return "Auto-start is on (fake mechanism)."
        return "Auto-start is off (fake mechanism)."

    def autostart_enabled(self) -> bool | None:
        return self.enabled


class RealMechanismAdapter:
    """Adapter over the EXISTING autostart code path — no new code.

    Resolves lazily to ``core.presence`` so importing this module
    never imports winreg or PySide6 (presence imports both lazily
    too). ``set_fn`` / ``enabled_fn`` ARE ``presence``'s functions
    (identity-checked in the suite); ``value_name`` / ``run_key``
    ARE presence's frozen constants. Calling through this adapter
    changes behaviour exactly as calling presence directly would —
    nothing more, nothing less.
    """

    def __init__(self) -> None:
        from . import presence as _presence

        self._presence = _presence

    @property
    def value_name(self) -> str:
        return self._presence._VALUE_NAME

    @property
    def run_key(self) -> str:
        return self._presence._RUN_KEY

    @property
    def set_fn(self):
        return self._presence.set_autostart

    @property
    def enabled_fn(self):
        return self._presence.autostart_enabled

    def set_autostart(self, enabled: bool) -> str:
        return self._presence.set_autostart(bool(enabled))

    def autostart_enabled(self) -> bool | None:
        return self._presence.autostart_enabled()


def _default_adapter() -> RealMechanismAdapter:
    return RealMechanismAdapter()


def current_autostart_state(
    adapter: AutostartAdapter | None = None,
) -> bool | None:
    """Mechanism state via the adapter; None = unavailable here."""
    adapter = adapter if adapter is not None else _default_adapter()
    try:
        return adapter.autostart_enabled()
    except Exception:
        return None


@dataclass(frozen=True)
class AutostartResult:
    """Outcome of a set_autostart call. ``applied`` is the truth
    that matters: False means the mechanism was NOT touched."""

    applied: bool
    message: str
    enabled: bool | None


def set_autostart(
    enabled: bool,
    data_dir: Path | str | None = None,
    adapter: AutostartAdapter | None = None,
) -> AutostartResult:
    """Set autostart BOTH ways through the injected (or real) adapter.

    - enabled=True requires stored consent: without it, this
      REFUSES and never calls the adapter (consent gate).
    - enabled=False always applies (removal needs no consent).
    The result reports the adapter's post-call state when the
    adapter can report one; a failed adapter call reports
    applied=False rather than raising, so a caller can never
    mistake a failed off for a successful one.
    """
    enabled = bool(enabled)
    resolved = _resolve_data_dir(data_dir)
    adapter = adapter if adapter is not None else _default_adapter()
    if enabled and not get_autostart_consent(resolved):
        return AutostartResult(
            applied=False,
            message=(
                "Auto-start not enabled: the user has not consented "
                "(record consent first, then enable)."
            ),
            enabled=current_autostart_state(adapter),
        )
    try:
        message = adapter.set_autostart(enabled)
    except Exception as exc:  # noqa: BLE001 — report, never raise
        return AutostartResult(
            applied=False,
            message=f"Could not change auto-start: {exc}",
            enabled=current_autostart_state(adapter),
        )
    return AutostartResult(
        applied=True,
        message=str(message),
        enabled=current_autostart_state(adapter),
    )


def withdraw_autostart(
    data_dir: Path | str | None = None,
    adapter: AutostartAdapter | None = None,
) -> AutostartResult:
    """Withdraw consent AND turn autostart off, in that order.

    The consent record is cleared first so that even if the
    mechanism call fails, nothing can later re-enable autostart
    on the strength of a stale consent.
    """
    resolved = _resolve_data_dir(data_dir)
    record_autostart_consent(False, resolved)
    return set_autostart(False, resolved, adapter)


def get_uninstall_data_choice(
    data_dir: Path | str | None = None,
) -> str | None:
    """The recorded uninstall choice: "keep", "delete", or None.

    None means the user has not chosen yet. The uninstaller must
    treat None as "keep" (see UNINSTALL_DEFAULT_ON_UNDECIDED) and
    ask — it must never delete on an unrecorded choice.
    """
    return read_record(data_dir)["uninstall_data_choice"]


def record_uninstall_data_choice(
    choice: str, data_dir: Path | str | None = None
) -> dict:
    """Record the user's explicit keep-my-data / delete-my-data choice.

    Raises ValueError on anything else — an ambiguous choice must
    never be persisted as if it were a decision.
    """
    if choice not in UNINSTALL_CHOICES:
        raise ValueError(
            f"uninstall data choice must be one of {UNINSTALL_CHOICES}, "
            f"got {choice!r}"
        )
    resolved = _resolve_data_dir(data_dir)
    record = read_record(resolved)
    record["uninstall_data_choice"] = choice
    record["uninstall_data_choice_ts"] = _now_iso()
    return _write_record(resolved, record)
