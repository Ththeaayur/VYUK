"""Combinatorial optimisation engine (R19-2, google/or-tools inspiration).

Parent-ruled (licence, 2026-11-12): google/or-tools (Apache-2.0, LICENSE from stable,
11,358 bytes, 202 lines, read in full). Nothing is adopted — no C++ code,
no OR-Tools library, no wrapper, no port. The build takes only the
solver shapes (TSP/VRP routing, bin packing, knapsack, shortest path,
linear sum assignment, max flow, linear programming, constraint
programming) and rebuilds them as own stdlib code.

What is built (one effectful tool `optimise`, free by design):
  * TSP — nearest-neighbour + 2-opt. A HEURISTIC. It does not prove
    optimality; the answer says so and gives the tour cost.
  * VRP — Clarke-Wright savings with a capacity limit + 2-opt per
    route. A HEURISTIC. Every route respects the vehicle capacity;
    every customer is visited exactly once.
  * Bin packing — first-fit decreasing / best-fit decreasing.
    A HEURISTIC. Every bin respects the capacity.
  * Knapsack — 0/1 dynamic programming. EXACT (integer weights and
    capacity only; non-integers are refused honestly).
  * Shortest path — Dijkstra. EXACT (non-negative weights only;
    negative weights are refused honestly).
  * Assignment — Hungarian algorithm (Kuhn-Munkres, O(n^3)). EXACT.
  * Max flow — Edmonds-Karp. EXACT.
  * LP — two-phase simplex, maximise c.x subject to A x (<=, >=, =) b,
    x >= 0. EXACT for the stated form (bounded sizes; infeasible and
    unbounded are reported honestly, not silently).
  * Constraints — a small backtracking solver with forward checking.
    NOT CP-SAT. It solves small integer-domain problems; it does not
    claim CP-SAT's propagation, learning or scale. A node limit stops
    runaway searches honestly.

What is NOT built (STOP, stated on the tin):
  * Min cost flow, mixed-integer programming (MIP), the real CP-SAT
    solver, PDLP/Glop-scale sparse LP, the C++ OR-Tools library, any
    wrapper around OR-Tools. No second app, no library, no subprocess.
  * No network, no file reads of operator data beyond the arguments
    given. The operator supplies every number; the tool never fetches
    one.

Bounds (refused honestly, with the numbers): TSP/VRP at most 200
points; bin packing at most 1,000 items; knapsack capacity at most
100,000 with integer weights; assignment at most 100 x 100; max flow
at most 200 nodes / 2,000 edges; LP at most 50 variables / 50
constraints; the constraint solver at most 20 variables, domain size
at most 20, 100,000 search nodes.
"""

import hashlib
import heapq
import json
import math
import sqlite3
import threading
import time
from pathlib import Path

from .config import DATA_DIR
from . import store_base

PROPOSED_MARKER = "Parent-ruled (licence, 2026-11-12)"

_ACTIONS = ("tsp", "vrp", "binpack", "knapsack", "shortest_path",
            "assignment", "max_flow", "lp", "constraints", "status",
            "events")

_MAX_POINTS = 200
_MAX_ITEMS = 1000
_MAX_KNAPSACK_CAPACITY = 100000
_MAX_ASSIGN = 100
_MAX_FLOW_NODES = 200
_MAX_FLOW_EDGES = 2000
_MAX_LP_VARS = 50
_MAX_LP_ROWS = 50
_MAX_CSP_VARS = 20
_MAX_CSP_DOMAIN = 20
_MAX_CSP_NODES = 100000
_TOL = 1e-9

_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Input parsing (operator-supplied arguments only; nothing is fetched)
# ---------------------------------------------------------------------------

def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v)


def _parse_points(value):
    """A list of [x, y] pairs -> a distance matrix (Euclidean)."""
    if not isinstance(value, list) or not value:
        return None
    pts = []
    for p in value:
        if not isinstance(p, list) or len(p) != 2 \
                or not _is_num(p[0]) or not _is_num(p[1]):
            return None
        pts.append((float(p[0]), float(p[1])))
    if len(pts) > _MAX_POINTS:
        return "too-many"
    n = len(pts)
    dist = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                dx = pts[i][0] - pts[j][0]
                dy = pts[i][1] - pts[j][1]
                dist[i][j] = math.hypot(dx, dy)
    return dist


def _parse_matrix(value):
    """A square distance/cost matrix (list of lists of numbers)."""
    if not isinstance(value, list) or not value:
        return None
    n = len(value)
    if n > _MAX_POINTS:
        return "too-many"
    mat = []
    for row in value:
        if not isinstance(row, list) or len(row) != n:
            return None
        r = []
        for v in row:
            if not _is_num(v):
                return None
            r.append(float(v))
        mat.append(r)
    for i in range(n):
        if abs(mat[i][i]) > _TOL:
            return None
    return mat


def _parse_num_list(value, limit=None):
    if not isinstance(value, list) or not value:
        return None
    if limit is not None and len(value) > limit:
        return "too-many"
    out = []
    for v in value:
        if not _is_num(v):
            return None
        out.append(float(v))
    return out


def _parse_edges(value, limit=None):
    """A list of [u, v, w] triples -> (n, [(u, v, w), ...])."""
    if not isinstance(value, list) or not value:
        return None
    if limit is not None and len(value) > limit:
        return "too-many"
    edges = []
    n = 0
    for e in value:
        if not isinstance(e, list) or len(e) != 3:
            return None
        u, v, w = e
        if not isinstance(u, int) or isinstance(u, bool) \
                or not isinstance(v, int) or isinstance(v, bool) \
                or not _is_num(w):
            return None
        if u < 0 or v < 0:
            return None
        edges.append((u, v, float(w)))
        n = max(n, u + 1, v + 1)
    return (n, edges)


def _tour_cost(route, dist):
    total = 0.0
    n = len(route)
    for i in range(n):
        total += dist[route[i]][route[(i + 1) % n]]
    return total


# ---------------------------------------------------------------------------
# TSP — nearest-neighbour + 2-opt (heuristic)
# ---------------------------------------------------------------------------

def _two_opt(route, dist):
    n = len(route)
    if n < 4:
        return route
    improved = True
    while improved:
        improved = False
        for i in range(n - 1):
            for k in range(i + 2, n):
                a, b = route[i], route[i + 1]
                c = route[k]
                d = route[(k + 1) % n]
                delta = dist[a][c] + dist[b][d] \
                    - dist[a][b] - dist[c][d]
                if delta < -1e-9:
                    route[i + 1:k + 1] = reversed(route[i + 1:k + 1])
                    improved = True
    return route


