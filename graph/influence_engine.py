"""
Influence Weight Engine — Pluggable edge influence computation with decay models.

Computes directional influence between nodes using:
  - Typed edge base weights
  - Configurable decay functions (linear, exponential, threshold)
  - Node risk score amplification
"""

import math
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol

import networkx as nx

from .graph_generator import EdgeType, IdentityGraphGenerator, GraphConfig


# ── Edge Weight Config ───────────────────────────────────────────────────────

EDGE_WEIGHTS = {
    EdgeType.FAMILY: 0.8,
    EdgeType.COWORKER: 0.4,
    EdgeType.FRIEND: 0.6,
    EdgeType.WEAK_SIGNAL: 0.1,
}


# ── Decay Models ─────────────────────────────────────────────────────────────

class DecayType(str, Enum):
    NONE = "none"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"
    THRESHOLD = "threshold"


def no_decay(distance: int, **kwargs) -> float:
    """No decay — full influence regardless of distance."""
    return 1.0


def linear_decay(distance: int, max_distance: int = 10, **kwargs) -> float:
    """Influence drops linearly to zero at max_distance.

    f(d) = max(0, 1 - d / max_distance)
    """
    if distance >= max_distance:
        return 0.0
    return 1.0 - (distance / max_distance)


def exponential_decay(distance: int, half_life: float = 3.0, **kwargs) -> float:
    """Influence halves every `half_life` hops.

    f(d) = 2^(-d / half_life)
    """
    return math.pow(2, -distance / half_life)


def threshold_decay(distance: int, cutoff: int = 3, **kwargs) -> float:
    """Full influence within cutoff, zero beyond.

    f(d) = 1 if d <= cutoff else 0
    """
    return 1.0 if distance <= cutoff else 0.0


DECAY_FUNCTIONS: dict[DecayType, Callable] = {
    DecayType.NONE: no_decay,
    DecayType.LINEAR: linear_decay,
    DecayType.EXPONENTIAL: exponential_decay,
    DecayType.THRESHOLD: threshold_decay,
}


# ── Influence Engine ─────────────────────────────────────────────────────────

@dataclass
class InfluenceConfig:
    edge_weights: dict = None           # override EDGE_WEIGHTS
    decay_type: DecayType = DecayType.EXPONENTIAL
    decay_params: dict = None           # kwargs passed to decay function
    risk_amplification: bool = True     # multiply by source risk_score

    def __post_init__(self):
        if self.edge_weights is None:
            self.edge_weights = dict(EDGE_WEIGHTS)
        if self.decay_params is None:
            self.decay_params = {}


