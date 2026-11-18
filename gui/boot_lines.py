"""R7-2 — boot-terminal rows (pure, no Qt).

The pure layer of the cinematic boot screen (gui/boot_screen.py
is the Qt overlay): it turns a boot report (the
core/desktop/boot_sequence.py ``report()`` dict — beats, every
stage with its verdict, the log, the greeting) into boot-terminal
rows, stage name + verdict per beat in order, greeting last.
Pure so the lab can prove it on both interpreters, the
agent_overlay.py two-layer pattern.
"""

# ASCII verdict tags: they render in any font (Share Tech Mono
# has no guaranteed check/circle glyphs), and the tag — not the
# colour — carries the meaning, so a failed stage reads as failed
# without a red the palette does not own.
VERDICT_TAGS = {
    "ready": "[ OK ]",
    "degraded": "[WARN]",
    "failed": "[FAIL]",
    "skipped": "[SKIP]",
    "unchecked": "[    ]",
}


def boot_progress(report):
    """(checked, total) stages in a boot report (defensive)."""
    stages = (report or {}).get("stages") or []
    checked = sum(1 for s in stages if s.get("checked"))
    return checked, len(stages)


def boot_lines(report):
    """Boot-terminal rows for a boot report, in order.

    Returns a list of ``(text, kind)`` where kind is one of
    "header", "progress", "beat", "stage", "greeting". Beat
    headers group their stages; the greeting (which names only
    what was actually checked) comes last.
    """
    report = report or {}
    stages = report.get("stages") or []
    beats = report.get("beats") or []
    version = report.get("version", "")
    checked, total = boot_progress(report)
    lines = [
        ("VYUK %s — BOOT" % version if version else "VYUK — BOOT",
         "header"),
        ("checked %d of %d" % (checked, total), "progress"),
    ]
    for beat in beats:
        beat_stages = [s for s in stages if s.get("beat") == beat]
        if not beat_stages:
            continue
        lines.append(("── %s ──" % str(beat).upper(), "beat"))
        for stage in beat_stages:
            verdict = stage.get("verdict", "unchecked")
            tag = VERDICT_TAGS.get(verdict, "[    ]")
            label = stage.get("label") or stage.get("id") or "?"
            text = "  %s %s" % (tag, label)
            reason = stage.get("reason") or ""
            if reason and verdict != "ready":
                text += " — %s" % reason
            lines.append((text, "stage"))
    greeting = report.get("greeting") or ""
    if greeting:
        lines.append(("> %s" % greeting, "greeting"))
    return lines
