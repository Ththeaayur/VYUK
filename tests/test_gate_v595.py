"""v5.95 gate: Live model fallback chain contains no retired preview ids.

Gate decision (preview-model fallback cleanup, owed before 17 Nov 2026):
Google retires the 2.5 native-audio PREVIEW ids on 17 Nov 2026 with no
redirect, so a fallback must never land on one. The chain keeps the GA
head first and the non-preview native-audio id as the only fallback.
"""

import unittest

from core.voice.live_client import KNOWN_LIVE_MODELS  # Phase 3 V1b INTENTIONAL: moved to core/voice/

# Ids Google retires 17 Nov 2026 (no redirect). If Google ever revives
# one under the same name, this test must be updated deliberately, not
# by deleting the pin.
RETIRED_PREVIEW_IDS = (
    "gemini-2.5-flash-native-audio-preview-12-2025",
    "gemini-live-2.5-flash-preview",
)


class TestGateV595FallbackChain(unittest.TestCase):
    def test_ga_head_leads(self):
        self.assertEqual(KNOWN_LIVE_MODELS[0], "gemini-3.8-live")

    def test_no_retired_preview_ids(self):
        for retired in RETIRED_PREVIEW_IDS:
            self.assertNotIn(retired, KNOWN_LIVE_MODELS)

    def test_no_preview_id_anywhere(self):
        # Catches a future preview id smuggled in under a new name.
        for model_id in KNOWN_LIVE_MODELS:
            self.assertNotIn("preview", model_id)

    def test_chain_still_has_a_fallback(self):
        # Removing the previews must not leave a single-model chain:
        # the configured-model-first loop still needs a second chance.
        self.assertGreaterEqual(len(KNOWN_LIVE_MODELS), 2)
        self.assertIn("gemini-live-2.5-flash-native-audio",
                      KNOWN_LIVE_MODELS)


if __name__ == "__main__":
    unittest.main()
