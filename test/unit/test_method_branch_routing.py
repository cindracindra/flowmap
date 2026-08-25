import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"))

from domain.method_branch_routing import prepare_method_branch_routes
from model import (
    ArmExit,
    BranchArm,
    BranchArmRef,
    BranchGroup,
    Edge,
    MethodDefinition,
    Node,
)


def _requirements(edge: Edge) -> set[tuple[str, str]]:
    return {(item.groupId, item.armLabel) for item in edge.branchRequirements}


def _method(
    nodes: list[Node],
    edges: list[Edge],
    groups: list[BranchGroup],
) -> MethodDefinition:
    entry = Node(id="entry", type="entry", calleeFullName="Example.run:void()")
    return MethodDefinition(
        entryId="entry",
        methodFullName="Example.run:void()",
        entry=entry,
        nodes=nodes,
        sequenceEdges=edges,
        branchGroups=groups,
    )


def test_tags_non_empty_and_nested_arm_edges() -> None:
    method = _method(
        nodes=[
            Node(
                id="outer",
                type="call",
                branchArms=[BranchArmRef("g1", "if")],
            ),
            Node(
                id="nested",
                type="call",
                branchArms=[
                    BranchArmRef("g1", "if"),
                    BranchArmRef("g2", "if"),
                ],
            ),
        ],
        edges=[
            Edge("entry", "outer", "sequence"),
            Edge("outer", "nested", "sequence"),
        ],
        groups=[
            BranchGroup("g1", "IF", arms=[BranchArm("if"), BranchArm("else")]),
            BranchGroup("g2", "IF", arms=[BranchArm("if"), BranchArm("else")]),
        ],
    )

    routed = prepare_method_branch_routes(method)

    assert routed.branchGroups[0].arms[0].firstCallId == "outer"
    assert routed.branchGroups[0].branchPointIds == ["entry"]
    assert _requirements(routed.sequenceEdges[0]) == {("g1", "if")}
    assert _requirements(routed.sequenceEdges[1]) == {
        ("g1", "if"),
        ("g2", "if"),
    }


def test_materializes_empty_continuing_arm_to_local_continuation() -> None:
    method = _method(
        nodes=[
            Node(
                id="work",
                type="call",
                branchArms=[BranchArmRef("g1", "if")],
            ),
            Node(id="after", type="call"),
            Node(id="end", type="exit", exitKind="fallthrough"),
        ],
        edges=[
            Edge("entry", "work", "sequence"),
            Edge("work", "after", "sequence"),
            Edge("after", "end", "sequence"),
        ],
        groups=[BranchGroup(
            "g1",
            "IF",
            arms=[
                BranchArm("if", exits=[ArmExit("continues")]),
                BranchArm("else", exits=[ArmExit("continues")]),
            ],
        )],
    )

    routed = prepare_method_branch_routes(method)
    skip = next(
        edge for edge in routed.sequenceEdges
        if edge.source == "entry"
        and edge.target == "after"
        and ("g1", "else") in _requirements(edge)
    )

    assert skip.type == "sequence"
    assert routed.branchGroups[0].arms[1].empty is True
    assert routed.branchGroups[0].arms[1].targetIds == ["after"]
    assert _requirements(routed.sequenceEdges[0]) == {("g1", "if")}


def test_annotates_existing_empty_arm_edge_without_leaving_uncontrolled_copy() -> None:
    method = _method(
        nodes=[
            Node(
                id="work",
                type="call",
                branchArms=[BranchArmRef("g1", "if")],
            ),
            Node(id="after", type="call"),
        ],
        edges=[
            Edge("entry", "work", "sequence"),
            Edge("entry", "after", "sequence"),
        ],
        groups=[BranchGroup(
            "g1",
            "IF",
            branchPointIds=["entry"],
            arms=[
                BranchArm("if", exits=[ArmExit("continues")]),
                BranchArm("else", exits=[ArmExit("continues")]),
            ],
        )],
    )

    routed = prepare_method_branch_routes(method)
    routes = [
        edge for edge in routed.sequenceEdges
        if edge.source == "entry" and edge.target == "after"
    ]

    assert len(routes) == 1
    assert _requirements(routes[0]) == {("g1", "else")}


def test_throw_arm_does_not_gain_a_normal_continuation() -> None:
    method = _method(
        nodes=[
            Node(
                id="throw",
                type="exit",
                exitKind="throw",
                branchArms=[BranchArmRef("g1", "if")],
            ),
            Node(id="after", type="call"),
        ],
        edges=[
            Edge("entry", "throw", "sequence"),
            Edge("entry", "after", "sequence"),
        ],
        groups=[BranchGroup(
            "g1",
            "IF",
            branchPointIds=["entry"],
            arms=[
                BranchArm("if", exits=[ArmExit("throw")]),
                BranchArm("else", exits=[ArmExit("continues")]),
            ],
        )],
    )

    routed = prepare_method_branch_routes(method)

    assert not any(
        edge.source == "throw" and edge.target == "after"
        for edge in routed.sequenceEdges
    )
    assert _requirements(routed.sequenceEdges[0]) == {("g1", "if")}
    assert _requirements(routed.sequenceEdges[1]) == {("g1", "else")}


def test_return_only_guard_recovers_branch_point_from_exit_route() -> None:
    method = _method(
        nodes=[
            Node(
                id="early_return",
                type="exit",
                exitKind="return",
                branchArms=[BranchArmRef("g1", "if")],
            ),
            Node(id="recurse", type="call"),
            Node(id="final_return", type="exit", exitKind="return"),
        ],
        edges=[
            Edge("entry", "early_return", "sequence"),
            Edge("entry", "recurse", "sequence"),
            Edge("recurse", "final_return", "sequence"),
        ],
        groups=[BranchGroup(
            "g1",
            "IF",
            # The extraction branch point was a filtered condition/operator.
            branchPointIds=["removed-condition"],
            arms=[
                BranchArm("if", exits=[ArmExit("return")]),
                BranchArm("else", exits=[ArmExit("continues")]),
            ],
        )],
    )

    routed = prepare_method_branch_routes(method)

    assert routed.branchGroups[0].branchPointIds == ["entry"]
    assert routed.branchGroups[0].arms[0].empty is True
    assert routed.branchGroups[0].arms[1].empty is True
    assert _requirements(next(
        edge for edge in routed.sequenceEdges if edge.target == "early_return"
    )) == {("g1", "if")}
    assert _requirements(next(
        edge for edge in routed.sequenceEdges if edge.target == "recurse"
    )) == {("g1", "else")}
