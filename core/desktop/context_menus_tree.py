class _ContextTreeBackend:
    """Wrapper: the opened context menu as an item-3 menu tree.

    ``menu_tree`` is the FRESH context tree read (never a cached
    list); expand/invoke/foreground/bounds delegate to the
    underlying context backend, so ``core.desktop.menu_paths`` walks
    the opened menu with its own exact-name, C-14, and refusal
    rules intact.
    """

    def __init__(self, underlying, menu_node):
        self._underlying = underlying
        self._menu_node = menu_node

    def foreground_identity(self):
        return self._underlying.foreground_identity()

    def bounds(self):
        return self._underlying.bounds()

    def menu_tree(self):
        try:
            result = self._underlying.context_menu_tree(self._menu_node)
        except Exception as exc:  # noqa: BLE001 — backend failure is a refusal
            return None, None, "context menu tree read failed: %s" % exc
        if isinstance(result, tuple) and len(result) == 3:
            items, stamp, note = result
        elif isinstance(result, tuple) and len(result) == 2:
            items, stamp = result
            note = ""
        else:
            items, stamp, note = result, None, ""
        if not isinstance(items, list):
            return None, None, "context menu tree returned no item list"
        return items, stamp, (note or "")

    def expand(self, node):
        return self._underlying.expand(node)

    def invoke(self, node):
        return self._underlying.invoke(node)


