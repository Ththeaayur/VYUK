import threading
from collections.abc import Callable
from pathlib import Path

# INTENTIONAL (batch 3): cross-module imports (progress/dl-state + fetch).
from .local_assets_progress import (
    _clear_download_failure,
    _progress_set_run_state,
    _record_download_failure,
)
from .local_assets_kokoro import (
    KOKORO_MODEL_URL,
    KOKORO_VOICES_URL,
    MIN_KOKORO_MODEL_BYTES,
    MIN_KOKORO_VOICES_BYTES,
    download_kokoro_paths,
    kokoro_paths,
    kokoro_ready,
)
from .local_assets_model import (
    _size,
    download_model_path,
    effective_model_file,
    model_path,
    model_ready,
    model_tier_for,
)
from .local_assets_proxy import _human_size, download_file
from .local_assets_vlm import _fetch_vlm_model
from .local_assets_whisper import (
    _fetch_piper_voice,
    _fetch_wake_model,
    _fetch_whisper_weights,
    effective_whisper_model_name,
    whisper_ready,
)


# --- Orchestrator --------------------------------------------------------
# Ensure serialisation (v5.22, brain audit round 2 #5): two paths can
# decide the pack needs downloading at the same moment — the
# DownloadManager (Lite's _kick_downloads, the Settings button) and a
# full session's direct call from core/local_client._prepare_builtin.
# Before v5.22 both could run the stages concurrently over the same
# .part files: two writers on one resume file is how a pack corrupts.
# The public ensure_local_assets() below now holds _ENSURE_LOCK for
# its whole run. A caller that had to WAIT re-checks readiness once
# it holds the lock: when the active run already landed everything,
# it answers its own callback with the standard ready lines and
# returns them instead of running the stages a second time.
# Deadlock audit: nothing inside the serialized run calls
# ensure_local_assets again — the stages are _fetch_whisper_weights /
# download_file / _fetch_wake_model, and the only in-module caller is
# DownloadManager._run, which calls the PUBLIC name on its own thread
# (that is the contention this lock exists to serialise).
_ENSURE_LOCK = threading.Lock()


def ensure_local_assets(progress_cb: Callable[[str], None],
                        cancel_event: threading.Event | None = None) -> list[str]:
    """Serialised entry point — the contract of
    _ensure_local_assets_serialized() (downloads whatever the built-in
    brain still needs; returns the progress lines it emitted; never
    raises), plus the v5.22 guarantee that runs never overlap: a
    concurrent caller waits for the active run, then — if the pack
    has landed in the meantime — returns the ready lines without
    downloading anything itself."""
    waited = not _ENSURE_LOCK.acquire(blocking=False)
    if waited:
        _ENSURE_LOCK.acquire()
    try:
        if waited and whisper_ready() and kokoro_ready() and model_ready():
            lines: list[str] = []

            def emit_ready(line: str):
                lines.append(line)
                try:
                    progress_cb(line)
                except Exception:  # noqa: BLE001 — same guard as the run's emit
                    pass

            emit_ready(f"Ears (Whisper {effective_whisper_model_name()}) ready.")
            emit_ready("Voice (Kokoro) ready.")
            tier_found = model_tier_for(effective_model_file())
            if tier_found:
                _name, tier = tier_found
                emit_ready(
                    f"Brain model ready ({tier['label']}, {tier['gb']:g} GB).")
            else:
                emit_ready(
                    f"Local brain model ready "
                    f"({_human_size(_size(model_path()))}).")
            return lines
        return _ensure_local_assets_serialized(progress_cb, cancel_event)
    finally:
        _ENSURE_LOCK.release()


