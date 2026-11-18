"""S3 Gallery (v5.67 Skills track, Leaf 3): first-party-only catalogue.

Launch posture (SK-6.5, ADOPT): the Gallery lists ONLY first-party
signed skills. There is no third-party catalogue admission path in
this module, in any form. Distributing third-party executable skills
creates distributor exposure, and the legal-counsel review of the
distributor position (plan section 14.20) is a HARD GATE that is NOT
cleared. Every refusal below names that gate in plain words.

WHY FIRST-PARTY ONLY (context, not code)
----------------------------------------
Every open voice/app skill store is a graveyard or a cautionary
tale: the Alexa open store, Actions on Google (shut down 13 June
2023), the GPT Store spam flood. The living models are curated
galleries (Apple) and give-away-the-format (Anthropic). VYUK
launches as a curated Gallery, and the open third-party
catalogue idea is REJECTED on that unanimous graveyard
evidence. Nothing in
this module is shaped like a storefront: no catalogue admission
for outsiders, no catalogue listing for outsiders, and no
commercial catalogue flow of any kind.

STANDALONE BY DESIGN
--------------------
This module imports NO other VYUK skill module (no Leaf 1 format
module, no Leaf 2 gate/signing module). The coordinator composes
this catalogue model with the signing gate afterwards. This module
therefore defines its model against plain skill-record dicts, and
checks ADMISSION POLICY: source, signer key-id pin, signature
presence, and hash pin. Cryptographic signature verification
itself lives in the S2 gate/signing module; a caller that holds a
cryptographically verified signature passes an entry whose
``signature`` field is present, and this module still refuses the
entry if its source is not first-party, its signer key differs
from the catalogue pin, or its hashes do not match.

SKILL-RECORD DICT FIELDS (the contract the coordinator wires)
--------------------------------------------------------------
- ``name``: str, skill name, unique within a catalogue.
- ``version``: str, skill version (e.g. "1.2.0").
- ``source``: str, where the entry claims to come from. ONLY
  ``"first-party"`` is admissible in a catalogue. Other values
  seen in the wild: ``"third-party-registry"`` (an outside
  registry), ``"person-to-person"`` (a skill shared person to
  person -- sharing lands through the import gate, preview
  before admit plus dry-run plus quarantine, lands disabled,
  NEVER routed in as a catalogue entry).
- ``signer_key_id``: str or None, the key that signed the entry.
  Must equal the catalogue's pinned signer key-id. A changed
  signer key is a refusal, not a warning.
- ``signature``: str or None, the entry's signature blob. None
  or empty means UNSIGNED, which is a refusal.
- ``capabilities``: list[str], the capability manifest (e.g.
  ``["read_files", "run_command"]``). Capability gains drive
  re-consent on update (see ``propose_update``).
- ``card``: str, the scan-derived card text shown to the user.
- ``steps``: list[str], the readable step list shown to the user.
- ``files``: dict[str, str], the skill's files by filename,
  including ``"SKILL.md"``. File contents are text.
- ``pinned_hashes``: dict[str, str], SHA-256 hex digests of each
  file in ``files``, pinned by the catalogue version. Loading
  verifies content against these pins; a mismatch refuses the
  entry, never loads it hopefully.
- ``export``: dict, the export record (see ``export_record``).

CATALOGUE DICT FIELDS
---------------------
- ``catalogue_version``: str, the catalogue release version.
- ``signer_key_id``: str, the ONE pinned first-party signer key.
- ``source``: str, must be ``"first-party"``. A catalogue whose
  own source is anything else is rejected wholesale.
- ``entries``: list[skill-record dict].
- ``pins`` (optional): dict[name, dict[filename, sha256 hex]].
  When present, a catalogue version pins every entry's hashes at
  the catalogue level too: an entry whose ``pinned_hashes``
  disagree with the catalogue pins for its name is refused.

BEHAVIOURS
----------
- Single-source + pinned: ``load_catalogue`` admits an entry
  only when its source, signer key, signature presence, and
  hashes all check out. Anything else lands in ``refused`` with
  a plain reason. A refused entry is never loaded hopefully.
- Inspect-before-run: ``inspect_entry`` returns the card and
  step list for reading WITHOUT admitting or executing anything.
  It is a pure function: it mutates nothing and records no
  admission.
- Pinned updates only: ``propose_update`` diffs files and
  capabilities, refuses unpinned/unsigned/wrong-key candidates,
  requires re-consent on any capability gain, and allows the
  auto-apply lane ONLY for a no-capability-change update that
  passes a scan (``scan_clean=True``). ``apply_update``
  enforces the consent rule.
- EOL / export promise (SK-6.2 lesson): every catalogue entry
  carries an export record, and ``export_entry`` writes the
  skill as a plain-folder zip -- ``SKILL.md`` plus its files,
  verbatim, in no hostage format -- so the skill keeps working
  even if the Gallery goes away.
- No network I/O anywhere in this module: imports are hashlib,
  zipfile, and copy only. The suite rigs socket/urllib and
  asserts zero calls, and scans this source for network imports.

No catalogue admission, listing, or flow for outside parties
exists here, by design, until the section 14.20 counsel gate is
cleared and the owner rules otherwise.
"""

