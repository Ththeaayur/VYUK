# INTENTIONAL (context_menus split B2): _find_target_element uses
# _clean_name, which now lives in context_menus_helpers.
from .context_menus_helpers import _clean_name


# ---------------------------------------------------------------------------
# Default backend — existing tree sources only, lazy imports, field-owed
# ---------------------------------------------------------------------------


class _DefaultContextBackend:
    """Live Windows backend (field-owed, headless-safe).

    Subclasses the item-3 default backend, so expand/invoke
    (ExpandCollapse / Invoke patterns only — never
    coordinates, never synthesised keystrokes), node wrapping,
    and tree stamps are shared, not duplicated. The context
    additions: the trigger right-clicks the tree-matched
    element (``click_input(button='right')`` on the UIA
    wrapper — the element comes from the live walk, never
    from memorised coordinates), then re-walks for a Menu
    popup element. Popup ownership varies by app and toolkit:
    this backend looks for the popup in the foreground
    window's descendant walk; a popup owned by the desktop
    window (or another owner) is NOT found here and the
    trigger fails honestly — refining that walk is field
    work on real Windows evidence, not something to guess
    at in the lab. Every method degrades honestly
    off-Windows (no menu + note) instead of fabricating one.
    """

    def __init__(self):
        from . import menu_paths as _mp  # Phase 3 S7a-2: menu_paths already in core/desktop/  # Phase 3 S7a: menu_paths moved to core/desktop/
        self._menu_backend = _mp.default_backend()

    def foreground_identity(self):
        return self._menu_backend.foreground_identity()

    def bounds(self):
        return self._menu_backend.bounds()

    def expand(self, node):
        return self._menu_backend.expand(node)

    def invoke(self, node):
        return self._menu_backend.invoke(node)

    def _find_menu_popup(self):
        """(menu wrapper, note) for a Menu element in the
        foreground window's current descendant walk, else
        (None, reason). MenuBar elements are skipped: a
        context popup is a Menu, not the app's menu bar."""
        try:
            from . import uia as _uia  # Phase 3 D4b: uia hub now in core/desktop/; S7a-2 interim closed
            fg = self.foreground_identity()
            win, _desc = _uia._uia_window(str(fg.get("title") or ""))
            elements, failure = _uia._descendants(win)
            if failure is not None or elements is None:
                return None, failure or "window walk failed"
            for el in elements:
                facts = _uia._element_facts(el)
                if facts is None:
                    continue
                if facts.get("control_type") == "Menu":
                    return el, ""
            return None, "no Menu popup element in the window walk"
        except Exception as exc:  # noqa: BLE001 — degrade, never raise
            return None, "menu popup walk unavailable: %s" % exc

    def _find_target_element(self, target):
        """(wrapper, note) for the tree-matched target element:
        exact name match (accelerator stripped), control type
        matched when the target names one."""
        try:
            from . import uia as _uia  # Phase 3 D4b: uia hub now in core/desktop/; S7a-2 interim closed
            fg = self.foreground_identity()
            win, _desc = _uia._uia_window(str(fg.get("title") or ""))
            elements, failure = _uia._descendants(win)
            if failure is not None or elements is None:
                return None, failure or "window walk failed"
            wanted = _clean_name(target.get("name"))
            wanted_type = target.get("control_type")
            matches = []
            for el in elements:
                facts = _uia._element_facts(el)
                if facts is None:
                    continue
                if _clean_name(facts.get("name")) != wanted:
                    continue
                if wanted_type and facts.get("control_type") \
                        != wanted_type:
                    continue
                matches.append(el)
            if not matches:
                return None, ("no element named '%s' in the window "
                              "walk" % wanted)
            if len(matches) > 1:
                return None, ("more than one element named '%s' — "
                              "the trigger refuses rather than "
                              "right-clicking a look-alike" % wanted)
            return matches[0], ""
        except Exception as exc:  # noqa: BLE001 — degrade, never raise
            return None, "target walk unavailable: %s" % exc

    def open_context_menu(self, target):
        """Right-click the tree-matched target, then find the
        popup. Returns (menu node, note); (None, reason) when
        the popup cannot be proven open."""
        element, note = self._find_target_element(target or {})
        if element is None:
            return None, note
        try:
            element.click_input(button="right")
        except Exception as exc:  # noqa: BLE001 — trigger failure is a refusal
            return None, "right-click on the target failed: %s" % exc
        popup, popup_note = self._find_menu_popup()
        if popup is None:
            return None, ("no context menu appeared after the "
                          "right-click (%s)" % popup_note)
        node = self._menu_backend._node(
            popup, {"name": "", "control_type": "Menu"})
        return node, ""

    def current_context_menu(self):
        """The open Menu popup, if one is in the current walk."""
        popup, note = self._find_menu_popup()
        if popup is None:
            return None, note
        node = self._menu_backend._node(
            popup, {"name": "", "control_type": "Menu"})
        return node, ""

    def context_menu_tree(self, menu_node):
        """Fresh children of the popup node, via the shared
        item-3 node reader."""
        wrapper = self._menu_backend._wrappers.get(
            menu_node.get("key")) if isinstance(menu_node, dict) else None
        if wrapper is None:
            return None, None, "unknown context menu node"
        children = self._menu_backend._children_nodes(wrapper)
        return children, self._menu_backend._stamp(children), ""


def default_backend():
    """A fresh default backend (stateful per call: wrapper map)."""
    return _DefaultContextBackend()


