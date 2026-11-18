"""Battery + metered-network awareness (v5.56, Wave 2, PF-12) —
the BEHAVIOUR half of PF-7's degraded-mode story (PF-7 is the
display; this module is the policy).

Policy (measured, not just flagged — see test_v556_power.py):
  * on battery (not charging): watcher cadence throttled
    (steady poll x4, x8 at <=20%) AND local inference throttled
    (inference factor 0.5 — the consumer seam local_client
    reads; this module only publishes the factor, it does not
    reach into the local brain);
  * on a metered connection: model downloads AND updates
    paused (downloads_allowed() / updates_allowed() go False);
  * on AC and unmetered (or metered-unknown): everything
    resumes (multiplier 1, factor 1.0, downloads allowed).

Detection — exactly how, and what is NOT executed here:
  * Battery: psutil.sensors_battery() (already a declared
    dependency; watchers._probe_battery uses the same call).
    No battery / no psutil -> power_source "unknown", treated
    as AC for policy (a desktop must not be throttled for
    lacking a battery). Never raises.
  * Metered: Windows ONLY, via the WinRT Network Cost API —
    Windows.Networking.Connectivity.NetworkInformation.
    GetInternetConnectionProfile().GetConnectionCost().
    NetworkCostType: Fixed / Variable = metered, Unrestricted
    = unmetered, Unknown = unknown. Python reaches WinRT only
    through a `winrt`/`winsdk` binding, which is NOT a
    dependency of this tree (zero new deps for Wave 2), so the
    default reader returns None (unknown) on hosts without
    that binding — FIELD-OWED: on real Windows the metered
    half needs either that binding (licence read first) or a
    native reader, and must be re-verified on Windows before
    it is claimed to work there. The policy itself is fully
    testable through the injected `metered_reader` seam, and
    unknown metered FAILS OPEN (downloads allowed): pausing
    updates for every host that merely lacks the binding
    would be worse than the metered cost it tries to save.
    This module never shells out, never guesses from an SSID,
    and never treats "Wi-Fi" as "unmetered" — Wi-Fi can be
    metered (hotspots), so only the Cost API verdict counts.

Wiring (what this module does vs what callers do):
  * Watchers: WatcherEngine accepts power_policy=<callable>
    (core/watchers.py, v5.56) and applies the multiplier to
    its STEADY poll only — the N10 fast poll for confirming
    states still wins (min), so throttling never hides a
    mid-confirmation watcher. watcher_policy_fn() below is
    the ready-made callable; apply_to_watcher(engine) attaches
    it. With no policy attached, watcher behaviour is
    byte-identical to before (opt-in on purpose: the loop's
    default path is not a place for a new probe).
  * Downloads / updates: updater.py's check()/download_and_
    stage() and local_assets' ensure_*/download_* paths are
    OUTSIDE Stream F's file list, so they are NOT edited
    here. Their gate is this module's downloads_allowed() /
    updates_allowed() on current_policy() — one call each at
    the top of check() and download_and_stage() (updater) and
    at the top of the ensure_* entry points (local_assets) —
    recorded as a coordinator integration step, with the exact
    call sites named in the Wave 2 handoff. Until that lands,
    the metered half is policy-complete but not yet enforced
    at the download call sites: the PF-7 surface shows the
    pause state honestly either way.
  * Local inference: local_client reads
    local_inference_factor(policy) — a 1.0 / 0.5 factor, not
    a kill switch: throttled inference still answers, slower
    (fewer parallel / smaller batches at the consumer's
    discretion). The consumer call site is likewise a
    coordinator integration step, not edited here.

This module is PURE stdlib + lazy psutil + a lazy optional
winrt attempt. It imports nothing from the tree, so watchers
can import it lazily without a cycle. Never raises.
"""

#: Steady-poll multiplier on battery / at low battery.
BATTERY_MULTIPLIER = 4
LOW_BATTERY_MULTIPLIER = 8

#: Battery percent at/below which the stronger multiplier
#: applies (mirrors watchers.BATTERY_LOW_PERCENT without
#: importing watchers).
LOW_BATTERY_PERCENT = 20

#: Local-inference factor on battery (1.0 = full speed).
BATTERY_INFERENCE_FACTOR = 0.5


