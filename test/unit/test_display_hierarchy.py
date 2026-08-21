import sys
from pathlib import Path

FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.display_hierarchy import build_display_hierarchy
from model import BranchArm, BranchArmRef, BranchGroup, Edge, Graph, Node


def test_builds_nested_methods_and_branches_as_id_only_sidecar() -> None:
    graph = Graph(
        rootId="root",
        nodes=[
            Node(id="root", type="entry", calleeFullName="Root.run", depth=0),
            Node(id="fork", type="call", calleeFullName="check", depth=0),
            Node(
                id="child",
                type="entry",
                calleeFullName="Child.work",
                depth=1,
                branchArms=[BranchArmRef("guard", "if")],
            ),
            Node(
                id="inside",
                type="call",
                calleeFullName="save",
                depth=1,
                branchArms=[BranchArmRef("guard", "if")],
            ),
            Node(id="after", type="call", calleeFullName="finish", depth=0),
        ],
        edges=[
            Edge("root", "fork", "sequence"),
            Edge("fork", "child", "invoke"),
            Edge("child", "inside", "sequence"),
            Edge("inside", "after", "sequence", returnFrom="fork"),
        ],
        branchGroups=[
            BranchGroup(
                id="guard",
                kind="IF",
                branchPointIds=["fork"],
                arms=[
                    BranchArm("if", firstCallId="child", empty=False),
                    BranchArm("else", empty=True),
                ],
            )
        ],
    )

    hierarchy = build_display_hierarchy(graph)
    root = hierarchy["roots"][0]
    assert root["entryId"] == "root"
    assert [item["kind"] for item in root["items"]] == [
        "operation", "branch", "operation"
    ]
    branch = root["items"][1]
    assert branch["panelId"] == "guard"
    selected_arm = branch["arms"][0]
    assert selected_arm["armId"] == "if"
    assert selected_arm["items"][0]["kind"] == "method"
    assert selected_arm["items"][0]["entryId"] == "child"
    assert selected_arm["items"][0]["items"] == [
        {"kind": "operation", "nodeId": "inside"}
    ]
    assert "calleeFullName" not in str(hierarchy)


def test_returns_empty_hierarchy_for_unrooted_graph() -> None:
    assert build_display_hierarchy(Graph()) == {"roots": []}


def test_nests_branch_block_inside_its_owning_outer_arm() -> None:
    outer = BranchArmRef("outer", "if")
    inner = BranchArmRef("inner", "if")
    graph = Graph(
        rootId="root",
        nodes=[
            Node(id="root", type="entry", depth=0),
            Node(id="outer_fork", type="call", depth=0),
            Node(id="inner_fork", type="call", depth=0, branchArms=[outer]),
            Node(id="work", type="call", depth=0, branchArms=[outer, inner]),
            Node(id="after", type="call", depth=0),
        ],
        edges=[
            Edge("root", "outer_fork", "sequence"),
            Edge("outer_fork", "inner_fork", "sequence"),
            Edge("inner_fork", "work", "sequence"),
            Edge("work", "after", "sequence"),
        ],
        branchGroups=[
            BranchGroup(
                id="outer", kind="IF", branchPointIds=["outer_fork"],
                arms=[BranchArm("if"), BranchArm("else", empty=True)],
            ),
            BranchGroup(
                id="inner", kind="IF", branchPointIds=["inner_fork"],
                arms=[BranchArm("if"), BranchArm("else", empty=True)],
            ),
        ],
    )

    hierarchy = build_display_hierarchy(graph)
    outer_block = hierarchy["roots"][0]["items"][1]
    outer_arm_items = outer_block["arms"][0]["items"]
    assert [item["kind"] for item in outer_arm_items] == ["operation", "branch"]
    inner_block = outer_arm_items[1]
    assert inner_block["panelId"] == "inner"
    assert inner_block["arms"][0]["items"] == [
        {"kind": "operation", "nodeId": "work"}
    ]
