"""NOTE (click-anywhere Batch 1): this module moved from core/browser.py
to core/browser/browser.py — the new core/browser/ package shadows the old
module path. core/browser/__init__.py re-exports this module's public
names lazily (PEP 562) so `from core import browser` keeps working.

Full browser control for VYUK — she drives a REAL browser, structurally.

Desktop clicking is approximate (OCR guesses, pixel coordinates). The
web is not: every page is a DOM, a tree of named elements, so browser
actions here are by MEANING — "click the Sign in button", "fill the
field labelled Email" — never by guessed pixels.

Architecture (the constraint that shapes this whole module):
Playwright's sync API is thread-affine — every call must happen on the
ONE thread that created it. So all browser state lives on a single
dedicated daemon worker thread (BrowserWorker). Every public tool
function does no browser work itself: it packages the work as a
callable, submits it to the worker's queue, and waits on an Event
(with a timeout) for the result string. The worker starts LAZILY on
first browser use — importing this module or registering the tools
launches nothing.

Connection mode — exactly one, since v5.70 (owner order:
real Chrome only; the own-browser and dedicated-profile
launches were deleted, not deprecated):
CDP attach to the user's real Chrome (their logins, cookies, open
tabs) at http://localhost:9222 — EXPLICIT OPT-IN ONLY (v5.49,
programme item 33): the config flag
browser_allow_real_chrome_attach defaults OFF, and with it off
the real-profile attach path is unreachable — action 'launch'
refuses before any connection is attempted. With it ON, Chrome
must have been started with --remote-debugging-port=<port>;
if the port isn't there, launch FAILS with instructions; it
never silently falls back, and there is no other browser to
fall back to. The navigation/read/click/type actions auto-attach
under the same opt-in when no session is active; with the
opt-in off they refuse with guidance instead of starting
anything.
Chrome 136 (GAPS-3, 2026-11-12): from Chrome 136 the debug-port
flag is ignored on the default profile, so a real-profile attach
only works when the port is actually open — a pre-136 flag
launch, or the chrome://inspect remote-debugging toggle on newer
Chrome. The enable-flow launch therefore opens Chrome on VYUK's
separate debug profile (--user-data-dir=<VYUK debug profile>,
core/desktop/browser_cdp._debug_profile_dir, diagnosed before
any launch): the same real Chrome binary, a separate profile —
its logins and tabs are NOT the everyday profile's, and every
message says so. The v5.70 own-browser launch stays deleted; only
the launch FLAG changed, because no flag-only launch can work on
Chrome 136+.

Trust rules (also stated in the tool descriptions):
- VYUK NEVER types into a password field. Ever. Those are the user's.
  The rule is pack-wide: browser_type refuses a password field,
  browser_press refuses text-like keystrokes while one is focused.
- Clicks that smell like money or destruction are gated on the
  RESOLVED element (its real text / aria-label / value / title /
  href), not on the string the model asked for — selector targets
  get the same scrutiny. When the GUI has registered a human
  confirmation handler (core/confirm_gate.py), the HUMAN approves
  or denies the click in a popup; the model's confirm=true alone is
  not enough. With no handler, confirm=true remains the fallback.
- CAPTCHAs / bot walls will block her; the user takes those clicks.
- Point clicks (browser_click by='point', v5.7) are the one
  deliberate exception to "never pixels": the point is first
  resolved to the element living there and clicked by meaning,
  through the same gate; a raw coordinate click happens ONLY when
  nothing interactive lives at the point (drawn/canvas content),
  and the result labels it as a raw coordinate click.

Same rules as the other control modules: plain functions returning
human-readable strings; Playwright is imported lazily inside the
worker and guarded, so this module imports cleanly anywhere —
including machines where Playwright isn't installed yet.

Wiring: the parent build calls register_all(registry) from core/tools_handlers_pack_hands.py (_register_browser_tools) — this module never touches the registry itself.
"""

import datetime
import json
import logging
import os
import queue
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from .. import injection

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

