"""The chat transcript as a tree (chat UI build, 2026-11-16).

Pure Python — no Qt, no tree imports — so the model is unit-tested
headlessly and the GUI is a thin renderer over it.

A conversation is a tree, not a flat list (research rule 5): every
message has a parent, siblings share a parent, and the visible
transcript is the path from the root to the current leaf. A message
may carry several versions — a regenerate is a new version of the
same assistant message, an edit is a new version of the user
message with a new answer branching from it — and the old version
stays reachable through the same flip control ("1 of 2"), never
silently deleted. System lines and result cards are leaf items on
the same path, so a branch switch re-renders them too instead of
orphaning them.

Versions are per-message, and children answer a specific version
of their parent (``parent_version``): when you flip a message back
to an old version, the children that answered the newer version
hide and the ones that answered the old one return. Each version
remembers which child was current (``active_child_by_version``),
so flipping back and forth never loses a branch.

``StreamingReveal`` is the live reply's word gate: chunks arrive
from the server in real time and only whole words — words followed
by whitespace — are ever revealed. A trailing partial word is
held until the next chunk completes it or the turn finalises, so
words never flicker. Nothing here invents timing: reveal follows
arrival, never a timer.
"""

from __future__ import annotations

import html as _html

__all__ = [
    "ChatMessage",
    "ChatThread",
    "StreamingReveal",
    "lit_word_html",
]


def lit_word_html(text: str, accent_css: str) -> str:
    """Render revealed streaming text with the current word lit.

    The current word — the last whole word — gets a soft accent
    colour; everything before it stays plain. No highlight wash,
    nothing pulses: the lit word is the only decoration. The text
    is HTML-escaped first, so a chunk can never inject markup.
    """
    plain = str(text or "")
    if not plain.strip():
        return ""
    esc = _html.escape(plain)
    words = esc.split(" ")
    if len(words) == 1:
        return (f'<span style="color:{accent_css}">'
                f"{words[0]}</span>")
    head = " ".join(words[:-1])
    tail = words[-1]
    return (f"{head} "
            f'<span style="color:{accent_css}">{tail}</span>')


