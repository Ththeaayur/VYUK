"""Page-watch tools for the skills_web_watch pack (moved verbatim)."""

from .config import DATA_DIR
from . import skills_web_research as _web5
from datetime import datetime
import difflib
import json
import re
from . import senses
import urllib.error
import urllib.parse
import urllib.request
# INTENTIONAL: cross-part names live in the helpers module (bare-name rule, plan §2 P3); the facade re-export alone would not resolve them in this module's globals.
from .skills_web6_helpers import (_LINK_CAP, _PAGE_CAP, _PRICE_FILE, _READ_CAP, _SNAP_FILE, _TIMEOUT, _UA, _fetch, _headline_lines, _normalise_url, _parse_page, _visible_text)


# ---------------------------------------------------------------------------
# PRICE WATCH — a page's first price, against a target (small state file)
# ---------------------------------------------------------------------------

_PRICE_BEFORE = re.compile(
    r"(₹|\$|€|£|Rs\.?|INR|USD|EUR|GBP)\s?(\d[\d,]*(?:\.\d{1,2})?)")
_PRICE_AFTER = re.compile(
    r"(\d[\d,]*(?:\.\d{1,2})?)\s?(₹|\$|€|£|INR|USD|EUR|GBP)")


def _first_price(text: str):
    """The first plausible price in visible text:
    (amount, display) or None."""
    best = None
    m = _PRICE_BEFORE.search(text)
    if m:
        best = (m.start(), m.group(2), f"{m.group(1)}{m.group(2)}")
    m = _PRICE_AFTER.search(text)
    if m and (best is None or m.start() < best[0]):
        best = (m.start(), m.group(1), f"{m.group(1)}{m.group(2)}")
    if best is None:
        return None
    try:
        amount = float(best[1].replace(",", ""))
    except ValueError:
        return None
    return amount, best[2]


def _price_path():
    return DATA_DIR / _PRICE_FILE


