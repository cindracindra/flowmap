from __future__ import annotations

from dataclasses import dataclass

from domain.method_branch_routing import prepare_all_method_branch_routes
from domain.method_scoping import build_method_definitions
from domain.phase_segmentation import Analysis
from model import Graph, MethodDefinition

from .operation_definition import OperationDefinition


@dataclass(slots=True)
class GraphBundle:
    methodsByEntryId: dict[str, MethodDefinition]
    operationsById: dict[str, OperationDefinition]
    callersByEntryId: dict[str, list[str]]
    operationIdsByMethodEntryId: dict[str, list[str]]
    phaseAnalysis: Analysis


def _operation_root_ids(graph: Graph) -> list[str]:
    if graph.roots:
        return list(graph.roots)
    if graph.entryPoint is None:
        return []
    return [
        node.id
        for node in graph.nodes
        if node.type == "entry" and node.calleeFullName == graph.entryPoint
    ]


def _reachable_method_ids(
    root_id: str,
    methods: dict[str, MethodDefinition],
) -> list[str]:
    entry_ids = set(methods)
    reached: set[str] = set()
    stack = [root_id]
    while stack:
        entry_id = stack.pop()
        if entry_id in reached or entry_id not in methods:
            continue
        reached.add(entry_id)
        stack.extend(
            edge.target
            for edge in methods[entry_id].invokeEdges
            if edge.target in entry_ids
        )
    return sorted(reached)


def build_graph_bundle(
    filtered_graph: Graph,
    phase_analysis: Analysis,
    methods: dict[str, MethodDefinition] | None = None,
) -> GraphBundle:
    """Combine canonical method topology with completed method analysis."""

    if {node.id for node in filtered_graph.nodes} != {
        node.id for node in phase_analysis.graph.nodes
    }:
        raise ValueError("phase_analysis was not computed for the supplied filtered graph")

    if methods is None:
        methods = prepare_all_method_branch_routes(
            build_method_definitions(filtered_graph)
        )
    callers_by_entry: dict[str, set[str]] = {
        entry_id: set() for entry_id in methods
    }
    for caller_entry_id, method in methods.items():
        for edge in method.invokeEdges:
            if edge.target in callers_by_entry:
                callers_by_entry[edge.target].add(caller_entry_id)

    operations: dict[str, OperationDefinition] = {}
    for root_id in _operation_root_ids(filtered_graph):
        method = methods.get(root_id)
        if method is None:
            raise ValueError(f"Operation root {root_id!r} has no method definition")
        operations[root_id] = OperationDefinition(
            id=root_id,
            rootEntryId=root_id,
            label=method.methodFullName,
            reachableMethodEntryIds=_reachable_method_ids(root_id, methods),
        )

    operation_ids_by_method: dict[str, list[str]] = {
        entry_id: [] for entry_id in methods
    }
    for operation in operations.values():
        for entry_id in operation.reachableMethodEntryIds:
            operation_ids_by_method[entry_id].append(operation.id)

    return GraphBundle(
        methodsByEntryId=methods,
        operationsById=operations,
        callersByEntryId={
            entry_id: sorted(callers)
            for entry_id, callers in callers_by_entry.items()
        },
        operationIdsByMethodEntryId={
            entry_id: sorted(operation_ids)
            for entry_id, operation_ids in operation_ids_by_method.items()
        },
        phaseAnalysis=phase_analysis,
    )
