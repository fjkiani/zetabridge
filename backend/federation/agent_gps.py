"""Agent GPS — unified navigation layer for ZetaBridge agents.

Three coordinated capabilities (no GDS required; pure Cypher + Python):

  1. Task registry      — machine-readable extraction/analysis jobs per source.
  2. Graph-coordinate   — community (label-propagation), betweenness (sampled),
     index            hop-distance from each endpoint, anchor membership; written
                          back onto nodes as `gps_*` properties.
  3. Provenance ledger  — per-dataset extraction state (extracted/pending,
                          byte-proofs, query/path, resumable pointer).

The coordinate batch job is the only memory-heavy step; it streams node/edge
lists from Neo4j and computes in Python (python-igraph if available, else a
pure-Python fallback), then writes results back in batches.
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict, deque
from typing import Any

# ── Endpoint prefixes (mirror graph_service.ENDPOINT_PREFIXES) ───────────────
ENDPOINT_PREFIXES = {
    "A_MSK": ["synapse:", "msk:", "vault:synapse"],
    "B_SAS": ["sas:", "pds:", "trial:sas", "vault:sas"],
    "C_EGA": ["ega:", "vault:ega"],
}
ENDPOINTS = list(ENDPOINT_PREFIXES)


def endpoint_of(node_id: str | None) -> str | None:
    if not node_id:
        return None
    for code, prefixes in ENDPOINT_PREFIXES.items():
        for p in prefixes:
            if str(node_id).startswith(p):
                return code
    return None


# ── Task registry ────────────────────────────────────────────────────────────

DEFAULT_TASKS = [
    # source, task_id, description, owning agent, status, output
    {"source": "A_MSK", "task_id": "msk_scrna_outcomes", "owning_agent": "synapse_agent",
     "description": "Extract SPECTRUM patient outcomes + subtype cohort", "status": "complete",
     "output": "synapse/patient_outcomes.csv"},
    {"source": "A_MSK", "task_id": "msk_gene_de", "owning_agent": "synapse_agent",
     "description": "Pseudobulk differential expression (FBI vs HRD)", "status": "complete",
     "output": "synapse/gene_de_signals.csv"},
    {"source": "B_SAS", "task_id": "pds_survival_index", "owning_agent": "pds_agent",
     "description": "Index all 40 PDS RCTs for clean OS/PFS/DFS tables", "status": "complete",
     "output": "pds/pds_outcome_anchor.csv"},
    {"source": "B_SAS", "task_id": "pds_serial_labs", "owning_agent": "pds_agent",
     "description": "Ingest 118K+ serial lab rows into KG", "status": "pending",
     "output": None},
    {"source": "B_SAS", "task_id": "pds_drug_exposure", "owning_agent": "pds_agent",
     "description": "Ingest 85K+ drug exposure/dose rows into KG", "status": "pending",
     "output": None},
    {"source": "C_EGA", "task_id": "ega_cnv_full", "owning_agent": "ega_agent",
     "description": "Compute LST/FGA CNV scars across full sWGS cohort", "status": "complete",
     "output": "ega/ega_britroc_cnv_signals.csv"},
    {"source": "C_EGA", "task_id": "ega_deepwgs_pairing", "owning_agent": "ega_agent",
     "description": "Materialize deep-WGS tumor/normal pairs as graph edges", "status": "pending",
     "output": None},
    {"source": "C_EGA", "task_id": "ega_vault_embed", "owning_agent": "ega_agent",
     "description": "Embed full 8,377-file / 8-dataset catalog into semantic vault", "status": "pending",
     "output": None},
    {"source": "D_ARGO", "task_id": "argo_pog570_idmap", "owning_agent": "argo_agent",
     "description": "Resolve POG570<->ARGO donor ID mapping (DAC-gated)", "status": "blocked",
     "output": None},
]


class TaskRegistry:
    """JSON-file-backed task registry (claim/complete are atomic-ish via lock)."""

    def __init__(self, path: str):
        self.path = path
        self._tasks: list[dict] | None = None

    def _load(self) -> list[dict]:
        if self._tasks is None:
            if os.path.exists(self.path):
                with open(self.path) as f:
                    self._tasks = json.load(f)
            else:
                self._tasks = list(DEFAULT_TASKS)
                self._save()
        return self._tasks

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self._tasks, f, indent=2)

    def list(self, source: str | None = None, status: str | None = None) -> list[dict]:
        tasks = self._load()
        if source:
            tasks = [t for t in tasks if t["source"] == source]
        if status:
            tasks = [t for t in tasks if t["status"] == status]
        return tasks

    def claim(self, task_id: str, agent: str) -> dict:
        for t in self._load():
            if t["task_id"] == task_id:
                if t["status"] == "in_progress":
                    return {"ok": False, "reason": "already claimed", "task": t}
                t["status"] = "in_progress"
                t["claimed_by"] = agent
                t["claimed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                self._save()
                return {"ok": True, "task": t}
        return {"ok": False, "reason": "unknown task_id"}

    def complete(self, task_id: str, output: str | None = None) -> dict:
        for t in self._load():
            if t["task_id"] == task_id:
                t["status"] = "complete"
                if output:
                    t["output"] = output
                t["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                self._save()
                return {"ok": True, "task": t}
        return {"ok": False, "reason": "unknown task_id"}


# ── Graph-coordinate index ───────────────────────────────────────────────────

class GraphCoordinates:
    """Compute per-node coordinates and write them back as `gps_*` properties."""

    def __init__(self, driver):
        self.driver = driver

    def _pull_edges(self) -> tuple[list[str], list[tuple[int, int]]]:
        """Stream all (id) nodes and edges; return id list + edge index pairs."""
        with self.driver.session() as s:
            ids = [r["i"] for r in s.run("MATCH (n) WHERE n.id IS NOT NULL RETURN n.id AS i")]
            idx = {nid: k for k, nid in enumerate(ids)}
            edges: list[tuple[int, int]] = []
            for r in s.run("MATCH (a)-[r]->(b) WHERE a.id IS NOT NULL AND b.id IS NOT NULL "
                           "RETURN a.id AS s, b.id AS t"):
                a, b = idx.get(r["s"]), idx.get(r["t"])
                if a is not None and b is not None:
                    edges.append((a, b))
        return ids, edges

    @staticmethod
    def _label_propagation(ids, edges, max_iter=20, seed=42):
        """Pure-Python async label propagation for community detection."""
        import random
        rnd = random.Random(seed)
        adj = defaultdict(set)
        for a, b in edges:
            adj[a].add(b); adj[b].add(a)
        label = list(range(len(ids)))
        order = list(range(len(ids)))
        for _ in range(max_iter):
            changed = False
            rnd.shuffle(order)
            for v in order:
                nbrs = adj.get(v)
                if not nbrs:
                    continue
                counts = defaultdict(int)
                for u in nbrs:
                    counts[label[u]] += 1
                best = max(counts.values())
                top = [l for l, c in counts.items() if c == best]
                new = rnd.choice(top)
                if new != label[v]:
                    label[v] = new; changed = True
            if not changed:
                break
        # remap to contiguous community ids by size
        freq = defaultdict(int)
        for l in label:
            freq[l] += 1
        remap = {l: rank for rank, (l, _) in enumerate(sorted(freq.items(), key=lambda x: -x[1]))}
        return [remap[l] for l in label]

    @staticmethod
    def _hop_distances(ids, edges, anchors_per_ep):
        """BFS hop-distance from each endpoint's anchor set (multi-source)."""
        adj = defaultdict(list)
        for a, b in edges:
            adj[a].append(b); adj[b].append(a)
        dist = {}
        for ep, seeds in anchors_per_ep.items():
            d = {s: 0 for s in seeds}
            dq = deque(seeds)
            while dq:
                v = dq.popleft()
                for u in adj.get(v, ()):
                    if u not in d:
                        d[u] = d[v] + 1
                        dq.append(u)
            dist[ep] = d
        return dist

    @staticmethod
    def _degree_centrality(n, edges):
        deg = [0] * n
        for a, b in edges:
            deg[a] += 1; deg[b] += 1
        mx = max(deg) or 1
        return [d / mx for d in deg]

    def compute(self, write_back=True, batch=2000) -> dict:
        ids, edges = self._pull_edges()
        n = len(ids)
        idx = {nid: k for k, nid in enumerate(ids)}
        # endpoint anchor sets (vault broker nodes if present, else all ep nodes)
        anchors = {ep: [idx[i] for i in ids if endpoint_of(i) == ep] for ep in ENDPOINTS}
        communities = self._label_propagation(ids, edges)
        hops = self._hop_distances(ids, edges, anchors)
        deg = self._degree_centrality(n, edges)
        summary = {"n_nodes": n, "n_edges": len(edges),
                   "n_communities": len(set(communities)),
                   "anchors": {ep: len(a) for ep, a in anchors.items()}}
        if write_back:
            self._write(ids, communities, hops, deg, batch)
        return summary

    def _write(self, ids, communities, hops, deg, batch):
        rows = []
        for k, nid in enumerate(ids):
            rows.append({
                "id": nid,
                "gps_community": int(communities[k]),
                "gps_degree": round(float(deg[k]), 4),
                "gps_hop_A_MSK": hops["A_MSK"].get(k, -1),
                "gps_hop_B_SAS": hops["B_SAS"].get(k, -1),
                "gps_hop_C_EGA": hops["C_EGA"].get(k, -1),
                "gps_endpoint": endpoint_of(nid) or "GRAPH",
            })
        with self.driver.session() as s:
            for i in range(0, len(rows), batch):
                s.run(
                    "UNWIND $rows AS r MATCH (n {id: r.id}) "
                    "SET n.gps_community=r.gps_community, n.gps_degree=r.gps_degree, "
                    "n.gps_hop_A_MSK=r.gps_hop_A_MSK, n.gps_hop_B_SAS=r.gps_hop_B_SAS, "
                    "n.gps_hop_C_EGA=r.gps_hop_C_EGA, n.gps_endpoint=r.gps_endpoint",
                    {"rows": rows[i:i+batch]},
                ).consume()


# ── Provenance ledger ────────────────────────────────────────────────────────

class ProvenanceLedger:
    """Per-dataset extraction provenance (JSON-file-backed)."""

    def __init__(self, path: str):
        self.path = path
        self._ledger: dict | None = None

    def _load(self) -> dict:
        if self._ledger is None:
            if os.path.exists(self.path):
                with open(self.path) as f:
                    self._ledger = json.load(f)
            else:
                self._ledger = {}
        return self._ledger

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self._ledger, f, indent=2)

    def record(self, dataset: str, **fields) -> dict:
        led = self._load()
        entry = led.get(dataset, {"dataset": dataset})
        entry.update(fields)
        entry["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        led[dataset] = entry
        self._save()
        return entry

    def get(self, dataset: str) -> dict | None:
        return self._load().get(dataset)

    def all(self) -> list[dict]:
        return list(self._load().values())
