"""
Diffusion Engine — Iterative influence propagation over identity graphs.

PageRank-style score propagation with:
  - Synchronous (round-based) and asynchronous (event-based) modes
  - Configurable dampening, convergence, and iteration limits
  - Typed edge weights from the influence engine
  - Per-iteration history for analysis
"""

import heapq
import json
import math
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

import networkx as nx

from .graph_generator import EdgeType, IdentityGraphGenerator, GraphConfig
from .influence_engine import EDGE_WEIGHTS


# ── Diffusion Config ─────────────────────────────────────────────────────────

class DiffusionMode(str, Enum):
    SYNCHRONOUS = "synchronous"    # all nodes update simultaneously per round
    ASYNCHRONOUS = "asynchronous"  # nodes update one-at-a-time, priority-driven


@dataclass
class DiffusionConfig:
    alpha: float = 0.85              # propagation weight (like PageRank damping)
    max_iterations: int = 100        # hard stop
    convergence_threshold: float = 1e-6  # L2 norm of score delta
    mode: DiffusionMode = DiffusionMode.SYNCHRONOUS

    # Edge weights by type
    edge_weights: dict | None = None

    # Base score: each node's intrinsic score (risk_score from graph)
    use_risk_as_base: bool = True    # use node risk_score as base_score
    default_base_score: float = 0.0  # fallback if use_risk_as_base=False

    # Normalization
    normalize_weights: bool = True   # row-normalize outgoing weights per node

    # Async-specific
    async_schedule: str = "max_delta"  # "max_delta" | "round_robin"

    def __post_init__(self):
        if self.edge_weights is None:
            self.edge_weights = dict(EDGE_WEIGHTS)


# ── Iteration Snapshot ───────────────────────────────────────────────────────

@dataclass
class DiffusionSnapshot:
    iteration: int
    scores: dict[int, float]
    delta_l2: float
    max_delta_node: int | None = None
    max_delta_value: float = 0.0


# ── Diffusion Engine ─────────────────────────────────────────────────────────