from ..desktop.browser_cdp import (_CDP_URL, _DEFAULT_CDP_PORT, _config_value, _truthy, _real_chrome_attach_allowed, _cdp_port, _cdp_url_for, _cdp_fetch_json, _cdp_handshake, _PROFILE_HINT_KEYS, _norm_profile, _REAL_ATTACH_CONFIG, _DEBUG_PORT_CONFIG, _REAL_ATTACH_REFUSAL)  # refactor P1 step 4 (browser_cdp concern, moved verbatim)
# v5.49 (programme item 33): the debug port has a defined default
# (9222, the historical _CDP_URL) but is config-readable via
# browser_debug_port — see _cdp_port().

from ..desktop.browser_session import (log, _JOB_TIMEOUT_S, _LAUNCH_JOB_TIMEOUT_S, _NAV_JOB_TIMEOUT_S, _GOTO_TIMEOUT_MS, _ELEMENT_TIMEOUT_MS, _SETTLE_TIMEOUT_MS, _ACTIVITY_LOG_PATH, _DATA_DIR, _is_windows, _short, _looks_like_target_closed, BrowserWorker, _log_browser_event, _worker, _worker_lock, _nav_timeout_streak, _submit, _abandon_worker, _state, _reset_state, _connected, _start_pw, _BROWSERS_DIR, _active_page, _page_label, _need_connection, _BOOTSTRAP_NOTE, _OPEN_URL_BOUNDARY, _guided_no_session, _bootstrap_job, _ensure_session, _with_bootstrap, _impl_attach_cdp, _adopt_browser, _impl_close, _impl_status, _settle)  # refactor P1 step 4 (browser_session concern, moved verbatim)
from ..desktop.browser_enable import (browser_enable_status, enable_browser_control, launch_chrome_for_browser_control, restart_chrome_for_browser_control, disable_browser_control)  # Batch 3 (2026-11-05): one-click enable flow — same re-export surface as the other browser concerns

# Job budgets (v5.53, F5 — field forensics Failure A). Every
# browser job runs on the single worker lane, and ONE budget
# for everything is what killed sessions in the field: a cold
# Chrome launch or a heavy page is SLOW, not wedged, yet the
# old code abandoned the worker (and reset the connection)
# for either. The classes are what make the longer budgets
# safe: the abandon recovery now only ever fires for the
# generic class, whose jobs are all quick in honest operation.
                               # the persistent-context launch on a
                               # loaded machine legitimately takes
                               # tens of seconds — it no longer
                               # eats the budget of the action
                               # queued behind it
                               # new tab): the job itself caps the
                               # navigation at _GOTO_TIMEOUT_MS and
                               # settles for _SETTLE_TIMEOUT_MS, so
                               # this is headroom plus queue lag —
                               # and expiring it costs a soft
                               # result, never the session (_submit)

# Where connection-event lines land (the codebase's
# activity.log convention — cf. core/confirm_gate's direct
# append). A module global so suites can point it at a
# scratch file.

# Clicks matching this pattern move money or destroy things:
# human-confirm-gated. The pattern is matched against the RESOLVED
# element's real text/attributes (see _impl_click), never just the
# requested target string. Vocabulary widened in v4.4 after an audit
# probed the misses: "Place your order" (Amazon's real checkout
# button), bare "Delete", "Buy", "Order now", "Erase…", "Remove…",
# bare "Confirm" all walked through the old pattern.
from ..desktop.browser_gates import (_DANGEROUS_CLICK_RE, _MATCH_SCAN_CAP, _DESTRUCTIVE_SUBMIT_RE, _FORM_INTENT_JS, _FOCUSED_FORM_INTENT_JS, _is_submit_key, _dangerous_click_verdict, _dangerous_submit_verdict, _uninspectable_submit_verdict, _raw_point_verdict, _classify_submit_intent, _submit_gate, _loc_visible, _narrow_visible, _LOGIN_WALL_JS, _login_wall_result)  # refactor P1 step 4 (browser_gates concern, moved verbatim)

# v5.70: the own-browser profile (data/browser_profile) and the
# dedicated debug profile (data/chrome_debug_profile) are gone
# with their launches. What remains under data/ is only the
# Playwright driver browsers root (used by browser_engine.
# start_playwright, via _start_pw, for the CDP connection itself,
# never to launch a browser).

