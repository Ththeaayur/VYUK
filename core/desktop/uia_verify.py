"""uia_verify concern extracted from core/desktop/uia.py (refactor Phase 1 step 4). Behaviour identical; every name here stays importable from core/desktop/uia."""




def _expect_corpus(snap) -> dict:
    """Normalise one snapshot dict into evidence fields. Non-dict
    input (None — the probe had no eyes) yields all-empty fields."""
    empty = {"fg": None, "focus": None, "windows": [], "texts": [],
             "tree_hash": None, "all": ""}
    if not isinstance(snap, dict):
        return empty
    fg = snap.get("fg")
    focus = snap.get("focus")
    windows = [str(w) for w in (snap.get("windows") or []) if str(w or "").strip()]
    texts = [str(t) for t in (snap.get("texts") or []) if str(t or "").strip()]
    ocr = str(snap.get("ocr_text") or snap.get("text") or "")
    parts = [str(fg or ""), str(focus or ""), *windows, *texts, ocr]
    return {"fg": fg, "focus": focus, "windows": windows,
            "texts": texts, "tree_hash": snap.get("tree_hash"),
            "all": "\n".join(p for p in parts if p)}


def _corpus_contains(corpus_text: str, target: str) -> bool:
    return target.lower() in str(corpus_text or "").lower()


def _parse_expectation(text: str):
    """(kind, target, style) for one expectation string, or None
    when no groundable shape parses. kind is "window" (a titled
    top-level window/dialog) or "text"; style is "appears" (the
    thing must NEWLY be there) or "present" (it must be there)."""
    import re

    t = " ".join(str(text or "").split()).strip().rstrip(".")
    if not t:
        return None

    def _clean(raw):
        target = " ".join(str(raw or "").split()).strip(" '\"")
        if len(target) < 1 or target.lower() in (
                "something", "anything", "it", "a window", "the window",
                "a dialog", "the dialog"):
            return None
        return target

    win = (r"(?:dialog|window|alert|popup|message box)")
    m = re.search(win + r"\s+(?:titled|named|called)\s*['\"]([^'\"]+)['\"]",
                  t, re.IGNORECASE)
    if not m:
        m = re.search(win + r"\s+(?:titled|named|called)\s+(.+?)"
                      r"(?:\s+(?:appears|opens|shows up|pops up|is shown))?$",
                      t, re.IGNORECASE)
    if not m:
        m = re.search(win + r"[^'\"]*['\"]([^'\"]+)['\"]", t, re.IGNORECASE)
    if m:
        target = _clean(m.group(1))
        return ("window", target, "appears") if target else None
    m = re.search(r"^text\s+['\"]([^'\"]+)['\"]", t, re.IGNORECASE)
    if not m:
        m = re.search(r"^text\s+(.+?)(?:\s+(?:is\s+)?"
                      r"(?:present|visible|appears|shown|displayed))$",
                      t, re.IGNORECASE)
    if not m:
        m = re.search(r"^['\"]([^'\"]+)['\"]\s+(?:is\s+)?"
                      r"(?:present|visible|appears)", t, re.IGNORECASE)
    if not m:
        m = re.search(r"^(.+?)\s+(?:appears|is present|is visible|"
                      r"shows up|pops up)$", t, re.IGNORECASE)
    if m:
        target = _clean(m.group(1))
        if not target:
            return None
        style = ("appears" if re.search(
            r"appear|shows up|pops up", t, re.IGNORECASE) else "present")
        return ("text", target, style)
    return None


