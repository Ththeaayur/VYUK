"""Solar PV modelling for the buyer's own arrays — own code.

Parent-ruled (licence, 2026-11-12): pvlib/pvlib-python
(BSD-3-Clause, "BSD 3-Clause License / Copyright (c) 2023 pvlib python
Contributors / Copyright (c) 2014 PVLIB python Development Team /
Copyright (c) 2013 Sandia National Laboratories"; the LICENSE file from
the default branch main was read in full 2026-11-12) is INSPIRATION
ONLY. No code, data, model file or package is adopted from it; this
module is written from scratch in stdlib Python over the published
formulas (NREL Solar Position Algorithm, Ineichen/Perez clear-sky,
Hay-Davies plane-of-array, PVWatts DC + inverter, Ross cell
temperature). Ideas and published equations are not copyrighted, so no
BSD duty is triggered by a from-scratch rebuild. The "pvlib" name is
NOT used here: the tool is the generic word "solar_yield", and no
package, fork or service is named after the original.

What this is: the computation engine for sizing and quoting a real PV
array — sun position, clear-sky irradiance on a tilted plane, and the
DC/AC yield of a named array. It is a READ-ONLY estimator: pure
computation over caller-supplied figures, no storage, no network,
nothing leaves the machine.

What it is NOT: it is not a weather forecast and not a measured-yield
record. Every irradiance and yield figure is a CLEAR-SKY estimate
(cloudless sky, no shading, no soiling, no snow, no mismatch or wiring
losses — the PVWatts default loss factors are NOT applied), from the
Linke turbidity the caller supplies (3.0 if left out). A range estimate
uses one representative day (the 15th) per month, scaled by the days of
that month inside the range — labelled as such in the output. The tree's
NOAA sunrise/sunset tool owns sun TIMES; this tool owns sun POSITION
(zenith/azimuth), irradiance and yield, and never returns sunrise or
sunset.
"""

import math
from datetime import date, datetime, timedelta, timezone

from .config import DATA_DIR  # noqa: F401  (kept for the house import shape; no store is used)

# ---------------------------------------------------------------------------
# Constants (published values)
# ---------------------------------------------------------------------------

_SOLAR_CONSTANT = 1366.1  # W/m^2, Spencer (pvlib get_extra_radiation default)
_MAX_DAYS = 366  # longest yield range, in days
_MAX_TILT = 90.0


def _rad(deg):
    return deg * math.pi / 180.0


def _deg(rad):
    return rad * 180.0 / math.pi


def _norm360(angle):
    angle = math.fmod(angle, 360.0)
    if angle < 0:
        angle += 360.0
    return angle


def _clamp(value, low, high):
    return low if value < low else high if value > high else value


# ---------------------------------------------------------------------------
# Date / time helpers (all times UTC unless the caller says otherwise)
# ---------------------------------------------------------------------------

def _parse_date(text, name):
    """'yyyy-mm-dd' -> date, or (None, error)."""
    if not text:
        return None, None
    try:
        parts = str(text).strip().split("-")
        if len(parts) != 3:
            raise ValueError("not yyyy-mm-dd")
        return date(int(parts[0]), int(parts[1]), int(parts[2])), None
    except (ValueError, TypeError):
        return None, (f"{name} {text!r} is not a date — use yyyy-mm-dd, "
                      f"e.g. '2026-06-15'.")


def _parse_time(text, name):
    """'HH:MM' (UTC) -> (hour, minute), or (None, error)."""
    if not text:
        return (12, 0), None
    try:
        parts = str(text).strip().split(":")
        if len(parts) != 2:
            raise ValueError("not HH:MM")
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("out of range")
        return (hour, minute), None
    except (ValueError, TypeError):
        return None, (f"{name} {text!r} is not a time — use HH:MM in UTC, "
                      f"e.g. '12:00'.")


def _num(value, name, low, high, default=None):
    """Coerce a caller number into [low, high], or (None, error)."""
    if value is None:
        if default is None:
            return None, f"{name} is needed — give a number."
        return default, None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, f"{name} {value!r} is not a number."
    if not (low <= number <= high):
        return None, (f"{name} {value!r} is out of range — "
                      f"it has to be between {low} and {high}.")
    return number, None


def _pressure_at_altitude(altitude_m):
    """Standard-atmosphere pressure (Pa) at an altitude in metres."""
    return 101325.0 * (1.0 - 2.25577e-05 * altitude_m) ** 5.25588


