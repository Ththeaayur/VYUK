"""Services + power tools of the System & Apps pack.

Phase A batch A31 part 2 (skills split close-out): moved verbatim
from core/skills_system.py — list_services, service_control,
power_plans and battery_health, plus the service/power helpers
only they use. Behaviour unchanged.
"""

import re
import tempfile
import time
from pathlib import Path

# INTENTIONAL (split A31 part 2): the shared helpers live in
# skills_system_helpers (plan P3(c)); this module imports them
# rather than duplicating them.
from .skills_system_helpers import _NOT_WINDOWS, _is_windows, _run, _tail


# ---------------------------------------------------------------------------
# SERVICES — list, start, stop
# ---------------------------------------------------------------------------


def _parse_sc_query(text: str) -> list[dict]:
    """Parse `sc query` output into {name, display, state} dicts.
    Pure function of the text — unit-tested with canned output."""
    services: list[dict] = []
    current: dict[str, str] = {}
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("SERVICE_NAME:"):
            if current.get("name"):
                services.append(current)
            current = {"name": stripped.split(":", 1)[1].strip(), "display": "", "state": ""}
        elif stripped.startswith("DISPLAY_NAME:"):
            current["display"] = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("STATE"):
            match = re.search(r":\s*\d+\s+(\w+)", stripped)
            if match:
                current["state"] = match.group(1)
    if current.get("name"):
        services.append(current)
    return services


def list_services(state_filter: str = "", limit: int = 80) -> str:
    """List Windows services with their state, optionally filtered by text."""
    if not _is_windows():
        return f"Windows services only exist on Windows — {_NOT_WINDOWS}"
    rc, out, err = _run(["sc", "query", "state=", "all"], timeout=60)
    if rc != 0:
        return f"Could not list services: {_tail(err or out) or 'sc.exe failed.'}"
    services = _parse_sc_query(out)
    if not services:
        return "No services were reported by Windows."
    needle = (state_filter or "").strip().lower()
    if needle:
        services = [s for s in services
                    if needle in s["name"].lower()
                    or needle in s["display"].lower()
                    or needle in s["state"].lower()]
        if not services:
            return f"No service matches '{state_filter}'."
    try:
        cap = max(1, min(int(limit), 300))
    except (TypeError, ValueError):
        cap = 80
    lines = [f"• {s['name']} — {s['display'] or s['name']} [{s['state']}]"
             for s in services[:cap]]
    suffix = f"\nShowing {len(lines)} of {len(services)} — filter by name to narrow it down." \
        if len(services) > cap else ""
    heading = f"Services matching '{state_filter}'" if needle else "Windows services"
    return f"{heading} ({len(services)}):\n" + "\n".join(lines) + suffix


_SC_ERROR_NOTES = {
    "5": "Access denied — starting or stopping this service needs an administrator "
         "VYUK (right-click VYUK and choose 'Run as administrator').",
    "1056": "That service is already running.",
    "1060": "No service with that name exists on this PC.",
    "1062": "That service is not started, so it can't be stopped.",
    "1083": "The service is configured to run in a program that doesn't exist.",
}


_SERVICE_POLL_ATTEMPTS = 11  # first look is immediate, then every interval


_SERVICE_POLL_INTERVAL_S = 0.5  # 10 gaps x 0.5 s = a ~5 s verification budget


def _sc_state(target: str) -> str:
    """One service's current state from `sc query <name>` — "RUNNING",
    "STOPPED", "START_PENDING", ... — or "" when the query fails or
    its output can't be read. Never guesses."""
    rc, out, _err = _run(["sc", "query", target], timeout=30)
    if rc != 0:
        return ""
    entries = _parse_sc_query(out)
    for entry in entries:
        if entry["name"].lower() == target.lower():
            return entry["state"]
    return entries[0]["state"] if len(entries) == 1 else ""


def _wait_for_service_state(target: str, wanted: str) -> tuple[str, bool]:
    """Poll `sc query` until the service is seen in the `wanted` state
    or the poll budget runs out. Returns (last state actually seen,
    reached?). An sc verb's exit 0 only means the request was
    ACCEPTED — a service can sit in START_PENDING and then fail — so
    callers must claim success only when this returns reached=True
    (the firewall_control re-read pattern, v5.9)."""
    last = ""
    for attempt in range(_SERVICE_POLL_ATTEMPTS):
        if attempt:
            time.sleep(_SERVICE_POLL_INTERVAL_S)
        state = _sc_state(target)
        if state:
            last = state
            if state.upper() == wanted:
                return last, True
    return last, False


