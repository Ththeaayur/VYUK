"""Proxy + download-transport family (SKILLS SPLIT PHASE B, local_assets batch 1).

System-proxy routing, mirror fallback, zip-bomb caps, integrity hashes,
and the download_file / _download_once transport, split out of
core/voice/local_assets.py. The facade (local_assets.py) re-exports
every name here; behaviour is unchanged.
"""

import hashlib
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

# INTENTIONAL: cross-module imports (proxy -> model; acyclic).
from .local_assets_model import _size, model_tier_for

WAKE_MODEL_NAME = "vosk-model-small-en-us-0.15"


PIPER_VOICE_FILE = "en_US-amy-medium.onnx"


DOWNLOAD_SHA256 = {
    "kokoro-v1.0.onnx":
        "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
    "voices-v1.0.bin":
        "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
    f"{WAKE_MODEL_NAME}.zip":
        "30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498",
    PIPER_VOICE_FILE:
        "b3a6e47b57b8c7fbe6a0ce2518161a50f59a9cdd8a50835c02cb02bdd6206c18",
    f"{PIPER_VOICE_FILE}.json":
        "95a23eb4d42909d38df73bb9ac7f45f597dbfcde2d1bf9526fdeaf5466977d77",
}


_CHUNK_BYTES = 1024 * 1024               # 1 MB streaming chunks


_DOWNLOAD_TIMEOUT_S = 60                 # per socket operation, not total


def _human_size(num_bytes: int) -> str:
    if num_bytes >= 1_000_000_000:
        return f"{num_bytes / 1_000_000_000:.1f} GB"
    if num_bytes >= 1_000_000:
        return f"{num_bytes / 1_000_000:.0f} MB"
    if num_bytes >= 1000:
        return f"{num_bytes / 1000:.0f} KB"
    return f"{num_bytes} B"


_HF_PRIMARY_HOST = "huggingface.co"


_HF_MIRROR_HOST = "hf-mirror.com"


HF_MIRROR_BASE = "https://hf-mirror.com"


def _merged_proxies() -> dict[str, str]:
    """Raw system proxy values for http/https: getproxies() first
    (registry + environment), then explicit HTTP_PROXY / HTTPS_PROXY
    env vars win per scheme. Values are NOT yet validated — a stack
    can hand back a PAC file URL or plain garbage, and dialing either
    would be worse than going direct (see _usable_proxy)."""
    found: dict = {}
    try:
        found = urllib.request.getproxies() or {}
    except Exception:  # noqa: BLE001 — proxy discovery must never break a download
        found = {}
    merged: dict[str, str] = {}
    for scheme in ("http", "https"):
        value = found.get(scheme)
        if isinstance(value, str) and value.strip():
            merged[scheme] = value.strip()
    for scheme in ("http", "https"):
        for var in (f"{scheme.upper()}_PROXY", f"{scheme}_proxy"):
            value = (os.environ.get(var) or "").strip()
            if value:
                merged[scheme] = value
                break
    return merged


