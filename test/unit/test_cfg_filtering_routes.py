import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"))

from domain.cfg_filtering import _bridge_edges, filter_noise_cfg
from model import (
    ArmExit,
    BranchArm,
    BranchArmRef,
    BranchGroup,
    BranchRequirement,
    Edge,
    Graph,
    Node,
)


METHOD = "Example.run:void()"


def _requirements(edge: Edge) -> set[tuple[str, str]]:
    return {(item.groupId, item.armLabel) for item in edge.branchRequirements}


def _edge_by_requirements(
    edges: list[Edge],
    source: str,
    target: str,
    requirements: set[tuple[str, str]],
) -> Edge:
    return next(
        edge for edge in edges
        if edge.source == source
        and edge.target == target
        and _requirements(edge) == requirements
    )


def test_bridge_keeps_two_semantically_distinct_paths_to_same_node() -> None:
    edges = [
        Edge("before", "if_operator", "sequence", branchRequirements=[
            BranchRequirement("g1", "if")
        ]),
        Edge("if_operator", "after", "sequence"),
        Edge("before", "else_operator", "sequence", branchRequirements=[
            BranchRequirement("g1", "else")
        ]),
        Edge("else_operator", "after", "sequence"),
    ]

    bridged = _bridge_edges(edges, {"if_operator", "else_operator"})

    assert len(bridged) == 2
    _edge_by_requirements(bridged, "before", "after", {("g1", "if")})
    _edge_by_requirements(bridged, "before", "after", {("g1", "else")})


def test_bridge_accumulates_nested_guards_and_preserves_edge_metadata() -> None:
    edges = [
        Edge(
            "before",
            "outer_operator",
            "sequence",
            returnFrom="call-site",
            fallback=True,
            branchRequirements=[BranchRequirement("outer", "if")],
        ),
        Edge(
            "outer_operator",
            "inner_operator",
            "sequence",
            loopBack=True,
            branchRequirements=[BranchRequirement("inner", "else")],
        ),
        Edge("inner_operator", "after", "sequence"),
    ]

    bridged = _bridge_edges(edges, {"outer_operator", "inner_operator"})

    route = _edge_by_requirements(
        bridged,
        "before",
        "after",
        {("outer", "if"), ("inner", "else")},
    )
    assert route.returnFrom == "call-site"
    assert route.fallback is True
    assert route.loopBack is True


def test_bridge_rejects_logically_impossible_conflicting_arm_path() -> None:
    edges = [
        Edge("before", "operator", "sequence", branchRequirements=[
            BranchRequirement("g1", "if")
        ]),
        Edge("operator", "after", "sequence", branchRequirements=[
            BranchRequirement("g1", "else")
        ]),
    ]

    assert _bridge_edges(edges, {"operator"}) == []


def test_filter_removes_condition_and_exposes_empty_continuing_route() -> None:
    graph = Graph(
        entryPoint=METHOD,
        nodes=[
            Node("entry", "entry", calleeFullName=METHOD),
            Node("condition", "call", calleeFullName="<operator>.equals", callerMethod=METHOD),
            Node(
                "if_work",
                "call",
                calleeFullName="Service.work:void()",
                callerMethod=METHOD,
                branchArms=[BranchArmRef("g1", "if")],
            ),
            Node("after", "call", calleeFullName="Service.after:void()", callerMethod=METHOD),
        ],
        edges=[
            Edge("entry", "condition", "sequence"),
            Edge("condition", "if_work", "sequence"),
            Edge("condition", "after", "sequence"),
            Edge("if_work", "after", "sequence"),
        ],
        branchGroups=[BranchGroup(
            "g1",
            "IF",
            method=METHOD,
            branchPointIds=["condition"],
            arms=[
                BranchArm("if", exits=[ArmExit("continues")]),
                BranchArm(
                    "else",
                    empty=True,
                    exits=[ArmExit("continues")],
                    targetIds=["after"],
                ),
            ],
        )],
    )

    filtered = filter_noise_cfg(graph)

    assert "condition" not in {node.id for node in filtered.nodes}
    assert filtered.branchGroups[0].branchPointIds == ["entry"]
    assert filtered.branchGroups[0].arms[0].firstCallId == "if_work"
    assert filtered.branchGroups[0].arms[1].empty is True
    _edge_by_requirements(filtered.edges, "entry", "if_work", {("g1", "if")})
    _edge_by_requirements(filtered.edges, "entry", "after", {("g1", "else")})