def _ensure_local_assets_serialized(
        progress_cb: Callable[[str], None],
        cancel_event: threading.Event | None = None) -> list[str]:
    """Downloads whatever the built-in brain still needs; returns the
    progress lines it emitted (the same lines go to progress_cb live).

    Idempotent and cheap when everything is present: two stat calls and
    three "ready" lines. Never raises — failures come back as lines
    starting with "ERROR:" so the session can surface them as a status.

    Cancellation (v2.0): when `cancel_event` is set mid-download the
    current download stops, the line "Download paused — it will resume
    where it left off." is emitted, and NO further downloads are
    attempted — the partial .part files stay on disk as resume state.
    """
    lines: list[str] = []
    paused_line = "Download paused — it will resume where it left off."

    def emit(line: str):
        lines.append(line)
        try:
            progress_cb(line)
        except Exception:  # noqa: BLE001 — a broken callback must not kill setup
            pass

    def run_download(url: str, dest: Path, label: str,
                     asset: str | None = None) -> str:
        """Returns "ok", "error" (an ERROR: line was emitted), or
        "cancelled" (the caller must stop and return). `asset` names
        the download in the structured progress tracker (v3.2)."""
        err = download_file(url, dest, emit, label, cancel_event,
                            progress_asset=asset)
        if err == "CANCELLED":
            return "cancelled"
        if err is not None:
            emit(f"ERROR: {err} (source: {url})")
            return "error"
        return "ok"

    def cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    def paused(lines_so_far: list[str]) -> list[str]:
        _progress_set_run_state("paused")
        emit(paused_line)
        return lines_so_far

    _progress_set_run_state("downloading")
    try:
        if cancelled():
            return paused(lines)

        # Download order (v2.7 — "progressively alive"): ears first,
        # then voice, then the brain, so a partially-downloaded pack
        # is maximally useful at every step (the lite session in
        # core/voice/lite_client.py serves a live session off whatever has
        # landed). The steps themselves are unchanged from before —
        # same URLs, same .part resume semantics, same lines.

        # 1) The ears: faster-whisper's weights (self-managed by the
        #    library on first WhisperModel construction — see
        #    _fetch_whisper_weights; a failure here never blocks the
        #    rest of the pack and never emits an ERROR line).
        _fetch_whisper_weights(emit)
        if cancelled():
            return paused(lines)

        # 2) The voice (model + voice bank are a pair — fetch whichever is short).
        # Readiness reads the RESOLVED paths (a bundled file counts);
        # downloads always land in data/ (download_kokoro_paths) —
        # the install tree is never written.
        model_file, voices_file = kokoro_paths()
        dl_model_file, dl_voices_file = download_kokoro_paths()
        if kokoro_ready():
            emit("Voice (Kokoro) ready.")
        else:
            ok = True
            if _size(model_file) <= MIN_KOKORO_MODEL_BYTES:
                outcome = run_download(KOKORO_MODEL_URL, dl_model_file,
                                       "voice engine (Kokoro)",
                                       asset="kokoro_model")
                if outcome == "cancelled":
                    return paused(lines)
                ok = outcome == "ok" and ok
            if _size(voices_file) <= MIN_KOKORO_VOICES_BYTES:
                outcome = run_download(KOKORO_VOICES_URL, dl_voices_file,
                                       "voice bank (Kokoro)",
                                       asset="kokoro_voices")
                if outcome == "cancelled":
                    return paused(lines)
                ok = outcome == "ok" and ok
            if ok and kokoro_ready():
                emit("Voice (Kokoro) ready.")
        if cancelled():
            return paused(lines)

        # 2b) The wake-word model — rides the voice stage (same
        #     downloader, same staging); see _fetch_wake_model for
        #     why its failures surface as notes, never ERROR lines.
        _fetch_wake_model(emit, cancel_event)
        if cancelled():
            return paused(lines)

        # 2c) The Piper fallback voice — same voice stage,
        #     same note-not-error posture (see _fetch_piper_voice).
        _fetch_piper_voice(emit, cancel_event)
        if cancelled():
            return paused(lines)

        # 3) The brain itself. The tier (and its download URL) is
        #    resolved from the EFFECTIVE model (v3.2 — the user's pick,
        #    a tier already on disk, or the RAM-sized tier); a custom
        #    filename is never downloaded — it is expected on disk,
        #    placed by the user, and a missing one is guidance, not a
        #    crash.
        eff_file = effective_model_file()
        tier_found = model_tier_for(eff_file)
        if model_ready():
            if tier_found:
                _name, tier = tier_found
                emit(f"Brain model ready ({tier['label']}, {tier['gb']:g} GB).")
            else:
                emit(f"Local brain model ready ({_human_size(_size(model_path()))}).")
        elif tier_found:
            _name, tier = tier_found
            outcome = run_download(tier["url"], download_model_path(),
                                   f"brain model ({tier['label']})",
                                   asset="brain")
            if outcome == "cancelled":
                return paused(lines)
            if outcome == "ok":
                emit(f"Brain model ready ({tier['label']}, {tier['gb']:g} GB).")
        else:
            emit(
                f"ERROR: Custom brain model expected at "
                f"data/models/{eff_file} — place the GGUF "
                "there (auto-download covers the Lite, Balanced and "
                "Quality tier models)."
            )

        # 3b) The local vision-language model (v5.45, E5b): a
        #     Quality-tier extra, fetched only through its tier
        #     gate (see _fetch_vlm_model / the VLM section above);
        #     silent on every other tier, and its failures never
        #     fail the pack — OCR covers screens without it.
        _fetch_vlm_model(emit, cancel_event)
        if cancelled():
            return paused(lines)
    except Exception as e:  # noqa: BLE001 — setup must report, never raise
        emit(f"ERROR: local setup failed: {e}")

    # The run's end state for pack_progress(): complete only when
    # every piece is actually on disk; an ERROR line leaves the run
    # FAILED (v3.3 — a state of its own, distinct from paused) and
    # the failure is persisted with its timestamp, so the doctor can
    # quote it after a restart and launch-time auto-provision can
    # honour the retry cadence in auto_start_allowed() instead of
    # hammering a dead network at every start.
    if whisper_ready() and kokoro_ready() and model_ready():
        _progress_set_run_state("complete")
        _clear_download_failure()
    elif any(line.startswith("ERROR:") for line in lines):
        _progress_set_run_state("failed")
        first_error = next(
            line for line in lines if line.startswith("ERROR:"))
        _record_download_failure(first_error)
    else:
        _progress_set_run_state("idle")
    return lines


