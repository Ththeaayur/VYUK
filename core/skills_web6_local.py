"""Local-machine tools for the skills_web_watch pack (moved verbatim)."""

from pathlib import Path
import json
import sys
import time
# INTENTIONAL: cross-part names live in the helpers module (bare-name rule, plan §2 P3); the facade re-export alone would not resolve them in this module's globals.
from .skills_web6_helpers import (_size_words)


# ---------------------------------------------------------------------------
# DOWNLOAD ETA — the Downloads folder's in-progress file (read-only)
# ---------------------------------------------------------------------------

_PARTIAL_SUFFIXES = (".part", ".crdownload", ".tmp", ".download",
                     ".opdownload", ".partial")


def _downloads_dir() -> Path:
    return Path.home() / "Downloads"


def download_eta() -> str:
    """Size and pace of the newest in-progress download, in words."""
    folder = _downloads_dir()
    if not folder.is_dir():
        return (f"There's no Downloads folder at {folder}, so I "
                "can't watch for downloads there.")
    try:
        files = [p for p in folder.iterdir() if p.is_file()]
    except OSError as e:
        return f"I couldn't look inside {folder}: {e}"
    if not files:
        return "Your Downloads folder is empty — nothing is downloading."
    partials = [p for p in files
                if p.name.lower().endswith(_PARTIAL_SUFFIXES)]
    candidate = None
    if partials:
        candidate = max(partials, key=lambda p: p.stat().st_mtime)
    else:
        newest = max(files, key=lambda p: p.stat().st_mtime)
        if time.time() - newest.stat().st_mtime < 15 * 60:
            candidate = newest
    if candidate is None:
        return ("No download is in progress — there are no partial "
                "files in Downloads, and nothing there changed in "
                "the last 15 minutes.")
    try:
        size1 = candidate.stat().st_size
        time.sleep(2)
        if not candidate.exists():
            return (f"{candidate.name} vanished while I measured "
                    "it — the download likely just finished.")
        size2 = candidate.stat().st_size
    except OSError as e:
        return f"I couldn't measure {candidate.name}: {e}"
    rate = (size2 - size1) / 2.0
    lines = [f"{candidate.name} — {_size_words(size2)} so far."]
    if rate > 0:
        lines.append(f"It's still growing, at about "
                     f"{_size_words(rate)} a second.")
        lines.append("I don't know its full size — partial files "
                     "don't say — so I can't give an honest "
                     "finish time.")
    elif size2 == size1:
        lines.append("It didn't grow in the couple of seconds I "
                     "watched — it may be paused, stalled, or "
                     "finished and just named like a partial file.")
    else:
        lines.append("It actually shrank while I watched — odd; "
                     "the app behind it may have restarted it.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SITE PERMISSIONS REVIEW — what the browser profile has granted (read-only)
# ---------------------------------------------------------------------------

_PERM_LABELS = {
    "media_stream_camera": "camera",
    "media_stream_mic": "microphone",
    "media_stream": "camera and microphone",
    "geolocation": "location",
    "notifications": "notifications",
}


def _profile_pref_files() -> list[tuple[str, Path]]:
    """(browser label, Preferences path) for the usual profiles."""
    home = Path.home()
    roots: list[tuple[str, Path]] = []
    if sys.platform.startswith("win"):
        local = Path.home() / "AppData" / "Local"
        roots = [("Chrome", local / "Google" / "Chrome" / "User Data"),
                 ("Edge", local / "Microsoft" / "Edge" / "User Data")]
    elif sys.platform == "darwin":
        support = home / "Library" / "Application Support"
        roots = [("Chrome", support / "Google" / "Chrome"),
                 ("Edge", support / "Microsoft Edge")]
    else:
        config_home = home / ".config"
        roots = [("Chrome", config_home / "google-chrome"),
                 ("Chromium", config_home / "chromium"),
                 ("Edge", config_home / "microsoft-edge")]
    found: list[tuple[str, Path]] = []
    for label, root in roots:
        if not root.is_dir():
            continue
        profiles = [root / "Default"]
        try:
            profiles += sorted(root.glob("Profile *"))
        except OSError:
            pass
        for profile in profiles[:4]:
            prefs = profile / "Preferences"
            if prefs.is_file():
                found.append((f"{label} ({profile.name})", prefs))
    return found


def site_permissions_review() -> str:
    """Which sites may use the camera/mic/location/notifications."""
    pref_files = _profile_pref_files()
    if not pref_files:
        return ("I couldn't find a Chrome or Edge profile on this "
                "PC (I looked in the usual places), so there are no "
                "saved site permissions for me to review.")
    grants: dict[str, set[str]] = {}
    sources: list[str] = []
    for label, prefs in pref_files:
        try:
            data = json.loads(prefs.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — unreadable profile: skip it
            continue
        sources.append(label)
        exceptions = (((data.get("profile") or {})
                       .get("content_settings") or {})
                      .get("exceptions") or {})
        for key, friendly in _PERM_LABELS.items():
            for origin, value in (exceptions.get(key) or {}).items():
                if isinstance(value, dict) and value.get("setting") == 1:
                    site = origin.split(",", 1)[0]
                    grants.setdefault(site, set()).add(friendly)
    if not grants:
        checked = ", ".join(sources) or "none readable"
        return ("No website has been allowed the camera, "
                "microphone, location or notifications in the "
                f"browser profile(s) I checked ({checked}).")
    lines = [f"{len(grants)} site(s) have been given browser "
             f"permissions ({', '.join(sources)}):"]
    for site in sorted(grants)[:20]:
        lines.append(f"• {site} — {', '.join(sorted(grants[site]))}")
    if len(grants) > 20:
        lines.append(f"…and {len(grants) - 20} more site(s).")
    lines.append("(Read from the profile's settings file — "
                 "nothing was changed.)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# TAB CENSUS — count and group the open tabs (read-only)
# ---------------------------------------------------------------------------


def _tab_rows():
    """[(title, url)] via the browser engine's worker seam, None
    when no browser is connected, 'error' when it misanswers."""
    from . import browser

    if not browser._connected():
        return None

    def _job() -> str:
        pages = browser._state["context"].pages
        rows = []
        for page in pages:
            try:
                title = page.title()
            except Exception:  # noqa: BLE001
                title = ""
            try:
                page_url = page.url
            except Exception:  # noqa: BLE001
                page_url = ""
            rows.append({"title": title or "", "url": page_url or ""})
        return json.dumps(rows)

    try:
        data = json.loads(browser._submit(_job))
    except Exception:  # noqa: BLE001 — a non-JSON answer is a misanswer
        return "error"
    if not isinstance(data, list):
        return "error"
    return [(row.get("title", ""), row.get("url", ""))
            for row in data if isinstance(row, dict)]


def tab_census() -> str:
    """How many tabs are open, grouped by page."""
    rows = _tab_rows()
    if rows is None:
        return ("The browser isn't connected right now, so I "
                "can't see its tabs — tap 'Connect my browser' to "
                "set it up in one tap (or Settings → Browser "
                "control).")
    if rows == "error":
        return ("The browser connection answered something I "
                "couldn't read, so I can't count the tabs right now.")
    if not rows:
        return "The browser is connected, but no tabs are open."
    total = len(rows)
    by_url: dict[str, list[str]] = {}
    for title, page_url in rows:
        if page_url:
            by_url.setdefault(page_url, []).append(title)
    dup_groups = {u: t for u, t in by_url.items() if len(t) > 1}
    dup_tabs = sum(len(t) for t in dup_groups.values())
    if dup_tabs:
        lines = [f"{total} tab(s) open — {dup_tabs} of them are "
                 "the same page as another tab:"]
        for page_url, titles in dup_groups.items():
            label = next((t for t in titles if t), "") or page_url
            lines.append(f"• {len(titles)} tabs on '{label}' "
                         f"({page_url})")
        lines.append("All the tabs:")
    else:
        lines = [f"{total} tab(s) open, all on different pages:"]
    for title, page_url in rows[:25]:
        lines.append(f"• {title or '(untitled)'} — {page_url}")
    if total > 25:
        lines.append(f"…and {total - 25} more tab(s).")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# QR DECODE — read a QR code from an image file (read-only)
# ---------------------------------------------------------------------------


def qr_decode_image(image_path: str) -> str:
    """Decode the QR code in an image; honest when it can't."""
    raw = (image_path or "").strip()
    if not raw:
        return "Give me the path to the image holding the QR code."
    path = Path(raw).expanduser()
    if not path.is_file():
        return (f"I can't find an image at {raw} — check the "
                "path and I'll try again.")
    try:
        import cv2
    except ImportError:
        return ("This PC doesn't have OpenCV — the QR-reading "
                "library VYUK uses — installed, so I can't "
                "decode QR codes here.")
    try:
        img = cv2.imread(str(path))
    except Exception as e:  # noqa: BLE001
        return f"I couldn't open {path.name} as an image: {e}"
    if img is None:
        return (f"{path.name} isn't an image I could open, so "
                "there's no QR code to read in it.")
    found: list[str] = []
    try:
        detector = cv2.QRCodeDetector()
        try:
            ok, texts, _points, _ = detector.detectAndDecodeMulti(img)
            if ok:
                found = [t for t in texts if t]
        except Exception:  # noqa: BLE001 — fall back to single decode
            found = []
        if not found:
            text, _points, _ = detector.detectAndDecode(img)
            if text:
                found = [text]
    except Exception as e:  # noqa: BLE001
        return f"The QR reader hit a problem with that image: {e}"
    if not found:
        return (f"I looked through {path.name} but couldn't read "
                "a QR code in it — it may be too small, blurry or "
                "partly hidden.")
    lines = [f"The QR code in {path.name} says:"]
    for text in found[:5]:
        lines.append(f"• {text}")
    return "\n".join(lines)
