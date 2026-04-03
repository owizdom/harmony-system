"""
Graph Bridge — Connects the citizen scoring system to the identity graph modules.

Translates between:
  - citizen_id (str) / civic_score (0-1000) ↔ node_id (int) / risk_score (0.0-1.0)
  - SQLite relationship rows ↔ NetworkX typed edges

Provides high-level methods for contagion simulation, influence analysis,
and network metrics over the live citizen database.
"""

import copy
import logging
from dataclasses import dataclass

import networkx as nx

from graph import (
    EdgeType,
    EDGE_WEIGHTS,
    InfluenceEngine,
    InfluenceConfig,
    DecayType,
    DiffusionEngine,
    DiffusionConfig,
    DiffusionMode,
    ContagionSimulator,
    ContagionConfig,
    ContagionResult,
    compute_metrics,
    SystemicMetrics,
)

logger = logging.getLogger(__name__)

# Valid edge types that map to the graph module's EdgeType enum
VALID_EDGE_TYPES = {"family", "coworker", "friend", "weak_signal"}


def civic_to_risk(civic_score: int) -> float:
    """Convert civic_score (0-1000) → risk_score (0.0-1.0).

    Low civic score = high risk. Score of 500 (neutral) → 0.5 risk.
    """
    return round(max(0.0, min(1.0, 1.0 - (civic_score / 1000.0))), 6)


def risk_to_civic(risk_score: float) -> int:
    """Convert risk_score (0.0-1.0) → civic_score (0-1000)."""
    return max(0, min(1000, int(round((1.0 - risk_score) * 1000))))


@dataclass
class NetworkAnalysis:
    """Results of a network-level analysis for a citizen."""
    citizen_id: str
    node_id: int
    network_risk_score: float        # propagated risk via diffusion
    influence_reach: int             # nodes within influence radius
    top_influencers: list            # [{citizen_id, influence, edge_type}]
    top_influenced: list             # [{citizen_id, influence, edge_type}]
    connection_count: int
    avg_neighbor_risk: float


@dataclass
class ContagionAnalysis:
    """Results of contagion simulation triggered by a citizen."""
    seed_citizen_id: str
    affected_citizens: list          # [{citizen_id, risk_delta, new_risk}]
    affected_count: int
    affected_fraction: float
    influence_radius: int
    iterations: int
    score_adjustments: dict          # {citizen_id: civic_score_delta}
    metrics: dict                    # systemic metrics summary