# v5.49 (programme item 33): names of the new config seams. Neither
# field exists in core/config.py yet (another builder owns config);
# both are read dynamically via _config_value with safe defaults,
# so adding the fields later needs no change here. Coordinator
# note: add `browser_allow_real_chrome_attach = False` and
# `browser_debug_port = 9222` to AppConfig/Settings when convenient.













# --- CDP handshake / identity (v5.49, programme item 33) --------
# Two monkeypatchable seams: _cdp_fetch_json is the ONLY place
# that touches HTTP, and _cdp_handshake composes it. Suites fake
# _cdp_fetch_json; production talks to the loopback endpoint
# through a no-proxy opener (a system proxy must never see, or
# reroute, a loopback DevTools probe).













# ---------------------------------------------------------------------------
# Browser worker — the ONLY thread allowed to touch Playwright
# ---------------------------------------------------------------------------





from ..desktop.browser_nav import (_looks_like_timeout, _goto_target, _scam_hash_warning, _impl_goto, _impl_history, _impl_new_tab, _impl_close_tab, _impl_switch_tab, _impl_list_tabs, _impl_scroll, _impl_wait, _impl_screenshot)  # refactor P1 step 4 (browser_nav concern, moved verbatim)





# Consecutive navigation-job timeouts (v5.53, F5): the second
# one in a row declares the session suspect IN THE RESULT —
# still without a silent reset. Any completed nav job clears
# the streak.






# ---------------------------------------------------------------------------
# Browser state — touched ONLY on the worker thread
# ---------------------------------------------------------------------------









# --- Playwright driver browsers root (v5.35) ----------------------
# The CDP connection still runs through Playwright's driver, so
# the driver looks for its browsers under the app's data root
# (data/ms-playwright) before the per-user cache. v5.70 deleted
# the bundled-Chromium fetch itself along with the own-browser
# launch it served: nothing here downloads a browser any more.








# ---------------------------------------------------------------------------
# v5.52 (item 5 + extension): the two-browser seam, and auto-bootstrap.
#
# The field failure: the model opened youtube.com with open_url
# (a fire-and-forget OS open into the SYSTEM browser — a window
# VYUK cannot see or drive), then called a browser_control search
# against a controllable session that did not exist, failed, and
# narrated the confusion at the user. Two fixes:
#
# 1. ONE guided error shape for every session-requiring action
#    (_guided_no_session): it names the seam plainly — open_url's
#    window is not a session — and the recovery (start a session
#    first, then goto).
# 2. Auto-bootstrap: the public tool functions below call
#    _ensure_session() BEFORE dispatching a session-requiring
#    action. With no session active, the tool layer itself tries
#    the ONE remaining path — the opt-in real-Chrome attach
#    (_impl_attach_cdp) — ONCE, then performs the requested action
#    in the same call and prefixes the result with _BOOTSTRAP_NOTE.
#    With the opt-in off, or Chrome not started with the debug
#    flag, the bootstrap fails and the guided error fires, naming
#    the failure; nothing else is started. When a session IS active
#    the bootstrap job is a no-op probe and behaviour is
#    byte-identical to before.
# ---------------------------------------------------------------------------












# ---------------------------------------------------------------------------
# Connection implementations (worker thread)
# ---------------------------------------------------------------------------







# ---------------------------------------------------------------------------
# Navigation / tab implementations (worker thread)
# ---------------------------------------------------------------------------



























# ---------------------------------------------------------------------------
# Reading implementations (worker thread)
# ---------------------------------------------------------------------------

# Only VISIBLE controls are listed (v5.10): the same visibility
# predicate the elements collector uses (non-zero rect, not
# display:none / visibility:hidden). Before this, a button inside a
# CLOSED <dialog> — unrendered, unclickable — was offered in field
# listings and in the click not-found hint as if it could be acted
# on. `total` therefore counts visible controls.
from ..desktop.browser_read import (_FIELDS_JS, _LINKS_JS, _EXTRACT_LIMITS_NOTE, _page_text_usage, _page_text, _fields_payload, _field_lines, _impl_read_text, _impl_read_find, _impl_read_links, _impl_read_fields, _impl_read_elements)  # refactor P1 step 4 (browser_read concern, moved verbatim)