def _service_poll_budget_s() -> float:
    return (_SERVICE_POLL_ATTEMPTS - 1) * _SERVICE_POLL_INTERVAL_S


def service_control(name: str, action: str, confirm: bool = False) -> str:
    """Start, stop or restart a Windows service by name (confirm-gated).

    The sc verb's exit code is never taken as the result: afterwards
    the service's state is polled with `sc query` until it reaches
    the target state (RUNNING for start/restart, STOPPED for stop),
    and the receipt reports the state actually observed — a service
    still in a pending state is reported as pending, not "done"."""
    act = (action or "").strip().lower()
    if act not in ("start", "stop", "restart"):
        return f"Unknown service action: '{action}'. Use one of: start, stop, restart."
    if not confirm:
        gerund = {"start": "Starting", "stop": "Stopping", "restart": "Restarting"}[act]
        return (f"{gerund} the service '{name}' changes how Windows "
                "runs and needs confirm=true. Ask again with confirmation.")
    if not _is_windows():
        return f"Windows services only exist on Windows — {_NOT_WINDOWS}"
    target = (name or "").strip()
    if not target:
        return "Give the name of a service (see list_services)."

    def _sc(verb: str) -> tuple[int, str, str]:
        """Run one sc verb -> (rc, error code, combined output)."""
        rc, out, err = _run(["sc", verb, target], timeout=60)
        combined = (out + "\n" + err).strip()
        match = re.search(r"FAILED\s+(\d+)", combined)
        return rc, (match.group(1) if match else ""), combined

    def _failure(verb: str, code: str, combined: str) -> str:
        note = _SC_ERROR_NOTES.get(code, "")
        detail = _tail(combined)
        return (f"Could not {verb} the service '{target}'"
                + (f" (error {code})" if code else "")
                + (f": {note}" if note else "")
                # The note is VYUK's gloss; Windows' own words always
                # follow it, never get replaced by it (v5.9, m1).
                + (f"\nWindows said: {detail}" if detail else ""))

    def _pending(verb: str, wanted: str, seen: str) -> str:
        budget = f"{_service_poll_budget_s():.0f}"
        if seen:
            return (f"Service '{target}' — {verb} was requested and Windows "
                    f"accepted it, but after {budget} seconds the service is "
                    f"still {seen}, not {wanted}. It has NOT finished "
                    "changing state; check its state again in a moment "
                    "before relying on it.")
        return (f"Service '{target}' — {verb} was requested and Windows "
                "accepted it, but the service's state couldn't be read "
                "back afterwards, so the result is unconfirmed.")

    was_not_running = False
    if act in ("stop", "restart"):
        rc, code, combined = _sc("stop")
        if rc != 0:
            if act == "restart" and code == "1062":
                # "Not started" IS the stopped state — for a restart,
                # starting is precisely what was asked (v5.9, m1).
                was_not_running = True
            else:
                return _failure("stop", code, combined)
        else:
            seen, reached = _wait_for_service_state(target, "STOPPED")
            if not reached:
                if act == "stop":
                    return _pending("stop", "STOPPED", seen)
                return (f"Restart of service '{target}' is incomplete: the "
                        "stop was requested, but Windows still reports it "
                        f"as {seen or 'an unreadable state'} after "
                        f"{_service_poll_budget_s():.0f} seconds, so the "
                        "start step has not been issued. Try the restart "
                        "again once the service settles.")
    if act in ("start", "restart"):
        rc, code, combined = _sc("start")
        if rc != 0:
            return _failure("start", code, combined)
        seen, reached = _wait_for_service_state(target, "RUNNING")
        if not reached:
            return _pending(act, "RUNNING", seen)
        if act == "restart" and was_not_running:
            return (f"Service '{target}' wasn't running, so there was "
                    "nothing to stop; it has been started, and Windows "
                    "confirms it is now RUNNING.")
        return (f"Service '{target}' — {act} done; Windows confirms it "
                f"is now {seen}.")
    # act == "stop", stop verified above.
    return (f"Service '{target}' — stop done; Windows confirms it is "
            "now STOPPED.")


# ---------------------------------------------------------------------------
# POWER — plans and battery health
# ---------------------------------------------------------------------------


_GUID_RE = re.compile(
    r"([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})")


