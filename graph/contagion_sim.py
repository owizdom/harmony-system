"""
Contagion Trigger Simulation — Model what happens when a node is flagged.

Injects risk=1.0 at a seed node, runs diffusion, and measures:
  - Number of affected nodes above threshold
  - Score decay over hop distance
  - Graph radius of influence
  - Per-iteration spread wavefront
"""

import json
import math
import copy
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import networkx as nx

from .graph_generator import IdentityGraphGenerator, GraphConfig
from .diffusion_engine import DiffusionEngine, DiffusionConfig, DiffusionMode


# ── Contagion Config ─────────────────────────────────────────────────────────

@dataclass
class ContagionConfig:
    seed_nodes: list[int] = None       # nodes to flag (default: [0])
    seed_risk: float = 1.0             # injected risk score
    risk_threshold: float = 0.1        # "affected" if final score >= this
    max_hops_analysis: int = 10        # how far to measure decay
    diffusion: DiffusionConfig = None  # propagation parameters

    def __post_init__(self):
        if self.seed_nodes is None:
            self.seed_nodes = [0]
        if self.diffusion is None:
            self.diffusion = DiffusionConfig(alpha=0.85, max_iterations=100)


# ── Contagion Result ─────────────────────────────────────────────────────────

@dataclass
class ContagionResult:
    seed_nodes: list[int]
    total_nodes: int
    affected_count: int                     # nodes above threshold
    affected_fraction: float
    influence_radius: int                   # max hop where score > threshold
    iterations_to_converge: int
    score_by_hop: dict[int, float]          # avg score at each hop distance
    affected_by_hop: dict[int, int]         # count of affected nodes per hop
    node_count_by_hop: dict[int, int]       # total nodes per hop
    baseline_scores: dict[int, float]       # pre-injection scores
    triggered_scores: dict[int, float]      # post-injection scores
    score_deltas: dict[int, float]          # triggered - baseline per node
    wavefront: list[dict]                   # per-iteration spread data


# ── Simulator ────────────────────────────────────────────────────────────────

class ContagionSimulator:
    """Simulate contagion spread from flagged seed nodes."""

    def __init__(self, graph: nx.Graph, config: ContagionConfig | None = None):
        self.graph = graph
        self.config = config or ContagionConfig()

    def run(self) -> ContagionResult:
        """Execute full contagion simulation: baseline -> inject -> propagate -> measure."""

        # ── Step 1: Baseline diffusion (no injection) ──
        baseline_engine = DiffusionEngine(self.graph, copy.deepcopy(self.config.diffusion))
        baseline_scores = baseline_engine.propagate()

        # ── Step 2: Inject seed nodes ──
        injected_graph = self.graph.copy()
        for seed in self.config.seed_nodes:
            injected_graph.nodes[seed]["risk_score"] = self.config.seed_risk

        # ── Step 3: Propagate with injection ──
        triggered_engine = DiffusionEngine(injected_graph, copy.deepcopy(self.config.diffusion))
        triggered_scores = triggered_engine.propagate()

        # ── Step 4: Measure impact ──
        return self._analyze(
            baseline_scores, triggered_scores, triggered_engine
        )

    def _analyze(
        self,
        baseline: dict[int, float],
        triggered: dict[int, float],
        engine: DiffusionEngine,
    ) -> ContagionResult:
        threshold = self.config.risk_threshold
        seeds = self.config.seed_nodes

        # Score deltas
        deltas = {n: triggered[n] - baseline[n] for n in baseline}

        # Hop distances from seed(s)
        hop_distances = {}
        for seed in seeds:
            lengths = nx.single_source_shortest_path_length(
                self.graph, seed, cutoff=self.config.max_hops_analysis
            )
            for node, dist in lengths.items():
                if node not in hop_distances or dist < hop_distances[node]:
                    hop_distances[node] = dist

        # Score and affected count by hop
        scores_at_hop = defaultdict(list)
        affected_at_hop = defaultdict(int)
        nodes_at_hop = defaultdict(int)

        for node, dist in hop_distances.items():
            scores_at_hop[dist].append(triggered[node])
            nodes_at_hop[dist] += 1
            if triggered[node] >= threshold:
                affected_at_hop[dist] += 1

        avg_score_by_hop = {
            hop: round(sum(scores) / len(scores), 8)
            for hop, scores in sorted(scores_at_hop.items())
        }

        # Influence radius: max hop where any node is affected
        influence_radius = 0
        for hop in sorted(affected_at_hop.keys(), reverse=True):
            if affected_at_hop[hop] > 0:
                influence_radius = hop
                break

        # Total affected
        affected = [n for n, s in triggered.items() if s >= threshold]

        # Wavefront: per-iteration spread
        wavefront = []
        for snap in engine.history:
            above = sum(1 for s in snap.scores.values() if s >= threshold)
            wavefront.append({
                "iteration": snap.iteration,
                "nodes_above_threshold": above,
                "delta_l2": round(snap.delta_l2, 10),
            })

        return ContagionResult(
            seed_nodes=seeds,
            total_nodes=len(baseline),
            affected_count=len(affected),
            affected_fraction=round(len(affected) / len(baseline), 6),
            influence_radius=influence_radius,
            iterations_to_converge=len(engine.history),
            score_by_hop=dict(avg_score_by_hop),
            affected_by_hop=dict(affected_at_hop),
            node_count_by_hop=dict(nodes_at_hop),
            baseline_scores=baseline,
            triggered_scores=triggered,
            score_deltas=deltas,
            wavefront=wavefront,
        )

    def run_multi_seed(self, seed_list: list[int]) -> list[ContagionResult]:
        """Run independent contagion simulations from multiple seed nodes."""
        results = []
        for seed in seed_list:
            cfg = ContagionConfig(
                seed_nodes=[seed],
                seed_risk=self.config.seed_risk,
                risk_threshold=self.config.risk_threshold,
                max_hops_analysis=self.config.max_hops_analysis,
                diffusion=copy.deepcopy(self.config.diffusion),
            )
            sim = ContagionSimulator(self.graph, cfg)
            results.append(sim.run())
        return results


