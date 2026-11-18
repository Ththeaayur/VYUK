"""MainWindow chat mixin: page transitions, chat bubbles,
find-in-chat, history restore/clear.

v5.50 (programme item 43): split out of gui/main_window.py,
which remains as the re-export shim. Bodies moved verbatim;
behaviour is unchanged."""

# GUI split #9 INTENTIONAL: ChatMixin split verbatim into the
# three part-mixins below; this file keeps the composed class so
# every existing importer (main_window, window_memory, tests) keeps
# working unchanged. No method name is defined by more than one mixin.
# Chat UI build (2026-11-16): two more part-mixins — the live
# reply (streaming, stop, retry, regenerate) and the transcript
# as a tree (edit/branch, quote, find, history, actions).
from .window_chat_nav import ChatNavMixin
from .window_chat_bubbles import ChatBubblesMixin
from .window_chat_history import ChatHistoryMixin
from .window_chat_stream import ChatStreamMixin
from .window_chat_thread import ChatThreadMixin


class ChatMixin(
    ChatNavMixin,
    ChatBubblesMixin,
    ChatHistoryMixin,
    ChatStreamMixin,
    ChatThreadMixin,
):
    """Composed chat mixin (GUI split #9): the three part-mixins
    above, in plan order. Method bodies live verbatim in the
    part modules; this class only composes them.
    """