class InfluenceEngine:
    """Pluggable influence weight engine over an identity graph."""

    def __init__(self, graph: nx.Graph, config: InfluenceConfig | None = None):
        self.graph = graph
        self.config = config or InfluenceConfig()
        self._decay_fn = DECAY_FUNCTIONS[self.config.decay_type]

    def compute_edge_influence(self, source: int, target: int) -> float:
        """Compute direct (1-hop) influence from source to target.

        influence = base_weight * risk_amplifier

        Returns 0.0 if no direct edge exists.
        """
        if not self.graph.has_edge(source, target):
            return 0.0

        edge_data = self.graph.edges[source, target]
        edge_type = EdgeType(edge_data["edge_type"])
        base_weight = self.config.edge_weights.get(edge_type, 0.1)

        if self.config.risk_amplification:
            source_risk = self.graph.nodes[source].get("risk_score", 0.0)
            return round(base_weight * (1.0 + source_risk), 6)

        return base_weight

    def compute_influence(self, source: int, target: int) -> float:
        """Compute multi-hop influence from source to target with decay.

        Uses shortest path distance and applies the configured decay function.
        Returns 0.0 if no path exists.
        """
        try:
            distance = nx.shortest_path_length(self.graph, source, target)
        except nx.NetworkXNoPath:
            return 0.0

        if distance == 0:
            return 1.0

        # Walk the shortest path, accumulate edge influences
        path = nx.shortest_path(self.graph, source, target)
        path_influence = 1.0
        for i in range(len(path) - 1):
            u, v = path[i], path[i + 1]
            edge_data = self.graph.edges[u, v]
            edge_type = EdgeType(edge_data["edge_type"])
            base_weight = self.config.edge_weights.get(edge_type, 0.1)
            path_influence *= base_weight

        decay = self._decay_fn(distance, **self.config.decay_params)
        influence = path_influence * decay

        if self.config.risk_amplification:
            source_risk = self.graph.nodes[source].get("risk_score", 0.0)
            influence *= (1.0 + source_risk)

        return round(influence, 6)

    def influence_radius(self, source: int, max_hops: int = 5) -> dict[int, float]:
        """Compute influence from source to all reachable nodes within max_hops.

        Returns {node_id: influence_score} sorted descending.
        """
        lengths = nx.single_source_shortest_path_length(self.graph, source, cutoff=max_hops)
        results = {}
        for target, dist in lengths.items():
            if target == source:
                continue
            inf = self.compute_influence(source, target)
            if inf > 0:
                results[target] = inf

        return dict(sorted(results.items(), key=lambda x: x[1], reverse=True))

    def top_influencers(self, target: int, max_hops: int = 5, top_k: int = 10) -> list[tuple[int, float]]:
        """Find nodes with highest influence ON a target node."""
        lengths = nx.single_source_shortest_path_length(self.graph, target, cutoff=max_hops)
        scores = []
        for source, dist in lengths.items():
            if source == target:
                continue
            inf = self.compute_influence(source, target)
            if inf > 0:
                scores.append((source, inf))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def register_decay(self, name: str, fn: Callable):
        """Register a custom decay function at runtime."""
        DECAY_FUNCTIONS[name] = fn
        self.config.decay_type = name
        self._decay_fn = fn


# ── Demo ─────────────────────────────────────────────────────────────────────

def main():
    # Generate a graph
    gen = IdentityGraphGenerator(GraphConfig(n=200, seed=42))
    G = gen.generate()

    print("=" * 60)
    print("INFLUENCE ENGINE DEMO")
    print("=" * 60)

    # Test each decay model
    for decay in DecayType:
        config = InfluenceConfig(decay_type=decay)
        engine = InfluenceEngine(G, config)

        print(f"\n── Decay: {decay.value} ──")

        # Direct edge influence
        edge = list(G.edges())[0]
        s, t = edge
        direct = engine.compute_edge_influence(s, t)
        print(f"  Direct influence {s} → {t}: {direct}")

        # Multi-hop influence
        multi = engine.compute_influence(0, 50)
        print(f"  Multi-hop influence 0 → 50: {multi}")

        # Influence radius from node 0
        radius = engine.influence_radius(0, max_hops=3)
        top5 = list(radius.items())[:5]
        print(f"  Top 5 influenced by node 0 (3 hops):")
        for nid, score in top5:
            print(f"    node {nid}: {score}")

    # Custom decay demo
    print(f"\n── Custom Decay: sigmoid ──")
    config = InfluenceConfig(decay_type=DecayType.EXPONENTIAL)
    engine = InfluenceEngine(G, config)

    def sigmoid_decay(distance: int, steepness: float = 1.5, midpoint: float = 3.0, **kw) -> float:
        return 1.0 / (1.0 + math.exp(steepness * (distance - midpoint)))

    engine.register_decay("sigmoid", sigmoid_decay)
    radius = engine.influence_radius(0, max_hops=4)
    top5 = list(radius.items())[:5]
    print(f"  Top 5 influenced by node 0 (4 hops, sigmoid decay):")
    for nid, score in top5:
        print(f"    node {nid}: {score}")

    # Top influencers on a target
    print(f"\n── Top influencers on node 100 ──")
    config = InfluenceConfig(decay_type=DecayType.EXPONENTIAL)
    engine = InfluenceEngine(G, config)
    top = engine.top_influencers(100, max_hops=3, top_k=5)
    for nid, score in top:
        etype = G.edges[nid, 100]["edge_type"] if G.has_edge(nid, 100) else "indirect"
        print(f"  node {nid} ({etype}): {score}")


if __name__ == "__main__":
    main()
