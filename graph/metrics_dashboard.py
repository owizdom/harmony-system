"""
Second-Order Effect Measurement — Metrics dashboard with visualization.

Quantifies systemic impact of contagion:
  - Score distribution histograms (before/after)
  - Clustering coefficient shift
  - Degree-based vulnerability analysis
  - Threshold crossing counts
  - Time to stabilization
  - 6-panel matplotlib dashboard
"""

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np
import networkx as nx

from .graph_generator import IdentityGraphGenerator, GraphConfig
from .diffusion_engine import DiffusionEngine, DiffusionConfig
from .contagion_sim import ContagionSimulator, ContagionConfig, ContagionResult


# ── Metrics ──────────────────────────────────────────────────────────────────

@dataclass
class SystemicMetrics:
    # Threshold crossings
    nodes_above_threshold_baseline: int
    nodes_above_threshold_triggered: int
    new_crossings: int                       # nodes that crossed threshold due to contagion
    crossing_fraction: float                 # new_crossings / total

    # Score distribution stats
    baseline_mean: float
    baseline_std: float
    triggered_mean: float
    triggered_std: float
    mean_shift: float
    max_delta: float
    max_delta_node: int

    # Clustering
    clustering_baseline: float               # avg clustering coeff (weighted by pre-scores)
    clustering_triggered: float              # avg clustering coeff (weighted by post-scores)
    clustering_shift: float

    # Degree vulnerability
    degree_vulnerability: dict               # {degree_bucket: avg_score_delta}

    # Stabilization
    iterations_to_converge: int
    time_to_90pct_spread: int | None         # iteration where 90% of final affected count reached
    convergence_curve: list[dict]