class DiffusionEngine:
    """Core diffusion/propagation engine over an identity graph."""

    def __init__(self, graph: nx.Graph, config: DiffusionConfig | None = None):
        self.graph = graph
        self.config = config or DiffusionConfig()
        self.scores: dict[int, float] = {}
        self.base_scores: dict[int, float] = {}
        self.history: list[DiffusionSnapshot] = []
        self._edge_weight_cache: dict[tuple[int, int], float] = {}
        self._init_scores()
        self._cache_edge_weights()

    def _init_scores(self):
        """Initialize propagation scores and base scores from node attributes."""
        for nid in self.graph.nodes():
            if self.config.use_risk_as_base:
                base = self.graph.nodes[nid].get("risk_score", self.config.default_base_score)
            else:
                base = self.config.default_base_score
            self.base_scores[nid] = base
            self.scores[nid] = base

    def _cache_edge_weights(self):
        """Pre-compute typed edge weights, optionally row-normalized."""
        # Raw weights
        raw: dict[int, dict[int, float]] = {n: {} for n in self.graph.nodes()}
        for u, v, data in self.graph.edges(data=True):
            etype = EdgeType(data.get("edge_type", "weak_signal"))
            w = self.config.edge_weights.get(etype, 0.1)
            raw[u][v] = w
            raw[v][u] = w  # undirected

        # Normalize per node (row-normalize)
        for u in raw:
            total = sum(raw[u].values())
            for v in raw[u]:
                if self.config.normalize_weights and total > 0:
                    self._edge_weight_cache[(u, v)] = raw[u][v] / total
                else:
                    self._edge_weight_cache[(u, v)] = raw[u][v]

    # ── Propagation Core ─────────────────────────────────────────────────

    def propagate(self) -> dict[int, float]:
        """Run diffusion to convergence. Dispatches to sync or async mode."""
        self.history.clear()

        if self.config.mode == DiffusionMode.SYNCHRONOUS:
            return self._propagate_sync()
        else:
            return self._propagate_async()

    def _propagate_sync(self) -> dict[int, float]:
        """Synchronous (round-based): all nodes update simultaneously.

        score_next[i] = alpha * sum(w_ij * score[j] for j in neighbors(i)) + (1 - alpha) * base[i]
        """
        nodes = list(self.graph.nodes())

        for iteration in range(1, self.config.max_iterations + 1):
            new_scores = {}

            for node in nodes:
                neighbor_sum = 0.0
                for neighbor in self.graph.neighbors(node):
                    w = self._edge_weight_cache.get((node, neighbor), 0.0)
                    neighbor_sum += w * self.scores[neighbor]

                new_scores[node] = (
                    self.config.alpha * neighbor_sum
                    + (1 - self.config.alpha) * self.base_scores[node]
                )

            # Convergence check
            delta_l2, max_node, max_val = self._compute_delta(self.scores, new_scores)

            self.scores = new_scores
            self.history.append(DiffusionSnapshot(
                iteration=iteration,
                scores=dict(self.scores),
                delta_l2=delta_l2,
                max_delta_node=max_node,
                max_delta_value=max_val,
            ))

            if delta_l2 < self.config.convergence_threshold:
                break

        return dict(self.scores)

    def _propagate_async(self) -> dict[int, float]:
        """Asynchronous (event-based): nodes update one at a time.

        Priority queue drives update order — node with largest pending delta updates first.
        Changes propagate immediately to neighbors.
        """
        nodes = list(self.graph.nodes())

        # Initial pass: compute each node's "pending" new score
        pending = {}
        for node in nodes:
            pending[node] = self._compute_node_score(node)

        # Priority queue: (-abs_delta, node_id) — max-heap via negation
        if self.config.async_schedule == "max_delta":
            pq = []
            for node in nodes:
                delta = abs(pending[node] - self.scores[node])
                heapq.heappush(pq, (-delta, node))
        else:
            # Round-robin: just cycle through nodes
            pq = deque(nodes)

        updates_this_round = 0
        iteration = 0

        while iteration < self.config.max_iterations:
            iteration += 1
            old_scores = dict(self.scores)

            # Process one full pass (N updates = 1 "iteration")
            updates_this_round = 0
            visited = set()

            while len(visited) < len(nodes):
                if self.config.async_schedule == "max_delta":
                    if not pq:
                        break
                    _, node = heapq.heappop(pq)
                    if node in visited:
                        continue
                else:
                    node = pq[0]
                    pq.rotate(-1)
                    if node in visited:
                        continue

                visited.add(node)

                new_val = self._compute_node_score(node)
                self.scores[node] = new_val
                updates_this_round += 1

                # Re-queue affected neighbors
                if self.config.async_schedule == "max_delta":
                    for neighbor in self.graph.neighbors(node):
                        if neighbor not in visited:
                            new_neighbor = self._compute_node_score(neighbor)
                            delta = abs(new_neighbor - self.scores[neighbor])
                            heapq.heappush(pq, (-delta, neighbor))

            # Convergence over the full iteration
            delta_l2, max_node, max_val = self._compute_delta(old_scores, self.scores)

            self.history.append(DiffusionSnapshot(
                iteration=iteration,
                scores=dict(self.scores),
                delta_l2=delta_l2,
                max_delta_node=max_node,
                max_delta_value=max_val,
            ))

            if delta_l2 < self.config.convergence_threshold:
                break

            # Rebuild priority queue for next iteration
            if self.config.async_schedule == "max_delta":
                pq = []
                for node in nodes:
                    new_val = self._compute_node_score(node)
                    delta = abs(new_val - self.scores[node])
                    heapq.heappush(pq, (-delta, node))

        return dict(self.scores)

    def _compute_node_score(self, node: int) -> float:
        """Compute updated score for a single node from current neighbor scores."""
        neighbor_sum = 0.0
        for neighbor in self.graph.neighbors(node):
            w = self._edge_weight_cache.get((node, neighbor), 0.0)
            neighbor_sum += w * self.scores[neighbor]

        return (
            self.config.alpha * neighbor_sum
            + (1 - self.config.alpha) * self.base_scores[node]
        )

    def _compute_delta(
        self, old: dict[int, float], new: dict[int, float]
    ) -> tuple[float, int | None, float]:
        """L2 norm of score change; also track node with max change."""
        sum_sq = 0.0
        max_node = None
        max_val = 0.0
        for nid in old:
            d = abs(new[nid] - old[nid])
            sum_sq += d * d
            if d > max_val:
                max_val = d
                max_node = nid
        return math.sqrt(sum_sq), max_node, max_val

    # ── Analysis ─────────────────────────────────────────────────────────

    def top_scores(self, k: int = 20) -> list[tuple[int, float]]:
        """Return top-k nodes by final propagated score."""
        ranked = sorted(self.scores.items(), key=lambda x: x[1], reverse=True)
        return ranked[:k]

    def convergence_curve(self) -> list[dict]:
        """Return iteration-by-iteration convergence data."""
        return [
            {
                "iteration": s.iteration,
                "delta_l2": round(s.delta_l2, 10),
                "max_delta_node": s.max_delta_node,
                "max_delta_value": round(s.max_delta_value, 10),
            }
            for s in self.history
        ]

    def score_distribution(self, buckets: int = 10) -> dict[str, int]:
        """Histogram of final scores."""
        vals = list(self.scores.values())
        if not vals:
            return {}
        lo, hi = min(vals), max(vals)
        if hi == lo:
            return {f"{lo:.4f}": len(vals)}
        step = (hi - lo) / buckets
        dist = {}
        for i in range(buckets):
            bucket_lo = lo + i * step
            bucket_hi = lo + (i + 1) * step
            label = f"{bucket_lo:.4f}-{bucket_hi:.4f}"
            count = sum(1 for v in vals if bucket_lo <= v < bucket_hi)
            if i == buckets - 1:
                count = sum(1 for v in vals if bucket_lo <= v <= bucket_hi)
            dist[label] = count
        return dist

    def save(self, out_dir: str = "output"):
        """Save final scores and convergence data."""
        p = Path(out_dir)
        p.mkdir(parents=True, exist_ok=True)

        with open(p / "diffusion_scores.json", "w") as f:
            json.dump(
                {str(k): round(v, 8) for k, v in self.scores.items()},
                f, indent=2,
            )

        with open(p / "convergence.json", "w") as f:
            json.dump(self.convergence_curve(), f, indent=2)

        summary = {
            "mode": self.config.mode.value,
            "alpha": self.config.alpha,
            "iterations": len(self.history),
            "converged": (
                len(self.history) > 0
                and self.history[-1].delta_l2 < self.config.convergence_threshold
            ),
            "final_delta_l2": round(self.history[-1].delta_l2, 12) if self.history else None,
            "top_10": [
                {"node": nid, "score": round(s, 8)} for nid, s in self.top_scores(10)
            ],
            "score_distribution": self.score_distribution(),
        }
        with open(p / "diffusion_summary.json", "w") as f:
            json.dump(summary, f, indent=2)

        print(f"Diffusion results saved to {p.resolve()}/")
        print(f"  Mode: {summary['mode']}")
        print(f"  Iterations: {summary['iterations']}")
        print(f"  Converged: {summary['converged']}")
        print(f"  Final ΔL2: {summary['final_delta_l2']}")
        return summary