class StreamingReveal:
    """Word gate for a live reply: only whole words are revealed.

    Feed server chunks as they arrive; :attr:`revealed` is the text
    safe to show (every word in it is complete, followed by
    whitespace). The trailing partial word stays in :attr:`pending`
    until a later chunk completes it or :meth:`finalize` releases
    the whole buffer. Feeding an empty chunk changes nothing.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._revealed = ""

    def feed(self, chunk: str) -> str:
        """Append a chunk; return the newly revealed text ("" when
        the chunk only extended the pending partial word)."""
        if not chunk:
            return ""
        self._buffer += chunk
        boundary = max(
            self._buffer.rfind(" "),
            self._buffer.rfind("\n"),
            self._buffer.rfind("\t"),
        )
        new_revealed = self._buffer[: boundary + 1] if boundary >= 0 \
            else ""
        delta = new_revealed[len(self._revealed):]
        self._revealed = new_revealed
        return delta

    @property
    def revealed(self) -> str:
        """Whole words safe to show, trailing whitespace stripped
        for display (the lit-word render splits on spaces)."""
        return self._revealed.rstrip()

    @property
    def pending(self) -> str:
        """The trailing partial word, not yet shown."""
        return self._buffer[len(self._revealed):]

    @property
    def full(self) -> str:
        """Everything received so far, revealed or not."""
        return self._buffer

    def finalize(self) -> str:
        """Release the whole buffer (turn over): every word is
        revealed, nothing stays pending. Returns the full text."""
        self._revealed = self._buffer
        return self._buffer


class ChatMessage:
    """One node in the transcript tree."""

    def __init__(self, msg_id: int, kind: str, role: str,
                 text: str, parent_id: int | None,
                 parent_version: int = 0, **extra):
        self.id = msg_id
        self.kind = kind  # "text" | "card" | "system"
        self.role = role  # "user" | "assistant" | "" (system/card)
        self.versions = [text]
        self.active = 0
        self.parent_id = parent_id
        self.parent_version = parent_version
        self.children: list[int] = []
        # Which child is current, per version of THIS message.
        self.active_child_by_version: dict[int, int | None] = {}
        self.status = extra.get("status", "done")
        # "done" | "streaming" | "stopped" | "failed"
        self.error = extra.get("error")
        self.spoken = bool(extra.get("spoken", False))
        self.quote_id = extra.get("quote_id")
        self.quote_text = extra.get("quote_text")
        self.pinned = False
        self.deleted = False
        self.ts = extra.get("ts")
        # The conversation_logs row this node restores from
        # (None for a live message): find jumps to it, and the
        # last-seen position is an id, not a guess.
        self.store_id = extra.get("store_id")
        # Card items only.
        self.card_title = extra.get("card_title")
        self.card_tool = extra.get("card_tool")
        self.card_answer = extra.get("card_answer")

    @property
    def text(self) -> str:
        return self.versions[self.active]

    @property
    def version_count(self) -> int:
        return len(self.versions)

    def branch_label(self) -> str:
        """"1 of 2" — the flip control's text, or "" when there is
        only one version and nothing to flip between."""
        if len(self.versions) <= 1:
            return ""
        return f"{self.active + 1} of {len(self.versions)}"


class ChatThread:
    """The transcript tree. The visible thread is the path from
    the root to the current leaf, following each node's active
    version and its current child for that version."""

    def __init__(self) -> None:
        self._messages: dict[int, ChatMessage] = {}
        self._next_id = 1
        root = ChatMessage(0, "root", "", "", None)
        self._messages[0] = root

    # ------------------------------------------------------------ add
    def _new(self, kind: str, role: str, text: str,
             parent_id: int | None, **extra) -> ChatMessage:
        msg_id = self._next_id
        self._next_id += 1
        if parent_id is None:
            # The leaf is always derived from the visible path, so
            # an edit or a version flip can never leave a stale
            # parent behind: the new message answers whatever the
            # visible thread currently ends with.
            parent_id = self.leaf().id
        parent = self._messages[parent_id]
        msg = ChatMessage(
            msg_id, kind, role, text, parent_id,
            parent_version=parent.active, **extra)
        self._messages[msg_id] = msg
        parent.children.append(msg_id)
        parent.active_child_by_version[parent.active] = msg_id
        return msg

    def add_user(self, text: str, *, spoken: bool = False,
                 quote_id: int | None = None,
                 quote_text: str | None = None,
                 ts: str | None = None,
                 store_id: int | None = None) -> ChatMessage:
        return self._new("text", "user", text, None,
                         spoken=spoken, quote_id=quote_id,
                         quote_text=quote_text, ts=ts,
                         store_id=store_id)

    def add_assistant(self, text: str, *,
                      status: str = "done",
                      error: str | None = None,
                      ts: str | None = None,
                      store_id: int | None = None) -> ChatMessage:
        return self._new("text", "assistant", text, None,
                         status=status, error=error, ts=ts,
                         store_id=store_id)

    def add_card(self, title: str, body: str, *,
                 tool_name: str | None = None,
                 answer: str | None = None,
                 ts: str | None = None) -> ChatMessage:
        return self._new("card", "", body, None,
                         card_title=title, card_tool=tool_name,
                         card_answer=answer, ts=ts)

    def add_system(self, text: str, *,
                   ts: str | None = None) -> ChatMessage:
        return self._new("system", "", text, None, ts=ts)

    # ------------------------------------------------------------ read
    def get(self, msg_id: int) -> ChatMessage | None:
        return self._messages.get(msg_id)

    def leaf(self) -> ChatMessage:
        """The last message on the visible thread (the root when
        the thread is empty). Always derived, never cached."""
        thread = self.visible_thread()
        return thread[-1] if thread else self._messages[0]

    def visible_thread(self) -> list[ChatMessage]:
        """The path from the root to the current leaf, skipping
        deleted subtrees (a deleted node's children hide with it;
        undo restores them)."""
        out: list[ChatMessage] = []
        node = self._messages[0]
        while True:
            child_id = node.active_child_by_version.get(node.active)
            if child_id is None:
                break
            child = self._messages.get(child_id)
            if child is None or child.deleted:
                break
            out.append(child)
            node = child
        return out

    def path_to(self, msg_id: int) -> list[ChatMessage]:
        """Ancestors of a message, root-first (for jump-back)."""
        chain: list[ChatMessage] = []
        node = self._messages.get(msg_id)
        while node is not None and node.id != 0:
            chain.append(node)
            node = self._messages.get(node.parent_id) \
                if node.parent_id is not None else None
        return list(reversed(chain))

    def make_visible(self, msg_id: int) -> bool:
        """Steer the visible path so ``msg_id`` is on it: for each
        ancestor, activate the version the child answers and make
        that child current. Returns False when the id is unknown."""
        chain = self.path_to(msg_id)
        if not chain:
            return False
        for child in chain:
            parent = self._messages.get(child.parent_id) \
                if child.parent_id is not None else None
            if parent is None:
                continue
            parent.active = child.parent_version
            parent.active_child_by_version[child.parent_version] \
                = child.id
        return True

    def pinned_messages(self) -> list[ChatMessage]:
        return [m for m in self._messages.values()
                if m.pinned and not m.deleted and m.id != 0]

    # ------------------------------------------------------------ mutate
    def add_version(self, msg_id: int, text: str) -> bool:
        """Regenerate: a new version of the same message, made
        active at once. The old version stays, flippable."""
        msg = self._messages.get(msg_id)
        if msg is None or msg.kind != "text":
            return False
        msg.versions.append(text)
        msg.active = len(msg.versions) - 1
        msg.active_child_by_version.setdefault(msg.active, None)
        return True

    def set_active_version(self, msg_id: int, index: int) -> bool:
        msg = self._messages.get(msg_id)
        if msg is None or not 0 <= index < len(msg.versions):
            return False
        msg.active = index
        return True

    def edit_user(self, msg_id: int, new_text: str) -> bool:
        """Edit a sent message: a new version of it, made active,
        with no children yet — the next answer branches from here.
        The old version and its whole branch stay reachable by
        flipping back. Returns False when the message is not a
        user text message."""
        msg = self._messages.get(msg_id)
        if msg is None or msg.kind != "text" or msg.role != "user":
            return False
        msg.versions.append(new_text)
        msg.active = len(msg.versions) - 1
        msg.active_child_by_version.setdefault(msg.active, None)
        return True

    def set_text(self, msg_id: int, text: str) -> bool:
        """Replace the ACTIVE version's text in place (the live
        reply finalising: the node was created empty when the
        first chunk arrived, and the streamed text lands here).
        Versions are not added — a finalise is not a new
        version, it is the same reply arriving in full."""
        msg = self._messages.get(msg_id)
        if msg is None:
            return False
        msg.versions[msg.active] = text
        return True

    def set_status(self, msg_id: int, status: str,
                   error: str | None = None) -> bool:
        msg = self._messages.get(msg_id)
        if msg is None:
            return False
        msg.status = status
        if error is not None:
            msg.error = error
        return True

    def set_spoken(self, msg_id: int, spoken: bool = True) -> bool:
        msg = self._messages.get(msg_id)
        if msg is None:
            return False
        msg.spoken = bool(spoken)
        return True

    def set_quote(self, msg_id: int, quote_id: int,
                  quote_text: str) -> bool:
        msg = self._messages.get(msg_id)
        if msg is None:
            return False
        msg.quote_id = quote_id
        msg.quote_text = quote_text
        return True

    def set_pinned(self, msg_id: int, pinned: bool = True) -> bool:
        msg = self._messages.get(msg_id)
        if msg is None or msg.id == 0:
            return False
        msg.pinned = bool(pinned)
        return True

    def delete_message(self, msg_id: int) -> bool:
        """Delete one message: it (and the subtree under it)
        leaves the visible thread. The node stays in the tree,
        so undo is a flag clear, not a reconstruction."""
        msg = self._messages.get(msg_id)
        if msg is None or msg.id == 0:
            return False
        msg.deleted = True
        return True

    def restore_message(self, msg_id: int) -> bool:
        """Undo a delete: the message returns to the visible
        thread (its parent's current child for that version is
        pointed back at it, so the path shows it again)."""
        msg = self._messages.get(msg_id)
        if msg is None:
            return False
        msg.deleted = False
        parent = self._messages.get(msg.parent_id) \
            if msg.parent_id is not None else None
        if parent is not None:
            parent.active_child_by_version[msg.parent_version] \
                = msg.id
        return True

    def clear(self) -> None:
        """A cleared conversation is a fresh thread."""
        self.__init__()
