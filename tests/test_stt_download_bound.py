"""Field-check fix #18 (M10c): the first-turn STT hub download is bounded.

The field check watched a first turn stall 6+ minutes inside the hub
download of the `small` weights — the model construction had no
timeout, no size cap and showed no progress, so the turn never
completed. The factory (core/voice/asr.py) now bounds it: a total
wait deadline on a construction that may download, and a size cap on
the landed weights. These tests drive the REAL factory with faked
constructions and temp hub caches (no network, no real weights):

- a hanging construction returns None inside the deadline, with a
  plain timeout note (the turn degrades to typed input);
- an over-cap snapshot is refused with a plain note and the bad file
  is removed, before any construction runs;
- a healthy under-cap snapshot still loads and is cached;
- a local snapshot path constructs directly, ignoring the deadline
  (it never touches the network);
- the default cap covers an unknown model name.
"""
import io
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from core.voice import asr

_SENTINEL = object()  # stand-in for a loaded WhisperModel
OVER_CAP_BYTES = 1_200_000_000   # over small's 1 GB cap
UNDER_CAP_BYTES = 50_000_000     # clears the 10 MB sanity floor, under cap


def _seed_snapshot(hub_dir, model_name, size_bytes):
    """A hub-cache snapshot for `model_name` whose model.bin is a
    sparse file of `size_bytes` (logical size, ~0 bytes on disk)."""
    snap = (Path(hub_dir) / f"models--Systran--faster-whisper-{model_name}"
            / "snapshots" / "testrev")
    snap.mkdir(parents=True, exist_ok=True)
    model_bin = snap / "model.bin"
    with open(model_bin, "wb") as fh:
        fh.truncate(size_bytes)
    return model_bin


class SttDownloadBoundTests(unittest.TestCase):
    def setUp(self):
        self._saved_models = dict(asr._SHARED_MODELS)
        asr._SHARED_MODELS.clear()
        self._avail = mock.patch.object(
            asr, "faster_whisper_available", return_value=True)
        self._avail.start()

    def tearDown(self):
        self._avail.stop()
        asr._SHARED_MODELS.clear()
        asr._SHARED_MODELS.update(self._saved_models)

    def test_hanging_construction_times_out(self):
        """A construction that never returns unblocks the caller
        inside the deadline with None — the turn degrades instead
        of hanging."""
        def _never_returns(model_arg):
            time.sleep(30)

        with mock.patch.object(asr, "_construct_whisper_model",
                               side_effect=_never_returns), \
                mock.patch.object(asr, "WHISPER_LOAD_TIMEOUT_S", 0.5):
            buf = io.StringIO()
            started = time.monotonic()
            with redirect_stdout(buf):
                model = asr.get_whisper_model("small")
            elapsed = time.monotonic() - started
        self.assertIsNone(model)
        self.assertLess(elapsed, 5.0,
                        f"factory took {elapsed:.1f}s to give up")
        self.assertNotIn("small", asr._SHARED_MODELS)

    def test_timeout_is_surfaced_plainly(self):
        """The timeout is printed as a plain sentence naming the
        model and the deadline — not a silent None."""
        def _never_returns(model_arg):
            time.sleep(30)

        with mock.patch.object(asr, "_construct_whisper_model",
                               side_effect=_never_returns), \
                mock.patch.object(asr, "WHISPER_LOAD_TIMEOUT_S", 0.5):
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertIsNone(asr.get_whisper_model("small"))
            out = buf.getvalue()
        self.assertIn("timed out after 0.5s", out)
        self.assertIn("small", out)
        self.assertIn("typing works meanwhile", out)

    def test_oversize_snapshot_refused_and_removed(self):
        """A snapshot over its cap is refused before any
        construction runs, and the bad file is deleted so the next
        call re-fetches."""
        with tempfile.TemporaryDirectory() as tmp:
            model_bin = _seed_snapshot(tmp, "small", OVER_CAP_BYTES)
            construct = mock.Mock(return_value=_SENTINEL)
            with mock.patch.dict(os.environ,
                                 {"HUGGINGFACE_HUB_CACHE": tmp}), \
                    mock.patch.object(asr, "_construct_whisper_model",
                                      construct):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    model = asr.get_whisper_model("small")
                out = buf.getvalue()
            self.assertIsNone(model)
            construct.assert_not_called()
            self.assertFalse(model_bin.exists(),
                             "over-cap model.bin was not removed")

    def test_oversize_message_names_the_cap(self):
        """The refusal names the model, the landed size and the cap
        — the user can tell what happened and why."""
        with tempfile.TemporaryDirectory() as tmp:
            _seed_snapshot(tmp, "small", OVER_CAP_BYTES)
            with mock.patch.dict(os.environ,
                                 {"HUGGINGFACE_HUB_CACHE": tmp}):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    self.assertIsNone(asr.get_whisper_model("small"))
                out = buf.getvalue()
        self.assertIn("small", out)
        self.assertIn("1.2 GB", out)
        self.assertIn("1.0 GB", out)
        self.assertIn("cap", out)

    def test_healthy_small_load_still_succeeds(self):
        """An under-cap snapshot still loads through the bound
        factory and is cached — the bound changes nothing about a
        healthy fetch."""
        with tempfile.TemporaryDirectory() as tmp:
            _seed_snapshot(tmp, "small", UNDER_CAP_BYTES)
            construct = mock.Mock(return_value=_SENTINEL)
            with mock.patch.dict(os.environ,
                                 {"HUGGINGFACE_HUB_CACHE": tmp}), \
                    mock.patch.object(asr, "_construct_whisper_model",
                                      construct):
                model = asr.get_whisper_model("small")
                again = asr.get_whisper_model("small")
            self.assertIs(model, _SENTINEL)
            self.assertIs(again, _SENTINEL)
            self.assertEqual(construct.call_count, 1,
                             "healthy load must construct once, then cache")

    def test_local_path_construction_ignores_timeout(self):
        """A local snapshot path loads from disk — no network, no
        wait to bound — so it constructs directly even with a tiny
        deadline."""
        construct = mock.Mock(return_value=_SENTINEL)
        with mock.patch.object(asr, "_construct_whisper_model",
                               construct), \
                mock.patch.object(asr, "WHISPER_LOAD_TIMEOUT_S", 0.001):
            model = asr.get_whisper_model("/tmp/fake-whisper-snapshot")
        self.assertIs(model, _SENTINEL)
        construct.assert_called_once_with("/tmp/fake-whisper-snapshot")

    def test_default_cap_covers_unknown_model(self):
        """A model name with no per-model cap gets the 4 GB default
        — a 1.2 GB snapshot for it is under the cap and loads."""
        with tempfile.TemporaryDirectory() as tmp:
            _seed_snapshot(tmp, "huge-model", OVER_CAP_BYTES)
            construct = mock.Mock(return_value=_SENTINEL)
            with mock.patch.dict(os.environ,
                                 {"HUGGINGFACE_HUB_CACHE": tmp}), \
                    mock.patch.object(asr, "_construct_whisper_model",
                                      construct):
                model = asr.get_whisper_model("huge-model")
            self.assertIs(model, _SENTINEL)
            construct.assert_called_once()


if __name__ == "__main__":
    unittest.main()
