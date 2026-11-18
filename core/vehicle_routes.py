"""Advanced vehicle routing: time-windowed routes, pickup-and-delivery,
multi-depot fleets and vehicle skills — own stdlib Python.

Inspiration: VROOM-Project/vroom ("Vehicle Routing Open-source
Optimization Machine", C++20, BSD-2-Clause). Vroom solves TSP, CVRP,
VRPTW, MDHVRPTW (multi-depot heterogeneous vehicle VRPTW) and PDPTW
(pickup-and-delivery with time windows) in milliseconds, modelling
vehicles (capacity, skills, working hours, breaks, per-vehicle start
and end), jobs (amounts, service time windows, service duration,
skills, priority) and shipments (a pickup and a delivery that must
happen on the same route).

Licence: BSD-2-Clause — "Parent-ruled (licence, 2026-11-12)" (the Vroom LICENSE,
read in full from master: 1,296 bytes, 24 lines, copyright
"Copyright (c) 2015-2025, Julien Coupey"). Nothing is adopted — no C++
code, no Vroom binary, no library, no wrapper, no port. The module
takes only the routing shapes (vehicles, jobs, shipments, time
windows, skills, priority, breaks) as inspiration in own stdlib code.

What is NOT built (STOP, stated on the tin):
  * NO Vroom, no C++, no binary, no library — own Python heuristic.
  * NO routing engine (no OSRM / Openrouteservice / Valhalla, no
    network call anywhere) — the operator supplies the travel-time
    matrix, or points and the tool computes Euclidean distances.
  * NO live traffic, no real road network — the matrix is the
    operator's own numbers; the time windows are in the matrix's
    units.
  * NO multi-user, no hosted anything, no server.
  * The solver is a HEURISTIC (cheapest insertion + 2-opt), not
    exact — the answer says so. It does not guarantee the optimal
    route. Every route it returns is FEASIBLE (capacity, time
    windows, skills, working hours, breaks and pickup-before-
    delivery all hold — the same feasibility function that builds
    the routes re-checks them before the result is claimed), and
    jobs that cannot be served are reported as unassigned with the
    reason, never silently dropped.

The model (Vroom's, as inspiration):
  * Vehicles — capacity (per vehicle, so the fleet is heterogeneous),
    skills (a set; a vehicle serves a job only when it has every
    skill the job needs), start and end depots (per vehicle, may
    differ — an open trip has only a start or only an end), working
    hours (start_time / end_time), breaks (time windows when the
    vehicle neither departs nor serves).
  * Jobs — a location (a node), a demand, a service duration, a time
    window [earliest, latest] (the service must START inside it;
    arriving early means waiting), skills, a priority (higher is
    served first).
  * Shipments — a pickup job and a delivery job that must ride the
    same route, pickup before delivery.
  * One matrix is both the travel time and the cost (the time windows
    are in the matrix's units). Points are an alternative to a matrix:
    the tool computes Euclidean distances.

Bounds (refused honestly, with the numbers): at most 200 points; at
most 50 vehicles; at most 200 jobs; at most 100 shipments. A job no
vehicle can carry is reported as unassigned with the reason, never
silently dropped.

Free by design (app-state): the operator supplies every number, the
tool only computes and records the plan in its own hash-chained
ledger. Reads (status, events) never create the store file.
"""

import hashlib
import json
import math
import sqlite3
import threading
import time
from pathlib import Path

from . import store_base

PROPOSED_MARKER = "Parent-ruled (licence, 2026-11-12)"

_ACTIONS = ("plan", "status", "events")

_MAX_POINTS = 200
_MAX_VEHICLES = 50
_MAX_JOBS = 200
_MAX_SHIPMENTS = 100
_MAX_EVENTS = 100

_LOCK = threading.Lock()


def _db_path():
    from core.config import DATA_DIR

    return Path(DATA_DIR) / "vehicle_routes.sqlite3"


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(float(v))


def _fmt_num(v):
    if isinstance(v, float):
        if abs(v - round(v)) < 1e-9:
            return str(int(round(v)))
        return "%.6g" % v
    return str(v)


def _parse_points(value):
    """A list of [x, y] pairs -> a Euclidean distance matrix. Returns
    None on bad input, "too-many" when over the point bound."""
    if not isinstance(value, list) or not value:
        return None
    if len(value) > _MAX_POINTS:
        return "too-many"
    pts = []
    for p in value:
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            return None
        if not _is_num(p[0]) or not _is_num(p[1]):
            return None
        pts.append((float(p[0]), float(p[1])))
    n = len(pts)
    return [[0.0 if i == j else
             math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1])
             for j in range(n)] for i in range(n)]


