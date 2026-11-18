"""Research edition track (v5.68, plan §5.12) — the NET-NEW deltas only.

D-17: Wave 6 owns the research stack (KN-4 substrate, KN-5 stack,
KW-1 in ``research_stack.py`` / ``knowledge.py``). This module
builds ONLY what Wave 6 did not: the dated-CSV retraction form,
the arXiv RSS/OAI-PMH parsers, native triage ranking, the
Miniflux-pattern polling/dedup layer, the A6 deep-research loop
kernel, the §11.8 telemetry-off patterns, and the D/E/F/G shelf
patterns. Where Wave 6 already has the item (Crossref ONE client,
OpenAlex keyed patch, arXiv API + Atom, PDF-serve refusal,
warn-at-cite, licence gate, disclosure flags, peer-review
refusal, deadline verification), this module IMPORTS it — it
never rebuilds it.

Library, not a tool: stdlib only, NO network I/O (no urllib,
requests, or HTTP client imports — a suite asserts that), NO
subprocess, NO executor import. Network clients accept an
injected ``fetcher`` callable; with none, every network method
returns an honest unavailable record. Nothing here executes
retrieved content, notebook cells, or research-loop steps:
retrieval is data-only, citations come from the retrieval log,
and the summarising pass has no executor access (Wave 6
``summarise_without_executor`` is reused, not reimplemented).

Licence notes (plan §5.12 states; primary sources in
``research_track_studies.md``):
- Retraction Watch bundling form: STILL-OWED (GitLab LICENSE
  unread — the repo location is confirmed, the API route is the
  VERIFIED form). The local CSV slot provisions via
  ``local_assets`` (INV-9) or stays absent; absence is honest.
- Miniflux pattern: Apache-2.0 (secondary-confirmed; the repo
  LICENSE read at vendoring is STILL-OWED). Pattern only — no
  Miniflux code is vendored. FreshRSS-as-bundled-code is
  EXCLUDED (AGPL).
- Jupyter AI: BSD-3-Clause VERIFIED. LIDA: STILL-OWED.
- Quarto: drive an INSTALLED binary only (MIT CLI); the
  bundled-binary form is EXCLUDED (drags GPLv2 Pandoc).
  Typst compiler: Apache-2.0 VERIFIED (local compiler only).
- SciencePlots + Great Tables: both MIT VERIFIED — built here
  as NATIVE patterns (style data + table text), never by
  importing matplotlib/pandas (neither is a dependency).
- repo2docker spec: BSD-3-Clause VERIFIED; the shippable form
  is VYUK's own lighter capture, never the Linux-only packer.
- rrtools: pattern only (VYUK-original text); rrtools' own
  LICENSE is STILL-OWED — no rrtools code/text is copied.
- Zenodo deposit: PILOT verified-route; the nonmilitary-purposes
  clause is surfaced in every prep record. OSF full terms:
  STILL-OWED. Deposits/submissions are HUMAN-ACT: this module
  prepares, never submits (no submit function exists).
- Template fetching (F6): STILL-OWED (gallery/venue ToS);
  templates come from the user's own TeX install only.
- Rejected here, never built: Sakana, Semantic Scholar as a
  bundled dependency, scite, Elsevier/Wiley TDM, LCN, Inciteful,
  eLabFTW/RSpace, ClearML Server, lakeFS >=1.87, Overleaf/HedgeDoc
  hosting, A5's self-hosted Firecrawl option (AGPL), PDF
  store/serve (copyright), FreshRSS-as-bundled-code (AGPL).
"""

# Facade (Phase B B10 split): pure re-export. The implementation lives in
# the research_edition_deltas_* modules below; importers are unchanged.
# INTENTIONAL (research_edition_deltas split B10): stdlib imports re-exported
# so the module namespace (dir diff) is identical to the old module.
import csv
import io
import json
import os
import re
import shutil
import xml.etree.ElementTree as ET
from ..config import DATA_DIR  # INTENTIONAL (research_edition_deltas split B10): re-export
from .research_stack import CrossrefClient, OpenAlexClient  # INTENTIONAL (research_edition_deltas split B10): re-export (old module imported, never rebuilt)