def _usable_proxy(value) -> str | None:
    """A proxy value urllib can actually dial: an http/https URL with
    a host and a sane port. Anything else — a PAC (.pac) URL, a bare
    host:port with no scheme, malformed junk — returns None so the
    caller connects directly instead of crashing on proxy discovery."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value.lower().endswith(".pac"):
        return None
    try:
        parts = urllib.parse.urlsplit(value)
        host = parts.hostname
        _ = parts.port  # property access raises on a malformed port
    except Exception:  # noqa: BLE001
        return None
    if parts.scheme not in ("http", "https") or not host:
        return None
    return value


def _proxy_description(clean: dict[str, str]) -> str | None:
    """"host:port" of the proxy a download will use (https preferred —
    every pack download is https), credentials stripped: this string
    goes into progress lines and logs, so it must never carry a
    password."""
    for scheme in ("https", "http"):
        value = clean.get(scheme)
        if not value:
            continue
        try:
            parts = urllib.parse.urlsplit(value)
            host = parts.hostname or ""
            port = parts.port
        except Exception:  # noqa: BLE001
            continue
        if host:
            return f"{host}:{port}" if port else host
    return None


def _build_opener() -> tuple[object, str | None]:
    """(opener, proxy description) for one download_file() call.

    The opener is built from the validated system proxies. ANY
    problem — getproxies() raising, every value unusable — falls back
    to an explicitly proxy-free opener: direct, like the pre-v3.3
    behaviour, and never a crash over proxy discovery."""
    try:
        clean: dict[str, str] = {}
        for scheme, value in _merged_proxies().items():
            usable = _usable_proxy(value)
            if usable:
                clean[scheme] = usable
        if clean:
            handler = urllib.request.ProxyHandler(clean)
            return (urllib.request.build_opener(handler),
                    _proxy_description(clean))
    except Exception:  # noqa: BLE001 — fall through to direct
        pass
    return urllib.request.build_opener(urllib.request.ProxyHandler({})), None


def _open_response(req, opener, timeout: float):
    """Opens `req` through `opener` — the single network seam of the
    download path (tests substitute it to point huggingface.co and
    hf-mirror.com at local servers; production code just dials)."""
    return opener.open(req, timeout=timeout)


def _mirror_variant(url: str) -> str | None:
    """The hf-mirror.com twin of a huggingface.co URL — identical
    path and bytes, different host — or None when `url` is not a
    huggingface.co URL (GitHub release downloads, the Kokoro voice
    files, have no mirror twin and are not rewritten)."""
    try:
        parts = urllib.parse.urlsplit(url)
    except Exception:  # noqa: BLE001
        return None
    if (parts.hostname or "") != _HF_PRIMARY_HOST:
        return None
    return urllib.parse.urlunsplit(
        (parts.scheme or "https", _HF_MIRROR_HOST, parts.path,
         parts.query, parts.fragment)
    )


def _arm_hf_mirror() -> bool:
    """Points the Hugging Face hub library at hf-mirror.com for a
    retry (the Whisper weights fetch, which huggingface_hub performs
    itself). Returns False when HF_ENDPOINT is already set — that is
    the user's own endpoint choice and it wins. Best-effort: sets the
    env var (future imports honour it) AND patches
    huggingface_hub.constants in place when the library is already
    imported in this process, because the constant is frozen at
    import time; if neither takes effect, the retry still runs and
    simply uses the original endpoint until the next app start."""
    if (os.environ.get("HF_ENDPOINT") or "").strip():
        return False
    os.environ["HF_ENDPOINT"] = HF_MIRROR_BASE
    try:
        import huggingface_hub.constants as _hf_constants

        _hf_constants.HF_ENDPOINT = HF_MIRROR_BASE
    except Exception:  # noqa: BLE001 — env var is set; that may be enough
        pass
    return True


def sha256_of(path: Path) -> str:
    """Streaming SHA-256 hex digest of a file — read in 1 MB chunks,
    so even a 5 GB brain file is hashed in constant memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


ZIP_MAX_UNCOMPRESSED_BYTES = 10_000_000_000   # 10 GB total output


ZIP_MAX_COMPRESSION_RATIO = 100.0             # uncompressed / archive size


ZIP_MAX_MEMBERS = 10_000                      # member count


