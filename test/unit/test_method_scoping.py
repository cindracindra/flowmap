from domain.method_scoping import build_method_definitions
from model import ArmExit, BranchArm, BranchGroup, Edge, Graph, LoopGroup, Node

ROOT = "Example.root:void()"
CALLEE = "Example.callee:void()"


def test_method_scoping_copies_authoritative_structure_metadata_unchanged() -> None:
    branch = BranchGroup(
        "branch", "IF", method=ROOT,
        entryNodeId="branch_entry", exitNodeId="branch_exit",
        arms=[
            BranchArm(
                "if",
                exits=[ArmExit("continues", "branch_exit")],
            ),
            BranchArm(
                "else", empty=True,
                exits=[ArmExit("continues", "branch_exit")],
            ),
        ],
    )
    loop = LoopGroup(
        "loop", "WHILE", method=ROOT,
        entryNodeId="loop_entry", exitNodeId="loop_exit",
    )
    graph = Graph(
        nodes=[
            Node("root", "entry", calleeFullName=ROOT),
            Node("callee", "entry", calleeFullName=CALLEE),
            Node("branch_entry", "structure", callerMethod=ROOT, structureGroupId="branch", structureRole="entry"),
            Node("branch_exit", "structure", callerMethod=ROOT, structureGroupId="branch", structureRole="exit"),
            Node("loop_entry", "structure", callerMethod=ROOT, structureGroupId="loop", structureRole="entry"),
            Node("loop_exit", "structure", callerMethod=ROOT, structureGroupId="loop", structureRole="exit"),
            Node("call", "call", callerMethod=ROOT, calleeFullName=CALLEE),
            Node("callee_work", "call", callerMethod=CALLEE, calleeFullName="Service.work:void()"),
        ],
        edges=[
            Edge("root", "branch_entry", "sequence"),
            Edge("branch_exit", "loop_entry", "sequence"),
            Edge("loop_exit", "call", "sequence"),
            Edge("call", "callee", "invoke"),
            Edge("call", "callee_work", "sequence"),
        ],
        branchGroups=[branch],
        loopGroups=[loop],
    )

    methods = build_method_definitions(graph)
    root = methods["root"]

    assert root.branchGroups == [branch]
    assert root.branchGroups[0] is branch
    assert root.branchGroups[0].arms[0].exits == [
        ArmExit("continues", "branch_exit")
    ]
    assert root.loopGroups == [loop]
    assert {(edge.source, edge.target) for edge in root.sequenceEdges} == {
        ("root", "branch_entry"),
        ("branch_exit", "loop_entry"),
        ("loop_exit", "call"),
    }
    assert [(edge.source, edge.target) for edge in root.invokeEdges] == [
        ("call", "callee")
    ]


def test_method_scoping_never_uses_sequence_reachability_as_ownership() -> None:
    graph = Graph(
        nodes=[
            Node("root", "entry", calleeFullName=ROOT),
            Node("callee", "entry", calleeFullName=CALLEE),
            Node("owned", "call", callerMethod=ROOT),
            Node("foreign", "call", callerMethod=CALLEE),
        ],
        edges=[Edge("owned", "foreign", "sequence")],
    )

    methods = build_method_definitions(graph)

    assert [node.id for node in methods["root"].nodes] == ["owned"]
    assert [node.id for node in methods["callee"].nodes] == ["foreign"]
    assert methods["root"].sequenceEdges == []
    assert methods["callee"].sequenceEdges == []
