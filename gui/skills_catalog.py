"""Skills catalogue — facade (GUI split #2).

The catalogue split verbatim into gui/skills_catalog_data.py (group
tables + GROUP_ORDER) and gui/skills_catalog_widget.py (widget +
helpers). This file re-exports every public name so every existing
importer (window_pages, skills_galaxy, tests) keeps working unchanged.
"""

# GUI split #2 INTENTIONAL: catalogue split verbatim into the data and
# widget modules below; this facade re-exports every public name (plus
# the private helpers tests address) so importers keep working unchanged.
from .skills_catalog_data import (
    GROUP_MEMBERS,
    GROUP_ORDER,
    ROLE_CHIPS,
    ROLE_FRIENDLY,
    ROLE_GROUP,
    ROLE_KIND,
    ROLE_NAME,
    ROLE_SUMMARY,
    _FALLBACK_GROUP,
    _MAX_SUMMARY,
    _NAME_TO_GROUP,
)
from .skills_catalog_widget import (
    SkillsCatalogWidget,
    _CatalogueDelegate,
    _humanise,
    _summarise,
    build_skills_catalog,
    group_for_name,
)

__all__ = [
    "GROUP_MEMBERS",
    "GROUP_ORDER",
    "ROLE_CHIPS",
    "ROLE_FRIENDLY",
    "ROLE_GROUP",
    "ROLE_KIND",
    "ROLE_NAME",
    "ROLE_SUMMARY",
    "SkillsCatalogWidget",
    "build_skills_catalog",
    "group_for_name",
]
