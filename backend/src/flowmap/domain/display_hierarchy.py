"""Derive the ID-only display hierarchy carried beside a flattened CFG.

The CFG stays flat and remains authoritative for execution.  This sidecar
restores method-instance and branch-arm containment for presentation clients
without duplicating node or branch payloads.
"""

from __future__ import annotations

from typing import Any

from model import BranchGroup, Graph, Node


def _walk_order(graph: Graph, root_id: str) -> dict[str, int]:
    adjacency: dict[str, list[str]] = {}
    for edge in graph.edges:
        if edge.type not in {"sequence", "invoke"}:
            continue
        adjacency.setdefault(edge.source, []).append(edge.target)

    order: dict[str, int] = {}
    stack = [root_id]
    while stack:
        node_id = stack.pop()
        if node_id in order:
            continue
        order[node_id] = len(order)
        stack.extend(reversed(adjacency.get(node_id, ())))
    for node in graph.nodes:
        if node.id not in order:
            order[node.id] = len(order)
    return order


def _displayable_groups(graph: Graph) -> list[BranchGroup]:
    groups: list[BranchGroup] = []
    for group in graph.branchGroups:
        if not group.branchPointIds or all(arm.empty for arm in group.arms):
            continue
        if group.kind == "TRY" and not any(
            arm.label != "noCatch" and not arm.empty for arm in group.arms
        ):
            continue
        groups.append(group)
    return groups


def build_display_hierarchy(graph: Graph) -> dict[str, Any]:
    """Build a serializable method/branch/arm/operation containment tree."""
    if graph.rootId is None:
        return {"roots": []}

    order = _walk_order(graph, graph.rootId)
    ordered_nodes = sorted(graph.nodes, key=lambda node: order[node.id])
    nodes_by_id = {node.id: node for node in graph.nodes}

    # Server-stamped depth identifies the active cloned method instance.
    active_at_depth: list[dict[str, Any] | None] = []
    method_for_node: dict[str, dict[str, Any]] = {}
    method_records: list[dict[str, Any]] = []
    for node in ordered_nodes:
        depth = node.depth or 0
        if node.type == "entry":
            parent = active_at_depth[depth - 1] if depth > 0 and len(active_at_depth) >= depth else None
            item: dict[str, Any] = {
                "kind": "method",
                "entryId": node.id,
                "items": [],
            }
            record = {"item": item, "parent": parent, "order": order[node.id]}
            del active_at_depth[depth:]
            active_at_depth.append(record)
            method_records.append(record)
            method_for_node[node.id] = record
            continue
        if depth < len(active_at_depth) and active_at_depth[depth] is not None:
            method_for_node[node.id] = active_at_depth[depth]  # type: ignore[assignment]

    groups = _displayable_groups(graph)
    groups_by_id = {group.id: group for group in groups}
    group_method = {
        group.id: method_for_node.get(group.branchPointIds[0])
        for group in groups
        if group.branchPointIds
    }
    member_counts: dict[tuple[str, str], int] = {}
    for node in graph.nodes:
        for ref in node.branchArms:
            key = (ref.groupId, ref.armLabel)
            member_counts[key] = member_counts.get(key, 0) + 1

    arms_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    branches_by_id: dict[str, dict[str, Any]] = {}
    for group in groups:
        arms = []
        for arm in group.arms:
            arm_item = {
                "kind": "arm",
                "panelId": group.id,
                "armId": arm.label,
                "items": [],
            }
            arms.append(arm_item)
            arms_by_key[(group.id, arm.label)] = arm_item
        branches_by_id[group.id] = {
            "kind": "branch",
            "panelId": group.id,
            "arms": arms,
        }

    def innermost_arm(
        node: Node, method_record: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        candidates = [
            (member_counts.get((ref.groupId, ref.armLabel), 0), arms_by_key[(ref.groupId, ref.armLabel)])
            for ref in node.branchArms
            if (ref.groupId, ref.armLabel) in arms_by_key
            and group_method.get(ref.groupId) is method_record
        ]
        return min(candidates, key=lambda candidate: candidate[0])[1] if candidates else None

    # Branch blocks are owned by the arm containing their attachment point,
    # or by that point's method when they are top-level in the method.
    for group in groups:
        anchor = next(
            (nodes_by_id[node_id] for node_id in group.branchPointIds if node_id in nodes_by_id),
            None,
        )
        if anchor is None:
            continue
        anchor_method = method_for_node.get(anchor.id)
        owner = innermost_arm(anchor, anchor_method)
        if owner is None:
            owner = anchor_method["item"] if anchor_method is not None else None
        if owner is not None:
            owner["items"].append(branches_by_id[group.id])

    for node in ordered_nodes:
        if node.type == "entry":
            continue
        operation = {"kind": "operation", "nodeId": node.id}
        record = method_for_node.get(node.id)
        owner = innermost_arm(node, record)
        if owner is None:
            owner = record["item"] if record is not None else None
        if owner is not None:
            owner["items"].append(operation)

    # Entry nodes are method headers. Propagated branch membership places a
    # complete invoked method inside the arm that caused it to execute.
    for record in method_records:
        item = record["item"]
        entry = nodes_by_id[item["entryId"]]
        # Only a branch in the caller can own the invoked method as a whole.
        owner = innermost_arm(entry, record["parent"])
        if owner is None and record["parent"] is not None:
            owner = record["parent"]["item"]
        if owner is not None:
            owner["items"].append(item)

    def item_order(item: dict[str, Any]) -> float:
        if item["kind"] == "operation":
            return float(order[item["nodeId"]])
        if item["kind"] == "method":
            return float(order[item["entryId"]])
        group = groups_by_id[item["panelId"]]
        # The attachment operation precedes the block it opens.
        return min(order[node_id] for node_id in group.branchPointIds) + 0.25

    def sort_owner(owner: dict[str, Any]) -> None:
        owner["items"].sort(key=item_order)
        for item in owner["items"]:
            if item["kind"] == "method":
                sort_owner(item)
            elif item["kind"] == "branch":
                for arm in item["arms"]:
                    sort_owner(arm)

    roots = [record for record in method_records if record["parent"] is None]
    roots.sort(key=lambda record: record["order"])
    root_items = [record["item"] for record in roots]
    for root in root_items:
        sort_owner(root)
    return {"roots": root_items}
