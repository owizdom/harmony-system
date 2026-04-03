"""
Identity Graph Generator — Synthetic Heterogeneous Social Graph

Generates configurable social graphs with typed edges and risk-scored nodes.
Supports Erdős–Rényi, Barabási–Albert, and Watts–Strogatz models.
"""

import json
import random
import csv
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional

import networkx as nx


# ── Schema ───────────────────────────────────────────────────────────────────

class EdgeType(str, Enum):
    FAMILY = "family"
    COWORKER = "coworker"
    FRIEND = "friend"
    WEAK_SIGNAL = "weak_signal"  # like/follow


EDGE_WEIGHT_RANGES = {
    EdgeType.FAMILY: (0.7, 1.0),
    EdgeType.COWORKER: (0.4, 0.7),
    EdgeType.FRIEND: (0.5, 0.9),
    EdgeType.WEAK_SIGNAL: (0.05, 0.3),
}

EDGE_TYPE_DISTRIBUTION = {
    EdgeType.FAMILY: 0.10,
    EdgeType.COWORKER: 0.25,
    EdgeType.FRIEND: 0.30,
    EdgeType.WEAK_SIGNAL: 0.35,
}


@dataclass
class Node:
    node_id: int
    risk_score: float  # 0.0 – 1.0
    metadata: dict = field(default_factory=dict)


@dataclass
class Edge:
    source: int
    target: int
    edge_type: str
    weight: float


# ── Graph Models ─────────────────────────────────────────────────────────────

class GraphModel(str, Enum):
    ERDOS_RENYI = "erdos_renyi"
    BARABASI_ALBERT = "barabasi_albert"
    WATTS_STROGATZ = "watts_strogatz"


@dataclass
class GraphConfig:
    """All configurable graph parameters."""
    n: int = 500                          # number of nodes
    model: GraphModel = GraphModel.BARABASI_ALBERT

    # Erdős–Rényi
    er_p: float = 0.01                    # edge probability

    # Barabási–Albert
    ba_m: int = 3                         # edges per new node

    # Watts–Strogatz
    ws_k: int = 6                         # nearest neighbours in ring
    ws_p: float = 0.3                     # rewiring probability

    # Risk scoring
    risk_mean: float = 0.15               # most people low-risk
    risk_std: float = 0.20

    # Edge type distribution (override defaults if needed)
    edge_type_dist: Optional[dict] = None

    seed: Optional[int] = 42

    def __post_init__(self):
        if self.edge_type_dist is None:
            self.edge_type_dist = dict(EDGE_TYPE_DISTRIBUTION)


# ── Generator ────────────────────────────────────────────────────────────────