def zip_bomb_refusal(infos, archive_bytes: int) -> str | None:
    """The zip-bomb refusal for an archive, or None when it is
    within the caps (v5.47, programme item 18).

    `infos` is the archive's ZipInfo list (zf.infolist()) and
    `archive_bytes` the archive file's own size on disk. The
    check is pure header arithmetic — total uncompressed size,
    compression ratio, member count — so it runs BEFORE any
    member is extracted and costs nothing. The refusal message
    carries the actual numbers next to the caps, so a refused
    archive can be judged from the log line alone. Never raises:
    an unreadable header is the caller's corrupt-zip path, not a
    bomb verdict, so surprises here degrade to None."""
    try:
        members = list(infos)
        total = sum(int(getattr(info, "file_size", 0) or 0)
                    for info in members)
        count = len(members)
        if archive_bytes and archive_bytes > 0:
            ratio = total / archive_bytes
        else:
            ratio = float("inf") if total > 0 else 0.0
    except Exception:  # noqa: BLE001 — see docstring (caller's path)
        return None
    if (total <= ZIP_MAX_UNCOMPRESSED_BYTES
            and ratio <= ZIP_MAX_COMPRESSION_RATIO
            and count <= ZIP_MAX_MEMBERS):
        return None
    ratio_text = (f"{ratio:.1f}" if ratio != float("inf") else "inf")
    return (
        "Refused: this archive exceeds the zip-bomb safety caps "
        f"({count} members, cap {ZIP_MAX_MEMBERS}; "
        f"{total} bytes uncompressed, cap "
        f"{ZIP_MAX_UNCOMPRESSED_BYTES} bytes; compression ratio "
        f"{ratio_text}, cap {ZIP_MAX_COMPRESSION_RATIO:g}) — "
        "nothing was extracted"
    )


def _tier_sha256(dest: Path) -> str | None:
    """The expected SHA-256 for a download destination, or None when
    no hash is on record. Sources, in order: the three tier brain
    files (MODEL_TIERS — the single runtime source of truth for
    those), then DOWNLOAD_SHA256 (v5.47, programme item 17 — the
    Kokoro pair, the Vosk wake zip, and the Piper voice + config,
    keyed by destination filename). This one lookup is what the
    _download_once integrity gate consults, so every pinned asset
    is verified by the same gate. A custom model the user placed
    by hand has no hash on record, so its downloads skip
    verification (the size floors still apply)."""
    found = model_tier_for(dest.name)
    if found:
        return found[1].get("sha256") or None
    return DOWNLOAD_SHA256.get(dest.name) or None


_INTEGRITY_PREFIX = "INTEGRITY::"


def _integrity_error(result) -> str | None:
    """The plain error text when `result` is a sentinel-marked
    integrity failure, else None."""
    if isinstance(result, str) and result.startswith(_INTEGRITY_PREFIX):
        return result[len(_INTEGRITY_PREFIX):]
    return None


