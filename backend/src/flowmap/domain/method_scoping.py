from __future__ import annotations

from model import Graph, MethodDefinition


def build_method_definitions(filtered_graph: Graph) -> dict[str, MethodDefinition]:
    """Scope a filtered CFG using its explicit method ownership metadata.

    Calls and exits are assigned through ``callerMethod``, matching the
    long-standing phase-structure rule. Sequence topology orders those owned
    nodes but does not decide whether they belong to the method, so a valid
    disconnected branch/cycle is not silently lost.
    """

    entries = [node for node in filtered_graph.nodes if node.type == "entry"]
    entry_id_by_name = {
        entry.calleeFullName: entry.id
        for entry in entries
        if entry.calleeFullName is not None
    }
    owner_by_node_id = {entry.id: entry.id for entry in entries}
    for node in filtered_graph.nodes:
        if node.type == "entry" or node.callerMethod is None:
            continue
        owner_id = entry_id_by_name.get(node.callerMethod)
        if owner_id is not None:
            owner_by_node_id[node.id] = owner_id

    definitions: dict[str, MethodDefinition] = {}
    for entry in entries:
        if entry.calleeFullName is None:
            raise ValueError(f"Method entry {entry.id!r} has no full name")
        body_nodes = [
            node
            for node in filtered_graph.nodes
            if node.id != entry.id and owner_by_node_id.get(node.id) == entry.id
        ]
        member_ids = {entry.id, *(node.id for node in body_nodes)}
        definitions[entry.id] = MethodDefinition(
            entryId=entry.id,
            methodFullName=entry.calleeFullName,
            entry=entry,
            nodes=body_nodes,
            sequenceEdges=[
                edge
                for edge in filtered_graph.edges
                if edge.type == "sequence"
                and edge.source in member_ids
                and edge.target in member_ids
            ],
            invokeEdges=[
                edge
                for edge in filtered_graph.edges
                if edge.type == "invoke" and edge.source in member_ids
            ],
            branchGroups=[
                group
                for group in filtered_graph.branchGroups
                if group.method == entry.calleeFullName
            ],
            loopGroups=[
                loop
                for loop in filtered_graph.loopGroups
                if loop.method == entry.calleeFullName
            ],
            semanticFeatures={
                node_id: features
                for node_id, features in filtered_graph.semanticFeatures.items()
                if node_id in member_ids
            },
        )

    return definitions