def _parse_power_schemes(text: str) -> list[dict]:
    """Parse `powercfg /list` output: {guid, name, active}. Pure function.

    Structural, not keyed on the English label "Power Scheme GUID:"
    (powercfg localizes it): a scheme line in any language carries a
    GUID, the plan name in parentheses right after it, and a trailing
    "*" on the active plan. Those three anchors are language-free."""
    schemes: list[dict] = []
    for line in (text or "").splitlines():
        match = _GUID_RE.search(line)
        if not match:
            continue
        name_match = re.search(r"\((.*?)\)", line[match.end():])
        if not name_match:
            continue
        schemes.append({
            "guid": match.group(1),
            "name": name_match.group(1).strip(),
            "active": line.rstrip().endswith("*"),
        })
    return schemes


def power_plans(action: str = "list", plan: str = "", confirm: bool = False) -> str:
    """List Windows power plans, or switch the active one (confirm-gated)."""
    act = (action or "list").strip().lower()
    if act not in ("list", "set"):
        return f"Unknown power plan action: '{action}'. Use one of: list, set."
    if act == "set" and not confirm:
        return ("Switching the power plan changes how this PC balances speed and "
                "battery, and needs confirm=true. Ask again with confirmation.")
    if not _is_windows():
        return f"Power plans are a Windows feature — {_NOT_WINDOWS}"
    rc, out, err = _run(["powercfg", "/list"], timeout=30)
    if rc != 0:
        return f"Could not read the power plans: {_tail(err or out) or 'powercfg failed.'}"
    schemes = _parse_power_schemes(out)
    if not schemes:
        # Windows always has at least one power scheme, so an empty
        # parse means the answer wasn't readable — never "none exist".
        return ("Couldn't read the power plans from powercfg's answer "
                "(it came back in a shape I don't recognise). powercfg said:\n"
                + (_tail(out) or "no details were printed."))
    if act == "list":
        lines = []
        for s in schemes:
            tag = " (active)" if s["active"] else ""
            lines.append(f"• {s['name']}{tag} — {s['guid']}")
        return "Power plans:\n" + "\n".join(lines)
    wanted = (plan or "").strip().lower()
    if not wanted:
        return "Switching plans needs a plan name, e.g. 'Balanced' or 'High performance'."
    matches = [s for s in schemes if wanted in s["name"].lower()]
    if not matches:
        names = ", ".join(s["name"] for s in schemes)
        return f"No power plan matches '{plan}'. Available plans: {names}."
    if len(matches) > 1:
        # A needle like "performance" matches both "High performance"
        # and "Ultimate Performance" on a stock install — picking the
        # first silently switches to a plan the user may not mean.
        # Refuse and name them all (the winget_install exact-match
        # posture, v5.9 m2).
        lines = [f"• {s['name']} — {s['guid']}" for s in matches]
        return (f"'{plan}' matches more than one power plan, so nothing "
                "was switched. Say which one you mean:\n" + "\n".join(lines))
    target = matches[0]
    if target["active"]:
        return f"'{target['name']}' is already the active power plan."
    previous = next((s for s in schemes if s["active"]), None)
    rc, out, err = _run(["powercfg", "/setactive", target["guid"]], timeout=30)
    if rc != 0:
        return (f"Could not switch to '{target['name']}': "
                f"{_tail(err or out) or 'powercfg failed.'}")
    # /setactive exiting 0 is powercfg accepting the request; confirm
    # by re-listing and checking the active flag actually moved, and
    # report old -> new by the plans' real names (v5.9, m2).
    rc2, out2, _err2 = _run(["powercfg", "/list"], timeout=30)
    if rc2 == 0:
        after = _parse_power_schemes(out2)
        confirmed = any(s["guid"].lower() == target["guid"].lower()
                        and s["active"] for s in after)
        if confirmed:
            old_name = previous["name"] if previous else "the previous plan"
            return (f"Power plan switched: {old_name} -> '{target['name']}' "
                    "(confirmed: powercfg now shows it as the active plan).")
        still_active = next((s for s in after if s["active"]), None)
        if still_active is not None:
            return (f"powercfg accepted the switch to '{target['name']}', "
                    f"but its plan list still shows '{still_active['name']}' "
                    "as the active plan — the switch did not take effect.")
        return (f"powercfg accepted the switch to '{target['name']}', but "
                "no plan shows as active in its list now, so the switch "
                "couldn't be confirmed.")
    return (f"The switch to '{target['name']}' was requested, but the "
            "confirmation re-read of the plan list failed, so whether it "
            "took effect is unknown.")


def _extract_after_label(text: str, label: str, pattern: str) -> str | None:
    """Find `label` in report text, then the first regex match shortly after."""
    idx = (text or "").upper().find(label.upper())
    if idx < 0:
        return None
    match = re.search(pattern, text[idx:idx + 400], re.IGNORECASE)
    return match.group(1) if match else None


