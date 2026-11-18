"""Part of the skills_system_inventory split (Phase A batch A28): see
skills_system_inventory.py (facade) for the pack docstring and registration.
Move only — bodies verbatim from the original module.
"""

# INTENTIONAL: cross-part names — security functions resolve these bare
# names in their defining module's globals, so they are imported here,
# not relied on from the facade (bare-name rule, plan §2 P3).
from .skills_system3_helpers import (
    _NOT_WINDOWS,
    _bool_word,
    _field,
    _is_windows,
    _looks_admin_denied,
    _ps_structured,
    _run,
    _tail,
)


# ---------------------------------------------------------------------------
# SECURITY — Defender + firewall posture (read-only)
# ---------------------------------------------------------------------------


def _format_security(rc: int, rows: list[list[str]], err: str) -> str:
    """Format Defender + firewall rows. Pure — tested directly.
    Missing pieces are reported as missing, never guessed."""
    defender: list[str] | None = None
    firewall: list[tuple[str, str]] = []
    for row in rows:
        tag = _field(row, 0)
        if tag == "DEF":
            defender = row
        elif tag == "FW":
            firewall.append((_field(row, 1), _field(row, 2)))
    if defender is None and not firewall:
        if rc != 0:
            return (f"Couldn't read the security status: "
                    f"{_tail(err) or 'the security cmdlets failed.'}")
        return ("Couldn't read the security status — neither the "
                "Defender cmdlets nor the firewall cmdlets answered.")
    lines = ["Security status:"]
    partial: list[str] = []
    if defender is None:
        partial.append("antivirus")
        lines.append("• Antivirus (Defender): couldn't be read.")
    elif _field(defender, 1).lower() == "unavailable":
        partial.append("antivirus")
        lines.append("• Antivirus: the Defender cmdlets aren't "
                     "available, so VYUK can't see the antivirus "
                     "state — a third-party antivirus may be "
                     "managing protection instead.")
    else:
        lines.append(f"• Antivirus (Defender): "
                     f"{_bool_word(_field(defender, 1))}")
        lines.append(f"• Real-time protection: "
                     f"{_bool_word(_field(defender, 2))}")
        sig = _field(defender, 3)
        lines.append(f"• Signatures last updated: {sig or 'unknown'}")
        scan = _field(defender, 4)
        lines.append(f"• Last quick scan ended: {scan or 'unknown'}")
    if firewall:
        states = ", ".join(f"{name} {_bool_word(enabled)}"
                           for name, enabled in firewall)
        lines.append(f"• Firewall profiles: {states}")
    else:
        partial.append("firewall")
        lines.append("• Firewall: couldn't be read.")
    if partial:
        lines.append(f"(Partial picture: {', '.join(partial)} "
                     "unavailable — the rest is as shown.)")
    return "\n".join(lines)


def security_status() -> str:
    """Defender state + firewall profile states. Read-only."""
    if not _is_windows():
        return f"Windows security status has no meaning here — {_NOT_WINDOWS}"
    sep = "[char]0x1f"
    command = (
        "try { $s = Get-MpComputerStatus -ErrorAction Stop; "
        f"'DEF' + {sep} + $s.AntivirusEnabled + {sep} + "
        f"$s.RealTimeProtectionEnabled + {sep} + "
        "$(if ($s.AntivirusSignatureLastUpdated) { "
        "$s.AntivirusSignatureLastUpdated.ToString('yyyy-MM-dd HH:mm') } "
        f"else {{ '' }}) + {sep} + "
        "$(if ($s.QuickScanEndTime) { "
        "$s.QuickScanEndTime.ToString('yyyy-MM-dd HH:mm') } "
        "else { '' }) } "
        f"catch {{ 'DEF' + {sep} + 'unavailable' }}; "
        "try { Get-NetFirewallProfile | ForEach-Object { "
        f"'FW' + {sep} + $_.Name + {sep} + $_.Enabled }} }} "
        "catch { }"
    )
    rc, rows, err = _ps_structured(command, timeout=60)
    return _format_security(rc, rows, err)


# ---------------------------------------------------------------------------
# FIREWALL — profile states + on/off (state changes re-read to verify)
# ---------------------------------------------------------------------------

_FIREWALL_PS_READ = (
    "Get-NetFirewallProfile | ForEach-Object { "
    "$_.Name + [char]0x1f + $_.Enabled }"
)


def _format_firewall(rows: list[list[str]]) -> str:
    profiles = [(_field(r, 0), _field(r, 1)) for r in rows if _field(r, 0)]
    if not profiles:
        return ""
    states = ", ".join(f"{name} {_bool_word(enabled)}"
                       for name, enabled in profiles)
    return states


def firewall_control(action: str = "status") -> str:
    """Read or set the Windows firewall across all profiles."""
    act = (action or "status").strip().lower()
    if act not in ("status", "enable", "disable"):
        return (f"Unknown firewall action: '{action}'. "
                "Use one of: status, enable, disable.")
    if not _is_windows():
        return f"The Windows firewall only exists on Windows — {_NOT_WINDOWS}"
    if act == "status":
        rc, rows, err = _ps_structured(_FIREWALL_PS_READ, timeout=60)
        states = _format_firewall(rows)
        if not states:
            return (f"Couldn't read the firewall state: "
                    f"{_tail(err) or 'Get-NetFirewallProfile failed.'}")
        return f"Firewall profiles: {states}."
    want = "true" if act == "enable" else "false"
    rc, out, err = _run(
        ["powershell", "-NoProfile", "-Command",
         f"Set-NetFirewallProfile -All -Enabled ${want}"],
        timeout=60)
    if rc != 0:
        detail = _tail(err or out) or "Set-NetFirewallProfile failed."
        if _looks_admin_denied(detail):
            return ("Couldn't change the firewall — that needs an "
                    "administrator context, which VYUK isn't running "
                    f"in. Windows said: {detail}")
        return f"Couldn't change the firewall: {detail}"
    # The set command claims nothing by itself — re-read the truth.
    rc2, rows, err2 = _ps_structured(_FIREWALL_PS_READ, timeout=60)
    states = _format_firewall(rows)
    verb = "enabled" if act == "enable" else "disabled"
    if not states:
        return (f"The firewall {verb} command was accepted, but VYUK "
                "couldn't re-read the profile states to confirm "
                f"({_tail(err2) or 'the read-back failed'}).")
    expected = "on" if act == "enable" else "off"
    if all(_bool_word(en) == expected for _n, en in
           [(r[0], r[1] if len(r) > 1 else "") for r in rows if _field(r, 0)]):
        return f"Firewall {verb} on all profiles — confirmed: {states}."
    return (f"The firewall {verb} command ran, but the profiles now "
            f"read: {states} — not uniformly {expected}, so check "
            "before trusting it.")
