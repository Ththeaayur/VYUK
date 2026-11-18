"""
Senses for VYUK (v5.20, NOSE) — pure sensing helpers.

Structured, dict-returning inspectors that the NOSE tools
(core/skills_nose.py) narrate and the Senses dashboard renders:
Wi-Fi surroundings, network quality, frame brightness, and the
three "sniffs" (file / link / text). Everything here is a LOOK,
not a touch: nothing is changed, downloaded for keeps or executed.

Contract, shared by every public function: NEVER raises. A
failure becomes an honest field in the returned dict ("available":
False, "ok": False, a "note"/"verdict" that says what happened),
because a sense that crashes is worse than a sense that says it
couldn't see.

Stdlib only, plus PIL inside frame_brightness (already a hard
dependency of the app) imported lazily so this module loads
anywhere. The link sniffer dials through the system proxy like
core.voice.local_assets' downloads do — on campus/hostel networks the
direct route can be dead while the browser (and the proxy) work.
"""


# INTENTIONAL (senses split B4): the old module's stdlib imports,
# re-exported so the facade namespace matches the old one.
import hashlib
import io
import json
import os
import re
import shutil
import socket
import ssl
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# INTENTIONAL (senses split B4): every name the old module defined
# is re-exported from the impl module that now owns it, so existing
# module-attribute importers (skills_nose, skills_web_research,
# skills_senses_checks, skills_web_watch, skills_web6_watch, watchers)
# work unchanged and with identical function objects.
from .senses_wifi import (
    _build_sniff_opener,
    _parse_netsh_networks,
    _parse_nmcli_networks,
    _usable_proxy,
    _wifi_command_output,
    wifi_scan,
)
from .senses_quality import (
    _QUALITY_HOSTS,
    _QUALITY_SAMPLES,
    _quality_target,
    frame_brightness,
    network_quality,
)
from .senses_file import (
    _EXT_TYPES,
    _HASH_CAP,
    _ZIP_CONTAINER_EXTS,
    _detect_magic,
    _looks_like_text,
    _size_human,
    sniff_file,
)
from .senses_link import (
    _LINK_TIMEOUT,
    _MAX_REDIRECTS,
    _NoRedirect,
    _SHORTENER_HOSTS,
    _UA,
    _cert_expiry,
    _parse_cert_date,
    is_shortener,
    sniff_link,
)
from .senses_text import (
    _EMAIL_RE,
    _STOPWORDS,
    _URL_RE,
    _WORD_RE,
    _detect_language,
    sniff_text,
)