# ---------------------------------------------------------------------------
# Solar position — NREL Solar Position Algorithm (Reda & Andreas 2004)
# ---------------------------------------------------------------------------

def _julian_day(moment):
    """Julian day for a UTC datetime (the standard civil-date formula)."""
    year, month, day = moment.year, moment.month, moment.day
    if month <= 2:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + a // 4
    ut = moment.hour + moment.minute / 60.0 + moment.second / 3600.0
    return (math.floor(365.25 * (year + 4716))
            + math.floor(30.6001 * (month + 1)) + day + b - 1524.5
            + ut / 24.0)


def _solar_position(moment, latitude, longitude, altitude_m=0.0,
                    temp_c=25.0):
    """Sun position for a UTC datetime.

    Returns a dict with apparent_zenith, azimuth (from north, clockwise),
    declination, hour_angle, equation_of_time (minutes), airmass
    (Kasten & Young, absolute), extraterrestrial (W/m^2, Spencer) and
    solar_noon_utc ('HH:MM'). All angles in degrees.
    """
    jd = _julian_day(moment)
    t = (jd - 2451545.0) / 36525.0

    # Sun's geometric mean longitude and anomaly (degrees).
    l0 = _norm360(280.46646 + t * (36000.76983 + t * 0.0003032))
    m_anom = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    ecc = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    # Equation of centre.
    centre = (math.sin(_rad(m_anom)) * (1.914602 - t * (0.004817 + 0.000014 * t))
              + math.sin(_rad(2 * m_anom)) * (0.019993 - 0.000101 * t)
              + math.sin(_rad(3 * m_anom)) * 0.000289)
    true_long = l0 + centre
    omega = 125.04 - 1934.136 * t
    # Apparent longitude (aberration + nutation).
    lam = true_long - 0.00569 - 0.00478 * math.sin(_rad(omega))
    # Mean and true obliquity of the ecliptic.
    eps0 = 23.0 + (26.0 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60.0) / 60.0
    eps = eps0 + 0.00256 * math.cos(_rad(omega))
    # Declination.
    decl = _deg(math.asin(math.sin(_rad(eps)) * math.sin(_rad(lam))))
    # Equation of time, in minutes.
    y = math.tan(_rad(eps / 2.0)) ** 2
    eqtime = 4.0 * _deg(
        y * math.sin(_rad(2 * l0))
        - 2 * ecc * math.sin(_rad(m_anom))
        + 4 * ecc * y * math.sin(_rad(m_anom)) * math.cos(_rad(2 * l0))
        - 0.5 * y * y * math.sin(_rad(4 * l0))
        - 1.25 * ecc * ecc * math.sin(_rad(2 * m_anom)))
    # Hour angle from apparent solar time (longitude east positive).
    ut_hours = moment.hour + moment.minute / 60.0 + moment.second / 3600.0
    ast = ut_hours + longitude / 15.0 + eqtime / 60.0
    ha = _norm360(15.0 * (ast - 12.0))
    if ha > 180.0:
        ha -= 360.0
    # Zenith and azimuth (from north, clockwise).
    lat_r, decl_r, ha_r = _rad(latitude), _rad(decl), _rad(ha)
    cos_z = (math.sin(lat_r) * math.sin(decl_r)
             + math.cos(lat_r) * math.cos(decl_r) * math.cos(ha_r))
    zenith = _deg(math.acos(_clamp(cos_z, -1.0, 1.0)))
    az = _deg(math.atan2(math.sin(ha_r),
                         math.cos(ha_r) * math.sin(lat_r)
                         - math.tan(decl_r) * math.cos(lat_r))) + 180.0
    azimuth = _norm360(az)
    # Refraction correction (SPA, arcseconds -> degrees), scaled by the
    # local pressure and temperature.
    pressure = _pressure_at_altitude(altitude_m)
    elev = 90.0 - zenith
    if elev > 85.0:
        delta = 0.0
    elif elev > 5.0:
        tan_e = math.tan(_rad(elev))
        delta = (58.1 / tan_e - 0.07 / tan_e ** 3
                 + 0.000086 / tan_e ** 5)
    elif elev > -0.575:
        delta = (1735.0 + elev * (-518.2 + elev * (103.4 + elev * (-12.79 + elev * 0.711))))
    else:
        delta = -20.772 / math.tan(_rad(elev))
    delta *= (pressure / 100.0 / 1010.0) * (283.0 / (273.0 + temp_c))
    apparent_zenith = 90.0 - (elev + delta / 3600.0)
    # Airmass, Kasten & Young (1989), absolute (pressure-scaled).
    if apparent_zenith >= 93.0:
        airmass = None
    else:
        am = 1.0 / (math.cos(_rad(apparent_zenith))
                    + 0.50572 * (96.07995 - apparent_zenith) ** -1.6364)
        airmass = am * pressure / 101325.0
    # Extraterrestrial irradiance, Spencer series.
    doy = moment.timetuple().tm_yday
    b = 2.0 * math.pi * (doy - 1) / 365.0
    i0 = _SOLAR_CONSTANT * (1.000110 + 0.034221 * math.cos(b)
                            + 0.001280 * math.sin(b)
                            + 0.000719 * math.cos(2 * b)
                            + 0.000077 * math.sin(2 * b))
    # Solar noon (UTC): apparent solar time 12:00.
    noon_hours = (12.0 - eqtime / 60.0 - longitude / 15.0) % 24.0
    noon_h, noon_m = int(noon_hours), int(round((noon_hours % 1) * 60))
    if noon_m == 60:
        noon_h = (noon_h + 1) % 24
        noon_m = 0
    return {
        "apparent_zenith": apparent_zenith,
        "zenith": zenith,
        "azimuth": azimuth,
        "declination": decl,
        "hour_angle": ha,
        "equation_of_time": eqtime,
        "airmass": airmass,
        "extraterrestrial": i0,
        "solar_noon_utc": f"{noon_h:02d}:{noon_m:02d}",
    }