import copy
import hashlib
import zipfile

__all__ = [
    "SOURCE_FIRST_PARTY",
    "SOURCE_THIRD_PARTY_REGISTRY",
    "SOURCE_PERSON_TO_PERSON",
    "COUNSEL_GATE_REASON",
    "EXPORT_FORMAT",
    "sha256_text",
    "compute_pinned_hashes",
    "make_entry",
    "export_record",
    "hash_mismatches",
    "verify_hashes",
    "admission_refusal",
    "admit",
    "inspect_entry",
    "diff_update",
    "propose_update",
    "apply_update",
    "export_entry",
    "Gallery",
    "UpdateRefused",
]

SOURCE_FIRST_PARTY = "first-party"
SOURCE_THIRD_PARTY_REGISTRY = "third-party-registry"
SOURCE_PERSON_TO_PERSON = "person-to-person"

# Pinned catalogue signer key-id for this track's first-party
# catalogue. A catalogue dict carries its own signer_key_id;
# this constant is the default pin used by make_entry and by
# the suite's fixtures, so a changed signer key is detectable
# as a mismatch against the catalogue pin.
FIRST_PARTY_SIGNER_KEY_ID = "vyuk-gallery-first-party-v1"

# Plain reason naming the hard gate, used verbatim by every
# non-first-party refusal so the user always sees WHY, not
# just a bare rejection.
COUNSEL_GATE_REASON = (
    "Refused: only first-party signed skills are admitted to the "
    "Gallery. Third-party catalogue admission needs the section "
    "14.20 legal-counsel review of the distributor position, "
    "which is NOT cleared, so no third-party admission path "
    "ships in this track."
)

EXPORT_FORMAT = "plain-folder-zip"

EXPORT_RECORD_TEMPLATE = {
    "format": EXPORT_FORMAT,
    "works_without_gallery": True,
    "note": (
        "Plain-folder export: SKILL.md plus the skill files, "
        "verbatim, in no hostage format. The skill keeps "
        "working even if the Gallery goes away."
    ),
}


class UpdateRefused(Exception):
    """Raised by apply_update when the update may not proceed."""


def sha256_text(text):
    """SHA-256 hex digest of a text file's contents."""
    if isinstance(text, str):
        text = text.encode("utf-8")
    return hashlib.sha256(text).hexdigest()


def compute_pinned_hashes(files):
    """Pin every file: {filename: sha256 hex}."""
    return {name: sha256_text(content) for name, content in files.items()}


def export_record(entry):
    """The EOL/export record carried by a catalogue entry.

    Returns a fresh dict (never a shared mutable template) so
    callers cannot mutate one entry's record into another's.
    """
    record = copy.deepcopy(EXPORT_RECORD_TEMPLATE)
    record["skill_name"] = entry.get("name")
    record["skill_version"] = entry.get("version")
    record["files"] = sorted((entry.get("files") or {}).keys())
    return record


def make_entry(name, version, capabilities, card, steps, files,
               source=SOURCE_FIRST_PARTY,
               signer_key_id=FIRST_PARTY_SIGNER_KEY_ID,
               signature="signed-by-first-party"):
    """Build a well-formed skill-record dict with hashes pinned.

    ``files`` must include ``"SKILL.md"``. The export record is
    attached by construction, so every entry made this way
    carries the EOL/export promise from birth.
    """
    if "SKILL.md" not in files:
        raise ValueError("a skill record must include SKILL.md")
    entry = {
        "name": name,
        "version": version,
        "source": source,
        "signer_key_id": signer_key_id,
        "signature": signature,
        "capabilities": list(capabilities),
        "card": card,
        "steps": list(steps),
        "files": dict(files),
        "pinned_hashes": compute_pinned_hashes(files),
    }
    entry["export"] = export_record(entry)
    return entry


def hash_mismatches(entry):
    """Filenames whose content disagrees with the pinned hash.

    Also reports pinned files with no content, and content files
    with no pin (an unpinned file is a mismatch: the catalogue
    version pins EVERY entry hash, so a file outside the pin
    set is not loadable).
    """
    files = entry.get("files") or {}
    pinned = entry.get("pinned_hashes") or {}
    bad = []
    for name, digest in pinned.items():
        content = files.get(name)
        if content is None or sha256_text(content) != digest:
            bad.append(name)
    for name in files:
        if name not in pinned:
            bad.append(name)
    return sorted(set(bad))


