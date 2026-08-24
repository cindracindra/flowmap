from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"))

from domain.cfg_pipeline import flatten_cfg  # noqa: E402
from model import Graph  # noqa: E402


def node(id_, type_, callee=None, caller=None, *, dead=False, arms=None):
    value = {"id": id_, "type": type_}
    if callee is not None:
        value["calleeFullName"] = callee
    if caller is not None:
        value["callerMethod"] = caller
    if dead:
        value["deadEnd"] = True
    if arms:
        value["branchArms"] = [
            {"groupId": group, "armLabel": arm} for group, arm in arms
        ]
    return value


def edge(source, target, type_="sequence"):
    return {"from": source, "to": target, "type": type_}


def requirements(edge_):
    return {(item.groupId, item.armLabel) for item in edge_.branchRequirements}


class ReturnEdgeBridgingTests(unittest.TestCase):
    def test_nested_exit_unwinds_past_tail_call_to_outer_continuation(self):
        # main(): A(); B()   A(): C(); return   C(): X(); <fallthrough>
        graph = Graph.from_dict({
            "entryPoint": "main",
            "nodes": [
                node("e_main", "entry", "main"),
                node("c_a", "call", "A", "main"),
                node("c_b", "call", "pkg.B.run", "main"),
                node("e_a", "entry", "A"),
                node("c_c", "call", "C", "A"),
                {"id": "r_a", "type": "exit", "exitKind": "return", "callerMethod": "A"},
                node("e_c", "entry", "C"),
                node("c_x", "call", "pkg.X.run", "C"),
                {"id": "f_c", "type": "exit", "exitKind": "fallthrough", "callerMethod": "C"},
                node("leaf_x", "leaf", "pkg.X.run"),
                node("leaf_b", "leaf", "pkg.B.run"),
            ],
            "edges": [
                edge("e_main", "c_a"), edge("c_a", "c_b"),
                edge("c_a", "e_a", "invoke"), edge("e_a", "c_c"),
                edge("c_c", "r_a"), edge("c_c", "e_c", "invoke"),
                edge("e_c", "c_x"), edge("c_x", "f_c"),
                edge("c_x", "leaf_x", "invoke"), edge("c_b", "leaf_b", "invoke"),
            ],
        })

        flattened = flatten_cfg(graph)
        by_orig = {item.origId: item for item in flattened.nodes}
        route = next(
            item for item in flattened.edges
            if item.source == by_orig["leaf_x"].id and item.target == by_orig["c_b"].id
        )
        self.assertEqual(route.type, "sequence")
        self.assertEqual(route.returnFrom, by_orig["c_a"].id)
        self.assertFalse(route.fallback)
        self.assertFalse(any(item.type == "exit" for item in flattened.nodes))

    def test_multiple_explicit_return_frontiers_keep_their_route_requirements(self):
        graph = Graph.from_dict({
            "entryPoint": "main",
            "nodes": [
                node("e_main", "entry", "main"), node("c_a", "call", "A", "main"),
                node("c_b", "call", "pkg.B.run", "main"), node("e_a", "entry", "A"),
                node("c_x", "call", "pkg.X.run", "A", arms=[("g", "if")]),
                node("c_y", "call", "pkg.Y.run", "A", arms=[("g", "else")]),
                {"id": "r_x", "type": "exit", "exitKind": "return", "callerMethod": "A",
                 "branchArms": [{"groupId": "g", "armLabel": "if"}]},
                {"id": "r_y", "type": "exit", "exitKind": "return", "callerMethod": "A",
                 "branchArms": [{"groupId": "g", "armLabel": "else"}]},
                node("leaf_x", "leaf", "pkg.X.run"), node("leaf_y", "leaf", "pkg.Y.run"),
                node("leaf_b", "leaf", "pkg.B.run"),
            ],
            "edges": [
                edge("e_main", "c_a"), edge("c_a", "c_b"), edge("c_a", "e_a", "invoke"),
                edge("e_a", "c_x"), edge("e_a", "c_y"), edge("c_x", "r_x"),
                edge("c_y", "r_y"), edge("c_x", "leaf_x", "invoke"),
                edge("c_y", "leaf_y", "invoke"), edge("c_b", "leaf_b", "invoke"),
            ],
            "branchGroups": [{
                "id": "g", "kind": "IF", "method": "A", "branchPointIds": ["e_a"],
                "arms": [
                    {"label": "if", "firstCallId": "c_x", "terminus": "return",
                     "exits": [{"kind": "return", "frontierIds": ["c_x"]}]},
                    {"label": "else", "firstCallId": "c_y", "terminus": "return",
                     "exits": [{"kind": "return", "frontierIds": ["c_y"]}]},
                ],
            }],
        })

        flattened = flatten_cfg(graph)
        by_orig = {item.origId: item for item in flattened.nodes}
        group = flattened.branchGroups[0]
        routes = [
            item for item in flattened.edges
            if item.target == by_orig["c_b"].id and item.returnFrom is not None
        ]
        self.assertEqual({by_orig_id(flattened, item.source) for item in routes}, {"leaf_x", "leaf_y"})
        expected = {
            "leaf_x": {(group.id, "if")},
            "leaf_y": {(group.id, "else")},
        }
        self.assertEqual(
            {by_orig_id(flattened, item.source): requirements(item) for item in routes},
            expected,
        )
        self.assertTrue(all(not item.fallback for item in routes))

    def test_external_leaf_returns_to_nearest_continuing_node(self):
        graph = Graph.from_dict({
            "entryPoint": "run",
            "nodes": [
                node("e", "entry", "run"), node("c_get", "call", "Store.get", "run"),
                node("c_use", "call", "Store.use", "run"),
                node("leaf_get", "leaf", "Store.get"), node("leaf_use", "leaf", "Store.use"),
            ],
            "edges": [
                edge("e", "c_get"), edge("c_get", "c_use"),
                edge("c_get", "leaf_get", "invoke"), edge("c_use", "leaf_use", "invoke"),
            ],
        })
        flattened = flatten_cfg(graph)
        by_orig = {item.origId: item for item in flattened.nodes}
        routes = [item for item in flattened.edges if item.source == by_orig["leaf_get"].id]
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].target, by_orig["c_use"].id)
        self.assertEqual(routes[0].returnFrom, by_orig["c_get"].id)
        self.assertFalse(routes[0].fallback)

    def test_all_authoritative_callee_routes_throw_and_never_reach_continuation(self):
        graph = Graph.from_dict({
            "entryPoint": "main",
            "nodes": [
                node("e_main", "entry", "main"), node("c_bad", "call", "bad", "main"),
                node("c_after", "call", "After.run", "main"), node("e_bad", "entry", "bad"),
                node("c_x", "call", "X.fail", "bad", arms=[("g", "if")]),
                node("c_y", "call", "Y.fail", "bad", arms=[("g", "else")]),
                {"id": "t_x", "type": "exit", "exitKind": "throw", "callerMethod": "bad",
                 "branchArms": [{"groupId": "g", "armLabel": "if"}]},
                {"id": "t_y", "type": "exit", "exitKind": "throw", "callerMethod": "bad",
                 "branchArms": [{"groupId": "g", "armLabel": "else"}]},
                node("leaf_x", "leaf", "X.fail"), node("leaf_y", "leaf", "Y.fail"),
                node("leaf_after", "leaf", "After.run"),
            ],
            "edges": [
                edge("e_main", "c_bad"), edge("c_bad", "c_after"), edge("c_bad", "e_bad", "invoke"),
                edge("e_bad", "c_x"), edge("e_bad", "c_y"), edge("c_x", "t_x"), edge("c_y", "t_y"),
                edge("c_x", "leaf_x", "invoke"), edge("c_y", "leaf_y", "invoke"),
                edge("c_after", "leaf_after", "invoke"),
            ],
            "branchGroups": [{"id": "g", "kind": "IF", "method": "bad", "branchPointIds": ["e_bad"],
                "arms": [
                    {"label": "if", "firstCallId": "c_x", "terminus": "throw",
                     "exits": [{"kind": "throw", "frontierIds": ["c_x"]}]},
                    {"label": "else", "firstCallId": "c_y", "terminus": "throw",
                     "exits": [{"kind": "throw", "frontierIds": ["c_y"]}]},
                ]}],
        })
        flattened = flatten_cfg(graph)
        after = next(item for item in flattened.nodes if item.origId == "c_after")
        self.assertFalse(any(item.target == after.id for item in flattened.edges))
        self.assertFalse(any(item.fallback for item in flattened.edges))

    def test_external_condition_leaf_returns_to_nonempty_and_empty_arms(self):
        graph = Graph.from_dict(condition_branch_graph())
        flattened = flatten_cfg(graph)
        by_orig = {item.origId: item for item in flattened.nodes}
        group = flattened.branchGroups[0]
        routes = [item for item in flattened.edges if item.source == by_orig["leaf_valid"].id]
        self.assertEqual(
            {(by_orig_id(flattened, item.target), frozenset(requirements(item))) for item in routes},
            {
                ("c_do", frozenset({(group.id, "if")})),
                ("c_after", frozenset({(group.id, "else")})),
            },
        )
        self.assertTrue(all(item.returnFrom == by_orig["c_valid"].id for item in routes))

    def test_new_order_shape_returns_cart_lookup_only_to_nearest_condition_call(self):
        # Reduced newOrderForm(): session.getAttribute(cart) is followed by
        # the next condition call. Calls in either later arm are deliberately
        # not projected as additional siblings of the lookup.
        method = "OrderController.newOrderForm"
        graph = Graph.from_dict({
            "entryPoint": method,
            "nodes": [
                node("e", "entry", method),
                node("c_account", "call", "HttpSession.getAttribute", method),
                node("c_cart", "call", "HttpSession.getAttribute", method),
                node("c_auth", "call", "AccountSession.isAuthenticated", method),
                node("c_redirect", "call", "Model.addAttribute", method, arms=[("auth", "if")]),
                node("c_order", "call", "Order.<init>", method, arms=[("auth", "else"), ("cart", "if")]),
                node("c_error", "call", "Model.addAttribute", method, arms=[("auth", "else"), ("cart", "else")]),
                node("leaf_account", "leaf", "HttpSession.getAttribute"),
                node("leaf_cart", "leaf", "HttpSession.getAttribute"),
                node("leaf_auth", "leaf", "AccountSession.isAuthenticated"),
                node("leaf_redirect", "leaf", "Model.addAttribute"),
                node("leaf_order", "leaf", "Order.<init>"), node("leaf_error", "leaf", "Model.addAttribute"),
            ],
            "edges": [
                edge("e", "c_account"), edge("c_account", "c_cart"), edge("c_cart", "c_auth"),
                edge("c_auth", "c_redirect"), edge("c_auth", "c_order"), edge("c_auth", "c_error"),
                edge("c_account", "leaf_account", "invoke"), edge("c_cart", "leaf_cart", "invoke"),
                edge("c_auth", "leaf_auth", "invoke"), edge("c_redirect", "leaf_redirect", "invoke"),
                edge("c_order", "leaf_order", "invoke"), edge("c_error", "leaf_error", "invoke"),
            ],
        })
        flattened = flatten_cfg(graph)
        cart_leaf = next(item for item in flattened.nodes if item.origId == "leaf_cart")
        outgoing = [item for item in flattened.edges if item.source == cart_leaf.id]
        self.assertEqual(len(outgoing), 1)
        self.assertEqual(by_orig_id(flattened, outgoing[0].target), "c_auth")
        self.assertEqual(outgoing[0].type, "sequence")
        cart_call = next(item for item in flattened.nodes if item.origId == "c_cart")
        self.assertEqual(outgoing[0].returnFrom, cart_call.id)
        self.assertEqual(requirements(outgoing[0]), set())

    def test_empty_arm_method_exit_is_qualified_before_returning_to_caller(self):
        # addItemToCart(): cart = getCart(); cart.containsItemId()
        # getCart(): value = session.getAttribute();
        #            if (value == null) { new Cart(); setAttribute(); }
        #            return value;
        # The direct getAttribute tail reaches the caller only through the
        # empty ELSE. It must not survive as an unconditional duplicate.
        graph = Graph.from_dict({
            "entryPoint": "addItemToCart",
            "nodes": [
                node("e_add", "entry", "addItemToCart"),
                node("c_get_cart", "call", "getCart", "addItemToCart"),
                node("c_contains", "call", "Cart.containsItemId", "addItemToCart"),
                node("e_get_cart", "entry", "getCart"),
                node("c_get_attribute", "call", "HttpSession.getAttribute", "getCart"),
                node("c_new", "call", "Cart.<init>", "getCart", arms=[("null", "if")]),
                node("c_set", "call", "HttpSession.setAttribute", "getCart", arms=[("null", "if")]),
                {"id": "f_get_cart", "type": "exit", "exitKind": "return",
                 "callerMethod": "getCart"},
                node("leaf_get_attribute", "leaf", "HttpSession.getAttribute"),
                node("leaf_new", "leaf", "Cart.<init>"),
                node("leaf_set", "leaf", "HttpSession.setAttribute"),
                node("leaf_contains", "leaf", "Cart.containsItemId"),
            ],
            "edges": [
                edge("e_add", "c_get_cart"), edge("c_get_cart", "c_contains"),
                edge("c_get_cart", "e_get_cart", "invoke"),
                edge("e_get_cart", "c_get_attribute"),
                edge("c_get_attribute", "c_new"),
                edge("c_get_attribute", "f_get_cart"),
                edge("c_new", "c_set"), edge("c_set", "f_get_cart"),
                edge("c_get_attribute", "leaf_get_attribute", "invoke"),
                edge("c_new", "leaf_new", "invoke"), edge("c_set", "leaf_set", "invoke"),
                edge("c_contains", "leaf_contains", "invoke"),
            ],
            "branchGroups": [{
                "id": "null", "kind": "IF", "method": "getCart",
                "branchPointIds": ["c_get_attribute"],
                "arms": [
                    {"label": "if", "firstCallId": "c_new", "terminus": "continues",
                     "exits": [{"kind": "continues", "frontierIds": ["c_set"],
                                "branchRequirements": [{"groupId": "null", "armLabel": "if"}]}]},
                    {"label": "else", "empty": True, "terminus": "continues",
                     "exits": [{"kind": "continues", "frontierIds": ["c_get_attribute"],
                                "branchRequirements": [{"groupId": "null", "armLabel": "else"}]}]},
                ],
            }],
        })

        flattened = flatten_cfg(graph)
        group = flattened.branchGroups[0]
        direct_routes = [
            item for item in flattened.edges
            if by_orig_id(flattened, item.source) == "leaf_get_attribute"
            and by_orig_id(flattened, item.target) == "c_contains"
        ]
        self.assertEqual(len(direct_routes), 1)
        self.assertEqual(requirements(direct_routes[0]), {(group.id, "else")})
        get_cart_call = next(item for item in flattened.nodes if item.origId == "c_get_cart")
        self.assertEqual(direct_routes[0].returnFrom, get_cart_call.id)
        self.assertFalse(direct_routes[0].fallback)