# ── Persistence ──────────────────────────────────────────────────────────────

def save_contagion_result(result: ContagionResult, out_dir: str = "output/contagion"):
    p = Path(out_dir)
    p.mkdir(parents=True, exist_ok=True)

    summary = {
        "seed_nodes": result.seed_nodes,
        "total_nodes": result.total_nodes,
        "affected_count": result.affected_count,
        "affected_fraction": result.affected_fraction,
        "influence_radius": result.influence_radius,
        "iterations_to_converge": result.iterations_to_converge,
        "score_by_hop": result.score_by_hop,
        "affected_by_hop": result.affected_by_hop,
        "node_count_by_hop": result.node_count_by_hop,
        "wavefront": result.wavefront,
    }
    with open(p / "contagion_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    with open(p / "score_deltas.json", "w") as f:
        json.dump(
            {str(k): round(v, 8) for k, v in sorted(
                result.score_deltas.items(), key=lambda x: x[1], reverse=True
            )},
            f, indent=2,
        )

    print(f"Contagion results saved to {p.resolve()}/")
    return summary


# ── Demo ─────────────────────────────────────────────────────────────────────

def main():
    gen = IdentityGraphGenerator(GraphConfig(n=300, seed=42))
    G = gen.generate()

    # Pick the highest-degree node as seed (worst-case spreader)
    hub = max(G.nodes(), key=lambda n: G.degree(n))
    print(f"Seed node: {hub}  (degree={G.degree(hub)})")

    config = ContagionConfig(
        seed_nodes=[hub],
        seed_risk=1.0,
        risk_threshold=0.1,
        diffusion=DiffusionConfig(alpha=0.85, max_iterations=100),
    )
    sim = ContagionSimulator(G, config)
    result = sim.run()

    print(f"\n{'='*60}")
    print(f"CONTAGION SIMULATION RESULTS")
    print(f"{'='*60}")
    print(f"  Affected nodes: {result.affected_count}/{result.total_nodes} "
          f"({result.affected_fraction:.1%})")
    print(f"  Influence radius: {result.influence_radius} hops")
    print(f"  Iterations to converge: {result.iterations_to_converge}")

    print(f"\n── Score Decay by Hop ──")
    for hop, score in result.score_by_hop.items():
        affected = result.affected_by_hop.get(hop, 0)
        total = result.node_count_by_hop.get(hop, 0)
        bar = "#" * int(score * 50)
        print(f"  hop {hop:<3} avg_score={score:.6f}  affected={affected}/{total}  {bar}")

    print(f"\n── Wavefront (first 10 iterations) ──")
    for w in result.wavefront[:10]:
        print(f"  iter {w['iteration']:<3}  nodes_above_threshold={w['nodes_above_threshold']:<4}  "
              f"ΔL2={w['delta_l2']:.2e}")

    # Compare different seeds
    print(f"\n── Multi-Seed Comparison (top 5 hubs) ──")
    top_hubs = sorted(G.nodes(), key=lambda n: G.degree(n), reverse=True)[:5]
    multi_results = sim.run_multi_seed(top_hubs)
    for r in multi_results:
        seed = r.seed_nodes[0]
        print(f"  seed={seed:<4} deg={G.degree(seed):<4} "
              f"affected={r.affected_count:<4} radius={r.influence_radius}")

    save_contagion_result(result)


if __name__ == "__main__":
    main()