def compute_metrics(
    graph: nx.Graph,
    result: ContagionResult,
    risk_threshold: float = 0.1,
) -> SystemicMetrics:
    """Compute all second-order effect metrics from a contagion result."""

    baseline = result.baseline_scores
    triggered = result.triggered_scores
    deltas = result.score_deltas
    nodes = list(graph.nodes())

    # ── Threshold crossings ──
    above_baseline = sum(1 for s in baseline.values() if s >= risk_threshold)
    above_triggered = sum(1 for s in triggered.values() if s >= risk_threshold)
    new_crossings = sum(
        1 for n in nodes
        if baseline[n] < risk_threshold and triggered[n] >= risk_threshold
    )

    # ── Score distribution ──
    b_vals = list(baseline.values())
    t_vals = list(triggered.values())
    b_mean, b_std = np.mean(b_vals), np.std(b_vals)
    t_mean, t_std = np.mean(t_vals), np.std(t_vals)

    max_delta_node = max(deltas, key=lambda n: deltas[n])
    max_delta = deltas[max_delta_node]

    # ── Clustering coefficient shift ──
    # Weight clustering by node score to see if high-cluster regions get hit harder
    clustering = nx.clustering(graph)
    weighted_cluster_base = sum(
        clustering[n] * baseline[n] for n in nodes
    ) / max(sum(baseline.values()), 1e-12)
    weighted_cluster_trig = sum(
        clustering[n] * triggered[n] for n in nodes
    ) / max(sum(triggered.values()), 1e-12)

    # ── Degree-based vulnerability ──
    degree_deltas = defaultdict(list)
    for n in nodes:
        deg = graph.degree(n)
        bucket = (deg // 5) * 5  # bucket by 5s
        degree_deltas[bucket].append(deltas[n])

    degree_vuln = {
        bucket: round(np.mean(vals), 8)
        for bucket, vals in sorted(degree_deltas.items())
    }

    # ── Time to 90% spread ──
    final_affected = result.affected_count
    target_90 = int(final_affected * 0.9)
    time_90 = None
    for w in result.wavefront:
        if w["nodes_above_threshold"] >= target_90:
            time_90 = w["iteration"]
            break

    return SystemicMetrics(
        nodes_above_threshold_baseline=above_baseline,
        nodes_above_threshold_triggered=above_triggered,
        new_crossings=new_crossings,
        crossing_fraction=round(new_crossings / len(nodes), 6),
        baseline_mean=round(float(b_mean), 8),
        baseline_std=round(float(b_std), 8),
        triggered_mean=round(float(t_mean), 8),
        triggered_std=round(float(t_std), 8),
        mean_shift=round(float(t_mean - b_mean), 8),
        max_delta=round(max_delta, 8),
        max_delta_node=max_delta_node,
        clustering_baseline=round(weighted_cluster_base, 8),
        clustering_triggered=round(weighted_cluster_trig, 8),
        clustering_shift=round(weighted_cluster_trig - weighted_cluster_base, 8),
        degree_vulnerability=degree_vuln,
        iterations_to_converge=result.iterations_to_converge,
        time_to_90pct_spread=time_90,
        convergence_curve=[
            {"iteration": w["iteration"], "delta_l2": w["delta_l2"]}
            for w in result.wavefront
        ],
    )


# ── Dashboard ────────────────────────────────────────────────────────────────

def render_dashboard(
    graph: nx.Graph,
    result: ContagionResult,
    metrics: SystemicMetrics,
    save_path: str = "output/dashboard.png",
):
    """Render a 6-panel matplotlib dashboard."""

    fig = plt.figure(figsize=(18, 12))
    fig.suptitle(
        f"Contagion Impact Dashboard  |  Seed: {result.seed_nodes}  |  "
        f"Affected: {result.affected_count}/{result.total_nodes} "
        f"({result.affected_fraction:.1%})",
        fontsize=14, fontweight="bold",
    )
    gs = gridspec.GridSpec(2, 3, hspace=0.35, wspace=0.3)

    baseline = list(result.baseline_scores.values())
    triggered = list(result.triggered_scores.values())
    deltas = list(result.score_deltas.values())

    # ── Panel 1: Score distribution before/after ──
    ax1 = fig.add_subplot(gs[0, 0])
    bins = np.linspace(0, max(max(baseline), max(triggered)) * 1.1, 40)
    ax1.hist(baseline, bins=bins, alpha=0.6, label="Baseline", color="#4A90D9", edgecolor="white")
    ax1.hist(triggered, bins=bins, alpha=0.6, label="Triggered", color="#D94A4A", edgecolor="white")
    ax1.axvline(metrics.baseline_mean, color="#2563EB", linestyle="--", linewidth=1.5, label=f"Base μ={metrics.baseline_mean:.4f}")
    ax1.axvline(metrics.triggered_mean, color="#DC2626", linestyle="--", linewidth=1.5, label=f"Trig μ={metrics.triggered_mean:.4f}")
    ax1.set_xlabel("Risk Score")
    ax1.set_ylabel("Node Count")
    ax1.set_title("Score Distribution (Before / After)")
    ax1.legend(fontsize=8)

    # ── Panel 2: Score decay by hop distance ──
    ax2 = fig.add_subplot(gs[0, 1])
    hops = sorted(result.score_by_hop.keys())
    scores_by_hop = [result.score_by_hop[h] for h in hops]
    affected_by_hop = [result.affected_by_hop.get(h, 0) for h in hops]
    nodes_by_hop = [result.node_count_by_hop.get(h, 1) for h in hops]
    frac_by_hop = [a / max(n, 1) for a, n in zip(affected_by_hop, nodes_by_hop)]

    color_score = "#2563EB"
    color_frac = "#DC2626"
    ax2.bar(hops, scores_by_hop, alpha=0.7, color=color_score, label="Avg Score")
    ax2_twin = ax2.twinx()
    ax2_twin.plot(hops, frac_by_hop, "o-", color=color_frac, linewidth=2, label="Affected %")
    ax2_twin.set_ylabel("Fraction Affected", color=color_frac)
    ax2.set_xlabel("Hop Distance from Seed")
    ax2.set_ylabel("Avg Score", color=color_score)
    ax2.set_title("Score Decay by Hop Distance")
    lines1, labels1 = ax2.get_legend_handles_labels()
    lines2, labels2 = ax2_twin.get_legend_handles_labels()
    ax2.legend(lines1 + lines2, labels1 + labels2, fontsize=8)

    # ── Panel 3: Convergence curve ──
    ax3 = fig.add_subplot(gs[0, 2])
    iters = [c["iteration"] for c in metrics.convergence_curve]
    delta_l2 = [c["delta_l2"] for c in metrics.convergence_curve]
    ax3.semilogy(iters, delta_l2, "o-", color="#7C3AED", markersize=3, linewidth=1.5)
    ax3.axhline(1e-6, color="gray", linestyle=":", alpha=0.7, label="Threshold (1e-6)")
    if metrics.time_to_90pct_spread:
        ax3.axvline(metrics.time_to_90pct_spread, color="#F59E0B", linestyle="--",
                     label=f"90% spread @ iter {metrics.time_to_90pct_spread}")
    ax3.set_xlabel("Iteration")
    ax3.set_ylabel("ΔL2 (log scale)")
    ax3.set_title("Convergence Curve")
    ax3.legend(fontsize=8)

    # ── Panel 4: Degree-based vulnerability ──
    ax4 = fig.add_subplot(gs[1, 0])
    deg_buckets = sorted(metrics.degree_vulnerability.keys())
    deg_deltas = [metrics.degree_vulnerability[b] for b in deg_buckets]
    labels = [f"{b}-{b+4}" for b in deg_buckets]
    colors = ["#DC2626" if d > 0 else "#2563EB" for d in deg_deltas]
    ax4.barh(labels, deg_deltas, color=colors, alpha=0.8)
    ax4.set_xlabel("Avg Score Delta")
    ax4.set_ylabel("Degree Bucket")
    ax4.set_title("Degree-Based Vulnerability")
    ax4.axvline(0, color="black", linewidth=0.5)

    # ── Panel 5: Wavefront (nodes above threshold over time) ──
    ax5 = fig.add_subplot(gs[1, 1])
    wave_iters = [w["iteration"] for w in result.wavefront]
    wave_counts = [w["nodes_above_threshold"] for w in result.wavefront]
    ax5.fill_between(wave_iters, wave_counts, alpha=0.3, color="#F59E0B")
    ax5.plot(wave_iters, wave_counts, "-", color="#D97706", linewidth=2)
    ax5.axhline(result.affected_count, color="gray", linestyle=":", alpha=0.5)
    ax5.set_xlabel("Iteration")
    ax5.set_ylabel("Nodes Above Threshold")
    ax5.set_title(f"Wavefront Spread (threshold={0.1})")

    # ── Panel 6: Summary stats table ──
    ax6 = fig.add_subplot(gs[1, 2])
    ax6.axis("off")
    table_data = [
        ["Metric", "Value"],
        ["Seed Nodes", str(result.seed_nodes)],
        ["Total Nodes", str(result.total_nodes)],
        ["New Threshold Crossings", f"{metrics.new_crossings} ({metrics.crossing_fraction:.1%})"],
        ["Influence Radius", f"{result.influence_radius} hops"],
        ["Iterations to Converge", str(metrics.iterations_to_converge)],
        ["Time to 90% Spread", str(metrics.time_to_90pct_spread or "N/A")],
        ["Mean Shift", f"{metrics.mean_shift:+.6f}"],
        ["Max Node Delta", f"{metrics.max_delta:.6f} (node {metrics.max_delta_node})"],
        ["Clustering Shift", f"{metrics.clustering_shift:+.6f}"],
        ["Baseline σ", f"{metrics.baseline_std:.6f}"],
        ["Triggered σ", f"{metrics.triggered_std:.6f}"],
    ]
    table = ax6.table(
        cellText=table_data[1:],
        colLabels=table_data[0],
        cellLoc="left",
        loc="center",
        colWidths=[0.55, 0.45],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 1.4)
    # Header styling
    for (row, col), cell in table.get_celld().items():
        if row == 0:
            cell.set_facecolor("#374151")
            cell.set_text_props(color="white", fontweight="bold")
        elif row % 2 == 0:
            cell.set_facecolor("#F3F4F6")
    ax6.set_title("Summary", fontweight="bold")

    Path(save_path).parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(save_path, dpi=150, bbox_inches="tight", facecolor="white")
    print(f"Dashboard saved to {save_path}")
    plt.close()


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Contagion Metrics Dashboard")
    parser.add_argument("-n", type=int, default=300, help="Number of nodes")
    parser.add_argument("--seed-node", type=int, default=None, help="Node to flag (default: highest degree)")
    parser.add_argument("--threshold", type=float, default=0.1, help="Risk threshold")
    parser.add_argument("--alpha", type=float, default=0.85, help="Diffusion dampening")
    parser.add_argument("-o", "--output", default="output/dashboard.png")
    args = parser.parse_args()

    # Generate graph
    gen = IdentityGraphGenerator(GraphConfig(n=args.n, seed=42))
    G = gen.generate()

    # Pick seed
    seed_node = args.seed_node
    if seed_node is None:
        seed_node = max(G.nodes(), key=lambda n: G.degree(n))
    print(f"Graph: {args.n} nodes, {G.number_of_edges()} edges")
    print(f"Seed node: {seed_node} (degree={G.degree(seed_node)})")

    # Run contagion
    config = ContagionConfig(
        seed_nodes=[seed_node],
        seed_risk=1.0,
        risk_threshold=args.threshold,
        diffusion=DiffusionConfig(alpha=args.alpha, max_iterations=150),
    )
    sim = ContagionSimulator(G, config)
    result = sim.run()

    # Compute metrics
    metrics = compute_metrics(G, result, risk_threshold=args.threshold)

    # Print summary
    print(f"\n{'='*60}")
    print("SYSTEMIC IMPACT METRICS")
    print(f"{'='*60}")
    print(f"  Threshold crossings:  {metrics.new_crossings} new "
          f"({metrics.nodes_above_threshold_baseline} -> {metrics.nodes_above_threshold_triggered})")
    print(f"  Mean score shift:     {metrics.mean_shift:+.6f}")
    print(f"  Max node delta:       {metrics.max_delta:.6f} (node {metrics.max_delta_node})")
    print(f"  Clustering shift:     {metrics.clustering_shift:+.6f}")
    print(f"  Influence radius:     {result.influence_radius} hops")
    print(f"  Converged in:         {metrics.iterations_to_converge} iterations")
    print(f"  90% spread at:        iter {metrics.time_to_90pct_spread or 'N/A'}")
    print(f"\n  Degree vulnerability:")
    for bucket, delta in metrics.degree_vulnerability.items():
        bar = "+" * max(1, int(delta * 200)) if delta > 0 else ""
        print(f"    deg {bucket:>3}-{bucket+4:<3}: Δ={delta:+.6f}  {bar}")

    # Render dashboard
    render_dashboard(G, result, metrics, save_path=args.output)

    # Save metrics JSON
    out_dir = Path(args.output).parent
    with open(out_dir / "systemic_metrics.json", "w") as f:
        json.dump({
            "new_crossings": metrics.new_crossings,
            "crossing_fraction": metrics.crossing_fraction,
            "mean_shift": metrics.mean_shift,
            "max_delta": metrics.max_delta,
            "max_delta_node": metrics.max_delta_node,
            "clustering_shift": metrics.clustering_shift,
            "influence_radius": result.influence_radius,
            "iterations_to_converge": metrics.iterations_to_converge,
            "time_to_90pct_spread": metrics.time_to_90pct_spread,
            "degree_vulnerability": metrics.degree_vulnerability,
        }, f, indent=2)


if __name__ == "__main__":
    main()
