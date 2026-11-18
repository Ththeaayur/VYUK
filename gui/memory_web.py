"""
Memory Web (v3.1) — an Obsidian-style graph of everything VYUK
remembers, living beside the plain list on the Memory page.

The window shows "You" at the centre, one hub per fact category,
the facts themselves orbiting their hub, membership edges holding
the structure together, and CROSS-LINKS between facts in different
categories that share a meaningful word — "coffee" in a learned
like and "coffee" in a note weave the web together.

Two layers, deliberately separated:

* The pure data layer (build_graph + seed_positions + relax_step)
  knows nothing about Qt. build_graph turns store rows into nodes
  and edges under hard caps, so the web stays legible (and cheap)
  no matter how much VYUK learns. It is unit-tested headlessly.

* MemoryWebWidget paints that graph with a QPainter, settles it
  with a Fruchterman-Reingold relaxation driven by a QTimer (the
  web visibly falls into place in under a second), and supports
  pan / zoom / drag-to-pin / hover neighbourhood highlighting /
  hub collapse / search dimming.

v3.2 — two upgrades, same contract (MemoryWebWidget is now a thin
container hosting the renderers, so the window's wiring is
untouched):

* Labels no longer overlap. The 2D painter plans its labels
  (priority: You, hubs, the selected node, the hovered node, then
  facts by degree), skips any fact label that would collide with
  an already-placed one, flips hub labels to the other side of
  their node before giving up, and backs every label with a soft
  halo so text stays readable over edges. Skipped labels return
  as you zoom in (or hover their node).

* A 3D mode (MemoryWeb3DWidget, an all-Qt QOpenGLWidget — lit
  sphere impostors on GL_POINTS, GL_LINES edges, orbit camera,
  slow idle auto-rotate) lives beside the 2D canvas behind
  "2D | 3D" chips in the widget's top-right corner. The choice
  persists in QSettings ("memory/dimension", default 2D); if GL
  is unavailable on the machine the 3D chip disables itself and
  the web stays 2D. The 2D layout code is untouched — the 3D
  layout is a sibling implementation with z-coordinates.

Graph rules (as built):
- Hubs: one per category present. Facts per category are capped
  at MAX_PER_CATEGORY (newest by updated_at first); the hub
  remembers the overflow count for its tooltip. Total fact nodes
  are capped at MAX_FACTS (globally oldest are shed first, into
  their hub's overflow).
- Cross-links: tokens are lowercase [a-z0-9] words of length >= 4
  from key+value, minus the stopword set below, minus any token
  equal to a category-name part, minus purely numeric tokens
  (years would link everything to everything). A pair of facts in
  DIFFERENT categories sharing >= 1 token links; each fact keeps
  at most MAX_CROSS_DEGREE cross-links, preferring pairs with
  more shared tokens, then longer ones.

v5.18 — four additions, same container contract (the window's
wiring is still untouched; new state syncs between the renderers
on a mode swap exactly like folds do):

* Search results are now HIGHLIGHTED, not merely un-dimmed (E24):
  matched facts wear a pulsing halo — an animated ring in 2D, a
  brighter, slightly enlarged impostor with an expanding ring in
  3D. The pulse is driven per renderer (2D: a small QTimer that
  runs only while a search is active; 3D: the existing frame
  timer). search_matched_ids() / search_match_count() expose the
  match set.

* A category FILTER (E25): set_category_filter(cats) hides fact
  nodes outside the given category set (None = everything). A hub
  whose facts are ALL filtered or scrubbed away hides too. The
  filter composes with hub folding (a node shows iff it passes
  the filter AND is not folded away) and with search (search
  still only dims; it never hides).

* A cinematic AUTO-TOUR (E26): start_tour() walks the camera
  from hub to hub (~2.6 s per stop), then returns to an overview
  and stops. Design call: the tour lives INSIDE each renderer
  (the camera state lives there too) behind start/stop/tour_active;
  the container just delegates to the renderer on stage and hands
  a running tour across a 2D <-> 3D swap. The 2D tour eases its
  own QTimer-driven pan/zoom; the 3D tour eases inside the frame
  timer and suspends the idle auto-rotate while it runs. Any
  pointer press or wheel scroll on the canvas stops the tour —
  the user's hand always wins.

* A TIMELINE cutoff (E27): set_time_cutoff(epoch) hides fact
  nodes whose updated_at parses to AFTER the cutoff. Undated or
  unparseable rows are always visible — we don't know when they
  are from, so they never vanish. Dates parse as LOCAL time
  ("YYYY-MM-DD HH:MM:SS", fractional seconds tolerated, or a
  bare "YYYY-MM-DD"). The slider UI lives in the window; this
  module owns the data behaviour.
"""


# Refactor Phase 1 step 5: this module is now the same-path
# re-export for its split families — every name below is the
# very same object as in its defining module, so every
# existing import and attribute access keeps working.

from .memory_web_graph import (MAX_PER_CATEGORY, MAX_FACTS, MAX_CROSS_DEGREE, STOPWORDS, KNOWN_COLORS, PALETTE, _WORD_RE, category_color, category_label, GraphNode, GraphEdge, GraphData, _fact_tokens, _you_label, build_graph, _FACT_TIME_FORMATS, _fact_epoch, _GOLDEN_ANGLE, seed_positions, _EDGE_WEIGHTS, relax_step, seed_positions_3d, relax_step_3d, _plan_label_rects, _LABEL_HALO, _label_halo_rect, _draw_label_halo, _draw_label_text, _draw_label, _node_tooltip, _LABEL_FACT_PX, _LABEL_HUB_PX, _TEXT_MAIN, _TEXT_MUTED, _EDGE_CROSS)  # refactor P1 step 5 (moved verbatim)
from .memory_web_canvas2d import (MemoryWeb2DCanvas)  # refactor P1 step 5 (moved verbatim)
from .memory_web_glconsts import (_GL_FLOAT, _GL_POINTS, _GL_LINES, _GL_COLOR_BUFFER_BIT, _GL_DEPTH_BUFFER_BIT, _GL_DEPTH_TEST, _GL_BLEND, _GL_SRC_ALPHA, _GL_ONE_MINUS_SRC_ALPHA, _GL_PROGRAM_POINT_SIZE, _POINT_VS, _POINT_FS, _LINE_VS, _LINE_FS)  # refactor P1 step 5 (moved verbatim)
from .memory_web_gl import (_GL_AVAILABLE, QOpenGLWidget)  # refactor P1 step 5 (moved verbatim)
from .memory_web_widget import (MemoryWebWidget)  # refactor P1 step 5 (moved verbatim)
try:
    from .memory_web_gl import MemoryWeb3DWidget  # refactor P1 step 5 (moved verbatim, conditional)
except ImportError:
    pass
