"""Suite for R17-8 pvlib inspiration: solar_yield (stdlib PV modelling).

Cross-checks the from-scratch SPA against the tree's own NOAA
sunrise/sunset approximation (same Spencer declination series the tree
already ships in core/skills_travel.py), checks the physical bounds of
the clear-sky / plane-of-array chain, and checks yield sanity including
polar-night zero and inverter clipping. No operator-supplied artifacts
are needed, so nothing skips.
"""
import math
import unittest
from datetime import datetime, timezone

from core.solar_yield import (
    _clearsky,
    _poa_irradiance,
    _solar_position,
    solar_yield,
)


def _noaa_declination(day):
    """The tree's own NOAA/Spencer declination series (skills_travel.py)."""
    gamma = 2 * math.pi / 365.0 * (day.timetuple().tm_yday - 1 + 0.5)
    return math.degrees(
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma))


def _at(day, hour, minute, lat, lon):
    return _solar_position(
        datetime(day.year, day.month, day.day, hour, minute,
                 tzinfo=timezone.utc), lat, lon)


class TestSolarPosition(unittest.TestCase):
    def test_solstice_declination(self):
        pos = _at(datetime(2026, 6, 21), 12, 0, 51.5, 0.0)
        self.assertAlmostEqual(pos["declination"], 23.44, delta=0.15)

    def test_december_solstice_declination(self):
        pos = _at(datetime(2026, 12, 21), 12, 0, 51.5, 0.0)
        self.assertAlmostEqual(pos["declination"], -23.44, delta=0.15)

    def test_equinox_declination_near_zero(self):
        pos = _at(datetime(2026, 3, 20), 12, 0, 51.5, 0.0)
        self.assertLess(abs(pos["declination"]), 0.6)

    def test_noaa_crosscheck_four_dates(self):
        # The NOAA series is the approximation (its own docstring: "within
        # a few minutes, not exact") — its declination runs up to ~0.25 deg
        # off the SPA near the equinoxes, where the Fourier series is
        # least accurate, and within 0.02 deg at the solstices. A 0.3 deg
        # band still catches any gross error (wrong sign, a degrees /
        # radians mix-up) while honouring what the approximation is.
        for day in (datetime(2026, 3, 20), datetime(2026, 6, 21),
                    datetime(2026, 9, 22), datetime(2026, 12, 21)):
            pos = _at(day, 12, 0, 51.5, 0.0)
            self.assertAlmostEqual(pos["declination"],
                                   _noaa_declination(day.date()),
                                   delta=0.3,
                                   msg=f"declination on {day.date()}")

    def test_solar_noon_azimuth_north_hemisphere(self):
        # At solar noon in London the sun is due south.
        pos = _at(datetime(2026, 6, 21), 12, 2, 51.5, 0.0)
        self.assertAlmostEqual(pos["azimuth"], 180.0, delta=1.0)
        self.assertAlmostEqual(pos["apparent_zenith"], 51.5 - 23.44,
                               delta=0.5)

    def test_solar_noon_azimuth_south_hemisphere(self):
        # At solar noon in Sydney in December the sun is due north.
        # The noon time is taken from the function itself (01:53 UTC),
        # not guessed: near the meridian the azimuth swings fast, so a
        # one-minute error in the assumed noon is a degree in azimuth.
        noon = _at(datetime(2026, 12, 21), 12, 0, -33.87, 151.21)
        hour, minute = (int(p) for p in noon["solar_noon_utc"].split(":"))
        pos = _at(datetime(2026, 12, 21), hour, minute, -33.87, 151.21)
        az = pos["azimuth"]
        self.assertTrue(az < 1.0 or az > 359.0, f"azimuth {az}")

    def test_solar_noon_time_london(self):
        pos = _at(datetime(2026, 6, 21), 12, 0, 51.5, 0.0)
        hours, minutes = (int(p) for p in pos["solar_noon_utc"].split(":"))
        self.assertAlmostEqual(hours * 60 + minutes, 12 * 60 + 2, delta=2)

    def test_airmass_and_extraterrestrial_sanity(self):
        pos = _at(datetime(2026, 6, 21), 12, 0, 51.5, 0.0)
        self.assertIsNotNone(pos["airmass"])
        self.assertLess(pos["airmass"], 1.3)
        self.assertGreater(pos["airmass"], 1.0)
        self.assertLess(pos["extraterrestrial"], 1340.0)
        self.assertGreater(pos["extraterrestrial"], 1310.0)

    def test_night_zenith_over_90_airmass_none(self):
        pos = _at(datetime(2026, 6, 21), 2, 0, 51.5, 0.0)
        self.assertGreater(pos["apparent_zenith"], 90.0)
        self.assertIsNone(pos["airmass"])

    def test_equation_of_time_june(self):
        pos = _at(datetime(2026, 6, 21), 12, 0, 51.5, 0.0)
        self.assertAlmostEqual(pos["equation_of_time"], -1.8, delta=0.5)


