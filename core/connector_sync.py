"""Shared connector sync engine (GAPS-7, BLOCKS #6).

The Gmail/Calendar connectors each hand-rolled a single-page fetch:
one list call, maxResults capped at 25, no retry, no pagination and
no memory of the last sync — so a sync silently returned a partial
set and never recovered from a transient failure. This module is the
one place that knows how to page, retry and remember:

- paginate(): follow pageToken/nextPageToken until exhausted (or the
  caller's limit), with a page cap and a repeated-token guard so a
  misbehaving provider can never spin forever;
- call_with_retry(): retry transient failures with exponential
  backoff — 429 / 5xx / timeouts / connection errors only. A 401 /
  403 auth or scope refusal is never retried: retrying a refusal
  the provider will repeat is pointless, and the tree's failure
  taxonomy already names those states;
- SyncState: a small JSON store (one file, atomic tmp+replace
  write, re-read verified before the caller claims the save —
  the A1-store pattern) holding one watermark per source, so a
  re-sync fetches only what's new.

Provider-agnostic: callers pass a fetch_page(page_token) callable
returning (items, next_page_token). No provider, no network call of
its own — the engine only drives callables the connectors hand it.
Nothing here sends, deletes or creates anything outside VYUK: the
only write is the engine's own state file under data/.
"""

import json
import os
import time
from pathlib import Path

DEFAULT_PAGE_SIZE = 100
MAX_PAGES = 50  # safety bound: at most 5,000 items per sync
MAX_ATTEMPTS = 3
BASE_DELAY_SECONDS = 0.5
MAX_LIST_ITEMS = 250  # the new per-call cap for the mail/calendar listings

_STATE_FILE_NAME = "connector_sync_state.json"
_STATE_VERSION = 1

_TRANSIENT_STATUSES = (429, 500, 502, 503, 504)
_NON_TRANSIENT_MARKERS = (
    "401", "403", "404", "insufficient", "invalid_grant",
    "invalid credentials", "not found", "bad request",
    "invalid argument",
)
_TRANSIENT_MARKERS = (
    "429", "500", "502", "503", "504",
    "ratelimitexceeded", "rate limit exceeded",
    "backenderror", "backend error",
    "timeout", "timed out",
    "temporarily unavailable", "service unavailable",
    "connection reset", "connection aborted", "eof occurred",
    "internal error", "try again",
)


def _short(exc) -> str:
    """One-line error text, capped — the same shape the Google
    connectors' _short gives, without importing them (this module
    stays provider-agnostic)."""
    text = " ".join(str(exc).split())
    return text[:160] + ("…" if len(text) > 160 else "")


def is_transient_error(exc: Exception) -> bool:
    """True when a failure is worth retrying: a rate limit, a 5xx,
    a timeout or a dropped connection. A rate-limit refusal that
    arrives as a 403 with a rateLimitExceeded reason IS transient;
    any other 401/403/404 (auth, scope, missing) is NOT — the
    provider will refuse the retry exactly the same way. An error
    carrying no recognisable marker is not retried either: an
    unknown failure retried blindly can repeat a side effect, and
    for a read the honest answer is the failure, not a guess."""
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None)
    if status is not None:
        try:
            status = int(status)
        except (TypeError, ValueError):  # noqa: BLE001
            status = None
    if status is not None:
        return status in _TRANSIENT_STATUSES
    text = str(exc).lower()
    if "ratelimitexceeded" in text or "user rate limit" in text:
        return True
    if any(marker in text for marker in _NON_TRANSIENT_MARKERS):
        return False
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def call_with_retry(fn, attempts: int = MAX_ATTEMPTS,
                    base_delay: float = BASE_DELAY_SECONDS,
                    sleep=time.sleep, is_transient=None):
    """Calls fn() until it succeeds or the attempts run out.
    Returns (value, attempts_used). Raises the last error when
    every attempt failed. Only transient errors are retried, with
    exponential backoff (base_delay, 2x, 4x, ...); a non-transient
    error raises on the first attempt, unretried. ``sleep`` is
    injectable so tests can record the delays without waiting."""
    check = is_transient or is_transient_error
    attempts = max(1, int(attempts))
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return fn(), attempt
        except Exception as exc:  # noqa: BLE001
            last = exc
            if not check(exc) or attempt == attempts:
                raise
            sleep(base_delay * (2 ** (attempt - 1)))
    raise last  # pragma: no cover — the loop always raises or returns