def _tsp_solve(dist):
    """Nearest-neighbour from every start, 2-opt each, keep the best.

    Returns (cost, route). The route is a cycle (the return to the
    start is implicit)."""
    n = len(dist)
    if n == 1:
        return (0.0, [0])
    if n == 2:
        return (2.0 * dist[0][1], [0, 1])
    best = None
    for start in range(n):
        unvisited = set(range(n))
        unvisited.discard(start)
        route = [start]
        cur = start
        while unvisited:
            nxt = min(unvisited, key=lambda j: (dist[cur][j], j))
            route.append(nxt)
            unvisited.discard(nxt)
            cur = nxt
        route = _two_opt(route, dist)
        cost = _tour_cost(route, dist)
        if best is None or cost < best[0] - 1e-9:
            best = (cost, route)
    return best


# ---------------------------------------------------------------------------
# VRP — Clarke-Wright savings + 2-opt per route (heuristic)
# ---------------------------------------------------------------------------

def _vrp_solve(dist, demands, capacity):
    """Clarke-Wright savings with a capacity limit.

    Node 0 is the depot; customers are 1..n-1. `demands` has one entry
    per customer (demands[i] is customer i+1's demand). Returns a list
    of routes; each route is a dict with customers (depot excluded),
    load and cost. Every customer appears exactly once and every
    route's load is at most the capacity."""
    n = len(dist)
    customers = list(range(1, n))
    routes = [[c] for c in customers]
    loads = {c: demands[c - 1] for c in customers}
    owner = {c: idx for idx, c in enumerate(customers)}

    def _endpoints(route):
        return route[0], route[-1]

    savings = []
    for i in customers:
        for j in customers:
            if i < j:
                savings.append((dist[0][i] + dist[0][j] - dist[i][j],
                                i, j))
    savings.sort(key=lambda s: (-s[0], s[1], s[2]))
    for _s, i, j in savings:
        ri = owner.get(i)
        rj = owner.get(j)
        if ri is None or rj is None or ri == rj:
            continue
        ra = routes[ri]
        rb = routes[rj]
        if ra is None or rb is None:
            continue
        if loads[i] + loads[j] > capacity + 1e-9:
            continue
        ai, bi = _endpoints(ra)
        aj, bj = _endpoints(rb)
        merged = None
        if bi == i and aj == j:
            merged = ra + rb
        elif ai == i and bj == j:
            merged = rb + ra
        elif bi == i and bj == j:
            merged = ra + list(reversed(rb))
        elif ai == i and aj == j:
            merged = list(reversed(ra)) + rb
        if merged is None:
            continue
        # loads[c] is the load of c's route, so the merged load is the
        # sum of the two route loads (not a sum over the customers).
        total = loads[i] + loads[j]
        routes[ri] = merged
        routes[rj] = None
        for c in merged:
            owner[c] = ri
            loads[c] = total
    out = []
    for route in routes:
        if not route:
            continue
        cyc = [0] + route
        cyc = _two_opt(cyc, dist)
        # rotate so the depot is first (the 2-opt may have moved it)
        k = cyc.index(0)
        cyc = cyc[k:] + cyc[:k]
        cost = _tour_cost(cyc, dist)
        load = sum(demands[c - 1] for c in route)
        out.append({"customers": route, "load": load, "cost": cost})
    return out


# ---------------------------------------------------------------------------
# Bin packing — first-fit / best-fit decreasing (heuristic)
# ---------------------------------------------------------------------------

def _binpack(items, capacity, method="ffd"):
    """First-fit decreasing (or best-fit decreasing) bin packing.

    `items` is a list of sizes, each at most the capacity. Returns a
    list of bins; each bin is a dict with the item indices and the
    load. Every bin's load is at most the capacity."""
    order = sorted(range(len(items)), key=lambda i: (-items[i], i))
    bins = []  # each: {"items": [indices], "load": float}
    for idx in order:
        size = items[idx]
        if method == "bfd":
            best = None
            best_room = None
            for b in bins:
                room = capacity - b["load"]
                if room >= size - 1e-9 and (best_room is None
                                            or room < best_room):
                    best = b
                    best_room = room
            if best is not None:
                best["items"].append(idx)
                best["load"] += size
            else:
                bins.append({"items": [idx], "load": size})
        else:  # ffd
            placed = False
            for b in bins:
                if b["load"] + size <= capacity + 1e-9:
                    b["items"].append(idx)
                    b["load"] += size
                    placed = True
                    break
            if not placed:
                bins.append({"items": [idx], "load": size})
    return bins


# ---------------------------------------------------------------------------
# Knapsack — 0/1 dynamic programming (exact, integer weights)
# ---------------------------------------------------------------------------

def _knapsack(weights, values, capacity):
    """0/1 knapsack by dynamic programming. Exact.

    Weights and capacity must be integers. Returns (max_value,
    chosen_indices)."""
    n = len(weights)
    cap = int(capacity)
    dp = [0] * (cap + 1)
    keep = [[False] * (cap + 1) for _ in range(n)]
    for i in range(n):
        w = int(weights[i])
        v = values[i]
        for c in range(cap, w - 1, -1):
            if dp[c - w] + v > dp[c]:
                dp[c] = dp[c - w] + v
                keep[i][c] = True
    chosen = []
    c = cap
    for i in range(n - 1, -1, -1):
        if keep[i][c]:
            chosen.append(i)
            c -= int(weights[i])
    chosen.reverse()
    return (dp[cap], chosen)


# ---------------------------------------------------------------------------
# Shortest path — Dijkstra (exact, non-negative weights)
# ---------------------------------------------------------------------------

def _dijkstra(n, edges, source, target):
    """Dijkstra's algorithm. Returns (distance, path) or (None, None)
    when the target is unreachable. Negative weights are refused by
    the caller."""
    adj = [[] for _ in range(n)]
    for (u, v, w) in edges:
        adj[u].append((v, w))
    dist = [math.inf] * n
    prev = [None] * n
    dist[source] = 0.0
    heap = [(0.0, source)]
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist[u] + 1e-12:
            continue
        if u == target:
            break
        for (v, w) in adj[u]:
            nd = d + w
            if nd < dist[v] - 1e-12:
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, v))
    if math.isinf(dist[target]):
        return (None, None)
    path = []
    cur = target
    while cur is not None:
        path.append(cur)
        cur = prev[cur]
    path.reverse()
    return (dist[target], path)


# ---------------------------------------------------------------------------
# Assignment — Hungarian algorithm, Kuhn-Munkres O(n^3) (exact)
# ---------------------------------------------------------------------------

