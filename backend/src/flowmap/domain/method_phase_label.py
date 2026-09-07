"""Build stable execution-phase labelling questions from completed analysis.

This module deliberately stops at the LLM boundary. It does not label phases,
mutate ``Analysis``, or depend on flattened clone IDs. The existing flattened
labelling flow can therefore remain in place while the method-level contract
is developed and evaluated independently.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable, Literal, TypedDict

from domain.execution_phase.method_analysis import effective_phase_count
from domain.execution_phase.orchestration import ExecutionPhaseAnalysis
from domain.execution_phase.resolution import operation_context, signature_payload
from domain.execution_phase.semantic import SemanticSignature, build_core_signature
from model import NodeSemanticFeatures, Phase


class OperationEvidence(TypedDict, total=False):
    callee: str | None
    code: str | None


class PhaseEvidence(TypedDict):
    phaseId: str
    method: str
    phaseIndex: int
    localPhaseCount: int
    coreSignature: dict[str, list[str]]
    operations: list[OperationEvidence]


class LabelSubject(TypedDict):
    id: str
    phaseEvidence: list[PhaseEvidence]


class MethodPhaseLabelRequest(TypedDict):
    schemaVersion: Literal["execution-phase-label-v2"]
    subjects: list[LabelSubject]


MethodPhaseBatchLabeler = Callable[[MethodPhaseLabelRequest], dict[str, str]]


def _phase_id(entry_id: str, phase: Phase, index: int) -> str:
    return phase.id or f"{entry_id}:phase:{index + 1}"


def _phase_signature(
    analysis: ExecutionPhaseAnalysis,
    phase: Phase,
) -> SemanticSignature:
    return build_core_signature(
        SemanticSignature.from_operation(
            analysis.graph.semanticFeatures.get(node_id, NodeSemanticFeatures())
        )
        for node_id in phase.nodes
    )


class _DisjointPhases:
    """Small transient union-find; not a new phase-analysis model."""

    def __init__(self, phase_ids: set[str]) -> None:
        self.parent = {phase_id: phase_id for phase_id in phase_ids}

    def find(self, phase_id: str) -> str:
        parent = self.parent[phase_id]
        if parent != phase_id:
            self.parent[phase_id] = self.find(parent)
        return self.parent[phase_id]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        # Stable roots make subject IDs deterministic across graph iteration.
        first, second = sorted((left_root, right_root))
        self.parent[second] = first


def build_label_subjects(analysis: ExecutionPhaseAnalysis) -> MethodPhaseLabelRequest:
    """Return one self-contained label question per phase/equivalence group.

    A transparent equivalence is derived only from already-resolved structure:
    the caller phase contains one call, that call is non-retained, and its
    call-site-wide maximum effective callee count is one. Every target with
    one effective phase joins the same transitive label group.
    """

    nodes_by_id = {node.id: node for node in analysis.graph.nodes}
    entry_nodes = {
        node.id: node
        for node in analysis.graph.nodes
        if node.type == "entry"
    }
    phase_records: dict[str, tuple[str, int, Phase]] = {}
    phase_id_by_entry_and_index: dict[tuple[str, int], str] = {}

    for entry_id, method in sorted(analysis.analyses_by_entry_id.items()):
        for index, phase in enumerate(method.phases):
            phase_id = _phase_id(entry_id, phase, index)
            if phase_id in phase_records:
                raise ValueError(f"duplicate method phase id {phase_id!r}")
            phase_records[phase_id] = (entry_id, index, phase)
            phase_id_by_entry_and_index[(entry_id, index)] = phase_id

    groups = _DisjointPhases(set(phase_records))

    for caller_phase_id, (entry_id, _, phase) in phase_records.items():
        method = analysis.analyses_by_entry_id[entry_id]
        if len(phase.nodes) != 1:
            continue
        call_id = phase.nodes[0]
        if (
            call_id in method.retained_call_ids
            or len(analysis.callee_entries_by_call_id.get(call_id, ())) != 1
        ):
            continue
        for target_entry_id in analysis.callee_entries_by_call_id.get(call_id, ()):
            target = analysis.analyses_by_entry_id.get(target_entry_id)
            if (
                target is None
                or effective_phase_count(
                    target_entry_id,
                    analysis.analyses_by_entry_id,
                    analysis.callee_entries_by_call_id,
                ) != 1
            ):
                continue
            # An effective count of one implies exactly one local phase after
            # retention rechecking. Keep the guard explicit for malformed data.
            if len(target.phases) != 1:
                continue
            target_phase_id = phase_id_by_entry_and_index.get((target_entry_id, 0))
            if target_phase_id is not None:
                groups.union(caller_phase_id, target_phase_id)

    phase_ids_by_root: dict[str, list[str]] = defaultdict(list)
    for phase_id in phase_records:
        phase_ids_by_root[groups.find(phase_id)].append(phase_id)

    subjects: list[LabelSubject] = []
    sorted_phase_groups = sorted(
        (sorted(ids) for ids in phase_ids_by_root.values()),
        key=lambda ids: ids[0],
    )
    for subject_number, phase_ids in enumerate(sorted_phase_groups, start=1):
        evidence: list[PhaseEvidence] = []
        for phase_id in phase_ids:
            entry_id, index, phase = phase_records[phase_id]
            method = analysis.analyses_by_entry_id[entry_id]
            entry = entry_nodes.get(entry_id)
            evidence.append({
                "phaseId": phase_id,
                "method": (
                    entry.calleeFullName
                    if entry and entry.calleeFullName
                    else entry_id
                ),
                "phaseIndex": index + 1,
                "localPhaseCount": len(method.phases),
                "coreSignature": signature_payload(
                    _phase_signature(analysis, phase)
                ),
                "operations": [
                    dict(operation_context(node))
                    for node_id in phase.nodes
                    if (node := nodes_by_id.get(node_id)) is not None
                ],
            })
        subjects.append({
            # The LLM only needs a uniform opaque correlation key. Using
            # different prefixes for singleton and grouped phases encouraged
            # models to rewrite ``group-N`` as the generic ``subject-N``.
            "id": f"item-{subject_number}",
            "phaseEvidence": evidence,
        })

    return {
        "schemaVersion": "execution-phase-label-v2",
        "subjects": subjects,
    }


def label_method_analysis(
    analysis: ExecutionPhaseAnalysis,
    labeler: MethodPhaseBatchLabeler,
) -> int:
    """Run one method-level batch and attach returned labels by phase ID.

    Subject IDs are the LLM response correlation keys. Phase IDs are derived
    from ``phaseEvidence``, allowing one transparent-delegation label to update
    every equivalent execution phase.
    """
    request = build_label_subjects(analysis)
    if not request["subjects"]:
        return 0
    labels_by_subject_id = labeler(request)
    phases_by_id: dict[str, Phase] = {}
    for entry_id, method in analysis.analyses_by_entry_id.items():
        for index, phase in enumerate(method.phases):
            phase_id = _phase_id(entry_id, phase, index)
            if phase_id in phases_by_id:
                raise ValueError(f"duplicate method phase id {phase_id!r}")
            phase.id = phase_id
            phases_by_id[phase_id] = phase

    labelled = 0
    for subject in request["subjects"]:
        label = labels_by_subject_id.get(subject["id"])
        if label is None:
            continue
        for phase_evidence in subject["phaseEvidence"]:
            phase_id = phase_evidence["phaseId"]
            phase = phases_by_id.get(phase_id)
            if phase is None:
                raise ValueError(
                    f"label subject {subject['id']!r} references missing phase "
                    f"{phase_id!r}"
                )
            phase.label = label
            labelled += 1
    return labelled
