"""research_edition_deltas_guards — part of the research_edition_deltas split (Phase B B10).

Moved verbatim from core/memory/research_edition_deltas.py; see the
facade there for the re-export map.
"""
from .research_edition_deltas_helpers import _unavailable  # INTENTIONAL (research_edition_deltas split B10): shared helper, lives in helpers per plan §2 P3(c)
import json
import os
import shutil

# ---------------------------------------------------------------------------
# RS19-D shelf — §11.8 telemetry-off enforcement (patterns)
# ---------------------------------------------------------------------------
# Forced-off + self-test proof. What this proves: the FORCING
# (env vars + config text) is present. What stays OWED: the
# installed-machine proof (MLflow get_telemetry_client() is
# None; DVC config read; Streamlit config dump) — that proof
# runs where the tools are installed, and this module never
# claims it from here.



TELEMETRY_OFF_ENV = {
    # MLflow: anonymised telemetry to mlflow-telemetry.io,
    # killed by either var (MLflow's own docs). Must be set
    # IN-PROCESS before import — clean-env subprocesses may
    # not inherit it, which the self-test cannot see.
    "MLFLOW_DISABLE_TELEMETRY": "true",
    "DO_NOT_TRACK": "true",
    # DVC: analytics ON by default; env opt-out (config form
    # is `dvc config core.analytics false` — see
    # dvc_template_config()).
    "DVC_NO_ANALYTICS": "true",
    # Streamlit: usage-stats collection off (config form is
    # browser.gatherUsageStats = false — see
    # telemetry_off_selftest()'s config check).
    "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
}


_STREAMLIT_CONFIG_OFF = "gatherUsageStats = false"


def telemetry_off_selftest(env=None, streamlit_config_text=""):
    """Self-test that asserts the §11.8 forcing is present.
    env defaults to the live process env. Failures name the
    missing forcing; a pass proves the forcing, NOT the tools'
    behaviour on an installed machine (that proof stays OWED)."""
    env = os.environ if env is None else env
    failures = []
    for key, value in TELEMETRY_OFF_ENV.items():
        actual = str(env.get(key, "")).lower()
        if actual != value.lower():
            failures.append(
                f"telemetry env not forced off: {key} "
                f"(want {value!r}, got {actual!r})")
    if streamlit_config_text and \
            _STREAMLIT_CONFIG_OFF not in streamlit_config_text:
        failures.append(
            "Streamlit config does not force "
            "browser.gatherUsageStats = false")
    return {"passed": not failures, "failures": failures,
            "note": "proves the forcing is present; the "
                    "installed-machine proof (MLflow client is "
                    "None, DVC config read, Streamlit config "
                    "dump) stays OWED where the tools live"}


def dvc_template_config():
    """Config lines a VYUK-generated template repo sets for the
    user (DVC pattern): analytics off before the first run."""
    return ["[core]", "    analytics = false"]


def refuse_mlflow_wizard():
    """REFUSED, always: MLflow's README 'setup wizard' pattern
    (curl-pipe install + agent code-modification of the user's
    codebase) is not executed, not recommended, by default."""
    return _unavailable(
        "MLflow setup-wizard pattern refused: no curl-pipe "
        "install, no agent modification of user code")


def validate_cell_request(request):
    """Validate a notebook-cell execution request. Returns the
    funnel route, never an execution. Code is DATA until the
    funnel + confirm gate say otherwise."""
    request = request or {}
    code = request.get("code", "")
    language = str(request.get("language", "python") or "")
    if not isinstance(code, str) or not code.strip():
        return {"routable": False,
                "reason": "cell code must be a non-empty string"}
    if language not in ("python", "r", "julia"):
        return {"routable": False,
                "reason": f"unsupported cell language: {language}"}
    return {"routable": True,
            "route": "tools.execute with confirm posture",
            "language": language,
            "code_chars": len(code),
            "note": "execution happens only through the funnel; "
                    "this validator never runs code"}


_JUPYTER_AI_CLOUD_MARKERS = (
    "api.openai.com", "api.anthropic.com", "generativelanguage",
    "openrouter", "api.cohere.com", "api.mistral.ai",
)


def audit_jupyter_ai_config(config):
    """Local-model-only audit for a Jupyter AI configuration:
    no cloud provider endpoints, no API keys, local model
    named. Prompts go to the CHOSEN provider when invoked, so
    a cloud provider here is a data-flow finding, not a
    telemetry footnote."""
    config = config or {}
    failures = []
    text = json.dumps(config, default=str).lower()
    for marker in _JUPYTER_AI_CLOUD_MARKERS:
        if marker in text:
            failures.append(
                f"cloud provider endpoint in config: {marker}")
    for key, value in config.items():
        if ("key" in str(key).lower()
                or "token" in str(key).lower()) \
                and value not in (None, "", False):
            failures.append(f"credential configured: {key}")
    if not config.get("local_model"):
        failures.append("no local model named")
    return {"local_only": not failures, "failures": failures}