# --- Background download manager -----------------------------------------
class DownloadManager:
    """Thread-safe background runner for ensure_local_assets().

    The Settings "Set up local brain" button drives downloads through
    the `downloads` singleton below, so a multi-GB download survives
    page switches in the UI and can be paused (cancel()) — the partial
    .part files remain on disk and the next start() resumes them. The
    local session does NOT use this manager: it calls
    ensure_local_assets() directly with its own cancel event (see
    core/voice/local_client.py's _prepare_builtin).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel_event: threading.Event | None = None
        self._lines: list[str] = []

    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self, progress_cb: Callable[[str], None],
              done_cb: Callable[[list[str]], None] | None = None) -> bool:
        """Starts a background setup run. Returns False (and does
        nothing) when a run is already in flight."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._cancel_event = threading.Event()
            self._lines = []
            self._thread = threading.Thread(
                target=self._run,
                args=(progress_cb, done_cb),
                daemon=True,
                name="NovaOfflinePackDownload",
            )
            self._thread.start()
            return True

    def _run(self, progress_cb, done_cb):
        def collect(line: str):
            with self._lock:
                self._lines.append(line)
            try:
                progress_cb(line)
            except Exception:  # noqa: BLE001 — UI callback must not kill the run
                pass

        try:
            # NOTE: ensure_local_assets is looked up in module globals
            # at call time — tests (and only tests) monkeypatch it here.
            lines = ensure_local_assets(collect, self._cancel_event)
        except Exception as e:  # noqa: BLE001 — ensure never raises by contract; belt-and-braces
            lines = [f"ERROR: local setup failed: {e}"]
            collect(lines[0])
        with self._lock:
            self._lines = list(lines)
        if done_cb is not None:
            try:
                done_cb(lines)
            except Exception:  # noqa: BLE001
                pass

    def cancel(self):
        """Asks the current run to pause at the next chunk boundary;
        the partial .part files stay on disk as resume state."""
        with self._lock:
            event = self._cancel_event
        if event is not None:
            event.set()

    def last_lines(self) -> list[str]:
        """The progress lines of the current (or last finished) run."""
        with self._lock:
            return list(self._lines)


downloads = DownloadManager()