# What _page_text can and cannot see (audit B3). Appended to read
# results so a partial extract never presents as the whole page:
# the extraction favours the main content region when one exists
# (innerText never crosses into iframes or shadow DOM), and lazy
# content that only loads on scroll may not have rendered yet.










# v5.53 (F2): the playback probe behind play_music's
# continuity recheck — is the page's media element alive,
# user-paused, finished, or showing the player-error card
# YouTube serves when it cuts a stream mid-playback
# ("Something went wrong", the 2026-10-23 field failure: 42 s
# of real audio, then the error card, and the dead stream
# stood as the outcome). Read-only; never raises.
from ..desktop.browser_playback import (_PLAYBACK_PROBE_JS, _impl_playback_state, page_playback_state, _PLAYBACK_TRIANGULATION_JS, _impl_playback_votes, page_playback_votes, _impl_playback_death, log_playback_death)  # refactor P1 step 4 (browser_playback concern, moved verbatim)






# v5.59 (Wave 4, BR-5): the triangulated playback probe. v5.53's
# F2 probe read ONE signal — the page's media element — and
# play_music trusted it (or, when it could not speak, the audio
# session alone). BR-5 keeps the media element as the PRIMARY
# vote, adds navigator.mediaSession as corroboration ONLY
# (a page that never sets MediaSession metadata reports
# playbackState "none", which is NO vote, not a "paused"
# vote), and leaves the audio session as the THIRD vote, cast
# by the caller (core/music_player), which alone can see it.
# "Playing" is claimed only on 2-of-3 votes — that rule lives
# in core/music_player.triangulate_playback; this probe only
# supplies the two in-page votes, honestly, including their
# unknowns. Read-only; never raises. The probe picks the
# first non-paused, non-ended, error-free element as the
# playing candidate, else the first media element at all, so
# a page with a playing track plus a paused advert does not
# read as paused.


















# ---------------------------------------------------------------------------
# Element snapshots — SEE the page, then point at an element (v5.6)
#
# Text-based clicking makes the brain guess which match is meant and
# whether it is even visible. The snapshot turns that around: a JS
# collector walks every frame, keeps only VISIBLE interactive
# elements, and numbers them (e1, e2, …). Shown elements are tagged
# in the live DOM with a data-nova-ref attribute, so a later
# browser_click by='ref' resolves EXACTLY the element that was
# listed — no re-guessing, no first-match roulette. Refs belong to
# the LATEST snapshot (a listing or an ambiguous-click refusal) and
# are only honoured while the page address still matches it.
#
# v5.7: the walk is DEEP — it descends into every OPEN shadow root
# (web components) too, in the collector, the tagger and the ref
# resolver alike; shadow-resident rows carry an "(in shadow:
# <host>)" suffix, and their refs resolve through the in-page deep
# walk, since no locator addresses an attribute inside a shadow
# tree. CLOSED shadow roots stay unreachable — that is the web
# platform's design, not a VYUK limitation, and snapshot counts
# never pretend to include them.
# ---------------------------------------------------------------------------

from ..desktop.browser_elements import (_ELEMENTS_CAP, _ELEMENT_JS_HELPERS, _COLLECT_ELEMENTS_JS, _TAG_REFS_JS, _SINGLE_ELEMENT_JS, _element_row, _frame_marker, _KIND_FILTERS, _take_snapshot, _snapshot_from_candidates, _base_locator, _scoped_locator, _element_identity, _probe_int, _PAGE_PROBE_JS, _page_probe, _change_sentence)  # refactor P1 step 4 (browser_elements concern, moved verbatim)

# Shared JS: classify an element's display role, and describe it.
# Composed into both the bulk collector and the single-element
# prober so the two can never drift apart.

# Bulk collector: runs once per frame. Clears stale ref/token tags,
# keeps only visible interactive elements (filtering happens HERE,
# in the page, so the counts VYUK reports are true), stamps each
# kept element with a per-snapshot token so the refs can be tagged
# precisely afterwards, and returns plain data — tagging and ref
# numbering happen server-side, after any query filter and cap.