def _load_prices() -> dict:
    try:
        data = json.loads(_price_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — missing/corrupt = no watches
        return {}


def _save_prices(watches: dict) -> bool:
    try:
        _price_path().write_text(json.dumps(watches, indent=2),
                                 encoding="utf-8")
        return True
    except OSError:
        return False


def _check_one_price(full: str, entry: dict | None) -> str:
    """Fetch one page and report its price against the target."""
    body, _resp, err = _fetch(full, _PAGE_CAP)
    if body is None:
        return f"Couldn't fetch {full} — {err}."
    text = _visible_text(body.decode("utf-8", errors="replace"))
    found = _first_price(text)
    if found is None:
        return (f"No price found on {full} — the page may show "
                "prices only after a login, or draw them with "
                "scripts I can't run.")
    amount, display = found
    if entry is not None:
        entry["last_price"] = amount
        entry["last_display"] = display
        entry["last_checked"] = datetime.now().isoformat(
            timespec="seconds")
    target = (entry or {}).get("target")
    if target:
        if amount <= target:
            return (f"{full} is at {display} — that's at or under "
                    f"your target of {target:,.2f}.")
        return (f"{full} is at {display} — still above your "
                f"target of {target:,.2f}.")
    return f"The first price on {full} is {display}."


def price_watch(action: str = "check", url: str = "",
                target_price: float = 0) -> str:
    """Watch a product page's price against an optional target."""
    act = (action or "check").strip().lower()
    if act not in ("add", "check", "list", "remove"):
        return (f"Unknown price_watch action: '{action}'. Use "
                "'add', 'check', 'list' or 'remove'.")
    watches = _load_prices()
    if act == "list":
        if not watches:
            return ("You're not watching any prices yet — use "
                    "action 'add' with a product page to start one.")
        lines = [f"Watching {len(watches)} price(s):"]
        for link, entry in watches.items():
            bits = [link]
            if entry.get("target"):
                bits.append(f"target {entry['target']:,.2f}")
            if entry.get("last_display"):
                bits.append(f"last seen {entry['last_display']}")
            lines.append("• " + " — ".join(bits))
        return "\n".join(lines)
    full, host = _normalise_url(url)
    if not host:
        return "Give me the product page's address for that action."
    if act == "remove":
        if full not in watches:
            return f"{full} wasn't being price-watched."
        del watches[full]
        if not _save_prices(watches):
            return ("I removed it on this side, but couldn't save "
                    "the watch list — it may come back next start.")
        return f"Stopped price-watching {full}."
    if act == "add":
        try:
            target = float(target_price) if target_price else None
        except (TypeError, ValueError):
            return f"'{target_price}' isn't a price I can use as a target."
        watches[full] = {
            "target": target,
            "added_at": datetime.now().isoformat(timespec="seconds"),
            "last_price": None, "last_display": "",
            "last_checked": "",
        }
        if not _save_prices(watches):
            return ("I couldn't save the watch list, so this "
                    "price watch won't stick.")
        return (f"Now price-watching {full}"
                + (f" — I'll call it a deal at or under "
                   f"{target:,.2f}." if target else
                   " — no target set, checks just report the price.")
                + " Ask me to check it any time.")
    # check
    if full in watches:
        line = _check_one_price(full, watches[full])
        _save_prices(watches)
        return line
    if url.strip():
        return _check_one_price(full, None)
    if not watches:
        return ("No price watches to check — use action 'add' "
                "with a product page first.")
    lines = []
    for link in list(watches)[:10]:
        lines.append(_check_one_price(link, watches[link]))
    _save_prices(watches)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# PAGE CHANGE SUMMARY — WHAT changed since the last snapshot
# ---------------------------------------------------------------------------


def _snap_path():
    return DATA_DIR / _SNAP_FILE


def _load_snaps() -> dict:
    try:
        data = json.loads(_snap_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — missing/corrupt = no snapshots
        return {}


def _save_snaps(snaps: dict) -> bool:
    try:
        _snap_path().write_text(json.dumps(snaps, indent=2),
                                encoding="utf-8")
        return True
    except OSError:
        return False


def page_change_summary(url: str) -> str:
    """Diff a page's headline lines against the saved snapshot."""
    full, host = _normalise_url(url)
    if not host:
        return "Give me a page address to summarise the changes on."
    body, _resp, err = _fetch(full, _PAGE_CAP)
    if body is None:
        return (f"Couldn't fetch {full} — {err}. (The saved "
                "snapshot, if any, is unchanged.)")
    title, lines = _headline_lines(
        body.decode("utf-8", errors="replace"))
    snaps = _load_snaps()
    previous = snaps.get(full)
    stamp = datetime.now().isoformat(timespec="seconds")
    snaps[full] = {"title": title, "lines": lines, "saved_at": stamp}
    if not _save_snaps(snaps):
        return ("I read the page but couldn't save the snapshot, "
                "so the comparison won't stick.")
    if previous is None:
        note = ""
        try:
            if full in _web5._load_watches():
                note = (" It's also on your page-watch list, which "
                        "notices THAT it changes.")
        except Exception:  # noqa: BLE001 — a garnish, never a blocker
            note = ""
        return (f"First snapshot of {full} saved — {len(lines)} "
                f"headline line(s) from '{title or host}'. Ask me "
                "again later and I'll tell you in words what "
                f"changed.{note}")
    old_lines = previous.get("lines") or []
    if old_lines == lines:
        return (f"Nothing on {full} has changed since "
                f"{previous.get('saved_at', 'the last check')} — "
                "same headlines, word for word.")
    matcher = difflib.SequenceMatcher(a=old_lines, b=lines)
    added: list[str] = []
    removed: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "insert"):
            added.extend(lines[j1:j2])
        if tag in ("replace", "delete"):
            removed.extend(old_lines[i1:i2])
    out = [f"Since {previous.get('saved_at', 'the last check')}, "
           f"{full} changed: {len(added)} new line(s), "
           f"{len(removed)} gone."]
    for line in added[:3]:
        out.append(f"• New: {line}")
    if len(added) > 3:
        out.append(f"…and {len(added) - 3} more new line(s).")
    for line in removed[:2]:
        out.append(f"• Gone: {line}")
    if len(removed) > 2:
        out.append(f"…and {len(removed) - 2} more removed line(s).")
    out.append("The new version is the snapshot now.")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# READ PAGE ALOUD — the article, stripped and ready to speak
# ---------------------------------------------------------------------------


def read_page_aloud(url: str, max_words: int = 0) -> str:
    """Fetch a page and return its main text in speakable form.

    max_words > 0 stops the reading at about that many words —
    whole paragraphs are kept until the budget runs out (a single
    opening paragraph longer than the budget is cut mid-way). The
    leftover is always counted honestly."""
    try:
        cap = int(max_words or 0)
    except (TypeError, ValueError):
        return f"'{max_words}' isn't a number of words."
    if cap < 0:
        return "max_words can't be negative — use 0 to read the whole page."
    full, host = _normalise_url(url)
    if not host:
        return "Give me the address of the page to read aloud."
    body, _resp, err = _fetch(full, _PAGE_CAP)
    if body is None:
        return f"Couldn't fetch {full} — {err}."
    page = _parse_page(body.decode("utf-8", errors="replace"))
    paragraphs = [text for kind, text in page.blocks
                  if kind in ("p", "blockquote") and len(text) >= 40]
    if not paragraphs:
        paragraphs = [text for _kind, text in page.blocks
                      if len(text) >= 40]
    if not paragraphs:
        return (f"{full} answered, but I couldn't find a readable "
                "article on it — the page may be mostly menus, "
                "scripts or a login wall.")
    words = len(" ".join(paragraphs).split())
    title = page.title or host
    head = f"{title} — about {words:,} words."
    if cap:
        kept: list[str] = []
        used = 0
        for para in paragraphs:
            count = len(para.split())
            if kept and used + count > cap:
                break
            if not kept and count > cap:
                kept.append(" ".join(para.split()[:cap]))
                used = cap
                break
            kept.append(para)
            used += count
        trimmed = kept != paragraphs
        article = "\n\n".join(kept)
        if len(article) <= _READ_CAP:
            if trimmed:
                left = words - len(article.split())
                return (head + "\n\n" + article
                        + f"\n\n(Stopped at your {cap:,}-word limit — "
                          f"about {left:,} more words of the article "
                          "weren't read.)")
            return head + "\n\n" + article
    else:
        article = "\n\n".join(paragraphs)
    if len(article) <= _READ_CAP:
        return head + "\n\n" + article
    cut = article.rfind("\n\n", 0, _READ_CAP)
    if cut < _READ_CAP // 2:
        cut = _READ_CAP
    spoken = article[:cut].rstrip()
    left = words - len(spoken.split())
    return (head + "\n\n" + spoken
            + f"\n\n(That's where I have to stop — about {left:,} "
              "more words of the article weren't read.)")


# ---------------------------------------------------------------------------
# LINK HEALTH CHECK — a batch of links, in plain words (read-only)
# ---------------------------------------------------------------------------


class _StopRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so the walk can see and report each hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _probe(url: str) -> dict:
    """Walk one URL by hand (≤6 hops). Returns {status, final_url,
    hops, error}: status None + error set when it never answered."""
    opener = senses._build_sniff_opener((_StopRedirect(),))
    hops: list[str] = []
    current = url
    for _ in range(7):
        status = None
        location = ""
        for method in ("HEAD", "GET"):
            req = urllib.request.Request(
                current, method=method,
                headers={"User-Agent": _UA, "Accept": "*/*"})
            try:
                with opener.open(req, timeout=_TIMEOUT) as resp:
                    status = resp.getcode() or 0
                break
            except urllib.error.HTTPError as e:
                status = e.code or 0
                headers = (getattr(e, "headers", None)
                           or getattr(e, "hdrs", None))
                location = (headers.get("Location", "")
                            if headers else "")
                if method == "HEAD" and status in (405, 501):
                    continue  # the site refuses HEAD — try a GET
                break
            except Exception as e:  # noqa: BLE001 — plainly reported
                return {"status": None, "final_url": current,
                        "hops": hops,
                        "error": f"it didn't answer ({e})"}
        if status is not None and 300 <= status < 400 and location:
            hops.append(current)
            current = urllib.parse.urljoin(current, location)
            continue
        return {"status": status, "final_url": current,
                "hops": hops, "error": ""}
    return {"status": None, "final_url": current, "hops": hops,
            "error": "it kept redirecting (more than 6 hops)"}


def link_health_check(urls: str) -> str:
    """Alive / redirecting / dead for a batch of links."""
    raw = [part.strip() for part in re.split(r"[;,\n]", urls or "")
           if part.strip()]
    if not raw:
        return ("Give me one or more links, separated by commas "
                "or semicolons, and I'll check each one.")
    if len(raw) > _LINK_CAP:
        extra = len(raw) - _LINK_CAP
        raw = raw[:_LINK_CAP]
        note = (f" (Only the first {_LINK_CAP} were checked — "
                f"{extra} more were skipped.)")
    else:
        note = ""
    lines = []
    alive = redirecting = broken = 0
    for item in raw:
        full, host = _normalise_url(item)
        if not host:
            broken += 1
            lines.append(f"• {item} — that doesn't look like a "
                         "usable web address.")
            continue
        result = _probe(full)
        if result["error"]:
            broken += 1
            lines.append(f"• {full} — dead: {result['error']}.")
        elif result["hops"]:
            redirecting += 1
            lines.append(
                f"• {full} — redirecting: after "
                f"{len(result['hops'])} redirect(s) it lands at "
                f"{result['final_url']} (HTTP {result['status']}).")
        elif result["status"] and 200 <= result["status"] < 300:
            alive += 1
            lines.append(f"• {full} — alive (HTTP {result['status']}).")
        elif result["status"] and result["status"] < 400:
            broken += 1
            lines.append(f"• {full} — answered HTTP "
                         f"{result['status']} (a redirect with "
                         "nowhere to go).")
        else:
            broken += 1
            lines.append(f"• {full} — answered HTTP "
                         f"{result['status']} — the page itself "
                         "isn't there (the site is up, the link is "
                         "broken).")
    lines.append(f"Checked {len(raw)} link(s): {alive} alive, "
                 f"{redirecting} redirecting, {broken} dead or broken.")
    return "\n".join(lines) + note