def _parse_grouped_number(raw: str) -> float | None:
    """Parse a number whose '.' or ',' (or thin spaces) may be purely a
    thousands separator — "50,040" / "50.040" / "50 040" all mean
    50040, the way Windows writes whole-number quantities like mWh
    in most locales. A lone separator that can't be a thousands group
    is treated as a decimal point. None when unparseable."""
    s = (raw or "").strip()
    for sep in ("\u00a0", "\u202f", " "):
        s = s.replace(sep, "")
    if not s:
        return None
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", s):
        return float(re.sub(r"[.,]", "", s))
    if re.fullmatch(r"\d+", s):
        return float(s)
    if re.fullmatch(r"\d+[.,]\d+", s):
        return float(s.replace(",", "."))
    return None


def _battery_figures(html: str) -> tuple[float | None, float | None, str | None]:
    """(design mWh, full-charge mWh, cycles) from a battery report.

    Structural first: in the report's Installed-batteries table the
    first two mWh figures are the design capacity and then the
    full-charge capacity — a fixed order in any language, while the
    labels themselves are localized — and the cycle count is the
    next plain-number table cell after them. The English-label parse
    below remains as a fallback for layouts that differ."""
    text = html or ""
    mwh = list(re.finditer(r"([\d][\d.,  ]*?)\s*mWh", text))
    if len(mwh) >= 2:
        design = _parse_grouped_number(mwh[0].group(1))
        full = _parse_grouped_number(mwh[1].group(1))
        cycles = None
        cell = re.search(r">\s*([\d][\d.,]*)\s*<", text[mwh[1].end():mwh[1].end() + 3000])
        if cell:
            value = _parse_grouped_number(cell.group(1))
            if value is not None:
                cycles = f"{value:,.0f}"
        if design is not None and full is not None:
            return design, full, cycles
    design_raw = _extract_after_label(text, "DESIGN CAPACITY", r"([\d][\d,\.]*)\s*mWh")
    full_raw = _extract_after_label(text, "FULL CHARGE CAPACITY", r"([\d][\d,\.]*)\s*mWh")
    cycles_raw = _extract_after_label(text, "CYCLE COUNT", r"([\d][\d,]*)")
    design = _parse_grouped_number(design_raw) if design_raw else None
    full = _parse_grouped_number(full_raw) if full_raw else None
    cycles = None
    if cycles_raw:
        value = _parse_grouped_number(cycles_raw)
        cycles = f"{value:,.0f}" if value is not None else cycles_raw
    return design, full, cycles


def battery_health() -> str:
    """Generate Windows' battery report and summarise capacity wear + cycles."""
    if not _is_windows():
        return f"Battery health reports are a Windows feature — {_NOT_WINDOWS}"
    report_path = Path(tempfile.gettempdir()) / "nova_battery_report.html"
    try:
        rc, out, err = _run(
            ["powercfg", "/batteryreport", "/output", str(report_path)], timeout=60)
        if rc != 0 or not report_path.exists():
            detail = _tail(err or out)
            return ("Windows could not produce a battery report — this is normal on "
                    "a desktop PC with no battery."
                    + (f"\nWindows said: {detail}" if detail else ""))
        html = report_path.read_text(encoding="utf-8", errors="replace")
        design, full, cycles_raw = _battery_figures(html)
        if design is None or full is None:
            return ("The battery report was generated, but its capacity figures "
                    "couldn't be read — the report's layout or language isn't one "
                    "I recognise, so battery wear can't be computed. (On a desktop "
                    "PC with no battery, having no figures is expected.)")
        if design <= 0:
            return "The battery report's design capacity read as zero, so wear can't be computed."
        health_pct = full / design * 100.0
        wear_pct = 100.0 - health_pct
        lines = [
            f"Design capacity: {design:,.0f} mWh",
            f"Full-charge capacity now: {full:,.0f} mWh",
            f"Battery health: {health_pct:.1f}% of new (wear {wear_pct:.1f}%)",
        ]
        if cycles_raw:
            lines.append(f"Charge cycles so far: {cycles_raw}")
        else:
            lines.append("Charge cycles: not reported by this battery.")
        if health_pct >= 90:
            lines.append("Verdict: the battery is in good shape.")
        elif health_pct >= 75:
            lines.append("Verdict: normal ageing — noticeably less than new, still serviceable.")
        else:
            lines.append("Verdict: heavily worn — expect much shorter unplugged time than when new.")
        return "Battery health:\n" + "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"Could not read battery health: {e}"
    finally:
        try:
            report_path.unlink(missing_ok=True)
        except OSError:
            pass
