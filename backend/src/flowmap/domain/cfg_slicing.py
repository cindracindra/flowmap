import dataclasses

from domain.cfg_semantics import scoped_semantic_features
from model import Graph


def classify_roots_and_orphans(graph: Graph) -> Graph:
    """Classify roots and orphans by their surviving executable flow."""

    nodes_by_id = {n.id: n for n in graph.nodes}
    entry_id_by_fullname = {
        n.calleeFullName: n.id for n in graph.nodes if n.type == "entry"
    }

    invoke_in: dict[str, int] = {}
    entries_with_executable_flow: set[str] = set()
    for e in graph.edges:
        if e.type not in ("sequence", "invoke"):
            continue
        source_node = nodes_by_id.get(e.source)
        if source_node is not None:
            if source_node.type == "entry":
                target_node = nodes_by_id.get(e.target)
                # Exit markers describe how a method ends; they are not an
                # executable operation by themselves. In particular, an
                # entry -> fallthrough edge must not turn an otherwise empty,
                # uncalled method into a root instead of an orphan.
                if target_node is None or target_node.type != "exit":
                    entries_with_executable_flow.add(source_node.id)
            elif source_node.callerMethod is not None:
                owner_id = entry_id_by_fullname.get(source_node.callerMethod)
                if owner_id is not None:
                    entries_with_executable_flow.add(owner_id)

        if e.type == "invoke" and nodes_by_id.get(e.target, None) is not None:
            if nodes_by_id[e.target].type == "entry":
                invoke_in[e.target] = invoke_in.get(e.target, 0) + 1

    entry_ids = [n.id for n in graph.nodes if n.type == "entry"]
    roots = sorted(
        id_
        for id_ in entry_ids
        if invoke_in.get(id_, 0) == 0 and id_ in entries_with_executable_flow
    )
    orphans = sorted(
        id_
        for id_ in entry_ids
        if invoke_in.get(id_, 0) == 0 and id_ not in entries_with_executable_flow
    )

    return dataclasses.replace(graph, roots=roots, orphans=orphans)


def slice_from_root(graph: Graph, root_id: str) -> Graph:
    """Extracts the forward-reachable subgraph from root_id (an
    "entry" node id) out of the full-codebase graph."""

    nodes_by_id = {n.id: n for n in graph.nodes}
    root = nodes_by_id[root_id]
    if root.type != "entry":
        raise ValueError(
            f"slice_from_root's root must be an 'entry' node, got {root.type!r}"
        )

    forward: dict[str, list[str]] = {}
    for e in graph.edges:
        if e.type in ("sequence", "invoke"):
            forward.setdefault(e.source, []).append(e.target)

    reached: set[str] = set()
    stack = [root_id]
    while stack:
        node_id = stack.pop()
        if node_id in reached:
            continue
        reached.add(node_id)
        stack.extend(forward.get(node_id, []))

    sliced_nodes = [nodes_by_id[i] for i in reached]
    sliced_edges = [
        e for e in graph.edges if e.source in reached and e.target in reached
    ]

    sliced_method_names = {n.calleeFullName for n in sliced_nodes if n.type == "entry"}
    tagged_group_ids = {t.groupId for n in sliced_nodes for t in n.branchArms}
    sliced_groups = [
        g
        for g in graph.branchGroups
        if (
            g.method in sliced_method_names
            if g.method is not None
            else g.id in tagged_group_ids
        )
    ]
    tagged_loop_ids = {loop_id for n in sliced_nodes for loop_id in n.loopIds}
    sliced_loops = [
        loop
        for loop in graph.loopGroups
        if (
            loop.method in sliced_method_names
            if loop.method is not None
            else loop.id in tagged_loop_ids
        )
    ]

    return Graph(
        entryPoint=root.calleeFullName,
        nodes=sliced_nodes,
        edges=sliced_edges,
        branchGroups=sliced_groups,
        loopGroups=sliced_loops,
        semanticFeatures=scoped_semantic_features(
            graph.semanticFeatures, reached
        ),
    )


def filter_and_classify_roots_and_orphans(graph: Graph) -> Graph:
    """Two-pass whole-codebase operation analysis.

    The first pass records the raw structure. Filtering then keeps every
    method entry temporarily so entries exposed or emptied by filtering are
    available to the final classification. The returned graph is filtered
    and its roots/orphans describe that filtered graph.
    """
    # Local import keeps the stage dependency one-way at module load time:
    # filtering uses the semantic scoping helper defined above.
    from domain.cfg_filtering import filter_noise_cfg

    initially_classified = classify_roots_and_orphans(graph)
    filtered = filter_noise_cfg(initially_classified, preserve_all_entries=True)
    return classify_roots_and_orphans(filtered)
