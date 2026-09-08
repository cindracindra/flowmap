"""Building blocks for the execution-phase abstraction."""

from .cohesion import (
    CohesionDecision,
    SimilarityScore,
    evaluate_op_to_op,
    evaluate_region_to_op,
    evaluate_region_to_region,
    score_semantic_similarity,
)
from .orchestration import (
    ExecutionPhaseAnalysis,
    build_callee_index,
    build_direct_flow_index,
    execution_phase_analysis,
)
from .method_analysis import (
    MethodAnalysis,
    analyse_method,
    call_effective_phase_count,
    effective_phase_count,
    resolve,
    structure_call_ids,
)
from .retained_call_analysis import (
    phases_for_frontiers,
    recheck_retained_calls,
    retained_call_frontiers,
)
from .resolution import (
    BatchGateResolver,
    GateAnswer,
    ResolvedGateAction,
    ResolvedGateDecision,
    UnresolvedGateQuestion,
    UnresolvedReason,
    build_gate_question,
    build_gate_questions,
    resolve_uncertain_gates,
)
from .semantic import SemanticSignature, build_core_signature
from .structure import (
    BranchStructure,
    LinearStructure,
    MethodStructure,
    build_method_structure,
    build_method_structures,
)
from .structure_analysis import (
    BranchStructureAnalysis,
    StraightStructureAnalysis,
    StructureBoundaryContext,
    StructureAnalysis,
    analyse_branch_structure,
    analyse_sibling_structures,
    analyse_straight_structure,
    analyse_structure_boundary,
)

__all__ = [
    "CohesionDecision",
    "BranchStructure",
    "BranchStructureAnalysis",
    "BatchGateResolver",
    "ExecutionPhaseAnalysis",
    "GateAnswer",
    "LinearStructure",
    "MethodStructure",
    "MethodAnalysis",
    "analyse_method",
    "call_effective_phase_count",
    "StraightStructureAnalysis",
    "StructureBoundaryContext",
    "StructureAnalysis",
    "ResolvedGateAction",
    "ResolvedGateDecision",
    "UnresolvedGateQuestion",
    "UnresolvedReason",
    "analyse_branch_structure",
    "analyse_sibling_structures",
    "analyse_straight_structure",
    "analyse_structure_boundary",
    "SemanticSignature",
    "SimilarityScore",
    "build_core_signature",
    "build_callee_index",
    "build_direct_flow_index",
    "build_gate_question",
    "build_gate_questions",
    "build_method_structure",
    "build_method_structures",
    "evaluate_op_to_op",
    "evaluate_region_to_op",
    "evaluate_region_to_region",
    "effective_phase_count",
    "resolve",
    "recheck_retained_calls",
    "resolve_uncertain_gates",
    "phases_for_frontiers",
    "retained_call_frontiers",
    "score_semantic_similarity",
    "structure_call_ids",
    "execution_phase_analysis",
]