def download_file(url: str, dest: Path, progress_cb: Callable[[str], None],
                  label: str,
                  cancel_event: threading.Event | None = None,
                  progress_asset: str | None = None) -> str | None:
    """Streams `url` to `dest`, reporting progress lines to progress_cb.

    Returns None on success, the sentinel string "CANCELLED" when
    `cancel_event` is set mid-download, or an error string on failure —
    this function never raises. The download lands in a sibling
    ".part" file first and is moved into place with os.replace only
    when complete, so a killed download never leaves a half-file that
    *_ready() would accept (the floors above are the second line of
    defence).

    `progress_asset` (v3.2) names this download in the structured
    progress tracker ("kokoro_model" / "kokoro_voices" / "brain") so
    pack_progress() can report real byte counts next to the text
    lines; the lines themselves are unchanged.

    RESUME (v2.0): if the .part already holds N > 0 bytes, the request
    carries `Range: bytes=N-`. A 206 reply appends to the .part and
    progress counts the resumed bytes (percentages are of the FULL
    file; progress lines start with "Resuming"). A 200 reply means
    the server ignored the range — the .part is truncated and the file
    restarts (a progress line says so). A network failure or a cancel
    KEEPS the .part: it is resume state, not junk (ARCHITECTURE.md
    landmine 13). On success the final size is verified against the
    expected total when the server provided one.

    TRANSPORT (v3.3): the attempt goes through the system proxy when
    one is configured and usable (a single "Downloading via proxy
    host:port" line is emitted — host only, never credentials), and a
    failed huggingface.co attempt is retried ONCE against
    hf-mirror.com — same path, same ".part" resume state; when the
    mirror also fails, the ORIGINAL error is the one returned.

    INTEGRITY (v4.2; extended v5.47, programme item 17): when the
    destination is a pinned file — a tier brain file (MODEL_TIERS)
    or a DOWNLOAD_SHA256 asset (Kokoro, Vosk wake zip, Piper) — the
    completed download is SHA-256-verified against its pin before
    it is promoted into place (see the gate in _download_once). A
    mismatch is an integrity failure, not
    a transport failure: it is returned immediately (no mirror
    retry), the corrupt file is deleted, and the run fails through
    the usual error plumbing.
    """
    from . import local_assets as _la  # INTENTIONAL: facade-at-call-time for batch-3 progress names
    # Gate C (P2-2): the power/network policy, enforced at this
    # download entry point — every asset download in the tree
    # funnels through here (ensure_* paths and the toolbox
    # embedding provisioner), so one gate covers them all. A
    # paused download returns its refusal as the usual error
    # string before any socket opens; callers already surface
    # that string as an ERROR line. Same non-inventions as
    # updater._policy_refusal: no battery-based refusal exists
    # in the policy, and an unreadable policy fails open.
    try:
        from .. import power_policy as _power_policy  # Phase 3 V2: power_policy stays flat
        _policy = _power_policy.current_policy()
        if not _power_policy.downloads_allowed(_policy):
            try:
                _detail = str(_policy.get("reason") or "")
            except Exception:  # noqa: BLE001 — cosmetic
                _detail = ""
            if _detail:
                return f"downloads paused by power/network policy ({_detail})"
            return "downloads paused by power/network policy"
    except Exception:  # noqa: BLE001 — unreadable policy: open
        pass
    opener, proxy_desc = _build_opener()
    if proxy_desc:
        progress_cb(f"Downloading via proxy {proxy_desc}")
    result = _download_once(url, dest, progress_cb, label,
                            cancel_event, progress_asset, opener)
    integrity = _integrity_error(result)
    if integrity is not None:
        return integrity
    if result is None or result == "CANCELLED":
        return result
    mirror = _mirror_variant(url)
    if mirror is None:
        return result
    progress_cb(
        f"{label}: huggingface.co didn't answer — "
        "retrying via the hf-mirror.com mirror…"
    )
    retry = _download_once(mirror, dest, progress_cb, label,
                           cancel_event, progress_asset, opener)
    integrity = _integrity_error(retry)
    if integrity is not None:
        return integrity
    if retry is None or retry == "CANCELLED":
        return retry
    if progress_asset:
        # Both hosts failed: the error this function surfaces is the
        # ORIGINAL host's — make the tracker tell the same story, so
        # pack_progress()'s stage error matches the emitted ERROR line
        # instead of the mirror's secondary refusal.
        _la._progress_update(progress_asset, state="error", error=result)  # INTENTIONAL: facade-at-call-time (batch 3 owns progress)
    return result