def _hungarian(cost):
    """Minimum-cost assignment (rectangular matrices allowed).

    `cost` is a list of lists (n rows, m columns). Returns
    (total_cost, assignment) where assignment[i] is the column assigned
    to row i. Rows and columns are 0-based. The algorithm is the
    O(n^3) Hungarian method (potentials); it is exact."""
    n = len(cost)
    m = len(cost[0])
    transposed = False
    if n > m:
        cost = [[cost[i][j] for i in range(n)] for j in range(m)]
        n, m = m, n
        transposed = True
    # a is 1-indexed internally; n <= m.
    a = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            a[i][j] = float(cost[i - 1][j - 1])
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [math.inf] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = math.inf
            j1 = 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = a[i0][j] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    assign = [-1] * n
    for j in range(1, m + 1):
        if p[j] != 0:
            assign[p[j] - 1] = j - 1
    if transposed:
        # cost was transposed: assignment over the transposed matrix
        # maps original columns to original rows; invert it.
        inv = [-1] * m
        for i, j in enumerate(assign):
            if j >= 0:
                inv[j] = i
        total = sum(float(cost[i][j]) for i, j in enumerate(assign)
                    if j >= 0)
        return (total, inv)
    total = sum(float(cost[i][assign[i]]) for i in range(n))
    return (total, assign)


# ---------------------------------------------------------------------------
# Max flow — Edmonds-Karp (exact)
# ---------------------------------------------------------------------------

def _max_flow(n, edges, source, sink):
    """Edmonds-Karp max flow. Returns (max_flow, flows) where flows is
    a list of (u, v, flow) for the original edges (parallel edges are
    merged in the residual graph, so the flow is reported per original
    edge index)."""
    # Residual capacities, keyed by (u, v); parallel edges summed.
    cap = {}
    for (u, v, c) in edges:
        cap[(u, v)] = cap.get((u, v), 0.0) + c
        cap.setdefault((v, u), 0.0)
    adj = {}
    for (u, v) in cap:
        adj.setdefault(u, []).append(v)
        adj.setdefault(v, []).append(u)
    flow_value = 0.0
    while True:
        parent = {source: None}
        queue = [source]
        head = 0
        while head < len(queue):
            u = queue[head]
            head += 1
            if u == sink:
                break
            for v in adj.get(u, []):
                if v not in parent and cap.get((u, v), 0.0) > 1e-12:
                    parent[v] = u
                    queue.append(v)
        if sink not in parent:
            break
        bottleneck = math.inf
        v = sink
        while v != source:
            u = parent[v]
            bottleneck = min(bottleneck, cap[(u, v)])
            v = u
        v = sink
        while v != source:
            u = parent[v]
            cap[(u, v)] -= bottleneck
            cap[(v, u)] += bottleneck
            v = u
        flow_value += bottleneck
    flows = []
    for (u, v, c) in edges:
        # flow on the original edge = original capacity minus the
        # residual capacity in the forward direction.
        f = c - cap.get((u, v), 0.0)
        flows.append((u, v, max(0.0, f)))
    return (flow_value, flows)


# ---------------------------------------------------------------------------
# LP — two-phase simplex (exact for the stated form)
# ---------------------------------------------------------------------------

def _simplex_run(T, basis, n_vars, forbid):
    """Bland's-rule pivots on a tableau. T[0] is the objective row
    [1, -c_1..-c_N, value]; T[i] (i>=1) is [0, A[i], b_i]. basis[i-1]
    is the tableau column (0-based variable index) basic in row i.
    Returns ("optimal", value) or ("unbounded", None)."""
    N = n_vars
    m = len(T) - 1
    while True:
        entering = None
        for j in range(N):
            if j in forbid:
                continue
            if T[0][j + 1] < -_TOL:
                entering = j
                break
        if entering is None:
            return ("optimal", T[0][N + 1])
        leaving = None
        best_ratio = None
        best_basis = None
        for i in range(1, m + 1):
            a = T[i][entering + 1]
            if a > _TOL:
                ratio = T[i][N + 1] / a
                if best_ratio is None or ratio < best_ratio - 1e-12:
                    best_ratio = ratio
                    leaving = i
                    best_basis = basis[i - 1]
                elif abs(ratio - best_ratio) <= 1e-12 \
                        and basis[i - 1] < best_basis:
                    leaving = i
                    best_basis = basis[i - 1]
        if leaving is None:
            return ("unbounded", None)
        piv = T[leaving][entering + 1]
        row = T[leaving]
        for j in range(len(row)):
            row[j] /= piv
        for i in range(len(T)):
            if i == leaving:
                continue
            factor = T[i][entering + 1]
            if abs(factor) > 1e-15:
                for j in range(len(T[i])):
                    T[i][j] -= factor * row[j]
        basis[leaving - 1] = entering