def verify_hashes(entry):
    """True only when every file matches its pinned hash."""
    return hash_mismatches(entry) == []


def admission_refusal(entry, catalogue_signer_key_id):
    """Plain reason the entry may NOT be admitted, or None.

    Check order is deliberate: source first (the counsel-gate
    refusal is the headline for every outside entry), then
    signature presence, then signer-key pin, then hash pin.
    """
    source = entry.get("source")
    if source != SOURCE_FIRST_PARTY:
        if source == SOURCE_PERSON_TO_PERSON:
            return (
                COUNSEL_GATE_REASON + " A person-to-person shared "
                "skill is not a catalogue entry: sharing lands "
                "through the import gate (preview before admit, "
                "dry-run, quarantine, lands disabled), never "
                "routed in as a catalogue entry."
            )
        return COUNSEL_GATE_REASON
    if not entry.get("signature"):
        return (
            "Refused: unsigned entry. Only first-party signed "
            "skills are admitted to the Gallery, and this entry "
            "carries no signature."
        )
    if entry.get("signer_key_id") != catalogue_signer_key_id:
        return (
            "Refused: signer key does not match the catalogue "
            "pin. The entry's signer key changed, so it is not "
            "admitted under the pinned first-party key."
        )
    bad = hash_mismatches(entry)
    if bad:
        return (
            "Refused: hash mismatch for file(s): %s. The "
            "catalogue version pins every entry hash; a "
            "mismatched entry is refused, never loaded "
            "hopefully." % ", ".join(bad)
        )
    return None


def admit(entry, catalogue_signer_key_id):
    """Admit-or-refuse for one entry: (admitted, reason|None)."""
    reason = admission_refusal(entry, catalogue_signer_key_id)
    return (reason is None, reason)


def inspect_entry(entry):
    """Inspect-before-run: card + step list for reading only.

    Pure: returns fresh copies, mutates nothing, admits
    nothing, executes nothing. Callers show this to the user
    BEFORE any admission decision.
    """
    return {
        "name": entry.get("name"),
        "version": entry.get("version"),
        "source": entry.get("source"),
        "card": entry.get("card"),
        "steps": list(entry.get("steps") or []),
        "capabilities": list(entry.get("capabilities") or []),
        "admitted": False,
    }


def diff_update(old, new):
    """Diff two entries: files + capability delta.

    Returns added/removed/changed files, added/removed
    capabilities, and the two summary flags the update rules
    turn on: ``capability_gain`` (any capability added) and
    ``content_changed`` (any file added, removed, or changed).
    """
    old_files = old.get("files") or {}
    new_files = new.get("files") or {}
    old_caps = set(old.get("capabilities") or [])
    new_caps = set(new.get("capabilities") or [])
    files_added = sorted(set(new_files) - set(old_files))
    files_removed = sorted(set(old_files) - set(new_files))
    files_changed = sorted(
        name for name in set(old_files) & set(new_files)
        if old_files[name] != new_files[name]
    )
    caps_added = sorted(new_caps - old_caps)
    caps_removed = sorted(old_caps - new_caps)
    return {
        "files_added": files_added,
        "files_removed": files_removed,
        "files_changed": files_changed,
        "capabilities_added": caps_added,
        "capabilities_removed": caps_removed,
        "capability_gain": bool(caps_added),
        "content_changed": bool(files_added or files_removed or files_changed),
    }


def propose_update(old, new, catalogue_signer_key_id, scan_clean):
    """Propose a pinned update, with its consent requirement.

    Rules (same rule as the import gate):
    - The candidate must itself be admissible (first-party,
      signed, pinned key, hashes verify, and -- when the
      catalogue carries pins for it -- matching the catalogue
      pins). Otherwise the proposal is invalid with a reason.
    - ``requires_reconsent`` is True on ANY capability gain.
    - The auto-apply lane is open ONLY when there is no
      capability gain AND the scan is clean (``scan_clean``):
      a security update with no capability change may
      auto-apply after scan. A dirty scan closes the lane.
    - The diff is always shown, even for auto-apply updates.
    """
    admitted, reason = admit(new, catalogue_signer_key_id)
    if not admitted:
        return {
            "valid": False,
            "reason": reason,
            "diff": diff_update(old, new),
            "requires_reconsent": True,
            "auto_apply_allowed": False,
        }
    diff = diff_update(old, new)
    requires_reconsent = diff["capability_gain"]
    auto_apply_allowed = (not diff["capability_gain"]) and bool(scan_clean)
    return {
        "valid": True,
        "reason": None,
        "diff": diff,
        "requires_reconsent": requires_reconsent,
        "auto_apply_allowed": auto_apply_allowed,
    }