def _parse_matrix(value):
    """A square matrix of non-negative numbers, zeros on the diagonal.
    Returns None on bad input, "too-many" when over the point bound."""
    if not isinstance(value, list) or not value:
        return None
    if len(value) > _MAX_POINTS:
        return "too-many"
    n = len(value)
    out = []
    for row in value:
        if not isinstance(row, list) or len(row) != n:
            return None
        r = []
        for v in row:
            if not _is_num(v) or float(v) < 0:
                return None
            r.append(float(v))
        out.append(r)
    for i in range(n):
        if abs(out[i][i]) > 1e-9:
            return None
    return out


def _clean_str(v, what, limit=120, required=False, default=None):
    if v is None:
        if required:
            raise _Refusal("%s is required" % what)
        return default
    if not isinstance(v, str):
        raise _Refusal("%s must be a string" % what)
    s = v.strip()
    if required and not s:
        raise _Refusal("%s is required" % what)
    if len(s) > limit:
        raise _Refusal("%s is at most %d characters" % (what, limit))
    return s


def _clean_num(v, what, lo=None, hi=None, required=False, default=None):
    if v is None:
        if required:
            raise _Refusal("%s is required" % what)
        return default
    if not _is_num(v):
        raise _Refusal("%s must be a number" % what)
    f = float(v)
    if lo is not None and f < lo - 1e-9:
        raise _Refusal("%s must be at least %s" % (what, _fmt_num(lo)))
    if hi is not None and f > hi + 1e-9:
        raise _Refusal("%s must be at most %s" % (what, _fmt_num(hi)))
    return f


class _Refusal(Exception):
    """An honest refusal: the input is bad or the problem is infeasible
    before any solving starts. Carries the reason, with the numbers."""


def _parse_skills(value, what):
    if value is None:
        return []
    if not isinstance(value, list):
        raise _Refusal("%s must be a list of skill names" % what)
    out = []
    for s in value:
        if not isinstance(s, str) or not s.strip():
            raise _Refusal("%s must be a list of skill names" % what)
        if len(s.strip()) > 60:
            raise _Refusal("%s skill names are at most 60 characters"
                           % what)
        out.append(s.strip())
    if len(set(out)) != len(out):
        raise _Refusal("%s has a repeated skill" % what)
    return out


def _parse_breaks(value, what):
    if value is None:
        return []
    if not isinstance(value, list):
        raise _Refusal("%s must be a list of [start, end] pairs" % what)
    out = []
    for b in value:
        if not isinstance(b, (list, tuple)) or len(b) != 2:
            raise _Refusal("%s must be a list of [start, end] pairs"
                           % what)
        if not _is_num(b[0]) or not _is_num(b[1]):
            raise _Refusal("%s breaks must be numbers" % what)
        s, e = float(b[0]), float(b[1])
        if s < 0 or e <= s + 1e-9:
            raise _Refusal("%s breaks need 0 <= start < end" % what)
        out.append((s, e))
    out.sort()
    for i in range(1, len(out)):
        if out[i][0] < out[i - 1][1] - 1e-9:
            raise _Refusal("%s breaks must not overlap" % what)
    return out


