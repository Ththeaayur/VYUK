"""The one canonical brain-tier table (GAPS-22, B-V1/B-V2).

Before this module the three GGUF tiers were named in two
independent hand-copied literals — ``MODEL_TIERS`` in
``core/voice/local_assets_model.py`` (file + URL + size + label +
sha256, the download metadata) and ``TIER_FILES`` in
``core/model_manager.py`` (file -> tier, "mirroring" the first by
hand) — plus a third table in ``core/config.py`` that no longer
exists. The two copies could drift silently: nothing but a
reviewer's eye kept them agreeing, and the two model resolvers
(``effective_model()`` vs ``model_manager.resolve_*``) read
different tables over different availability inputs, so they
could disagree about which model serves (the GAPS-6 gap proof).

This module is the single source: one table, no imports, no
config, no I/O. ``local_assets_model`` keeps the name
``MODEL_TIERS`` (re-exported through the ``local_assets``
facade, so every existing importer resolves identically);
``model_manager`` builds ``TIER_FILES`` / ``TIER_ORDER`` from
it; the router reads them through ``model_manager`` as before.
A test pins the projection, so the agreement is by
construction now, not by hand.
"""

MODEL_TIERS = {
    "lite": {
        "file": "Qwen3-1.7B-Q4_K_M.gguf",
        "url": (
            "https://huggingface.co/unsloth/Qwen3-1.7B-GGUF"
            "/resolve/main/Qwen3-1.7B-Q4_K_M.gguf"
        ),
        "gb": 1.1,
        "label": "Qwen3-1.7B",
        "sha256": "b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897",
    },
    "balanced": {
        "file": "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        "url": (
            "https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF"
            "/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
        ),
        "gb": 2.5,
        "label": "Qwen3-4B",
        "sha256": "3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597",
    },
    "quality": {
        "file": "Qwen3-8B-Instruct-2507-Q4_K_M.gguf",
        "url": (
            "https://huggingface.co/Qwen/Qwen3-8B-GGUF"
            "/resolve/main/Qwen3-8B-Q4_K_M.gguf"
        ),
        "gb": 5.0,
        "label": "Qwen3-8B",
        "sha256": "d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785",
    },
}

#: GGUF filename -> tier name (the projection ``model_manager``
#: and the router read). Built from MODEL_TIERS, never hand-copied.
TIER_FILES = {tier["file"]: name for name, tier in MODEL_TIERS.items()}

#: Fallback order: biggest tier present first (the load path's
#: rule — the router deliberately uses its own cheaper-first
#: order instead; see ``core/query_routing._routing_fallback``).
TIER_ORDER = ("quality", "balanced", "lite")
