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
    LoopGroup,
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


def test_bridge_accumulates_nested_guards() -> None:
    edges = [
        Edge(
            "before",
            "outer_operator",
            "sequence",
            branchRequirements=[BranchRequirement("outer", "if")],
        ),
        Edge(
            "outer_operator",
            "inner_operator",
            "sequence",
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


def test_call_noise_bridge_stops_at_structural_anchor() -> None:
    edges = [
        Edge("before", "noise_before", "sequence"),
        Edge("noise_before", "branch_entry", "sequence"),
        Edge("branch_entry", "noise_inside", "sequence"),
        Edge("noise_inside", "after", "sequence"),
    ]

    bridged = _bridge_edges(edges, {"noise_before", "noise_inside"})

    assert {(edge.source, edge.target) for edge in bridged} == {
        ("before", "branch_entry"),
        ("branch_entry", "after"),
    }
    assert not any(
        edge.source == "before" and edge.target == "after"
        for edge in bridged
    )


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
                Edge("condition", "if_work", "sequence", branchRequirements=[
                    BranchRequirement("g1", "if")
                ]),
                Edge("condition", "after", "sequence", branchRequirements=[
                    BranchRequirement("g1", "else")
                ]),
                Edge("if_work", "after", "sequence", branchRequirements=[
                    BranchRequirement("g1", "if")
                ]),
        ],
        branchGroups=[BranchGroup(
            "g1",
            "IF",
            entryNodeId="condition",
            method=METHOD,
            arms=[
                BranchArm("if", exits=[ArmExit("continues")]),
                BranchArm(
                    "else",
                    empty=True,
                    exits=[ArmExit("continues", destinationNodeId="after")],
                ),
            ],
        )],
    )

    filtered = filter_noise_cfg(graph)

    assert "condition" not in {node.id for node in filtered.nodes}
    # Filtering does not infer ordering metadata.
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
                Edge("entry", "throw_exit", "sequence", branchRequirements=[
                    BranchRequirement("g1", "if")
                ]),
                Edge("entry", "return_exit", "sequence", branchRequirements=[
                    BranchRequirement("g2", "if")
                ]),
                    Edge("entry", "after", "sequence", branchRequirements=[
                        BranchRequirement("g1", "else"),
                        BranchRequirement("g2", "else")
                    ]),
            Edge("after", "end", "sequence"),
        ],
        branchGroups=[
            BranchGroup(
                "g1",
                "IF",
                entryNodeId="entry",
                method=METHOD,
                arms=[
                    BranchArm("if", empty=True, exits=[ArmExit("throw")]),
                        BranchArm(
                            "else",
                            empty=True,
                            exits=[ArmExit(
                                "continues",
                                destinationNodeId="after",
                            )],
                    ),
                ],
            ),
            BranchGroup(
                "g2",
                "IF",
                entryNodeId="entry",
                method=METHOD,
                arms=[
                    BranchArm(
                        "if", empty=True, exits=[ArmExit("return", "return_exit")]
                    ),
                    BranchArm(
                        "else",
                        empty=True,
                        exits=[ArmExit("continues", destinationNodeId="after")],
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
        {("g2", "if")},
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


def test_structural_decisions_isolate_consecutive_empty_branch_routes() -> None:
    """Later empty guards must never contaminate an earlier operation edge."""
    graph = Graph(
        entryPoint=METHOD,
        nodes=[
            Node("entry", "entry", calleeFullName=METHOD),
            Node("outer_condition", "call", calleeFullName="<operator>.notEquals", callerMethod=METHOD),
            Node("outer_decision", "structure", callerMethod=METHOD, structureGroupId="outer", structureRole="decision"),
            Node(
                "compare",
                "call",
                calleeFullName="Comparable.compareTo:int(java.lang.Object)",
                callerMethod=METHOD,
                branchArms=[BranchArmRef("outer", "if")],
            ),
            Node("inner_condition", "call", calleeFullName="<operator>.equals", callerMethod=METHOD),
            Node("inner_decision", "structure", callerMethod=METHOD, structureGroupId="inner", structureRole="decision"),
            Node(
                "found",
                "exit",
                callerMethod=METHOD,
                exitKind="return",
                branchArms=[
                    BranchArmRef("outer", "if"),
                    BranchArmRef("inner", "if"),
                ],
            ),
            Node("later_condition", "call", calleeFullName="<operator>.logicalNot", callerMethod=METHOD),
            Node("later_decision", "structure", callerMethod=METHOD, structureGroupId="later", structureRole="decision"),
            Node(
                "absent",
                "exit",
                callerMethod=METHOD,
                exitKind="return",
                branchArms=[BranchArmRef("later", "if")],
            ),
            Node("after", "call", calleeFullName="Service.after:void()", callerMethod=METHOD),
        ],
        edges=[
            Edge("entry", "outer_condition", "sequence"),
            Edge("outer_condition", "outer_decision", "sequence"),
                Edge("outer_decision", "compare", "sequence", branchRequirements=[
                    BranchRequirement("outer", "if")
                ]),
                Edge("outer_decision", "later_condition", "sequence", branchRequirements=[
                    BranchRequirement("outer", "else")
                ]),
                Edge("compare", "inner_condition", "sequence", branchRequirements=[
                    BranchRequirement("outer", "if")
                ]),
                Edge("inner_condition", "inner_decision", "sequence", branchRequirements=[
                    BranchRequirement("outer", "if")
                ]),
                Edge("inner_decision", "found", "sequence", branchRequirements=[
                    BranchRequirement("outer", "if"),
                    BranchRequirement("inner", "if"),
                ]),
                Edge("inner_decision", "later_condition", "sequence", branchRequirements=[
                    BranchRequirement("outer", "if"),
                    BranchRequirement("inner", "else"),
                ]),
            Edge("later_condition", "later_decision", "sequence"),
                Edge("later_decision", "absent", "sequence", branchRequirements=[
                    BranchRequirement("later", "if")
                ]),
                Edge("later_decision", "after", "sequence", branchRequirements=[
                    BranchRequirement("later", "else")
                ]),
        ],
        branchGroups=[
            BranchGroup(
                "outer", "IF", entryNodeId="outer_decision", method=METHOD,
                arms=[
                    BranchArm("if", exits=[ArmExit("continues")]),
                    BranchArm(
                        "else",
                        empty=True,
                        exits=[ArmExit("continues", destinationNodeId="later_condition")],
                    ),
                ],
            ),
            BranchGroup(
                "inner", "IF", entryNodeId="inner_decision", method=METHOD,
                arms=[
                    BranchArm("if", empty=True, exits=[ArmExit("return")]),
                    BranchArm(
                        "else",
                        empty=True,
                        exits=[ArmExit("continues", destinationNodeId="later_condition")],
                    ),
                ],
            ),
            BranchGroup(
                "later", "IF", entryNodeId="later_decision", method=METHOD,
                arms=[
                    BranchArm("if", empty=True, exits=[ArmExit("return")]),
                    BranchArm(
                        "else",
                        empty=True,
                        exits=[ArmExit("continues", destinationNodeId="after")],
                    ),
                ],
            ),
        ],
    )

    filtered = filter_noise_cfg(graph)

    assert {group.id for group in filtered.branchGroups} == {"outer", "inner", "later"}
    groups = {group.id: group for group in filtered.branchGroups}
    # Removed compatibility targets are cleared, never reconstructed by
    # filtering. Anchored production artifacts point continuing exits at the
    # surviving structure exit instead.
    assert groups["outer"].arms[1].exits[0].destinationNodeId is None
    assert groups["inner"].arms[1].exits[0].destinationNodeId is None
    assert groups["later"].arms[1].exits[0].destinationNodeId == "after"
    _edge_by_requirements(filtered.edges, "outer_decision", "compare", {("outer", "if")})
    assert not any(
        requirement.groupId in {"inner", "later"}
        for edge in filtered.edges
        if edge.source == "outer_decision" and edge.target == "compare"
        for requirement in edge.branchRequirements
    )
    _edge_by_requirements(filtered.edges, "inner_decision", "found", {("outer", "if"), ("inner", "if")})
    _edge_by_requirements(filtered.edges, "later_decision", "absent", {("later", "if")})
    _edge_by_requirements(filtered.edges, "later_decision", "after", {("later", "else")})


def test_structural_decision_preserves_existing_nonempty_if_else_geometry() -> None:
    graph = Graph(
        entryPoint=METHOD,
        nodes=[
            Node("entry", "entry", calleeFullName=METHOD),
            Node("condition_call", "call", calleeFullName="Flags.check:boolean()", callerMethod=METHOD),
            Node("decision", "structure", callerMethod=METHOD, structureGroupId="g1", structureRole="decision"),
            Node("left", "call", calleeFullName="Service.left:void()", callerMethod=METHOD,
                 branchArms=[BranchArmRef("g1", "if")]),
            Node("right", "call", calleeFullName="Service.right:void()", callerMethod=METHOD,
                 branchArms=[BranchArmRef("g1", "else")]),
            Node("after", "call", calleeFullName="Service.after:void()", callerMethod=METHOD),
        ],
        edges=[
            Edge("entry", "condition_call", "sequence"),
            Edge("condition_call", "decision", "sequence"),
                Edge("decision", "left", "sequence", branchRequirements=[
                    BranchRequirement("g1", "if")
                ]),
                Edge("decision", "right", "sequence", branchRequirements=[
                    BranchRequirement("g1", "else")
                ]),
                Edge("left", "after", "sequence", branchRequirements=[
                    BranchRequirement("g1", "if")
                ]),
                Edge("right", "after", "sequence", branchRequirements=[
                    BranchRequirement("g1", "else")
                ]),
        ],
        branchGroups=[BranchGroup(
            "g1", "IF", entryNodeId="decision", method=METHOD,
            arms=[BranchArm("if"), BranchArm("else")],
        )],
    )

    filtered = filter_noise_cfg(graph)

    assert filtered.branchGroups[0].entryNodeId == "decision"
    _edge_by_requirements(filtered.edges, "decision", "left", {("g1", "if")})
    _edge_by_requirements(filtered.edges, "decision", "right", {("g1", "else")})
    assert any(edge.source == "condition_call" and edge.target == "decision" for edge in filtered.edges)


def test_removed_transparent_group_drops_requirements_and_deduplicates_routes() -> None:
    graph = Graph(
        entryPoint=METHOD,
        nodes=[
            Node("entry", "entry", calleeFullName=METHOD),
            Node("decision", "structure", callerMethod=METHOD, structureGroupId="transparent", structureRole="decision"),
            Node("after", "call", calleeFullName="Service.after:void()", callerMethod=METHOD),
        ],
        edges=[
            Edge("entry", "decision", "sequence"),
            Edge(
                "decision",
                "after",
                "sequence",
                branchRequirements=[BranchRequirement("transparent", "if")],
            ),
            Edge(
                "decision",
                "after",
                "sequence",
                branchRequirements=[BranchRequirement("transparent", "else")],
            ),
        ],
        branchGroups=[BranchGroup(
            "transparent",
            "IF",
            entryNodeId="decision",
            method=METHOD,
            arms=[
                BranchArm("if", empty=True, exits=[ArmExit("continues")]),
                BranchArm("else", empty=True, exits=[ArmExit("continues")]),
            ],
        )],
    )

    filtered = filter_noise_cfg(graph)

    assert filtered.branchGroups == []
    assert all(node.id != "decision" for node in filtered.nodes)
    routes = [
        edge for edge in filtered.edges
        if edge.type == "sequence"
        and edge.source == "entry"
        and edge.target == "after"
    ]
    assert len(routes) == 1
    assert routes[0].branchRequirements == []


def test_parent_group_is_retained_when_it_contains_a_meaningful_child() -> None:
    graph = Graph(
        entryPoint=METHOD,
        nodes=[
            Node("entry", "entry", calleeFullName=METHOD),
            Node("outer_decision", "structure", callerMethod=METHOD, structureGroupId="outer", structureRole="decision"),
            Node(
                "inner_decision",
                "structure",
                callerMethod=METHOD,
                structureGroupId="inner",
                structureRole="decision",
                branchArms=[BranchArmRef("outer", "if")],
            ),
            Node(
                "work",
                "call",
                callerMethod=METHOD,
                calleeFullName="Service.work:void()",
                branchArms=[BranchArmRef("inner", "if")],
            ),
            Node("after", "call", callerMethod=METHOD, calleeFullName="Service.after:void()"),
        ],
        edges=[
            Edge("entry", "outer_decision", "sequence"),
            Edge(
                "outer_decision",
                "inner_decision",
                "sequence",
                branchRequirements=[BranchRequirement("outer", "if")],
            ),
            Edge("inner_decision", "work", "sequence"),
            Edge("inner_decision", "after", "sequence"),
            Edge("work", "after", "sequence"),
        ],
        branchGroups=[
            BranchGroup(
                "outer",
                "IF",
                entryNodeId="outer_decision",
                method=METHOD,
                arms=[BranchArm("if", empty=True), BranchArm("else", empty=True)],
            ),
            BranchGroup(
                "inner",
                "IF",
                entryNodeId="inner_decision",
                method=METHOD,
                enclosingRequirements=[BranchRequirement("outer", "if")],
                arms=[
                    BranchArm("if"),
                    BranchArm(
                        "else",
                        empty=True,
                        exits=[ArmExit("continues", destinationNodeId="after")],
                    ),
                ],
            ),
        ],
    )

    filtered = filter_noise_cfg(graph)

    assert {group.id for group in filtered.branchGroups} == {"outer", "inner"}
    inner_decision = next(node for node in filtered.nodes if node.id == "inner_decision")
    assert inner_decision.branchArms == [BranchArmRef("outer", "if")]
    inner_group = next(group for group in filtered.branchGroups if group.id == "inner")
    assert inner_group.enclosingRequirements == [BranchRequirement("outer", "if")]


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
                deadEnd=True,
                branchArms=[BranchArmRef("g1", "if")],
            ),
            Node(
                "second_throw",
                "call",
                calleeFullName="IllegalArgumentException.<init>",
                callerMethod=METHOD,
                deadEnd=True,
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
                # Extraction requirements come from the source route. A later
                # sibling guard has not been selected on this terminal edge.
                branchRequirements=[BranchRequirement("g1", "if")],
            ),
                Edge(
                    "entry",
                    "second_throw",
                    "sequence",
                    branchRequirements=[
                        BranchRequirement("g1", "else"),
                        BranchRequirement("g2", "if"),
                    ],
                ),
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
                entryNodeId="entry",
                method=METHOD,
                arms=[
                    BranchArm("if", exits=[ArmExit("throw")]),
                    BranchArm(
                        "else",
                        empty=True,
                        exits=[ArmExit(
                            "continues", destinationNodeId="after"
                        )],
                    ),
                ],
            ),
            BranchGroup(
                "g2",
                "IF",
                entryNodeId="entry",
                method=METHOD,
                arms=[
                    BranchArm("if", exits=[ArmExit("throw")]),
                    BranchArm(
                        "else",
                        empty=True,
                        exits=[ArmExit("continues", destinationNodeId="after")],
                    ),
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


def test_branch_cleanup_does_not_remove_loop_structure_anchors() -> None:
    graph = Graph(
        entryPoint=METHOD,
        nodes=[
            Node("entry", "entry", calleeFullName=METHOD),
            Node(
                "loop_entry", "structure", callerMethod=METHOD,
                structureGroupId="loop1", structureRole="entry",
            ),
            Node(
                "body", "call", callerMethod=METHOD,
                calleeFullName="Service.work:void()", loopIds=["loop1"],
            ),
            Node(
                "loop_exit", "structure", callerMethod=METHOD,
                structureGroupId="loop1", structureRole="exit",
            ),
            Node(
                "after", "call", callerMethod=METHOD,
                calleeFullName="Service.after:void()",
            ),
        ],
        edges=[
            Edge("entry", "loop_entry", "sequence"),
            Edge("loop_entry", "body", "sequence"),
            Edge("body", "loop_exit", "sequence"),
            Edge("loop_exit", "after", "sequence"),
        ],
        loopGroups=[LoopGroup(
            "loop1", "WHILE", method=METHOD,
            entryNodeId="loop_entry", exitNodeId="loop_exit",
        )],
    )

    filtered = filter_noise_cfg(graph)

    assert {"loop_entry", "loop_exit"}.issubset(
        {node.id for node in filtered.nodes}
    )
    assert {(edge.source, edge.target) for edge in filtered.edges} >= {
        ("entry", "loop_entry"),
        ("loop_entry", "body"),
        ("body", "loop_exit"),
        ("loop_exit", "after"),
    }


def test_empty_nested_branches_are_removed_to_a_fixed_point() -> None:
    outer = BranchGroup(
        "outer", "IF", method=METHOD,
        entryNodeId="outer_entry", exitNodeId="outer_exit",
        arms=[
            BranchArm("if", exits=[ArmExit("continues", "outer_exit")]),
            BranchArm("else", exits=[ArmExit("continues", "outer_exit")]),
        ],
    )
    inner = BranchGroup(
        "inner", "IF", method=METHOD,
        entryNodeId="inner_entry", exitNodeId="inner_exit",
        arms=[
            BranchArm("if", exits=[ArmExit("continues", "inner_exit")]),
            BranchArm("else", exits=[ArmExit("continues", "inner_exit")]),
        ],
    )
    graph = Graph(
        entryPoint=METHOD,
        nodes=[
            Node("entry", "entry", calleeFullName=METHOD),
            Node("outer_entry", "structure", structureGroupId="outer", structureRole="entry"),
            Node("outer_decision", "structure", structureGroupId="outer", structureRole="decision"),
            Node(
                "inner_entry", "structure", structureGroupId="inner", structureRole="entry",
                branchArms=[BranchArmRef("outer", "if")],
            ),
            Node(
                "inner_decision", "structure", structureGroupId="inner", structureRole="decision",
                branchArms=[BranchArmRef("outer", "if")],
            ),
            Node(
                "inner_exit", "structure", structureGroupId="inner", structureRole="exit",
                branchArms=[BranchArmRef("outer", "if")],
            ),
            Node("outer_exit", "structure", structureGroupId="outer", structureRole="exit"),
            Node("after", "call", calleeFullName="Service.after:void()"),
        ],
        edges=[
            Edge("entry", "outer_entry", "sequence"),
            Edge("outer_entry", "outer_decision", "sequence"),
            Edge("outer_decision", "inner_entry", "sequence", branchRequirements=[BranchRequirement("outer", "if")]),
            Edge("inner_entry", "inner_decision", "sequence", branchRequirements=[BranchRequirement("outer", "if")]),
            Edge("inner_decision", "inner_exit", "sequence", branchRequirements=[BranchRequirement("outer", "if"), BranchRequirement("inner", "if")]),
            Edge("inner_exit", "outer_exit", "sequence", branchRequirements=[BranchRequirement("outer", "if")]),
            Edge("outer_decision", "outer_exit", "sequence", branchRequirements=[BranchRequirement("outer", "else")]),
            Edge("outer_exit", "after", "sequence"),
        ],
        branchGroups=[outer, inner],
    )

    filtered = filter_noise_cfg(graph)

    assert filtered.branchGroups == []
    assert {node.id for node in filtered.nodes} == {"entry", "after"}
    assert [(edge.source, edge.target, edge.branchRequirements) for edge in filtered.edges] == [
        ("entry", "after", [])
    ]


def test_meaningful_nested_branch_retains_its_empty_parent_loop() -> None:
    graph = Graph(
        entryPoint=METHOD,
        nodes=[
            Node("entry", "entry", calleeFullName=METHOD),
            Node("loop_entry", "structure", structureGroupId="loop", structureRole="entry"),
            Node(
                "branch_entry", "structure", structureGroupId="branch", structureRole="entry",
                loopIds=["loop"],
            ),
            Node(
                "work", "call", calleeFullName="Service.work:void()",
                loopIds=["loop"], branchArms=[BranchArmRef("branch", "if")],
            ),
            Node(
                "branch_exit", "structure", structureGroupId="branch", structureRole="exit",
                loopIds=["loop"],
            ),
            Node("loop_exit", "structure", structureGroupId="loop", structureRole="exit"),
            Node("after", "call", calleeFullName="Service.after:void()"),
        ],
        edges=[
            Edge("entry", "loop_entry", "sequence"),
            Edge("loop_entry", "branch_entry", "sequence"),
            Edge("branch_entry", "work", "sequence", branchRequirements=[BranchRequirement("branch", "if")]),
            Edge("work", "branch_exit", "sequence", branchRequirements=[BranchRequirement("branch", "if")]),
            Edge("branch_exit", "loop_exit", "sequence"),
            Edge("loop_exit", "after", "sequence"),
        ],
        branchGroups=[BranchGroup(
            "branch", "IF", entryNodeId="branch_entry", exitNodeId="branch_exit",
            arms=[
                BranchArm("if", exits=[ArmExit("continues", "branch_exit")]),
                BranchArm("else", exits=[ArmExit("continues", "branch_exit")]),
            ],
        )],
        loopGroups=[LoopGroup(
            "loop", "WHILE", entryNodeId="loop_entry", exitNodeId="loop_exit",
        )],
    )

    filtered = filter_noise_cfg(graph)

    assert [group.id for group in filtered.branchGroups] == ["branch"]
    assert [group.id for group in filtered.loopGroups] == ["loop"]
    assert {"loop_entry", "branch_entry", "branch_exit", "loop_exit"} <= {
        node.id for node in filtered.nodes
    }


def test_noise_only_loop_is_removed_with_all_references() -> None:
    graph = Graph(
        entryPoint=METHOD,
        nodes=[
            Node("entry", "entry", calleeFullName=METHOD),
            Node("loop_entry", "structure", structureGroupId="loop", structureRole="entry"),
            Node(
                "guard_noise", "call", calleeFullName="<operator>.equals",
                loopIds=["loop"],
            ),
            Node("loop_exit", "structure", structureGroupId="loop", structureRole="exit"),
            Node(
                "after", "call", calleeFullName="Service.after:void()",
            ),
            Node("method_end", "exit", exitKind="fallthrough", loopIds=["loop"]),
        ],
        edges=[
            Edge("entry", "loop_entry", "sequence"),
            Edge("loop_entry", "guard_noise", "sequence"),
            Edge("guard_noise", "loop_exit", "sequence"),
            Edge("loop_exit", "after", "sequence"),
            Edge("after", "method_end", "sequence"),
        ],
        loopGroups=[LoopGroup(
            "loop", "WHILE", entryNodeId="loop_entry", exitNodeId="loop_exit",
        )],
    )

    filtered = filter_noise_cfg(graph)

    assert filtered.loopGroups == []
    assert {(edge.source, edge.target) for edge in filtered.edges} == {
        ("entry", "after"), ("after", "method_end")
    }
    assert all(node.loopIds == [] for node in filtered.nodes)


def test_filter_clears_removed_terminal_destination_but_keeps_outcome() -> None:
    graph = Graph(
        entryPoint=METHOD,
        nodes=[
            Node("entry", "entry", calleeFullName=METHOD),
            Node("branch_entry", "structure", structureGroupId="branch", structureRole="entry"),
            Node("decision", "structure", structureGroupId="branch", structureRole="decision"),
            Node("branch_exit", "structure", structureGroupId="branch", structureRole="exit"),
            Node("after", "call", calleeFullName="Service.after:void()"),
        ],
        edges=[
            Edge("entry", "branch_entry", "sequence"),
            Edge("branch_entry", "decision", "sequence"),
            Edge("decision", "branch_exit", "sequence", branchRequirements=[BranchRequirement("branch", "else")]),
            Edge("branch_exit", "after", "sequence"),
        ],
        branchGroups=[BranchGroup(
            "branch", "IF", entryNodeId="branch_entry", exitNodeId="branch_exit",
            arms=[
                BranchArm("if", exits=[ArmExit("throw", "removed_throw")]),
                BranchArm("else", exits=[ArmExit("continues", "branch_exit")]),
            ],
        )],
    )

    filtered = filter_noise_cfg(graph)

    throw_exit = filtered.branchGroups[0].arms[0].exits[0]
    assert throw_exit.kind == "throw"
    assert throw_exit.destinationNodeId is None