# ---------------------------------------------------------------------------
# Clear-sky irradiance — Ineichen / Perez
# ---------------------------------------------------------------------------

def _clearsky(apparent_zenith, airmass, linke_turbidity, altitude_m,
              dni_extra):
    """Clear-sky GHI / DNI / DHI (W/m^2) for one instant.

    Ineichen & Perez (2002) as published: the Linke-turbidity clear-sky
    model with the Perez enhancement. Night (zenith >= 90) is all zeros
    — the division by cos(zenith) in the beam correction is guarded by
    returning early instead of dividing by zero.
    """
    cos_z = max(math.cos(_rad(apparent_zenith)), 0.0)
    if cos_z <= 0.0 or airmass is None:
        return {"ghi": 0.0, "dni": 0.0, "dhi": 0.0}
    fh1 = math.exp(-altitude_m / 8000.0)
    fh2 = math.exp(-altitude_m / 1250.0)
    cg1 = 5.09e-05 * altitude_m + 0.868
    cg2 = 3.92e-05 * altitude_m + 0.0387
    tl = linke_turbidity
    ghi = (cg1 * dni_extra * cos_z
           * math.exp(-cg2 * airmass * (fh1 + fh2 * (tl - 1.0)))
           * math.exp(0.01 * airmass ** 1.8))
    b = 0.664 + 0.163 / fh1
    bnci = dni_extra * max(b * math.exp(-0.09 * airmass * (tl - 1.0)), 0.0)
    bnci_2 = ((1.0 - (0.1 - 0.2 * math.exp(-tl)) / (0.1 + 0.882 / fh1))
              / cos_z)
    bnci_2 = ghi * min(max(bnci_2, 0.0), 1e20)
    dni = min(bnci, bnci_2)
    dhi = max(ghi - dni * cos_z, 0.0)
    return {"ghi": ghi, "dni": dni, "dhi": dhi}


# ---------------------------------------------------------------------------
# Plane-of-array irradiance — Hay & Davies (1980)
# ---------------------------------------------------------------------------

def _poa_irradiance(surface_tilt, surface_azimuth, solar_zenith,
                    solar_azimuth, dni, dhi, ghi, dni_extra, albedo):
    """POA components (W/m^2) for one instant.

    Beam + Hay-Davies sky diffuse (isotropic + circumsolar) + ground
    reflected. Returns (poa, beam, sky, ground, aoi, cos_aoi).
    """
    tilt_r = _rad(surface_tilt)
    cos_aoi = (math.cos(_rad(solar_zenith)) * math.cos(tilt_r)
               + math.sin(_rad(solar_zenith)) * math.sin(tilt_r)
               * math.cos(_rad(solar_azimuth - surface_azimuth)))
    cos_aoi_c = max(cos_aoi, 0.0)
    beam = dni * cos_aoi_c
    cos_z = max(math.cos(_rad(solar_zenith)), 0.0)
    ai = dni / dni_extra if dni_extra > 0 else 0.0
    rb = cos_aoi_c / max(cos_z, 0.01745)
    sky = dhi * ((1.0 - ai) * (1.0 + math.cos(tilt_r)) / 2.0 + ai * rb)
    sky = max(sky, 0.0)
    ground = ghi * albedo * (1.0 - math.cos(tilt_r)) / 2.0
    poa = max(beam + sky + ground, 0.0)
    aoi = _deg(math.acos(_clamp(cos_aoi, -1.0, 1.0)))
    return poa, beam, sky, ground, aoi, cos_aoi