def _lp_solve(c, A, senses, b):
    """Two-phase simplex. Maximise c.x subject to A x (sense) b,
    x >= 0. Returns (status, value, x) with status in {"optimal",
    "infeasible", "unbounded"}."""
    n = len(c)
    m = len(A)
    if m == 0:
        if any(v > _TOL for v in c):
            return ("unbounded", None, None)
        return ("optimal", 0.0, [0.0] * n)
    rows = []
    for i in range(m):
        ai = [float(v) for v in A[i]]
        bi = float(b[i])
        s = senses[i]
        if bi < -_TOL:
            ai = [-v for v in ai]
            bi = -bi
            s = {"<=": ">=", ">=": "<=", "=": "="}[s]
        rows.append((ai, s, bi))
    N = n
    basis = []
    art_cols = set()
    eq_rows = []
    for (ai, s, bi) in rows:
        coeffs = {j: ai[j] for j in range(n) if abs(ai[j]) > 1e-15}
        if s == "<=":
            coeffs[N] = 1.0
            basis.append(N)
            N += 1
        elif s == ">=":
            coeffs[N] = -1.0
            N += 1
            coeffs[N] = 1.0
            basis.append(N)
            art_cols.add(N)
            N += 1
        else:
            coeffs[N] = 1.0
            basis.append(N)
            art_cols.add(N)
            N += 1
        eq_rows.append((coeffs, bi))

    def _build_tableau(cost):
        T = []
        obj = [0.0] * (N + 2)
        obj[0] = 1.0
        for j in range(N):
            obj[j + 1] = -cost[j]
        T.append(obj)
        for (coeffs, bi) in eq_rows:
            row = [0.0] * (N + 2)
            for j, v in coeffs.items():
                row[j + 1] = v
            row[N + 1] = bi
            T.append(row)
        for i in range(1, len(T)):
            col = basis[i - 1]
            factor = T[0][col + 1]
            if abs(factor) > 1e-15:
                for j in range(N + 2):
                    T[0][j] -= factor * T[i][j]
        return T

    cost1 = [0.0] * N
    for col in art_cols:
        cost1[col] = -1.0
    T = _build_tableau(cost1)
    status, _ = _simplex_run(T, basis, N, set())
    if status == "unbounded":
        return ("infeasible", None, None)
    if T[0][N + 1] < -1e-7:
        return ("infeasible", None, None)
    i = 1
    while i <= len(T) - 1:
        col = basis[i - 1]
        if col in art_cols:
            pivot_col = None
            for j in range(N):
                if j not in art_cols and abs(T[i][j + 1]) > 1e-9:
                    pivot_col = j
                    break
            if pivot_col is None:
                del T[i]
                del basis[i - 1]
                continue
            piv = T[i][pivot_col + 1]
            row = T[i]
            for j in range(len(row)):
                row[j] /= piv
            for k in range(len(T)):
                if k == i:
                    continue
                factor = T[k][pivot_col + 1]
                if abs(factor) > 1e-15:
                    for j in range(len(T[k])):
                        T[k][j] -= factor * row[j]
            basis[i - 1] = pivot_col
        i += 1
    cost2 = [0.0] * N
    for j in range(n):
        cost2[j] = float(c[j])
    obj = [0.0] * (N + 2)
    obj[0] = 1.0
    for j in range(N):
        obj[j + 1] = -cost2[j]
    T[0] = obj
    for i in range(1, len(T)):
        col = basis[i - 1]
        factor = T[0][col + 1]
        if abs(factor) > 1e-15:
            for j in range(N + 2):
                T[0][j] -= factor * T[i][j]
    status, value = _simplex_run(T, basis, N, art_cols)
    if status == "unbounded":
        return ("unbounded", None, None)
    x = [0.0] * n
    for i in range(1, len(T)):
        col = basis[i - 1]
        if col < n:
            x[col] = max(0.0, T[i][N + 1])
    return ("optimal", value, x)


# ---------------------------------------------------------------------------
# Constraints — a small backtracking solver with forward checking
# (NOT CP-SAT: no learning, no global propagators, small problems only)
# ---------------------------------------------------------------------------

def _linear_range(terms, domains, assignment):
    lo = 0.0
    hi = 0.0
    for name, coeff in terms.items():
        if name in assignment:
            v = assignment[name]
            lo += coeff * v
            hi += coeff * v
        else:
            dom = domains.get(name)
            if not dom:
                return None
            if coeff >= 0:
                lo += coeff * min(dom)
                hi += coeff * max(dom)
            else:
                lo += coeff * max(dom)
                hi += coeff * min(dom)
    return (lo, hi)


def _linear_ok(terms, sense, rhs, domains, assignment):
    rng = _linear_range(terms, domains, assignment)
    if rng is None:
        return False
    lo, hi = rng
    if sense == "<=":
        return lo <= rhs + 1e-9
    if sense == ">=":
        return hi >= rhs - 1e-9
    return lo <= rhs + 1e-9 and hi >= rhs - 1e-9


def _csp_apply(domains, assignment, con):
    """Apply one constraint to the domains (in place). Returns
    (ok, pruned)."""
    ctype = con.get("type")
    if ctype == "all_different":
        names = con.get("vars") or []
        seen = set()
        for name in names:
            if name in assignment:
                if assignment[name] in seen:
                    return (False, False)
                seen.add(assignment[name])
        pruned = False
        for name in names:
            if name not in assignment:
                before = len(domains[name])
                domains[name] = {v for v in domains[name]
                                 if v not in seen}
                if not domains[name]:
                    return (False, False)
                if len(domains[name]) != before:
                    pruned = True
        return (True, pruned)
    if ctype == "linear":
        terms = con.get("terms") or {}
        sense = con.get("sense")
        rhs = con.get("rhs")
        if sense not in ("<=", ">=", "=") or rhs is None \
                or not _is_num(rhs):
            return (False, False)
        if not _linear_ok(terms, sense, rhs, domains, assignment):
            return (False, False)
        pruned = False
        for name in terms:
            if name in assignment:
                continue
            keep = set()
            for v in domains[name]:
                trial = dict(assignment)
                trial[name] = v
                if _linear_ok(terms, sense, rhs, domains, trial):
                    keep.add(v)
            if not keep:
                return (False, False)
            if keep != domains[name]:
                domains[name] = keep
                pruned = True
        return (True, pruned)
    if ctype == "not_equal":
        a = con.get("a")
        b = con.get("b")
        if a in assignment and b in assignment:
            return (assignment[a] != assignment[b], False)
        pruned = False
        if a in assignment and b not in assignment:
            if assignment[a] in domains[b]:
                domains[b].discard(assignment[a])
                if not domains[b]:
                    return (False, False)
                pruned = True
        if b in assignment and a not in assignment:
            if assignment[b] in domains[a]:
                domains[a].discard(assignment[b])
                if not domains[a]:
                    return (False, False)
                pruned = True
        return (True, pruned)
    if ctype == "equal":
        a = con.get("a")
        b = con.get("b")
        if a in assignment and b in assignment:
            return (assignment[a] == assignment[b], False)
        pruned = False
        if a in assignment and b not in assignment:
            if assignment[a] not in domains[b]:
                return (False, False)
            if domains[b] != {assignment[a]}:
                domains[b] = {assignment[a]}
                pruned = True
        if b in assignment and a not in assignment:
            if assignment[b] not in domains[a]:
                return (False, False)
            if domains[a] != {assignment[b]}:
                domains[a] = {assignment[b]}
                pruned = True
        return (True, pruned)
    return (False, False)


def _csp_propagate(domains, assignment, constraints):
    domains = {k: set(v) for k, v in domains.items()}
    changed = True
    while changed:
        changed = False
        for con in constraints:
            ok, pruned = _csp_apply(domains, assignment, con)
            if not ok:
                return (False, None)
            if pruned:
                changed = True
    return (True, domains)


