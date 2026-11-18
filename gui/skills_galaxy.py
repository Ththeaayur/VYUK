"""Skills Galaxy (v5.18, E21 + E22): the toolset as a star map.

The Memory Web pointed its woven-graph technology at what
VYUK *remembers*; the Galaxy points the same technology at
what she can *do*. Every registered built-in tool is a star,
clustered around a bright hub for its catalogue group (the same
grouping the Skills list uses — gui/skills_catalog owns it), and
the whole map renders two ways behind "2D | 3D" chips, exactly
like the Memory Web: a flat QPainter canvas, or a lazy OpenGL
view of lit sphere-impostor stars on a Fibonacci shell. The
choice persists in QSettings ("skills/dimension"); a machine
without working OpenGL quietly stays 2D with the 3D chip
disabled.

Unlike the web there is no force layout to settle: positions
are a pure, deterministic function of the declarations (golden-
angle spirals in 2D, seeded tangent clusters in 3D), so the map
is calm from the first frame and two builds agree to the pixel.

SkillDetailCard is the E22 companion: pick a star and it shows
the skill's friendly name, group and one-line description, with
an "Ask with this" button the window can wire to the assistant.

Self-contained by design: Qt + stdlib + the catalogue's grouping
data are the only imports. Nothing here touches the disk, the
network, or the tools themselves.
"""

# GUI split #5 INTENTIONAL: galaxy split verbatim into data, canvas2d,
# gl, canvas3d, widget modules below; this facade re-exports every
# public name (plus the private helpers tests address) so importers
# keep working unchanged.
from .skills_galaxy_canvas2d import Galaxy2DCanvas
from .skills_galaxy_canvas3d import Galaxy3DWidget, _GL_AVAILABLE
from .skills_galaxy_data import (
    GROUP_PALETTE,
    GalaxyData,
    GalaxyEdge,
    GalaxyNode,
    _ABBREVIATIONS,  # GUI split #5 INTENTIONAL: missed by the split script; facade must re-export every old name
    _BACKDROP,
    _GOLDEN_ANGLE,
    _LABEL_HALO,
    _LABEL_HUB_PX,
    _LABEL_STAR_PX,
    _MAX_DESCRIPTION,
    _TEXT_MAIN,
    _TEXT_MUTED,
    _cluster_radius,
    _draw_label,
    _draw_label_halo,
    _draw_label_text,
    _fibonacci_sphere,
    _label_halo_rect,
    _node_tooltip,
    _plan_label_rects,
    _prettify,
    _seed_floats,
    build_galaxy,
    group_color,
    layout_positions_2d,
    layout_positions_3d,
    search_sets,
    trim_description,
    world_extent_2d,
)
from .skills_galaxy_gl import (
    _GL_BLEND,
    _GL_COLOR_BUFFER_BIT,
    _GL_DEPTH_BUFFER_BIT,
    _GL_DEPTH_TEST,
    _GL_FLOAT,
    _GL_LINES,
    _GL_ONE_MINUS_SRC_ALPHA,
    _GL_POINTS,
    _GL_PROGRAM_POINT_SIZE,
    _GL_SRC_ALPHA,
    _LINE_FS,
    _LINE_VS,
    _POINT_FS,
    _POINT_VS,
)
from .skills_galaxy_widget import SkillDetailCard, SkillsGalaxyWidget