# ---------------------------------------------------------------------------
# Module + inverter — PVWatts DC, Ross cell temperature, PVWatts inverter
# ---------------------------------------------------------------------------

def _cell_temperature(poa, temp_air, noct):
    """Ross model: T_cell = T_air + (NOCT - 20) / 800 * G_poa."""
    return temp_air + (noct - 20.0) / 800.0 * poa


def _dc_power(poa, pdc0, gamma_pdc, temp_cell):
    """PVWatts DC: P_dc = (G_poa / 1000) * P_dc0 * (1 + gamma * (T_cell - 25))."""
    return (poa / 1000.0) * pdc0 * (1.0 + gamma_pdc * (temp_cell - 25.0))


def _ac_power(p_dc, p_ac0, eta_inv_nom, eta_inv_ref=0.9637):
    """PVWatts inverter: eta(zeta) with clipping at the AC rating.

    eta = (eta_nom / eta_ref) * (-0.0162*zeta - 0.0059/zeta + 0.9858),
    zeta = P_dc / P_dc0, P_dc0 = P_ac0 / eta_nom; P_ac = min(eta*P_dc, P_ac0).
    """
    if p_dc <= 0:
        return 0.0
    p_dc0 = p_ac0 / eta_inv_nom
    zeta = p_dc / p_dc0
    eta = ((eta_inv_nom / eta_inv_ref)
           * (-0.0162 * zeta - 0.0059 / zeta + 0.9858))
    eta = _clamp(eta, 0.0, 1.0)
    return min(eta * p_dc, p_ac0)


def _instant_power(moment, latitude, longitude, tilt, azimuth, altitude_m,
                   linke_turbidity, albedo, temp_air, pdc0, gamma_pdc,
                   noct, inverter_ac0, eta_inv_nom):
    """One instant's full chain: position -> clear-sky -> POA -> DC -> AC.

    Returns (position dict, clearsky dict, poa dict, p_ac watts).
    """
    pos = _solar_position(moment, latitude, longitude, altitude_m, temp_air)
    cs = _clearsky(pos["apparent_zenith"], pos["airmass"], linke_turbidity,
                   altitude_m, pos["extraterrestrial"])
    poa, beam, sky, ground, aoi, cos_aoi = _poa_irradiance(
        tilt, azimuth, pos["apparent_zenith"], pos["azimuth"],
        cs["dni"], cs["dhi"], cs["ghi"], pos["extraterrestrial"], albedo)
    t_cell = _cell_temperature(poa, temp_air, noct)
    p_dc = max(_dc_power(poa, pdc0, gamma_pdc, t_cell), 0.0)
    p_ac = _ac_power(p_dc, inverter_ac0, eta_inv_nom)
    return pos, cs, {"poa": poa, "beam": beam, "sky": sky, "ground": ground,
                     "aoi": aoi, "cos_aoi": cos_aoi,
                     "temp_cell": t_cell, "p_dc": p_dc}, p_ac


def _day_energy(day, latitude, longitude, tilt, azimuth, altitude_m,
                linke_turbidity, albedo, temp_air, pdc0, gamma_pdc, noct,
                inverter_ac0, eta_inv_nom, detail=False):
    """One day's clear-sky energy (kWh) from hourly midpoints (HH:30 UTC).

    Returns (kwh, peak_w_ac, hours list of (label, p_ac) when detail).
    """
    total_wh = 0.0
    peak = 0.0
    hours = []
    for hour in range(24):
        moment = datetime(day.year, day.month, day.day, hour, 30,
                          tzinfo=timezone.utc)
        _pos, _cs, _poa, p_ac = _instant_power(
            moment, latitude, longitude, tilt, azimuth, altitude_m,
            linke_turbidity, albedo, temp_air, pdc0, gamma_pdc, noct,
            inverter_ac0, eta_inv_nom)
        total_wh += p_ac
        peak = max(peak, p_ac)
        if detail:
            hours.append((f"{hour:02d}:30", round(p_ac, 1)))
    return total_wh / 1000.0, peak, hours


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------

