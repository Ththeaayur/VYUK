"""Wave 10 (v5.65) Leaf 3 — A8 machine doctor (STAGED, unwired).

Reports on the machine's health — battery wear, disk health,
GPU driver versions, temperatures — and never auto-remediates:
driver changes are a human's decision, so this module reports
and stops. It never cries wolf:

- Battery wear warns ONLY at >= 25% loss (design capacity vs
  full-charge capacity): ``BATTERY_WEAR_WARN_PCT = 25``.
  Below that, however worn, the status is "ok" with the
  measurement shown.
- Disk warns ONLY on explicit failing indicators: a SMART
  status in {"failing", "failed", "bad"} or an explicit
  predictive-failure flag. Raw counters (reallocated sectors,
  pending sectors) are REPORTED but never alarm on their own —
  a nonzero counter without a failing status is information,
  not a warning.
- Temperature warns ONLY above ``TEMP_WARN_C = 95`` degrees C
  — a high, documented threshold, deliberately above the
  85-90 C a laptop legitimately reaches under load, so normal
  hot running never produces a warning.
- An unknown sensor produces status "unknown", never an alarm
  and never a guessed value. A false-alarm path is a test
  failure: below-threshold values produce NO warning, unknown
  sensors produce NO warning.

Every warning cites its measurement: {"sensor", "value",
"source", "threshold", "message"}. A warning without all four
is a bug.

Readings come through INJECTED reader callables, so Windows
WMI readers and Linux sysfs readers can be supplied later by
the coordinator without touching this module:

  readers = {
    "battery": callable -> dict | None,
    "disk": callable -> dict | None,
    "gpu": callable -> list[dict] | None,
    "temperatures": callable -> list[dict] | None,
  }

Reader contracts (all fields optional — missing = unknown):
- battery: {"design_capacity_mwh", "full_charge_mwh",
  "source"} (any capacity unit works as long as both use the
  same one; the wear ratio is unit-free).
- disk: {"smart_status", "predictive_failure",
  "reallocated_sectors", "pending_sectors", "source"}.
  ``smart_status`` is matched case-insensitively; anything
  other than the failing set is "not failing".
- gpu: [{"name", "driver_version", "source"}]. Driver versions
  are REPORTED only — this module holds no known-good version
  table, so it never warns that a driver is old.
- temperatures: [{"sensor", "value_c", "source"}].

The module's own default readers return "unknown" rather than
guessing: ``default_readers()`` maps every key to a callable
returning None. A reader that raises is treated as unknown,
never as a crash — a broken sensor probe must not break the
doctor. Platform readers (WMI/sysfs) belong in the coordinator
layer, not here.

Stdlib only. Product VERSION is untouched (still 5.64.1); this
module carries no product-version constant of its own.
"""

from __future__ import annotations

BATTERY_WEAR_WARN_PCT = 25.0
TEMP_WARN_C = 95.0
DISK_FAILING_STATUSES = frozenset({"failing", "failed", "bad"})


def default_readers() -> dict:
    """Conservative readers: every sensor reads as unknown."""
    return {
        "battery": lambda: None,
        "disk": lambda: None,
        "gpu": lambda: None,
        "temperatures": lambda: None,
    }


def _call(reader, default):
    """Call an injected reader; exceptions and None mean unknown."""
    if not callable(reader):
        return default
    try:
        value = reader()
    except Exception:  # noqa: BLE001 — a broken probe is unknown
        return default
    return default if value is None else value


def _battery_section(reading) -> dict:
    section: dict = {"status": "unknown", "wear_pct": None,
                     "design_capacity": None, "full_charge": None,
                     "source": None, "threshold_pct": BATTERY_WEAR_WARN_PCT,
                     "warnings": []}
    if not isinstance(reading, dict):
        return section
    design = reading.get("design_capacity_mwh")
    full = reading.get("full_charge_mwh")
    section["design_capacity"] = design
    section["full_charge"] = full
    section["source"] = reading.get("source")
    if (isinstance(design, (int, float))
            and isinstance(full, (int, float))
            and design > 0 and full >= 0):
        wear = (design - full) / design * 100.0
        section["wear_pct"] = wear
        if wear >= BATTERY_WEAR_WARN_PCT:
            section["status"] = "warn"
            section["warnings"].append({
                "sensor": "battery",
                "value": wear,
                "unit": "pct_wear",
                "source": section["source"],
                "threshold": BATTERY_WEAR_WARN_PCT,
                "message": (
                    f"Battery wear {wear:.1f}% (design {design}, "
                    f"full charge {full}) meets/exceeds the "
                    f"{BATTERY_WEAR_WARN_PCT:.0f}% warn threshold"),
            })
        else:
            section["status"] = "ok"
    return section