class TestClearskyAndPoa(unittest.TestCase):
    def _noon(self, day=datetime(2026, 6, 21)):
        pos = _at(day, 12, 0, 51.5, 0.0)
        cs = _clearsky(pos["apparent_zenith"], pos["airmass"], 3.0, 0.0,
                       pos["extraterrestrial"])
        return pos, cs

    def test_noon_physical_bounds(self):
        pos, cs = self._noon()
        self.assertGreater(cs["ghi"], 700.0)
        self.assertLess(cs["ghi"], 1200.0)
        self.assertGreater(cs["dni"], 500.0)
        self.assertLess(cs["dni"], pos["extraterrestrial"])
        self.assertGreater(cs["dhi"], 0.0)
        self.assertLess(cs["dhi"], cs["ghi"])

    def test_night_all_zero(self):
        pos = _at(datetime(2026, 6, 21), 2, 0, 51.5, 0.0)
        cs = _clearsky(pos["apparent_zenith"], pos["airmass"], 3.0, 0.0,
                       pos["extraterrestrial"])
        self.assertEqual(cs, {"ghi": 0.0, "dni": 0.0, "dhi": 0.0})

    def test_poa_components_sum(self):
        pos, cs = self._noon()
        poa, beam, sky, ground, aoi, _cos = _poa_irradiance(
            30.0, 180.0, pos["apparent_zenith"], pos["azimuth"],
            cs["dni"], cs["dhi"], cs["ghi"], pos["extraterrestrial"], 0.2)
        self.assertAlmostEqual(poa, beam + sky + ground, delta=0.01)
        self.assertLess(aoi, 5.0)
        self.assertGreater(poa, cs["ghi"])  # tilt gain at solstice noon

    def test_flat_panel_poa_equals_ghi(self):
        pos, cs = self._noon()
        poa, _b, _s, _g, _a, _c = _poa_irradiance(
            0.0, 180.0, pos["apparent_zenith"], pos["azimuth"],
            cs["dni"], cs["dhi"], cs["ghi"], pos["extraterrestrial"], 0.2)
        self.assertAlmostEqual(poa, cs["ghi"], delta=0.01)

    def test_higher_turbidity_lower_ghi(self):
        pos = _at(datetime(2026, 6, 21), 12, 0, 51.5, 0.0)
        clear = _clearsky(pos["apparent_zenith"], pos["airmass"], 2.0, 0.0,
                          pos["extraterrestrial"])
        hazy = _clearsky(pos["apparent_zenith"], pos["airmass"], 8.0, 0.0,
                         pos["extraterrestrial"])
        self.assertGreater(clear["ghi"], hazy["ghi"])
        self.assertGreater(clear["dni"], hazy["dni"])


