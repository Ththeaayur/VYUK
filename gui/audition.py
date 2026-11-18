"""Kenney audition player — B-R1-9 (audition material ONLY).

Plays the six Kenney CC0 candidates in gui/assets/audition/kenney/
on EXPLICIT demand only, so the owner can A/B them against the
shipped earcons when he rules on the earcon veto.

THE VETO BOUNDARY — read before touching this module:
* The shipped earcon identity is the synthesised v3 family
  (gui/sounds.py, 15 cues, both families complete). There is no
  missing cue for these files to fill.
* The owner's v5.51 earcon veto is STILL PENDING
  (core/sound_pools_v564.py, EARCON_VETO_PENDING = True).
  attempt_layer() refuses fail-closed for EVERY asset, including
  these CC0-cleared ones. Nothing in this module layers audio:
  play() is an audition playback, not product audio. It is never
  called by Earcons, never called by any live UI path, and the
  only call site is the Settings > Sounds audition row, where
  the user presses a button for one named candidate.
* A cleared licence answers "may we use this at all"; the veto
  answers "may it sound like VYUK". This module never answers
  the second question.

Loudness: the shipped earcons measure -20.00 dBFS RMS by design.
These six do not (forceField ~-11.5, twoTone ~-14.5/-15.0,
clicks -22.5/-26.4 — see BUNDLE_RECORD.md), so play() applies a
per-file playback-only gain matching what the owner hears to the
shipped -20 dBFS standard, and records the gain applied. The
files themselves are never altered.

Licence gate: the player loads licence_log.json next to the WAVs
and re-validates every record with the tree's own
sound_pools_v564.validate_record at construction. A file whose
record does not validate 'cleared' is never played. A missing
or unreadable log refuses everything (fail-closed).

Missing file, missing backend, or sounds off are honest
failures: play() returns a record with played=False and a
reason — never an exception, never a substitute sound.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

try:
    from PySide6.QtCore import QUrl
    from PySide6.QtMultimedia import QSoundEffect

    _AVAILABLE = True
except Exception:  # noqa: BLE001
    _AVAILABLE = False

_ASSETS = Path(__file__).resolve().parent / "assets" / "audition" / "kenney"

#: The shipped-earcon loudness standard (w9_wiring_canon measures
#: every shipped cue at -20.00 dBFS RMS by design). Audition
#: playback is gain-matched to this so the A/B is fair.
TARGET_RMS_DBFS = -20.0

#: name -> (filename, moment, pack, measured RMS dBFS).
#: Two candidates per moment, so the owner has a real A/B choice.
AUDITION_SOUNDS = {
    "forceField_000": ("kenney_forceField_000.wav", "boot", "Kenney Sci-Fi Sounds", -11.53),
    "forceField_001": ("kenney_forceField_001.wav", "boot", "Kenney Sci-Fi Sounds", -11.59),
    "twoTone1": ("kenney_twoTone1.wav", "notification", "Kenney Digital Audio", -14.49),
    "twoTone2": ("kenney_twoTone2.wav", "notification", "Kenney Digital Audio", -15.03),
    "click_001": ("kenney_click_001.wav", "orb_tap", "Kenney Interface Sounds", -26.40),
    "click1": ("kenney_click1.wav", "orb_tap", "Kenney UI Audio", -22.46),
}


def match_gain_dbfs(rms_dbfs: float) -> float:
    """Playback-only gain (dB) matching a file to the shipped
    -20 dBFS standard. Positive when the file is quieter than
    the standard, negative when louder. Applied at playback
    only; the file is never altered."""
    return TARGET_RMS_DBFS - rms_dbfs


def match_gain_factor(rms_dbfs: float) -> float:
    """The same gain as a linear multiplier for QSoundEffect
    volume (0.0-1.0 scale)."""
    return 10.0 ** (match_gain_dbfs(rms_dbfs) / 20.0)


class KenneyAudition:
    """Explicit-demand audition player for the Kenney candidates.

    ``base_dir`` is injectable for tests (a temp dir holding a
    subset of the bundle + its licence_log.json). ``volume`` and
    ``enabled`` mirror the Earcons settings the Settings page
    already owns; the player never reads QSettings itself.
    """

    def __init__(self, parent=None, volume: float = 0.5,
                 enabled: bool = True, base_dir: Path | None = None):
        self.volume = volume
        self.enabled = enabled
        self._parent = parent
        self._base = Path(base_dir) if base_dir is not None else _ASSETS
        self._effects = {}
        self._cleared: dict[str, bool] = {}
        self._load_licence_gate()
        self._load_effects()

    # -- licence gate (fail-closed) ---------------------------------
    def _load_licence_gate(self):
        self._cleared = {}
        try:
            from core.gui_facade import sound_pools_v564 as _pools
        except Exception:  # noqa: BLE001 - no gate without the module
            try:
                from voice_assistant.core.gui_facade import sound_pools_v564 as _pools
            except Exception:  # noqa: BLE001
                return
        try:
            log = json.loads((self._base / "licence_log.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - missing/unreadable log: refuse all
            return
        # NOTE: the log's own "earcon_veto_pending" flag is not
        # trusted here and never changes behaviour — the veto lives
        # in sound_pools_v564.EARCON_VETO_PENDING, and this player
        # never layers regardless of what any log claims.
        for entry in log.get("entries", []):
            record = entry.get("record", {})
            asset = record.get("asset", "")
            if not asset:
                continue
            try:
                verdict, _reason = _pools.validate_record(
                    _pools.AssetLicenceRecord(
                        quarry=record.get("quarry", ""),
                        asset=asset,
                        licence=record.get("licence", ""),
                        source_url=record.get("source_url", ""),
                        verified_from_primary=bool(record.get("verified_from_primary", False)),
                        owner_decision=bool(record.get("owner_decision", False)),
                        pack_licence_file=record.get("pack_licence_file", ""),
                        loudness_matched=bool(record.get("loudness_matched", False))))
            except Exception:  # noqa: BLE001 - unvalidatable: not cleared
                verdict = "rejected"
            self._cleared[asset] = (verdict == "cleared")

    def is_cleared(self, name: str) -> bool:
        """True only when the licence log validates this
        candidate 'cleared'. Unknown names are never cleared."""
        entry = AUDITION_SOUNDS.get(name)
        if entry is None:
            return False
        return bool(self._cleared.get(entry[0], False))

    # -- effects ------------------------------------------------------
    def _load_effects(self):
        for effect in self._effects.values():
            try:
                effect.deleteLater()
            except Exception:  # noqa: BLE001
                pass
        self._effects = {}
        if not _AVAILABLE:
            return
        for name, (filename, _moment, _pack, rms) in AUDITION_SOUNDS.items():
            path = self._base / filename
            if not path.exists():
                continue
            try:
                effect = QSoundEffect(self._parent)
                effect.setSource(QUrl.fromLocalFile(str(path)))
                effect.setVolume(self._play_volume(rms))
                self._effects[name] = effect
            except Exception:  # noqa: BLE001
                continue

    def _play_volume(self, rms_dbfs: float) -> float:
        """Base volume x loudness-match gain, clamped to the
        QSoundEffect 0.0-1.0 range. A file louder than the
        standard gets turned DOWN; a quieter one gets turned
        UP only as far as the 1.0 ceiling allows."""
        return max(0.0, min(1.0, self.volume * match_gain_factor(rms_dbfs)))

    def set_volume(self, volume: float):
        self.volume = volume
        for name, effect in self._effects.items():
            try:
                effect.setVolume(self._play_volume(AUDITION_SOUNDS[name][3]))
            except Exception:  # noqa: BLE001
                pass

    def set_enabled(self, enabled: bool):
        self.enabled = enabled

    # -- the only playback entry point --------------------------------
    def play(self, name: str) -> dict:
        """Play one named candidate on explicit demand. Returns a
        record: {played, name, moment, gain_db, reason}. Every
        refusal is honest and named; nothing is substituted."""
        entry = AUDITION_SOUNDS.get(name)
        if entry is None:
            return {"played": False, "name": name, "moment": None,
                    "gain_db": None, "reason": f"unknown candidate: {name}"}
        filename, moment, _pack, rms = entry
        if not self.enabled:
            return {"played": False, "name": name, "moment": moment,
                    "gain_db": None, "reason": "interface sounds are off"}
        if not self.is_cleared(name):
            return {"played": False, "name": name, "moment": moment,
                    "gain_db": None,
                    "reason": "licence log does not clear this candidate"}
        path = self._base / filename
        if not path.exists():
            return {"played": False, "name": name, "moment": moment,
                    "gain_db": None,
                    "reason": f"audition file missing: {filename}"}
        if not _AVAILABLE:
            return {"played": False, "name": name, "moment": moment,
                    "gain_db": match_gain_dbfs(rms),
                    "reason": "no audio backend (QtMultimedia unavailable)"}
        effect = self._effects.get(name)
        if effect is None:
            return {"played": False, "name": name, "moment": moment,
                    "gain_db": match_gain_dbfs(rms),
                    "reason": f"audition effect failed to load: {filename}"}
        try:
            effect.play()
        except Exception as exc:  # noqa: BLE001
            return {"played": False, "name": name, "moment": moment,
                    "gain_db": match_gain_dbfs(rms),
                    "reason": f"playback failed: {exc}"}
        return {"played": True, "name": name, "moment": moment,
                "gain_db": round(match_gain_dbfs(rms), 2),
                "reason": "played (audition only — not product audio)"}

    def candidates(self) -> list[dict]:
        """The audition table, for the Settings row and tests."""
        return [{"name": name, "file": entry[0], "moment": entry[1],
                 "pack": entry[2], "rms_dbfs": entry[3],
                 "gain_db": round(match_gain_dbfs(entry[3]), 2),
                 "cleared": self.is_cleared(name)}
                for name, entry in AUDITION_SOUNDS.items()]