def _parse_vehicles(value, n_nodes):
    """Validate the vehicles list. Returns a list of vehicle dicts."""
    if not isinstance(value, list) or not value:
        raise _Refusal("vehicles must be a non-empty list")
    if len(value) > _MAX_VEHICLES:
        raise _Refusal("at most %d vehicles" % _MAX_VEHICLES)
    vehicles = []
    seen_ids = set()
    for idx, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise _Refusal("vehicle %d must be an object" % (idx + 1))
        vid = _clean_str(raw.get("id"), "vehicle id", limit=60)
        if vid is None:
            vid = "V%d" % (idx + 1)
        if vid in seen_ids:
            raise _Refusal("vehicle id %r is repeated" % vid)
        seen_ids.add(vid)
        cap = _clean_num(raw.get("capacity"), "vehicle %s capacity" % vid,
                         lo=0, required=True)
        if cap <= 0:
            raise _Refusal("vehicle %s capacity must be positive" % vid)
        skills = _parse_skills(raw.get("skills"), "vehicle %s" % vid)
        start = raw.get("start", 0)
        end = raw.get("end", start)
        if not isinstance(start, int) or isinstance(start, bool):
            raise _Refusal("vehicle %s start must be a node index"
                           % vid)
        if not isinstance(end, int) or isinstance(end, bool):
            raise _Refusal("vehicle %s end must be a node index" % vid)
        if not (0 <= start < n_nodes) or not (0 <= end < n_nodes):
            raise _Refusal("vehicle %s start/end must be node indexes"
                           " 0..%d" % (vid, n_nodes - 1))
        start_time = _clean_num(raw.get("start_time"),
                                "vehicle %s start_time" % vid,
                                lo=0, default=0.0)
        end_time = _clean_num(raw.get("end_time"),
                              "vehicle %s end_time" % vid,
                              lo=0, default=None)
        if end_time is not None and end_time <= start_time + 1e-9:
            raise _Refusal("vehicle %s needs start_time < end_time"
                           % vid)
        breaks = _parse_breaks(raw.get("breaks"), "vehicle %s" % vid)
        vehicles.append({
            "id": vid, "capacity": cap, "skills": skills,
            "start": start, "end": end, "start_time": start_time,
            "end_time": end_time, "breaks": breaks,
        })
    return vehicles


def _parse_jobs(value, n_nodes):
    """Validate the jobs list. Returns (jobs, jobs_by_id)."""
    if not isinstance(value, list) or not value:
        raise _Refusal("jobs must be a non-empty list")
    if len(value) > _MAX_JOBS:
        raise _Refusal("at most %d jobs" % _MAX_JOBS)
    jobs = []
    by_id = {}
    for idx, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise _Refusal("job %d must be an object" % (idx + 1))
        jid = _clean_str(raw.get("id"), "job id", limit=60)
        if jid is None:
            jid = "J%d" % (idx + 1)
        if jid in by_id:
            raise _Refusal("job id %r is repeated" % jid)
        loc = raw.get("location")
        if not isinstance(loc, int) or isinstance(loc, bool):
            raise _Refusal("job %s location must be a node index" % jid)
        if not (0 <= loc < n_nodes):
            raise _Refusal("job %s location must be a node index 0..%d"
                           % (jid, n_nodes - 1))
        demand = _clean_num(raw.get("demand"), "job %s demand" % jid,
                            lo=0, default=0.0)
        service = _clean_num(raw.get("service"),
                             "job %s service" % jid, lo=0, default=0.0)
        window = raw.get("window")
        if window is not None:
            if not isinstance(window, (list, tuple)) or len(window) != 2:
                raise _Refusal("job %s window must be [earliest, latest]"
                               % jid)
            if not _is_num(window[0]) or not _is_num(window[1]):
                raise _Refusal("job %s window must be numbers" % jid)
            earliest, latest = float(window[0]), float(window[1])
            if earliest < 0 or latest < earliest - 1e-9:
                raise _Refusal("job %s window needs 0 <= earliest <="
                               " latest" % jid)
            window = (earliest, latest)
        skills = _parse_skills(raw.get("skills"), "job %s" % jid)
        priority = _clean_num(raw.get("priority"),
                              "job %s priority" % jid, default=0.0)
        job = {"id": jid, "location": loc, "demand": demand,
               "service": service, "window": window, "skills": skills,
               "priority": priority}
        jobs.append(job)
        by_id[jid] = job
    return jobs, by_id


