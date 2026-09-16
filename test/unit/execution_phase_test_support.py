from __future__ import annotations

from typing import Any

from domain.execution_phase.method_analysis import MethodAnalysis
from domain.execution_phase.orchestration import ExecutionPhaseAnalysis
from domain.execution_phase.structure import BranchStructure, LinearStructure, MethodStructure
from domain.execution_phase.structure_analysis import (
    BranchStructureAnalysis,
    StraightStructureAnalysis,
    StructureAnalysis,
)
from model import Phase, UnresolvedGate


def phase_snapshot(phase: Phase) -> dict[str, Any]:
    return {
        "nodes": list(phase.nodes),
        "id": phase.id,
        "label": phase.label,
    }


def gate_snapshot(gate: UnresolvedGate) -> dict[str, Any]:
    return {
        "id": gate.id,
        "left_id": gate.left_id,
        "right_id": gate.right_id,
        "confidence": gate.confidence,
        "evidence": list(gate.evidence),
        "kind": gate.kind,
    }


def structure_snapshot(structure: LinearStructure | BranchStructure) -> dict[str, Any]:
    if isinstance(structure, LinearStructure):
        return {"kind": "linear", "node_ids": list(structure.node_ids)}
    return {
        "kind": "branch",
        "group_id": structure.group_id,
        "arms": [
            [structure_snapshot(child) for child in arm]
            for arm in structure.arms
        ],
    }


def method_structure_snapshot(structure: MethodStructure) -> dict[str, Any]:
    return {
        "method_entry_id": structure.method_entry_id,
        "structures": [structure_snapshot(child) for child in structure.structures],
    }


def structure_analysis_snapshot(result: StructureAnalysis) -> dict[str, Any]:
    common = {
        "structure": structure_snapshot(result.structure),
        "entry_frontier_ids": list(result.entry_frontier_ids),
        "exit_frontier_ids": list(result.exit_frontier_ids),
        "phases": [phase_snapshot(phase) for phase in result.phases],
        "unresolved_gates": [gate_snapshot(gate) for gate in result.unresolved_gates],
        "retained_call_ids": list(result.retained_call_ids),
    }
    if isinstance(result, StraightStructureAnalysis):
        return {"kind": "straight-analysis", **common}
    assert isinstance(result, BranchStructureAnalysis)
    return {
        "kind": "branch-analysis",
        **common,
        "arms": [
            [structure_analysis_snapshot(child) for child in arm]
            for arm in result.arms
        ],
    }


def method_analysis_snapshot(result: MethodAnalysis) -> dict[str, Any]:
    return {
        "method_entry_id": result.method_entry_id,
        "phases": [phase_snapshot(phase) for phase in result.phases],
        "retained_call_ids": sorted(result.retained_call_ids),
        "unresolved_gates": [gate_snapshot(gate) for gate in result.unresolved_gates],
        "structure_analyses": [
            structure_analysis_snapshot(analysis)
            for analysis in result.structure_analyses
        ],
    }


def analysis_snapshot(result: ExecutionPhaseAnalysis) -> dict[str, Any]:
    return {
        "excluded": dict(result.excluded),
        "structures_by_entry_id": {
            entry_id: method_structure_snapshot(structure)
            for entry_id, structure in result.structures_by_entry_id.items()
        },
        "callee_entries_by_call_id": {
            call_id: list(entry_ids)
            for call_id, entry_ids in result.callee_entries_by_call_id.items()
        },
        "direct_flow_pairs": sorted([list(pair) for pair in result.direct_flow_pairs]),
        "analyses_by_entry_id": {
            entry_id: method_analysis_snapshot(analysis)
            for entry_id, analysis in result.analyses_by_entry_id.items()
        },
        "unresolved_gate_questions": [
            question.to_prompt_payload()
            for question in result.unresolved_gate_questions
        ],
    }