def _drive_command(binary, args):
    """Drive-only command builder for an installed binary.
    Returns the command when the binary resolves on PATH;
    refuses (never bundles, never downloads) when absent.
    Building a command is not executing it."""
    path = shutil.which(binary)
    if not path:
        return _unavailable(
            f"{binary} is not installed on PATH — VYUK drives "
            f"an installed {binary}, never bundles or downloads it")
    return {"available": True, "binary": path,
            "command": [path] + [str(arg) for arg in args]}


def quarto_command(source, output_format="html"):
    """Quarto render command for an installed Quarto."""
    return _drive_command("quarto", [source, "--to", output_format])


def typst_command(source, output=None):
    """Typst compile command for an installed Typst compiler."""
    args = ["compile", source]
    if output:
        args.append(output)
    return _drive_command("typst", args)


def publication_table(rows, columns, fmt="markdown", title="",
                      footnote=""):
    """Great Tables pattern, natively: rows (dicts) + columns
    into a publication-style table as text. No pandas import.
    fmt is 'markdown' or 'latex'; anything else falls back to
    markdown rather than failing. A spanner title + footnote
    ride the table; numbers are rendered as given, never
    reformatted behind the author's back."""
    header = list(columns)
    body = [[str(row.get(col, "")) for col in header]
            for row in (rows or [])]
    if fmt == "latex":
        lines = []
        if title:
            lines.append(f"% {title}")
        colspec = "l" * len(header) if header else "l"
        lines.append(f"\\begin{{tabular}}{{{colspec}}}")
        lines.append(" & ".join(header) + " \\\\")
        lines.append("\\hline")
        for line in body:
            lines.append(" & ".join(line) + " \\\\")
        lines.append("\\end{tabular}")
        if footnote:
            lines.append(f"% {footnote}")
        return "\n".join(lines)
    lines = []
    if title:
        lines.append(f"**{title}**")
    lines.append(" | ".join(header))
    lines.append(" | ".join("---" for _ in header))
    for line in body:
        lines.append(" | ".join(line))
    if footnote:
        lines.append(f"*{footnote}*")
    return "\n".join(lines)


FIGURE_STYLES = {
    "science": {"font": "serif", "font_size": 10,
                "axes_linewidth": 1.0, "palette": "muted"},
    "ieee": {"font": "serif", "font_size": 8,
             "axes_linewidth": 0.8, "palette": "grayscale"},
    "nature": {"font": "sans-serif", "font_size": 7,
               "axes_linewidth": 0.7, "palette": "muted"},
}


def figure_style_spec(journal):
    """SciencePlots pattern as DATA: a named journal style's
    parameters. This is a spec, not a renderer — the caller
    applies it only if matplotlib is installed; this function
    never imports matplotlib. Unknown journals fall back to
    'science' rather than failing."""
    key = str(journal or "").lower()
    return dict(FIGURE_STYLES.get(key, FIGURE_STYLES["science"]))


def capture_environment(versions, os_info, inputs):
    """G1 lighter capture manifest. Pure dict builder: the
    caller supplies versions (mapping), os_info (string), and
    inputs (list of paths/identifiers). Validation names
    missing pieces instead of emitting a manifest that claims
    completeness it does not have."""
    versions = versions or {}
    os_info = str(os_info or "")
    inputs = list(inputs or [])
    missing = []
    if not versions:
        missing.append("versions (lockfile) is empty")
    if not os_info:
        missing.append("os_info is empty")
    if not inputs:
        missing.append("inputs list is empty")
    return {
        "complete": not missing,
        "missing": missing,
        "manifest": {
            "versions": dict(versions),
            "os": os_info,
            "inputs": inputs,
        },
    }


def compendium_scaffold(project, author, licence="MIT"):
    """G5 research-compendium scaffold (rrtools pattern,
    VYUK-original text): folders, a DESCRIPTION-style manifest,
    a README citation block, a licence picker, a FAIR
    checklist, and a reproduce script. Generation only — the
    caller writes files through the funnel. Licence picker
    offers MIT (code), CC-BY-4.0 (text/figures), CC0 (data);
    anything else is refused rather than guessed."""
    if licence not in ("MIT", "CC-BY-4.0", "CC0"):
        return _unavailable(
            f"licence picker offers MIT, CC-BY-4.0, CC0 — "
            f"not {licence!r}")
    name = str(project or "research-compendium")
    files = {
        f"{name}/README.md": (
            f"# {name}\n\nAuthor: {author}\n\n"
            f"How to cite: {author}, {name} "
            f"(this compendium).\n\nLicence: {licence}.\n"),
        f"{name}/DESCRIPTION": (
            f"Project: {name}\nAuthor: {author}\n"
            f"Licence: {licence}\n"),
        f"{name}/data/README.md": (
            "Data are read-only here. Raw inputs live in "
            "data/raw/; derived data in data/derived/.\n"),
        f"{name}/analysis/analysis.md": (
            "Analysis as a dynamic document: methods, code, "
            "and outputs in one place.\n"),
        f"{name}/output/README.md": (
            "Outputs: figures, tables, and the manuscript.\n"),
        f"{name}/reproduce.sh": (
            "#!/bin/sh\n# Reproduce: re-run the analysis from "
            "this compendium.\n# (Fill in the analysis command.)\n"),
        f"{name}/FAIR-checklist.md": (
            "- Findable: DOI + README citation block\n"
            "- Accessible: repository + licence\n"
            "- Interoperable: open formats, manifests\n"
            "- Reusable: licence + reproduce script\n"),
    }
    return {"available": True, "project": name,
            "licence": licence, "files": files}