def _parse_shipments(value, by_id):
    """Validate the shipments list. Returns a list of (pickup_id,
    delivery_id) pairs. A job may be in at most one shipment, as a
    pickup or a delivery, never both."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise _Refusal("shipments must be a list")
    if len(value) > _MAX_SHIPMENTS:
        raise _Refusal("at most %d shipments" % _MAX_SHIPMENTS)
    out = []
    used = set()
    for idx, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise _Refusal("shipment %d must be an object" % (idx + 1))
        pickup = _clean_str(raw.get("pickup"), "shipment pickup",
                            limit=60, required=True)
        delivery = _clean_str(raw.get("delivery"), "shipment delivery",
                              limit=60, required=True)
        if pickup == delivery:
            raise _Refusal("shipment %d pickup and delivery must differ"
                           % (idx + 1))
        if pickup not in by_id:
            raise _Refusal("shipment %d pickup %r is not a job"
                           % (idx + 1, pickup))
        if delivery not in by_id:
            raise _Refusal("shipment %d delivery %r is not a job"
                           % (idx + 1, delivery))
        for jid in (pickup, delivery):
            if jid in used:
                raise _Refusal("job %r is already in a shipment" % jid)
            used.add(jid)
        out.append((pickup, delivery))
    return out

# ---------------------------------------------------------------------------
# Solver — one feasibility function, used to build AND to verify.
# ---------------------------------------------------------------------------

def _advance_past_breaks(t, breaks):
    """The earliest time >= t at which the vehicle is not on a break."""
    changed = True
    while changed:
        changed = False
        for s, e in breaks:
            if s - 1e-9 <= t < e - 1e-9:
                t = e
                changed = True
                break
    return t


def _service_overlaps_break(start, duration, breaks):
    """The end of the break a service starting at `start` with the
    given duration would overlap, or None when it overlaps none."""
    end = start + duration
    for s, e in breaks:
        if start < e - 1e-9 and s < end - 1e-9:
            return e
    return None


def _route_timeline(route, vehicle, by_id, shipments, matrix):
    """Walk a route (a list of job ids in visit order) for one vehicle
    and compute its timeline. Returns (feasible, cost, timeline,
    reason). The timeline is a list of dicts with arrival,
    service_start, service_end and departure per job. This ONE
    function decides feasibility everywhere — insertion tries a
    candidate route through it, 2-opt accepts a move only through it,
    and the handler re-checks every finished route through it before
    the result is claimed. So a returned route is feasible by
    construction, and a bug in the timeline shows up as a refusal,
    never as a claimed infeasible route."""
    # Pickup before delivery: for every shipment with both jobs on
    # this route, the pickup must come first.
    pos = {jid: i for i, jid in enumerate(route)}
    for pickup, delivery in shipments:
        if pickup in pos and delivery in pos:
            if pos[pickup] >= pos[delivery]:
                return (False, None, None,
                        "shipment %s -> %s would deliver before pickup"
                        % (pickup, delivery))
    # Skills: the vehicle must have every skill every job needs.
    for jid in route:
        job = by_id[jid]
        missing = [s for s in job["skills"]
                   if s not in vehicle["skills"]]
        if missing:
            return (False, None, None,
                    "vehicle %s lacks the skills %s for job %s"
                    % (vehicle["id"], ", ".join(missing), jid))
    # Capacity: the load must fit the vehicle.
    load = sum(by_id[jid]["demand"] for jid in route)
    if load > vehicle["capacity"] + 1e-9:
        return (False, None, None,
                "route load %s exceeds vehicle %s capacity %s"
                % (_fmt_num(load), vehicle["id"],
                   _fmt_num(vehicle["capacity"])))
    breaks = vehicle["breaks"]
    now = _advance_past_breaks(vehicle["start_time"], breaks)
    node = vehicle["start"]
    cost = 0.0
    timeline = []
    for jid in route:
        job = by_id[jid]
        leg = matrix[node][job["location"]]
        cost += leg
        arrival = _advance_past_breaks(now + leg, breaks)
        earliest, latest = job["window"] if job["window"] else (None, None)
        start = arrival
        if earliest is not None and start < earliest - 1e-9:
            start = earliest
        # A service must not overlap a break: delay it to the end of
        # the break it would overlap, and re-check (breaks may abut).
        overlap = _service_overlaps_break(start, job["service"], breaks)
        while overlap is not None:
            start = overlap
            overlap = _service_overlaps_break(start, job["service"],
                                              breaks)
        if latest is not None and start > latest + 1e-9:
            return (False, None, None,
                    "job %s window closes at %s, service would start"
                    " at %s" % (jid, _fmt_num(latest), _fmt_num(start)))
        end = start + job["service"]
        timeline.append({"job": jid, "arrival": arrival,
                         "service_start": start, "service_end": end,
                         "departure": end})
        now = end
        node = job["location"]
    leg = matrix[node][vehicle["end"]]
    cost += leg
    if vehicle["end_time"] is not None:
        if now + leg > vehicle["end_time"] + 1e-9:
            return (False, None, None,
                    "route returns at %s, after vehicle %s working"
                    " hours end %s"
                    % (_fmt_num(now + leg), vehicle["id"],
                       _fmt_num(vehicle["end_time"])))
    return (True, cost, timeline, None)


def _unassigned_reason(unit_jobs, vehicles):
    """Why a unit (a list of jobs) could not be placed anywhere."""
    need_skills = set()
    for job in unit_jobs:
        need_skills.update(job["skills"])
    if need_skills:
        if not any(need_skills <= set(v["skills"]) for v in vehicles):
            return ("no vehicle has the required skills: %s"
                    % ", ".join(sorted(need_skills)))
    total_demand = sum(j["demand"] for j in unit_jobs)
    if total_demand > 1e-9:
        if not any(total_demand <= v["capacity"] + 1e-9
                   for v in vehicles):
            return ("demand %s exceeds every vehicle capacity"
                    % _fmt_num(total_demand))
    return ("no feasible insertion in any route (time window, working"
            " hours, or capacity)")


def _solve(matrix, vehicles, jobs, by_id, shipments):
    """Cheapest insertion + 2-opt. Returns (routes, unassigned).
    routes is a list of dicts with vehicle, jobs, cost, timeline.
    unassigned is a list of dicts with the unit and the reason."""
    shipment_of = {}
    for pickup, delivery in shipments:
        shipment_of[pickup] = (pickup, delivery)
        shipment_of[delivery] = (pickup, delivery)
    # Insertion units: a shipment is one unit (pickup + delivery), a
    # job in no shipment is its own unit. Highest priority first,
    # then the earliest window.
    units = []
    seen = set()
    for pickup, delivery in shipments:
        units.append([by_id[pickup], by_id[delivery]])
        seen.add(pickup)
        seen.add(delivery)
    for job in jobs:
        if job["id"] not in seen:
            units.append([job])

    def unit_key(unit):
        prio = max(j["priority"] for j in unit)
        earliest = None
        for j in unit:
            if j["window"] is not None:
                e = j["window"][0]
                earliest = e if earliest is None else min(earliest, e)
        return (-prio, earliest if earliest is not None else float("inf"))

    units.sort(key=unit_key)
    routes = {v["id"]: [] for v in vehicles}
    route_cost = {v["id"]: 0.0 for v in vehicles}
    unassigned = []
    for unit in units:
        unit_ids = [j["id"] for j in unit]
        best = None  # (delta, vehicle, candidate_route)
        for v in vehicles:
            cur = routes[v["id"]]
            old_ok, old_cost, _, _ = _route_timeline(
                cur, v, by_id, shipments, matrix)
            if not old_ok:
                continue
            if len(unit) == 1:
                jid = unit_ids[0]
                for p in range(len(cur) + 1):
                    cand = cur[:p] + [jid] + cur[p:]
                    ok, cost, _, _ = _route_timeline(
                        cand, v, by_id, shipments, matrix)
                    if not ok:
                        continue
                    delta = cost - old_cost
                    if best is None or delta < best[0] - 1e-9:
                        best = (delta, v, cand)
            else:
                pickup, delivery = unit_ids
                for i in range(len(cur) + 1):
                    tmp = cur[:i] + [pickup] + cur[i:]
                    for k in range(i + 1, len(tmp) + 1):
                        cand = tmp[:k] + [delivery] + tmp[k:]
                        ok, cost, _, _ = _route_timeline(
                            cand, v, by_id, shipments, matrix)
                        if not ok:
                            continue
                        delta = cost - old_cost
                        if best is None or delta < best[0] - 1e-9:
                            best = (delta, v, cand)
        if best is None:
            unassigned.append({
                "jobs": unit_ids,
                "reason": _unassigned_reason(unit, vehicles)})
        else:
            _, v, cand = best
            routes[v["id"]] = cand
            ok, cost, _, _ = _route_timeline(
                cand, v, by_id, shipments, matrix)
            route_cost[v["id"]] = cost if ok else 0.0
    # 2-opt improvement per route: reverse a segment only when the
    # result stays feasible and is cheaper.
    for v in vehicles:
        route = routes[v["id"]]
        improved = True
        passes = 0
        while improved and passes < 50:
            improved = False
            passes += 1
            ok, cost, _, _ = _route_timeline(
                route, v, by_id, shipments, matrix)
            if not ok:
                break
            for i in range(len(route)):
                for j in range(i + 1, len(route)):
                    cand = (route[:i] + list(reversed(route[i:j + 1]))
                            + route[j + 1:])
                    ok2, cost2, _, _ = _route_timeline(
                        cand, v, by_id, shipments, matrix)
                    if ok2 and cost2 < cost - 1e-9:
                        route = cand
                        cost = cost2
                        improved = True
                        break
                if improved:
                    break
        routes[v["id"]] = route
    out = []
    for v in vehicles:
        route = routes[v["id"]]
        ok, cost, timeline, reason = _route_timeline(
            route, v, by_id, shipments, matrix)
        if not ok:
            raise _Refusal("internal: the solver produced an infeasible"
                           " route for vehicle %s (%s)"
                           % (v["id"], reason))
        out.append({"vehicle": v["id"], "jobs": list(route),
                    "cost": cost, "timeline": timeline,
                    "load": sum(by_id[j]["demand"] for j in route)})
    return out, unassigned

# ---------------------------------------------------------------------------
# Store — hash-chained ledger of plans (A1-store).
# ---------------------------------------------------------------------------

class _RPStore(store_base.StoreBase):
    """Hash-chained ledger of route plans. Open-per-operation; every
    write is followed by a re-read before the caller claims it
    (A1-store)."""

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else _db_path()

    def _connect(self):
        conn = store_base.connect(
            str(self.path), pragmas=("PRAGMA journal_mode=WAL",))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " ts REAL NOT NULL,"
            " kind TEXT NOT NULL,"
            " payload TEXT NOT NULL,"
            " prev_hash TEXT NOT NULL,"
            " hash TEXT NOT NULL)")
        conn.commit()
        return conn


    def _append_event_locked(self, conn, kind, payload):
        ts = time.time()
        prev = conn.execute(
            "SELECT hash FROM events ORDER BY id DESC LIMIT 1"
            ).fetchone()
        prev_hash = prev[0] if prev else "0" * 64
        digest = store_base.chain_digest(
            "json_body_ts", prev_hash,
            {"ts": ts, "kind": kind, "payload": payload})
        conn.execute(
            "INSERT INTO events (ts, kind, payload, prev_hash, hash)"
            " VALUES (?, ?, ?, ?, ?)",
            (ts, kind, json.dumps(payload), prev_hash, digest))
        return digest

    def append_event(self, kind, payload):
        """Append a ledger event and re-read it (A1-store). Returns
        True when the re-read row matches what was written."""
        with _LOCK:
            conn = self._connect()
            try:
                digest = self._append_event_locked(conn, kind, payload)
                conn.commit()
                row = conn.execute(
                    "SELECT hash, payload FROM events"
                    " ORDER BY id DESC LIMIT 1").fetchone()
            finally:
                conn.close()
        if row is None:
            return False
        if row[0] != digest:
            return False
        if json.loads(row[1]) != payload:
            return False
        return True

    def list_events(self, limit=20):
        if self._missing():
            return []
        with _LOCK:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT id, ts, kind, payload FROM events"
                    " ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
            finally:
                conn.close()
        return rows

    def verify_chain(self):
        if self._missing():
            return True
        with _LOCK:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT ts, kind, payload, prev_hash, hash FROM"
                    " events ORDER BY id").fetchall()
            finally:
                conn.close()
        entries = [{"ts": ts, "kind": kind,
                    "payload": json.loads(payload),
                    "prev_hash": prev_hash, "hash": digest}
                   for ts, kind, payload, prev_hash, digest in rows]
        return store_base.verify_entries(
            entries, "json_body_ts", store_base.GENESIS)["ok"]

    def status_counts(self):
        if self._missing():
            return {}
        with _LOCK:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT kind, COUNT(*) FROM events"
                    " GROUP BY kind").fetchall()
            finally:
                conn.close()
        return {kind: count for kind, count in rows}


def _inputs_hash(action, inputs):
    body = json.dumps({"action": action, "inputs": inputs},
                      sort_keys=True, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def _do_status(store):
    if store._missing():
        return ("ok", "no route plans yet — the store is empty.",
                {"plans": 0, "routes": 0, "jobs_served": 0,
                 "jobs_unassigned": 0, "chain_ok": True, "events": 0,
                 "chain_tip": "the store file does not exist yet"})
    counts = store.status_counts()
    chain_ok = store.verify_chain()
    plans = counts.get("plan", 0)
    return ("ok",
            "vehicle_routes: %d plan(s) recorded, ledger chain %s."
            % (plans, "verifies" if chain_ok else "DOES NOT verify"),
            {"plans": plans, "chain_ok": chain_ok,
             "events": sum(counts.values())})


def _do_events(store, mode, limit):
    m = _clean_str(mode, "mode", limit=20, default="list")
    if m is not None:
        m = m.lower()
    if m not in ("list", "verify"):
        return ("error",
                "vehicle_routes refused: events mode must be 'list' or"
                " 'verify'.", {})
    if store._missing():
        if m == "verify":
            return ("ok", "Ledger chain verifies (no plans yet).",
                    {"chain_ok": True, "events": 0})
        return ("ok", "no route plans yet — the store is empty.",
                {"events": []})
    if m == "verify":
        ok = store.verify_chain()
        if ok:
            return ("ok", "Ledger chain verifies over the full chain.",
                    {"chain_ok": True})
        return ("error",
                "Ledger chain does NOT verify — the record is broken.",
                {"chain_ok": False})
    lim = 20
    if limit is not None:
        if not _is_num(limit) or int(limit) < 1:
            return ("error",
                    "vehicle_routes refused: limit must be a positive"
                    " number.", {})
        lim = min(int(limit), _MAX_EVENTS)
    rows = store.list_events(lim)
    events = [{"id": r[0], "ts": r[1], "kind": r[2],
               "payload": json.loads(r[3])} for r in rows]
    return ("ok", "vehicle_routes: %d event(s)." % len(events),
            {"events": events})


def _do_plan(store, points, distance_matrix, vehicles, jobs, shipments):
    # The travel-time matrix: operator-supplied, or Euclidean from
    # points. One matrix is both the travel time and the cost.
    matrix = None
    if distance_matrix is not None:
        matrix = _parse_matrix(distance_matrix)
        if matrix is None:
            raise _Refusal("distance_matrix must be a square matrix of"
                           " numbers with zeros on the diagonal")
        if matrix == "too-many":
            raise _Refusal("at most %d points" % _MAX_POINTS)
    elif points is not None:
        matrix = _parse_points(points)
        if matrix is None:
            raise _Refusal("points must be a list of [x, y] pairs")
        if matrix == "too-many":
            raise _Refusal("at most %d points" % _MAX_POINTS)
    else:
        raise _Refusal("plan needs points or a distance_matrix")
    n = len(matrix)
    if n < 2:
        raise _Refusal("plan needs at least 2 nodes (a depot and a"
                       " job location)")
    parsed_vehicles = _parse_vehicles(vehicles, n)
    parsed_jobs, by_id = _parse_jobs(jobs, n)
    parsed_shipments = _parse_shipments(shipments, by_id)
    routes, unassigned = _solve(matrix, parsed_vehicles, parsed_jobs,
                                by_id, parsed_shipments)
    total_cost = sum(r["cost"] for r in routes)
    served = sum(len(r["jobs"]) for r in routes)
    inputs = {"vehicles": parsed_vehicles, "jobs": parsed_jobs,
              "shipments": parsed_shipments, "matrix": matrix}
    payload = {"inputs_hash": _inputs_hash("plan", inputs),
               "routes": [{"vehicle": r["vehicle"], "jobs": r["jobs"],
                          "cost": r["cost"], "load": r["load"],
                          "timeline": r["timeline"]} for r in routes],
               "unassigned": unassigned,
               "total_cost": total_cost}
    if not store.append_event("plan", payload):
        raise _Refusal("the plan could not be recorded")
    used = [r for r in routes if r["jobs"]]
    lines = ["route plan: %d vehicle(s), %d job(s) served, %d"
             " unassigned, total cost %s (cheapest-insertion + 2-opt"
             " heuristic):"
             % (len(parsed_vehicles), served, len(unassigned),
                _fmt_num(total_cost))]
    for r in used:
        v = next(v for v in parsed_vehicles if v["id"] == r["vehicle"])
        stops = " -> ".join([str(v["start"])]
                            + [str(by_id[j]["location"]) for j in r["jobs"]]
                            + [str(v["end"])])
        lines.append("  %s (load %s / %s, cost %s): %s"
                     % (r["vehicle"], _fmt_num(r["load"]),
                        _fmt_num(v["capacity"]), _fmt_num(r["cost"]),
                        stops))
    for u in unassigned:
        lines.append("  unassigned %s: %s"
                     % (", ".join(u["jobs"]), u["reason"]))
    return ("ok", "\n".join(lines),
            {"routes": payload["routes"], "unassigned": unassigned,
             "total_cost": total_cost, "served": served,
             "inputs_hash": payload["inputs_hash"]})


def vehicle_routes(action=None, points=None, distance_matrix=None,
               vehicles=None, jobs=None, shipments=None, limit=None,
               mode=None, _store=None):
    """The funnel entry point for `vehicle_routes`.

    action: plan | status | events. The rest are the named arguments.
    Returns a dict: {"status": "ok", "message": ..., **data} on
    success, {"status": "error", "message": ...} on refusal."""
    store = _store if _store is not None else _RPStore()
    try:
        act = _clean_str(action, "action", limit=20, required=True)
        if act not in _ACTIONS:
            raise _Refusal("unknown action %r — %s"
                           % (action, ", ".join(_ACTIONS)))
        if act == "status":
            status, msg, data = _do_status(store)
        elif act == "events":
            status, msg, data = _do_events(store, mode, limit)
        else:
            status, msg, data = _do_plan(store, points, distance_matrix,
                                         vehicles, jobs, shipments)
    except _Refusal as exc:
        status, msg, data = "error", str(exc), None
    except sqlite3.Error as exc:
        status, msg, data = "error", "the store failed: %s" % exc, None
    if status == "error":
        return {"status": "error", "message": msg}
    out = {"status": "ok", "message": msg}
    if data is not None:
        out.update(data)
    return out


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_all(registry) -> None:
    """Register the advanced routing pack (R19-10, VROOM-Project/vroom
    inspiration): vehicle_routes — time-windowed vehicle routing (VRPTW),
    pickup-and-delivery (shipments), multi-depot heterogeneous fleets
    and vehicle skills, in own stdlib Python over a hash-chained
    ledger. Free by design (app-state): the operator supplies every
    number, the tool only computes. Nothing adopted — no C++ code, no
    Vroom binary, no library, no wrapper, no port. The module owns its
    catalogue via register_all (same pattern as the other packs)."""
    registry.register(
        name="vehicle_routes",
        description=(
            "Plan advanced vehicle routes the operator describes: "
            "time-windowed delivery routes (VRPTW), pickup-and-delivery "
            "shipments, multi-depot fleets with different vehicles, and "
            "vehicle skills, breaks and working hours. The operator "
            "supplies every number (vehicles with capacity, skills, "
            "depots, working hours and breaks; jobs with location, "
            "demand, service duration, time window, skills and "
            "priority; shipments pairing a pickup with a delivery; and "
            "a travel-time matrix or points). The tool computes the "
            "routes with its own stdlib heuristic (cheapest insertion "
            "+ 2-opt) and records the plan in a hash-chained ledger. "
            "Every returned route is feasible — capacity, time windows, "
            "skills, working hours, breaks and pickup-before-delivery "
            "all hold — and jobs that cannot be served are reported as "
            "unassigned with the reason, never silently dropped. No "
            "Vroom binary, no routing engine, no network. Use when the "
            "user wants delivery routes with time windows, pickups and "
            "deliveries on the same run, several depots, or vehicles "
            "that can only do certain jobs."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "description": "One of: 'plan' (solve), 'status' or 'events'",
                           "enum": list(_ACTIONS)},
                "points": {"type": "array",
                           "description": "With 'plan', the points as [x, y] pairs instead of a distance_matrix (at most 200); Euclidean distances become the travel times"},
                "distance_matrix": {"type": "array",
                                    "description": "With 'plan', a square travel-time matrix (zeros on the diagonal); the time windows are in the matrix's units, and the matrix is also the cost"},
                "vehicles": {"type": "array",
                             "description": "With 'plan', the vehicles: each has capacity (required), skills (a list of names), start and end depot node indexes, start_time and end_time (working hours), and breaks (a list of [start, end] pairs); at most 50"},
                "jobs": {"type": "array",
                         "description": "With 'plan', the jobs: each has location (a node index, required), demand, service (duration), window ([earliest, latest] — the service must start inside it), skills (a list of names), and priority (higher is served first); at most 200"},
                "shipments": {"type": "array",
                              "description": "With 'plan', the shipments: each pairs a pickup job id with a delivery job id; both ride the same route, pickup before delivery; at most 100"},
                "limit": {"type": "integer",
                          "description": "With 'events', most rows shown (20 if left out, max 100)"},
                "mode": {"type": "string",
                         "description": "With 'events', 'list' or 'verify'"},
            },
            "required": ["action"],
        },
        handler=vehicle_routes,
    )
