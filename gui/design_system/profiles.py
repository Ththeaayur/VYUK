"""HUD intensity profiles (guide §4 D6, §5.6).

Three profiles, functionally identical — only the presentation changes.
Cinematic is the default on capable machines; Professional is the
default under power-saver; Minimal is the default for the Contrast
theme. The setting key is HUD_PROFILE_KEY; reading and writing the
stored value is the settings page's job (Phase E) — this module only
owns the model and the resolution rule, so it stays Qt-free.
"""

from dataclasses import dataclass

HUD_PROFILE_KEY = "hud_profile"


@dataclass(frozen=True)
class HudProfile:
    """One intensity profile: what the user sees, not what works."""

    name: str
    intensity: str  # "full" | "light" | "off"
    backdrop: bool
    scanlines: bool
    radar_sweep: bool
    transitions: bool
    telemetry: bool


PROFILES = {
    "Cinematic": HudProfile(
        name="Cinematic",
        intensity="full",
        backdrop=True,
        scanlines=True,
        radar_sweep=True,
        transitions=True,
        telemetry=True,
    ),
    "Professional": HudProfile(
        name="Professional",
        intensity="light",
        backdrop=True,
        scanlines=False,
        radar_sweep=False,
        transitions=True,
        telemetry=True,
    ),
    "Minimal": HudProfile(
        name="Minimal",
        intensity="off",
        backdrop=False,
        scanlines=False,
        radar_sweep=False,
        transitions=False,
        telemetry=False,
    ),
}

DEFAULT_PROFILE = "Cinematic"


def resolve_profile(stored=None, power_saver=False, contrast_theme=False):
    """Resolve the effective profile name from stored value + conditions.

    Contrast theme forces Minimal; power-saver forces Professional;
    otherwise the stored profile wins, falling back to Cinematic for
    unknown or absent values. The stored value itself is never
    rewritten here — migration of stored values is the settings
    page's job.
    """
    if contrast_theme:
        return "Minimal"
    if power_saver:
        return "Professional"
    if stored in PROFILES:
        return stored
    return DEFAULT_PROFILE
