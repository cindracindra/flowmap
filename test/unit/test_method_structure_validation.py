import pytest

from domain.method_structure_validation import validate_method_structures
from model import (
    ArmExit,
    BranchArm,
    BranchArmRef,
    BranchGroup,
    BranchRequirement,
    Edge,
    LoopGroup,
    MethodDefinition,
    Node,
)

METHOD = "Example.run:void()"


def _anchored_method() -> MethodDefinition:
    entry = Node("entry", "entry", calleeFullName=METHOD)
    return MethodDefinition(
        entryId="entry",
        methodFullName=METHOD,
        entry=entry,
        nodes=[
            Node("branch_entry", "structure", structureGroupId="branch", structureRole="entry"),
            Node("decision", "structure", structureGroupId="branch", structureRole="decision"),
            Node(
                "work", "call", branchArms=[BranchArmRef("branch", "if")],
            ),
            Node("branch_exit", "structure", structureGroupId="branch", structureRole="exit"),
            Node("after", "call"),
        ],
        sequenceEdges=[
            Edge("entry", "branch_entry", "sequence"),
            Edge("branch_entry", "decision", "sequence"),
            Edge("decision", "work", "sequence", branchRequirements=[BranchRequirement("branch", "if")]),
            Edge("decision", "branch_exit", "sequence", branchRequirements=[BranchRequirement("branch", "else")]),
            Edge("work", "branch_exit", "sequence", branchRequirements=[BranchRequirement("branch", "if")]),
            Edge("branch_exit", "after", "sequence"),
        ],
        branchGroups=[BranchGroup(
            "branch", "IF",
            entryNodeId="branch_entry",
            exitNodeId="branch_exit",
            arms=[
                BranchArm("if", exits=[ArmExit("continues", "branch_exit")]),
                BranchArm("else", empty=True, exits=[ArmExit("continues", "branch_exit")]),
            ],
        )],
    )


def test_validation_preserves_authoritative_method_topology() -> None:
    method = _anchored_method()

    validated = validate_method_structures(method)

    assert validated is method
    assert validated.sequenceEdges == method.sequenceEdges
    assert validated.branchGroups == method.branchGroups


def test_validation_rejects_missing_arm_exit() -> None:
    method = _anchored_method()
    method.branchGroups[0].arms[0].exits = []

    with pytest.raises(ValueError, match="has no ArmExit"):
        validate_method_structures(method)


def test_validation_rejects_requirement_for_unknown_arm() -> None:
    method = _anchored_method()
    method.sequenceEdges[2].branchRequirements = [
        BranchRequirement("branch", "elseif99")
    ]

    with pytest.raises(ValueError, match="invalid requirement"):
        validate_method_structures(method)


def test_validation_rejects_scope_retained_after_branch_exit() -> None:
    method = _anchored_method()
    method.sequenceEdges[-1].branchRequirements = [
        BranchRequirement("branch", "if")
    ]

    with pytest.raises(ValueError, match="retains its own branch scope"):
        validate_method_structures(method)


def test_validation_accepts_nested_branch_inside_loop() -> None:
    method = _anchored_method()
    method.nodes.extend([
        Node("loop_entry", "structure", structureGroupId="loop", structureRole="entry"),
        Node("loop_exit", "structure", structureGroupId="loop", structureRole="exit"),
    ])
    method.loopGroups = [LoopGroup(
        "loop", "WHILE", entryNodeId="loop_entry", exitNodeId="loop_exit"
    )]
    for node in method.nodes:
        if node.id in {"branch_entry", "decision", "work", "branch_exit"}:
            node.loopIds.append("loop")

    assert validate_method_structures(method) is method