def _csp_solve(variables, constraints, limit=1):
    """Backtracking with MRV + forward checking. Returns (solutions,
    nodes, status) with status in {"solved", "no-solution",
    "node-limit"}. Solutions are dicts {name: value}."""
    names = sorted(variables.keys())
    domains = {name: set(variables[name]) for name in names}
    solutions = []
    state = {"nodes": 0}

    def _search(assignment, doms):
        if len(solutions) >= limit:
            return
        if state["nodes"] >= _MAX_CSP_NODES:
            return
        state["nodes"] += 1
        if len(assignment) == len(names):
            solutions.append(dict(assignment))
            return
        unassigned = [nm for nm in names if nm not in assignment]
        var = min(unassigned, key=lambda nm: (len(doms[nm]), nm))
        for value in sorted(doms[var], key=lambda v: str(v)):
            if state["nodes"] >= _MAX_CSP_NODES \
                    or len(solutions) >= limit:
                return
            trial = dict(assignment)
            trial[var] = value
            ok, new_doms = _csp_propagate(doms, trial, constraints)
            if not ok:
                continue
            _search(trial, new_doms)

    ok, doms = _csp_propagate(domains, {}, constraints)
    if ok:
        _search({}, doms)
    if solutions:
        status = "solved"
    elif state["nodes"] >= _MAX_CSP_NODES:
        status = "node-limit"
    else:
        status = "no-solution"
    return (solutions, state["nodes"], status)


# ---------------------------------------------------------------------------
# Store: a hash-chained ledger of solves (one SQLite file, DATA_DIR)
# ---------------------------------------------------------------------------

def _db_path() -> Path:
    return Path(DATA_DIR) / "optimise.sqlite3"


class _OptStore(store_base.StoreBase):
    """Hash-chained ledger of solves. Open-per-operation; every write
    is followed by a re-read before the caller claims it (A1-store)."""

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


def _fmt_num(v):
    if isinstance(v, float):
        if abs(v - round(v)) < 1e-9:
            return str(int(round(v)))
        return "%.6g" % v
    return str(v)