# Second snapshot phase: tag exactly the shown elements (found via
# their tokens, immune to DOM reordering) with their public refs.
# The lookup is a deep walk: tokens stamped inside shadow trees
# are invisible to a plain document.querySelector.

# Single-element prober for ambiguous-click candidates: tags the
# element with its ref immediately and returns its description.

# Describe-only prober (v5.7): the shared classify/describe with
# NO tagging — used on element handles VYUK already holds (a point
# probe's result), where there is nothing to tag.
from ..desktop.browser_click import (_CLICK_VERBS, _CLICK_NOUNS, _act_on_element, _impl_click, _DESCRIBE_ELEMENT_JS, _FIND_REF_JS, _resolve_ref_handle, _impl_click_ref, _viewport_size, _POINT_AT_JS, _probe_point, _handle_tag, _CLIMB_INTERACTIVE_JS, _climb_handle, _RAW_POINT_VERBS, _RAW_POINT_NOUNS, _raw_point_click, _impl_click_point)  # refactor P1 step 4 (browser_click concern, moved verbatim)

# Ref resolver for shadow-resident elements (v5.7): a locator
# cannot address an attribute inside a shadow tree, so the tagged
# element is found with the in-page deep walk and returned as a
# handle. Light-DOM refs still resolve through locators.

# Point probe (v5.7): what actually lives at viewport point (x, y)?
# elementFromPoint RETARGETS shadow content to its host, so
# descend into open shadow roots — shadow roots support
# elementFromPoint in the same viewport coordinates — and prefer
# the inner element that is really painted there.

# Interactive climb (v5.7): from the element at a point, climb to
# the nearest interactive ancestor (the brief selector, verbatim).
# closest() stops at shadow boundaries, so when a tree has no
# interactive ancestor the climb continues from its HOST in the
# outer tree (a <fancy-button> whose inner span was hit).

# Before/after probe for click reporting: URL/title are read
# server-side; this counts visible overlays (dialogs/menus — how a
# hover's effect is noticed) and visible interactive elements.
# The overlay selector is ONE querySelectorAll selector list, so an
# element matching two entries is still returned (and counted)
# once. dialog[open] is there for native <dialog> elements: their
# dialog role is implicit, so an attribute-only selector missed a
# showModal() dialog entirely and the receipt claimed the page was
# unchanged when a dialog had in fact opened (v5.10).














# ---------------------------------------------------------------------------
# Element action implementations (worker thread)
# ---------------------------------------------------------------------------









# Audit fix Batch 1 (WEB-01 / WEB-02) — the submit gate.
#
# Payment / destructive page signals, named explicitly (the
# DECIDED refuse fork for uninspectable forms): money out
# (pay, purchase, checkout, place order, order now, buy, send
# money, donate, submit payment/order) or destruction
# (delete, remove, erase, destroy, unsubscribe, cancel an
# account/subscription/order). This set is deliberately
# narrower than _DANGEROUS_CLICK_RE (which also flags bare
# "send"/"confirm"): the refuse fork fires only on these
# signals, everything else uninspectable goes confirm-first.

# Form-intent probes (same style as _FOCUSED_PASSWORD_JS): the
# owning form's action + submit-button text, which is what the
# dangerous-action pattern is judged against — never the field
# label alone, and never the typed text (typing "send money"
# into a search box is not a payment).



































# ---------------------------------------------------------------------------
# Point clicks (v5.7) — browser_click by='point', target 'x,y'
#
# The point is a LAST resort, never a first guess: VYUK first
# identifies what actually lives at the point (elementFromPoint,
# shadow-descended, one iframe level deep). When an interactive
# element is there, it goes through the standard element pipeline
# — identity, trust gate, act, report — and the result reads as a
# point-led element click. Only when NOTHING interactive lives at
# the point (drawn/canvas content) does the raw mouse go down, and
# the result labels it a raw coordinate click, plainly.
# ---------------------------------------------------------------------------
















