"""P19 travel + P20 PILOT + P23 recipes/scoreboard, Wave 9
Build 9 — descriptors and records only.

Per build_flow_v6.md Wave 9: "P19 travel-for-every-body
and door-to-door planner (non-commercial-routing form),
P20 hologram and spatial-desktop PILOT slices, P23
shareable recipes and open scoreboard (as graduation
evidence surfaces)." PILOT/BUILD here means descriptor
and record forms only: nothing is booked, nothing is
paid, nothing is routed commercially, nothing is driven,
nothing is rendered, nothing is moved, and no claim goes
beyond what the caller supplied.

WHAT THIS MODULE IS
-------------------
Three descriptor families, all caller-supplied, all
records only:
* TRAVEL. A trip descriptor: caller-supplied legs only,
  in the shipped trip-planner shape — no legs means NO
  trip, NOT_COMPUTED — with a caller-supplied
  accessibility note (never inferred), caller-supplied
  pack items (P19-19 smart packing, checklist labels
  only; P19-14 trip pack, contents labels only), and a
  caller-supplied parking note (P19-18 parking and
  last-mile memory, saved only when asked, as a label
  the caller gave). A door-to-door plan descriptor:
  caller-supplied legs in order, a plan record out,
  with a plain note that commercial routing is NOT
  used here and no route was computed by a routing
  service (P19-12 door-to-door planner,
  non-commercial-routing form; P19-20 travel for every
  body, step-free/quiet/slower options as caller
  supplied labels, never inferred). A transport-handler
  descriptor: a caller-supplied transport mode label in,
  a handler note out, never a vehicle control, never a
  driver (P19 chauffeur position: transport-handler
  yes, literal driver no). A driving mode label returns
  the driving-mode note only: voice-only, short
  answers, OFF by default, local law first, navigation
  stays in the maps app. A safe-arrival label returns
  the safe-arrival note only: NOT an emergency-services
  replacement, and it says plainly to call emergency
  services when it is serious (P19-21).
* SPATIAL. A loadout descriptor: a caller-supplied
  loadout name plus capability labels plus rule labels
  in, a record out that lists exactly what it allows,
  applied False, restored False — suiting up and
  suiting down are actions, not built here (P20-1
  loadouts, non-hardware form). A hologram/scene
  descriptor: caller-supplied scene labels in, a scene
  record out, hardware False, rendered False — a
  description, not a render; a hologram note states
  that Pepper's ghost is a classic illusion, not a true
  hologram (P20-6). A spatial-desktop descriptor:
  caller-supplied zone labels in, a layout record out,
  read_live False, never a live desktop read, with
  optional caller-supplied augmented labels (P20-7
  spatial desktop, layout-record form; P20-9 augmented
  labels, label records only — a confidence value is
  refused, since recognition confidence must come from
  a recogniser, not a descriptor). A sonification
  descriptor: caller-supplied state labels plus cue
  labels in, a cue-map record out, played False, audio
  False — a map, not a sound (P20-10 sonification,
  non-hardware form; the map notes quiet, optional,
  and off in calls).
* RECIPES/SCOREBOARD. A shareable-recipe descriptor:
  caller-supplied recipe steps plus source as supplied
  in, a recipe record out, provenance on every step —
  a sourceless step refuses whole, and a step flagged
  personal refuses whole, since a shareable recipe
  carries no personal data (P23-25 shareable recipes;
  the review step and permission summary before
  install, and any creator marketplace, are NOT built
  here — marketplace-later touches tension T2, which
  is recorded, not decided). An open-scoreboard
  descriptor: caller-supplied graduation-evidence
  entries only — item plus evidence reference as
  supplied — in, a scoreboard record out, never an
  invented score, never a claim beyond the supplied
  evidence — any numeric score or rate field refuses
  whole, since a score must come from a bench
  pipeline, not a descriptor (P23-26 open scoreboard,
  as graduation evidence surfaces).
* A re-validation rule for recording a travel,
  spatial, or recipe descriptor: the descriptor is
  re-built from its own fields and must match exactly,
  or it is refused, never written on trust.

WHAT THIS MODULE IS NOT — HARD REFUSALS
---------------------------------------
Any call carrying a refused kind of input refuses WHOLE,
never with a partial answer:
* any booking, purchase, payment, or message-send
  action — an action parameter other than "describe",
  or a step/leg label shaped as one of those actions,
  refuses the whole call; a booked/paid/sent claim
  flag refuses whole too. Payments, bookings, and
  messages stay Tier 2 plus confirmation, and no such
  surface is built here (ruling 34 still OPEN);
* any vehicle control or driving action — a control
  word (steer, accelerate, brake, autopilot,
  self-driving) refuses whole; a transport mode label
  is a label, never a command, and the module's own
  outputs always carry controlled False;
* any commercial-routing request or claim — a routed
  or route-computed flag, a routing-service name, or
  a claim a route was computed refuses whole; every
  door-to-door output carries routed False and
  commercial_routing_used False. Commercial routing
  stays PARKED under the GraphHopper position (its
  free tier is NON-COMMERCIAL, dev/test only, never
  shipped as a commercial feature), and no routing
  service is called, named as used, or depended on
  here;
* any safety-critical sizing, load, or sign-off claim
  — "will hold", "load-bearing", "sign-off",
  "certified safe", "rated for", "maximum load", or
  "safety-critical" refuses whole (P20-3 scan to 3D
  and P20-4 virtual wind tunnel stay unbuilt: a scan
  must state its error margin and never size
  safety-critical parts, and a wind tunnel is
  intuition only, never sign-off);
* any fake attack, fake pin, or fake data — refuses
  whole (P20-11 shield HUD is NOT built here: it
  needs real log entries, and a descriptor of invented
  blocked items would be exactly the fake it must
  never show);
* any hardware movement — refuses whole (P20
  hardware-moving slices stay GATED on Tier 2 plus an
  independent physical stop);
* any scoreboard entry without a supplied evidence
  reference, or any sourceless recipe step — refuses
  whole. Nothing is invented here, ever.
The module imports no vehicle, payment, routing-service,
or hardware code — proven by the AST test in
tests/test_travel_w9.py. It books nothing, pays
nothing, routes nothing commercially, drives nothing,
renders nothing, and moves nothing: there is nothing
here to do any of that with, by construction.
Descriptors only, records only.

P19 / P20 / P23 SLICES NOT BUILT HERE, NAMED NOT DROPPED
--------------------------------------------------------
* P19-1 mission brief, P19-2 delegation dial — not
  rebuilt here; the Wave 6 mission and delegation
  shapes already cover them, and this build does not
  duplicate shipped code.
* P19-3 valet keys — not built; a limited-use
  permission against another service needs a service
  integration, not a descriptor.
* P19-4 hand-off packet — not built here; the Wave 6
  hand-off packet shape already covers it.
* P19-5 receipts with evidence — not built; a receipt
  needs a probe confirmation, and this module probes
  nothing.
* P19-7 quote and compare — not built; live sources
  plus a Tier 2 purchase flow, both absent here.
* P19-8 obligations radar — not built; calendar and
  subscription reads are live reads.
* P19-9 inbox chief of staff — not built; mail reads
  and sends are live actions.
* P19-10 standing orders with expiry — not built; a
  rule engine with replay against a past week is not
  a descriptor.
* P19-13 leave-now nudge — not built; live sources
  plus a scheduled nudge are actions, not descriptors.
* P19-15 disruption handling — not built; live flight
  data plus rebooking options are live actions.
* P19-16 commute briefing — not built; live meetings,
  delays, and weather are live sources.
* P19-17 share my ETA — not built; live location
  sharing needs an opt-in grant plus a location
  pipeline, not a descriptor.
* P20-2 update ceremony — not built here; the Wave 8
  Track L release checklist already covers the
  gate-shaped part, and the snapshot/rollback action
  is not a descriptor.
* P20-3 scan to 3D — not built; photogrammetry plus
  a phone camera plus an error margin is hardware
  plus capture, and safety-critical sizing is refused.
* P20-4 virtual wind tunnel — not built; a solver
  plus a GPU is not a descriptor, and sign-off is
  refused.
* P20-5 text to 3D sketches — not built; model
  generation plus a licence check owed per model.
* P20-8 command mirror — not built here; a paired
  display over the user's own network needs pairing
  handling, and an open port is never built.
* P20-11 shield HUD — not built here; it needs real
  log entries, and invented blocked items would be
  fake attacks.
* P20-12 house party protocol — not built; parallel
  helpers under one budget need goal-loop wiring.
* P20-13 build time-lapse — not built; a bench
  camera plus capture is not a descriptor.
* P20-14 scan, edit, print loop — not built; scan
  plus print are hardware actions under Tier 2 plus
  a physical stop.
* P23-1 signed everything, P23-2 install routes,
  P23-3 verifiable releases, P23-4 release train —
  not built here; signing is DEFERRED-SPEND, the
  installer is authored never compiled, and the feed
  has no host (Wave 10 commercial close, not Wave 9).
* P23-5 clean exit, P23-6 three-minute first command,
  P23-7 no-setup mode, P23-8 fake-data playground,
  P23-9 fix-my-setup button — not built here; each
  needs product behaviour (uninstall, first-run,
  offline mode, practice data, diagnostics), not a
  descriptor.
* P23-10 clear tiers, P23-11 trial and refunds,
  P23-12 licensing that respects users, P23-13
  payments and tax — not built; ruling 34 on pricing,
  EULA, and support is still OPEN, and no payment
  surface exists.
* P23-14 teams and organisations — not built; ORG
  scope stays PARKED unless the owner rules the scope
  changed to multi-user.
* P23-15 trust centre, P23-16 honest legal pack,
  P23-17 biometric and recording consent, P23-18 AI
  transparency, P23-19 accessibility conformance —
  not built here; each needs a qualified-professional
  review or a conformance evidence pipeline, and no
  conformance claim is made by this module.
* P23-20 in-app help with diagnostic bundle, P23-21
  known-issues feed, P23-22 support runbook, P23-23
  plain-language changelog, P23-24 one audience
  first, P23-27 partnerships — not built here; each
  needs live product behaviour, real tickets, or real
  users, none of which a descriptor supplies.
"""

from __future__ import annotations  # INTENTIONAL (travel split B1):
# the old module carried this future import, which binds the name
# `annotations` in the module namespace; the facade keeps it so the
# dir diff against the old module is empty.

import re  # INTENTIONAL (travel split B1): the old module's stdlib
# import, re-exported so the facade namespace matches the old one.

# INTENTIONAL (travel split B1): every name the old module defined
# is re-exported from the impl module that now owns it, so existing
# module-attribute importers (travel_tools, test_travel_w9) work
# unchanged and with identical function objects.
from .travel_checks import (
    _ACTION_WORDS,
    _CLAIM_PHRASES,
    _FAKE_PHRASES,
    _ROUTING_WORDS,
    _SAFETY_PHRASES,
    _check_action,
    _check_labels,
    _check_leg,
    _check_legs,
    _check_text,
    _dedup,
    _refuse,
    _words,
)
from .travel_builders import (
    _BUILDERS,
    build_door_to_door,
    build_loadout,
    build_recipe,
    build_scene,
    build_scoreboard,
    build_sonify,
    build_spatial,
    build_transport,
    build_trip,
)
from .travel_validate import validate_recordable_travel