def _inputs_hash(action, inputs):
    body = json.dumps({"action": action, "inputs": inputs},
                      sort_keys=True, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def optimise(action=None, points=None, distance_matrix=None,
             demands=None, capacity=None, items=None, weights=None,
             values=None, edges=None, costs=None, source=None,
             target=None, objective=None, constraints=None,
             variables=None, limit=None, mode=None, name=None,
             detail=None, _store=None):
    """Solve a combinatorial optimisation problem the operator
    describes. The operator supplies every number; the tool never
    fetches one. Returns a plain-text answer."""
    store = _store if _store is not None else _OptStore()
    act = str(action or "").strip().lower()
    if act not in _ACTIONS:
        return ("optimise refused: action must be one of %s."
                % ", ".join(_ACTIONS))
    label = str(name or "").strip()[:80]

    def _record(kind, summary, inputs):
        payload = {"action": kind, "name": label,
                   "inputs_hash": _inputs_hash(kind, inputs),
                   "summary": summary}
        return store.append_event(kind, payload)

    if act == "status":
        counts = store.status_counts()
        total = sum(counts.values())
        if not counts:
            return "No solves yet — the ledger is empty."
        lines = ["Optimise status — %d solve(s) recorded:" % total]
        for kind in sorted(counts):
            lines.append("  %s: %d" % (kind, counts[kind]))
        if not store.verify_chain():
            lines.append("WARNING: the ledger chain does NOT verify.")
        if str(detail or "").strip().lower() == "full":
            rows = store.list_events(limit=5)
            for _rid, ts, kind, payload in rows:
                p = json.loads(payload)
                lines.append("  #%d %s — %s"
                             % (_rid, kind, p.get("summary", "")))
        return "\n".join(lines)

    if act == "events":
        if str(mode or "").strip().lower() == "verify":
            if store.verify_chain():
                return "Ledger chain verifies — every solve is intact."
            return "Ledger chain does NOT verify — the record is broken."
        lim = 20
        if isinstance(limit, int) and not isinstance(limit, bool):
            lim = max(1, min(100, limit))
        rows = store.list_events(limit=lim)
        if not rows:
            return "No solves yet — the ledger is empty."
        lines = ["Optimise ledger (%d most recent):" % len(rows)]
        for _rid, ts, kind, payload in rows:
            p = json.loads(payload)
            lines.append("  #%d %s — %s"
                         % (_rid, kind, p.get("summary", "")))
        return "\n".join(lines)

    if act == "tsp":
        dist = None
        if distance_matrix is not None:
            dist = _parse_matrix(distance_matrix)
            if dist is None:
                return ("optimise refused: distance_matrix must be a"
                        " square matrix of numbers with zeros on the"
                        " diagonal.")
            if dist == "too-many":
                return ("optimise refused: at most %d points."
                        % _MAX_POINTS)
        elif points is not None:
            dist = _parse_points(points)
            if dist is None:
                return ("optimise refused: points must be a list of"
                        " [x, y] pairs.")
            if dist == "too-many":
                return ("optimise refused: at most %d points."
                        % _MAX_POINTS)
        else:
            return ("optimise refused: tsp needs points or a"
                    " distance_matrix.")
        n = len(dist)
        if n < 2:
            return "optimise refused: tsp needs at least 2 points."
        cost, route = _tsp_solve(dist)
        summary = "tsp: %d points, tour cost %s (heuristic)" \
            % (n, _fmt_num(cost))
        if not _record("tsp", summary, {"n": n, "cost": cost}):
            return "optimise refused: the solve could not be recorded."
        tour = " -> ".join(str(r) for r in route) + " -> %d" % route[0]
        return ("TSP — %d points, tour cost %s (nearest-neighbour +"
                " 2-opt heuristic, not a proven optimum):\n  %s"
                % (n, _fmt_num(cost), tour))

    if act == "vrp":
        dist = None
        if distance_matrix is not None:
            dist = _parse_matrix(distance_matrix)
            if dist is None:
                return ("optimise refused: distance_matrix must be a"
                        " square matrix of numbers with zeros on the"
                        " diagonal.")
            if dist == "too-many":
                return ("optimise refused: at most %d points."
                        % _MAX_POINTS)
        elif points is not None:
            dist = _parse_points(points)
            if dist is None:
                return ("optimise refused: points must be a list of"
                        " [x, y] pairs.")
            if dist == "too-many":
                return ("optimise refused: at most %d points."
                        % _MAX_POINTS)
        else:
            return ("optimise refused: vrp needs points or a"
                    " distance_matrix.")
        n = len(dist)
        if n < 2:
            return "optimise refused: vrp needs a depot and a customer."
        if demands is None:
            return ("optimise refused: vrp needs demands (one per"
                    " customer, %d values)." % (n - 1))
        dems = _parse_num_list(demands)
        if dems is None or len(dems) != n - 1:
            return ("optimise refused: demands must be %d numbers."
                    % (n - 1))
        if any(d < -1e-9 for d in dems):
            return "optimise refused: demands must not be negative."
        if capacity is None or not _is_num(capacity) or capacity <= 0:
            return ("optimise refused: vrp needs a positive vehicle"
                    " capacity.")
        if any(d > float(capacity) + 1e-9 for d in dems):
            return ("optimise refused: a customer's demand exceeds the"
                    " vehicle capacity — no feasible route exists.")
        routes = _vrp_solve(dist, dems, float(capacity))
        total = sum(r["cost"] for r in routes)
        summary = "vrp: %d customers, %d route(s), total cost %s" \
            % (n - 1, len(routes), _fmt_num(total))
        if not _record("vrp", summary,
                       {"n": n, "routes": len(routes), "cost": total}):
            return "optimise refused: the solve could not be recorded."
        lines = ["VRP — %d customers, %d route(s), total cost %s"
                 " (Clarke-Wright savings + 2-opt heuristic):"
                 % (n - 1, len(routes), _fmt_num(total))]
        for idx, r in enumerate(routes, 1):
            stops = " -> ".join(["0"] + [str(c) for c in r["customers"]]
                                + ["0"])
            lines.append("  Route %d (load %s / %s, cost %s): %s"
                         % (idx, _fmt_num(r["load"]),
                            _fmt_num(capacity), _fmt_num(r["cost"]),
                            stops))
        return "\n".join(lines)

    if act == "binpack":
        if items is None:
            return "optimise refused: binpack needs items (a list of sizes)."
        sizes = _parse_num_list(items, limit=_MAX_ITEMS)
        if sizes is None:
            return ("optimise refused: items must be a list of"
                    " numbers.")
        if sizes == "too-many":
            return ("optimise refused: at most %d items." % _MAX_ITEMS)
        if capacity is None or not _is_num(capacity) or capacity <= 0:
            return ("optimise refused: binpack needs a positive bin"
                    " capacity.")
        if any(s > float(capacity) + 1e-9 for s in sizes):
            return ("optimise refused: an item is larger than the bin"
                    " capacity — it cannot be packed.")
        if any(s < -1e-9 for s in sizes):
            return "optimise refused: item sizes must not be negative."
        method = str(mode or "ffd").strip().lower()
        if method not in ("ffd", "bfd"):
            return ("optimise refused: binpack mode must be 'ffd' or"
                    " 'bfd'.")
        bins = _binpack(sizes, float(capacity), method=method)
        summary = "binpack: %d item(s) in %d bin(s) (%s)" \
            % (len(sizes), len(bins), method)
        if not _record("binpack", summary,
                       {"n": len(sizes), "bins": len(bins)}):
            return "optimise refused: the solve could not be recorded."
        lines = ["Bin packing — %d item(s) in %d bin(s) (%s):"
                 % (len(sizes), len(bins),
                    "first-fit decreasing" if method == "ffd"
                    else "best-fit decreasing")]
        for idx, b in enumerate(bins, 1):
            lines.append("  Bin %d (load %s / %s): items %s"
                         % (idx, _fmt_num(b["load"]),
                            _fmt_num(capacity),
                            ", ".join("#%d (%s)" % (i, _fmt_num(sizes[i]))
                                     for i in b["items"])))
        return "\n".join(lines)

    if act == "knapsack":
        if weights is None or values is None:
            return ("optimise refused: knapsack needs weights and"
                    " values (parallel lists).")
        ws = _parse_num_list(weights)
        vs = _parse_num_list(values)
        if ws is None or vs is None or len(ws) != len(vs):
            return ("optimise refused: weights and values must be"
                    " parallel lists of numbers.")
        if capacity is None or not _is_num(capacity):
            return "optimise refused: knapsack needs a capacity."
        cap = float(capacity)
        if cap < 0 or abs(cap - round(cap)) > 1e-9:
            return ("optimise refused: knapsack capacity must be a"
                    " non-negative integer.")
        if cap > _MAX_KNAPSACK_CAPACITY:
            return ("optimise refused: knapsack capacity at most %d."
                    % _MAX_KNAPSACK_CAPACITY)
        if any(abs(w - round(w)) > 1e-9 or w < 0 for w in ws):
            return ("optimise refused: knapsack weights must be"
                    " non-negative integers.")
        value, chosen = _knapsack(ws, vs, cap)
        summary = "knapsack: max value %s from %d item(s)" \
            % (_fmt_num(value), len(chosen))
        if not _record("knapsack", summary,
                       {"n": len(ws), "value": value}):
            return "optimise refused: the solve could not be recorded."
        if chosen:
            picks = ", ".join("#%d (weight %s, value %s)"
                              % (i, _fmt_num(ws[i]), _fmt_num(vs[i]))
                              for i in chosen)
        else:
            picks = "nothing fits"
        return ("Knapsack — max value %s (exact, 0/1 DP), capacity %s:"
                "\n  %s" % (_fmt_num(value), _fmt_num(cap), picks))

    if act == "shortest_path":
        if edges is None:
            return ("optimise refused: shortest_path needs edges"
                    " ([u, v, weight] triples).")
        parsed = _parse_edges(edges, limit=10000)
        if parsed is None:
            return ("optimise refused: edges must be [u, v, weight]"
                    " triples with non-negative node numbers.")
        if parsed == "too-many":
            return "optimise refused: at most 10,000 edges."
        n, edgelist = parsed
        if any(w < -1e-9 for (_u, _v, w) in edgelist):
            return ("optimise refused: Dijkstra needs non-negative"
                    " weights — a negative weight is refused.")
        if source is None or target is None \
                or not isinstance(source, int) \
                or not isinstance(target, int) \
                or isinstance(source, bool) or isinstance(target, bool):
            return ("optimise refused: shortest_path needs integer"
                    " source and target nodes.")
        if source < 0 or target < 0 or source >= n or target >= n:
            return ("optimise refused: source/target are outside the"
                    " graph (nodes 0..%d)." % (n - 1))
        dist, path = _dijkstra(n, edgelist, source, target)
        if dist is None:
            summary = "shortest_path: %d unreachable from %d" \
                % (target, source)
            _record("shortest_path", summary, {"n": n})
            return ("Shortest path — node %d is unreachable from node"
                    " %d." % (target, source))
        summary = "shortest_path: %d -> %d, distance %s" \
            % (source, target, _fmt_num(dist))
        if not _record("shortest_path", summary,
                       {"n": n, "distance": dist}):
            return "optimise refused: the solve could not be recorded."
        return ("Shortest path — %d to %d, distance %s (Dijkstra):\n  %s"
                % (source, target, _fmt_num(dist),
                   " -> ".join(str(p) for p in path)))

    if act == "assignment":
        if costs is None:
            return ("optimise refused: assignment needs costs (a"
                    " matrix of numbers).")
        if not isinstance(costs, list) or not costs:
            return ("optimise refused: costs must be a non-empty"
                    " matrix.")
        m = len(costs[0])
        if m == 0 or len(costs) > _MAX_ASSIGN or m > _MAX_ASSIGN:
            return ("optimise refused: assignment at most %dx%d."
                    % (_MAX_ASSIGN, _MAX_ASSIGN))
        mat = []
        for row in costs:
            if not isinstance(row, list) or len(row) != m:
                return ("optimise refused: costs must be a rectangular"
                        " matrix.")
            r = []
            for v in row:
                if not _is_num(v):
                    return ("optimise refused: costs must be numbers.")
                r.append(float(v))
            mat.append(r)
        total, assign = _hungarian(mat)
        summary = "assignment: %dx%d, total cost %s" \
            % (len(mat), m, _fmt_num(total))
        if not _record("assignment", summary,
                       {"rows": len(mat), "cols": m, "cost": total}):
            return "optimise refused: the solve could not be recorded."
        lines = ["Assignment — %dx%d, total cost %s (Hungarian, exact):"
                 % (len(mat), m, _fmt_num(total))]
        for i, j in enumerate(assign):
            if j >= 0:
                lines.append("  Row %d -> column %d (cost %s)"
                             % (i, j, _fmt_num(mat[i][j])))
            else:
                lines.append("  Row %d -> unassigned" % i)
        return "\n".join(lines)

    if act == "max_flow":
        if edges is None:
            return ("optimise refused: max_flow needs edges"
                    " ([u, v, capacity] triples).")
        parsed = _parse_edges(edges, limit=_MAX_FLOW_EDGES)
        if parsed is None:
            return ("optimise refused: edges must be [u, v, capacity]"
                    " triples with non-negative node numbers.")
        if parsed == "too-many":
            return ("optimise refused: at most %d edges."
                    % _MAX_FLOW_EDGES)
        n, edgelist = parsed
        if n > _MAX_FLOW_NODES:
            return ("optimise refused: at most %d nodes."
                    % _MAX_FLOW_NODES)
        if any(w < -1e-9 for (_u, _v, w) in edgelist):
            return "optimise refused: capacities must not be negative."
        if source is None or target is None \
                or not isinstance(source, int) \
                or not isinstance(target, int) \
                or isinstance(source, bool) or isinstance(target, bool):
            return ("optimise refused: max_flow needs integer source"
                    " and target (sink) nodes.")
        if source < 0 or target < 0 or source >= n or target >= n:
            return ("optimise refused: source/target are outside the"
                    " graph (nodes 0..%d)." % (n - 1))
        if source == target:
            return ("optimise refused: source and sink must differ.")
        value, flows = _max_flow(n, edgelist, source, target)
        summary = "max_flow: %d -> %d, max flow %s" \
            % (source, target, _fmt_num(value))
        if not _record("max_flow", summary,
                       {"n": n, "flow": value}):
            return "optimise refused: the solve could not be recorded."
        lines = ["Max flow — %d to %d, max flow %s (Edmonds-Karp,"
                 " exact):" % (source, target, _fmt_num(value))]
        for (u, v, f) in flows:
            if f > 1e-9:
                lines.append("  %d -> %d: %s" % (u, v, _fmt_num(f)))
        return "\n".join(lines)

    if act == "lp":
        if objective is None or constraints is None:
            return ("optimise refused: lp needs objective (a list of"
                    " coefficients) and constraints (a list of"
                    " {coeffs, sense, rhs}).")
        obj = _parse_num_list(objective)
        if obj is None:
            return ("optimise refused: objective must be a list of"
                    " numbers.")
        if len(obj) > _MAX_LP_VARS:
            return ("optimise refused: at most %d variables."
                    % _MAX_LP_VARS)
        if not isinstance(constraints, list) or not constraints:
            return ("optimise refused: lp needs at least one"
                    " constraint.")
        if len(constraints) > _MAX_LP_ROWS:
            return ("optimise refused: at most %d constraints."
                    % _MAX_LP_ROWS)
        A = []
        senses = []
        b = []
        for con in constraints:
            if not isinstance(con, dict):
                return ("optimise refused: each constraint must be"
                        " {coeffs, sense, rhs}.")
            coeffs = _parse_num_list(con.get("coeffs"))
            sense = con.get("sense")
            rhs = con.get("rhs")
            if coeffs is None or len(coeffs) != len(obj):
                return ("optimise refused: each constraint's coeffs"
                        " must have %d entries." % len(obj))
            if sense not in ("<=", ">=", "="):
                return ("optimise refused: sense must be '<=', '>='"
                        " or '='.")
            if not _is_num(rhs):
                return ("optimise refused: rhs must be a number.")
            A.append(coeffs)
            senses.append(sense)
            b.append(float(rhs))
        status, value, x = _lp_solve(obj, A, senses, b)
        if status == "infeasible":
            _record("lp", "lp: infeasible", {"n": len(obj)})
            return ("LP — infeasible: no assignment satisfies all the"
                    " constraints.")
        if status == "unbounded":
            _record("lp", "lp: unbounded", {"n": len(obj)})
            return ("LP — unbounded: the objective can grow without"
                    " limit under these constraints.")
        varnames = None
        if isinstance(variables, list) and len(variables) == len(obj):
            varnames = [str(v) for v in variables]
        summary = "lp: optimal value %s" % _fmt_num(value)
        if not _record("lp", summary,
                       {"n": len(obj), "value": value}):
            return "optimise refused: the solve could not be recorded."
        lines = ["LP — optimal value %s (two-phase simplex, exact):"
                 % _fmt_num(value)]
        for i, v in enumerate(x):
            nm = varnames[i] if varnames else "x%d" % (i + 1)
            lines.append("  %s = %s" % (nm, _fmt_num(v)))
        return "\n".join(lines)

    if act == "constraints":
        if variables is None or constraints is None:
            return ("optimise refused: constraints needs variables"
                    " ({name: [domain values]}) and constraints (a"
                    " list of {type, ...}).")
        if not isinstance(variables, dict) or not variables:
            return ("optimise refused: variables must be a non-empty"
                    " {name: [values]} mapping.")
        if len(variables) > _MAX_CSP_VARS:
            return ("optimise refused: at most %d variables."
                    % _MAX_CSP_VARS)
        varmap = {}
        for name, dom in variables.items():
            if not isinstance(name, str) or not name:
                return ("optimise refused: variable names must be"
                        " non-empty strings.")
            if not isinstance(dom, list) or not dom:
                return ("optimise refused: every variable needs a"
                        " non-empty domain list.")
            if len(dom) > _MAX_CSP_DOMAIN:
                return ("optimise refused: domain size at most %d."
                        % _MAX_CSP_DOMAIN)
            if any(isinstance(v, bool) for v in dom):
                return ("optimise refused: domain values must be"
                        " numbers or strings.")
            varmap[name] = list(dom)
        if not isinstance(constraints, list) or not constraints:
            return ("optimise refused: constraints needs at least one"
                    " constraint.")
        for con in constraints:
            if not isinstance(con, dict) \
                    or con.get("type") not in ("all_different",
                                               "linear", "not_equal",
                                               "equal"):
                return ("optimise refused: each constraint needs a"
                        " type (all_different, linear, not_equal or"
                        " equal).")
            for nm in (con.get("vars") or []):
                if nm not in varmap:
                    return ("optimise refused: constraint names an"
                            " unknown variable %r." % nm)
            for nm in (list((con.get("terms") or {}).keys())
                       + [con.get("a"), con.get("b")]):
                if nm is not None and nm not in varmap:
                    return ("optimise refused: constraint names an"
                            " unknown variable %r." % nm)
        sols, nodes, status = _csp_solve(varmap, constraints, limit=1)
        if status == "node-limit":
            _record("constraints", "constraints: node-limit",
                    {"n": len(varmap)})
            return ("Constraints — node limit reached (%d nodes): the"
                    " search was stopped before a solution was found."
                    " Shrink the domains or add constraints."
                    % _MAX_CSP_NODES)
        if status == "no-solution":
            _record("constraints", "constraints: no-solution",
                    {"n": len(varmap)})
            return ("Constraints — no solution exists (%d nodes"
                    " searched)." % nodes)
        sol = sols[0]
        summary = "constraints: solved in %d node(s)" % nodes
        if not _record("constraints", summary,
                       {"n": len(varmap), "nodes": nodes}):
            return "optimise refused: the solve could not be recorded."
        lines = ["Constraints — solved in %d node(s) (backtracking"
                 " with forward checking; a small solver, NOT CP-SAT):"
                 % nodes]
        for nm in sorted(sol):
            lines.append("  %s = %s" % (nm, sol[nm]))
        return "\n".join(lines)

    return "optimise refused: unknown action."


def register_all(registry) -> None:
    """Register the optimisation pack (R19-2, google/or-tools
    inspiration): optimise — a combinatorial optimisation engine in
    own stdlib code (TSP/VRP heuristics, bin packing, knapsack DP,
    Dijkstra, Hungarian assignment, Edmonds-Karp max flow, two-phase
    simplex LP, a small backtracking constraint solver) over a
    hash-chained ledger. Free by design (app-state): the operator
    supplies every number, the tool only computes. Nothing adopted —
    no C++ code, no OR-Tools library, no wrapper, no port. The module
    owns its catalogue via register_all (same pattern as the other
    packs)."""
    registry.register(
        name="optimise",
        description=(
            "Solve a combinatorial optimisation problem the operator "
            "describes: travelling-salesman and vehicle-routing tours, "
            "bin packing, knapsack, shortest paths, assignment, max "
            "flow, linear programming and small constraint problems. "
            "The operator supplies every number (points, distances, "
            "demands, items, edges, costs, objective, constraints); "
            "the tool computes the answer with its own stdlib "
            "algorithms and records the solve in a hash-chained "
            "ledger. TSP and VRP are heuristics (the answer says so); "
            "knapsack, Dijkstra, Hungarian, max flow and LP are exact "
            "for their stated forms. No OR-Tools library, no C++, no "
            "network. Use when the user wants the cheapest route, the "
            "best packing, the best assignment or the optimum of a "
            "small linear or constraint problem."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "description": "One of: 'tsp' (tour), 'vrp' (routes), 'binpack', 'knapsack', 'shortest_path', 'assignment', 'max_flow', 'lp' (linear programming), 'constraints' (small constraint solver), 'status' or 'events'"},
                "points": {"type": "array",
                           "description": "With 'tsp' or 'vrp', the points as [x, y] pairs (node 0 is the depot for vrp); at most 200"},
                "distance_matrix": {"type": "array",
                                    "description": "With 'tsp' or 'vrp', a square distance matrix instead of points (zeros on the diagonal)"},
                "demands": {"type": "array",
                            "description": "With 'vrp', one demand per customer (n-1 numbers, each at most the capacity)"},
                "capacity": {"type": "number",
                             "description": "With 'vrp', the vehicle capacity; with 'binpack', the bin capacity; with 'knapsack', the (integer) capacity"},
                "items": {"type": "array",
                          "description": "With 'binpack', the item sizes (each at most the bin capacity); at most 1,000"},
                "weights": {"type": "array",
                            "description": "With 'knapsack', the item weights (non-negative integers, parallel to values)"},
                "values": {"type": "array",
                           "description": "With 'knapsack', the item values (parallel to weights)"},
                "edges": {"type": "array",
                          "description": "With 'shortest_path' or 'max_flow', [u, v, weight] or [u, v, capacity] triples"},
                "costs": {"type": "array",
                          "description": "With 'assignment', the cost matrix (rows x columns, numbers)"},
                "source": {"type": "integer",
                           "description": "With 'shortest_path' or 'max_flow', the start node"},
                "target": {"type": "integer",
                           "description": "With 'shortest_path' or 'max_flow', the destination (sink) node"},
                "objective": {"type": "array",
                              "description": "With 'lp', the objective coefficients (maximise c.x, x >= 0)"},
                "constraints": {"type": "array",
                                "description": "With 'lp', [{coeffs, sense ('<=', '>=', '='), rhs}]; with 'constraints', [{type: all_different/linear/not_equal/equal, ...}]"},
                "variables": {"type": "object",
                              "description": "With 'lp', an optional list of variable names; with 'constraints', {name: [domain values]}"},
                "limit": {"type": "integer",
                          "description": "With 'events', most rows shown (20 if left out, max 100)"},
                "mode": {"type": "string",
                         "description": "With 'binpack', 'ffd' or 'bfd' (ffd if left out); with 'events', 'list' or 'verify'"},
                "name": {"type": "string",
                         "description": "A label for the solve, recorded in the ledger (at most 80 chars)"},
                "detail": {"type": "string",
                           "description": "With 'status', 'full' for the most recent solves"},
            },
            "required": ["action"],
        },
        handler=optimise,
    )