def paginate(fetch_page, page_size: int = DEFAULT_PAGE_SIZE,
             limit: int | None = None, max_pages: int = MAX_PAGES,
             attempts: int = MAX_ATTEMPTS,
             base_delay: float = BASE_DELAY_SECONDS,
             sleep=time.sleep) -> dict:
    """Drives fetch_page(page_token) until the provider is exhausted
    or the caller's limit is met. fetch_page returns
    (items, next_page_token); a None token ends the walk. Returns a
    dict: items / complete / pages / retries / stopped / error.

    complete is False exactly when the walk stopped early:
    - "page_limit": max_pages reached (a runaway guard, not a
      success — the caller must say the set is partial);
    - "token_repeat": the provider returned the same next-page
      token twice (a provider loop — stopping is the only honest
      move);
    - "error": a page failed after all retries (the items fetched
      so far are returned WITH complete False, never silently
      presented as the whole set).
    Reaching the caller's limit IS complete: the caller asked for
    that many and got them."""
    items: list = []
    token = None
    seen_tokens: set = set()
    pages = 0
    retries = 0
    stopped = ""
    error = ""
    limit = None if limit is None else max(0, int(limit))
    while True:
        try:
            (page_items, next_token), used = call_with_retry(
                lambda: fetch_page(token), attempts=attempts,
                base_delay=base_delay, sleep=sleep)
        except Exception as exc:  # noqa: BLE001
            stopped = "error"
            error = _short(exc)
            break
        retries += used - 1
        items.extend(page_items or [])
        pages += 1
        if limit is not None and len(items) >= limit:
            items = items[:limit]
            break
        if not next_token:
            break
        if next_token in seen_tokens:
            stopped = "token_repeat"
            break
        seen_tokens.add(next_token)
        token = next_token
        if pages >= max(1, int(max_pages)):
            stopped = "page_limit"
            break
    return {
        "items": items,
        "complete": stopped == "",
        "pages": pages,
        "retries": retries,
        "stopped": stopped,
        "error": error,
    }


def describe_partial(result: dict) -> str:
    """The honest trailer fragment for a partial sync: what stopped
    it, in one clause — or "" when the sync was complete. Shared by
    the Gmail and Calendar connectors so a partial set is disclosed
    the same way everywhere, never silently presented as whole."""
    if result.get("complete"):
        return ""
    stopped = result.get("stopped") or "error"
    if stopped == "page_limit":
        return " — stopped after %d pages" % result.get("pages", 0)
    if stopped == "token_repeat":
        return " — the provider kept returning the same page"
    if result.get("error"):
        return " — the next page failed (%s)" % result["error"]
    return " — the next page failed"


def sync_state_path() -> Path:
    """Where the sync watermarks live: the project data dir, next
    to the Google token (see core/config.py DATA_DIR). A function,
    not a constant, so tests can patch it to a tmp path — the
    engine never writes the real state file during a test run."""
    from .config import DATA_DIR

    return DATA_DIR / _STATE_FILE_NAME


class SyncState:
    """One watermark per sync source, in a single JSON file.

    State shape: {"version": 1, "sources": {key: {...}}} — the
    per-source dict is the caller's own shape (Gmail keeps a
    since_date + seen_ids, Calendar keeps a last_sync + known
    map). A missing or corrupt file reads as empty state, never
    raises: a lost watermark means the next sync re-fetches
    everything and says so, it never blocks the sync. A save is
    atomic (tmp file + os.replace) and re-read verified — put()
    returns (True, "") only when the file on disk, read back,
    holds exactly the value written (the A1-store pattern the
    audit counts as already-verified-in-handler)."""

    def __init__(self, path=None):
        # Coerce: sync_state_path() is documented as patchable by
        # tests, and a patched version naturally returns a str —
        # a str path has no .parent/.read_text, so an uncoerced
        # str silently broke every save (load swallows the
        # AttributeError and reads as empty). Path(...) accepts
        # both, so the engine works either way.
        self.path = Path(path if path is not None
                         else sync_state_path())

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — missing/corrupt: empty
            return {"version": _STATE_VERSION, "sources": {}}
        if not isinstance(data, dict):
            return {"version": _STATE_VERSION, "sources": {}}
        sources = data.get("sources")
        if not isinstance(sources, dict):
            sources = {}
        return {"version": _STATE_VERSION, "sources": sources}

    def get(self, key: str) -> dict:
        entry = self.load().get("sources", {}).get(key)
        return dict(entry) if isinstance(entry, dict) else {}

    def put(self, key: str, value: dict) -> tuple[bool, str]:
        data = self.load()
        data["sources"][key] = dict(value)
        return self._save(data, key, dict(value))

    def reset(self, key: str) -> tuple[bool, str]:
        data = self.load()
        data["sources"].pop(key, None)
        return self._save(data, key, None)

    def _save(self, data: dict, key: str,
              expect) -> tuple[bool, str]:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1, sort_keys=True),
                           encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception as exc:  # noqa: BLE001
            return False, _short(exc)
        # A1-store: re-read the file and confirm the value landed.
        try:
            saved = self.load().get("sources", {}).get(key)
        except Exception as exc:  # noqa: BLE001
            return False, _short(exc)
        if saved != expect:
            return False, "re-read mismatch after save"
        return True, ""
