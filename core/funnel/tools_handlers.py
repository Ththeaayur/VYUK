"""Tool handlers for the registry (v5.50, programme item 43).

Split out of core/tools.py: the frame-sink seam (how a captured
frame honestly reaches the model), the launch/browser/clipboard/
start-menu helpers, the deadline / timeout-cooldown / call-budget
limits, and ToolHandlersMixin — every _tool_* implementation, which
ToolRegistry (in core/funnel/tools_registry.py) inherits. The _register_*
pack registrations moved (Refactor Phase 1, Step 1) into the core/funnel/tools_handlers_pack_*.py modules
(hands/waves stay flat), re-attached to
ToolHandlersMixin by inheritance; the class surface is unchanged.

core/tools.py was deleted in Refactor Phase 4 (S1c); the funnel package is now the only home. Bodies moved
verbatim; behaviour is unchanged.
"""

import ctypes
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from typing import Any
from .. import injection  # Phase 3 F3: file now in core/funnel/; import climbs one more level
from ..memory.memory import (  # Phase 3 M2h: memory.py moved to core/memory/memory.py
    as_user_note,
    instruction_shaped,
    looks_like_credential,
    memory_store,
)  # Phase 3 F3: file now in core/funnel/; import climbs one more level

from .tools_handlers_pack_core1 import CorePacksMixin
from .tools_handlers_pack_core2 import MindWorldPacksMixin
from ..tools_handlers_pack_hands import HandsPacksMixin  # Phase 3 F3: file now in core/funnel/; import climbs one more level
from .tools_handlers_pack_libwire import LibwirePacksMixin
from .tools_handlers_pack_s7consumers import S7ConsumersPacksMixin
from .tools_handlers_pack_s7consumers2 import S7Consumers2PacksMixin
from .tools_handlers_pack_s7consumers3 import S7Consumers3PacksMixin
from ..tools_handlers_pack_waves import WavePacksMixin  # Phase 3 F3: file now in core/funnel/; import climbs one more level
from .tools_handlers_pack_wave78 import Wave78PacksMixin
from .tools_handlers_pack_wave9 import Wave9PacksMixin
from .tools_handlers_pack_wave10 import Wave10PacksMixin
from .tools_gate import (
    _PLUGIN_GATE_CLASS,
    _send_key_event,
)  # Phase 3 F1: tools_gate moved to core/funnel/ (Phase 3 F3: file now in core/funnel/ (intra-package sibling))
from .pack_registration import PACK_REGISTRATION_FAILURES, _record_pack_failure  # Phase 3 F2b: pack_registration moved to core/funnel/ (Phase 3 F3: file now in core/funnel/ (intra-package sibling))

# INTENTIONAL (skills split B48): frame/launch + limits helpers moved
# to sibling modules; these re-exports ARE the moved functions,
# so from-import snapshots and module-object uses resolve identically.
from .tools_handlers_frame_launch import (
    article_for,
    _frame_sink,
    _FrameSinkDeclined,
    FRAME_SINK_DECLINED,
    set_frame_sink,
    _deliver_frame,
    _frame_result_text,
    _monitor_flag_note,
    _marks_suffix,
    _ocr_captured_frame,
    _BROWSER_LOOKUP,
    _browser_controller,
    _clipboard_secret_shown,
    _START_APPS_TTL_SECONDS,
    _START_APPS_CACHE,
    _start_apps_inventory,
    _normalize_start_app_name,
    _match_start_app,
    _LAUNCH_VERIFY_TIMEOUT_S,
    _LAUNCH_VERIFY_INTERVAL_S,
    _launch_clock,
    _launch_sleep,
    _launch_evidence,
    _verify_launch,
)
from .tools_handlers_limits import (
    _TOOL_DEADLINE_CLASS,
    _TOOL_DEADLINE_DEFAULT_CLASS,
    _TOOL_DEADLINE_CONFIG_FIELDS,
    _limits_config_value,
    deadline_for,
    _timeout_cooldowns,
    _timeout_cooldown_lock,
    _cooldown_now,
    _timeout_target_key,
    _timeout_cooldown_seconds,
    note_tool_timeout,
    timeout_cooldown_active,
    _budget_timestamps,
    _budget_lock,
    _budget_now,
    _BUDGET_WINDOW_S,
    _tool_call_budget,
    _budget_check,
    _budget_refusal,
    reset_tool_budget,
)

# INTENTIONAL (skills split B49): module constants moved with
# their sole users into the command_url mixin; these re-exports
# ARE the moved constants, so module-object uses resolve identically.
from .tools_handlers_command_url import (
    VK_VOLUME_MUTE,
    VK_VOLUME_DOWN,
    VK_VOLUME_UP,
    _RECALL_STOPWORDS,
    _BROWSER_LABELS,
)

# INTENTIONAL (skills split B49): method mixins moved from this file;
# the composed class below preserves the pack-mixin inheritance
# order (new mixins first, then the 11 pack mixins in original
# order), so _register_toolbox_tools (WavePacksMixin) and every
# pack registration resolve exactly as before.
from .tools_handlers_command_url import ToolHandlersCommandUrlMixin
from .tools_handlers_capture_launch import ToolHandlersCaptureLaunchMixin
from .tools_handlers_data_services import ToolHandlersDataServicesMixin


class ToolHandlersMixin(
    ToolHandlersCommandUrlMixin,
    ToolHandlersCaptureLaunchMixin,
    ToolHandlersDataServicesMixin,
    CorePacksMixin,
    MindWorldPacksMixin,
    HandsPacksMixin,
    LibwirePacksMixin,
    S7ConsumersPacksMixin,
    S7Consumers2PacksMixin,
    S7Consumers3PacksMixin,
    WavePacksMixin,
    Wave78PacksMixin,
    Wave9PacksMixin,
    Wave10PacksMixin,
):
    pass
