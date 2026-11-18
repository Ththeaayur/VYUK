"""Social track (v5.73) — SO-1.7 graph query layer (narrow).

NetworkX's role, reimplemented natively in stdlib (no new
dependency): an IN-MEMORY query layer over typed edges the USER
asserted in the vault. Storage stays Markdown/SQLite (Wave 6);
this graph is rebuilt from files, never the database.

Hard rules, from the wave report:
- Edges are TYPED and carry PROVENANCE. An edge without a
  non-empty provenance is rejected — no edge from co-attendance,
  message frequency, shared surname/address, or photo
  co-occurrence, ever.
- NO centrality / importance score is computed or presented as a
  fact about a person. There is deliberately no centrality
  function in this module. "How do I know X?" (paths, mutuals,
  components) is the whole surface.
- No edge is ever inferred. add_edge requires stated
  provenance; inferred edges raise.

Stdlib only. Optional JSON serialisation (nodes + edges) with an
explicit path; no vault writes.
"""

import json
from collections import deque


class GraphError(ValueError):
    pass


class PeopleGraph:
    """Directed typed-edge graph with an undirected query view."""

    def __init__(self):
        self.nodes = set()
        self.edges = []  # dicts: a, b, type, provenance

    def add_edge(self, a, b, edge_type, provenance):
        """Add a user-stated typed edge. provenance must be a
        non-empty string naming where the user stated it (e.g.
        'user_stated 2026-10-25', 'vault:people/<slug>'). Empty
        provenance, self-edges, and inferred edges are rejected."""
        a, b = str(a or "").strip(), str(b or "").strip()
        edge_type = str(edge_type or "").strip()
        provenance = str(provenance or "").strip()
        if not a or not b:
            raise GraphError("edge endpoints must be non-empty")
        if a == b:
            raise GraphError("self-edges are not people edges")
        if not edge_type:
            raise GraphError("edge type must be non-empty")
        if not provenance:
            raise GraphError(
                "edge provenance is required: only user-stated "
                "edges may be recorded, never inferred ones")
        self.nodes.add(a)
        self.nodes.add(b)
        self.edges.append({"a": a, "b": b, "type": edge_type,
                           "provenance": provenance})
        return self.edges[-1]

    def neighbours(self, person):
        """[(neighbour, edge_type, direction)] where direction is
        'out' (person -> neighbour) or 'in' (neighbour -> person).
        Sorted for stable output."""
        out = []
        for edge in self.edges:
            if edge["a"] == person:
                out.append((edge["b"], edge["type"], "out"))
            elif edge["b"] == person:
                out.append((edge["a"], edge["type"], "in"))
        return sorted(out)

    def _undirected_adjacency(self):
        adjacency = {node: set() for node in self.nodes}
        for edge in self.edges:
            adjacency[edge["a"]].add(edge["b"])
            adjacency[edge["b"]].add(edge["a"])
        return adjacency

    def shortest_path(self, a, b):
        """Shortest 'how do I know X?' path as a node list, or
        None when disconnected. BFS over the undirected view."""
        if a not in self.nodes or b not in self.nodes:
            return None
        if a == b:
            return [a]
        adjacency = self._undirected_adjacency()
        prev = {a: None}
        queue = deque([a])
        while queue:
            current = queue.popleft()
            for nxt in sorted(adjacency[current]):
                if nxt not in prev:
                    prev[nxt] = current
                    if nxt == b:
                        path = [b]
                        while prev[path[-1]] is not None:
                            path.append(prev[path[-1]])
                        return list(reversed(path))
                    queue.append(nxt)
        return None

    def mutuals(self, a, b):
        """Common neighbours of a and b, sorted. Mutual
        connections only — never a closeness or importance
        claim."""
        adj = self._undirected_adjacency()
        if a not in adj or b not in adj:
            return []
        return sorted(adj[a] & adj[b])

    def components(self):
        """Connected components as sorted lists, sorted by first
        member. Circles, not rankings."""
        adjacency = self._undirected_adjacency()
        seen = set()
        groups = []
        for node in sorted(adjacency):
            if node in seen:
                continue
            group, stack = [], [node]
            seen.add(node)
            while stack:
                current = stack.pop()
                group.append(current)
                for nxt in adjacency[current]:
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
            groups.append(sorted(group))
        return sorted(groups)

    def to_json(self):
        return {"nodes": sorted(self.nodes), "edges": self.edges}

    @classmethod
    def from_json(cls, data):
        graph = cls()
        for edge in data.get("edges", []):
            graph.add_edge(edge["a"], edge["b"], edge["type"],
                           edge["provenance"])
        for node in data.get("nodes", []):
            graph.nodes.add(str(node))
        return graph


def save_graph(graph, path):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(graph.to_json(), handle, indent=1, sort_keys=True)
    return path


def load_graph(path):
    with open(path, encoding="utf-8") as handle:
        return PeopleGraph.from_json(json.load(handle))