def by_orig_id(graph, clone_id):
    return next(item.origId for item in graph.nodes if item.id == clone_id)


def condition_branch_graph():
    return {
        "entryPoint": "run",
        "nodes": [
            node("e", "entry", "run"), node("c_valid", "call", "isValid", "run"),
            node("c_do", "call", "Do.run", "run", arms=[("g", "if")]),
            node("c_after", "call", "After.run", "run"), node("leaf_valid", "leaf", "isValid"),
            node("leaf_do", "leaf", "Do.run"), node("leaf_after", "leaf", "After.run"),
        ],
        "edges": [
            edge("e", "c_valid"), edge("c_valid", "c_do"), edge("c_valid", "c_after"),
            edge("c_do", "c_after"), edge("c_valid", "leaf_valid", "invoke"),
            edge("c_do", "leaf_do", "invoke"), edge("c_after", "leaf_after", "invoke"),
        ],
        "branchGroups": [{"id": "g", "kind": "IF", "method": "run", "branchPointIds": ["c_valid"],
            "arms": [
                {"label": "if", "firstCallId": "c_do", "terminus": "continues",
                 "exits": [{"kind": "continues", "frontierIds": ["c_do"]}]},
                {"label": "else", "empty": True, "terminus": "continues",
                 "exits": [{"kind": "continues", "frontierIds": []}]},
            ]}],
    }


if __name__ == "__main__":
    unittest.main()
