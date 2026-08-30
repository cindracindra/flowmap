import pytest

from model import (
    ArmExit,
    ArmConditionStage,
    BranchArm,
    BranchGroup,
    ConditionStage,
    Edge,
    LoopGroup,
    Node,
)


def test_structural_node_schema_round_trips() -> None:
    node = Node(
        id="continue1",
        type="transfer",
        callerMethod="Example.run:void()",
        targetStructureGroupId="outerLoop",
        transferKind="continue",
        loopIds=["outerLoop"],
    )

    assert Node.from_dict(node.to_dict()) == node


def test_flattened_condition_stage_schema_round_trips() -> None:
    group = BranchGroup(
        id="branchA",
        kind="IF",
        entryNodeId="branchA:entry",
        exitNodeId="branchA:exit",
        conditionStages=[
            ConditionStage(
                id="stage1",
                nodeIds=["condition1"],
                decisionNodeId="stage1:decision",
            ),
            ConditionStage(
                id="stage2",
                nodeIds=["condition2"],
                decisionNodeId="stage2:decision",
            ),
        ],
        arms=[
            BranchArm(
                label="elseif1",
                exits=[ArmExit(kind="continue", destinationNodeId="loopA:exit")],
                conditionStages=[
                    ArmConditionStage(
                        stageId="stage1",
                        nodeIds=["condition1"],
                        outcome=False,
                    ),
                    ArmConditionStage(
                        stageId="stage2",
                        nodeIds=["condition2"],
                        outcome=True,
                    ),
                ],
            )
        ],
    )

    assert BranchGroup.from_dict(group.to_dict()) == group


def test_arm_exit_uses_one_authoritative_destination() -> None:
    exit_ = ArmExit(kind="continues", destinationNodeId="branchA:exit")

    assert exit_.to_dict() == {
        "kind": "continues",
        "destinationNodeId": "branchA:exit",
    }
    assert ArmExit.from_dict(exit_.to_dict()) == exit_


def test_loop_anchor_schema_round_trips() -> None:
    group = LoopGroup(
        id="loopA",
        kind="WHILE",
        entryNodeId="loopA:entry",
        exitNodeId="loopA:exit",
    )

    assert LoopGroup.from_dict(group.to_dict()) == group


def test_legacy_branch_node_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported node type"):
        Node.from_dict({"id": "decision", "type": "branch"})