# ── Demo ─────────────────────────────────────────────────────────────────────

def main():
    gen = IdentityGraphGenerator(GraphConfig(n=300, seed=42))
    G = gen.generate()

    print("=" * 60)
    print("DIFFUSION ENGINE DEMO")
    print("=" * 60)

    # ── Synchronous ──
    print("\n── Synchronous Propagation ──")
    config_sync = DiffusionConfig(
        alpha=0.85,
        max_iterations=100,
        convergence_threshold=1e-6,
        mode=DiffusionMode.SYNCHRONOUS,
    )
    engine_sync = DiffusionEngine(G, config_sync)
    t0 = time.perf_counter()
    engine_sync.propagate()
    dt_sync = time.perf_counter() - t0

    print(f"  Time: {dt_sync:.4f}s")
    print(f"  Iterations: {len(engine_sync.history)}")
    print(f"  Final ΔL2: {engine_sync.history[-1].delta_l2:.2e}")
    print(f"  Top 5 nodes:")
    for nid, score in engine_sync.top_scores(5):
        risk = G.nodes[nid].get("risk_score", 0)
        deg = G.degree(nid)
        print(f"    node {nid}: score={score:.6f}  risk={risk:.4f}  degree={deg}")

    # ── Asynchronous (max_delta) ──
    print("\n── Asynchronous Propagation (max_delta priority) ──")
    config_async = DiffusionConfig(
        alpha=0.85,
        max_iterations=100,
        convergence_threshold=1e-6,
        mode=DiffusionMode.ASYNCHRONOUS,
        async_schedule="max_delta",
    )
    engine_async = DiffusionEngine(G, config_async)
    t0 = time.perf_counter()
    engine_async.propagate()
    dt_async = time.perf_counter() - t0

    print(f"  Time: {dt_async:.4f}s")
    print(f"  Iterations: {len(engine_async.history)}")
    print(f"  Final ΔL2: {engine_async.history[-1].delta_l2:.2e}")
    print(f"  Top 5 nodes:")
    for nid, score in engine_async.top_scores(5):
        risk = G.nodes[nid].get("risk_score", 0)
        deg = G.degree(nid)
        print(f"    node {nid}: score={score:.6f}  risk={risk:.4f}  degree={deg}")

    # ── Convergence comparison ──
    print("\n── Convergence Comparison ──")
    print(f"  {'Iter':<6} {'Sync ΔL2':<18} {'Async ΔL2':<18}")
    max_len = max(len(engine_sync.history), len(engine_async.history))
    for i in range(min(max_len, 15)):
        s = engine_sync.history[i].delta_l2 if i < len(engine_sync.history) else None
        a = engine_async.history[i].delta_l2 if i < len(engine_async.history) else None
        s_str = f"{s:.2e}" if s is not None else "-"
        a_str = f"{a:.2e}" if a is not None else "-"
        print(f"  {i+1:<6} {s_str:<18} {a_str:<18}")

    # ── Alpha sensitivity ──
    print("\n── Alpha Sensitivity ──")
    for alpha in [0.5, 0.7, 0.85, 0.95]:
        cfg = DiffusionConfig(alpha=alpha, max_iterations=200, convergence_threshold=1e-6)
        eng = DiffusionEngine(G, cfg)
        eng.propagate()
        top1 = eng.top_scores(1)[0]
        print(f"  α={alpha:.2f}  iters={len(eng.history):<4}  top_score={top1[1]:.6f}  (node {top1[0]})")

    engine_sync.save("output/diffusion_sync")
    engine_async.save("output/diffusion_async")


if __name__ == "__main__":
    main()