# ---------------------------------------------------------------------------
# Plain-YouTube direct-results rung (conversion pass, Phase 0 field fix).
#
# FIELD FAILURE 2026-10-25: a plain-YouTube video search ("how to
# play games") failed because the search field could not be located
# in her browser. The plain path has no YouTube-specific search
# step: the brain gotos youtube.com (_impl_goto), then finds the
# box by element-finding (_impl_click / _impl_type through
# _scoped_locator), and that finding is the fragile step — on a
# live, possibly consent-walled / logged-out / degraded YouTube DOM
# the box counts zero matches or no visible match, and the search
# dies there (the count==0 / none-visible branches). The consent
# dismissal that exists in this codebase lives ONLY in the
# YouTube Music rung (core/music_player._ytm_dismiss_consent) —
# browser.py itself has no consent handling at all — so the plain
# path never even gets a dialog out of the way.
#
# THE RUNG: when a type into a YouTube search field fails to find
# the field (or fails to fill it), _impl_type falls back to the
# direct results URL — the same shape the YTM rung already proves
# (music.youtube.com/search?q=...): goto the results page, then
# dismiss a consent dialog best-effort, Reject-first, exactly like
# _ytm_dismiss_consent. No search-box click or fill is attempted
# by the rung itself, and no new tool is declared: the rung runs
# inside the existing browser_type call, so the fitted declaration
# payload does not grow. On a direct-URL failure the rung says so
# plainly and does NOT fall back to clicking the box — a click on
# this page is the step that already failed.
# What this rung does NOT do: it lands on the results page, it
# never clicks a result (picking a result is the brain's next
# step, and playback claims belong to play_music); it fakes no
# login and bypasses no wall (a login wall still stops _impl_type
# before this rung is reached); and it touches neither the
# stealth pilot (still OFF) nor the patched-engine path
# (removed GAPS-1) nor yt-dlp.
# A click-only failure cannot be recovered here: browser_click
# carries no query, so there is nothing to build a results URL
# from — recovery lands at the type step, which carries the
# query. If the user's later answers show the failure was never
# a search-box miss (e.g. the page showed something else
# entirely), this rung answers the wrong failure and must be
# revisited.
# ---------------------------------------------------------------------------

from ..desktop.browser_interact import (_YT_SEARCH_HOSTS, _YT_CONSENT_TEXTS, _youtube_results_url, _is_youtube_page, _dismiss_youtube_consent, _youtube_results_search, _impl_type, _select_options, _impl_select, _FOCUSED_PASSWORD_JS, _press_delivers_text, _impl_press)  # refactor P1 step 4 (browser_interact concern, moved verbatim)
# SAME texts, SAME order as music_player._YTM_CONSENT_TEXTS:
# Reject-first (least tracking), Accept only when no Reject
# button is offered. Kept as a local copy, not an import, so
# browser.py keeps its lazy-import shape — the suite pins the
# two constants equal.
















# Is the page's focused element a password input? One evaluate call,
# asked only when a press could deliver text (audit B9).


# v5.52 (item 4 extension, field message 2026-10-23 23:09):
# the LOGIN-WALL terminal state. A page presenting a visible
# password field is a wall she cannot climb — she has no
# credentials to type and must never try (detection only,
# the _impl_type password discipline). Acting flows STOP and
# return ONE sentence naming the unlock, as a named terminal
# result ("needs-user-login: …"), instead of retrying,
# flailing at SPA controls, or deliberating at the user.








# ---------------------------------------------------------------------------
# Public tool functions — marshal everything onto the worker thread
# ---------------------------------------------------------------------------

# INTENTIONAL (skills split B15): browser.py's own defined code (the
# dispatch layer + register_all) moved verbatim to core/browser_dispatch.py;
# the facade keeps the desktop re-export lists above and re-exports the
# defined names here so every importer and dir() view is unchanged.
from ..browser_dispatch import (
    _SESSION_CONTROL_ACTIONS,
    browser_control,
    _browser_control_dispatch,
    browser_read,
    _browser_read_dispatch,
    browser_click,
    browser_type,
    browser_select,
    browser_press,
    register_all,
)