def test_consecutive_empty_terminal_branches_keep_only_reachable_routes() -> None:
    graph = Graph(
        entryPoint=METHOD,
        nodes=[
            Node("entry", "entry", calleeFullName=METHOD),
            Node(
                "throw_exit",
                "exit",
                callerMethod=METHOD,
                exitKind="throw",
                branchArms=[BranchArmRef("g1", "if")],
            ),
            Node(
                "return_exit",
                "exit",
                callerMethod=METHOD,
                exitKind="return",
                branchArms=[BranchArmRef("g2", "if")],
            ),
            Node("after", "call", calleeFullName="Service.after:void()", callerMethod=METHOD),
            Node("end", "exit", callerMethod=METHOD, exitKind="fallthrough"),
        ],
        edges=[
            Edge("entry", "throw_exit", "sequence"),
            Edge("entry", "return_exit", "sequence"),
            Edge("entry", "after", "sequence"),
            Edge("after", "end", "sequence"),
        ],
        branchGroups=[
            BranchGroup(
                "g1",
                "IF",
                method=METHOD,
                branchPointIds=["entry"],
                arms=[
                    BranchArm("if", empty=True, exits=[ArmExit("throw")]),
                    BranchArm(
                        "else",
                        empty=True,
                        exits=[ArmExit("continues")],
                        targetIds=["return_exit", "after"],
                    ),
                ],
            ),
            BranchGroup(
                "g2",
                "IF",
                method=METHOD,
                branchPointIds=["entry"],
                arms=[
                    BranchArm(
                        "if", empty=True, exits=[ArmExit("return")], targetIds=["return_exit"]
                    ),
                    BranchArm(
                        "else", empty=True, exits=[ArmExit("continues")], targetIds=["after"]
                    ),
                ],
            ),
        ],
    )

    filtered = filter_noise_cfg(graph)

    _edge_by_requirements(filtered.edges, "entry", "throw_exit", {("g1", "if")})
    _edge_by_requirements(
        filtered.edges,
        "entry",
        "return_exit",
        {("g1", "else"), ("g2", "if")},
    )
    _edge_by_requirements(
        filtered.edges,
        "entry",
        "after",
        {("g1", "else"), ("g2", "else")},
    )
    assert not any(
        edge.source == "throw_exit" and edge.type == "sequence"
        for edge in filtered.edges
    )


def test_later_sibling_membership_does_not_control_earlier_throw_route() -> None:
    graph = Graph(
        entryPoint=METHOD,
        nodes=[
            Node("entry", "entry", calleeFullName=METHOD),
            Node(
                "first_throw",
                "call",
                calleeFullName="IllegalStateException.<init>",
                callerMethod=METHOD,
                terminus="throw",
                branchArms=[BranchArmRef("g1", "if")],
            ),
            Node(
                "second_throw",
                "call",
                calleeFullName="IllegalArgumentException.<init>",
                callerMethod=METHOD,
                terminus="throw",
                branchArms=[
                    BranchArmRef("g1", "else"),
                    BranchArmRef("g2", "if"),
                ],
            ),
            Node("after", "call", calleeFullName="Service.after:void()", callerMethod=METHOD),
        ],
        edges=[
            Edge(
                "entry",
                "first_throw",
                "sequence",
                # A pre-filter route can carry the later guard's lexical
                # normal arm even though execution terminates before it.
                branchRequirements=[
                    BranchRequirement("g1", "if"),
                    BranchRequirement("g2", "else"),
                ],
            ),
            Edge("entry", "second_throw", "sequence"),
            Edge(
                "entry",
                "after",
                "sequence",
                branchRequirements=[
                    BranchRequirement("g1", "else"),
                    BranchRequirement("g2", "else"),
                ],
            ),
        ],
        branchGroups=[
            BranchGroup(
                "g1",
                "IF",
                method=METHOD,
                branchPointIds=["entry"],
                arms=[
                    BranchArm("if", exits=[ArmExit("throw")]),
                    BranchArm("else", empty=True, exits=[ArmExit("continues")], targetIds=["second_throw", "after"]),
                ],
            ),
            BranchGroup(
                "g2",
                "IF",
                method=METHOD,
                branchPointIds=["entry"],
                arms=[
                    BranchArm("if", exits=[ArmExit("throw")]),
                    BranchArm("else", empty=True, exits=[ArmExit("continues")], targetIds=["after"]),
                ],
            ),
        ],
    )

    filtered = filter_noise_cfg(graph)

    _edge_by_requirements(filtered.edges, "entry", "first_throw", {("g1", "if")})
    _edge_by_requirements(
        filtered.edges,
        "entry",
        "second_throw",
        {("g1", "else"), ("g2", "if")},
    )
    _edge_by_requirements(
        filtered.edges,
        "entry",
        "after",
        {("g1", "else"), ("g2", "else")},
    )