class IdentityGraphGenerator:
    def __init__(self, config: GraphConfig | None = None):
        self.config = config or GraphConfig()
        self.rng = random.Random(self.config.seed)
        self.graph: nx.Graph | None = None
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []

    # ── Build ────────────────────────────────────────────────────────────

    def generate(self) -> nx.Graph:
        """Generate the full heterogeneous identity graph."""
        self._build_topology()
        self._assign_node_attributes()
        self._assign_edge_types()
        return self.graph

    def _build_topology(self):
        c = self.config
        match c.model:
            case GraphModel.ERDOS_RENYI:
                self.graph = nx.erdos_renyi_graph(c.n, c.er_p, seed=c.seed)
            case GraphModel.BARABASI_ALBERT:
                self.graph = nx.barabasi_albert_graph(c.n, c.ba_m, seed=c.seed)
            case GraphModel.WATTS_STROGATZ:
                self.graph = nx.watts_strogatz_graph(c.n, c.ws_k, c.ws_p, seed=c.seed)

    def _assign_node_attributes(self):
        self.nodes = []
        for nid in self.graph.nodes():
            risk = max(0.0, min(1.0, self.rng.gauss(
                self.config.risk_mean, self.config.risk_std
            )))
            node = Node(
                node_id=nid,
                risk_score=round(risk, 4),
                metadata={"degree": self.graph.degree(nid)},
            )
            self.nodes.append(node)
            self.graph.nodes[nid].update(asdict(node))

    def _assign_edge_types(self):
        types = list(self.config.edge_type_dist.keys())
        weights = list(self.config.edge_type_dist.values())
        self.edges = []
        for u, v in self.graph.edges():
            etype = self.rng.choices(types, weights=weights, k=1)[0]
            lo, hi = EDGE_WEIGHT_RANGES[etype]
            w = round(self.rng.uniform(lo, hi), 4)
            edge = Edge(source=u, target=v, edge_type=etype, weight=w)
            self.edges.append(edge)
            self.graph.edges[u, v].update({
                "edge_type": etype,
                "weight": w,
            })

    # ── Export ────────────────────────────────────────────────────────────

    def to_adjacency_list(self) -> dict[int, list[dict]]:
        """Return adjacency list: {node_id: [{neighbor, edge_type, weight}, ...]}."""
        adj = {n.node_id: [] for n in self.nodes}
        for e in self.edges:
            entry = {"neighbor": e.target, "edge_type": e.edge_type, "weight": e.weight}
            adj[e.source].append(entry)
            entry_rev = {"neighbor": e.source, "edge_type": e.edge_type, "weight": e.weight}
            adj[e.target].append(entry_rev)
        return adj

    def to_edge_list(self) -> list[dict]:
        """Return flat edge list with weights and types."""
        return [asdict(e) for e in self.edges]

    def save(self, out_dir: str = "output"):
        """Save nodes, edge list, and adjacency list to JSON + CSV."""
        p = Path(out_dir)
        p.mkdir(parents=True, exist_ok=True)

        # Nodes JSON
        with open(p / "nodes.json", "w") as f:
            json.dump([asdict(n) for n in self.nodes], f, indent=2)

        # Edge list JSON
        edge_list = self.to_edge_list()
        with open(p / "edge_list.json", "w") as f:
            json.dump(edge_list, f, indent=2)

        # Edge list CSV
        with open(p / "edge_list.csv", "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["source", "target", "edge_type", "weight"])
            writer.writeheader()
            writer.writerows(edge_list)

        # Adjacency list JSON
        adj = self.to_adjacency_list()
        with open(p / "adjacency_list.json", "w") as f:
            json.dump(adj, f, indent=2, default=str)

        # Summary
        summary = self._summary()
        with open(p / "summary.json", "w") as f:
            json.dump(summary, f, indent=2)

        print(f"Saved to {p.resolve()}/")
        for k, v in summary.items():
            print(f"  {k}: {v}")

    def _summary(self) -> dict:
        from collections import Counter
        type_counts = Counter(e.edge_type for e in self.edges)
        return {
            "model": self.config.model.value,
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "edge_type_counts": dict(type_counts),
            "avg_risk_score": round(sum(n.risk_score for n in self.nodes) / len(self.nodes), 4),
            "avg_degree": round(2 * len(self.edges) / len(self.nodes), 2),
            "clustering_coefficient": round(nx.average_clustering(self.graph), 4),
        }


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Identity Graph Generator")
    parser.add_argument("-n", type=int, default=500, help="Number of nodes")
    parser.add_argument("--model", choices=[m.value for m in GraphModel],
                        default="barabasi_albert")
    parser.add_argument("--er-p", type=float, default=0.01)
    parser.add_argument("--ba-m", type=int, default=3)
    parser.add_argument("--ws-k", type=int, default=6)
    parser.add_argument("--ws-p", type=float, default=0.3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("-o", "--output", default="output", help="Output directory")
    args = parser.parse_args()

    config = GraphConfig(
        n=args.n,
        model=GraphModel(args.model),
        er_p=args.er_p,
        ba_m=args.ba_m,
        ws_k=args.ws_k,
        ws_p=args.ws_p,
        seed=args.seed,
    )

    gen = IdentityGraphGenerator(config)
    gen.generate()
    gen.save(args.output)


if __name__ == "__main__":
    main()
