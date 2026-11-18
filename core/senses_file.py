"""Split from core/senses.py (Phase B batch B4): family impl module. Move only — see senses.py facade."""

import hashlib
import zipfile
from pathlib import Path


# ---------------------------------------------------------------------------
# sniff_file — what is this file, really
# ---------------------------------------------------------------------------

_HASH_CAP = 16 * 1024 * 1024  # sha256 covers at most the first 16 MiB

# Extensions that are ZIP containers by design (Office, Java,
# ebooks): ZIP magic in them is expected, never a mismatch.
_ZIP_CONTAINER_EXTS = {".docx", ".xlsx", ".pptx", ".odt", ".ods",
                       ".jar", ".apk", ".epub", ".zip"}

_EXT_TYPES = {
    ".png": "PNG image", ".jpg": "JPEG image", ".jpeg": "JPEG image",
    ".gif": "GIF image", ".pdf": "PDF document", ".zip": "ZIP archive",
    ".exe": "Windows executable", ".dll": "Windows executable",
    ".com": "Windows executable", ".scr": "Windows executable",
    ".wav": "WAV audio", ".mp3": "MP3 audio",
    ".mp4": "MP4 video", ".mov": "MP4 video", ".m4a": "MP4 audio",
    ".txt": "text", ".md": "text", ".py": "text", ".json": "text",
    ".csv": "text", ".log": "text", ".html": "text", ".xml": "text",
}


def _detect_magic(head: bytes) -> str | None:
    """The file type its first bytes claim, or None when no known
    signature matches."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG image"
    if head.startswith(b"\xff\xd8\xff"):
        return "JPEG image"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "GIF image"
    if head.startswith(b"%PDF"):
        return "PDF document"
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return "ZIP archive"
    if head.startswith(b"MZ"):
        return "Windows executable"
    if head.startswith(b"\x7fELF"):
        return "Linux executable (ELF)"
    if head.startswith(b"ID3") or head.startswith(b"\xff\xfb"):
        return "MP3 audio"
    if head[0:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "WAV audio"
    if head[4:8] == b"ftyp":
        return "MP4 video"
    return None


def _looks_like_text(sample: bytes) -> bool:
    """A short sample reads as text when it decodes as UTF-8 and
    carries no NUL bytes."""
    if not sample:
        return True  # an empty file is nobody's idea of a binary
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _size_human(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" \
                else f"{value:.1f} {unit}"
        value /= 1024
    return f"{num_bytes} B"


def sniff_file(path) -> dict:
    """Inspect a file's identity: size, what its bytes say it is
    vs what its name claims, a sha256 (first 16 MiB), ZIP contents
    summary, or text stats. A name/contents MISMATCH is the
    headline — that is how disguised files get caught."""
    try:
        target = Path(str(path)).expanduser()
        if not target.exists():
            return {"ok": False, "verdict": "That file doesn't exist."}
        if target.is_dir():
            return {"ok": False,
                    "verdict": "That's a folder, not a file — "
                               "point me at a file and I'll sniff it."}
        size = target.stat().st_size
        ext = target.suffix.lower()
        report: dict = {
            "ok": True, "path": str(target), "name": target.name,
            "size_bytes": size, "size_human": _size_human(size),
            "extension": ext,
        }
        # Header bytes for magic detection + hashing in one pass.
        head = b""
        digest = hashlib.sha256()
        hashed = 0
        with target.open("rb") as fh:
            head = fh.read(8192)
            fh.seek(0)
            remaining = _HASH_CAP
            while remaining > 0:
                chunk = fh.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                digest.update(chunk)
                hashed += len(chunk)
                remaining -= len(chunk)
        report["sha256"] = digest.hexdigest()
        report["hash_truncated"] = hashed < size

        detected = _detect_magic(head)
        is_text = False
        if detected is None and _looks_like_text(head[:4096]):
            detected = "text"
            is_text = True
        report["detected_type"] = detected or "unknown binary data"

        if detected == "ZIP archive":
            try:
                with zipfile.ZipFile(target) as zf:
                    infos = zf.infolist()
                    total = sum(i.file_size for i in infos)
                report["zip_entries"] = len(infos)
                report["zip_uncompressed_bytes"] = total
                ratio = (total / size) if size else 0.0
                report["zip_ratio"] = round(ratio, 1)
                report["zip_bomb_suspect"] = ratio > 100
            except zipfile.BadZipFile:
                report["zip_error"] = "The ZIP header reads as an " \
                                      "archive but the contents don't parse."
        if is_text:
            try:
                raw = target.read_bytes()
                report["lines"] = len(raw.splitlines())
                try:
                    raw.decode("utf-8")
                    report["utf8_valid"] = True
                except UnicodeDecodeError:
                    report["utf8_valid"] = False
            except OSError:
                pass

        expected = _EXT_TYPES.get(ext)
        mismatch = False
        if detected and expected and detected != expected:
            if not (detected == "ZIP archive" and ext in _ZIP_CONTAINER_EXTS):
                mismatch = True
        report["mismatch"] = mismatch
        if mismatch:
            verdict = (f"The name says {ext} but the contents are a "
                       f"{detected} — treat with care.")
        elif detected == "Windows executable" or \
                detected == "Linux executable (ELF)":
            verdict = (f"A real {detected} ({report['size_human']}). "
                       "Only run it if it came from somewhere you trust.")
        elif detected == "ZIP archive" and ext in _ZIP_CONTAINER_EXTS \
                and ext != ".zip":
            verdict = ("An Office/container file — a ZIP archive "
                       "underneath, exactly as expected.")
        elif report.get("zip_bomb_suspect"):
            verdict = ("A ZIP archive claiming to expand over 100× "
                       "its size — a possible zip bomb. Don't extract "
                       "it blindly.")
        elif detected:
            verdict = f"A {detected} ({report['size_human']}), " \
                      "and its name agrees."
        else:
            verdict = ("Unrecognised binary data — no known file "
                       "signature, and the name doesn't help.")
        report["verdict"] = verdict
        return report
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "verdict": f"I couldn't sniff that file: {e}"}