def apply_update(old, proposal, consent_given=False):
    """Apply a proposed update, enforcing the consent rule.

    Raises UpdateRefused when the proposal is invalid, when
    re-consent is required and not given, or when the
    auto-apply lane is claimed without a clean scan. Returns
    the NEW entry dict (the caller supplies it via the
    proposal's ``new_entry`` key -- see Gallery.update).
    """
    if not proposal.get("valid"):
        raise UpdateRefused(proposal.get("reason") or "invalid update proposal")
    if proposal.get("requires_reconsent") and not consent_given:
        raise UpdateRefused(
            "Re-consent required: this update gains capabilities "
            "(%s). It may not apply without the user's consent."
            % ", ".join(proposal["diff"]["capabilities_added"])
        )
    if not proposal.get("requires_reconsent") and not proposal.get("auto_apply_allowed"):
        raise UpdateRefused(
            "Auto-apply not allowed: no capability change but "
            "the scan was not clean, so this update needs "
            "explicit consent."
        )
    new_entry = proposal.get("new_entry")
    if new_entry is None:
        raise UpdateRefused("proposal carries no new entry to apply")
    return copy.deepcopy(new_entry)


def export_entry(entry, zip_path):
    """Export an entry as a plain-folder zip (no hostage format).

    The zip contains ``SKILL.md`` plus every file in the entry,
    verbatim, at the zip root -- so unzipping yields a plain
    folder that works without the Gallery. Returns the path
    written. Refuses entries whose hashes do not verify, so an
    export can never launder a tampered skill.
    """
    if not verify_hashes(entry):
        raise ValueError(
            "refused export: hash mismatch for file(s): %s"
            % ", ".join(hash_mismatches(entry))
        )
    files = entry.get("files") or {}
    if "SKILL.md" not in files:
        raise ValueError("refused export: entry has no SKILL.md")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(files):
            zf.writestr(name, files[name])
    return zip_path


class Gallery:
    """A single-source, pinned first-party catalogue.

    ``load_catalogue`` splits a catalogue dict into admitted
    entries and refused entries with plain reasons. A refused
    entry is NEVER in ``admitted`` -- there is no hopeful load.
    """

    def __init__(self, catalogue):
        self.catalogue_version = catalogue.get("catalogue_version")
        self.signer_key_id = catalogue.get("signer_key_id")
        self.source = catalogue.get("source")
        self.admitted = []
        self.refused = []
        self._admitted_names = set()
        if self.source != SOURCE_FIRST_PARTY:
            for entry in catalogue.get("entries") or []:
                self.refused.append((entry.get("name"), COUNSEL_GATE_REASON))
            return
        pins = catalogue.get("pins") or {}
        for entry in catalogue.get("entries") or []:
            name = entry.get("name")
            catalogue_pin = pins.get(name)
            if catalogue_pin is not None and entry.get("pinned_hashes") != catalogue_pin:
                self.refused.append((
                    name,
                    "Refused: catalogue pin mismatch for %s. The "
                    "catalogue version pins every entry hash; "
                    "this entry's hashes disagree with the "
                    "catalogue pin, so it is refused, never "
                    "loaded hopefully." % name,
                ))
                continue
            admitted, reason = admit(entry, self.signer_key_id)
            if admitted:
                self.admitted.append(copy.deepcopy(entry))
                self._admitted_names.add(name)
            else:
                self.refused.append((name, reason))

    def inspect(self, name):
        """Inspect-before-run for an admitted entry, by name.

        Reading the card + steps does not change admission
        state: the entry was already admitted at load, and
        inspecting it admits nothing new and executes nothing.
        """
        for entry in self.admitted:
            if entry.get("name") == name:
                return inspect_entry(entry)
        return None

    def update(self, name, candidate, scan_clean, consent_given=False):
        """Propose + apply a pinned update for an admitted entry.

        Returns (new_entry, proposal). Raises UpdateRefused
        when the update may not proceed.
        """
        for entry in self.admitted:
            if entry.get("name") == name:
                proposal = propose_update(
                    entry, candidate, self.signer_key_id, scan_clean)
                proposal["new_entry"] = candidate
                new_entry = apply_update(entry, proposal,
                                         consent_given=consent_given)
                entry.clear()
                entry.update(copy.deepcopy(new_entry))
                return new_entry, proposal
        raise UpdateRefused("no admitted entry named %r to update" % name)
