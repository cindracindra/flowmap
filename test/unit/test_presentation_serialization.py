from model import (
    ArmConditionStage,
    ArmExit,
    BranchArm,
    BranchGroup,
    ConditionStage,
    Edge,
    LoopGroup,
    MethodDefinition,
    Node,
)
from presentation.serialization import _serialize_leaf, _serialize_method

METHOD = "Example.run:void()"


def test_method_serialization_preserves_structure_contract_and_cfg_order() -> None:
    entry = Node("entry", "entry", calleeFullName=METHOD)
    method = MethodDefinition(
        entryId="entry",
        methodFullName=METHOD,
        entry=entry,
        nodes=[
            Node("call", "call", callerMethod=METHOD),
            Node("z_target", "call", callerMethod=METHOD),
            Node("a_target", "call", callerMethod=METHOD),
            Node(
                "branch_entry", "structure", callerMethod=METHOD,
                structureGroupId="branch", structureRole="entry",
            ),
            Node(
                "branch_exit", "structure", callerMethod=METHOD,
                structureGroupId="branch", structureRole="exit",
            ),
            Node(
                "break", "transfer", callerMethod=METHOD,
                transferKind="break", targetStructureGroupId="loop",
            ),
            Node(
                "loop_entry", "structure", callerMethod=METHOD,
                structureGroupId="loop", structureRole="entry",
            ),
            Node(
                "loop_exit", "structure", callerMethod=METHOD,
                structureGroupId="loop", structureRole="exit",
            ),
        ],
        sequenceEdges=[
            Edge("call", "z_target", "sequence"),
            Edge("call", "a_target", "sequence"),
            Edge("call", "z_target", "sequence"),
        ],
        branchGroups=[BranchGroup(
            "branch", "IF", entryNodeId="branch_entry", exitNodeId="branch_exit",
            conditionStages=[ConditionStage("stage", ["call"], "branch_entry")],
            arms=[BranchArm(
                "if",
                exits=[ArmExit("continues", "branch_exit")],
                conditionStages=[ArmConditionStage("stage", ["call"], True)],
            )],
        )],
        loopGroups=[LoopGroup(
            "loop", "WHILE", entryNodeId="loop_entry", exitNodeId="loop_exit"
        )],
    )

    serialized = _serialize_method(method, None, {"entry"}, set())

    assert serialized["calls"]["call"]["continuationIds"] == [
        "z_target", "a_target"
    ]
    assert serialized["branchGroups"][0]["entryNodeId"] == "branch_entry"
    assert serialized["branchGroups"][0]["exitNodeId"] == "branch_exit"
    assert serialized["branchGroups"][0]["conditionStages"][0]["id"] == "stage"
    assert serialized["loopGroups"][0] == {
        "id": "loop",
        "kind": "WHILE",
        "entryNodeId": "loop_entry",
        "exitNodeId": "loop_exit",
    }
    transfer = next(node for node in serialized["nodes"] if node["id"] == "break")
    assert transfer["transferKind"] == "break"
    assert transfer["targetStructureGroupId"] == "loop"


def test_method_serialization_references_shared_leaves_without_copying_them() -> None:
    entry = Node("entry", "entry", calleeFullName=METHOD)
    method = MethodDefinition(
        entryId="entry",
        methodFullName=METHOD,
        entry=entry,
        nodes=[
            Node("first", "call", callerMethod=METHOD),
            Node("second", "call", callerMethod=METHOD),
        ],
        invokeEdges=[
            Edge("first", "external", "invoke"),
            Edge("second", "external", "invoke"),
        ],
    )

    serialized = _serialize_method(method, None, {"entry"}, {"external"})

    assert serialized["calls"]["first"]["targetLeafIds"] == ["external"]
    assert serialized["calls"]["second"]["targetLeafIds"] == ["external"]
    assert all(node["id"] != "external" for node in serialized["nodes"])


def test_leaf_serialization_contains_only_compact_display_identity() -> None:
    leaf = Node(
        "external",
        "leaf",
        calleeFullName="com.example.client.External.send:java.lang.String(int)",
        callerMethod=METHOD,
        code="external.send(value)",
        line=42,
        sourceFile="Example.java",
    )

    assert _serialize_leaf(leaf) == {
        "id": "external",
        "type": "leaf",
        "calleeFullName": "External.send",
    }