__all__ = [
    "CrossrefClient",
    "OpenAlexClient",
    "parse_retraction_csv",
    "lookup_retraction_dated",
    "parse_crossmark_assertions",
    "warn_at_cite_typed",
    "rss_url",
    "oai_url",
    "parse_rss",
    "parse_oai_pmh",
    "rank_items",
    "ratings_from_store",
    "FeedPollState",
    "poll_feed",
    "research_loop",
    "TELEMETRY_OFF_ENV",
    "telemetry_off_selftest",
    "dvc_template_config",
    "refuse_mlflow_wizard",
    "validate_cell_request",
    "audit_jupyter_ai_config",
    "quarto_command",
    "typst_command",
    "publication_table",
    "figure_style_spec",
    "capture_environment",
    "compendium_scaffold",
    "zenodo_deposit_prep",
    "refuse_submit",
    "deadline_pack",
    "india_grant_links",
]

# INTENTIONAL (research_edition_deltas split B10): from research_edition_deltas_helpers
from .research_edition_deltas_helpers import _unavailable  # INTENTIONAL: re-export (private helper, used by tests via facade namespace)

# INTENTIONAL (research_edition_deltas split B10): from research_edition_deltas_retraction
from .research_edition_deltas_retraction import RETRACTION_CSV_COLUMNS
from .research_edition_deltas_retraction import parse_retraction_csv
from .research_edition_deltas_retraction import lookup_retraction_dated
from .research_edition_deltas_retraction import parse_crossmark_assertions
from .research_edition_deltas_retraction import warn_at_cite_typed

# INTENTIONAL (research_edition_deltas split B10): from research_edition_deltas_feeds
from .research_edition_deltas_feeds import rss_url
from .research_edition_deltas_feeds import oai_url
from .research_edition_deltas_feeds import _clean
from .research_edition_deltas_feeds import parse_rss
from .research_edition_deltas_feeds import parse_oai_pmh
from .research_edition_deltas_feeds import ratings_from_store
from .research_edition_deltas_feeds import _rank_tokens
from .research_edition_deltas_feeds import rank_items
from .research_edition_deltas_feeds import FEED_POLL_STATE_FILENAME
from .research_edition_deltas_feeds import FeedPollState
from .research_edition_deltas_feeds import poll_feed

# INTENTIONAL (research_edition_deltas split B10): from research_edition_deltas_loop
from .research_edition_deltas_loop import MAX_RESEARCH_ROUNDS
from .research_edition_deltas_loop import research_loop
from .research_edition_deltas_loop import summarise_without_executor, tag_retrieved  # INTENTIONAL (research_edition_deltas split B10): re-export

# INTENTIONAL (research_edition_deltas split B10): from research_edition_deltas_guards
from .research_edition_deltas_guards import TELEMETRY_OFF_ENV
from .research_edition_deltas_guards import _STREAMLIT_CONFIG_OFF
from .research_edition_deltas_guards import telemetry_off_selftest
from .research_edition_deltas_guards import dvc_template_config
from .research_edition_deltas_guards import refuse_mlflow_wizard
from .research_edition_deltas_guards import validate_cell_request
from .research_edition_deltas_guards import _JUPYTER_AI_CLOUD_MARKERS
from .research_edition_deltas_guards import audit_jupyter_ai_config
from .research_edition_deltas_guards import _drive_command
from .research_edition_deltas_guards import quarto_command
from .research_edition_deltas_guards import typst_command
from .research_edition_deltas_guards import publication_table
from .research_edition_deltas_guards import FIGURE_STYLES
from .research_edition_deltas_guards import figure_style_spec
from .research_edition_deltas_guards import capture_environment
from .research_edition_deltas_guards import compendium_scaffold
from .research_edition_deltas_guards import ZENODO_TERMS_NOTE
from .research_edition_deltas_guards import zenodo_deposit_prep
from .research_edition_deltas_guards import refuse_submit
from .research_edition_deltas_guards import deadline_pack
from .research_edition_deltas_guards import india_grant_links