class GraphBridge:
    """Bridge between citizen DB and identity graph analysis modules."""

    def __init__(self, db, config=None):
        self.db = db
        self.config = config or {}
        self._graph = None
        self._id_to_node = {}     # citizen_id → int node_id
        self._node_to_id = {}     # int node_id → citizen_id
        self._stale = True        # rebuild graph on next access

    def invalidate(self):
        """Mark graph as stale — will rebuild on next operation."""
        self._stale = True

    def _build_graph(self):
        """Build NetworkX graph from current DB state."""
        citizens = self.db.get_all_citizens()
        relationships = self.db.get_all_relationships()

        if not citizens:
            self._graph = nx.Graph()
            self._id_to_node = {}
            self._node_to_id = {}
            self._stale = False
            return

        # Map citizen_id → sequential int node_id
        self._id_to_node = {}
        self._node_to_id = {}
        for i, cid in enumerate(sorted(citizens.keys())):
            self._id_to_node[cid] = i
            self._node_to_id[i] = cid

        G = nx.Graph()

        # Add nodes with risk_score derived from civic_score
        for cid, record in citizens.items():
            nid = self._id_to_node[cid]
            risk = civic_to_risk(record["civic_score"])
            G.add_node(nid, risk_score=risk, citizen_id=cid,
                       civic_score=record["civic_score"],
                       risk_tier=record["risk_tier"])

        # Add edges from relationships table
        for rel in relationships:
            a_id = rel["citizen_a"]
            b_id = rel["citizen_b"]
            if a_id not in self._id_to_node or b_id not in self._id_to_node:
                continue
            a_node = self._id_to_node[a_id]
            b_node = self._id_to_node[b_id]
            edge_type = rel["edge_type"] if rel["edge_type"] in VALID_EDGE_TYPES else "weak_signal"
            G.add_edge(a_node, b_node,
                       edge_type=EdgeType(edge_type),
                       weight=rel["weight"])

        self._graph = G
        self._stale = False
        logger.info("Graph rebuilt: %d nodes, %d edges",
                    G.number_of_nodes(), G.number_of_edges())

    @property
    def graph(self) -> nx.Graph:
        if self._stale or self._graph is None:
            self._build_graph()
        return self._graph

    def get_graph_stats(self) -> dict:
        """Return summary statistics about the current network."""
        G = self.graph
        if G.number_of_nodes() == 0:
            return {"nodes": 0, "edges": 0, "message": "No citizens or relationships"}

        # Edge type distribution
        edge_types = {}
        for _, _, data in G.edges(data=True):
            et = data.get("edge_type", "unknown")
            key = et.value if hasattr(et, "value") else str(et)
            edge_types[key] = edge_types.get(key, 0) + 1

        risk_scores = [G.nodes[n].get("risk_score", 0) for n in G.nodes()]
        avg_risk = sum(risk_scores) / len(risk_scores) if risk_scores else 0

        degrees = [G.degree(n) for n in G.nodes()]
        connected = sum(1 for d in degrees if d > 0)

        return {
            "nodes": G.number_of_nodes(),
            "edges": G.number_of_edges(),
            "connected_citizens": connected,
            "isolated_citizens": G.number_of_nodes() - connected,
            "avg_risk_score": round(avg_risk, 4),
            "avg_degree": round(2 * G.number_of_edges() / max(G.number_of_nodes(), 1), 2),
            "edge_type_distribution": edge_types,
            "clustering_coefficient": round(nx.average_clustering(G), 4) if G.number_of_edges() > 0 else 0,
        }

    # ── Influence Analysis ──────────────────────────────────────────────

    def analyze_citizen_network(self, citizen_id: str, max_hops: int = 3) -> NetworkAnalysis:
        """Full network analysis for a single citizen."""
        G = self.graph
        if citizen_id not in self._id_to_node:
            self.db.get_citizen(citizen_id)
            self.invalidate()
            G = self.graph
            if citizen_id not in self._id_to_node:
                raise ValueError(f"Citizen {citizen_id} not in graph")

        nid = self._id_to_node[citizen_id]
        config = InfluenceConfig(decay_type=DecayType.EXPONENTIAL)
        engine = InfluenceEngine(G, config)

        # Influence radius: who does this citizen affect?
        radius = engine.influence_radius(nid, max_hops=max_hops)
        top_influenced = []
        for target_nid, score in list(radius.items())[:10]:
            target_cid = self._node_to_id[target_nid]
            edge_data = G.edges[nid, target_nid] if G.has_edge(nid, target_nid) else {}
            et = edge_data.get("edge_type", "indirect")
            top_influenced.append({
                "citizen_id": target_cid,
                "influence": round(score, 6),
                "edge_type": et.value if hasattr(et, "value") else str(et),
            })

        # Top influencers: who influences this citizen most?
        influencers = engine.top_influencers(nid, max_hops=max_hops, top_k=10)
        top_influencer_list = []
        for source_nid, score in influencers:
            source_cid = self._node_to_id[source_nid]
            edge_data = G.edges[source_nid, nid] if G.has_edge(source_nid, nid) else {}
            et = edge_data.get("edge_type", "indirect")
            top_influencer_list.append({
                "citizen_id": source_cid,
                "influence": round(score, 6),
                "edge_type": et.value if hasattr(et, "value") else str(et),
            })

        # Neighbor avg risk
        neighbors = list(G.neighbors(nid))
        avg_neighbor_risk = 0.0
        if neighbors:
            avg_neighbor_risk = sum(
                G.nodes[n].get("risk_score", 0) for n in neighbors
            ) / len(neighbors)

        # Diffusion score for this citizen
        diff_config = DiffusionConfig(
            alpha=self.config.get("diffusion_alpha", 0.85),
            max_iterations=50,
            convergence_threshold=1e-5,
        )
        diff_engine = DiffusionEngine(G, diff_config)
        scores = diff_engine.propagate()
        network_risk = scores.get(nid, 0.0)

        return NetworkAnalysis(
            citizen_id=citizen_id,
            node_id=nid,
            network_risk_score=round(network_risk, 6),
            influence_reach=len(radius),
            top_influencers=top_influencer_list,
            top_influenced=top_influenced,
            connection_count=len(neighbors),
            avg_neighbor_risk=round(avg_neighbor_risk, 6),
        )

    # ── Contagion Simulation ────────────────────────────────────────────

    def simulate_contagion(self, citizen_id: str,
                           risk_threshold: float = 0.1,
                           propagation_factor: float = 0.15) -> ContagionAnalysis:
        """Simulate what happens when a citizen is flagged (risk=1.0 injected).

        Returns affected citizens and recommended score adjustments.
        propagation_factor controls how much network contagion affects civic scores.
        """
        G = self.graph
        if citizen_id not in self._id_to_node:
            raise ValueError(f"Citizen {citizen_id} not in graph")
        if G.number_of_edges() == 0:
            return ContagionAnalysis(
                seed_citizen_id=citizen_id,
                affected_citizens=[], affected_count=0,
                affected_fraction=0.0, influence_radius=0,
                iterations=0, score_adjustments={}, metrics={},
            )

        seed_nid = self._id_to_node[citizen_id]

        contagion_cfg = ContagionConfig(
            seed_nodes=[seed_nid],
            seed_risk=1.0,
            risk_threshold=risk_threshold,
            max_hops_analysis=10,
            diffusion=DiffusionConfig(
                alpha=self.config.get("diffusion_alpha", 0.85),
                max_iterations=100,
                convergence_threshold=1e-6,
            ),
        )

        sim = ContagionSimulator(G, contagion_cfg)
        result = sim.run()

        # Compute systemic metrics
        metrics = compute_metrics(G, result, risk_threshold)

        # Build affected citizen list and score adjustments
        affected_citizens = []
        score_adjustments = {}

        for nid, delta in result.score_deltas.items():
            if abs(delta) < 1e-8:
                continue
            cid = self._node_to_id.get(nid)
            if not cid or cid == citizen_id:
                continue

            # Convert risk delta to civic score adjustment
            # Positive risk delta → negative civic score adjustment
            civic_delta = -int(round(delta * 1000 * propagation_factor))
            if civic_delta == 0:
                continue

            affected_citizens.append({
                "citizen_id": cid,
                "risk_delta": round(delta, 6),
                "new_risk": round(result.triggered_scores[nid], 6),
            })
            score_adjustments[cid] = civic_delta

        # Sort by impact magnitude
        affected_citizens.sort(key=lambda x: abs(x["risk_delta"]), reverse=True)

        metrics_summary = {
            "new_crossings": metrics.new_crossings,
            "crossing_fraction": metrics.crossing_fraction,
            "mean_shift": metrics.mean_shift,
            "max_delta": metrics.max_delta,
            "clustering_shift": metrics.clustering_shift,
            "degree_vulnerability": metrics.degree_vulnerability,
            "time_to_90pct_spread": metrics.time_to_90pct_spread,
        }

        # Log the event
        self.db.log_contagion_event(
            seed_citizen_id=citizen_id,
            affected_count=result.affected_count,
            affected_fraction=result.affected_fraction,
            influence_radius=result.influence_radius,
            result_data=metrics_summary,
        )

        return ContagionAnalysis(
            seed_citizen_id=citizen_id,
            affected_citizens=affected_citizens,
            affected_count=result.affected_count,
            affected_fraction=result.affected_fraction,
            influence_radius=result.influence_radius,
            iterations=result.iterations_to_converge,
            score_adjustments=score_adjustments,
            metrics=metrics_summary,
        )

    def apply_contagion(self, contagion: ContagionAnalysis):
        """Apply the contagion score adjustments to the database.

        This actually modifies connected citizens' scores based on network propagation.
        """
        applied = []
        for cid, delta in contagion.score_adjustments.items():
            record = self.db.get_citizen(cid)
            new_score = max(0, min(1000, record["civic_score"] + delta))
            self.db.update_citizen_score(cid, new_score)
            self.db.log_activity(cid, "network_contagion", {
                "source": contagion.seed_citizen_id,
                "score_delta": delta,
                "new_score": new_score,
                "reason": "network_risk_propagation",
            })
            applied.append({"citizen_id": cid, "delta": delta, "new_score": new_score})
            logger.info("Contagion: %s score %+d → %d (from %s)",
                        cid, delta, new_score, contagion.seed_citizen_id)

        self.invalidate()
        return applied

    # ── Dashboard Data ──────────────────────────────────────────────────

    def get_network_dashboard_data(self) -> dict:
        """Return data for the network section of the dashboard."""
        stats = self.get_graph_stats()
        events = self.db.get_contagion_events(limit=10)

        # Find most connected citizens
        G = self.graph
        if G.number_of_nodes() == 0:
            return {"stats": stats, "hubs": [], "recent_contagions": events}

        degree_list = [(self._node_to_id[n], G.degree(n)) for n in G.nodes() if G.degree(n) > 0]
        degree_list.sort(key=lambda x: x[1], reverse=True)
        hubs = [{"citizen_id": cid, "connections": deg} for cid, deg in degree_list[:10]]

        # High-risk connected citizens (high risk + high degree)
        risk_hubs = []
        for n in G.nodes():
            risk = G.nodes[n].get("risk_score", 0)
            deg = G.degree(n)
            if deg > 0 and risk > 0.5:
                cid = self._node_to_id[n]
                risk_hubs.append({
                    "citizen_id": cid,
                    "risk_score": round(risk, 4),
                    "connections": deg,
                    "threat_index": round(risk * deg, 2),
                })
        risk_hubs.sort(key=lambda x: x["threat_index"], reverse=True)

        return {
            "stats": stats,
            "hubs": hubs,
            "high_risk_hubs": risk_hubs[:10],
            "recent_contagions": events,
        }

    def render_contagion_dashboard(self, citizen_id: str, save_path: str) -> str:
        """Render a matplotlib contagion dashboard for a citizen. Returns the file path."""
        from graph import render_dashboard as _render

        G = self.graph
        if citizen_id not in self._id_to_node:
            raise ValueError(f"Citizen {citizen_id} not in graph")
        if G.number_of_edges() == 0:
            raise ValueError("No relationships in graph — cannot render dashboard")

        seed_nid = self._id_to_node[citizen_id]

        contagion_cfg = ContagionConfig(
            seed_nodes=[seed_nid],
            seed_risk=1.0,
            risk_threshold=0.1,
            diffusion=DiffusionConfig(alpha=0.85, max_iterations=100),
        )
        sim = ContagionSimulator(G, contagion_cfg)
        result = sim.run()
        metrics = compute_metrics(G, result, risk_threshold=0.1)

        _render(G, result, metrics, save_path=save_path)
        return save_path
