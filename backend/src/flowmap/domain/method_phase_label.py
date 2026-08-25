"""Build stable method-phase labelling questions from completed analysis.

This module deliberately stops at the LLM boundary. It does not label phases,
mutate ``Analysis``, or depend on flattened clone IDs. The existing flattened
labelling flow can therefore remain in place while the method-level contract
is developed and evaluated independently.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable, Literal, TypedDict

from domain.phase_segmentation import Analysis, callee_count, phase_count
from model import Node, NodeSemanticFeatures, Phase


class MethodEvidence(TypedDict):
    entryId: str
    fullName: str


class OperationEvidence(TypedDict):
    callNodeId: str
    callee: str | None
    code: str | None
    receiver: str | None
    arguments: list[str]
    inputs: list[str]
    fieldsRead: list[str]
    fieldsWritten: list[str]
    domainTypes: list[str]
    methodTerms: list[str]


class PhaseEvidence(TypedDict):
    phaseId: str
    method: MethodEvidence
    phaseIndex: int
    localPhaseCount: int
    operations: list[OperationEvidence]


class LabelSubject(TypedDict):
    id: str
    phaseIds: list[str]
    phaseEvidence: list[PhaseEvidence]


class MethodPhaseLabelRequest(TypedDict):
    schemaVersion: Literal["method-phase-label-v1"]
    subjects: list[LabelSubject]


MethodPhaseBatchLabeler = Callable[[MethodPhaseLabelRequest], dict[str, str]]


def _phase_id(entry_id: str, phase: Phase, index: int) -> str:
    return phase.id or f"{entry_id}:phase:{index + 1}"


def _operation_evidence(
    node: Node,
    features: NodeSemanticFeatures | None,
) -> OperationEvidence:
    return {
        "callNodeId": node.id,
        "callee": node.calleeFullName,
        "code": node.code,
        "receiver": features.receiver if features else None,
        "arguments": list(features.arguments) if features else [],
        "inputs": list(features.inputIdentifiers) if features else [],
        "fieldsRead": list(features.fieldsRead) if features else [],
        "fieldsWritten": list(features.fieldsWritten) if features else [],
        "domainTypes": list(features.domainTypes) if features else [],
        "methodTerms": list(features.methodTerms) if features else [],
    }


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


def build_label_subjects(analysis: Analysis) -> MethodPhaseLabelRequest:
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

    for entry_id, method in sorted(analysis.methods.items()):
        for index, phase in enumerate(method.phases):
            phase_id = _phase_id(entry_id, phase, index)
            if phase_id in phase_records:
                raise ValueError(f"duplicate method phase id {phase_id!r}")
            phase_records[phase_id] = (entry_id, index, phase)
            phase_id_by_entry_and_index[(entry_id, index)] = phase_id

    groups = _DisjointPhases(set(phase_records))

    for caller_phase_id, (entry_id, _, phase) in phase_records.items():
        method = analysis.methods[entry_id]
        if len(phase.nodes) != 1:
            continue
        call_id = phase.nodes[0]
        if call_id in method.retainedCallIds or callee_count(analysis, call_id) != 1:
            continue
        for target_entry_id in analysis.calleeEntries.get(call_id, ()):
            target = analysis.methods.get(target_entry_id)
            if target is None or phase_count(analysis, target_entry_id) != 1:
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
    for phase_ids in sorted(
        (sorted(ids) for ids in phase_ids_by_root.values()),
        key=lambda ids: ids[0],
    ):
        evidence: list[PhaseEvidence] = []
        for phase_id in phase_ids:
            entry_id, index, phase = phase_records[phase_id]
            method = analysis.methods[entry_id]
            entry = entry_nodes.get(entry_id)
            evidence.append({
                "phaseId": phase_id,
                "method": {
                    "entryId": entry_id,
                    "fullName": entry.calleeFullName if entry and entry.calleeFullName else entry_id,
                },
                "phaseIndex": index + 1,
                "localPhaseCount": len(method.phases),
                "operations": [
                    _operation_evidence(node, analysis.graph.semanticFeatures.get(node_id))
                    for node_id in phase.nodes
                    if (node := nodes_by_id.get(node_id)) is not None
                ],
            })
        subject_prefix = "label-group" if len(phase_ids) > 1 else "label-subject"
        subjects.append({
            "id": f"{subject_prefix}:{phase_ids[0]}",
            "phaseIds": phase_ids,
            "phaseEvidence": evidence,
        })

    return {
        "schemaVersion": "method-phase-label-v1",
        "subjects": subjects,
    }


def label_method_analysis(
    analysis: Analysis,
    labeler: MethodPhaseBatchLabeler,
) -> int:
    """Run one method-level batch and attach returned labels by phase ID.

    Subject IDs are the LLM response correlation keys. ``phaseIds`` remains
    the authoritative assignment list, allowing one transparent-delegation
    label to update every equivalent method phase.
    """
    request = build_label_subjects(analysis)
    if not request["subjects"]:
        return 0
    labels_by_subject_id = labeler(request)
    phases_by_id: dict[str, Phase] = {}
    for entry_id, method in analysis.methods.items():
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
        for phase_id in subject["phaseIds"]:
            phase = phases_by_id.get(phase_id)
            if phase is None:
                raise ValueError(
                    f"label subject {subject['id']!r} references missing phase "
                    f"{phase_id!r}"
                )
            phase.label = label
            labelled += 1
    return labelled
