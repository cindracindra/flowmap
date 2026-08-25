"""Stage 7: replay method phases onto one flattened CFG trace."""

from __future__ import annotations

from collections import defaultdict, deque

from domain.phase_segmentation import Analysis
from domain.phase_structure import BranchStructure, LinearStructure, Structure
from model import Graph, Node, Phase


def _original_id(node: Node) -> str:
    return node.origId or node.id


def _structure_node_ids(structures: tuple[Structure, ...]):
    for structure in structures:
        if isinstance(structure, LinearStructure):
            yield from structure.nodeIds
        elif isinstance(structure, BranchStructure):
            for arm in structure.arms:
                yield from _structure_node_ids(arm)


def overlay_phases(analysis: Analysis, flattened: Graph) -> list[Phase]:
    """Instantiate method-based phases using the clones in ``flattened``.

    ``Analysis`` remains keyed by filtered-graph ids. Every flattened node is
    translated through ``origId`` and replayed once for its particular method
    instance. Retention and exclusion are read from the completed analysis;
    this function makes no new membership decisions.
    """
    flat_nodes = {node.id: node for node in flattened.nodes}
    original_nodes = {node.id: node for node in analysis.graph.nodes}
    flat_rank = {node.id: index for index, node in enumerate(flattened.nodes)}

    plain_sequence_out: dict[str, list[str]] = defaultdict(list)
    invoke_out: dict[str, list[str]] = defaultdict(list)
    returns_by_call: dict[str, list[str]] = defaultdict(list)
    for edge in flattened.edges:
        if edge.type == "invoke":
            invoke_out[edge.source].append(edge.target)
        elif edge.type == "sequence" and not edge.loopBack:
            if edge.returnFrom is not None:
                returns_by_call[edge.returnFrom].append(edge.target)
            else:
                plain_sequence_out[edge.source].append(edge.target)

    for index in (plain_sequence_out, invoke_out, returns_by_call):
        for targets in index.values():
            targets.sort(key=lambda node_id: flat_rank.get(node_id, 10**9))

    entry_by_name = {
        node.calleeFullName: node.id
        for node in analysis.graph.nodes
        if node.type == "entry" and node.calleeFullName is not None
    }
    calls_by_method: dict[str, set[str]] = defaultdict(set)
    for node in analysis.graph.nodes:
        if node.type == "call" and node.callerMethod is not None:
            calls_by_method[node.callerMethod].add(node.id)

    def analysis_entry_id(entry_clone_id: str) -> str | None:
        clone = flat_nodes.get(entry_clone_id)
        if clone is None or clone.type != "entry":
            return None
        original = _original_id(clone)
        if original in analysis.methods:
            return original
        return entry_by_name.get(clone.calleeFullName)

    def internal_entry_targets(call_clone_id: str) -> tuple[str, ...]:
        return tuple(
            target
            for target in invoke_out.get(call_clone_id, ())
            if target in flat_nodes and flat_nodes[target].type == "entry"
        )

    def collect_instance_clones(
        entry_clone_id: str, entry_id: str
    ) -> dict[str, str]:
        entry = original_nodes.get(entry_id)
        method_name = entry.calleeFullName if entry is not None else None
        own_ids = set(calls_by_method.get(method_name or "", ())) | {entry_id}
        clones: dict[str, str] = {}
        visited: set[str] = set()
        queue = deque([entry_clone_id])

        while queue:
            clone_id = queue.popleft()
            if clone_id in visited or clone_id not in flat_nodes:
                continue
            clone = flat_nodes[clone_id]
            original = _original_id(clone)
            if original not in own_ids:
                continue

            visited.add(clone_id)
            existing = clones.get(original)
            if existing is not None and existing != clone_id:
                raise ValueError(
                    f"method instance {entry_clone_id!r} contains two clones "
                    f"of original node {original!r}"
                )
            clones[original] = clone_id

            # Both internally expanded calls and external leaf calls resume
            # through edges attributed to their call site. Restricting this
            # to calls with an internal entry stops the caller-instance walk
            # at its first external operation once leaf returns correctly
            # carry returnFrom.
            if clone.type == "call" and returns_by_call.get(clone_id):
                successors = returns_by_call.get(clone_id, ())
            else:
                successors = plain_sequence_out.get(clone_id, ())
            for target in successors:
                target_node = flat_nodes.get(target)
                if target_node is not None and _original_id(target_node) in own_ids:
                    queue.append(target)

        return clones

    if flattened.rootId is not None:
        root_id = flattened.rootId
    else:
        root_id = next(
            (
                node.id
                for node in flattened.nodes
                if node.type == "entry"
                and node.calleeFullName == flattened.entryPoint
            ),
            None,
        )
    if root_id is None:
        return []

    output: list[Phase] = []
    owner_by_clone: dict[str, Phase] = {}
    overlaid_entries: set[str] = set()

    def add_node(phase: Phase, clone_id: str) -> None:
        owner = owner_by_clone.get(clone_id)
        if owner is not None and owner is not phase:
            raise ValueError(f"flattened node {clone_id!r} belongs to two phases")
        if owner is None:
            owner_by_clone[clone_id] = phase
            phase.nodes.append(clone_id)

    def replay_instance(entry_clone_id: str, merge_into: Phase | None) -> None:
        if entry_clone_id in overlaid_entries:
            return
        overlaid_entries.add(entry_clone_id)

        entry_id = analysis_entry_id(entry_clone_id)
        if entry_id is None:
            return
        method = analysis.methods.get(entry_id)
        structure = analysis.structures.get(entry_id)
        if method is None or structure is None:
            return

        clone_by_orig = collect_instance_clones(entry_clone_id, entry_id)
        phase_index_by_node = {
            node_id: index
            for index, phase in enumerate(method.phases)
            for node_id in phase.nodes
        }
        instance_phases: dict[int, Phase] = {}

        for original_id in _structure_node_ids(structure.structures):
            clone_id = clone_by_orig.get(original_id)
            if clone_id is None or original_id in analysis.excluded:
                continue

            if original_id in method.retainedCallIds:
                for callee_entry_clone in internal_entry_targets(clone_id):
                    replay_instance(callee_entry_clone, None)
                continue

            phase_index = phase_index_by_node.get(original_id)
            if phase_index is None:
                raise ValueError(
                    f"eligible call {original_id!r} is neither phased nor retained"
                )

            if merge_into is not None:
                phase = merge_into
            else:
                phase = instance_phases.get(phase_index)
                if phase is None:
                    source_phase = method.phases[phase_index]
                    phase = Phase(
                        label=source_phase.label,
                        labelSourcePhaseId=source_phase.id,
                    )
                    instance_phases[phase_index] = phase
                    output.append(phase)

            add_node(phase, clone_id)
            for callee_entry_clone in internal_entry_targets(clone_id):
                replay_instance(callee_entry_clone, phase)

    replay_instance(root_id, None)
    for index, phase in enumerate(output, start=1):
        phase.id = f"phase-{index}"
    return output


def phase_tree_dict(flattened: Graph, phases: list[Phase]) -> dict:
    """Serialize phases labelled deterministically during overlay."""
    return {
        "entryPoint": flattened.entryPoint or "",
        "phases": [phase.to_dict() for phase in phases],
    }


def overlay_phase_tree(analysis: Analysis, flattened: Graph) -> dict:
    """Return a frontend phase tree using method-phase labels."""
    return phase_tree_dict(flattened, overlay_phases(analysis, flattened))