def detect_battery(reader=None):
    """Battery state: {"percent", "charging", "minutes_left"}
    or None (no battery / no psutil / probe failure). `reader`
    is an injectable zero-arg callable returning a psutil-like
    battery object (percent, power_plugged, secsleft) or None
    — tests inject fakes; production uses psutil lazily.
    Never raises."""
    try:
        if reader is not None:
            battery = reader()
        else:
            import psutil

            battery = psutil.sensors_battery()
        if battery is None:
            return None
        percent = int(round(float(battery.percent)))
        charging = bool(battery.power_plugged)
        minutes = None
        secs = getattr(battery, "secsleft", None)
        if isinstance(secs, (int, float)) and secs > 0:
            minutes = int(round(secs / 60))
        return {"percent": percent, "charging": charging,
                "minutes_left": minutes}
    except Exception:  # noqa: BLE001 — absent reading, never raise
        return None


def _windows_metered_via_winrt():
    """Windows Cost API verdict: True (metered) / False
    (unmetered) / None (unknown — no profile, no binding, or
    any failure). Unexecuted on this Linux VM: the `winrt`
    import fails here by design, so this returns None here;
    on Windows without a winrt binding it also returns None
    (see module docstring: FIELD-OWED). Never raises."""
    try:
        from winrt.windows.networking.connectivity import (  # type: ignore
            NetworkInformation,
            NetworkCostType,
        )

        profile = NetworkInformation.get_internet_connection_profile()
        if profile is None:
            return None
        cost = profile.get_connection_cost()
        if cost is None:
            return None
        cost_type = cost.network_cost_type
        if cost_type in (NetworkCostType.FIXED,
                         NetworkCostType.VARIABLE):
            return True
        if cost_type == NetworkCostType.UNRESTRICTED:
            return False
        return None
    except Exception:  # noqa: BLE001
        return None


def detect_metered(reader=None):
    """Metered state: True / False / None (unknown). `reader`
    is an injectable zero-arg callable returning True/False/
    None; without one, the Windows Cost API attempt runs (and
    returns None wherever WinRT is unavailable). Never raises,
    and never guesses from SSID or interface type."""
    try:
        if reader is not None:
            value = reader()
            return None if value is None else bool(value)
        return _windows_metered_via_winrt()
    except Exception:  # noqa: BLE001
        return None


def current_policy(*, battery=None, metered=None,
                   battery_reader=None, metered_reader=None):
    """The PF-12 policy for the current (or injected) state.
    `battery` / `metered` may be passed directly (tests, or a
    caller that already probed); otherwise the readers run.
    Returns a dict with: power_source ("ac" / "battery" /
    "unknown"), battery_pct, charging, metered (True/False/
    None), throttle_watchers, throttle_local_inference,
    watcher_multiplier, local_inference_factor,
    pause_downloads, pause_updates, and a human `reason`.
    Never raises."""
    try:
        if battery is None and battery_reader is not None:
            battery = detect_battery(reader=battery_reader)
        elif battery is None:
            battery = detect_battery()
        if metered is None and metered_reader is not None:
            metered = detect_metered(reader=metered_reader)
        elif metered is None:
            metered = detect_metered()
    except Exception:  # noqa: BLE001
        battery, metered = None, None
    charging = None
    percent = None
    if isinstance(battery, dict):
        charging = bool(battery.get("charging"))
        try:
            percent = int(battery.get("percent"))
        except (TypeError, ValueError):
            percent = None
    if charging:
        power_source = "ac"
    elif isinstance(battery, dict):
        power_source = "battery"
    else:
        power_source = "unknown"
    on_battery = power_source == "battery"
    if on_battery and percent is not None \
            and percent <= LOW_BATTERY_PERCENT:
        multiplier = LOW_BATTERY_MULTIPLIER
    elif on_battery:
        multiplier = BATTERY_MULTIPLIER
    else:
        multiplier = 1
    metered_bool = metered is True
    reasons = []
    if on_battery:
        pct = f" {percent}%" if percent is not None else ""
        reasons.append(f"on battery{pct} — background checks "
                       f"slowed x{multiplier}")
    else:
        reasons.append("on AC power (or no battery) — full speed")
    if metered_bool:
        reasons.append("metered connection — downloads paused")
    elif metered is False:
        reasons.append("unmetered connection — downloads allowed")
    else:
        reasons.append("metered state unknown — downloads allowed")
    return {
        "power_source": power_source,
        "battery_pct": percent,
        "charging": charging,
        "metered": metered,
        "throttle_watchers": on_battery,
        "throttle_local_inference": on_battery,
        "watcher_multiplier": multiplier,
        "local_inference_factor": (
            BATTERY_INFERENCE_FACTOR if on_battery else 1.0),
        "pause_downloads": metered_bool,
        "pause_updates": metered_bool,
        "reason": "; ".join(reasons),
    }