ZENODO_TERMS_NOTE = (
    "Zenodo access is open to all for nonmilitary purposes "
    "only (Zenodo Terms v1.3) — a commercial assistant cannot "
    "silently promise unrestricted Zenodo use; this clause is "
    "surfaced here and must be surfaced in-product. Metadata "
    "is CC0; file licence is depositor-chosen. Search API "
    "rate limits: 25/page anonymous, 100 authenticated, "
    "30 req/min — design around them."
)


def zenodo_deposit_prep(title, creators, files, licence="CC-BY-4.0"):
    """G6 Zenodo deposit PREPARATION (PILOT verified-route):
    deposit metadata, nothing more. Deposits are user-account,
    user-content, explicit-confirm only — and HUMAN-ACT: this
    module never submits (no submit function exists; see
    refuse_submit())."""
    if not title or not creators or not files:
        return _unavailable(
            "Zenodo prep needs a title, at least one creator, "
            "and at least one file")
    return {
        "available": True,
        "metadata": {
            "title": title,
            "creators": [{"name": name} for name in creators],
            "files": list(files),
            "licence": licence,
            "upload_type": "publication",
        },
        "terms_note": ZENODO_TERMS_NOTE,
        "submits": False,
        "note": "prepared, not submitted — deposits are a "
                "human act (explicit confirm + user account)",
    }


def refuse_submit():
    """Deposits/submissions are a HUMAN ACT: she prepares, he
    submits. The refusal is the feature (same shape as Wave 6's
    refuse_peer_review / serve_pdf)."""
    return _unavailable(
        "VYUK prepares deposits; it never submits them "
        "(human-act rule)")


def deadline_pack(entries, dataset_name="", dataset_date=""):
    """G7 deadline-pack template generator. Entries are dicts
    (title, venue, deadline, timezone, link). Validation names
    bad entries instead of emitting a pack that looks complete.
    The pack is a TEMPLATE the user dates and owns — never a
    bundled snapshot, never live-scraped at runtime. Every pack
    carries the verify-at-source warning: community-edited
    deadlines move, and a wrong AoE conversion that costs a
    submission is the worst failure on this shelf."""
    entries = entries or []
    problems = []
    clean = []
    for entry in entries:
        entry = entry or {}
        missing = [key for key in ("title", "venue", "deadline")
                   if not entry.get(key)]
        if missing:
            problems.append(
                f"entry missing {', '.join(missing)}: "
                f"{entry.get('title', '')}")
            continue
        clean.append({
            "title": entry["title"],
            "venue": entry["venue"],
            "deadline": entry["deadline"],
            "timezone": entry.get("timezone", "AoE"),
            "link": entry.get("link", ""),
        })
    source = (f"per {dataset_name}, {dataset_date}"
              if dataset_name or dataset_date
              else "per an undated dataset")
    return {
        "entries": clean,
        "problems": problems,
        "warning": f"Deadlines are {source} — verify on the "
                   "official CFP before acting; community "
                   "deadlines move, and AoE conversions must "
                   "be checked, not assumed.",
    }


def india_grant_links():
    """India grants: LINK-OUT + reminder shape only. No public
    API exists; applications are portal-only, and no scraper is
    built (portal ToS unverified, login-gated, fragile — and
    submission itself stays human). Source: wave-19 report,
    portal roots as named there."""
    return {
        "note": "No public API exists for DST/SERB/ANRF calls. "
                "These are curated deep-links with a "
                "reminder shape, not scraped data; submission "
                "stays human.",
        "portals": [
            {"name": "SERB online", "url": "https://serbonline.in"},
            {"name": "ANRF online", "url": "https://anrfonline.in"},
        ],
        "reminder_shape": {
            "title": "", "deadline": "", "checklist": [
                "eligibility confirmed on the portal",
                "documents per the portal checklist",
                "submitted by the applicant (human act)",
            ],
        },
    }
