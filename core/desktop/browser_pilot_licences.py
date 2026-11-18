"""Wave 4 (v5.59) browser-pilot licence verdicts — DATA, not code that
loads a browser engine.

Follow the core/pilot_licences_sound_translation.py precedent: every verdict below
is a record of a check against its PRIMARY source, made 2026-10-25
from a locally downloaded distribution artefact (``pip download``
+ LICENSE/METADATA read from the wheel itself). PyPI HTML pages
return challenge pages and aggregator claims do not count, so
neither is used anywhere here.

Nothing here downloads, launches, or ships a browser engine, and
nothing here overrides the Wave 2 CC-1 ship-gate audit. A verdict of
"STILL-OWED" or "AUDIT-ONLY" means the item does NOT ship in that
form until the coordinator re-verifies it from the primary source
named in the verdict's closing step.

Two standing walls ride with every verdict, licence or not:
stealth is not signed-in, and nothing here is CAPTCHA-solving.
The password and CAPTCHA walls live in core/browser.py's worker
and are untouched by this workstream; a licence clearance is
never a terms-of-service clearance for any site.

GAPS-1 (2026-11-12): the BR-1 verdict is REMOVED — the engine it
cleared is gone (seam + A/B harness deleted, the dependency
removed from requirements.txt / lock / THIRD_PARTY_LICENSES.md),
so its verdict is deleted with it. The removal record lives in
core/desktop/browser_automation.py and requirements.txt; the
remaining verdicts below are untouched.

HE-2 browser-use — PILOT-CLEAR, MIT (pilot only, never shipped)
---------------------------------------------------------------
Wheel browser-use 0.13.11 (read 2026-10-25): dist-info carries a
full MIT License text (Copyright (c) 2024 Gregor Zunic). That
clears the library for a PILOT trial. It does not clear it for
shipment: the wheel's dependency tree includes a telemetry
sender (posthog) among many other packages, so a live trial must
confirm the library's documented telemetry opt-out from its own
docs BEFORE import, and must configure no exporter. The trial
harness (core/desktop/browser_agent_pilots.py) imports nothing from
browser-use; availability is a lazy ``find_spec`` probe only.

HE-3 Stagehand — PILOT-MIT per METADATA expression (pilot only)
---------------------------------------------------------------
Wheel stagehand 4.3.0 (read 2026-10-25): METADATA declares
``License-Expression: MIT`` (author Browserbase), but the wheel
contains NO LICENSE text file. That is a primary-artefact
declaration, enough for a PILOT trial on the same terms as
browser-use (lazy probe only, no shipped code, telemetry
exporters never configured — note its opentelemetry-api
dependency), but CC-1 must re-confirm the full MIT text from
the project repository's LICENSE file before ANY shipped use.
Until then Stagehand is pilot-only, exactly like browser-use.

BR-6 Camoufox — CLEAR (MIT) as a package; lane-only, geoip NOT bundled
----------------------------------------------------------------------
Wheel camoufox 0.5.8 (read 2026-10-25): METADATA ``License: MIT``
+ ``License-File: LICENSE`` and a full MIT License text
(Copyright (c) 2024-2026 daijro and the Camoufox contributors).
The package itself is licence-clear for a pilot lane.

The [geoip] extra is AUDITED, NOT BUNDLED, and NOT ENABLED:
* The extra adds only the ``maxminddb`` reader; no database
  ships in the wheel.
* Databases are fetched at trial/runtime from third-party
  GitHub releases listed in the wheel's ``repos.yml``, never
  pinned: the default "GeoIP AIO by daijro" merges several
  databases (daijro/geoip-all-in-one, rebuilt weekly), and the
  deprecated "MaxMind GeoLite2" source mirrors MaxMind data via
  sapics/ip-location-db. The constituent database licences
  (including MaxMind's GeoLite2 terms) were NOT verified in
  this check, so no database is bundled, downloaded, or used
  by the lane. The lane forces geoip off (see
  core/desktop/browser_camoufox_lane.py).
* The patched Firefox binary is likewise fetched by
  camoufox's package manager at trial time from GitHub
  releases — it is not in the wheel and never ships in the
  tree. The lane is pilot-only for exactly that reason.

PC-B11 official browser MCP servers — DEV-ONLY, no shipped code
---------------------------------------------------------------
npm registry metadata for ``chrome-devtools-mcp`` 1.12.0 (read
2026-10-25): licence Apache-2.0, homepage
github.com/ChromeDevTools/chrome-devtools-mcp. The servers are
DEV-ONLY diagnostics: no server code, dependency, or wiring
ships in the tree. The dev harness note/script lives OUTSIDE
the tree (workspace/code-lab/dev_browser_mcp_v559.*) and pins
``--no-usage-statistics`` because Chrome DevTools MCP collects
Google usage statistics by default. Any dev run without that
pin is a protocol violation, not a variant.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LicenceVerdict:
    """One licence verdict, with its primary source."""

    item: str
    verdict: str  # "CLEAR", "PILOT-CLEAR", "PILOT-MIT", "AUDIT-ONLY", "DEV-ONLY"
    licence: str
    ships_in_v559: bool
    primary_source: str
    note: str
    closing_step: str


V559_PILOT_LICENCE_VERDICTS: tuple[LicenceVerdict, ...] = (
    LicenceVerdict(
        item="HE-2 browser-use (browser agent)",
        verdict="PILOT-CLEAR",
        licence="MIT (pilot trial only)",
        ships_in_v559=False,
        primary_source=(
            "pip-downloaded wheel browser-use-0.13.11 (read 2026-10-25): "
            "dist-info/licenses/LICENSE is the full MIT License text "
            "(Copyright (c) 2024 Gregor Zunic)."),
        note=(
            "Pilot trial only: lazy find_spec probe, never imported by "
            "shipped default paths, never in requirements.txt. The "
            "wheel's dependency tree includes a telemetry sender, so "
            "a live trial must confirm the library's documented "
            "telemetry opt-out from its own docs BEFORE import and "
            "configure no exporter. No winner is declared from stub "
            "data (see core/desktop/browser_agent_pilots.py)."),
        closing_step=(
            "Live trial on a Windows box with a real browser; "
            "telemetry opt-out confirmed from the library's own docs "
            "first. CC-1 re-verifies before any shipped use."),
    ),
    LicenceVerdict(
        item="HE-3 Stagehand (browser agent)",
        verdict="PILOT-MIT",
        licence="MIT per METADATA expression (pilot trial only)",
        ships_in_v559=False,
        primary_source=(
            "pip-downloaded wheel stagehand-4.3.0 (read 2026-10-25): "
            "dist-info/METADATA License-Expression MIT (author "
            "Browserbase). The wheel contains NO LICENSE text file."),
        note=(
            "Pilot trial only, on the same terms as browser-use: lazy "
            "probe, no shipped code, no telemetry exporter configured "
            "(note its opentelemetry-api dependency — the Wave 2 rule "
            "applies: local-file exporter only, and here: none). The "
            "missing LICENSE text file is why this is PILOT-MIT, not "
            "full CLEAR."),
        closing_step=(
            "Live trial on a Windows box with a real browser. Before "
            "ANY shipped use, CC-1 reads the full MIT text from the "
            "project repository's LICENSE file and re-records the "
            "verdict here."),
    ),
    LicenceVerdict(
        item="BR-6 Camoufox (scraping/challenge lane only)",
        verdict="CLEAR",
        licence="MIT (package); geoip databases: AUDIT-ONLY, not bundled",
        ships_in_v559=False,
        primary_source=(
            "pip-downloaded wheel camoufox-0.5.8 (read 2026-10-25): "
            "dist-info/METADATA License MIT + License-File LICENSE; "
            "dist-info/licenses/LICENSE is the full MIT License text "
            "(Copyright (c) 2024-2026 daijro and the Camoufox "
            "contributors). GeoIP audit from the same wheel: "
            "repos.yml lists unpinned GitHub-release databases "
            "(default 'GeoIP AIO by daijro', merging several "
            "databases; deprecated 'MaxMind GeoLite2' via "
            "sapics/ip-location-db); the [geoip] extra itself adds "
            "only the maxminddb reader."),
        note=(
            "The package is licence-clear, but the LANE is pilot-only "
            "and isolated: its own Firefox profile, never her Chrome "
            "profile or logins, never the default path, geoip forced "
            "off. The patched Firefox binary is fetched by the "
            "package manager at trial time, never shipped in the "
            "tree. No GeoIP database is bundled, downloaded, or used "
            "until its own licence is verified."),
        closing_step=(
            "Before any geoip use, verify each constituent database's "
            "licence (GeoIP AIO sources; MaxMind GeoLite2 terms) from "
            "its primary source and record it here. Until then the "
            "lane runs with geoip off, permanently."),
    ),
    LicenceVerdict(
        item="PC-B11 official browser MCP servers (dev diagnostics only)",
        verdict="DEV-ONLY",
        licence="Apache-2.0 (npm registry metadata)",
        ships_in_v559=False,
        primary_source=(
            "npm registry metadata for chrome-devtools-mcp 1.12.0 "
            "(read 2026-10-25): license Apache-2.0, homepage "
            "github.com/ChromeDevTools/chrome-devtools-mcp."),
        note=(
            "DEV-ONLY: no server code, dependency, or wiring ships in "
            "the tree. The dev harness lives outside the tree in "
            "workspace/code-lab/dev_browser_mcp_v559.* and pins "
            "--no-usage-statistics, because Chrome DevTools MCP "
            "collects Google usage statistics by default."),
        closing_step=(
            "None in-tree. A dev run without the "
            "--no-usage-statistics pin is a protocol violation."),
    ),
)

# Keys for verdict_for(): substring match, case-insensitive.
_KEY_ALIASES = {
    "browser-use": "HE-2 browser-use",
    "browser_use": "HE-2 browser-use",
    "he-2": "HE-2 browser-use",
    "stagehand": "HE-3 Stagehand",
    "he-3": "HE-3 Stagehand",
    "camoufox": "BR-6 Camoufox",
    "br-6": "BR-6 Camoufox",
    "geoip": "BR-6 Camoufox",
    "mcp": "PC-B11 official browser MCP",
    "pc-b11": "PC-B11 official browser MCP",
    "chrome devtools mcp": "PC-B11 official browser MCP",
}


def verdict_for(key: str) -> LicenceVerdict | None:
    """Return the verdict whose item matches ``key`` (substring, case-insensitive)."""
    lowered = (key or "").strip().lower()
    if not lowered:
        return None
    for alias, item_start in _KEY_ALIASES.items():
        if alias in lowered:
            for verdict in V559_PILOT_LICENCE_VERDICTS:
                if verdict.item.startswith(item_start):
                    return verdict
    for verdict in V559_PILOT_LICENCE_VERDICTS:
        if lowered in verdict.item.lower():
            return verdict
    return None


def all_verdicts() -> tuple[LicenceVerdict, ...]:
    """All Wave 4 browser-pilot verdicts, in plan order."""
    return V559_PILOT_LICENCE_VERDICTS