def watcher_poll_seconds(base_seconds, policy=None):
    """The steady poll a watcher loop should wait, given a
    policy dict (or None = unthrottled). Pure arithmetic on
    the policy's multiplier — the MEASURABLE cadence delta
    PF-12's acceptance line demands. Never raises."""
    try:
        base = float(base_seconds)
        if not isinstance(policy, dict):
            return base
        if policy.get("throttle_watchers"):
            mult = policy.get("watcher_multiplier")
            try:
                mult = float(mult)
            except (TypeError, ValueError):
                mult = float(BATTERY_MULTIPLIER)
            if mult < 1:
                mult = 1.0
            return base * mult
        return base
    except Exception:  # noqa: BLE001
        try:
            return float(base_seconds)
        except Exception:  # noqa: BLE001
            return base_seconds


def downloads_allowed(policy=None):
    """False exactly when the policy pauses downloads (metered).
    Never raises."""
    try:
        if isinstance(policy, dict):
            return not bool(policy.get("pause_downloads"))
        return True
    except Exception:  # noqa: BLE001
        return True


def updates_allowed(policy=None):
    """False exactly when the policy pauses updates (metered).
    Never raises."""
    try:
        if isinstance(policy, dict):
            return not bool(policy.get("pause_updates"))
        return True
    except Exception:  # noqa: BLE001
        return True


def local_inference_factor(policy=None):
    """1.0 full speed, 0.5 throttled on battery. Never raises."""
    try:
        if isinstance(policy, dict):
            factor = policy.get("local_inference_factor")
            return float(factor) if factor is not None else 1.0
        return 1.0
    except Exception:  # noqa: BLE001
        return 1.0


def policy_summary(policy=None):
    """One display line for the PF-7 surface / Settings card.
    Never raises."""
    try:
        if not isinstance(policy, dict):
            policy = current_policy()
        bits = []
        if policy.get("power_source") == "battery":
            pct = policy.get("battery_pct")
            bits.append(
                f"On battery{f' ({pct}%)' if pct is not None else ''} "
                f"— background checks slowed "
                f"x{policy.get('watcher_multiplier', BATTERY_MULTIPLIER)}, "
                f"local inference throttled")
        else:
            bits.append("On AC power — full speed")
        if policy.get("metered") is True:
            bits.append("metered connection — downloads + updates paused")
        elif policy.get("metered") is False:
            bits.append("unmetered connection — downloads allowed")
        else:
            bits.append("metered state unknown")
        return "; ".join(bits) + "."
    except Exception:  # noqa: BLE001
        return "Power state unavailable."


def watcher_policy_fn(*, battery_reader=None, metered_reader=None):
    """A zero-arg callable returning current_policy() — the
    shape WatcherEngine's power_policy parameter takes. Reader
    seams pass straight through (production: leave both None
    for the real probes). Never raises on construction; the
    returned callable never raises either."""
    def _fn():
        try:
            return current_policy(battery_reader=battery_reader,
                                  metered_reader=metered_reader)
        except Exception:  # noqa: BLE001 — fail open, never throttle
            return {"power_source": "unknown", "battery_pct": None,
                    "charging": None, "metered": None,
                    "throttle_watchers": False,
                    "throttle_local_inference": False,
                    "watcher_multiplier": 1,
                    "local_inference_factor": 1.0,
                    "pause_downloads": False, "pause_updates": False,
                    "reason": "power state unavailable — full speed"}

    return _fn


def apply_to_watcher(engine):
    """Attach the live policy to a WatcherEngine (sets its
    _power_policy) and return the engine. Never raises."""
    try:
        engine._power_policy = watcher_policy_fn()
    except Exception:  # noqa: BLE001
        pass
    return engine