def verify_expectation(expect_text, before, after, result_text=""):
    """Judge one declared expectation against before/after snapshots.

    Signature matches the planner's injected callback:
    (expect_text, before_snap, after_snap, result) — `result` may
    be a tools.execute() dict or a bare result string; its text
    doubles as the OCR fallback (a look's result carries the
    frame's OCR text, so text the snapshots lack can still be
    grounded there).

    Returns (verdict, reason), verdict in {"met", "not-met",
    "unverifiable"}:
    - "met" — positive evidence in the AFTER evidence: a window
      expectation whose title is a NEW top-level window (the
      after foreground, or an entry in the after "windows" list
      the before list lacks); a text expectation whose target is
      present in the after snapshot's foreground/focus/texts (or
      the result text), and — for "appears"-style expectations —
      was NOT already there before, or is newly present in the
      after-tree alongside a material tree-hash delta (the
      generic fallback: a hash delta alone never grounds a
      "met"; it counts only when the named thing is present in
      the after-tree).
    - "not-met" — the after evidence was observable and the
      expected thing is absent, or an "appears" expectation's
      target was already there with no material change (nothing
      appeared).
    - "unverifiable" — the expectation names nothing groundable,
      or no after evidence could be observed at all. NEVER a
      guessed "met"."""
    if isinstance(result_text, dict):
        result_text = str(result_text.get("result")
                          or result_text.get("message") or "")
    result_text = str(result_text or "")
    parsed = _parse_expectation(expect_text)
    if parsed is None:
        return ("unverifiable",
                f"the expectation '{expect_text}' names nothing this "
                "engine can ground — it understands shapes like "
                "\"a dialog titled X appears\" and \"text X present\"; "
                "no verdict was guessed.")
    # Command Deck Phase F: VERIFYING now has a real producer — the
    # check itself. Only the kind is emitted, never the expectation
    # text (it may carry user content). "met" verdicts below emit
    # Verified with the reason; not-met / unverifiable emit no
    # Verified event, because no verdict is ever guessed.
    try:
        from .. import state_bus_producers as _prod  # Phase 3 D2c: file now in core/desktop/

        _prod.emit_verifying(kind=parsed[0])
    except Exception:  # noqa: BLE001 — cosmetic channel, never break
        pass

    def _emit_verified(reason):
        try:
            from .. import state_bus_producers as _prod  # Phase 3 D2c: file now in core/desktop/

            _prod.emit_verified(evidence=reason)
        except Exception:  # noqa: BLE001 — cosmetic channel, never break
            pass
    kind, target, style = parsed
    b = _expect_corpus(before)
    a = _expect_corpus(after)
    after_all = a["all"] + ("\n" + result_text if result_text else "")
    in_after = _corpus_contains(after_all, target)
    in_before = _corpus_contains(b["all"], target)
    observed = bool(a["fg"] or a["focus"] or a["windows"] or a["texts"]
                    or a["tree_hash"] is not None or result_text.strip())
    hash_delta = (b["tree_hash"] is not None
                  and a["tree_hash"] is not None
                  and b["tree_hash"] != a["tree_hash"])
    changed = (b["fg"] != a["fg"] or b["focus"] != a["focus"]
               or hash_delta or set(b["windows"]) != set(a["windows"]))
    if kind == "window":
        new_in_list = any(_corpus_contains(w, target) for w in a["windows"]
                          if not any(_corpus_contains(w, target)
                                     for w in b["windows"]))
        fg_new = bool(a["fg"] and _corpus_contains(a["fg"], target)
                      and not (b["fg"] and _corpus_contains(b["fg"], target)))
        if new_in_list or fg_new:
            where = (f"foreground is now '{a['fg']}'" if fg_new
                     else "a new top-level window matches it")
            reason = f"a window titled '{target}' appeared — {where}."
            _emit_verified(reason)
            return ("met", reason)
        if in_after and not changed:
            return ("not-met",
                    f"'{target}' was already the foreground/window "
                    "before the action and nothing changed — it did "
                    "not newly appear.")
        if in_after:
            return ("not-met",
                    f"no NEW window titled '{target}' appeared — it "
                    "was already present before the action.")
        if observed:
            return ("not-met",
                    f"no window titled '{target}' is present after "
                    f"the action (foreground: {a['fg']!r}).")
        return ("unverifiable",
                f"no window titled '{target}' could be checked — "
                "neither snapshot observed a foreground window and "
                "the result text does not mention it.")
    # kind == "text"
    if style == "present":
        if in_after:
            via = ("the result text (OCR fallback)" if not _corpus_contains(a["all"], target)
                   else "the after snapshot")
            reason = f"text '{target}' is present in {via}."
            _emit_verified(reason)
            return ("met", reason)
        if observed:
            return ("not-met",
                    f"text '{target}' is not present in the after "
                    "snapshot or the result text.")
        return ("unverifiable",
                f"text '{target}' could not be checked — no after "
                "evidence was observed.")
    # style == "appears"
    if in_after and not in_before:
        reason = (f"'{target}' was absent before and is present "
                  "in the after evidence — it appeared.")
        _emit_verified(reason)
        return ("met", reason)
    if in_after and in_before:
        if hash_delta and _corpus_contains("\n".join(a["texts"]), target) \
                and not _corpus_contains("\n".join(b["texts"]), target):
            reason = (f"'{target}' is newly present in the "
                      "after-tree alongside a material tree change.")
            _emit_verified(reason)
            return ("met", reason)
        if not changed:
            return ("not-met",
                    f"'{target}' was already present before the "
                    "action and nothing changed — it did not appear.")
        return ("unverifiable",
                f"'{target}' is present both before and after and "
                "other things changed too — whether it (re)appeared "
                "cannot be grounded from these snapshots.")
    if observed:
        return ("not-met", f"'{target}' is not present in the after "
                "evidence — it did not appear.")
    return ("unverifiable", f"'{target}' could not be checked — no "
            "after evidence was observed.")
