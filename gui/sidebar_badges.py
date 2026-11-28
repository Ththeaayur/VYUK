"""Sidebar badges (UI Phase 2, D-P2-11 / D-P2-23).

Exactly two badges, both display-only (tapping the button
navigates; the badge itself does nothing):

* Activity — pending approvals, from pending_approvals_count()
  (gui/pending_approval.py): the ONE count the Activity page's
  Review button, the Home headline, and the Review queue all
  read, so the badge == the page.
* Inbox — unread mail, from the Inbox page's CACHED unread
  count (window._views_inbox_unread_count). The sidebar never
  triggers a network read: before the first Inbox visit the
  cache is None and there is no badge (unknown, not zero).

Rendering: a small pill anchored to the nav icon's top-right
corner, accent-filled, mono digits, exact count to 99, "99+"
above. Zero reads as zero — no badge at 0, never a "0" pill.
A failed read degrades to no badge, never a broken button.
The badge shows a count, never content — during private mode
the count still shows (Phase 1 S-03 precedent: counts stay,
content goes).

Positioning: the badge is a child of its nav button, so it
moves with the button; _refresh_sidebar_badges and
_apply_sidebar_compact both re-position it (the sidebar width
is fixed 220 / 64, so the icon's position is computable: 18 px
icon, 12 px from the button's left edge in full mode,
centred in the 64 px rail).
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel

from voice_assistant.gui.pending_approval import pending_approvals_count

_ICON = 18
_BADGE_H = 16


def badge_text(count):
    """The pill text for a count: "" (hide) at None / 0,
    the exact count to 99, "99+" above."""
    if count is None:
        return ""
    try:
        n = int(count)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    if n > 99:
        return "99+"
    return str(n)


def make_badge(button, accent="#35D6FF", on_accent="#04090E"):
    """Create the pill as a child of a nav button (hidden
    until a count is set)."""
    badge = QLabel(button)
    badge.setObjectName("navBadge")
    badge.setStyleSheet(
        "QLabel#navBadge { background: %s; color: %s;"
        " border-radius: 8px; padding: 0px 5px;"
        " font-size: 10px; font-weight: 700; }"
        % (accent, on_accent))
    try:
        from PySide6.QtGui import QFont

        from .style import MONO_FONT_STACK

        font = QFont()
        font.setFamily(MONO_FONT_STACK[0])
        font.setPointSize(10)
        font.setBold(True)
        badge.setFont(font)
    except Exception:  # noqa: BLE001 — the mono font is
        pass  # a nicety; the pill still renders.
    badge.setFixedHeight(_BADGE_H)
    badge.hide()
    return badge


def position_badge(badge, button, compact):
    """Anchor the pill to the icon's top-right corner."""
    if badge is None or button is None:
        return
    badge.adjustSize()
    width = badge.width()
    if compact:
        icon_left = max(0, (button.width() - _ICON) // 2)
    else:
        icon_left = 12
    icon_top = max(0, (button.height() - _ICON) // 2)
    badge.move(icon_left + _ICON - width // 2,
               max(0, icon_top - _BADGE_H // 2))
    badge.raise_()


def set_badge(badge, button, count, compact, accent="#35D6FF",
              on_accent="#04090E"):
    """Set a badge's count (hide at 0 / unknown) and position
    it. Never raises."""
    try:
        text = badge_text(count)
        if not text:
            badge.hide()
            return
        badge.setStyleSheet(
            "QLabel#navBadge { background: %s; color: %s;"
            " border-radius: 8px; padding: 0px 5px;"
            " font-size: 10px; font-weight: 700; }"
            % (accent, on_accent))
        badge.setText(text)
        badge.show()
        position_badge(badge, button, compact)
    except Exception:  # noqa: BLE001 — a badge never
        pass  # breaks the sidebar.


def reposition_sidebar_badges(window):
    """Re-anchor the visible badges to their icons without
    re-reading the counts (called from _apply_sidebar_compact
    on a tier change — the counts did not change, only the
    icon positions did). Never raises."""
    try:
        buttons = getattr(window, "nav_buttons", []) or []
        indices = getattr(window, "_nav_page_indices", []) or []
        compact = bool(getattr(window, "_sidebar_compact", False))
        for attr, page in (("_badge_activity", 1),
                           ("_badge_inbox", 9)):
            badge = getattr(window, attr, None)
            if badge is None or not badge.isVisible():
                continue
            try:
                btn = next(b for b, p in zip(buttons, indices)
                           if p == page)
            except StopIteration:
                btn = None
            position_badge(badge, btn, compact)
    except Exception:  # noqa: BLE001 — badges never break
        pass  # the sidebar.


def refresh_sidebar_badges(window):
    """Refresh both badges from the shared counts. Never
    raises; a failed read hides that badge."""
    try:
        activity = getattr(window, "_badge_activity", None)
        inbox = getattr(window, "_badge_inbox", None)
        buttons = getattr(window, "nav_buttons", []) or []
        indices = getattr(window, "_nav_page_indices", []) or []
        compact = bool(getattr(window, "_sidebar_compact", False))
        theme = getattr(window, "_resolved_theme", None) or {}
        accent = (theme.get("accent") if isinstance(theme, dict)
                  else None) or "#35D6FF"
        on_accent = "#04090E"
        if activity is not None:
            try:
                btn = next(
                    b for b, p in zip(buttons, indices) if p == 1)
            except StopIteration:
                btn = None
            set_badge(activity, btn, pending_approvals_count(),
                      compact, accent, on_accent)
        if inbox is not None:
            try:
                btn = next(
                    b for b, p in zip(buttons, indices) if p == 9)
            except StopIteration:
                btn = None
            set_badge(inbox, btn,
                      getattr(window, "_views_inbox_unread_count", None),
                      compact, accent, on_accent)
    except Exception:  # noqa: BLE001 — badges never break
        pass  # the sidebar.
