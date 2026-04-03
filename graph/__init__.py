"""Identity Graph — Network analysis modules for citizen relationship mapping."""

from .graph_generator import (
    IdentityGraphGenerator,
    GraphConfig,
    GraphModel,
    EdgeType,
    EDGE_WEIGHT_RANGES,
    EDGE_TYPE_DISTRIBUTION,
    Node,
    Edge,
)
from .influence_engine import (
    InfluenceEngine,
    InfluenceConfig,
    DecayType,
    EDGE_WEIGHTS,
)
from .diffusion_engine import (
    DiffusionEngine,
    DiffusionConfig,
    DiffusionMode,
    DiffusionSnapshot,
)
from .contagion_sim import (
    ContagionSimulator,
    ContagionConfig,
    ContagionResult,
    save_contagion_result,
)
from .metrics_dashboard import (
    compute_metrics,
    render_dashboard,
    SystemicMetrics,
)