def _download_once(url: str, dest: Path, progress_cb: Callable[[str], None],
                   label: str,
                   cancel_event: threading.Event | None = None,
                   progress_asset: str | None = None,
                   opener=None,
                   _range_retried: bool = False) -> str | None:
    """One attempt at `url` (see download_file for the full contract).
    `opener` carries the system-proxy routing chosen by
    download_file() for the whole call; a missing opener is built
    here so direct callers get the same routing.

    416 RECOVERY (v5.17): when a RESUME attempt (a .part with bytes
    already in it) dies with HTTP 416, the .part is deleted and this
    same URL is retried ONCE, fresh, inside this call — recovery
    happens here, before download_file ever needs its mirror
    fallback. `_range_retried` latches the single retry."""
    from . import local_assets as _la  # INTENTIONAL: facade-at-call-time for batch-3 progress names
    if opener is None:
        opener, _desc = _build_opener()
    part = dest.with_suffix(dest.suffix + ".part")

    def cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if cancelled():
            return "CANCELLED"
        existing = _size(part)
        headers = {"User-Agent": "VYUK/local-assets"}
        if existing > 0:
            headers["Range"] = f"bytes={existing}-"
        req = urllib.request.Request(url, headers=headers)
        with _open_response(req, opener, _DOWNLOAD_TIMEOUT_S) as resp:
            status = getattr(resp, "status", 200) or 200
            length = resp.headers.get("Content-Length") if resp.headers else None
            try:
                length_val = int(length) if length else 0
            except ValueError:
                length_val = 0

            if existing > 0 and status == 206:
                # Resume accepted: append; the expected total is what
                # we already have plus what this response will send.
                downloaded = existing
                total = existing + length_val if length_val else 0
                prefix = "Resuming"
                mode = "ab"
                if total > 0:
                    progress_cb(f"Resuming {label} — {int(downloaded * 100 / total)}%")
                else:
                    progress_cb(f"Resuming {label} — {_human_size(downloaded)} so far")
            else:
                if existing > 0:
                    # The server ignored the Range request (or answered
                    # a fresh 200): truncate and restart this file.
                    progress_cb("Server doesn't support resume — restarting this file.")
                downloaded = 0
                total = length_val
                prefix = "Downloading"
                mode = "wb"

            if progress_asset:
                # The response headers are open: the total (when the
                # server declared one) and the resumed byte count are
                # the structured truth behind the text lines.
                _la._progress_begin(progress_asset,  # INTENTIONAL: facade-at-call-time (batch 3 owns progress)
                                total=total if total > 0 else None,
                                done=downloaded)
            tracked = downloaded
            if total > 0:
                last_pct = int(downloaded * 100 / total)
            else:
                last_pct = -2
            last_mb_step = downloaded // (10 * 1024 * 1024)
            with open(part, mode) as out:
                while True:
                    if cancelled():
                        if progress_asset:
                            _la._progress_update(progress_asset, done=downloaded,  # INTENTIONAL: facade-at-call-time (batch 3 owns progress)
                                             state="paused")
                        return "CANCELLED"
                    chunk = resp.read(_CHUNK_BYTES)
                    if not chunk:
                        break
                    out.write(chunk)
                    downloaded += len(chunk)
                    if progress_asset and (
                        downloaded - tracked >= 262144 or downloaded >= total
                    ):
                        _la._progress_update(progress_asset, done=downloaded)  # INTENTIONAL: facade-at-call-time (batch 3 owns progress)
                        tracked = downloaded
                    if cancelled():
                        if progress_asset:
                            _la._progress_update(progress_asset, done=downloaded,  # INTENTIONAL: facade-at-call-time (batch 3 owns progress)
                                             state="paused")
                        return "CANCELLED"
                    if total > 0:
                        pct = int(downloaded * 100 / total)
                        if pct - last_pct >= 2 or downloaded >= total:
                            progress_cb(f"{prefix} {label} — {pct}%")
                            last_pct = pct
                    else:
                        mb_step = downloaded // (10 * 1024 * 1024)
                        if mb_step > last_mb_step:
                            progress_cb(
                                f"{prefix} {label} — "
                                f"{downloaded / (1024 * 1024):.0f} MB"
                            )
                            last_mb_step = mb_step
        if cancelled():
            if progress_asset:
                _la._progress_update(progress_asset, done=downloaded, state="paused")  # INTENTIONAL: facade-at-call-time (batch 3 owns progress)
            return "CANCELLED"
        if total > 0 and downloaded != total:
            # Short read: keep the .part so the next attempt resumes.
            err_text = (f"download of {label} failed: incomplete transfer "
                        f"({downloaded} of {total} bytes) — "
                        "it will resume next time")
            if progress_asset:
                _la._progress_update(progress_asset, done=downloaded,  # INTENTIONAL: facade-at-call-time (batch 3 owns progress)
                                 state="error", error=err_text)
            return err_text
        # v4.2 INTEGRITY GATE (extended v5.47, programme item 17):
        # a PINNED file — a tier brain file (MODEL_TIERS) or one of
        # the DOWNLOAD_SHA256 assets (Kokoro pair, Vosk wake zip,
        # Piper voice + config) — is verified against its SHA-256
        # while it is still the .part: the file only counts as ready
        # once os.replace promotes it, and a corrupt file never gets
        # promoted. On a mismatch the .part is DELETED, not kept: its
        # bytes are wrong, so resuming them could never heal the file
        # (this is the one deliberate exception to landmine 13's
        # "keep the .part" rule, which exists for INCOMPLETE
        # downloads). The failure returns sentinel-marked so
        # download_file skips its mirror retry and the error flows
        # through the normal plumbing (stage error, persisted
        # last_error, run state "failed", 600 s auto-retry cadence).
        # Files with no hash on record (a custom brain) skip the
        # gate entirely. Files already on disk are deliberately NOT
        # re-hashed at startup — verification is a
        # download-completion gate, not a multi-GB read tax on every
        # launch.
        expected_sha = _tier_sha256(dest)
        if expected_sha is not None and sha256_of(part) != expected_sha:
            try:
                part.unlink()
            except OSError:
                pass
            err_text = (
                f"download of {label} failed: the downloaded file "
                "failed its integrity check (SHA-256 mismatch) — the "
                "corrupt file was removed; downloading again is safe"
            )
            if progress_asset:
                _la._progress_update(progress_asset, done=downloaded,  # INTENTIONAL: facade-at-call-time (batch 3 owns progress)
                                 state="error", error=err_text)
            return _INTEGRITY_PREFIX + err_text
        if total <= 0:
            progress_cb(f"{prefix} {label} — {_human_size(downloaded)}")
        if progress_asset:
            _la._progress_update(progress_asset, done=downloaded,  # INTENTIONAL: facade-at-call-time (batch 3 owns progress)
                             total=total if total > 0 else downloaded,
                             state="done")
        os.replace(part, dest)
        if progress_asset:
            # Record which host actually served the file — the
            # doctor quotes it, and it is how a mirror save becomes
            # visible instead of silent.
            host = urllib.parse.urlsplit(url).hostname
            if host:
                _la._progress_update(progress_asset, via=host)  # INTENTIONAL: facade-at-call-time (batch 3 owns progress)
                _la._record_served_by(host)  # INTENTIONAL: facade-at-call-time (batch 3 owns progress)
        return None
    except Exception as e:  # noqa: BLE001 — callers want a string, not a raise
        # 416 RECOVERY (v5.17): a resume refused with "416 Range Not
        # Satisfiable" can NEVER succeed — the saved .part is already
        # complete, or oversized/poisoned (e.g. written while an old
        # broken URL served different bytes), so every future Range
        # request fails the same way and the download is stuck
        # forever. Delete the .part and retry this URL once, fresh,
        # inside this call; the retry's outcome is the call's
        # outcome. A 416 on a fresh attempt (nothing saved) stays a
        # plain error, and _range_retried caps this at two attempts.
        if (not _range_retried
                and isinstance(e, urllib.error.HTTPError)
                and getattr(e, "code", None) == 416
                and (locals().get("existing", 0) or 0) > 0):
            try:
                part.unlink()
            except OSError:
                pass
            progress_cb(
                f"{label}: the saved partial download can't be "
                "resumed — starting this file fresh.")
            return _download_once(url, dest, progress_cb, label,
                                  cancel_event, progress_asset, opener,
                                  _range_retried=True)
        # The .part (if any bytes landed) is deliberately KEPT: it is
        # the resume state the next attempt continues from.
        err_text = f"download of {label} failed: {e}"
        if progress_asset:
            if cancelled():
                _la._progress_update(  # INTENTIONAL: facade-at-call-time (batch 3 owns progress)
                    progress_asset,
                    done=locals().get("downloaded", 0) or 0,
                    state="paused",
                )
            else:
                _la._progress_update(  # INTENTIONAL: facade-at-call-time (batch 3 owns progress)
                    progress_asset,
                    done=locals().get("downloaded", 0) or 0,
                    state="error",
                    error=err_text,
                )
        return err_text