def _disk_section(reading) -> dict:
    section: dict = {"status": "unknown", "smart_status": None,
                     "predictive_failure": None,
                     "reallocated_sectors": None,
                     "pending_sectors": None,
                     "source": None, "warnings": []}
    if not isinstance(reading, dict):
        return section
    status = reading.get("smart_status")
    section["smart_status"] = status
    section["predictive_failure"] = reading.get("predictive_failure")
    section["reallocated_sectors"] = reading.get("reallocated_sectors")
    section["pending_sectors"] = reading.get("pending_sectors")
    section["source"] = reading.get("source")
    failing = (
        (isinstance(status, str)
         and status.strip().lower() in DISK_FAILING_STATUSES)
        or reading.get("predictive_failure") is True)
    if status is None and reading.get("predictive_failure") is None:
        return section  # nothing explicit: unknown, never an alarm
    if failing:
        section["status"] = "warn"
        section["warnings"].append({
            "sensor": "disk",
            "value": status if status is not None else "predictive_failure",
            "unit": "smart_status",
            "source": section["source"],
            "threshold": "explicit failing indicator",
            "message": (
                f"Disk reports a failing indicator (SMART status "
                f"{status!r}, predictive failure "
                f"{reading.get('predictive_failure')!r})"),
        })
    else:
        section["status"] = "ok"
    return section


def _gpu_section(reading) -> list[dict]:
    """GPU driver versions, reported only — never warned on."""
    if not isinstance(reading, list):
        return []
    out = []
    for entry in reading:
        if not isinstance(entry, dict):
            continue
        out.append({
            "name": entry.get("name"),
            "driver_version": entry.get("driver_version"),
            "source": entry.get("source"),
            "status": ("ok" if entry.get("driver_version") else "unknown"),
        })
    return out


def _temperature_sections(reading) -> list[dict]:
    sections = []
    if not isinstance(reading, list):
        return sections
    for entry in reading:
        if not isinstance(entry, dict):
            continue
        value = entry.get("value_c")
        section: dict = {"sensor": entry.get("sensor"),
                         "value_c": value,
                         "source": entry.get("source"),
                         "threshold_c": TEMP_WARN_C,
                         "status": "unknown", "warnings": []}
        if isinstance(value, (int, float)):
            if value > TEMP_WARN_C:
                section["status"] = "warn"
                section["warnings"].append({
                    "sensor": entry.get("sensor"),
                    "value": value,
                    "unit": "celsius",
                    "source": entry.get("source"),
                    "threshold": TEMP_WARN_C,
                    "message": (
                        f"Temperature {value}C on "
                        f"{entry.get('sensor')} exceeds the "
                        f"{TEMP_WARN_C:.0f}C warn threshold"),
                })
            else:
                section["status"] = "ok"
        sections.append(section)
    return sections


class MachineDoctor:
    """The machine doctor: injected readers in, a report out."""

    def __init__(self, readers: dict | None = None) -> None:
        base = default_readers()
        base.update(readers or {})
        self.readers = base

    def run(self) -> dict:
        """Run every probe and return the full report.

        {"battery", "disk", "gpus", "temperatures", "warnings",
        "overall"} — "overall" is "warn" when any warning exists,
        "ok" when every known sensor is ok, else "unknown".
        Warnings are the concatenation of every section's
        warnings, each citing value + source + threshold.
        """
        battery = _battery_section(
            _call(self.readers.get("battery"), None))
        disk = _disk_section(_call(self.readers.get("disk"), None))
        gpus = _gpu_section(_call(self.readers.get("gpu"), None))
        temperatures = _temperature_sections(
            _call(self.readers.get("temperatures"), None))
        warnings: list[dict] = []
        warnings.extend(battery["warnings"])
        warnings.extend(disk["warnings"])
        for section in temperatures:
            warnings.extend(section["warnings"])
        if warnings:
            overall = "warn"
        elif (battery["status"] == "ok" or disk["status"] == "ok"
                or any(t["status"] == "ok" for t in temperatures)
                or any(g["status"] == "ok" for g in gpus)):
            overall = "ok"
        else:
            overall = "unknown"
        return {"battery": battery, "disk": disk, "gpus": gpus,
                "temperatures": temperatures, "warnings": warnings,
                "overall": overall}


def run_machine_doctor(readers: dict | None = None) -> dict:
    """One-shot convenience wrapper over MachineDoctor."""
    return MachineDoctor(readers).run()
