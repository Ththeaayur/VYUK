"""The tool router (v5.33): a small resident tool set for the
Gemini Live session plus ONE dispatcher, use_toolbox, that
reaches the rest of the registry.

Why: the Live API fixes the tool set at session setup, and
the full registry's declarations run to ~320 KB — past the
~256 KB at which the API accepts the connection and then
kills every turn (RC-7). v5.32's answer was trimming every
description to fit a 240,000-byte budget. The router is the
structural answer: the session offers the 57 core tools
(toolbox.CORE_TOOL_PRIORITY — the same set the local
brain leads with) plus use_toolbox, together ~29 KB
(assistant; desktop ~41 KB incl. the router prompt), and
every other tool stays one describe-then-call away:

  find     rank the whole registry locally for a few words
           of intent; resident tools are marked as directly
           callable, the rest as toolbox-only.
  describe return one tool's full declaration (name,
           description, parameter schema) as text.
  call     execute the named tool through tools.execute —
           the SAME funnel as a direct call, so argument
           validation, the human confirmation gate and the
           injection-taint rules all apply unchanged. The
           router grants no privilege a direct call lacks.

v5.50 (programme item 45 — the round economy): two shortcuts
shave the describe round trip without touching any session's
declaration set. A CONFIDENT find (see find_is_confident for
the exact rule) inlines the top match's full declaration, so
the model calls it in round 2; an ambiguous find keeps the
list-only shape above. And when a call fails argument
validation, tools.execute attaches the tool's own declaration
to the error (its "schema" field) and _call passes it through
in text, so the model repairs its arguments in one round.
Both schemas are result TEXT only — the fitted Gemini payload
and the resident set are byte-unchanged by this item.

The retired all-direct fitted list (DECL-01, owner ruling
2026-11-01) no longer exists: the opt-in full profile and
router-off mode are built from resident + routers like every
other profile — and a router failure falls back to the
desktop set. The local brain and the text-cloud
sessions do not use this module — they already manage
their own tool budgets.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any

# B36 INTENTIONAL (part 1 of 2): tokens-search family + the negative-pair
# helpers keyword_scores needs live in toolbox_tokens; re-exported here so
# every importer path keeps resolving through this facade.
from .toolbox_tokens import (
    _TOKEN,
    ROUTER_PROMPT,
    _tokens,
    stem,
    _stemmed,
    WORD_SYNONYMS,
    _expand,
    TOOL_SYNONYMS,
    tool_search_text,
    keyword_scores,
    CORE_TOOL_PRIORITY,
    core_tool_names,
    rank_tools_keyword,
    NEGATIVE_PAIR_PENALTY,
    NEGATIVE_PAIRS,
    _negative_forbidden,
)


# B37 INTENTIONAL (part 2 of 2): the old mid-file hashlib/math/Path imports
# now live at facade top so the facade namespace matches the old module's.
import hashlib
import math
from pathlib import Path


# B37 INTENTIONAL (part 2 of 2): embeddings family moved here; re-exported
# so every importer path keeps resolving through this facade.
from .toolbox_embeddings import (
    EMBEDDING_DIR,
    EMBEDDING_MANIFEST,
    _EMBEDDING_CACHE_FILE,
    _embedder_singleton,
    _embedder_failed,
    embedding_paths,
    _embedding_file_sha256,
    _embedding_file_ready,
    embedding_model_ready,
    ensure_embedding_model,
    _WordPiece,
    OnnxEmbedder,
    get_embedder,
    set_embedder,
    _cosine,
    _tool_embedding_vectors,
    semantic_scores,
    rank_tools_semantic,
    rank_tools,
    ranking_scores,
)


# B37 INTENTIONAL (part 2 of 2): find-declarations + activity-resident
# families moved here; re-exported so every importer path keeps resolving.
from .toolbox_declarations import (
    FIND_SCHEMA_MIN_SCORE,
    FIND_SCHEMA_MIN_MARGIN,
    FIND_SCHEMA_MIN_RATIO,
    _unsupported_task_message,
    INLINE_MIN_SCORE,
    find_may_inline,
    find_is_confident,
    ASSISTANT_DESC_CAP,
    _cap_description_tree,
    gemini_decl_for_names,
    router_declarations,
    ACTIVITY_LOG_TAIL_BYTES,
    PROTECTED_CORE_TOOLS,
    _NON_TOOL_LOG_SOURCES,
    _TOOL_NAME_SHAPE,
    activity_log_path,
    parse_activity_counts,
    activity_usage_counts,
    compute_resident_tool_names,
    resident_tool_names,
    resident_declarations,
    _declaration,
    _mcp_declaration,
    _mcp_scores,
)


# B37 INTENTIONAL (part 2 of 2): find-call family moved here; re-exported
# so every importer path keeps resolving through this facade.
from .toolbox_findcall import (
    _find,
    _describe,
    _short_received,
    _coerce_call_args,
    _call,
    use_toolbox,
    register_all,
)
