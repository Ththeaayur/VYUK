from __future__ import annotations  # INTENTIONAL (skills split B21): moved code was written under postponed evaluation.
import datetime
import threading
import uuid
# INTENTIONAL (skills split B21): shared store plumbing lives in daily_helpers; AlarmStore methods resolve these by bare name.
from .daily_helpers import _load_json, _save_json_atomic

# ===========================================================================
# PF-22 — alarms / timers / reminders
# ===========================================================================

class Alarm:
    """One persisted alarm. ``status`` is pending | fired |
    dismissed | missed | snoozed (snoozed alarms re-enter as a
    pending alarm at the new fire time)."""

    def __init__(self, label, fire_at, message="", kind="alarm", alarm_id=None):
        self.id = alarm_id or uuid.uuid4().hex
        self.label = str(label)
        self.fire_at = fire_at  # datetime (naive, caller timezone)
        self.message = str(message or "")
        self.kind = str(kind or "alarm")
        self.status = "pending"
        self.fired_at = None
        self.snoozed_from = None

    def to_dict(self) -> dict:
        return {
            "id": self.id, "label": self.label,
            "fire_at": self.fire_at.isoformat(), "message": self.message,
            "kind": self.kind, "status": self.status,
            "fired_at": self.fired_at,
            "snoozed_from": self.snoozed_from,
        }

    @classmethod
    def from_dict(cls, data: dict):
        try:
            fire_at = datetime.datetime.fromisoformat(str(data["fire_at"]))
        except (KeyError, ValueError):
            return None
        alarm = cls(data.get("label", ""), fire_at,
                    data.get("message", ""), data.get("kind", "alarm"),
                    data.get("id"))
        alarm.status = str(data.get("status", "pending"))
        alarm.fired_at = data.get("fired_at")
        alarm.snoozed_from = data.get("snoozed_from")
        return alarm


class AlarmStore:
    """Persisted alarms in the caller's store file.

    Firing is explicit: ``tick(now)`` returns the alarms due at
    or before ``now`` and marks them fired (persisted before
    returning, so a crash after tick cannot re-fire them).
    ``check_missed(now)`` is the start-up pass: pending alarms
    whose time already passed while the assistant was down are
    marked ``missed`` and REPORTED in the return value — never
    silently dropped, never silently fired as if on time.
    """

    def __init__(self, store_path):
        if not store_path:
            raise ValueError("AlarmStore requires a caller-provided store_path")
        self.store_path = str(store_path)
        self._lock = threading.Lock()

    def _read_all(self) -> list[Alarm]:
        data = _load_json(self.store_path).get("alarms") or []
        alarms = []
        for item in data:
            if isinstance(item, dict):
                alarm = Alarm.from_dict(item)
                if alarm is not None:
                    alarms.append(alarm)
        return alarms

    def _write_all(self, alarms) -> None:
        with self._lock:
            data = _load_json(self.store_path)
            data["alarms"] = [a.to_dict() for a in alarms]
            _save_json_atomic(self.store_path, data)

    def add(self, label, fire_at, message="", kind="alarm") -> Alarm:
        alarm = Alarm(label, fire_at, message, kind)
        alarms = self._read_all()
        alarms.append(alarm)
        self._write_all(alarms)
        return alarm

    def list(self, status: str | None = None) -> list[Alarm]:
        alarms = self._read_all()
        if status is not None:
            alarms = [a for a in alarms if a.status == status]
        return alarms

    def get(self, alarm_id) -> Alarm | None:
        for alarm in self._read_all():
            if alarm.id == str(alarm_id):
                return alarm
        return None

    def tick(self, now: datetime.datetime) -> list[Alarm]:
        """Fire every pending alarm due at/before now. Fired state
        is persisted BEFORE the list is returned."""
        alarms = self._read_all()
        due = [a for a in alarms
               if a.status == "pending" and a.fire_at <= now]
        if due:
            for alarm in due:
                alarm.status = "fired"
                alarm.fired_at = now.isoformat()
            self._write_all(alarms)
        return due

    def check_missed(self, now: datetime.datetime) -> list[Alarm]:
        """Start-up pass: pending alarms whose fire time is already
        past are marked ``missed`` and returned for reporting.
        Callers MUST surface the returned list to the user — the
        missed state is also persisted, so a second start still
        reports it until dismissed."""
        alarms = self._read_all()
        missed = [a for a in alarms
                  if a.status in ("pending", "missed")
                  and a.fire_at < now]
        # Only newly-past alarms change state; already-missed ones
        # are re-reported until dismissed.
        changed = False
        for alarm in missed:
            if alarm.status == "pending":
                alarm.status = "missed"
                changed = True
        if changed:
            self._write_all(alarms)
        return missed

    def dismiss(self, alarm_id) -> bool:
        """Dismiss a fired or missed alarm. Returns False when unknown."""
        alarms = self._read_all()
        for alarm in alarms:
            if alarm.id == str(alarm_id) and alarm.status in ("fired", "missed"):
                alarm.status = "dismissed"
                self._write_all(alarms)
                return True
        return False

    def snooze(self, alarm_id, minutes: int = 10,
               now: datetime.datetime | None = None) -> Alarm | None:
        """Snooze a fired/missed alarm: it becomes pending again
        at now + minutes. Returns the alarm, or None when unknown
        or not in a snoozable state."""
        if minutes <= 0:
            return None
        now = now or datetime.datetime.now()
        alarms = self._read_all()
        for alarm in alarms:
            if alarm.id == str(alarm_id) and alarm.status in ("fired", "missed"):
                alarm.snoozed_from = alarm.fire_at.isoformat()
                alarm.fire_at = now + datetime.timedelta(minutes=minutes)
                alarm.status = "pending"
                alarm.fired_at = None
                self._write_all(alarms)
                return alarm
        return None

    def cancel(self, alarm_id) -> bool:
        alarms = self._read_all()
        kept = [a for a in alarms if a.id != str(alarm_id)]
        if len(kept) == len(alarms):
            return False
        self._write_all(kept)
        return True