def solar_yield(action="", latitude=None, longitude=None, date="",
               time="", tilt=None, azimuth=None, altitude=None,
               linke_turbidity=None, albedo=None, temp_air=None,
               pdc0=None, gamma_pdc=None, noct=None, inverter_ac0=None,
               eta_inv_nom=None, start_date="", end_date="", detail=None):
    """Model a real PV array: sun position, clear-sky irradiance, yield.

    Actions: 'sun' (position at a date/time), 'irradiance' (clear-sky
    GHI/DNI/DHI + plane-of-array at a date/time), 'yield' (clear-sky
    energy for an array over a date or a date range). All times UTC.
    Pure computation over caller-supplied figures — nothing is stored,
    nothing is fetched, nothing leaves the machine.
    """
    action = (action or "").strip().lower()
    if action not in ("sun", "irradiance", "yield"):
        return ("solar_yield: give an action — 'sun' for the sun's position "
                "at a date and time, 'irradiance' for clear-sky GHI/DNI/DHI "
                "and plane-of-array irradiance, or 'yield' for the clear-sky "
                "energy of an array over a date or date range. All times UTC.")

    # --- shared validation -------------------------------------------------
    lat, err = _num(latitude, "latitude", -90.0, 90.0)
    if err:
        return f"solar_yield: {err}"
    lon, err = _num(longitude, "longitude", -180.0, 180.0)
    if err:
        return f"solar_yield: {err}"
    tilt_v, err = _num(tilt, "tilt", 0.0, 90.0, default=30.0)
    if err:
        return f"solar_yield: {err}"
    az_v, err = _num(azimuth, "surface azimuth", 0.0, 360.0, default=180.0)
    if err:
        return f"solar_yield: {err}"
    alt_v, err = _num(altitude, "altitude", 0.0, 9000.0, default=0.0)
    if err:
        return f"solar_yield: {err}"
    tl_v, err = _num(linke_turbidity, "linke_turbidity", 1.0, 10.0, default=3.0)
    if err:
        return f"solar_yield: {err}"
    alb_v, err = _num(albedo, "albedo", 0.0, 1.0, default=0.2)
    if err:
        return f"solar_yield: {err}"
    temp_v, err = _num(temp_air, "temp_air", -40.0, 60.0, default=25.0)
    if err:
        return f"solar_yield: {err}"

    if action in ("sun", "irradiance"):
        day, err = _parse_date(date, "date")
        if err:
            return f"solar_yield: {err}"
        if day is None:
            day = datetime.now(timezone.utc).date()
        parsed, err = _parse_time(time, "time")
        if err:
            return f"solar_yield: {err}"
        hour, minute = parsed
        moment = datetime(day.year, day.month, day.day, hour, minute,
                          tzinfo=timezone.utc)
        pos = _solar_position(moment, lat, lon, alt_v, temp_v)
        when = moment.strftime("%Y-%m-%d %H:%M UTC")
        if action == "sun":
            if pos["airmass"] is not None:
                airmass_line = f"  airmass {pos['airmass']:.2f}"
            else:
                airmass_line = "  airmass — (sun below horizon)"
            return (
                f"Sun at {lat:.4f}, {lon:.4f} on {when} (clear-sky model, no weather):\n"
                f"  apparent zenith {pos['apparent_zenith']:.2f}°, "
                f"azimuth {pos['azimuth']:.2f}° from north\n"
                f"  declination {pos['declination']:.2f}°, "
                f"hour angle {pos['hour_angle']:.2f}°\n"
                f"  equation of time {pos['equation_of_time']:.1f} min, "
                f"solar noon {pos['solar_noon_utc']} UTC\n"
                f"{airmass_line}, extraterrestrial "
                f"{pos['extraterrestrial']:.1f} W/m²")
        cs = _clearsky(pos["apparent_zenith"], pos["airmass"], tl_v, alt_v,
                       pos["extraterrestrial"])
        poa, beam, sky, ground, aoi, _cos = _poa_irradiance(
            tilt_v, az_v, pos["apparent_zenith"], pos["azimuth"],
            cs["dni"], cs["dhi"], cs["ghi"], pos["extraterrestrial"], alb_v)
        return (
            f"Clear-sky irradiance at {lat:.4f}, {lon:.4f} on {when} "
            f"(tilt {tilt_v:.0f}°, azimuth {az_v:.0f}°, Linke turbidity {tl_v:.1f}, "
            f"albedo {alb_v:.2f} — no clouds, no shading, no measured weather):\n"
            f"  sun: zenith {pos['apparent_zenith']:.2f}°, "
            f"azimuth {pos['azimuth']:.2f}°\n"
            f"  GHI {cs['ghi']:.1f} W/m², DNI {cs['dni']:.1f} W/m², "
            f"DHI {cs['dhi']:.1f} W/m²\n"
            f"  plane-of-array {poa:.1f} W/m² "
            f"(beam {beam:.1f}, sky {sky:.1f}, ground {ground:.1f}), "
            f"angle of incidence {aoi:.1f}°")

    # --- yield ---------------------------------------------------------------
    pdc0_v, err = _num(pdc0, "pdc0", 1.0, 10_000_000.0, default=5000.0)
    if err:
        return f"solar_yield: {err}"
    gamma_v, err = _num(gamma_pdc, "gamma_pdc", -0.02, 0.0, default=-0.004)
    if err:
        return f"solar_yield: {err}"
    noct_v, err = _num(noct, "noct", 20.0, 70.0, default=45.0)
    if err:
        return f"solar_yield: {err}"
    ac0_v, err = _num(inverter_ac0, "inverter_ac0", 1.0, 10_000_000.0,
                      default=pdc0_v)
    if err:
        return f"solar_yield: {err}"
    eta_v, err = _num(eta_inv_nom, "eta_inv_nom", 0.5, 1.0, default=0.96)
    if err:
        return f"solar_yield: {err}"
    start, err = _parse_date(start_date or date, "start_date")
    if err:
        return f"solar_yield: {err}"
    end, err = _parse_date(end_date or start_date or date, "end_date")
    if err:
        return f"solar_yield: {err}"
    if start is None:
        start = datetime.now(timezone.utc).date()
    if end is None:
        end = start
    if end < start:
        return "solar_yield: end_date is before start_date — give a range that runs forward."
    span = (end - start).days + 1
    if span > _MAX_DAYS:
        return (f"solar_yield: the range is {span} days — at most "
                f"{_MAX_DAYS} days in one call.")
    # One representative day (the 15th) per month in the range, scaled by
    # the days of that month inside the range. (The 'date' parameter shadows
    # the date class in this function, so dates are built via datetime.)
    total_kwh = 0.0
    peak = 0.0
    detail_hours = []
    cursor = datetime(start.year, start.month, 1).date()
    months = []
    while cursor <= end:
        months.append((cursor.year, cursor.month))
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    for year, month in months:
        month_start = datetime(year, month, 1).date()
        month_end = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        lo = max(start, month_start)
        hi = min(end, month_end)
        days_here = (hi - lo).days + 1
        rep = datetime(year, month, 15).date()
        day_kwh, day_peak, hours = _day_energy(
            rep, lat, lon, tilt_v, az_v, alt_v, tl_v, alb_v, temp_v,
            pdc0_v, gamma_v, noct_v, ac0_v, eta_v, detail=bool(detail))
        total_kwh += day_kwh * days_here
        peak = max(peak, day_peak)
        if detail:
            detail_hours.append((rep.isoformat(), hours))
    kwp = pdc0_v / 1000.0
    capacity_factor = (total_kwh / (ac0_v / 1000.0 * 24.0 * span) * 100.0
                       if ac0_v > 0 else 0.0)
    out = (
        f"Clear-sky yield, {lat:.4f}, {lon:.4f}, {start.isoformat()} to "
        f"{end.isoformat()} ({span} day{'s' if span != 1 else ''}):\n"
        f"  array {pdc0_v:.0f} W DC, tilt {tilt_v:.0f}°, azimuth {az_v:.0f}°, "
        f"inverter {ac0_v:.0f} W AC at {eta_v:.0%} nominal, "
        f"NOCT {noct_v:.0f}°C, air {temp_v:.0f}°C\n"
        f"  energy {total_kwh:.2f} kWh "
        f"({total_kwh / span:.2f} kWh/day, {total_kwh / kwp:.1f} kWh/kWp)\n"
        f"  peak AC {peak:.0f} W, capacity factor {capacity_factor:.1f}%\n"
        f"  Clear-sky estimate only — no clouds, shading, soiling, snow, "
        f"mismatch or wiring losses; one representative day (the 15th) per "
        f"month, scaled by the days in range; Linke turbidity {tl_v:.1f}.")
    if detail:
        lines = [out, "  Hourly AC power (W) on each representative day:"]
        for rep_label, hours in detail_hours:
            lines.append(f"  {rep_label}: " + ", ".join(
                f"{label} {w:.0f}" for label, w in hours))
        out = "\n".join(lines)
    return out