class TestYield(unittest.TestCase):
    def test_june_month_london(self):
        out = solar_yield(action="yield", latitude=51.5, longitude=0.0,
                          start_date="2026-06-01", end_date="2026-06-30",
                          pdc0=5000)
        self.assertIn("1105.83 kWh", out)
        self.assertIn("peak AC 4263 W", out)

    def test_polar_night_zero(self):
        out = solar_yield(action="yield", latitude=69.6, longitude=18.9,
                          start_date="2026-01-01", end_date="2026-01-31",
                          pdc0=5000)
        self.assertIn("energy 0.00 kWh", out)
        self.assertIn("peak AC 0 W", out)

    def test_single_day_sane(self):
        out = solar_yield(action="yield", latitude=51.5, longitude=0.0,
                          start_date="2026-06-15", end_date="2026-06-15",
                          pdc0=5000)
        self.assertIn("36.86 kWh", out)

    def test_winter_below_summer(self):
        jan = solar_yield(action="yield", latitude=51.5, longitude=0.0,
                          start_date="2026-01-15", end_date="2026-01-15",
                          pdc0=5000)
        jun = solar_yield(action="yield", latitude=51.5, longitude=0.0,
                          start_date="2026-06-15", end_date="2026-06-15",
                          pdc0=5000)
        self.assertLess(float(jan.split("energy ")[1].split(" kWh")[0]),
                        float(jun.split("energy ")[1].split(" kWh")[0]))

    def test_inverter_clipping(self):
        out = solar_yield(action="yield", latitude=51.5, longitude=0.0,
                          start_date="2026-06-15", end_date="2026-06-15",
                          pdc0=20000, inverter_ac0=3000)
        self.assertIn("peak AC 3000 W", out)

    def test_hot_air_yields_less(self):
        cool = solar_yield(action="yield", latitude=51.5, longitude=0.0,
                           start_date="2026-06-15", end_date="2026-06-15",
                           pdc0=5000, temp_air=0)
        hot = solar_yield(action="yield", latitude=51.5, longitude=0.0,
                          start_date="2026-06-15", end_date="2026-06-15",
                          pdc0=5000, temp_air=40)
        self.assertLess(float(hot.split("energy ")[1].split(" kWh")[0]),
                        float(cool.split("energy ")[1].split(" kWh")[0]))

    def test_detail_hours(self):
        out = solar_yield(action="yield", latitude=51.5, longitude=0.0,
                          start_date="2026-06-15", end_date="2026-06-15",
                          pdc0=5000, detail=1)
        self.assertIn("Hourly AC power", out)
        self.assertIn("12:30 4263", out)
        self.assertIn("00:30 0", out)

    def test_capacity_factor_in_range(self):
        out = solar_yield(action="yield", latitude=51.5, longitude=0.0,
                          start_date="2026-06-01", end_date="2026-06-30",
                          pdc0=5000)
        cf = float(out.split("capacity factor ")[1].split("%")[0])
        self.assertGreater(cf, 0.0)
        self.assertLess(cf, 100.0)


class TestToolSurface(unittest.TestCase):
    def test_sun_output(self):
        out = solar_yield(action="sun", latitude=51.5, longitude=0.0,
                          date="2026-06-21", time="12:00")
        self.assertIn("apparent zenith 28.06", out)
        self.assertIn("declination 23.44", out)
        self.assertNotIn("sunrise", out.lower())
        self.assertNotIn("sunset", out.lower())

    def test_irradiance_output(self):
        out = solar_yield(action="irradiance", latitude=51.5,
                          longitude=0.0, date="2026-06-21", time="12:00")
        self.assertIn("GHI 898.8 W/m", out)
        self.assertIn("plane-of-array 1022.6 W/m", out)

    def test_unknown_action_lists_actions(self):
        out = solar_yield(action="bogus")
        self.assertIn("'sun'", out)
        self.assertIn("'irradiance'", out)
        self.assertIn("'yield'", out)

    def test_bad_latitude_honest(self):
        out = solar_yield(action="sun", latitude=200)
        self.assertIn("out of range", out)

    def test_bad_date_honest(self):
        out = solar_yield(action="sun", latitude=51.5, longitude=0.0,
                          date="21 June")
        self.assertIn("not a date", out)

    def test_bad_time_honest(self):
        out = solar_yield(action="sun", latitude=51.5, longitude=0.0,
                          date="2026-06-21", time="25:00")
        self.assertIn("not a time", out)

    def test_reversed_range_honest(self):
        out = solar_yield(action="yield", latitude=51.5, longitude=0.0,
                          start_date="2026-06-30", end_date="2026-06-01")
        self.assertIn("before start_date", out)

    def test_range_too_long_honest(self):
        out = solar_yield(action="yield", latitude=51.5, longitude=0.0,
                          start_date="2026-01-01", end_date="2027-01-02")
        self.assertIn("at most 366", out)

    def test_registry_declares_19_properties(self):
        from core.funnel.tools_registry import tools
        decl = next(d for d in tools.get_declarations()
                    if d["name"] == "solar_yield")
        self.assertEqual(len(decl["parameters"]["properties"]), 19)

    def test_snapshot_read_entry(self):
        import json
        import pathlib
        snap = json.loads(pathlib.Path("tests/_classification_snapshot.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(snap["solar_yield"],
                         {"confirm": False, "free": None, "read": True})


if __name__ == "__main__":
    unittest.main()
